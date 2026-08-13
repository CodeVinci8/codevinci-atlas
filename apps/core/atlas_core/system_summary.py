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


# Кэш последней выборки /proc/stat для дельты CPU-утилизации между вызовами.
_cpu_prev: tuple[int, int, float] | None = None


def _read_cpu_times() -> tuple[int, int] | None:
    """(total_jiffies, idle_jiffies) из первой строки /proc/stat."""
    stat = _read("/proc/stat")
    if not stat:
        return None
    for line in stat.splitlines():
        if line.startswith("cpu "):
            try:
                parts = [int(x) for x in line.split()[1:]]
            except ValueError:
                return None
            if len(parts) < 4:
                return None
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
            return sum(parts), idle
    return None


def _cpu_utilization() -> dict:
    """Реальная CPU-утилизация 0–100% из **дельты двух выборок** ``/proc/stat``
    (не преобразование load average).

    Честное первое измерение: при первом вызове (нет предыдущей выборки) реальную
    дельту посчитать НЕЛЬЗЯ → ``state=measuring`` (UI покажет «Измерение…», не
    0%). Следующий опрос (Pulse каждые 5 с) даёт настоящее окно и процент. Если
    ``/proc/stat`` недоступен → ``state=unavailable`` (UI покажет «Недоступно»).
    Никакого блокирующего bootstrap-sleep внутри запроса."""
    global _cpu_prev
    cur = _read_cpu_times()
    if cur is None:
        return {"utilization_pct": None, "sample_window_s": None,
                "source": "unavailable", "state": "unavailable"}
    now = time.monotonic()
    if _cpu_prev is None:
        # Первый сэмпл: сохраняем базовую точку, но дельты ещё нет — «Измерение…».
        _cpu_prev = (cur[0], cur[1], now)
        return {"utilization_pct": None, "sample_window_s": None,
                "source": "/proc/stat", "state": "measuring"}
    prev = (_cpu_prev[0], _cpu_prev[1])
    window = now - _cpu_prev[2]
    _cpu_prev = (cur[0], cur[1], now)
    dtotal = cur[0] - prev[0]
    didle = cur[1] - prev[1]
    # Слишком узкое окно (частый повторный вызов) → измерение недостоверно.
    if dtotal <= 0 or window < 0.05:
        return {"utilization_pct": None, "sample_window_s": round(window, 3),
                "source": "/proc/stat delta", "state": "measuring"}
    pct = (1.0 - didle / dtotal) * 100.0
    pct = max(0.0, min(100.0, pct))
    return {"utilization_pct": round(pct, 1), "sample_window_s": round(window, 3),
            "source": "/proc/stat delta", "state": "ok"}


def _cpu() -> dict:
    load = None
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = None
    util = _cpu_utilization()
    # load_avg — планировщик, НЕ CPU%; utilization_pct — реальная утилизация.
    return {"logical_cores": os.cpu_count(), "load_avg": load,
            "utilization_pct": util["utilization_pct"],
            "sample_window_s": util["sample_window_s"], "util_source": util["source"],
            "util_state": util["state"]}


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


def _is_missing_table(exc: Exception) -> bool:
    """Точно отличить «таблицы ещё нет» (до 0005) от реального сбоя БД."""
    from sqlalchemy.exc import OperationalError
    if not isinstance(exc, OperationalError):
        return False
    return "no such table" in str(getattr(exc, "orig", exc)).lower()


def _table_present(name: str) -> bool | None:
    """True/False если удалось проверить; None если сама проверка не прошла (БД сломана)."""
    from sqlalchemy import text
    try:
        with session_scope() as s:
            row = s.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
                            {"n": name}).first()
        return row is not None
    except Exception:  # noqa: BLE001 — БД недоступна/повреждена
        return None


def _run_counts() -> dict:
    # status: OK | PARTIAL (нет таблицы, напр. до 0005) | ERROR (реальный сбой БД).
    present = _table_present("runs")
    if present is None:
        return {"active": None, "queued": None, "paused": None, "owner_required": None,
                "status": "ERROR", "error": "db unavailable"}
    if present is False:
        return {"active": None, "queued": None, "paused": None, "owner_required": None,
                "status": "PARTIAL"}
    try:
        with session_scope() as s:
            rows = s.execute(select(Run.state, func.count()).group_by(Run.state)).all()
        by = {state: int(n) for state, n in rows}
        return {
            "active": sum(by.get(x, 0) for x in ("PREPARING", "RUNNING", "COLLECTING")),
            "queued": by.get("QUEUED", 0),
            "paused": by.get("PAUSED", 0),
            "owner_required": by.get("OWNER_REQUIRED", 0),
            "status": "OK",
        }
    except Exception as exc:  # noqa: BLE001 — таблица есть, но запрос упал → реальный сбой
        from .redaction import redact
        if _is_missing_table(exc):
            return {"active": None, "queued": None, "paused": None, "owner_required": None,
                    "status": "PARTIAL"}
        return {"active": None, "queued": None, "paused": None, "owner_required": None,
                "status": "ERROR", "error": redact(str(exc))[:80]}


