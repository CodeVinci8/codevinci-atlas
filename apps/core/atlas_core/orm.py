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


# --- VP-3 Product Map (Master Spec §36) ------------------------------------
# Immutable-версии Brief/Map, поштучные решения, envelope, parking lot,
# approval, один активный VP, идемпотентность. Содержимое bounded+redacted;
# content_hash — sha256 над canonical-JSON. Таблицы создаёт только Alembic.


class ProductIntake(Base):
    """Bounded owner-intake проекта (§36). Только данные, не команды (§30.2)."""

    __tablename__ = "product_intakes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")   # redacted, bounded
    refs_json: Mapped[str] = mapped_column(Text, default="[]")      # sanitized links/baseline refs
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Brief(Base):
    """Immutable-версия Product Brief (§36). Правка = новая версия."""

    __tablename__ = "briefs"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_brief_project_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|approved|superseded|rejected
    content_json: Mapped[str] = mapped_column(Text, default="{}")   # redacted, bounded
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    envelope_json: Mapped[str] = mapped_column(Text, default="{}")
    envelope_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MapVersion(Base):
    """Immutable-снапшот Project Map (§36)."""

    __tablename__ = "map_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_mapversion_project_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MapNode(Base):
    """Узел версии Map (§27.6, §36)."""

    __tablename__ = "map_nodes"
    __table_args__ = (UniqueConstraint("map_version_id", "node_key", name="uq_mapnode_version_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    map_version_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    node_key: Mapped[str] = mapped_column(String(80))
    node_type: Mapped[str] = mapped_column(String(30))  # goal|user_problem|brief_decision|vp|blocker|evidence_ref|next_action|parking_item
    title: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    truth_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    evidence_ref: Mapped[str] = mapped_column(String(120), default="")
    evidence_hash: Mapped[str] = mapped_column(String(80), default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class MapEdge(Base):
    """Типизированное ребро версии Map (§36)."""

    __tablename__ = "map_edges"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    map_version_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    src_key: Mapped[str] = mapped_column(String(80))
    dst_key: Mapped[str] = mapped_column(String(80))
    edge_type: Mapped[str] = mapped_column(String(20))  # dependency|blocks|proves|includes|next
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Decision(Base):
    """Поштучное решение Brief (§36). Оптимистичная версия для accept/reject."""

    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("project_id", "decision_key", name="uq_decision_project_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    decision_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)  # proposed|accepted|rejected
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    truth_status: Mapped[str] = mapped_column(String(20), default="HYPOTHESIS")
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class DecisionEvent(Base):
    """Append-only история переходов решения (§36)."""

    __tablename__ = "decision_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class ParkingItem(Base):
    """Parking-lot: вне активного scope, с причиной и условием возврата (§36)."""

    __tablename__ = "parking_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    return_condition: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="parked", index=True)  # parked|promoted|archived
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Approval(Base):
    """Approval-record: связывает точные Brief/Map/envelope/decisions-hash (§36)."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    brief_id: Mapped[str] = mapped_column(String(40))
    brief_hash: Mapped[str] = mapped_column(String(80))
    map_version_id: Mapped[str] = mapped_column(String(40), default="")
    envelope_hash: Mapped[str] = mapped_column(String(80), default="")
    decisions_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class VpActivation(Base):
    """Активация VP. UNIQUE(project_id, active_slot) даёт ровно один active (§36).

    active_slot == 'ACTIVE' — активная запись; после деактивации slot = id
    (уникален), поэтому вторая одновременная активация детерминированно падает.
    """

    __tablename__ = "vp_activations"
    __table_args__ = (UniqueConstraint("project_id", "active_slot", name="uq_active_vp"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_key: Mapped[str] = mapped_column(String(80))
    active_slot: Mapped[str] = mapped_column(String(40))  # 'ACTIVE' | id
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    activated_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class IdempotencyKey(Base):
    """Идемпотентность мутаций: повтор возвращает прежнюю сущность (§25.1)."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), default="")
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


# --- VP-4 Work Orders & Context (Master Spec §16, §37) ---------------------
# Versioned VP Spec из точного принятого Brief/Map/approval, executable Work
# Orders с жизненным циклом, история переходов, решения оптимизатора, immutable
# JobPackage, checkpoint, immutable HandoffPackage, ack/reject, rotation.
# Всё содержимое bounded + redacted; content_hash — sha256 над canonical-JSON.
# Секреты в durable-состояние не попадают. Таблицы создаёт только Alembic.


class VpSpec(Base):
    """Версионный VP Spec, детерминированно выведенный из принятого Brief/Map (§37).

    Связывает точное принятое состояние VP-3 (approval/brief/map/envelope/
    decisions-хеши) и baseline. Правка = новая версия; content immutable."""

    __tablename__ = "vp_specs"
    __table_args__ = (UniqueConstraint("project_id", "vp_key", "version",
                                       name="uq_vpspec_project_vp_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_key: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|active|superseded
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    # Точная привязка источника (§37 «bind exact accepted VP-3 state»).
    approval_id: Mapped[str] = mapped_column(String(40), default="")
    brief_id: Mapped[str] = mapped_column(String(40), default="")
    brief_hash: Mapped[str] = mapped_column(String(80), default="")
    map_version_id: Mapped[str] = mapped_column(String(40), default="")
    map_hash: Mapped[str] = mapped_column(String(80), default="")
    envelope_hash: Mapped[str] = mapped_column(String(80), default="")
    decisions_hash: Mapped[str] = mapped_column(String(80), default="")
    baseline_branch: Mapped[str] = mapped_column(String(255), default="")
    baseline_head: Mapped[str] = mapped_column(String(64), default="")
    content_json: Mapped[str] = mapped_column(Text, default="{}")   # redacted, bounded
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WorkOrder(Base):
    """Executable Work Order с жизненным циклом (§16.1, §37).

    Оптимистичная ``version`` защищает переходы от гонок. Связывает точные
    хеши источника; approval владельца не расширяет capabilities."""

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_spec_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_key: Mapped[str] = mapped_column(String(80), default="")
    wo_key: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(20), default="builder")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str] = mapped_column(String(40), default="")   # split/merge lineage
    origin: Mapped[str] = mapped_column(String(20), default="spec")  # spec|merge|split
    # Привязка источника.
    approval_id: Mapped[str] = mapped_column(String(40), default="")
    spec_hash: Mapped[str] = mapped_column(String(80), default="")
    spec_version: Mapped[int] = mapped_column(Integer, default=1)
    brief_hash: Mapped[str] = mapped_column(String(80), default="")
    map_hash: Mapped[str] = mapped_column(String(80), default="")
    envelope_hash: Mapped[str] = mapped_column(String(80), default="")
    baseline_branch: Mapped[str] = mapped_column(String(255), default="")
    baseline_head: Mapped[str] = mapped_column(String(64), default="")
    content_json: Mapped[str] = mapped_column(Text, default="{}")   # scope/criteria/checks/... redacted
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)         # optimistic
    lease_id: Mapped[str] = mapped_column(String(40), default="")    # активная writer-аренда
    writer_holder: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class WorkOrderEvent(Base):
    """Append-only история переходов Work Order (§16.1, §37)."""

    __tablename__ = "work_order_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    reason_code: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")            # redacted, bounded
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class OptimizerDecision(Base):
    """Записанное решение оптимизатора (§16.2). Scope/criteria не меняет."""

    __tablename__ = "optimizer_decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_spec_id: Mapped[str] = mapped_column(String(40), default="")
    decision: Mapped[str] = mapped_column(String(30), index=True)
    reason_code: Mapped[str] = mapped_column(String(40), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")     # bounded
    affected_json: Mapped[str] = mapped_column(Text, default="[]")  # Work Order id-шники
    exact_next_action: Mapped[str] = mapped_column(Text, default="")
    inputs_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class JobPackage(Base):
    """Immutable JobPackage: только релевантный контекст (§16.3).

    Не содержит: весь repo, полный chat, повторяющиеся logs, credentials,
    полные env-дампы, посторонние идеи. Bounded по байтам/числу элементов."""

    __tablename__ = "job_packages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_json: Mapped[str] = mapped_column(Text, default="{}")   # redacted, bounded
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    provenance_json: Mapped[str] = mapped_column(Text, default="[]")  # [{source, ref, hash}]
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")  # allowlisted
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    compact: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class WoCheckpoint(Base):
    """Durable checkpoint Work Order (§16.5, §21). Hash-verifiable, без секретов."""

    __tablename__ = "wo_checkpoints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), index=True)
    job_package_id: Mapped[str] = mapped_column(String(40), default="")
    vp_key: Mapped[str] = mapped_column(String(80), default="")
    baseline_head: Mapped[str] = mapped_column(String(64), default="")
    current_head: Mapped[str] = mapped_column(String(64), default="")
    changed_files_json: Mapped[str] = mapped_column(Text, default="[]")
    commands_json: Mapped[str] = mapped_column(Text, default="[]")
    failures_json: Mapped[str] = mapped_column(Text, default="[]")
    completed_criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    remaining_criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    decisions_json: Mapped[str] = mapped_column(Text, default="[]")
    impacted_checks_json: Mapped[str] = mapped_column(Text, default="[]")
    artifact_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    lease_state_json: Mapped[str] = mapped_column(Text, default="{}")
    writer_holder: Mapped[str] = mapped_column(String(80), default="")
    exact_next_action: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(String(40), default="")
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class HandoffPackage(Base):
    """Immutable HandoffPackage продолжения новой сессии (§16.4, §37)."""

    __tablename__ = "handoff_packages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    vp_key: Mapped[str] = mapped_column(String(80), default="")
    vp_spec_id: Mapped[str] = mapped_column(String(40), default="")
    work_order_id: Mapped[str] = mapped_column(String(40), index=True)
    job_package_id: Mapped[str] = mapped_column(String(40), default="")
    checkpoint_id: Mapped[str] = mapped_column(String(40), default="")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_json: Mapped[str] = mapped_column(Text, default="{}")   # redacted, bounded
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    baseline_head: Mapped[str] = mapped_column(String(64), default="")
    current_head: Mapped[str] = mapped_column(String(64), default="")
    spec_version: Mapped[int] = mapped_column(Integer, default=1)
    brief_hash: Mapped[str] = mapped_column(String(80), default="")
    map_hash: Mapped[str] = mapped_column(String(80), default="")
    approval_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="issued", index=True)  # issued|acknowledged|rejected|superseded
    compact: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class HandoffAck(Base):
    """Append-only ack/reject HandoffPackage свежей сессией (§16.4, §37)."""

    __tablename__ = "handoff_acks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    handoff_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    result: Mapped[str] = mapped_column(String(12), default="ACK")  # ACK|REJECT
    reason_code: Mapped[str] = mapped_column(String(40), default="")
    ack_hash: Mapped[str] = mapped_column(String(80), default="")
    baseline_ack: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="consumer")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class RotationRecord(Base):
    """Запись безопасной ротации (§16.5). Один writer; lease освобождается один раз."""

    __tablename__ = "rotation_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), index=True)
    trigger: Mapped[str] = mapped_column(String(40), default="")
    checkpoint_id: Mapped[str] = mapped_column(String(40), default="")
    handoff_id: Mapped[str] = mapped_column(String(40), default="")
    next_profile_request: Mapped[str] = mapped_column(String(80), default="")
    lease_released: Mapped[bool] = mapped_column(Boolean, default=False)
    one_writer_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="started")
    exact_next_action: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


