"""UDS Runner server (Master Spec §13, §30.2).

Прототип: asyncio Unix-domain-socket сервер. Принимает argv-массив, проверяет
token, allowlist каталогов/исполняемых файлов и запрет секретов в запросе,
запускает процесс в отдельной группе, стримит нормализованные redacted
события, соблюдает heartbeat/timeout/interrupt и ведёт recovery journal.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

from atlas_core.errors import ErrorCode, classify
from atlas_core.ids import new_id, utcnow_iso
from atlas_core.redaction import contains_secret, redact

from .journal import RecoveryJournal
from .protocol import ALLOWED_ENV_EXTRA_KEYS, decode, encode


class RunnerRefused(Exception):
    pass


@dataclass
class RunnerConfig:
    socket_path: str
    token: str
    journal_path: str
    allowed_executables: set[str] = field(default_factory=lambda: {
        "codex", "claude", "git", "gh", "printf", "python3", "bash", "true", "sleep", "cat"})
    allowed_dirs: list[str] = field(default_factory=list)
    allow_root: bool = False  # прод: systemd User=atlas; VP-0 среда — root
    # uid'ы идентичностей профилей, в которые Runner имеет право дропнуть привилегии.
    allowed_run_as_uids: set[int] = field(default_factory=set)
    heartbeat_s: float = 0.5
    grace_s: float = 2.0
    max_output_bytes: int = 1_000_000


@dataclass
class _Job:
    request_id: str
    proc: asyncio.subprocess.Process
    interrupted: bool = False


class RunnerServer:
    def __init__(self, config: RunnerConfig):
        self.cfg = config
        self.journal = RecoveryJournal(config.journal_path)
        self._jobs: dict[str, _Job] = {}
        self._server: asyncio.AbstractServer | None = None
        # Обрыв незавершённых job предыдущего инстанса — восстановление (§7.5).
        self.recovered_on_start = self.journal.unfinished_jobs()
        for rid in self.recovered_on_start:
            self.journal.interrupted(rid, "runner restart: job без finished помечен INTERRUPTED")

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if os.geteuid() == 0 and not self.cfg.allow_root:
            raise RunnerRefused("Runner отказывается работать от root (§13.2). "
                                "Прод: systemd User=atlas. Для VP-0 задайте allow_root явно.")
        sock = Path(self.cfg.socket_path)
        sock.parent.mkdir(parents=True, exist_ok=True)
        if sock.exists():
            sock.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(sock))
        os.chmod(sock, 0o660)  # §7.3 socket 0660

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        # прервать активные job
        for job in list(self._jobs.values()):
            self._terminate(job, "server stop")

    # --- обработка соединения ----------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            msg = decode(line)
            if msg.get("token") != self.cfg.token:
                await self._send(writer, {"type": "error", "code": ErrorCode.PERMISSION_DENIED.value,
                                          "evidence": "неверный request-token"})
                return
            mtype = msg.get("type")
            if mtype == "run":
                await self._handle_run(msg.get("request", {}), writer)
            elif mtype == "interrupt":
                await self._handle_interrupt(msg.get("request_id", ""), writer)
            elif mtype in ("ping", "health"):
                await self._send(writer, {
                    "type": "pong",
                    "recovered": self.recovered_on_start,
                    "health": {
                        "status": "READY",
                        "version": __import__("atlas_runner").__version__,
                        "uid": os.geteuid(),
                        "non_root": os.geteuid() != 0,
                        "active_jobs": len(self._jobs),
                        "socket": self.cfg.socket_path,
                    },
                })
            else:
                await self._send(writer, {"type": "error", "code": ErrorCode.OUTPUT_INVALID.value,
                                          "evidence": f"неизвестный тип {mtype}"})
        except Exception as exc:  # noqa: BLE001
            ce = classify(str(exc), exception=exc)
            try:
                await self._send(writer, {"type": "error", "code": ce.code.value, "evidence": ce.evidence})
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    # --- валидация ----------------------------------------------------------
    def _validate(self, req: dict) -> tuple[list[str], str, dict]:
        argv = req.get("argv")
        cwd = req.get("cwd")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise RunnerRefused("argv должен быть непустым массивом строк (shell-строки запрещены)")
        argv0 = os.path.basename(argv[0])
        if argv0 not in self.cfg.allowed_executables:
            raise RunnerRefused(f"исполняемый файл вне allowlist: {argv0}")
        if not isinstance(cwd, str) or not cwd:
            raise RunnerRefused("cwd обязателен")
        real = os.path.realpath(cwd)
        if self.cfg.allowed_dirs and not any(
            real == os.path.realpath(d) or real.startswith(os.path.realpath(d) + os.sep)
            for d in self.cfg.allowed_dirs
        ):
            raise RunnerRefused("cwd вне allowlist каталогов (защита от traversal/symlink)")
        env_extra = req.get("env_extra", {}) or {}
        for k, v in env_extra.items():
            if k not in ALLOWED_ENV_EXTRA_KEYS:
                raise RunnerRefused(f"недопустимый ключ окружения: {k}")
            if contains_secret(str(v)):
                raise RunnerRefused("в запросе обнаружен секрет — отклонено")
        # общий скан запроса на секреты (raw token/cookie запрещены §13.2)
        for a in argv:
            if contains_secret(a):
                raise RunnerRefused("в argv обнаружен секрет — отклонено")
        return argv, real, env_extra

    def _build_env(self, req: dict, env_extra: dict) -> dict:
        keep = req.get("allowed_env_keys") or ["PATH", "HOME", "LANG", "TERM", "USER"]
        env = {k: os.environ[k] for k in keep if k in os.environ}
        # чужие root-переменные вычищаются, ставится только переданный root
        env.pop("CODEX_HOME", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.update({k: str(v) for k, v in env_extra.items()})
        return env

    # --- запуск -------------------------------------------------------------
    async def _handle_run(self, req: dict, writer: asyncio.StreamWriter) -> None:
        request_id = req.get("request_id") or new_id("req")
        try:
            argv, cwd, env_extra = self._validate(req)
        except RunnerRefused as exc:
            await self._send(writer, {"type": "error", "code": ErrorCode.POLICY_DENIED.value,
                                      "evidence": redact(str(exc)), "request_id": request_id})
            return
        env = self._build_env(req, env_extra)
        timeout_s = float(req.get("timeout_s", 60.0))
        max_bytes = int(req.get("max_output_bytes", self.cfg.max_output_bytes))

        # Дроп привилегий в идентичность профиля (§7.2, §30.2): CLI провайдера
        # запускается под atlas-cx01/… и физически не видит чужие credentials.
        drop_kwargs: dict = {}
        run_as_uid = req.get("run_as_uid")
        if run_as_uid is not None:
            try:
                run_as_uid = int(run_as_uid)
                run_as_gid = int(req.get("run_as_gid", run_as_uid))
            except (TypeError, ValueError):
                await self._send(writer, {"type": "error", "code": ErrorCode.POLICY_DENIED.value,
                                          "evidence": "run_as_uid/gid должны быть числами", "request_id": request_id})
                return
            if self.cfg.allowed_run_as_uids and run_as_uid not in self.cfg.allowed_run_as_uids:
                await self._send(writer, {"type": "error", "code": ErrorCode.POLICY_DENIED.value,
                                          "evidence": f"дроп в uid {run_as_uid} не разрешён", "request_id": request_id})
                return
            if os.geteuid() != 0:
                # Нет привилегии дропнуть — в проде нужен CAP_SETUID/SETGID у Runner.
                await self._send(writer, {"type": "error", "code": ErrorCode.POLICY_DENIED.value,
                                          "evidence": "Runner без прав дропа привилегий (прод: CAP_SETUID/SETGID)",
                                          "request_id": request_id})
                return
            drop_kwargs = {"user": run_as_uid, "group": run_as_gid, "extra_groups": []}
            env.setdefault("HOME", env_extra.get("CODEX_HOME") or env_extra.get("CLAUDE_CONFIG_DIR") or cwd)

        await self._send(writer, {"type": "accepted", "request_id": request_id})
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # отдельная process group (§13.1)
                **drop_kwargs,
            )
        except FileNotFoundError:
            await self._send(writer, {"type": "error", "code": ErrorCode.TOOL_FAILED.value,
                                      "evidence": f"не найден исполняемый файл {os.path.basename(argv[0])}",
                                      "request_id": request_id})
            return
        except PermissionError as exc:
            await self._send(writer, {"type": "error", "code": ErrorCode.PERMISSION_DENIED.value,
                                      "evidence": redact(f"дроп привилегий не удался: {exc}"),
                                      "request_id": request_id})
            return
        job = _Job(request_id=request_id, proc=proc)
        self._jobs[request_id] = job
        self.journal.started(request_id, argv[0], proc.pid or -1)
        await self._send(writer, {"type": "run.started", "request_id": request_id, "pid": proc.pid})

        hasher = hashlib.sha256()
        total = 0
        truncated = False
        state = "SUCCEEDED"
        try:
            async def pump():
                nonlocal total, truncated
                assert proc.stdout is not None
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    total += len(raw)
                    if total > max_bytes:
                        truncated = True
                        self._terminate(job, "output limit exceeded")
                        break
                    red = redact(raw.decode("utf-8", errors="replace").rstrip("\n"))
                    hasher.update((red + "\n").encode("utf-8"))
                    await self._send(writer, {"type": "run.output", "request_id": request_id, "line": red})

            async def heartbeats():
                while proc.returncode is None:
                    await asyncio.sleep(self.cfg.heartbeat_s)
                    try:
                        await self._send(writer, {"type": "run.heartbeat", "request_id": request_id,
                                                  "at": utcnow_iso()})
                    except Exception:
                        return

            hb_task = asyncio.create_task(heartbeats())
            try:
                await asyncio.wait_for(pump(), timeout=timeout_s)
                await asyncio.wait_for(proc.wait(), timeout=self.cfg.grace_s)
            except asyncio.TimeoutError:
                self._terminate(job, "timeout")
                state = "TIMEOUT"
            finally:
                hb_task.cancel()
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=self.cfg.grace_s)
                except asyncio.TimeoutError:
                    self._terminate(job, "grace exceeded")
            self._jobs.pop(request_id, None)

        exit_code = proc.returncode if proc.returncode is not None else -1
        if job.interrupted and state != "TIMEOUT":
            state = "INTERRUPTED"
        elif state == "SUCCEEDED" and exit_code != 0:
            state = "FAILED"
        if truncated:
            state = "FAILED"

        self.journal.finished(request_id, exit_code, state)
        await self._send(writer, {
            "type": "run.finished", "request_id": request_id, "exit_code": exit_code,
            "state": state, "output_hash": "sha256:" + hasher.hexdigest(),
            "truncated": truncated,
        })

    async def _handle_interrupt(self, request_id: str, writer: asyncio.StreamWriter) -> None:
        job = self._jobs.get(request_id)
        if job is None:
            await self._send(writer, {"type": "interrupt.ack", "request_id": request_id, "found": False})
            return
        job.interrupted = True
        self._terminate(job, "operator interrupt")
        self.journal.interrupted(request_id, "operator interrupt")
        await self._send(writer, {"type": "interrupt.ack", "request_id": request_id, "found": True})

    def _terminate(self, job: _Job, reason: str) -> None:
        job.interrupted = True
        proc = job.proc
        if proc.returncode is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    async def _send(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        writer.write(encode(msg))
        await writer.drain()