def _lease_counts() -> dict:
    from .redaction import redact

    def _count(model, table: str):
        present = _table_present(table)
        if present is None:
            return None, "ERROR"
        if present is False:
            return None, "PARTIAL"
        try:
            with session_scope() as s:
                n = int(s.execute(select(func.count()).select_from(model)
                                  .where(model.released_at == "")).scalar_one())
            return n, "OK"
        except Exception as exc:  # noqa: BLE001
            if _is_missing_table(exc):
                return None, "PARTIAL"
            return None, f"ERROR:{redact(str(exc))[:60]}"
    w, ws = _count(WorktreeLease, "worktree_leases")
    p, ps = _count(RunLease, "run_leases")
    status = "OK" if ws == "OK" and ps == "OK" else ("PARTIAL" if "ERROR" not in (ws + ps) else "ERROR")
    return {"worktree_writers": w, "profile_leases": p, "status": status}


def _runner(settings) -> dict:
    try:
        from .runner_health import runner_health
        rh = runner_health(settings.runner_socket, settings.runner_token_file)
        return {"status": rh.get("status", "OFFLINE"), "uptime_s": rh.get("uptime_s")}
    except Exception:  # noqa: BLE001
        return {"status": "UNKNOWN", "uptime_s": None}


_ACTIONABLE_NA = frozenset({"OPEN_OWNER_RUN", "INSPECT_RUN", "CREATE_RUN",
                            "CONNECT_PROJECT", "OPEN_MAP"})


def _next_action(runs: dict) -> dict:
    """Контекстное **точное продуктовое** следующее действие (§27.2), отдельно от
    операционных предупреждений. Приоритет: owner-required Run → активный Run →
    принятый Work Order без Run → проект без принятого VP → нет проектов → всё
    завершено. Честный PARTIAL при отсутствии таблиц.

    ``actionable`` = есть ли реальное действие Run/VP, которое владелец может
    выполнить сейчас. «Всё завершено» (NEXT_VP) и PARTIAL — **не** actionable:
    UI не показывает их как фиктивное действие дашборда (напр. «VP-8 next»)."""
    from sqlalchemy import text as _text

    def _count(table: str, where=None) -> int | None:
        if _table_present(table) is not True:
            return None
        try:
            with session_scope() as s:
                q = f"SELECT COUNT(*) FROM {table}"
                if where:
                    q += f" WHERE {where}"
                return int(s.execute(_text(q)).scalar_one())
        except Exception:  # noqa: BLE001
            return None

    owner_req = runs.get("owner_required")
    active = runs.get("active")
    queued = runs.get("queued")
    if owner_req:
        return {"code": "OPEN_OWNER_RUN", "text": "Откройте Run, требующий решения владельца.",
                "target": "runs", "count": owner_req}
    if active:
        return {"code": "INSPECT_RUN", "text": "Проверьте прогресс активного Run.",
                "target": "runs", "count": active}
    if queued:
        return {"code": "INSPECT_RUN", "text": "Проверьте очередь Run.",
                "target": "runs", "count": queued}
    # Принятый (ready) Work Order без Run.
    accepted_wo = _count("work_orders",
                         "status='ready' AND id NOT IN (SELECT work_order_id FROM runs "
                         "WHERE work_order_id != '')")
    if accepted_wo:
        return {"code": "CREATE_RUN", "text": "Создайте Run по принятому Work Order.",
                "target": "workorders", "count": accepted_wo}
    projects = _count("projects", "status != 'disconnected'")
    if projects == 0:
        return {"code": "CONNECT_PROJECT", "text": "Подключите проект, чтобы начать.",
                "target": "projects"}
    # Проект без активного VP → открыть Map/Brief.
    no_vp = _count("projects",
                   "status != 'disconnected' AND id NOT IN "
                   "(SELECT project_id FROM vp_activations)")
    if no_vp:
        return {"code": "OPEN_MAP", "text": "Откройте Project Map/Brief для приёмки VP.",
                "target": "projects", "count": no_vp}
    if projects is None:
        return {"code": "PARTIAL", "text": "Состояние проектов недоступно.", "target": "pulse"}
    return {"code": "NEXT_VP", "text": "Всё завершено — перейдите к следующему VP/review.",
            "target": "quality"}


def system_summary(settings) -> dict:
    """Собрать sanitized-сводку. Partial-состояния честны (None)."""
    data_dir = settings.data_dir
    runs = _run_counts()
    next_action = _next_action(runs)
    next_action["actionable"] = next_action.get("code") in _ACTIONABLE_NA
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
        "runs": runs,
        "leases": _lease_counts(),
        "next_action": next_action,
    }


def collected_datetime() -> datetime:
    return datetime.now(timezone.utc)
