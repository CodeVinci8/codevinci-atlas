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
import subprocess

from ..capacity import Capacity, unknown_capacity
from ..contracts import JobPackage, Provider, RunResult, RunState, SessionCapability
from ..errors import AtlasError, classify
from ..ids import new_id, utcnow_iso
from ..redaction import redact
from .base import AdapterResult


class RealClaudeAdapter:
    provider = "claude"
    executable = "claude"

    def discover_capabilities(self) -> list[SessionCapability]:
        return [SessionCapability.NEW_SESSION, SessionCapability.RESUME_BY_ID,
                SessionCapability.FRESH_WITH_HANDOFF, SessionCapability.COMPACT]

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
        exe = executable or self.executable
        return [exe, "-p", "--resume", session_id, "--output-format", "stream-json",
                "--verbose", self._render_prompt(job)]

    def build_fresh_argv(self, job: JobPackage, *, origin_session_id: str | None = None,
                         executable: str | None = None) -> list[str]:
        """FRESH_WITH_HANDOFF (§12.3): новая сессия из принятого HandoffPackage.

        При наличии origin-сессии — ``--fork-session`` (новый session-id из
        оригинала, безопасный ack handoff); иначе — свежий запуск с новым
        ``--session-id``. Prompt несёт компактный handoff-контекст (не старый чат).
        """
        exe = executable or self.executable
        if origin_session_id:
            return [exe, "-p", "--resume", origin_session_id, "--fork-session",
                    "--output-format", "stream-json", "--verbose", self._render_prompt(job)]
        return self.build_start_argv(job, exe)

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
              executable: str | None = None, run_as_user: str | None = None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("claude CLI не найден: login required"))
        argv = self.build_start_argv(job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=None, run_as_user=run_as_user)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               executable: str | None = None, run_as_user: str | None = None) -> AdapterResult:
        if not self._resolve_exe(executable):
            raise AtlasError(classify("claude CLI не найден: login required"))
        argv = self.build_resume_argv(session_id, job, self._resolve_exe(executable))
        return self._execute(argv, job, profile_alias=profile_alias, root_path=root_path,
                             session_id=session_id, run_as_user=run_as_user)