# --- VP-5 Agent Pipeline (Master Spec §38, §17) ----------------------------
# Durable-состояние конвейера Codex Planner → Claude Builder → Codex Reviewer.
# Секреты/email/cookie/raw path/transcript/full payload НИКОГДА не хранятся.
# Таблицы создаёт только Alembic (0005_agent_pipeline).

def _iso_opt(dt: datetime | None) -> str | None:
    return _iso(dt) if dt is not None else None


class ModelRegistry(Base):
    """Реестр моделей/провайдеров: source, availability, observation time (§17.2)."""

    __tablename__ = "model_registry"
    __table_args__ = (UniqueConstraint("provider", "model_id", name="uq_model_provider_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)  # codex|claude
    model_id: Mapped[str] = mapped_column(String(120))
    alias: Mapped[str] = mapped_column(String(80), default="")
    display: Mapped[str] = mapped_column(String(200), default="")
    efforts_json: Mapped[str] = mapped_column(Text, default="[]")
    context_capability: Mapped[str] = mapped_column(String(40), default="")
    structured_capability: Mapped[bool] = mapped_column(Boolean, default=False)
    availability: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    source: Mapped[str] = mapped_column(String(30), default="unknown")
    confidence: Mapped[str] = mapped_column(String(20), default="unknown")
    discovered_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "provider": self.provider, "model_id": self.model_id,
            "alias": self.alias, "display": self.display,
            "efforts": _json.loads(self.efforts_json or "[]"),
            "context_capability": self.context_capability,
            "structured_capability": self.structured_capability,
            "availability": self.availability, "source": self.source,
            "confidence": self.confidence, "discovered_at": _iso(self.discovered_at),
        }


class DiscoverySnapshot(Base):
    """Снимок discover_capabilities per profile+time (§12)."""

    __tablename__ = "discovery_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    profile_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    models_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(30), default="unknown")
    observed_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class RolePreset(Base):
    """Пресет роли → предпочтения модели/эффорта (§17.2)."""

    __tablename__ = "role_presets"
    __table_args__ = (UniqueConstraint("preset_key", "role", name="uq_preset_role"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    preset_key: Mapped[str] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(20))  # planner|builder|reviewer
    provider: Mapped[str] = mapped_column(String(20), default="")
    model_pref_json: Mapped[str] = mapped_column(Text, default="[]")
    effort_pref: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class AgentProfile(Base):
    """Safe-метаданные профиля (§11.1/11.2): alias, provider, unix-метка,
    allowlist-ref auth root. БЕЗ email/token/cookie/raw path."""

    __tablename__ = "agent_profiles"
    __table_args__ = (UniqueConstraint("alias", name="uq_agent_profile_alias"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    alias: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20), index=True)  # codex|claude
    unix_label: Mapped[str] = mapped_column(String(40), default="")  # safe service label
    auth_root_ref: Mapped[str] = mapped_column(String(120), default="")  # allowlist ref, NOT raw path
    schedulable: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "alias": self.alias, "provider": self.provider,
            "unix_label": self.unix_label, "schedulable": self.schedulable,
            "enabled": self.enabled, "created_at": _iso(self.created_at),
        }


class ProfileState(Base):
    """Availability профиля: состояние/cooldown/drain + optimistic version (§11.3)."""

    __tablename__ = "profile_states"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_profile_state_profile"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default="AUTH_REQUIRED", index=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    drain: Mapped[bool] = mapped_column(Boolean, default=False)
    current_run_id: Mapped[str] = mapped_column(String(40), default="")
    current_role: Mapped[str] = mapped_column(String(20), default="")
    next_action: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id, "state": self.state,
            "cooldown_until": _iso_opt(self.cooldown_until), "drain": self.drain,
            "current_run_id": self.current_run_id, "current_role": self.current_role,
            "next_action": self.next_action, "updated_at": _iso(self.updated_at),
            "version": self.version,
        }


