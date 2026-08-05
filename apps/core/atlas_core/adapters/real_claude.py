"""Реальный адаптер Claude Code, сверенный с claude 2.1.220 на живом CLI.

Формат событий подтверждён реальным прогоном (redacted):
* `system`(init) → `session_id`, tools, model;
* `assistant` → `message`;
* `result` → `session_id`, `is_error`, `result` (финальный текст), `usage`.

Контракт:
* новый запуск: ``claude -p --output-format stream-json --verbose <prompt>``;
* продолжение: ``claude -p --resume <SESSION_ID> --output-format stream-json``;
* детерминированная сессия: ``--session-id <uuid>``; модель: ``--model``;
* статус: ``claude auth status --json`` → ``{"loggedIn":bool,"authMethod":…}``;
* изолированный root: ``CLAUDE_CONFIG_DIR=<root>``; per-profile исполняемый файл.

Изоляция как в Codex-адаптере (runuser + env -i). stdin закрыт. auth-детали
раскрывают только ``loggedIn``/``authMethod`` — никаких email/account.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time

from ..capacity import Capacity, unknown_capacity
from ..contracts import JobPackage, Provider, RunResult, RunState, SessionCapability
from ..errors import AtlasError, classify
from ..ids import new_id, utcnow_iso
from ..redaction import redact
from .base import AdapterResult


class _Out:
    """Лёгкий носитель результата subprocess (rc/stdout/stderr) для единого пути."""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_cancellable(argv, kw, *, timeout: float, cancel_event, poll: float = 0.4):
    """Запустить процесс в отдельной группе и прерывать по ``cancel_event`` (Emergency
    Stop) сигналами группе: SIGTERM, затем SIGKILL. Возвращает (rc, stdout, stderr,
    cancelled). При timeout — тоже группа-kill и raise TimeoutExpired-эквивалент."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         stdin=subprocess.DEVNULL, start_new_session=True, **kw)

    def _kill_group():
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                p.terminate()
            except OSError:
                return
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    p.kill()
                except OSError:
                    pass

    deadline = time.monotonic() + timeout
    cancelled = False
    while True:
        try:
            p.wait(timeout=poll)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel_event is not None and cancel_event.is_set():
            _kill_group()
            cancelled = True
            break
        if time.monotonic() > deadline:
            _kill_group()
            try:
                stdout, stderr = p.communicate(timeout=2)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""
            raise AtlasError(classify("timed out"))
    try:
        stdout, stderr = p.communicate(timeout=3)
    except Exception:  # noqa: BLE001
        stdout, stderr = "", ""
    return p.returncode, stdout or "", stderr or "", cancelled


