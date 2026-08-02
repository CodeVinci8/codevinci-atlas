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