class ProfileHealth(Base):
    """Health-наблюдение (§11.5): executable/version/auth/permissions, redacted error."""

    __tablename__ = "profile_health"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(40), index=True)
    executable: Mapped[str] = mapped_column(String(255), default="")
    cli_version: Mapped[str] = mapped_column(String(80), default="")
    auth_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    plan_label: Mapped[str] = mapped_column(String(40), default="")  # verified only
    permissions_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(String(80), default="")  # redacted short code / safe reason
    source: Mapped[str] = mapped_column(String(40), default="")  # напр. cli_status (VP-7)
    observed_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id, "executable": self.executable,
            "cli_version": self.cli_version, "auth_status": self.auth_status,
            "plan_label": self.plan_label, "permissions_ok": self.permissions_ok,
            "last_error": self.last_error, "source": self.source,
            "observed_at": _iso(self.observed_at),
        }


class CapacityObservation(Base):
    """Наблюдение ёмкости (§11.6): status/окна/reset/source/confidence; UNKNOWN не фикция."""

    __tablename__ = "capacity_observations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")  # AVAILABLE|LOW|EXHAUSTED|UNKNOWN
    five_h_used_pct: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    seven_d_used_pct: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="unknown")
    confidence: Mapped[str] = mapped_column(String(20), default="unknown")
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id, "status": self.status,
            "five_h_used_pct": self.five_h_used_pct,
            "seven_d_used_pct": self.seven_d_used_pct,
            "reset_at": _iso_opt(self.reset_at), "source": self.source,
            "confidence": self.confidence, "stale": self.stale,
            "observed_at": _iso(self.observed_at),
        }