class RealClaudeAdapter:
    provider = "claude"
    executable = "claude"

    def discover_capabilities(self) -> list[SessionCapability]:
        return [SessionCapability.NEW_SESSION, SessionCapability.RESUME_BY_ID,
                SessionCapability.FRESH_WITH_HANDOFF, SessionCapability.FORK_NATIVE,
                SessionCapability.COMPACT]

    def _resolve_exe(self, executable: str | None) -> str | None:
        return executable or shutil.which(self.executable)

    def cli_version(self, executable: str | None = None, *, root_path: str | None = None,
                    run_as_user: str | None = None) -> str | None:
        exe = self._resolve_exe(executable)
        if not exe:
            return None
        argv, kw = self._wrap([exe, "--version"], root_path or "/tmp", run_as_user)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                                 stdin=subprocess.DEVNULL, **kw)
            return redact((out.stdout or out.stderr).strip())
        except Exception:  # noqa: BLE001
            return None

    def build_start_argv(self, job: JobPackage, executable: str | None = None) -> list[str]:
        exe = executable or self.executable
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose"]
        model = job.inputs.get("model")
        if model:
            argv += ["--model", model]
        sid = job.inputs.get("session_id")
        if sid:
            argv += ["--session-id", sid]
        argv += [self._render_prompt(job)]
        return argv

    def build_resume_argv(self, session_id: str, job: JobPackage, executable: str | None = None) -> list[str]:
        """EXACT_RESUME (§12.3): продолжить ТУ ЖЕ совместимую сессию по её ID.

        Требует того же профиля (``CLAUDE_CONFIG_DIR``), где эта сессия создана.
        """
        exe = executable or self.executable
        return [exe, "-p", "--resume", session_id, "--output-format", "stream-json",
                "--verbose", self._render_prompt(job)]

    def build_fork_argv(self, origin_session_id: str, job: JobPackage,
                        executable: str | None = None) -> list[str]:
        """FORK_SESSION (§12.3, optional): новый session-id, КОПИРУЮЩИЙ историю
        оригинала (``--resume <id> --fork-session``).

        ВНИМАНИЕ: это НЕ fresh-session. Форк несёт прежний provider-контекст,
        поэтому допустим ТОЛЬКО в пределах того же профиля и при явном намерении
        сохранить историю. Для смены профиля использовать build_fresh_argv.
        """
        exe = executable or self.executable
        return [exe, "-p", "--resume", origin_session_id, "--fork-session",
                "--output-format", "stream-json", "--verbose", self._render_prompt(job)]

    def build_fresh_argv(self, job: JobPackage, *, new_session_id: str | None = None,
                         executable: str | None = None) -> list[str]:
        """FRESH_WITH_HANDOFF (§12.3): ГЕНУИННО новая сессия БЕЗ прежней истории.

        Никакого ``--resume``/``--fork-session``: контекст берётся только из
        принятого HandoffPackage (компактный prompt) + ack. Это единственный
        безопасный вариант при смене профиля (origin-сессия недоступна из чужого
        ``CLAUDE_CONFIG_DIR``). Опционально фиксируем детерминированный
        ``--session-id`` для нового запуска.
        """
        exe = executable or self.executable
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose"]
        model = job.inputs.get("model")
        if model:
            argv += ["--model", model]
        if new_session_id:
            argv += ["--session-id", new_session_id]
        argv += [self._render_prompt(job)]
        return argv

    def _render_prompt(self, job: JobPackage) -> str:
        return job.goal

    def _wrap(self, argv: list[str], root_path: str, run_as_user: str | None) -> tuple[list[str], dict]:
        safe_path = f"/usr/bin:/bin:{root_path}/.local/bin"
        if run_as_user and os.geteuid() == 0:
            wrapped = ["runuser", "-u", run_as_user, "--", "env", "-i",
                       f"HOME={root_path}", f"CLAUDE_CONFIG_DIR={root_path}", f"PATH={safe_path}",
                       *argv]
            return wrapped, {"cwd": root_path}
        env = {"HOME": root_path, "CLAUDE_CONFIG_DIR": root_path, "PATH": safe_path}
        for k in ("LANG", "LC_ALL", "TERM"):
            if k in os.environ:
                env[k] = os.environ[k]
        return argv, {"env": env, "cwd": root_path}

    def auth_status(self, root_path: str, *, executable: str | None = None,
                    run_as_user: str | None = None) -> dict:
        exe = self._resolve_exe(executable)
        if not exe:
            return {"authenticated": False, "state": "CLI_ABSENT", "detail": "claude CLI не найден"}
        argv, kw = self._wrap([exe, "auth", "status", "--json"], root_path, run_as_user)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=45,
                                 stdin=subprocess.DEVNULL, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"authenticated": False, "state": "ERROR", "detail": redact(str(exc))[:120]}
        logged_in, auth_method = False, None
        try:
            data = json.loads(out.stdout)
            logged_in = bool(data.get("loggedIn"))
            auth_method = data.get("authMethod")  # none|claude.ai|console — не секрет
        except (json.JSONDecodeError, TypeError):
            low = (out.stdout + out.stderr).lower()
            logged_in = "logged in" in low and "not logged in" not in low
        return {"authenticated": logged_in,
                "state": "READY" if logged_in else "AUTH_REQUIRED",
                "detail": f"claude: authMethod={auth_method}" if auth_method else
                          ("claude: авторизован" if logged_in else "claude: не авторизован")}

    def capacity(self, root_path: str) -> Capacity:
        return unknown_capacity()

    def _execute(self, argv: list[str], job: JobPackage, *, profile_alias: str, root_path: str,
                 session_id: str | None, run_as_user: str | None, cancel_event=None) -> AdapterResult:
        wrapped, kw = self._wrap(argv, root_path, run_as_user)
        timeout = job.inputs.get("timeout_s", 150)
        if cancel_event is None:
            # Обычный bounded-путь (VP-5 pipeline): без изменения поведения.
            try:
                out = subprocess.run(wrapped, capture_output=True, text=True,
                                     timeout=timeout, stdin=subprocess.DEVNULL, **kw)
            except subprocess.TimeoutExpired as exc:
                raise AtlasError(classify("timed out", exception=exc))
            except Exception as exc:  # noqa: BLE001
                raise AtlasError(classify(str(exc), exception=exc))
            rc, stdout, stderr = out.returncode, out.stdout, out.stderr
        else:
            # call-8 C: отменяемый путь — Emergency Stop прерывает процесс группой
            # сигналов (SIGTERM→SIGKILL) ДО нормального timeout; поздний результат
            # не фиксируется (raise INTERRUPTED).
            rc, stdout, stderr, cancelled = _run_cancellable(
                wrapped, kw, timeout=timeout, cancel_event=cancel_event)
            if cancelled:
                raise AtlasError(classify("emergency stop: builder interrupted", exit_code=137))
        if rc != 0:
            raise AtlasError(classify(stderr or stdout, exit_code=rc))
        out = _Out(rc, stdout, stderr)
        structured, sess, is_error = self._parse_stream_json(out.stdout)
        if is_error:
            raise AtlasError(classify("invalid output: claude result is_error", exit_code=1))
        result = RunResult(
            run_id=new_id("run"), state=RunState.SUCCEEDED, provider=Provider.CLAUDE,
            profile_alias=profile_alias, session_id=sess or session_id, exit_code=0,
            structured_output=structured, finished_at=utcnow_iso(),
        )
        return AdapterResult(result=result, handoff_state={"session_id": sess or session_id})

    def _parse_stream_json(self, stdout: str) -> tuple[dict, str | None, bool]:
        session_id = None
        result_text = None
        is_error = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            session_id = evt.get("session_id") or session_id
            if evt.get("type") == "result":
                is_error = bool(evt.get("is_error"))
                result_text = evt.get("result")
        structured = self._coerce_json(result_text) if isinstance(result_text, str) else {}
        return structured, session_id, is_error

    @staticmethod
    def _coerce_json(text: str) -> dict:
        text = (text or "").strip()
        try:
            v = json.loads(text)
            return v if isinstance(v, dict) else {"value": v}
        except (json.JSONDecodeError, TypeError):
            pass
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                v = json.loads(m.group(0))
                return v if isinstance(v, dict) else {"value": v}
            except json.JSONDecodeError:
                pass
        return {"text": text[:500]}

    def start(self, job: JobPackage, *, profile_alias: str, root_path: str,
              executable: str | None = None, run_as_user: str | None = None,
              cancel_event=None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("claude CLI не найден: login required"))
        argv = self.build_start_argv(job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=None, run_as_user=run_as_user, cancel_event=cancel_event)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               executable: str | None = None, run_as_user: str | None = None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("claude CLI не найден: login required"))
        argv = self.build_resume_argv(session_id, job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=session_id, run_as_user=run_as_user)
