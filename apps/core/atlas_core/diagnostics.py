"""Диагностика без идентичностей (Master Spec §11.5, §31, §33 Result).

Собирает факты о хосте, версиях CLI, профилях и их health БЕЗ раскрытия
email/token/cookie/raw path. Используется CLI ``atlas-doctor``, минимальным
web-status и приёмочным прогоном.
"""

from __future__ import annotations

import platform
import shutil
import subprocess

from .adapters.real_claude import RealClaudeAdapter
from .adapters.real_codex import RealCodexAdapter
from .capacity import unknown_capacity
from .ids import utcnow_iso
from .profiles import ProfileRegistry, check_root_permissions
from .redaction import redact


def _cli_version(cmd: str) -> str:
    if shutil.which(cmd) is None:
        return "НЕ УСТАНОВЛЕН"
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=15)
        return redact((out.stdout or out.stderr).strip().splitlines()[0]) if (out.stdout or out.stderr) else "?"
    except Exception:  # noqa: BLE001
        return "ошибка запроса версии"


def host_facts() -> dict:
    return {
        "collected_at": utcnow_iso(),
        "os": redact(platform.platform()),
        "python": platform.python_version(),
        "tools": {
            "git": _cli_version("git"),
            "gh": _cli_version("gh"),
            "docker": _cli_version("docker"),
            "codex": _cli_version("codex"),
            "claude": _cli_version("claude"),
            "uv": _cli_version("uv"),
            "pnpm": _cli_version("pnpm"),
        },
        "data_dir": "[REDACTED_PATH]",
        "runner_socket": "[REDACTED_PATH]",
    }


def profile_health() -> list[dict]:
    """Health профилей: alias, provider, state, права root, capacity UNKNOWN.

    Никаких идентичностей: raw path скрыт, auth-детали redacted.
    """

    reg = ProfileRegistry()
    adapters = {"codex": RealCodexAdapter(), "claude": RealClaudeAdapter()}
    rows = []
    for p in reg.list():
        perm = check_root_permissions(p)
        adapter = adapters[p.provider]
        # probe выполняется ПОД идентичностью профиля (реальная граница),
        # если она есть и мы root; иначе — напрямую.
        auth = adapter.auth_status(p.root_path, executable=p.executable_path,
                                   run_as_user=p.runtime_user)
        cli_ver = adapter.cli_version(p.executable_path, root_path=p.root_path,
                                      run_as_user=p.runtime_user)
        rows.append({
            "alias": p.alias,  # публичный alias — единственный идентификатор
            "provider": p.provider,
            "state": ("READY" if auth.get("authenticated") else p.state.value),
            "cli_version": cli_ver or p.cli_version,
            "root_mode": perm.get("mode"),
            "root_is_0700": perm.get("is_0700"),
            "root_owner": perm.get("owner"),
            "auth_state": auth.get("state"),
            "authenticated": auth.get("authenticated"),
            "capacity": unknown_capacity().to_dict(),  # честно: остаток UNKNOWN
            "auth_detail": redact(str(auth.get("detail", "")))[:120],
        })
    return rows


def snapshot() -> dict:
    return {
        "host": host_facts(),
        "profiles": profile_health(),
        "note": "Диагностика не содержит email, token, cookie и raw path (Master Spec §11.2).",
    }