class Run(Base):
    """Run конвейера (§17.4). Optimistic version; dedup_key для идемпотентности."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), default="")
    vp_key: Mapped[str] = mapped_column(String(80), default="")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    preset: Mapped[str] = mapped_column(String(80), default="")
    owner_override_json: Mapped[str] = mapped_column(Text, default="{}")
    dedup_key: Mapped[str] = mapped_column(String(120), default="")
    next_action: Mapped[str] = mapped_column(String(255), default="")
    blocker: Mapped[str] = mapped_column(String(255), default="")
    failure_class: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "project_id": self.project_id,
            "work_order_id": self.work_order_id, "vp_key": self.vp_key,
            "correlation_id": self.correlation_id, "state": self.state,
            "preset": self.preset,
            "owner_override": _json.loads(self.owner_override_json or "{}"),
            "next_action": self.next_action, "blocker": self.blocker,
            "failure_class": self.failure_class, "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at), "version": self.version,
        }


class RunRoleStep(Base):
    """Шаг роли Run: requested/effective модель+профиль, reason, verdict (§17.1/17.3)."""

    __tablename__ = "run_role_steps"
    __table_args__ = (UniqueConstraint("run_id", "role", "seq", name="uq_role_step_run_role_seq"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="")
    role: Mapped[str] = mapped_column(String(20), index=True)  # planner|builder|reviewer
    seq: Mapped[int] = mapped_column(Integer)
    requested_model: Mapped[str] = mapped_column(String(120), default="")
    effective_model: Mapped[str] = mapped_column(String(120), default="")
    requested_profile: Mapped[str] = mapped_column(String(80), default="")
    effective_profile: Mapped[str] = mapped_column(String(80), default="")
    provider: Mapped[str] = mapped_column(String(20), default="")
    session_ref: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    verdict: Mapped[str] = mapped_column(String(20), default="")
    reason_code: Mapped[str] = mapped_column(String(60), default="")
    builder_session_ref: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "run_id": self.run_id, "role": self.role, "seq": self.seq,
            "requested_model": self.requested_model, "effective_model": self.effective_model,
            "requested_profile": self.requested_profile,
            "effective_profile": self.effective_profile, "provider": self.provider,
            "session_ref": self.session_ref, "status": self.status,
            "verdict": self.verdict, "reason_code": self.reason_code,
            "updated_at": _iso(self.updated_at), "version": self.version,
        }


class RunEvent(Base):
    """Нормализованное событие Run (§25). Переживает рестарт Core."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="")
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    occurred_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "run_id": self.run_id, "seq": self.seq,
            "type": self.event_type, "occurred_at": _iso(self.occurred_at),
            "payload": _json.loads(self.payload_json or "{}"),
            "schema_version": self.schema_version,
        }


class ProviderSession(Base):
    """Ссылка на provider-сессию (§12.3): session_id-handle. БЕЗ transcript/credentials."""

    __tablename__ = "provider_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="")
    provider: Mapped[str] = mapped_column(String(20), index=True)
    profile_id: Mapped[str] = mapped_column(String(40), default="")
    session_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "run_id": self.run_id, "role": self.role,
            "provider": self.provider, "profile_id": self.profile_id,
            "session_id": self.session_id, "status": self.status,
            "started_at": _iso(self.started_at), "last_seen_at": _iso_opt(self.last_seen_at),
        }


