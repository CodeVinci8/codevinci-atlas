"""Сервис аудита (append-only) поверх ORM (Master Spec §31)."""

from __future__ import annotations

from sqlalchemy import select

from .db import session_scope
from .ids import new_id
from .orm import AuditEvent
from .redaction import redact


def record(event_type: str, message: str = "", *, actor: str = "core",
           correlation_id: str = "") -> str:
    aid = new_id("aud")
    with session_scope() as s:
        s.add(AuditEvent(id=aid, event_type=event_type, actor=actor,
                         correlation_id=correlation_id, message=redact(message)))
        s.commit()
    return aid


def query(*, event_type: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    limit = max(1, min(limit, 500))
    with session_scope() as s:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        if event_type:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        stmt = stmt.limit(limit).offset(offset)
        return [e.to_dict() for e in s.execute(stmt).scalars().all()]


def count() -> int:
    from sqlalchemy import func
    with session_scope() as s:
        return int(s.execute(select(func.count()).select_from(AuditEvent)).scalar_one())
