"""VP-4 Work Orders & Context — сервис VP Spec и жизненного цикла Work Order
(Master Spec §16, §37).

Из ОДНОГО точного принятого Brief/Map/approval детерминированно (без вызовов
модели) выводится версионный VP Spec, из него — executable Work Orders с явным
жизненным циклом. Каждый Work Order связывает точные хеши источника; approval
владельца НЕ расширяет capabilities. Всё содержимое bounded + redacted; секреты
в durable-состояние не попадают. Оптимистичная ``version`` и идемпотентные
ключи защищают от гонок и повторов; переходы атомарны и append-only.

Всё содержимое repo/issues/output модели — данные (§30.2): не исполняется и не
расширяет права. Строка внутри контекста не даёт shell/network/git/provider.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from . import audit
from .db import session_scope
from .errors import AtlasError
from .ids import new_id
from .orm import (
    Approval,
    Brief,
    Decision,
    GitBaseline,
    IdempotencyKey,
    MapVersion,
    Project,
    VpSpec,
    WorkOrder,
    WorkOrderEvent,
)
from .productmap import canonical_json, content_hash
from .redaction import contains_secret, redact
from .settings import load_settings
from .wsleases import WorktreeLeaseService

# --- домен -----------------------------------------------------------------
ROLES = ("builder", "planner", "reviewer")
WO_STATUSES = ("draft", "ready", "active", "checkpointed", "handoff_ready",
               "blocked", "completed", "cancelled")
SPEC_SCHEMA_VERSION = 1
WO_SCHEMA_VERSION = 1

# Явный жизненный цикл (§37 Phase D). Невалидный переход → INVALID_TRANSITION.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"ready", "cancelled"},
    "ready": {"active", "draft", "cancelled"},
    "active": {"checkpointed", "handoff_ready", "blocked", "completed", "cancelled"},
    "checkpointed": {"active", "handoff_ready", "blocked", "completed", "cancelled"},
    "handoff_ready": {"active", "completed", "blocked", "cancelled"},
    "blocked": {"ready", "active", "cancelled"},
    "completed": set(),   # терминальный
    "cancelled": set(),   # терминальный (архив без деструктивного удаления)
}
# Статусы, в которых writer-аренда должна удерживаться (один writer).
LEASE_HELD_STATUSES = ("active", "checkpointed", "handoff_ready")
TERMINAL_STATUSES = ("completed", "cancelled")

# Capabilities — только allowlist. Строка контекста не может выдать право.
CAPABILITY_ALLOWLIST = (
    "repository_read", "repository_write", "commands", "install_dependencies",
    "commit", "push_feature_branch", "create_pull_request", "merge_to_main_after_pass",
)
# Никогда не выдаётся автоматически / из контекста (§19, §30.2).
PROHIBITED_CAPABILITIES = (
    "direct_push_main", "force_push", "delete_branch", "delete_repository",
    "production_deploy", "paid_api_calls", "shell_arbitrary", "network_unrestricted",
    "import_cookies", "destructive_rollback",
)
_DEFAULT_CAPS = {
    "builder": ["repository_read", "repository_write", "commands",
                "install_dependencies", "commit", "push_feature_branch",
                "create_pull_request"],
    "planner": ["repository_read", "commands"],
    "reviewer": ["repository_read", "commands"],
}
# Всегда запрещённые действия внутри Work Order (§13.2, §17.5, §19).
PROHIBITED_ACTIONS = (
    "force push", "прямой push в main", "удаление данных/репозитория/веток",
    "копирование credentials между профилями", "production deploy",
    "платные вызовы API", "реальные provider-probe без owner-гейта",
    "произвольная shell-строка вне явной команды проекта",
)
STOP_CONDITIONS = (
    "Действие вне grant/scope envelope",
    "Scope drift относительно принятого Brief/Map",
    "Подозрение на утечку секрета",
    "Вторая неудача fix / второй REVISE",
    "Невалидный вывод дважды",
    "Нет совместимого профиля",
)
IMMUTABLE_CONSTRAINTS = (
    "Scope и acceptance criteria неизменяемы без нового owner-approval",
    "Один Builder writer на worktree; Planner/Reviewer read-only",
    "Credentials не копируются; секреты не попадают в БД/логи/artifacts",
    "Фактическое состояние Git/DB побеждает; расхождение — в audit",
)
# Детерминированные required-checks (ссылки, не исполняются здесь).
DEFAULT_REQUIRED_CHECKS = (
    {"id": "lint", "name": "ruff", "command": "ruff check apps tests scripts"},
    {"id": "tests", "name": "unittest",
     "command": "python -m unittest discover -s tests -p 'test_*.py'"},
    {"id": "schema", "name": "schema-validate",
     "command": "python scripts/validate_schemas.py"},
    {"id": "migrations", "name": "alembic-empty", "command": "alembic upgrade head"},
    {"id": "secret", "name": "secret-scan", "command": "python scripts/secret_scan.py"},
)
DEFAULT_TEST_IMPACT = ("INTEGRATION",)
REPORT_SCHEMA_REF = "contracts/schemas/run-result.json"

# Границы (bounded content, §16.3).
_MAX_TEXT = 4000
_MAX_ITEM = 500
_MAX_LIST = 60


class WorkOrderError(Exception):
    """Ошибка VP-4 со стабильным кодом и HTTP-статусом (§16.5)."""

    _HTTP = {
        "VERSION_CONFLICT": 409, "INVALID_TRANSITION": 409, "SCOPE_DRIFT": 409,
        "CRITERIA_LOST": 409, "CONTEXT_LIMIT": 409, "HANDOFF_STALE": 409,
        "HASH_MISMATCH": 409, "OWNER_REQUIRED": 409, "PROJECT_NOT_AVAILABLE": 409,
        "SOURCE_STALE": 409, "MERGE_INCOMPATIBLE": 409, "WRITER_CONFLICT": 409,
        "SPEC_INVALID": 422, "WO_INVALID": 422, "SPLIT_INVALID": 422,
        "CAPABILITY_DENIED": 422, "NOT_FOUND": 404,
    }

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = redact(reason)
        self.http = self._HTTP.get(code, 400)
        super().__init__(f"{code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


# --- helpers ---------------------------------------------------------------
def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(v, limit: int = _MAX_TEXT) -> str:
    if v is None:
        return ""
    return redact(str(v))[:limit]


def _item(v) -> str:
    return _text(v, _MAX_ITEM)


def _str_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in (line.strip() for line in v.splitlines()) if x]
    out = []
    for x in list(v)[:_MAX_LIST]:
        s = _item(x)
        if s:
            out.append(s)
    return out


def _guard_no_secret(*values: str) -> None:
    for v in values:
        if isinstance(v, str) and contains_secret(v):
            raise WorkOrderError("WO_INVALID", "Ввод содержит секрет — отклонено")


def allowlist_capabilities(caps) -> list[str]:
    """Пропустить только allowlisted capabilities; прочее (в т.ч. из контекста)
    отбрасывается. Явно запрещённые — ошибка (§30.2)."""
    if caps is None:
        return []
    out: list[str] = []
    for c in list(caps)[:_MAX_LIST]:
        c = str(c).strip()
        if c in PROHIBITED_CAPABILITIES:
            raise WorkOrderError("CAPABILITY_DENIED", f"capability запрещена: {c}")
        if c in CAPABILITY_ALLOWLIST and c not in out:
            out.append(c)
    return out


# --- детерминированная генерация VP Spec / Work Order ----------------------
def build_vp_spec_content(*, vp_key: str, brief_content: dict, envelope: dict,
                          decisions: list[dict], baseline: dict,
                          source_refs: list[str]) -> dict:
    """Детерминированно вывести VP Spec из структурного состояния (§37 Phase D)."""
    mvp = _str_list(brief_content.get("mvp_scope"))
    promised = _item(brief_content.get("promised_result")) or _item(brief_content.get("product_statement"))
    # acceptance criteria — стабильные ID, без потери
    criteria: list[dict] = []
    for i, item in enumerate(mvp, start=1):
        criteria.append({"id": f"ac{i}", "text": item, "required": True, "source": "mvp"})
    for d in decisions:
        if d.get("status") == "accepted":
            key = d.get("decision_key") or ""
            criteria.append({"id": f"ac_dec_{key}", "text": f"Соблюдено решение: {d.get('title')}",
                             "required": bool(d.get("required")), "source": "decision"})
    if not criteria:
        criteria.append({"id": "ac1", "text": promised or f"Достигнут результат {vp_key}",
                         "required": True, "source": "result"})
    out_of_scope = _str_list(brief_content.get("out_of_scope")) + _str_list(envelope.get("out_of_scope"))
    constraints = list(IMMUTABLE_CONSTRAINTS) + _str_list(envelope.get("constraints"))
    content = {
        "vp_key": vp_key,
        "result": promised or f"Законченный результат {vp_key}",
        "definition_of_done": [
            "Все обязательные acceptance criteria выполнены и подтверждены evidence",
            "Пройдены required checks соответствующего класса риска (§18.5)",
            "Нет регрессий вне scope; фактический запуск подтверждён",
        ],
        "inputs": list(source_refs),
        "outputs": mvp or [promised] if promised else [],
        "user_scenario": _item(brief_content.get("main_scenario"))
        or _item(brief_content.get("user_and_problem")),
        "interfaces": _str_list(envelope.get("in_scope")),
        "files_in_scope": _str_list(envelope.get("in_scope")),
        "immutable_constraints": _dedup(constraints),
        "out_of_scope": _dedup(out_of_scope),
        "acceptance_criteria": criteria,
        "required_checks": [dict(c) for c in DEFAULT_REQUIRED_CHECKS],
        "negative_tests": [
            "Невалидный/неполный ввод отклонён стабильным кодом",
            "Устаревшая версия/конфликтная мутация отклонена без тихой перезаписи",
        ],
        "regression_tests": [
            "Целевые проверки затронутых компонентов без полной регрессии (§18.5)",
        ],
        "demonstration": "Экспорт/приёмка принятого результата для owner-ревью",
        "stop_conditions": list(STOP_CONDITIONS),
        "baseline": {"branch": baseline.get("branch", ""), "head": baseline.get("head", "")},
        "exact_next_action": "Создать executable Work Order из VP Spec и перевести его в ready.",
    }
    return content


def build_work_order_content(*, role: str, goal: str, spec_content: dict,
                             criteria: list[dict], scope: dict, capabilities: list[str],
                             source_refs: list[str], baseline: dict,
                             test_impact: list[str]) -> dict:
    """Собрать content Work Order из VP Spec (наследует immutable-поля)."""
    return {
        "role": role,
        "goal": goal or spec_content.get("result", ""),
        "source_of_truth": list(source_refs),
        "baseline": {"branch": baseline.get("branch", ""), "head": baseline.get("head", "")},
        "scope": {"files": _str_list(scope.get("files")), "components": _str_list(scope.get("components"))},
        "out_of_scope": list(spec_content.get("out_of_scope", [])),
        "inputs": list(spec_content.get("inputs", [])),
        "acceptance_criteria": criteria,
        "required_checks": list(spec_content.get("required_checks", [])),
        "test_impact": test_impact or list(DEFAULT_TEST_IMPACT),
        "capabilities": capabilities,
        "prohibited_actions": list(PROHIBITED_ACTIONS),
        "stop_conditions": list(spec_content.get("stop_conditions", STOP_CONDITIONS)),
        "immutable_constraints": list(spec_content.get("immutable_constraints", IMMUTABLE_CONSTRAINTS)),
        "report_schema": REPORT_SCHEMA_REF,
        "exact_next_action": "Перевести Work Order в ready, затем claim (active) под одним writer.",
    }


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def criteria_ids(criteria: list[dict]) -> list[str]:
    return [c["id"] for c in criteria]


# --- сервис ----------------------------------------------------------------
class WorkOrderService:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or load_settings().db_path

    # --- идемпотентность ---------------------------------------------------
    def _idem_lookup(self, s, key: str) -> str | None:
        if not key:
            return None
        row = s.get(IdempotencyKey, key)
        return row.entity_id if row else None

    def _idem_store(self, s, key: str, scope: str, project_id: str, entity_id: str) -> None:
        if not key:
            return
        s.add(IdempotencyKey(key=key[:120], scope=scope, project_id=project_id, entity_id=entity_id))

    def _require_available(self, s, project_id: str) -> Project:
        p = s.get(Project, project_id)
        if p is None:
            raise WorkOrderError("NOT_FOUND", f"проект не найден: {project_id}")
        if p.status != "connected":
            raise WorkOrderError("PROJECT_NOT_AVAILABLE", f"проект недоступен (status={p.status})")
        return p

    def _latest_baseline(self, s, project_id: str) -> dict:
        row = s.execute(select(GitBaseline).where(GitBaseline.project_id == project_id)
                        .order_by(GitBaseline.observed_at.desc()).limit(1)).scalars().first()
        if row is None:
            return {"branch": "", "head": ""}
        return {"branch": row.branch, "head": row.head}

    def _current_approval(self, s, project_id: str) -> Approval | None:
        return s.execute(select(Approval).where(Approval.project_id == project_id)
                         .order_by(Approval.created_at.desc()).limit(1)).scalars().first()

    def _decisions_state_hash(self, s, project_id: str) -> str:
        rows = s.execute(select(Decision).where(Decision.project_id == project_id)
                         .order_by(Decision.decision_key.asc())).scalars().all()
        return content_hash([{"key": d.decision_key, "status": d.status} for d in rows])

    def _validate_source_binding(self, s, project_id: str, appr: Approval) -> tuple[Brief, MapVersion | None]:
        """Отклонить устаревшую/несовпадающую привязку источника (§37 Phase C)."""
        brief = s.get(Brief, appr.brief_id)
        if brief is None or brief.project_id != project_id:
            raise WorkOrderError("SOURCE_STALE", "принятый Brief не найден")
        if brief.content_hash != appr.brief_hash:
            raise WorkOrderError("SOURCE_STALE", "hash Brief разошёлся с approval")
        if self._decisions_state_hash(s, project_id) != appr.decisions_hash:
            raise WorkOrderError("SOURCE_STALE", "состояние решений изменилось после approval")
        mv = s.get(MapVersion, appr.map_version_id) if appr.map_version_id else None
        if appr.map_version_id and (mv is None or mv.project_id != project_id):
            raise WorkOrderError("SOURCE_STALE", "принятая версия карты не найдена")
        return brief, mv

    # --- VP Spec -----------------------------------------------------------
    def create_vp_spec(self, project_id: str, vp_key: str, *, actor: str = "owner",
                       correlation_id: str = "", idempotency_key: str = "") -> dict:
        vp_key = _item(vp_key)
        if not vp_key:
            raise WorkOrderError("SPEC_INVALID", "vp_key обязателен")
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self.get_vp_spec(project_id, seen)
            appr = self._current_approval(s, project_id)
            if appr is None:
                raise WorkOrderError("OWNER_REQUIRED",
                                     "нет принятого Brief/Map/approval — VP Spec невозможен")
            brief, mv = self._validate_source_binding(s, project_id, appr)
            brief_content = json.loads(brief.content_json)
            envelope = json.loads(brief.envelope_json)
            decisions = [{"decision_key": d.decision_key, "title": d.title,
                          "status": d.status, "required": bool(d.required)}
                         for d in s.execute(select(Decision).where(Decision.project_id == project_id)
                                            .order_by(Decision.decision_key.asc())).scalars().all()]
            baseline = self._latest_baseline(s, project_id)
            source_refs = self._source_refs(project_id, appr, brief, mv, baseline)
            content = build_vp_spec_content(vp_key=vp_key, brief_content=brief_content,
                                            envelope=envelope, decisions=decisions,
                                            baseline=baseline, source_refs=source_refs)
            _guard_no_secret(canonical_json(content))
            # версия
            cur = s.execute(select(func.max(VpSpec.version)).where(
                VpSpec.project_id == project_id, VpSpec.vp_key == vp_key)).scalar()
            version = (cur or 0) + 1
            parent = ""
            if cur:
                prev = s.execute(select(VpSpec).where(
                    VpSpec.project_id == project_id, VpSpec.vp_key == vp_key,
                    VpSpec.version == cur)).scalars().first()
                if prev:
                    parent = prev.id
                    prev.status = "superseded"
                    prev.superseded_at = _now()
            spec_id = new_id("spec")
            ch = content_hash(content)
            s.add(VpSpec(
                id=spec_id, project_id=project_id, vp_key=vp_key, version=version,
                parent_id=parent, status="active", schema_version=SPEC_SCHEMA_VERSION,
                approval_id=appr.id, brief_id=brief.id, brief_hash=brief.content_hash,
                map_version_id=appr.map_version_id, map_hash=(mv.content_hash if mv else ""),
                envelope_hash=brief.envelope_hash, decisions_hash=appr.decisions_hash,
                baseline_branch=baseline["branch"], baseline_head=baseline["head"],
                content_json=canonical_json(content), content_hash=ch,
                actor=actor, correlation_id=correlation_id, created_at=_now()))
            self._idem_store(s, idempotency_key, "vp_spec.create", project_id, spec_id)
            s.commit()
        audit.record("workorders.vpspec.created",
                     f"project={project_id} vp={vp_key} spec={spec_id} v={version} hash={ch}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_vp_spec(project_id, spec_id)

    def _source_refs(self, project_id: str, appr: Approval, brief: Brief,
                     mv: MapVersion | None, baseline: dict) -> list[str]:
        refs = [
            f"project:{project_id}",
            f"approval:{appr.id}",
            f"brief:{brief.id}@v{brief.version}#{brief.content_hash}",
            f"envelope:#{brief.envelope_hash}",
            f"decisions:#{appr.decisions_hash}",
            "docs/MASTER_SPEC.md",
        ]
        if mv is not None:
            refs.append(f"map:{mv.id}@v{mv.version}#{mv.content_hash}")
        if baseline.get("head"):
            refs.append(f"baseline:{baseline['branch']}@{baseline['head']}")
        return refs

    def get_vp_spec(self, project_id: str, spec_id: str) -> dict:
        with session_scope() as s:
            sp = s.get(VpSpec, spec_id)
            if sp is None or sp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"VP Spec не найден: {spec_id}")
            return _spec_dict(sp, full=True)

    def get_active_vp_spec(self, project_id: str, vp_key: str) -> dict | None:
        with session_scope() as s:
            sp = s.execute(select(VpSpec).where(
                VpSpec.project_id == project_id, VpSpec.vp_key == _item(vp_key),
                VpSpec.status == "active").order_by(VpSpec.version.desc()).limit(1)).scalars().first()
            return _spec_dict(sp, full=True) if sp else None

    def list_vp_specs(self, project_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(VpSpec).where(VpSpec.project_id == project_id)
                             .order_by(VpSpec.created_at.asc())).scalars().all()
            return [_spec_dict(sp) for sp in rows]

    # --- Work Order --------------------------------------------------------
    def create_work_order(self, project_id: str, spec_id: str, *, role: str = "builder",
                          goal: str = "", criterion_ids: list[str] | None = None,
                          scope: dict | None = None, capabilities: list[str] | None = None,
                          test_impact: list[str] | None = None, wo_key: str = "",
                          actor: str = "owner", correlation_id: str = "",
                          idempotency_key: str = "") -> dict:
        role = (role or "builder").strip()
        if role not in ROLES:
            raise WorkOrderError("WO_INVALID", f"неизвестная роль: {role}")
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self.get_work_order(project_id, seen)
            sp = s.get(VpSpec, spec_id)
            if sp is None or sp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"VP Spec не найден: {spec_id}")
            if sp.status == "superseded":
                raise WorkOrderError("SOURCE_STALE", "VP Spec устарел (superseded)")
            # привязка источника должна оставаться актуальной
            appr = self._current_approval(s, project_id)
            if appr is None or appr.id != sp.approval_id:
                raise WorkOrderError("SOURCE_STALE", "approval изменился после создания VP Spec")
            self._validate_source_binding(s, project_id, appr)

            spec_content = json.loads(sp.content_json)
            spec_criteria = spec_content.get("acceptance_criteria", [])
            if criterion_ids:
                wanted = set(criterion_ids)
                chosen = [c for c in spec_criteria if c["id"] in wanted]
                missing = wanted - {c["id"] for c in spec_criteria}
                if missing:
                    raise WorkOrderError("WO_INVALID", f"неизвестные criterion_ids: {sorted(missing)}")
            else:
                chosen = list(spec_criteria)
            criteria = [{"id": c["id"], "text": c["text"], "required": bool(c.get("required")),
                         "shared": False, "source": c.get("source", "")} for c in chosen]
            caps = allowlist_capabilities(capabilities) if capabilities is not None \
                else list(_DEFAULT_CAPS[role])
            baseline = {"branch": sp.baseline_branch, "head": sp.baseline_head}
            content = build_work_order_content(
                role=role, goal=_item(goal), spec_content=spec_content, criteria=criteria,
                scope=scope or {}, capabilities=caps,
                source_refs=spec_content.get("inputs", []), baseline=baseline,
                test_impact=_str_list(test_impact))
            _guard_no_secret(canonical_json(content))
            wo_id = new_id("wo")
            ch = content_hash(content)
            now = _now()
            s.add(WorkOrder(
                id=wo_id, project_id=project_id, vp_spec_id=sp.id, vp_key=sp.vp_key,
                wo_key=_item(wo_key) or wo_id, role=role, status="draft",
                goal=content["goal"], parent_id="", origin="spec",
                approval_id=sp.approval_id, spec_hash=sp.content_hash, spec_version=sp.version,
                brief_hash=sp.brief_hash, map_hash=sp.map_hash, envelope_hash=sp.envelope_hash,
                baseline_branch=sp.baseline_branch, baseline_head=sp.baseline_head,
                content_json=canonical_json(content), content_hash=ch,
                schema_version=WO_SCHEMA_VERSION, version=1, actor=actor,
                correlation_id=correlation_id, created_at=now, updated_at=now))
            self._add_event(s, wo_id, project_id, "", "draft", "created",
                            f"role={role} criteria={len(criteria)}", actor, correlation_id)
            self._idem_store(s, idempotency_key, "wo.create", project_id, wo_id)
            s.commit()
        audit.record("workorders.wo.created",
                     f"project={project_id} wo={wo_id} spec={spec_id} role={role} hash={ch}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_work_order(project_id, wo_id)

    def _add_event(self, s, wo_id: str, project_id: str, from_st: str, to_st: str,
                   reason: str, note: str, actor: str, correlation_id: str) -> None:
        s.add(WorkOrderEvent(id=new_id("woe"), work_order_id=wo_id, project_id=project_id,
                             from_status=from_st, to_status=to_st, reason_code=reason,
                             note=_item(note), actor=actor, correlation_id=correlation_id,
                             created_at=_now()))

    # --- lifecycle ---------------------------------------------------------
    def _lease_key(self, wo: WorkOrder) -> str:
        return f"wt:{wo.project_id}:{wo.baseline_branch or wo.vp_key or 'default'}"

    def _is_executable(self, wo: WorkOrder) -> tuple[bool, str]:
        content = json.loads(wo.content_json)
        if not wo.goal:
            return False, "нет goal"
        if not content.get("acceptance_criteria"):
            return False, "нет acceptance criteria"
        if not (wo.spec_hash and wo.approval_id):
            return False, "нет привязки источника"
        return True, "ok"

    def transition(self, project_id: str, wo_id: str, to_status: str, *,
                   expected_version: int | None = None, reason_code: str = "",
                   note: str = "", holder: str = "builder", actor: str = "owner",
                   correlation_id: str = "") -> dict:
        """Перевести Work Order в новый статус.

        Фазы разделены, чтобы writer-аренда (отдельное sqlite-соединение) не
        держалась одновременно с write-транзакцией ORM (SQLite WAL = один
        writer): 1) валидация (read-only), 2) acquire аренды, 3) версионный
        UPDATE + commit, 4) release старой аренды. Оптимистичная ``version``
        гарантирует атомарность при гонках; невалидный переход не меняет state.
        """
        to_status = (to_status or "").strip()
        if to_status not in WO_STATUSES:
            raise WorkOrderError("WO_INVALID", f"неизвестный статус: {to_status}")
        # --- фаза 1: валидация (только чтение) ---
        with session_scope() as s:
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            self._require_available(s, project_id)
            from_status = wo.status
            base_version = wo.version
            prev_lease_id = wo.lease_id
            prev_holder = wo.writer_holder
            role = wo.role
            lease_key = self._lease_key(wo)
            if to_status not in VALID_TRANSITIONS.get(from_status, set()):
                raise WorkOrderError("INVALID_TRANSITION",
                                     f"переход {from_status}→{to_status} недопустим")
            if expected_version is not None and expected_version != base_version:
                raise WorkOrderError("VERSION_CONFLICT",
                                     f"ожидалась версия {expected_version}, актуальна {base_version}")
            if to_status == "ready":
                ok, why = self._is_executable(wo)
                if not ok:
                    raise WorkOrderError("WO_INVALID", f"Work Order не executable: {why}")

        # Приобретаем аренду при входе в active, если она фактически не удерживается
        # (после безопасного release в ротации handoff_ready остаётся без writer).
        enter_active = to_status == "active" and not prev_lease_id
        release_lease = (to_status in TERMINAL_STATUSES or to_status == "blocked") \
            and from_status in LEASE_HELD_STATUSES and bool(prev_lease_id)

        # --- фаза 2: acquire writer-аренды (отдельное соединение, без write-lock ORM) ---
        acquired_lease_id = prev_lease_id
        new_holder = prev_holder
        if enter_active:
            leases = WorktreeLeaseService(self.db_path)
            try:
                lease = leases.acquire(project_id=project_id, worktree=lease_key,
                                       role=role, holder=holder)
                acquired_lease_id, new_holder = lease.id, holder
            except AtlasError as exc:
                raise WorkOrderError("WRITER_CONFLICT",
                                     f"второй writer запрещён: {exc.classified.evidence}") from None
            finally:
                leases.close()

        # --- фаза 3: версионный UPDATE + событие + commit ---
        try:
            with session_scope() as s:
                res = s.execute(update(WorkOrder).where(
                    WorkOrder.id == wo_id, WorkOrder.version == base_version).values(
                    status=to_status, version=base_version + 1, updated_at=_now(),
                    lease_id=("" if release_lease else acquired_lease_id),
                    writer_holder=("" if release_lease else new_holder)))
                if res.rowcount != 1:
                    raise WorkOrderError("VERSION_CONFLICT", "конкурентное изменение Work Order")
                self._add_event(s, wo_id, project_id, from_status, to_status,
                                reason_code or "transition", note, actor, correlation_id)
                s.commit()
        except Exception:
            # откат только что взятой аренды при провале апдейта
            if enter_active and acquired_lease_id and acquired_lease_id != prev_lease_id:
                lk = WorktreeLeaseService(self.db_path)
                try:
                    lk.release(acquired_lease_id)
                finally:
                    lk.close()
            raise

        # --- фаза 4: release старой аренды (после commit, без write-lock) ---
        if release_lease and prev_lease_id:
            lk = WorktreeLeaseService(self.db_path)
            try:
                lk.release(prev_lease_id)
            finally:
                lk.close()

        audit.record("workorders.wo.transition",
                     f"project={project_id} wo={wo_id} {from_status}->{to_status} "
                     f"reason={reason_code or 'transition'}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_work_order(project_id, wo_id)

    def release_writer_lease(self, project_id: str, wo_id: str, *,
                             expected_version: int | None = None, reason_code: str = "rotation",
                             actor: str = "core", correlation_id: str = "") -> dict:
        """Освободить writer-аренду в документированной безопасной точке ротации
        (§16.5 шаг 6), не меняя статус Work Order. Только в LEASE_HELD-статусах."""
        with session_scope() as s:
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            if wo.status not in LEASE_HELD_STATUSES:
                raise WorkOrderError("INVALID_TRANSITION",
                                     f"release аренды недопустим в статусе {wo.status}")
            base_version = wo.version
            prev_lease_id = wo.lease_id
            if expected_version is not None and expected_version != base_version:
                raise WorkOrderError("VERSION_CONFLICT",
                                     f"ожидалась версия {expected_version}, актуальна {base_version}")
        with session_scope() as s:
            res = s.execute(update(WorkOrder).where(
                WorkOrder.id == wo_id, WorkOrder.version == base_version).values(
                version=base_version + 1, lease_id="", writer_holder="", updated_at=_now()))
            if res.rowcount != 1:
                raise WorkOrderError("VERSION_CONFLICT", "конкурентное изменение Work Order")
            self._add_event(s, wo_id, project_id, wo.status, wo.status, reason_code,
                            "writer lease released", actor, correlation_id)
            s.commit()
        if prev_lease_id:
            lk = WorktreeLeaseService(self.db_path)
            try:
                lk.release(prev_lease_id)
            finally:
                lk.close()
        audit.record("workorders.wo.lease_released",
                     f"project={project_id} wo={wo_id} reason={reason_code}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_work_order(project_id, wo_id)

    def writer_count(self, project_id: str, wo_id: str) -> int:
        """Число активных writer-аренд на lease-key этого Work Order (проверка one-writer)."""
        with session_scope() as s:
            wo = s.get(WorkOrder, wo_id)
            if wo is None:
                return 0
            key = self._lease_key(wo)
        leases = WorktreeLeaseService(self.db_path)
        try:
            return leases.active_count(key)
        finally:
            leases.close()

    # --- reads -------------------------------------------------------------
    def get_work_order(self, project_id: str, wo_id: str, *, with_history: bool = True) -> dict:
        with session_scope() as s:
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            out = _wo_dict(wo, full=True)
            if with_history:
                ev = s.execute(select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == wo_id)
                               .order_by(WorkOrderEvent.created_at.asc())).scalars().all()
                out["history"] = [{"from": e.from_status, "to": e.to_status,
                                   "reason": e.reason_code, "note": e.note,
                                   "at": _iso(e.created_at)} for e in ev]
            return out

    def list_work_orders(self, project_id: str, *, vp_spec_id: str | None = None,
                         status: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(WorkOrder).where(WorkOrder.project_id == project_id)
            if vp_spec_id:
                stmt = stmt.where(WorkOrder.vp_spec_id == vp_spec_id)
            if status:
                stmt = stmt.where(WorkOrder.status == status)
            rows = s.execute(stmt.order_by(WorkOrder.created_at.asc())).scalars().all()
            return [_wo_dict(wo) for wo in rows]


# --- сериализация ----------------------------------------------------------
def _spec_dict(sp: VpSpec, *, full: bool = False) -> dict:
    out = {
        "id": sp.id, "project_id": sp.project_id, "vp_key": sp.vp_key,
        "version": sp.version, "status": sp.status, "schema_version": sp.schema_version,
        "content_hash": sp.content_hash, "created_at": _iso(sp.created_at),
        "binding": {
            "approval_id": sp.approval_id, "brief_id": sp.brief_id,
            "brief_hash": sp.brief_hash, "map_version_id": sp.map_version_id,
            "map_hash": sp.map_hash, "envelope_hash": sp.envelope_hash,
            "decisions_hash": sp.decisions_hash,
            "baseline_branch": sp.baseline_branch, "baseline_head": sp.baseline_head,
        },
    }
    if full:
        out["content"] = json.loads(sp.content_json)
    return out


def _wo_dict(wo: WorkOrder, *, full: bool = False) -> dict:
    out = {
        "id": wo.id, "project_id": wo.project_id, "vp_spec_id": wo.vp_spec_id,
        "vp_key": wo.vp_key, "wo_key": wo.wo_key, "role": wo.role, "status": wo.status,
        "goal": wo.goal, "origin": wo.origin, "parent_id": wo.parent_id,
        "version": wo.version, "content_hash": wo.content_hash,
        "spec_version": wo.spec_version, "writer_holder": wo.writer_holder,
        "lease_active": bool(wo.lease_id),
        "created_at": _iso(wo.created_at), "updated_at": _iso(wo.updated_at),
        "binding": {
            "approval_id": wo.approval_id, "spec_hash": wo.spec_hash,
            "brief_hash": wo.brief_hash, "map_hash": wo.map_hash,
            "envelope_hash": wo.envelope_hash,
            "baseline_branch": wo.baseline_branch, "baseline_head": wo.baseline_head,
        },
    }
    if full:
        out["content"] = json.loads(wo.content_json)
    return out


__all__ = ["WorkOrderService", "WorkOrderError", "VALID_TRANSITIONS", "WO_STATUSES",
           "ROLES", "CAPABILITY_ALLOWLIST", "PROHIBITED_CAPABILITIES",
           "allowlist_capabilities", "build_vp_spec_content", "build_work_order_content",
           "criteria_ids", "SPEC_SCHEMA_VERSION", "WO_SCHEMA_VERSION"]