class RouterDecision(Base):
    """Решение роутера (§17.3): requested vs effective + reason_code + кандидаты."""

    __tablename__ = "router_decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    requested_model: Mapped[str] = mapped_column(String(120), default="")
    requested_profile: Mapped[str] = mapped_column(String(80), default="")
    effective_model: Mapped[str] = mapped_column(String(120), default="")
    effective_profile: Mapped[str] = mapped_column(String(80), default="")
    reason_code: Mapped[str] = mapped_column(String(60), default="")
    candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    decided_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "run_id": self.run_id, "role": self.role,
            "requested_model": self.requested_model,
            "requested_profile": self.requested_profile,
            "effective_model": self.effective_model,
            "effective_profile": self.effective_profile,
            "reason_code": self.reason_code,
            "candidates": _json.loads(self.candidates_json or "[]"),
            "decided_at": _iso(self.decided_at),
        }


class RunLease(Base):
    """Аренда профиля Run (§13.4). Активная: released_at=''. UNIQUE(profile_id,
    released_at) → не более одной активной аренды на профиль (нет второго writer)."""

    __tablename__ = "run_leases"
    __table_args__ = (UniqueConstraint("profile_id", "released_at", name="uq_run_lease_profile_active"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="")
    profile_id: Mapped[str] = mapped_column(String(40), index=True)
    worktree: Mapped[str] = mapped_column(String(255), default="")
    holder: Mapped[str] = mapped_column(String(80), default="")
    acquired_at: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[str] = mapped_column(String(30), default="")
    heartbeat_at: Mapped[str] = mapped_column(String(30), default="")
    released_at: Mapped[str] = mapped_column(String(30), default="")


class RunRetry(Base):
    """Bounded-ретрай с классом ошибки (§12.4, §17.5)."""

    __tablename__ = "run_retries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="")
    attempt: Mapped[int] = mapped_column(Integer)
    error_class: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    backoff_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class RunPause(Base):
    """Pause/interruption/recovery запись (§31)."""

    __tablename__ = "run_pauses"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # pause|resume|interruption|recovery
    reason: Mapped[str] = mapped_column(String(120), default="")
    safe_continuation_ref: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class HandoffLink(Base):
    """Связь Run ↔ HandoffPackage (VP-4) / recovery (§16.4)."""

    __tablename__ = "handoff_links"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    handoff_package_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    kind: Mapped[str] = mapped_column(String(20), default="")  # checkpoint|handoff|recovery
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


# --- VP-6 Review & Quality (Master Spec §18, §39) --------------------------
# ReviewPackage (immutable, SHA-bound), findings, QualityReport, impact-оценка,
# Evidence Cache, manual audit, waiver, fix-loop. Content-hash — sha256 над
# canonical-JSON. Секреты/email/cookie/raw path/transcript НИКОГДА не хранятся.
# Таблицы создаёт только Alembic (0006_review_quality).


class ReviewPackage(Base):
    """Immutable, SHA-bound ReviewPackage (§18.1). Инвалидация фактом (§VP6-D4)."""

    __tablename__ = "review_packages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), default="")
    vp_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    wo_key: Mapped[str] = mapped_column(String(80), default="")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    branch: Mapped[str] = mapped_column(String(255), default="")
    base_sha: Mapped[str] = mapped_column(String(64), default="")
    head_sha: Mapped[str] = mapped_column(String(64), default="")
    spec_hash: Mapped[str] = mapped_column(String(80), default="")
    brief_hash: Mapped[str] = mapped_column(String(80), default="")
    map_hash: Mapped[str] = mapped_column(String(80), default="")
    diff_summary_json: Mapped[str] = mapped_column(Text, default="{}")      # bounded
    artifact_hashes_json: Mapped[str] = mapped_column(Text, default="[]")   # [{path, sha}]
    acceptance_json: Mapped[str] = mapped_column(Text, default="[]")        # матрица критериев
    claims_json: Mapped[str] = mapped_column(Text, default="[]")            # заявления Builder
    impact_class: Mapped[str] = mapped_column(String(20), default="")
    checks_json: Mapped[str] = mapped_column(Text, default="[]")            # команды/результаты/cache
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    limitations_json: Mapped[str] = mapped_column(Text, default="[]")
    grant_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    freshness_json: Mapped[str] = mapped_column(Text, default="{}")         # свежесть источников
    content_hash: Mapped[str] = mapped_column(String(80), default="", index=True)
    status: Mapped[str] = mapped_column(String(12), default="valid", index=True)  # valid|invalid
    invalid_code: Mapped[str] = mapped_column(String(40), default="")
    invalid_reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "project_id": self.project_id, "run_id": self.run_id,
            "work_order_id": self.work_order_id, "vp_key": self.vp_key,
            "wo_key": self.wo_key, "correlation_id": self.correlation_id,
            "branch": self.branch, "base_sha": self.base_sha, "head_sha": self.head_sha,
            "spec_hash": self.spec_hash, "brief_hash": self.brief_hash,
            "map_hash": self.map_hash,
            "diff_summary": _json.loads(self.diff_summary_json or "{}"),
            "artifact_hashes": _json.loads(self.artifact_hashes_json or "[]"),
            "acceptance": _json.loads(self.acceptance_json or "[]"),
            "claims": _json.loads(self.claims_json or "[]"),
            "impact_class": self.impact_class,
            "checks": _json.loads(self.checks_json or "[]"),
            "evidence_refs": _json.loads(self.evidence_refs_json or "[]"),
            "limitations": _json.loads(self.limitations_json or "[]"),
            "grant_snapshot": _json.loads(self.grant_snapshot_json or "{}"),
            "freshness": _json.loads(self.freshness_json or "{}"),
            "content_hash": self.content_hash, "status": self.status,
            "invalid_code": self.invalid_code, "invalid_reason": self.invalid_reason,
            "actor": self.actor, "created_at": _iso(self.created_at),
        }


