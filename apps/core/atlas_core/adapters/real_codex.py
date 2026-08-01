"""Реальный адаптер Codex, сверенный с codex-cli 0.146.0 (Master Spec §12.1).

Проверенный контракт (см. read-only аудит перед этим VP):
* новый запуск: ``codex exec --json --skip-git-repo-check -s read-only <prompt>``;
* строгий verdict: ``--output-schema <FILE>``; последнее сообщение: ``-o <FILE>``;
* продолжение: ``codex exec resume <SESSION_ID> --json <prompt>``;
* изолированный root: ``CODEX_HOME=<root>``;
* статус: ``codex login status`` (rc=0 и «Not logged in», если не авторизован);
* headless-логин: ``codex login --device-auth``.

Дроп привилегий: при ``run_as_user`` и правах root CLI запускается ``runuser
-u <идентичность> -- env …`` — под идентичностью профиля (§7.2), поэтому
процесс физически не видит чужие credentials. auth-детали НЕ раскрывают
email/account.
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

_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM")


class RealCodexAdapter:
    provider = "codex"
    executable = "codex"

    def discover_capabilities(self) -> list[SessionCapability]:
        caps = [SessionCapability.NEW_SESSION]
        if self._available():
            caps += [SessionCapability.RESUME_BY_ID, SessionCapability.FRESH_WITH_HANDOFF,
                     SessionCapability.FORK_NATIVE]
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

    # --- построение argv (чистое, тестируется) -----------------------------
    def build_start_argv(self, job: JobPackage) -> list[str]:
        argv = [self.executable, "exec", "--json", "--skip-git-repo-check", "-s", "read-only"]
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

    def build_resume_argv(self, session_id: str, job: JobPackage) -> list[str]:
        argv = [self.executable, "exec", "resume", session_id, "--json",
                "--skip-git-repo-check", "-s", "read-only"]
        argv += [self._render_prompt(job)]
        return argv

    def _render_prompt(self, job: JobPackage) -> str:
        # Компактный prompt: без полного chat/repo (§16.3).
        return job.goal

    # --- окружение и дроп привилегий ---------------------------------------
    def _wrap(self, argv: list[str], root_path: str, run_as_user: str | None) -> tuple[list[str], dict]:
        import os
        base = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env_pairs = {"CODEX_HOME": root_path, "HOME": root_path}
        if run_as_user and os.geteuid() == 0:
            # runuser сбрасывает окружение → задаём переменные через env(1)
            wrapped = ["runuser", "-u", run_as_user, "--", "env",
                       *[f"{k}={v}" for k, v in env_pairs.items()], *argv]
            return wrapped, base
        base.update(env_pairs)
        return argv, base

    def auth_status(self, root_path: str, *, run_as_user: str | None = None) -> dict:
        if not self._available():
            return {"authenticated": False, "state": "CLI_ABSENT",
                    "detail": "codex CLI не установлен"}
        argv, env = self._wrap([self.executable, "login", "status"], root_path, run_as_user)
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)
        except Exception as exc:  # noqa: BLE001
            return {"authenticated": False, "state": "ERROR", "detail": redact(str(exc))[:120]}
        low = f"{out.stdout}\n{out.stderr}".lower()
        authed = ("logged in" in low) and ("not logged in" not in low)
        # detail НЕ содержит account/email — только факт состояния
        return {"authenticated": authed,
                "state": "READY" if authed else "AUTH_REQUIRED",
                "detail": "codex: авторизован" if authed else "codex: не авторизован"}

    def capacity(self, root_path: str) -> Capacity:
        return unknown_capacity()

    # --- исполнение ---------------------------------------------------------
    def _execute(self, argv: list[str], job: JobPackage, *, profile_alias: str, root_path: str,
                 session_id: str | None, run_as_user: str | None) -> AdapterResult:
        if not self._available():
            raise AtlasError(classify("codex CLI не установлен: login required"))
        wrapped, env = self._wrap(argv, root_path, run_as_user)
        try:
            out = subprocess.run(wrapped, capture_output=True, text=True,
                                 timeout=job.inputs.get("timeout_s", 120), env=env)
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
        session_id = None
        last_text = None
        last_obj: dict = {}
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
            # id сессии/треда встречается под разными ключами в зависимости от версии
            for key in ("thread_id", "session_id", "conversation_id", "id"):
                if evt.get(key):
                    session_id = evt[key]
                    break
            # финальное сообщение ассистента
            msg = evt.get("message") or evt.get("text") or evt.get("last_agent_message")
            if isinstance(msg, str):
                last_text = msg
            last_obj = evt
        structured = {}
        if last_text:
            try:
                structured = json.loads(last_text)
            except (json.JSONDecodeError, TypeError):
                structured = {"text": last_text[:500]}
        elif last_obj:
            structured = {"last_event_type": last_obj.get("type")}
        return structured, session_id

    def start(self, job: JobPackage, *, profile_alias: str, root_path: str,
              run_as_user: str | None = None) -> AdapterResult:
        return self._execute(self.build_start_argv(job), job, profile_alias=profile_alias,
                             root_path=root_path, session_id=None, run_as_user=run_as_user)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               run_as_user: str | None = None) -> AdapterResult:
        return self._execute(self.build_resume_argv(session_id, job), job, profile_alias=profile_alias,
                             root_path=root_path, session_id=session_id, run_as_user=run_as_user)
