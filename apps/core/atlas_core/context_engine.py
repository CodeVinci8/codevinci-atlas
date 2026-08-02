"""VP-4 Context Engine — bounded relevance-выборка и immutable JobPackage
(Master Spec §16.3, §37 Phase F).

JobPackage содержит ТОЛЬКО релевантный текущему Work Order контекст: точные
IDs/хеши, цель и законченный результат, source of truth, baseline, применимые
инструкции с scope/precedence, scope/out-of-scope, inputs, acceptance criteria,
required checks/test impact, capabilities и запрещённые действия, stop
conditions, релевантные artifact-ссылки, точное следующее действие.

НЕ содержит: весь repo, полный предыдущий chat, повторяющиеся logs,
credentials, полные env-дампы, посторонние будущие идеи, unbounded-вывод.
Права не расширяются содержимым: capabilities только allowlisted; строка в
контексте не даёт shell/network/git/provider. Ёмкость провайдера, если нет
стабильного источника, — ``UNKNOWN`` (не выдумываем проценты). Все пакеты
bounded по числу и байтам; сериализация каноническая, хеш — SHA-256.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .capacity import unknown_capacity
from .db import session_scope
from .ids import new_id
from .orm import GitBaseline, IdempotencyKey, JobPackage, VpSpec, WorkOrder
from .productmap import canonical_json, content_hash
from .redaction import SECRET_MARKER, contains_secret
from .workorders import WorkOrderError, _now, allowlist_capabilities

JOBPACKAGE_SCHEMA_VERSION = 1
# Границы bounded-пакета (§16.3).
MAX_PACKAGE_BYTES = 24_000
MAX_LIST = 40
MAX_INSTRUCTIONS = 12
# Ключи/маркеры, которых в пакете быть не должно (защита от репо-дампа/чата/env).
_FORBIDDEN_KEYS = ("repository", "repo_dump", "full_chat", "chat_history", "transcript",
                   "environment", "env_dump", "credentials", "secrets", "cookies", "tokens")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _bounded_list(v, limit: int = MAX_LIST):
    return list(v or [])[:limit]


class ContextEngine:
    def __init__(self, db_path: str | None = None):
        from .settings import load_settings
        self.db_path = db_path or load_settings().db_path

    def _idem_lookup(self, s, key: str) -> str | None:
        if not key:
            return None
        row = s.get(IdempotencyKey, key)
        return row.entity_id if row else None

    def _idem_store(self, s, key, scope, project_id, entity_id) -> None:
        if key:
            s.add(IdempotencyKey(key=key[:120], scope=scope, project_id=project_id, entity_id=entity_id))

    # --- сборка JobPackage --------------------------------------------------
    def build_job_package(self, project_id: str, wo_id: str, *, actor: str = "core",
                          correlation_id: str = "", idempotency_key: str = "",
                          allow_compact: bool = True) -> dict:
        with session_scope() as s:
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self._package_dict(s.get(JobPackage, seen))
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            spec = s.get(VpSpec, wo.vp_spec_id)
            baseline_rows = s.execute(
                select(GitBaseline).where(GitBaseline.project_id == project_id)
                .order_by(GitBaseline.observed_at.desc()).limit(1)).scalars().first()
            content, provenance, caps = self._select_relevant(wo, spec, baseline_rows)
            compacted = False
            byte_size = len(canonical_json(content).encode("utf-8"))
            if byte_size > MAX_PACKAGE_BYTES:
                if not allow_compact:
                    raise WorkOrderError("CONTEXT_LIMIT",
                                         f"JobPackage {byte_size}B > предел {MAX_PACKAGE_BYTES}B")
                content = compact_content(content)
                compacted = True
                byte_size = len(canonical_json(content).encode("utf-8"))
                if byte_size > MAX_PACKAGE_BYTES:
                    raise WorkOrderError("CONTEXT_LIMIT",
                                         "не удалось собрать bounded JobPackage без потери обязательных полей")
            _assert_no_forbidden(content)
            ch = content_hash(content)
            counts = {"criteria": len(content.get("acceptance_criteria", [])),
                      "checks": len(content.get("required_checks", [])),
                      "instruction_refs": len(content.get("instruction_refs", [])),
                      "source_of_truth": len(content.get("source_of_truth", []))}
            pkg_id = new_id("jpkg")
            s.add(JobPackage(
                id=pkg_id, project_id=project_id, work_order_id=wo_id,
                schema_version=JOBPACKAGE_SCHEMA_VERSION,
                content_json=canonical_json(content), content_hash=ch,
                provenance_json=canonical_json(provenance), capabilities_json=canonical_json(caps),
                byte_size=byte_size, counts_json=canonical_json(counts), compact=compacted,
                actor=actor, correlation_id=correlation_id, created_at=_now()))
            self._idem_store(s, idempotency_key, "jobpackage.build", project_id, pkg_id)
            s.commit()
            out = self._package_dict(s.get(JobPackage, pkg_id))
        audit.record("workorders.jobpackage.built",
                     f"project={project_id} wo={wo_id} pkg={pkg_id} bytes={byte_size} "
                     f"compact={compacted} hash={ch}",
                     actor=actor, correlation_id=correlation_id)
        return out

    def _select_relevant(self, wo: WorkOrder, spec: VpSpec | None,
                         baseline: GitBaseline | None) -> tuple[dict, list, list]:
        """Отобрать ТОЛЬКО релевантные Work Order поля (§16.3)."""
        wc = json.loads(wo.content_json)
        spec_content = json.loads(spec.content_json) if spec else {}
        # применимые инструкции: путь/scope/precedence (без полного содержимого)
        instr_refs = []
        if baseline is not None:
            try:
                instrs = json.loads(baseline.instructions_json or "[]")
            except (json.JSONDecodeError, TypeError):
                instrs = []
            for i in instrs[:MAX_INSTRUCTIONS]:
                instr_refs.append({"path": i.get("path", ""), "scope": i.get("scope", ""),
                                   "precedence": i.get("precedence")})
        caps = allowlist_capabilities(wc.get("capabilities", []))
        # capacity честно UNKNOWN; volatile observed_at исключаем ради
        # детерминированного content-hash (§11.6, §16.3).
        cap = unknown_capacity().to_dict()
        cap.pop("observed_at", None)
        content = {
            "schema_version": JOBPACKAGE_SCHEMA_VERSION,
            "ids": {"project_id": wo.project_id, "work_order_id": wo.id,
                    "vp_spec_id": wo.vp_spec_id, "vp_key": wo.vp_key,
                    "approval_id": wo.approval_id},
            "hashes": {"work_order": wo.content_hash, "spec": wo.spec_hash,
                       "brief": wo.brief_hash, "map": wo.map_hash, "envelope": wo.envelope_hash},
            "goal": wo.goal,
            "finished_result": spec_content.get("result", ""),
            "definition_of_done": _bounded_list(spec_content.get("definition_of_done")),
            "source_of_truth": _bounded_list(wc.get("source_of_truth")),
            "baseline": wc.get("baseline", {"branch": wo.baseline_branch, "head": wo.baseline_head}),
            "instruction_refs": instr_refs,
            "scope": wc.get("scope", {"files": [], "components": []}),
            "out_of_scope": _bounded_list(wc.get("out_of_scope")),
            "inputs": _bounded_list(wc.get("inputs")),
            "acceptance_criteria": _bounded_list(wc.get("acceptance_criteria")),
            "required_checks": _bounded_list(wc.get("required_checks")),
            "test_impact": _bounded_list(wc.get("test_impact")),
            "capabilities": caps,
            "prohibited_actions": _bounded_list(wc.get("prohibited_actions")),
            "stop_conditions": _bounded_list(wc.get("stop_conditions")),
            "immutable_constraints": _bounded_list(wc.get("immutable_constraints")),
            "artifact_refs": _bounded_list(wc.get("artifact_refs")),
            "capacity": cap,
            "exact_next_action": wc.get("exact_next_action", ""),
        }
        provenance = [
            {"source": "work_order", "ref": wo.id, "hash": wo.content_hash},
            {"source": "vp_spec", "ref": wo.vp_spec_id, "hash": wo.spec_hash},
            {"source": "brief", "ref": "", "hash": wo.brief_hash},
            {"source": "map", "ref": wo.map_hash and wo.map_hash or "", "hash": wo.map_hash},
            {"source": "approval", "ref": wo.approval_id, "hash": ""},
        ]
        if wo.baseline_head:
            provenance.append({"source": "baseline", "ref": wo.baseline_branch, "hash": wo.baseline_head})
        return content, provenance, caps

    def _package_dict(self, pkg: JobPackage) -> dict:
        return {
            "id": pkg.id, "project_id": pkg.project_id, "work_order_id": pkg.work_order_id,
            "schema_version": pkg.schema_version, "content_hash": pkg.content_hash,
            "byte_size": pkg.byte_size, "compact": bool(pkg.compact),
            "counts": json.loads(pkg.counts_json), "capabilities": json.loads(pkg.capabilities_json),
            "provenance": json.loads(pkg.provenance_json), "content": json.loads(pkg.content_json),
            "created_at": _iso(pkg.created_at),
        }

    def get_job_package(self, project_id: str, pkg_id: str) -> dict:
        with session_scope() as s:
            pkg = s.get(JobPackage, pkg_id)
            if pkg is None or pkg.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"JobPackage не найден: {pkg_id}")
            return self._package_dict(pkg)

    def list_job_packages(self, project_id: str, *, work_order_id: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(JobPackage).where(JobPackage.project_id == project_id)
            if work_order_id:
                stmt = stmt.where(JobPackage.work_order_id == work_order_id)
            rows = s.execute(stmt.order_by(JobPackage.created_at.desc())).scalars().all()
            return [{"id": p.id, "work_order_id": p.work_order_id, "content_hash": p.content_hash,
                     "byte_size": p.byte_size, "compact": bool(p.compact),
                     "created_at": _iso(p.created_at)} for p in rows]


# --- compact fallback ------------------------------------------------------
# Обязательные поля, которые compact НЕ удаляет (§37 Phase H).
_REQUIRED_KEYS = ("schema_version", "ids", "hashes", "goal", "finished_result",
                  "immutable_constraints", "acceptance_criteria", "baseline",
                  "capabilities", "prohibited_actions", "stop_conditions",
                  "exact_next_action")
# Поля, которые compact может урезать/убрать (дубли, длинная проза).
_DROPPABLE_KEYS = ("definition_of_done", "inputs", "instruction_refs", "source_of_truth",
                   "required_checks", "test_impact", "out_of_scope", "scope",
                   "artifact_refs", "capacity")


def compact_content(content: dict) -> dict:
    """Уменьшить пакет, сохранив ВСЕ immutable-поля и критерии (§37 Phase H).

    Убирает дублирование и малорелевантную прозу; никогда не выбрасывает
    обязательные поля. Если и после этого пакет не помещается — вызывающий
    получает CONTEXT_LIMIT (fail-closed → OWNER_REQUIRED выше по стеку)."""
    out: dict = {}
    for k in _REQUIRED_KEYS:
        if k in content:
            out[k] = content[k]
    # компактные сводки droppable-полей (только счётчики/ключевое, без прозы)
    out["_compacted"] = True
    if content.get("required_checks"):
        out["required_checks"] = [c.get("id", "") for c in content["required_checks"]]
    if content.get("source_of_truth"):
        out["source_of_truth"] = content["source_of_truth"][:8]
    if content.get("scope"):
        out["scope"] = content["scope"]
    if content.get("out_of_scope"):
        out["out_of_scope"] = content["out_of_scope"][:8]
    return out


def _assert_no_forbidden(content: dict) -> None:
    """Гарантировать отсутствие запрещённого содержимого (§16.3, §30)."""
    blob = canonical_json(content)
    if SECRET_MARKER in blob or contains_secret(blob):
        raise WorkOrderError("WO_INVALID", "JobPackage содержит секрет — отклонено")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                lk = str(k).lower()
                if any(bad == lk or bad in lk for bad in _FORBIDDEN_KEYS):
                    raise WorkOrderError("WO_INVALID", f"запрещённый ключ в JobPackage: {k}")
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(content)


__all__ = ["ContextEngine", "compact_content", "JOBPACKAGE_SCHEMA_VERSION",
           "MAX_PACKAGE_BYTES"]