class QualityFinding(Base):
    """Finding (§18.2): severity/criterion/location/evidence/action/blocking/source/code."""

    __tablename__ = "quality_findings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    gate: Mapped[str] = mapped_column(String(40), default="", index=True)
    code: Mapped[str] = mapped_column(String(60), default="")   # стабильный код finding
    severity: Mapped[str] = mapped_column(String(12), default="minor")  # blocker|major|minor|info
    criterion: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")     # redacted, bounded
    action: Mapped[str] = mapped_column(Text, default="")
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(60), default="")
    freshness: Mapped[str] = mapped_column(String(20), default="")  # FRESH|STALE|UNKNOWN
    waived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "gate": self.gate, "code": self.code, "severity": self.severity,
            "criterion": self.criterion, "location": self.location,
            "evidence": self.evidence, "action": self.action,
            "blocking": self.blocking, "source": self.source,
            "freshness": self.freshness, "waived": self.waived,
            "created_at": _iso(self.created_at),
        }


class QualityReport(Base):
    """QualityReport (§18.2/§39): вердикт + объяснение, immutable content_hash."""

    __tablename__ = "quality_reports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(40), default="")
    verdict: Mapped[str] = mapped_column(String(20), default="", index=True)
    claims_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_summary: Mapped[str] = mapped_column(Text, default="")   # что доказывает/опровергает
    gate_fired: Mapped[str] = mapped_column(String(60), default="")
    sufficiency_reason: Mapped[str] = mapped_column(Text, default="")  # почему проверок достаточно
    next_action: Mapped[str] = mapped_column(Text, default="")
    stop_reason: Mapped[str] = mapped_column(Text, default="")         # почему полировка стоп
    blocking_count: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "run_id": self.run_id, "verdict": self.verdict,
            "claims": _json.loads(self.claims_json or "[]"),
            "evidence_summary": self.evidence_summary, "gate_fired": self.gate_fired,
            "sufficiency_reason": self.sufficiency_reason, "next_action": self.next_action,
            "stop_reason": self.stop_reason, "blocking_count": self.blocking_count,
            "findings_count": self.findings_count, "content_hash": self.content_hash,
            "actor": self.actor, "created_at": _iso(self.created_at),
        }


class ImpactAssessment(Base):
    """Impact-оценка (§18.5): класс + обоснование + выбранные check-группы."""

    __tablename__ = "impact_assessments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    impact_class: Mapped[str] = mapped_column(String(20), default="", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    check_groups_json: Mapped[str] = mapped_column(Text, default="[]")
    risk_trigger: Mapped[str] = mapped_column(Text, default="")
    full_regression: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "impact_class": self.impact_class, "reason": self.reason,
            "check_groups": _json.loads(self.check_groups_json or "[]"),
            "risk_trigger": self.risk_trigger, "full_regression": self.full_regression,
            "created_at": _iso(self.created_at),
        }


class EvidenceCacheEntry(Base):
    """Evidence Cache (§18.7). Ключ = SHA+команда/версия+input+env+scope."""

    __tablename__ = "evidence_cache"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_evidence_cache_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(120), index=True)
    sha: Mapped[str] = mapped_column(String(64), default="")
    command: Mapped[str] = mapped_column(String(200), default="")
    command_version: Mapped[str] = mapped_column(String(80), default="")
    input_hash: Mapped[str] = mapped_column(String(80), default="")
    environment: Mapped[str] = mapped_column(String(80), default="")
    scope: Mapped[str] = mapped_column(String(80), default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    last_reused_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "cache_key": self.cache_key, "sha": self.sha,
            "command": self.command, "command_version": self.command_version,
            "input_hash": self.input_hash, "environment": self.environment,
            "scope": self.scope, "result": _json.loads(self.result_json or "{}"),
            "passed": self.passed, "reason": self.reason, "stale": self.stale,
            "reuse_count": self.reuse_count, "created_at": _iso(self.created_at),
            "last_reused_at": _iso_opt(self.last_reused_at),
        }


class ManualAudit(Base):
    """Manual audit (§18.4): read-only, не мутирует код."""

    __tablename__ = "manual_audits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    target: Mapped[str] = mapped_column(String(20), default="")  # project|vp|diff|screen|dependencies|docs|ai_waste
    scope: Mapped[str] = mapped_column(String(200), default="")
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "target": self.target, "scope": self.scope, "read_only": self.read_only,
            "result": _json.loads(self.result_json or "{}"),
            "findings_count": self.findings_count, "actor": self.actor,
            "created_at": _iso(self.created_at),
        }


