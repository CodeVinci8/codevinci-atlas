"""ORM-модели Core на SQLAlchemy 2.x (Master Spec §8.1, §23).

VP-1 вводит минимальный, управляемый Alembic, срез: append-only журнал
аудита. Общие поля: сортируемый ID, UTC-время, actor, correlation. Секреты в
таблицы не пишутся (redaction на уровне сервиса, §30).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
            "created_at": _iso(self.created_at),
        }


class Project(Base):
    """Подключённый проект (VP-2, §35). Метаданные без credentials."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(20), index=True)  # local_git|github|archive|empty
    source_location: Mapped[str] = mapped_column(Text, default="")    # canonical path | sanitized github url
    source_ref: Mapped[str] = mapped_column(String(200), default="")  # sanitized (напр. owner/repo)
    status: Mapped[str] = mapped_column(String(20), default="connected", index=True)  # connected|disconnected
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)
    disconnected_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "source_kind": self.source_kind,
            "source_location": self.source_location, "source_ref": self.source_ref,
            "status": self.status, "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "disconnected_at": _iso(self.disconnected_at) if self.disconnected_at else None,
        }


class GitBaseline(Base):
    """Редактированный read-only baseline репозитория (§35)."""

    __tablename__ = "git_baselines"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    branch: Mapped[str] = mapped_column(String(255), default="")
    head: Mapped[str] = mapped_column(String(64), default="")
    remotes_json: Mapped[str] = mapped_column(Text, default="[]")       # sanitized
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    porcelain_json: Mapped[str] = mapped_column(Text, default="[]")     # bounded
    porcelain_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    tracked_total: Mapped[int] = mapped_column(Integer, default=0)
    tracked_changes: Mapped[int] = mapped_column(Integer, default=0)
    untracked: Mapped[int] = mapped_column(Integer, default=0)
    instructions_json: Mapped[str] = mapped_column(Text, default="[]")
    package_managers_json: Mapped[str] = mapped_column(Text, default="[]")
    baseline_commands_json: Mapped[str] = mapped_column(Text, default="[]")
    secret_scan_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    observed_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class Worktree(Base):
    """Изолированный worktree проекта (§13.4, §35)."""

    __tablename__ = "worktrees"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    branch: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)  # canonical, внутри allowlist
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|removed
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    removed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "branch": self.branch,
                "path": self.path, "status": self.status, "created_at": _iso(self.created_at),
                "removed_at": _iso(self.removed_at) if self.removed_at else None}


class WorktreeLease(Base):
    """Один writer на worktree (§13.4). Активная аренда: released_at=''.

    Атомарность единственного writer обеспечивает UNIQUE(worktree, released_at)
    при sentinel-значении ''. Управляется :mod:`atlas_core.wsleases` через прямое
    sqlite3-соединение (тот же файл БД) для атомарного acquire.
    """

    __tablename__ = "worktree_leases"
    __table_args__ = (UniqueConstraint("worktree", "released_at", name="uq_worktree_active"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    worktree: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="builder")
    holder: Mapped[str] = mapped_column(String(80), default="")
    acquired_at: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[str] = mapped_column(String(30))
    heartbeat_at: Mapped[str] = mapped_column(String(30))
    released_at: Mapped[str] = mapped_column(String(30), default="")
