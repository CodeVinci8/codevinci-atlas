"""Минимальные контракты VP-0 (Master Spec §12–§16, §21).

Только то, что нужно для доказательства: JobPackage, RunRequest,
нормализованное событие, RunResult, Checkpoint, HandoffPackage. Полные
Pydantic-модели и OpenAPI появятся в VP-1+. Здесь — dataclasses без внешних
зависимостей, сериализуемые в JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum

from .ids import new_id, utcnow_iso


class Provider(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"


class Role(str, Enum):
    PLANNER = "planner"
    BUILDER = "builder"
    REVIEWER = "reviewer"


class SessionCapability(str, Enum):
    NEW_SESSION = "NEW_SESSION"
    RESUME_BY_ID = "RESUME_BY_ID"
    FRESH_WITH_HANDOFF = "FRESH_WITH_HANDOFF"
    COMPACT = "COMPACT"
    CLEAR_INTERACTIVE = "CLEAR_INTERACTIVE"
    FORK_NATIVE = "FORK_NATIVE"


class RunState(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    SUCCEEDED = "SUCCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


@dataclass
class JobPackage:
    """Машиночитаемый пакет входов запуска (§16.3).

    Не содержит: весь repo, полный chat, повторяющиеся logs, credentials,
    посторонние идеи. Это проверяется тестом contract.
    """

    goal: str
    role: Role
    provider: Provider
    source_of_truth: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    prohibited_actions: list[str] = field(default_factory=list)
    output_schema_ref: str | None = None
    inputs: dict = field(default_factory=dict)
    handoff_ref: str | None = None
    job_id: str = field(default_factory=lambda: new_id("job"))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["role"] = self.role.value
        d["provider"] = self.provider.value
        return d


@dataclass
class RunRequest:
    """Запрос запуска в Runner (§13.2): argv-массив, cwd, allowed env keys.

    Никогда не содержит raw token/cookie или произвольную shell-строку.
    """

    argv: list[str]
    cwd: str
    profile_alias: str
    allowed_env_keys: list[str] = field(default_factory=list)
    env_root_var: str | None = None  # напр. CODEX_HOME / CLAUDE_CONFIG_DIR
    env_root_value: str | None = None  # путь к изолированному root профиля
    timeout_s: float = 60.0
    max_output_bytes: int = 1_000_000
    network: str = "default"
    request_id: str = field(default_factory=lambda: new_id("req"))

    def to_public_dict(self) -> dict:
        """Публичное представление без raw path профиля (для логов/UI)."""

        d = asdict(self)
        d["env_root_value"] = "[REDACTED_PATH]" if self.env_root_value else None
        return d


@dataclass
class RunEvent:
    """Нормализованное событие Runner→Core (§25.1, §31)."""

    type: str
    run_id: str
    payload: dict = field(default_factory=dict)
    occurred_at: str = field(default_factory=utcnow_iso)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    schema_version: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass
class RunResult:
    """Итог запуска: структурный вывод + метрики, без секретов."""

    run_id: str
    state: RunState
    provider: Provider
    profile_alias: str
    session_id: str | None = None
    exit_code: int | None = None
    structured_output: dict = field(default_factory=dict)
    output_hash: str | None = None
    error_code: str | None = None
    error_evidence: str | None = None
    events: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=utcnow_iso)
    finished_at: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["provider"] = self.provider.value
        return d


@dataclass
class Checkpoint:
    """Безопасная точка продолжения/fork (§21). Без секретов."""

    project_id: str
    vp_id: str
    work_order_id: str | None
    branch: str
    head: str | None
    status_porcelain: str
    profile_alias: str | None
    model: str | None
    effort: str | None
    session_id: str | None
    cause: str
    tests: list[dict] = field(default_factory=list)
    handoff_ref: str | None = None
    checkpoint_id: str = field(default_factory=lambda: new_id("ckpt"))
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HandoffPackage:
    """Компактный пакет продолжения новой сессии (§16.4).

    Новый агент сверяет handoff с фактическим Git/DB. Фактическое state
    побеждает; mismatch фиксируется в audit.
    """

    project_id: str
    vp_id: str
    goal: str
    immutable_constraints: list[str]
    baseline_head: str | None
    current_head: str | None
    changed_files: list[str]
    commands: list[dict]  # [{cmd, outcome, exit_code}]
    failures: list[dict]
    acceptance_matrix: list[dict]
    decisions: list[str]
    exact_next_action: str
    prohibited_actions: list[str]
    artifact_refs: list[str] = field(default_factory=list)
    from_profile_alias: str | None = None
    session_ids: list[str] = field(default_factory=list)
    progress: dict = field(default_factory=dict)  # состояние продолжения (для recovery)
    handoff_id: str = field(default_factory=lambda: new_id("hand"))
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