class Waiver(Base):
    """Waiver (§18.4): обязательные поля; не обходит non-waivable-правила."""

    __tablename__ = "waivers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    finding_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(200), default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    expiry: Mapped[str] = mapped_column(String(120), default="")
    review_condition: Mapped[str] = mapped_column(Text, default="")
    audit_ref: Mapped[str] = mapped_column(String(40), default="")
    waivable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    rejected_code: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "finding_id": self.finding_id, "reason": self.reason, "scope": self.scope,
            "actor": self.actor, "expiry": self.expiry,
            "review_condition": self.review_condition, "audit_ref": self.audit_ref,
            "waivable": self.waivable, "rejected_code": self.rejected_code,
            "created_at": _iso(self.created_at),
        }


class FixLoop(Base):
    """Fix-loop (§18.8): attempt 1..2; второй REVISE → BLOCKED."""

    __tablename__ = "fix_loops"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_package_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    verdict: Mapped[str] = mapped_column(String(20), default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    fix_work_order_id: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "review_package_id": self.review_package_id,
            "run_id": self.run_id, "attempt": self.attempt, "verdict": self.verdict,
            "blocked": self.blocked, "fix_work_order_id": self.fix_work_order_id,
            "created_at": _iso(self.created_at),
        }


class ProfileRegistryReconcile(Base):
    """Append-only запись idempotent-сверки реестра профилей → БД (VP6-D2)."""

    __tablename__ = "profile_registry_reconciles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    by_provider_json: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String(80), default="deploy")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "created": self.created, "updated": self.updated,
            "total": self.total, "by_provider": _json.loads(self.by_provider_json or "{}"),
            "actor": self.actor, "created_at": _iso(self.created_at),
        }


# ---------------------------------------------------------------------------
# VP-7 — Autonomy, GitHub & Time Machine (Master Spec §19, §20, §21, §23).
# Durable capability grants (раздельные capabilities, optimistic version),
# Emergency Stop (append-only lifecycle, переживает рестарт), immutable
# content-addressed checkpoints Time Machine, GitHub delivery/merge-gate записи.
# Секреты/token/cookie/email/raw path/transcript НИКОГДА не хранятся. Таблицы
# создаёт только Alembic (0007_autonomy_github_time_machine).

class Grant(Base):
    """Durable capability grant (§19). Capabilities раздельны (список кодов), а
    не boolean «full access». Optimistic ``version``; revocation/expiry явны.
    ``content_hash`` покрывает immutable-снимок (для grant_snapshot в
    ReviewPackage/checkpoint)."""

    __tablename__ = "grants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_ref: Mapped[str] = mapped_column(String(80), default="owner")
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    environment: Mapped[str] = mapped_column(String(40), default="")  # synthetic|local|…
    mode: Mapped[str] = mapped_column(String(20), default="GUIDED", index=True)
    allowed_repos_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_bases_json: Mapped[str] = mapped_column(Text, default="[]")
    workspace_allowlist_json: Mapped[str] = mapped_column(Text, default="[]")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    branch_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    command_restrictions_json: Mapped[str] = mapped_column(Text, default="{}")
    budget_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime] = mapped_column(default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)  # ACTIVE|REVOKED|EXPIRED
    revoked_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
    revoked_by: Mapped[str] = mapped_column(String(80), default="")
    revoke_reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    audit_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(80), default="", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "owner_ref": self.owner_ref, "project_id": self.project_id,
            "environment": self.environment, "mode": self.mode,
            "allowed_repos": _json.loads(self.allowed_repos_json or "[]"),
            "allowed_bases": _json.loads(self.allowed_bases_json or "[]"),
            "workspace_allowlist": _json.loads(self.workspace_allowlist_json or "[]"),
            "capabilities": _json.loads(self.capabilities_json or "[]"),
            "branch_rules": _json.loads(self.branch_rules_json or "{}"),
            "command_restrictions": _json.loads(self.command_restrictions_json or "{}"),
            "budget": _json.loads(self.budget_json or "{}"),
            "reason": self.reason, "starts_at": _iso(self.starts_at),
            "expires_at": _iso_opt(self.expires_at), "state": self.state,
            "revoked_at": _iso_opt(self.revoked_at), "revoked_by": self.revoked_by,
            "revoke_reason": self.revoke_reason, "actor": self.actor,
            "correlation_id": self.correlation_id,
            "audit_refs": _json.loads(self.audit_refs_json or "[]"),
            "content_hash": self.content_hash, "version": self.version,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }


class EmergencyStop(Base):
    """Append-only lifecycle Emergency Stop (§19). Текущее состояние =
    ``active`` последней записи. Переживает рестарт (durable), не
    реактивируется молча; RESUMED — только по явному owner-действию."""

    __tablename__ = "emergency_stops"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    action: Mapped[str] = mapped_column(String(12), default="ENGAGED")  # ENGAGED|RESUMED
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    interrupted_runs_json: Mapped[str] = mapped_column(Text, default="[]")
    released_leases_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id, "action": self.action, "active": self.active,
            "reason": self.reason, "actor": self.actor,
            "correlation_id": self.correlation_id,
            "interrupted_runs": _json.loads(self.interrupted_runs_json or "[]"),
            "released_leases": _json.loads(self.released_leases_json or "[]"),
            "created_at": _iso(self.created_at),
        }


