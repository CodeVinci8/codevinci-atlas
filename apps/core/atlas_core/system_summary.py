"""VP-5 Pulse system summary (Master Spec §27.2, §30, §31).

Правдивая сводка среды выполнения Core БЕЗ раскрытия идентифицирующих данных:
никаких public/private IP, raw hostname, Unix-имён кроме безопасных сервис-меток,
auth-root путей, container env и credentials. Отсутствующие/недоступные значения
возвращаются как ``None`` (частичные состояния честны, не выдумываются).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from .db import session_scope
from .ids import utcnow_iso
from .orm import Run, RunLease, WorktreeLease

# Момент старта процесса Core — для честного uptime сервиса.
_CORE_START = time.time()


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _cpu() -> dict:
    load = None
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = None
    return {"logical_cores": os.cpu_count(), "load_avg": load}


def _memory() -> dict:
    meminfo = _read("/proc/meminfo")
    if not meminfo:
        return {"total_bytes": None, "used_bytes": None}
    vals = {}
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
            vals[parts[0].rstrip(":")] = int(parts[1]) * 1024
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    used = (total - avail) if (total is not None and avail is not None) else None
    return {"total_bytes": total, "used_bytes": used}


def _disk(data_dir: str) -> dict:
    try:
        u = shutil.disk_usage(data_dir)
        return {"total_bytes": u.total, "used_bytes": u.used}
    except OSError:
        return {"total_bytes": None, "used_bytes": None}


def _os_identity() -> dict:
    name, version = None, None
    osr = _read("/etc/os-release")
    if osr:
        for line in osr.splitlines():
            if line.startswith("NAME="):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("VERSION="):
                version = line.split("=", 1)[1].strip().strip('"')
    un = os.uname() if hasattr(os, "uname") else None
    # Sanitized machine id — хеш /etc/machine-id, НЕ hostname; nodename не раскрываем.
    mid_raw = _read("/etc/machine-id")
    machine_id = ("m-" + hashlib.sha256(mid_raw.strip().encode()).hexdigest()[:12]) if mid_raw else None
    return {
        "os_name": name or platform.system() or None,
        "os_version": version,
        "kernel": (un.release if un else None),
        "arch": (un.machine if un else platform.machine() or None),
        "machine_id": machine_id,  # sanitized, необратимый
    }


def _uptime_host() -> float | None:
    up = _read("/proc/uptime")
    if not up:
        return None
    try:
        return float(up.split()[0])
    except (ValueError, IndexError):
        return None


def _backup_age_s(data_dir: str) -> float | None:
    bdir = Path(data_dir) / "backups"
    if not bdir.is_dir():
        return None
    newest = None
    for f in bdir.iterdir():
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    return (time.time() - newest) if newest else None


def _db_migration() -> str | None:
    from sqlalchemy import text
    try:
        with session_scope() as s:
            v = s.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
            return v[0] if v else None
    except Exception:  # noqa: BLE001
        return None


def _run_counts() -> dict:
    with session_scope() as s:
        rows = s.execute(select(Run.state, func.count()).group_by(Run.state)).all()
    by = {state: int(n) for state, n in rows}
    return {
        "active": sum(by.get(x, 0) for x in ("PREPARING", "RUNNING", "COLLECTING")),
        "queued": by.get("QUEUED", 0),
        "paused": by.get("PAUSED", 0),
        "owner_required": by.get("OWNER_REQUIRED", 0),
    }


def _lease_counts() -> dict:
    with session_scope() as s:
        writers = int(s.execute(select(func.count()).select_from(WorktreeLease)
                                .where(WorktreeLease.released_at == "")).scalar_one())
        profiles = int(s.execute(select(func.count()).select_from(RunLease)
                                 .where(RunLease.released_at == "")).scalar_one())
    return {"worktree_writers": writers, "profile_leases": profiles}


def _runner(settings) -> dict:
    try:
        from .runner_health import runner_health
        rh = runner_health(settings.runner_socket, settings.runner_token_file)
        return {"status": rh.get("status", "OFFLINE"), "uptime_s": rh.get("uptime_s")}
    except Exception:  # noqa: BLE001
        return {"status": "UNKNOWN", "uptime_s": None}


def system_summary(settings) -> dict:
    """Собрать sanitized-сводку. Partial-состояния честны (None)."""
    data_dir = settings.data_dir
    return {
        "collected_at": utcnow_iso(),
        "atlas_version": settings.version,
        "db_migration": _db_migration(),
        "cpu": _cpu(),
        "memory": _memory(),
        "disk": _disk(data_dir),
        "os": _os_identity(),
        "host_uptime_s": _uptime_host(),
        "services": {
            # Core наблюдает себя и Runner; Web напрямую не наблюдает → UNKNOWN (честно).
            "core": {"status": "READY", "uptime_s": round(time.time() - _CORE_START, 1)},
            "runner": _runner(settings),
            "web": {"status": "UNKNOWN", "uptime_s": None,
                    "note": "Core не наблюдает Web напрямую"},
        },
        "backup_age_s": _backup_age_s(data_dir),
        "runs": _run_counts(),
        "leases": _lease_counts(),
    }


def collected_datetime() -> datetime:
    return datetime.now(timezone.utc)
