"""Emergency Stop (Master Spec §19, §31).

Emergency Stop немедленно **запрещает новые jobs**, **прерывает** interruptible
active jobs, **безопасно снимает** активные leases (release, не удаление),
сохраняет БД/artifacts/worktrees/checkpoints, эмитит полный Audit, требует
**явного owner-resume** и **переживает рестарт** Core/Runner (durable-состояние
в таблице ``emergency_stops``). Молча не реактивируется: текущее состояние — это
``active`` последней записи lifecycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from . import audit
from .db import session_scope
from .ids import new_id, utcnow_iso
from .orm import EmergencyStop, Run, RunLease, WorktreeLease
from .redaction import redact

# Интеррумпируемые активные состояния Run (§17.4).
INTERRUPTIBLE_STATES = ("PREPARING", "RUNNING", "COLLECTING", "RATE_LIMITED", "AUTH_REQUIRED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# call-9 fix (TOCTOU): in-process флаг «engage начался» — становится истинным ДО
# durable-записи active=True и cancel_all_jobs(), поэтому blocks_new_jobs() запрещает
# новые старты сразу, закрывая окно между снимком jobs и durable-commit. Снимается
# после durable-commit (тогда истину держит durable active) и на resume.
import threading as _threading  # noqa: E402

_ENGAGING = _threading.Event()


def is_active() -> bool:
    """Активен ли Emergency Stop сейчас (durable, переживает рестарт)."""
    with session_scope() as s:
        row = s.execute(select(EmergencyStop)
                        .order_by(EmergencyStop.created_at.desc(), EmergencyStop.id.desc())
                        .limit(1)).scalars().first()
        return bool(row.active) if row is not None else False


def blocks_new_jobs() -> bool:
    """Новые jobs запрещены, пока Emergency Stop активен ИЛИ engage() в процессе
    (закрытие TOCTOU-окна до durable-commit)."""
    return _ENGAGING.is_set() or is_active()


def status() -> dict:
    with session_scope() as s:
        row = s.execute(select(EmergencyStop)
                        .order_by(EmergencyStop.created_at.desc(), EmergencyStop.id.desc())
                        .limit(1)).scalars().first()
        if row is None:
            return {"active": False, "since": None, "reason": "", "actor": "",
                    "interrupted_runs": [], "released_leases": []}
        return {"active": bool(row.active), "since": _iso(row.created_at),
                "reason": row.reason, "actor": row.actor, "action": row.action,
                "interrupted_runs": _loads(row.interrupted_runs_json),
                "released_leases": _loads(row.released_leases_json)}


def engage(*, reason: str = "", actor: str = "owner", correlation_id: str = "") -> dict:
    """Активировать Emergency Stop. Идемпотентно: повторный engage при активном
    состоянии не создаёт вторую активную запись (возвращает текущий статус)."""
    if is_active():
        audit.record("emergency.stop.engage.noop", "уже активен", actor=actor,
                     correlation_id=correlation_id)
        return status()

    # call-9 fix (TOCTOU): пометить «engage начался» ПЕРВЫМ действием — с этого
    # момента blocks_new_jobs() истинно, и любой новый/re-checking job аборт-ится
    # ещё до durable-commit. Снимаем флаг в finally после commit (истину держит
    # durable active=True).
    _ENGAGING.set()
    try:
        audit.record("emergency.stop.engaged.before",
                     f"reason={redact(reason)[:60]}", actor=actor, correlation_id=correlation_id)

        # call-8 C: немедленно прервать in-flight Builder-процессы (сигналы группе).
        # Порядок с _ENGAGING гарантирует: job, зарегистрировавшийся до снимка, будет
        # отменён; стартующий после — увидит blocks_new_jobs() и аборт-ится.
        try:
            from .runtime import cancel_all_jobs
            cancelled_jobs = cancel_all_jobs()
            if cancelled_jobs:
                audit.record("emergency.stop.jobs_cancelled", f"count={cancelled_jobs}",
                             actor=actor, correlation_id=correlation_id)
        except Exception:  # noqa: BLE001 — прерывание in-flight не должно ломать engage
            pass

        interrupted = _interrupt_active_runs(correlation_id=correlation_id)
        released = _release_active_leases()

        import json as _json
        sid = new_id("estop")
        now = _now()
        with session_scope() as s:
            s.add(EmergencyStop(
                id=sid, action="ENGAGED", active=True, reason=redact(reason)[:800],
                actor=actor, correlation_id=correlation_id,
                interrupted_runs_json=_json.dumps(interrupted, ensure_ascii=False),
                released_leases_json=_json.dumps(released, ensure_ascii=False),
                created_at=now))
            s.commit()
        audit.record("emergency.stop.engaged.after",
                     f"interrupted={len(interrupted)} released={len(released)}",
                     actor=actor, correlation_id=correlation_id)
    finally:
        _ENGAGING.clear()  # durable active=True теперь держит blocks_new_jobs()
    return status()


def resume(*, actor: str = "owner", correlation_id: str = "") -> dict:
    """Явно снять Emergency Stop (owner-действие). Не рестартует прерванные Run —
    их возобновляет владелец отдельно. Никогда не срабатывает автоматически."""
    if not is_active():
        return {"resumed": False, "reason": "Emergency Stop не активен", **status()}
    audit.record("emergency.stop.resume.before", "", actor=actor,
                 correlation_id=correlation_id)
    rid = new_id("estop")
    with session_scope() as s:
        s.add(EmergencyStop(id=rid, action="RESUMED", active=False, reason="",
                            actor=actor, correlation_id=correlation_id,
                            created_at=_now()))
        s.commit()
    audit.record("emergency.stop.resumed", "явный owner-resume", actor=actor,
                 correlation_id=correlation_id)
    return {"resumed": True, **status()}


def _interrupt_active_runs(*, correlation_id: str = "") -> list[str]:
    """Перевести interruptible active Run → INTERRUPTED (не удаляя данные).
    Bounded ORM-апдейт с bump version; безопасен и идемпотентен."""
    interrupted: list[str] = []
    with session_scope() as s:
        rows = s.execute(select(Run).where(Run.state.in_(INTERRUPTIBLE_STATES))).scalars().all()
        for r in rows:
            s.execute(update(Run).where(Run.id == r.id, Run.version == r.version).values(
                state="INTERRUPTED", blocker="Emergency Stop",
                next_action="Ожидается явный owner-resume Emergency Stop.",
                version=r.version + 1, updated_at=_now()))
            interrupted.append(r.id)
        s.commit()
    return interrupted


def _release_active_leases() -> list[str]:
    """Безопасно снять активные leases (release: released_at=now), НЕ удаляя
    строки/worktrees/данные. Возвращает список ref снятых аренд."""
    released: list[str] = []
    ts = utcnow_iso()
    with session_scope() as s:
        for model, prefix in ((RunLease, "run_lease"), (WorktreeLease, "wt_lease")):
            rows = s.execute(select(model).where(model.released_at == "")).scalars().all()
            for r in rows:
                r.released_at = ts
                released.append(f"{prefix}:{r.id}")
        s.commit()
    return released


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _loads(blob: str):
    import json as _json
    try:
        return _json.loads(blob or "[]")
    except (ValueError, TypeError):
        return []