class Checkpoint(Base):
    """Immutable content-addressed checkpoint Time Machine (§21). Без секретов,
    без transcript; хранятся только provider session id (не содержимое), хеши
    патча/артефактов/тестов, снимок grant (хеш). ``content_hash`` = sha256 над
    canonical immutable-payload; tamper инвалидирует."""

    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    vp_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    work_order_id: Mapped[str] = mapped_column(String(40), default="")
    run_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    db_revision: Mapped[str] = mapped_column(String(40), default="")
    branch: Mapped[str] = mapped_column(String(255), default="")
    base_sha: Mapped[str] = mapped_column(String(64), default="")
    head_sha: Mapped[str] = mapped_column(String(64), default="")
    worktree_status: Mapped[str] = mapped_column(String(20), default="")  # clean|dirty
    patch_hash: Mapped[str] = mapped_column(String(80), default="")
    artifact_hashes_json: Mapped[str] = mapped_column(Text, default="[]")
    profile_alias: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    effort: Mapped[str] = mapped_column(String(40), default="")
    session_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    grant_id: Mapped[str] = mapped_column(String(40), default="")
    grant_hash: Mapped[str] = mapped_column(String(80), default="")
    test_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    handoff_ref: Mapped[str] = mapped_column(String(40), default="")
    cause: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)

    def immutable_payload(self) -> dict:
        """Canonical immutable-содержимое, покрываемое ``content_hash``."""
        import json as _json
        return {
            "project_id": self.project_id, "vp_key": self.vp_key,
            "work_order_id": self.work_order_id, "run_id": self.run_id,
            "db_revision": self.db_revision, "branch": self.branch,
            "base_sha": self.base_sha, "head_sha": self.head_sha,
            "worktree_status": self.worktree_status, "patch_hash": self.patch_hash,
            "artifact_hashes": _json.loads(self.artifact_hashes_json or "[]"),
            "profile_alias": self.profile_alias, "model": self.model,
            "effort": self.effort,
            "session_ids": _json.loads(self.session_ids_json or "[]"),
            "grant_id": self.grant_id, "grant_hash": self.grant_hash,
            "test_refs": _json.loads(self.test_refs_json or "[]"),
            "evidence_refs": _json.loads(self.evidence_refs_json or "[]"),
            "handoff_ref": self.handoff_ref, "cause": self.cause,
        }

    def to_dict(self) -> dict:
        d = self.immutable_payload()
        d.update({"id": self.id, "actor": self.actor,
                  "correlation_id": self.correlation_id,
                  "content_hash": self.content_hash,
                  "created_at": _iso(self.created_at)})
        return d


class GithubDelivery(Base):
    """GitHub delivery/merge-gate запись (§20). PR-состояние, checks по текущему
    head, mergeability, решение gate + reason. Token НЕ хранится. Идемпотентность
    по ``idempotency_key`` (repo+base+branch+head)."""

    __tablename__ = "github_deliveries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(40), default="")
    repo: Mapped[str] = mapped_column(String(120), default="", index=True)
    base: Mapped[str] = mapped_column(String(120), default="")
    branch: Mapped[str] = mapped_column(String(255), default="")
    head_sha: Mapped[str] = mapped_column(String(64), default="")
    pr_number: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    pr_url: Mapped[str] = mapped_column(String(255), default="")
    pr_state: Mapped[str] = mapped_column(String(20), default="NONE")  # NONE|OPEN|MERGED|CLOSED
    checks_state: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    checks_head_sha: Mapped[str] = mapped_column(String(64), default="")
    mergeable: Mapped[bool] = mapped_column(Boolean, default=False)
    merge_state: Mapped[str] = mapped_column(String(30), default="")
    review_package_id: Mapped[str] = mapped_column(String(40), default="")
    quality_report_id: Mapped[str] = mapped_column(String(40), default="")
    gate_decision: Mapped[str] = mapped_column(String(20), default="")  # PERMIT|DENY
    gate_reason: Mapped[str] = mapped_column(String(60), default="")
    grant_id: Mapped[str] = mapped_column(String(40), default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(80), default="core")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "run_id": self.run_id,
            "repo": self.repo, "base": self.base, "branch": self.branch,
            "head_sha": self.head_sha, "pr_number": self.pr_number,
            "pr_url": self.pr_url, "pr_state": self.pr_state,
            "checks_state": self.checks_state, "checks_head_sha": self.checks_head_sha,
            "mergeable": self.mergeable, "merge_state": self.merge_state,
            "review_package_id": self.review_package_id,
            "quality_report_id": self.quality_report_id,
            "gate_decision": self.gate_decision, "gate_reason": self.gate_reason,
            "grant_id": self.grant_id, "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id, "actor": self.actor,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
        }
