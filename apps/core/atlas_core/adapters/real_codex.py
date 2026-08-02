"""Реальный адаптер Codex, сверенный с codex-cli 0.146.0 на живом CLI.

Формат событий подтверждён реальным прогоном (redacted):
* `thread.started` → `thread_id` (UUID сессии/треда);
* `turn.started`;
* `item.completed` → `item{type:"agent_message", text:"<ответ>"}`;
* `turn.completed` → `usage`.

Контракт:
* новый запуск: ``codex exec --json --skip-git-repo-check -s read-only -C <cwd> <prompt>``;
* строгий verdict: ``--output-schema <FILE>``;
* продолжение: ``codex exec resume <SESSION_ID> --json …``;
* статус: ``codex login status`` (rc=0; текст «Not logged in», если не авторизован);
* изолированный root: ``CODEX_HOME=<root>``; per-profile исполняемый файл.

Изоляция: под ``run_as_user`` и root запуск идёт через
``runuser -u <ident> -- env -i HOME=… CODEX_HOME=… PATH=… <exe> …`` — под
идентичностью профиля, поэтому чужие credentials недоступны. stdin закрыт
(codex иначе блокируется на чтении stdin). auth-детали НЕ раскрывают account.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from ..capacity import Capacity, unknown_capacity
from ..contracts import JobPackage, Provider, RunResult, RunState, SessionCapability
from ..errors import AtlasError, classify
from ..ids import new_id, utcnow_iso
from ..redaction import redact
from .base import AdapterResult


class RealCodexAdapter:
    provider = "codex"
    executable = "codex"

    def discover_capabilities(self) -> list[SessionCapability]:
        return [SessionCapability.NEW_SESSION, SessionCapability.RESUME_BY_ID,
                SessionCapability.FRESH_WITH_HANDOFF, SessionCapability.FORK_NATIVE]

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

    # --- построение argv (чистое, тестируется) -----------------------------
    def build_start_argv(self, job: JobPackage, executable: str | None = None) -> list[str]:
        exe = executable or self.executable
        argv = [exe, "exec", "--json", "--skip-git-repo-check", "-s", "read-only"]
        cwd = job.inputs.get("cwd")
        if cwd:
            argv += ["-C", cwd]
        model = job.inputs.get("model")
        if model:
            argv += ["-m", model]
        if job.output_schema_ref:
            argv += ["--output-schema", job.output_schema_ref]
        argv += [self._render_prompt(job)]
        return argv

    def build_resume_argv(self, session_id: str, job: JobPackage, executable: str | None = None) -> list[str]:
        exe = executable or self.executable
        argv = [exe, "exec", "resume", session_id, "--json", "--skip-git-repo-check", "-s", "read-only"]
        cwd = job.inputs.get("cwd")
        if cwd:
            argv += ["-C", cwd]
        argv += [self._render_prompt(job)]
        return argv

    def build_fresh_argv(self, job: JobPackage, executable: str | None = None) -> list[str]:
        """FRESH_WITH_HANDOFF (§12.3): для Codex свежая сессия — это новый
        ``codex exec`` с компактным handoff-контекстом в prompt (не resume)."""
        return self.build_start_argv(job, executable or self._resolve_exe(executable))

    def _render_prompt(self, job: JobPackage) -> str:
        return job.goal  # компактный prompt, без полного chat/repo (§16.3)

    # --- окружение и дроп привилегий ---------------------------------------
    def _wrap(self, argv: list[str], root_path: str, run_as_user: str | None) -> tuple[list[str], dict]:
        import os
        safe_path = f"/usr/bin:/bin:{root_path}/.local/bin"
        if run_as_user and os.geteuid() == 0:
            # runuser + env -i: полностью изолированное окружение идентичности
            wrapped = ["runuser", "-u", run_as_user, "--", "env", "-i",
                       f"HOME={root_path}", f"CODEX_HOME={root_path}", f"PATH={safe_path}",
                       *argv]
            return wrapped, {}
        env = {"HOME": root_path, "CODEX_HOME": root_path, "PATH": safe_path}
        for k in ("LANG", "LC_ALL", "TERM"):
            if k in os.environ:
                env[k] = os.environ[k]
        return argv, {"env": env}

    def auth_status(self, root_path: str, *, executable: str | None = None,
                    run_as_user: str | None = None) -> dict:
        exe = self._resolve_exe(executable)
        if not exe:
            return {"authenticated": False, "state": "CLI_ABSENT", "detail": "codex CLI не найден"}
        argv, kw = self._wrap([exe, "login", "status"], root_path, run_as_user)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"authenticated": False, "state": "ERROR", "detail": redact(str(exc))[:120]}
        low = f"{out.stdout}\n{out.stderr}".lower()
        authed = ("logged in" in low) and ("not logged in" not in low)
        return {"authenticated": authed,
                "state": "READY" if authed else "AUTH_REQUIRED",
                "detail": "codex: авторизован" if authed else "codex: не авторизован"}

    def capacity(self, root_path: str) -> Capacity:
        return unknown_capacity()

    # --- исполнение ---------------------------------------------------------
    def _execute(self, argv: list[str], job: JobPackage, *, profile_alias: str, root_path: str,
                 session_id: str | None, run_as_user: str | None) -> AdapterResult:
        wrapped, kw = self._wrap(argv, root_path, run_as_user)
        try:
            out = subprocess.run(wrapped, capture_output=True, text=True,
                                 timeout=job.inputs.get("timeout_s", 150),
                                 stdin=subprocess.DEVNULL, **kw)
        except subprocess.TimeoutExpired as exc:
            raise AtlasError(classify("timed out", exception=exc))
        except Exception as exc:  # noqa: BLE001
            raise AtlasError(classify(str(exc), exception=exc))
        if out.returncode != 0:
            raise AtlasError(classify(out.stderr or out.stdout, exit_code=out.returncode))
        structured, sess = self._parse_json_events(out.stdout)
        result = RunResult(
            run_id=new_id("run"), state=RunState.SUCCEEDED, provider=Provider.CODEX,
            profile_alias=profile_alias, session_id=sess or session_id, exit_code=0,
            structured_output=structured, finished_at=utcnow_iso(),
        )
        return AdapterResult(result=result, handoff_state={"session_id": sess or session_id})

    def _parse_json_events(self, stdout: str) -> tuple[dict, str | None]:
        """Разобрать поток codex exec --json (проверенный формат)."""
        session_id = None
        answer_text = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue  # пропускаем не-JSON (напр. "Reading additional input…")
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            if evt.get("type") == "thread.started" and evt.get("thread_id"):
                session_id = evt["thread_id"]
            elif evt.get("type") == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    answer_text = item["text"]
        structured: dict = {}
        if answer_text:
            structured = self._coerce_json(answer_text)
        return structured, session_id

    @staticmethod
    def _coerce_json(text: str) -> dict:
        text = text.strip()
        try:
            v = json.loads(text)
            return v if isinstance(v, dict) else {"value": v}
        except (json.JSONDecodeError, TypeError):
            pass
        # выделить первый JSON-объект из текста
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
              executable: str | None = None, run_as_user: str | None = None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("codex CLI не найден: login required"))
        argv = self.build_start_argv(job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=None, run_as_user=run_as_user)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               executable: str | None = None, run_as_user: str | None = None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("codex CLI не найден: login required"))
        argv = self.build_resume_argv(session_id, job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=session_id, run_as_user=run_as_user)
