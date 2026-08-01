"""Реальный адаптер Claude Code, сверенный с claude 2.1.220 (Master Spec §12.2).

Проверенный контракт:
* новый запуск: ``claude -p --output-format stream-json --verbose <prompt>``;
* продолжение: ``claude -p --resume <SESSION_ID> --output-format stream-json``;
* детерминированная сессия: ``--session-id <uuid>``; модель: ``--model``;
* изолированный root: ``CLAUDE_CONFIG_DIR=<root>``;
* статус: ``claude auth status --json`` → ``{"loggedIn": bool, "authMethod": …}``;
* логин: ``claude auth login`` (``--claudeai`` — подписка по умолчанию).

Дроп привилегий как в Codex-адаптере. auth-детали НЕ раскрывают email/account
— читается только ``loggedIn``/``authMethod``.
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

_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM")


class RealClaudeAdapter:
    provider = "claude"
    executable = "claude"

    def discover_capabilities(self) -> list[SessionCapability]:
        caps = [SessionCapability.NEW_SESSION]
        if self._available():
            caps += [SessionCapability.RESUME_BY_ID, SessionCapability.FRESH_WITH_HANDOFF,
                     SessionCapability.COMPACT]
        return caps

    def _available(self) -> bool:
        return shutil.which(self.executable) is not None

    def cli_version(self) -> str | None:
        if not self._available():
            return None
        try:
            out = subprocess.run([self.executable, "--version"], capture_output=True, text=True, timeout=15)
            return redact((out.stdout or out.stderr).strip())
        except Exception:  # noqa: BLE001
            return None

    def build_start_argv(self, job: JobPackage) -> list[str]:
        argv = [self.executable, "-p", "--output-format", "stream-json", "--verbose"]
        model = job.inputs.get("model")
        if model:
            argv += ["--model", model]
        sid = job.inputs.get("session_id")
        if sid:
            argv += ["--session-id", sid]
        # Обычный Builder использует project CLAUDE.md/settings → без --bare (§12.2).
        argv += [self._render_prompt(job)]
        return argv

    def build_resume_argv(self, session_id: str, job: JobPackage) -> list[str]:
        argv = [self.executable, "-p", "--resume", session_id,
                "--output-format", "stream-json", "--verbose", self._render_prompt(job)]
        return argv

    def _render_prompt(self, job: JobPackage) -> str:
        return job.goal

    def _wrap(self, argv: list[str], root_path: str, run_as_user: str | None) -> tuple[list[str], dict]:
        base = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env_pairs = {"CLAUDE_CONFIG_DIR": root_path, "HOME": root_path}
        if run_as_user and os.geteuid() == 0:
            wrapped = ["runuser", "-u", run_as_user, "--", "env",
                       *[f"{k}={v}" for k, v in env_pairs.items()], *argv]
            return wrapped, base
        base.update(env_pairs)
        return argv, base

    def auth_status(self, root_path: str, *, run_as_user: str | None = None) -> dict:
        if not self._available():
            return {"authenticated": False, "state": "CLI_ABSENT", "detail": "claude CLI недоступен"}
        argv, env = self._wrap([self.executable, "auth", "status", "--json"], root_path, run_as_user)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=45, env=env)
        except Exception as exc:  # noqa: BLE001
            return {"authenticated": False, "state": "ERROR", "detail": redact(str(exc))[:120]}
        logged_in = False
        auth_method = None
        try:
            data = json.loads(out.stdout)
            logged_in = bool(data.get("loggedIn"))
            auth_method = data.get("authMethod")  # none|claudeai|console — не секрет
        except (json.JSONDecodeError, TypeError):
            low = (out.stdout + out.stderr).lower()
            logged_in = "logged in" in low and "not logged in" not in low
        # detail НЕ содержит email/account — только метод
        return {"authenticated": logged_in,
                "state": "READY" if logged_in else "AUTH_REQUIRED",
                "detail": f"claude: authMethod={auth_method}" if auth_method else
                          ("claude: авторизован" if logged_in else "claude: не авторизован")}

    def capacity(self, root_path: str) -> Capacity:
        return unknown_capacity()

    def _execute(self, argv: list[str], job: JobPackage, *, profile_alias: str, root_path: str,
                 session_id: str | None, run_as_user: str | None) -> AdapterResult:
        if not self._available():
            raise AtlasError(classify("claude CLI недоступен: login required"))
        wrapped, env = self._wrap(argv, root_path, run_as_user)
        try:
            out = subprocess.run(wrapped, capture_output=True, text=True,
                                 timeout=job.inputs.get("timeout_s", 120), env=env,
                                 cwd=job.inputs.get("cwd"))
        except subprocess.TimeoutExpired as exc:
            raise AtlasError(classify("timed out", exception=exc))
        except Exception as exc:  # noqa: BLE001
            raise AtlasError(classify(str(exc), exception=exc))
        if out.returncode != 0:
            raise AtlasError(classify(out.stderr or out.stdout, exit_code=out.returncode))
        structured, sess = self._parse_stream_json(out.stdout)
        result = RunResult(
            run_id=new_id("run"), state=RunState.SUCCEEDED, provider=Provider.CLAUDE,
            profile_alias=profile_alias, session_id=sess or session_id, exit_code=0,
            structured_output=structured, finished_at=utcnow_iso(),
        )
        return AdapterResult(result=result, handoff_state={"session_id": sess or session_id})

    def _parse_stream_json(self, stdout: str) -> tuple[dict, str | None]:
        session_id = None
        result_text = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            session_id = evt.get("session_id") or session_id
            if evt.get("type") == "result":
                result_text = evt.get("result")
        structured = {}
        if isinstance(result_text, str):
            try:
                structured = json.loads(result_text)
            except (json.JSONDecodeError, TypeError):
                structured = {"text": result_text[:500]}
        return structured, session_id

    def start(self, job: JobPackage, *, profile_alias: str, root_path: str,
              run_as_user: str | None = None) -> AdapterResult:
        return self._execute(self.build_start_argv(job), job, profile_alias=profile_alias,
                             root_path=root_path, session_id=None, run_as_user=run_as_user)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               run_as_user: str | None = None) -> AdapterResult:
        return self._execute(self.build_resume_argv(session_id, job), job, profile_alias=profile_alias,
                             root_path=root_path, session_id=session_id, run_as_user=run_as_user)
