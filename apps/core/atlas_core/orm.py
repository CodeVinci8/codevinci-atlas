"""ORM-модели Core на SQLAlchemy 2.x (Master Spec §8.1, §23).

VP-1 вводит минимальный, управляемый Alembic, срез: append-only журнал
аудита. Общие поля: сортируемый ID, UTC-время, actor, correlation. Секреты в
таблицы не пишутся (redaction на уровне сервиса, §30).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    """Append-only событие аудита (§31)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")  # уже redacted
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "message": self.message,
            "created_at": self.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
