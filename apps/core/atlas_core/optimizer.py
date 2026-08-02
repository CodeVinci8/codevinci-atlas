"""VP-4 Optimizer — решения READY / MERGE_TASKS / SPLIT_AT_CHECKPOINT /
SWITCH_PROFILE / OWNER_REQUIRED (Master Spec §16.2, §37 Phase E).

Оптимизатор НИКОГДА не меняет scope или acceptance criteria. Merge объединяет
только совместимые Work Order и сохраняет каждый критерий (общие помечаются
``shared``, а не дублируются). Split возможен только на durable-checkpoint и
делит работу на два независимо законченных результата с полным отображением
критериев. Если совместимость/намерение недоказуемы — ``OWNER_REQUIRED``;
не гадаем. Каждое решение несёт стабильный reason-код, ограниченное
объяснение, затронутые Work Order ID и точное следующее действие.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import IdempotencyKey, OptimizerDecision, WoCheckpoint, WorkOrder
from .productmap import canonical_json, content_hash
from .workorders import WorkOrderError, _item, _now

DECISIONS = ("READY", "MERGE_TASKS", "SPLIT_AT_CHECKPOINT", "SWITCH_PROFILE", "OWNER_REQUIRED")
# Верхняя граница числа критериев в одном bounded JobPackage (§16.3).
_MAX_MERGED_CRITERIA = 40


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _criteria_of(wo: WorkOrder) -> list[dict]:
    return json.loads(wo.content_json).get("acceptance_criteria", [])


def _binding_of(wo: WorkOrder) -> tuple:
    return (wo.vp_spec_id, wo.role, wo.spec_hash, wo.brief_hash, wo.map_hash,
            wo.envelope_hash, wo.baseline_branch, wo.baseline_head)


def _caps_of(wo: WorkOrder) -> list[str]:
    return json.loads(wo.content_json).get("capabilities", [])


class OptimizerService:
    def __init__(self, db_path: str | None = None):
        from .settings import load_settings
        self.db_path = db_path or load_settings().db_path

    # --- идемпотентность ---------------------------------------------------
    def _idem_lookup(self, s, key: str) -> str | None:
        if not key:
            return None
        row = s.get(IdempotencyKey, key)
        return row.entity_id if row else None

    def _idem_store(self, s, key: str, scope: str, project_id: str, entity_id: str) -> None:
        if key:
            s.add(IdempotencyKey(key=key[:120], scope=scope, project_id=project_id, entity_id=entity_id))

    def _load_wos(self, s, project_id: str, ids: list[str]) -> list[WorkOrder]:
        wos = []
        for wid in ids:
            wo = s.get(WorkOrder, wid)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wid}")
            wos.append(wo)
        return wos

    # --- merge-совместимость -----------------------------------------------
    def _merge_compat(self, wos: list[WorkOrder]) -> tuple[bool, str]:
        if len(wos) < 2:
            return False, "нужно ≥2 Work Order для merge"
        b0 = _binding_of(wos[0])
        caps0 = set(_caps_of(wos[0]))
        c0 = json.loads(wos[0].content_json)
        for wo in wos[1:]:
            if _binding_of(wo) != b0:
                return False, "несовпадающая роль/источник/baseline"
            if set(_caps_of(wo)) != caps0:
                return False, "несовместимые capabilities"
            c = json.loads(wo.content_json)
            if set(c.get("immutable_constraints", [])) != set(c0.get("immutable_constraints", [])):
                return False, "конфликт immutable constraints"
            if set(c.get("stop_conditions", [])) != set(c0.get("stop_conditions", [])):
                return False, "конфликт stop conditions"
        # bounded: объединённое число критериев не выходит за предел пакета
        merged = self._merged_criteria(wos)
        if len(merged) > _MAX_MERGED_CRITERIA:
            return False, f"объединённый JobPackage превысит предел ({len(merged)})"
        if any(wo.status not in ("draft", "ready") for wo in wos):
            return False, "merge только для Work Order в статусе draft/ready"
        return True, "ok"

    def _merged_criteria(self, wos: list[WorkOrder]) -> list[dict]:
        """Объединить критерии по ID без потери; общие пометить shared (не дублировать)."""
        by_id: dict[str, dict] = {}
        seen_in: dict[str, int] = {}
        order: list[str] = []
        for wo in wos:
            for c in _criteria_of(wo):
                cid = c["id"]
                if cid not in by_id:
                    by_id[cid] = {"id": cid, "text": c["text"], "required": bool(c.get("required")),
                                  "shared": False, "source": c.get("source", "")}
                    order.append(cid)
                    seen_in[cid] = 1
                else:
                    seen_in[cid] += 1
        for cid, n in seen_in.items():
            if n > 1:
                by_id[cid]["shared"] = True
        return [by_id[cid] for cid in order]

    def _merged_scope(self, wos: list[WorkOrder]) -> dict:
        files: list[str] = []
        comps: list[str] = []
        oos: list[str] = []
        for wo in wos:
            c = json.loads(wo.content_json)
            for f in c.get("scope", {}).get("files", []):
                if f not in files:
                    files.append(f)
            for cp in c.get("scope", {}).get("components", []):
                if cp not in comps:
                    comps.append(cp)
            for o in c.get("out_of_scope", []):
                if o not in oos:
                    oos.append(o)
        return {"files": files, "components": comps, "out_of_scope": oos}

    def merge_preview(self, project_id: str, wo_ids: list[str]) -> dict:
        with session_scope() as s:
            wos = self._load_wos(s, project_id, wo_ids)
            ok, why = self._merge_compat(wos)
            merged = self._merged_criteria(wos)
            mapping = {wo.id: criteria_ids_list(_criteria_of(wo)) for wo in wos}
            union_ids = criteria_ids_list(merged)
            all_parent_ids = sorted({cid for ids in mapping.values() for cid in ids})
            conserved = sorted(union_ids) == all_parent_ids
            shared = [c["id"] for c in merged if c["shared"]]
            return {
                "compatible": ok, "reason": why if not ok else "COMPATIBLE_MERGE",
                "work_order_ids": wo_ids,
                "criterion_mapping": mapping,
                "merged_criteria": merged,
                "shared_criteria": shared,
                "criterion_conservation": conserved,
                "scope": self._merged_scope(wos),
            }

    def merge_confirm(self, project_id: str, wo_ids: list[str], *, goal: str = "",
                      actor: str = "owner", correlation_id: str = "",
                      idempotency_key: str = "") -> dict:
        with session_scope() as s:
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                from .workorders import WorkOrderService
                return {"decision": "MERGE_TASKS",
                        "merged_work_order": WorkOrderService(self.db_path).get_work_order(project_id, seen)}
            wos = self._load_wos(s, project_id, wo_ids)
            ok, why = self._merge_compat(wos)
            if not ok:
                self._record(s, project_id, wos[0].vp_spec_id, "OWNER_REQUIRED",
                             "MERGE_INCOMPATIBLE", why, wo_ids,
                             "Разрешите несовместимость вручную или уточните Work Order.",
                             actor, correlation_id)
                s.commit()
                raise WorkOrderError("MERGE_INCOMPATIBLE", why)
            merged = self._merged_criteria(wos)
            scope = self._merged_scope(wos)
            # инвариант сохранения критериев (жёсткая проверка перед мутацией)
            parent_ids = {cid for wo in wos for cid in criteria_ids_list(_criteria_of(wo))}
            if set(criteria_ids_list(merged)) != parent_ids:
                raise WorkOrderError("CRITERIA_LOST", "merge потерял бы критерий")
            base = wos[0]
            bc = json.loads(base.content_json)
            content = dict(bc)
            content["acceptance_criteria"] = merged
            content["scope"] = {"files": scope["files"], "components": scope["components"]}
            content["out_of_scope"] = scope["out_of_scope"]
            content["goal"] = _item(goal) or f"Объединённый Work Order ({len(wos)} источника)"
            content["merged_from"] = wo_ids
            wo_id = _insert_wo_row(s, base=base, content=content, origin="merge",
                                   parent_id="", actor=actor, correlation_id=correlation_id)
            # источники архивируются без деструктивного удаления
            self._archive_sources(s, wos, "merged", actor, correlation_id)
            self._record(s, project_id, base.vp_spec_id, "MERGE_TASKS", "COMPATIBLE_MERGE",
                         f"объединено {len(wos)} Work Order, критериев {len(merged)}",
                         wo_ids + [wo_id],
                         f"Перевести объединённый Work Order {wo_id} в ready.",
                         actor, correlation_id)
            self._idem_store(s, idempotency_key, "optimizer.merge", project_id, wo_id)
            s.commit()
        audit.record("workorders.optimizer.merge",
                     f"project={project_id} merged={wo_id} from={wo_ids}",
                     actor=actor, correlation_id=correlation_id)
        from .workorders import WorkOrderService
        return {"decision": "MERGE_TASKS", "reason_code": "COMPATIBLE_MERGE",
                "merged_work_order": WorkOrderService(self.db_path).get_work_order(project_id, wo_id),
                "criterion_mapping": {wo: criteria_ids_list(_criteria_of(w))
                                      for wo, w in zip(wo_ids, wos)}}

    # --- split -------------------------------------------------------------
    def _validate_split(self, wo: WorkOrder, groups: list[list[str]]) -> list[list[str]]:
        if len(groups) != 2:
            raise WorkOrderError("SPLIT_INVALID", "split даёт ровно два результата")
        parent_ids = set(criteria_ids_list(_criteria_of(wo)))
        g0, g1 = set(groups[0]), set(groups[1])
        if not g0 or not g1:
            raise WorkOrderError("SPLIT_INVALID", "каждый результат должен иметь ≥1 критерий")
        unknown = (g0 | g1) - parent_ids
        if unknown:
            raise WorkOrderError("SPLIT_INVALID", f"неизвестные критерии: {sorted(unknown)}")
        lost = parent_ids - (g0 | g1)
        if lost:
            raise WorkOrderError("CRITERIA_LOST", f"критерии без назначения: {sorted(lost)}")
        return [list(groups[0]), list(groups[1])]

    def split_preview(self, project_id: str, wo_id: str, groups: list[list[str]]) -> dict:
        with session_scope() as s:
            wo = self._load_wos(s, project_id, [wo_id])[0]
            g = self._validate_split(wo, groups)
            crit = {c["id"]: c for c in _criteria_of(wo)}
            shared = sorted(set(g[0]) & set(g[1]))
            children = [self._child_criteria(crit, ids, shared) for ids in g]
            union = sorted(set(g[0]) | set(g[1]))
            return {
                "splittable": wo.status == "checkpointed",
                "reason": "CHECKPOINT_SPLITTABLE" if wo.status == "checkpointed" else "NOT_AT_CHECKPOINT",
                "work_order_id": wo_id,
                "children_criteria": children,
                "shared_criteria": shared,
                "criterion_conservation": union == sorted(set(criteria_ids_list(_criteria_of(wo)))),
            }

    def _child_criteria(self, crit_by_id: dict, ids: list[str], shared: list[str]) -> list[dict]:
        out = []
        for cid in ids:
            c = crit_by_id[cid]
            out.append({"id": cid, "text": c["text"], "required": bool(c.get("required")),
                        "shared": cid in shared, "source": c.get("source", "")})
        return out

    def split_confirm(self, project_id: str, wo_id: str, groups: list[list[str]], *,
                      checkpoint_id: str, goals: list[str] | None = None,
                      actor: str = "owner", correlation_id: str = "",
                      idempotency_key: str = "") -> dict:
        goals = goals or ["", ""]
        with session_scope() as s:
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                dec = s.get(OptimizerDecision, seen)
                return json.loads(dec.affected_json) if dec else {}
            wo = self._load_wos(s, project_id, [wo_id])[0]
            if wo.status != "checkpointed":
                raise WorkOrderError("SPLIT_INVALID",
                                     "split возможен только на durable-checkpoint (статус checkpointed)")
            cp = s.get(WoCheckpoint, checkpoint_id)
            if cp is None or cp.work_order_id != wo_id:
                raise WorkOrderError("NOT_FOUND", "checkpoint не найден для этого Work Order")
            g = self._validate_split(wo, groups)
            crit = {c["id"]: c for c in _criteria_of(wo)}
            shared = sorted(set(g[0]) & set(g[1]))
            base_content = json.loads(wo.content_json)
            child_ids = []
            for i, ids in enumerate(g):
                content = dict(base_content)
                content["acceptance_criteria"] = self._child_criteria(crit, ids, shared)
                content["goal"] = _item(goals[i]) if i < len(goals) and goals[i] else \
                    f"{wo.goal} — результат {i + 1}"
                content["split_from"] = wo_id
                cid = _insert_wo_row(s, base=wo, content=content, origin="split",
                                     parent_id=wo_id, actor=actor, correlation_id=correlation_id)
                child_ids.append(cid)
            # родитель архивируется (split), lease освобождается через сервис ниже
            mapping = {child_ids[0]: g[0], child_ids[1]: g[1]}
            result = {"decision": "SPLIT_AT_CHECKPOINT", "reason_code": "CHECKPOINT_SPLITTABLE",
                      "parent": wo_id, "checkpoint_id": checkpoint_id,
                      "children": child_ids, "criterion_mapping": mapping,
                      "shared_criteria": shared}
            did = self._record(s, project_id, wo.vp_spec_id, "SPLIT_AT_CHECKPOINT",
                               "CHECKPOINT_SPLITTABLE",
                               f"split на checkpoint {checkpoint_id}: {len(g[0])}+{len(g[1])} критериев",
                               [wo_id] + child_ids,
                               f"Перевести дочерние Work Order {child_ids} в ready.",
                               actor, correlation_id, affected_payload=result)
            self._idem_store(s, idempotency_key, "optimizer.split", project_id, did)
            s.commit()
        # родитель → cancelled (архив, без деструктивного удаления; освобождает lease)
        from .workorders import WorkOrderService
        wsvc = WorkOrderService(self.db_path)
        parent = wsvc.get_work_order(project_id, wo_id, with_history=False)
        wsvc.transition(project_id, wo_id, "cancelled", expected_version=parent["version"],
                        reason_code="split_at_checkpoint", note=f"split→{child_ids}",
                        actor=actor, correlation_id=correlation_id)
        audit.record("workorders.optimizer.split",
                     f"project={project_id} parent={wo_id} children={child_ids} cp={checkpoint_id}",
                     actor=actor, correlation_id=correlation_id)
        return result

    # --- evaluate ----------------------------------------------------------
    def evaluate(self, project_id: str, work_order_ids: list[str], *, signals: dict | None = None,
                 actor: str = "core", correlation_id: str = "") -> dict:
        signals = signals or {}
        with session_scope() as s:
            wos = self._load_wos(s, project_id, work_order_ids) if work_order_ids else []
            spec_id = wos[0].vp_spec_id if wos else ""
            dec = self._evaluate_inner(s, project_id, spec_id, wos, work_order_ids,
                                       signals, actor, correlation_id)
            s.commit()
        audit.record(f"workorders.optimizer.{dec['decision'].lower()}",
                     f"project={project_id} reason={dec['reason_code']} affected={work_order_ids}",
                     actor=actor, correlation_id=correlation_id)
        return dec

    def _evaluate_inner(self, s, project_id, spec_id, wos, work_order_ids, signals,
                        actor, correlation_id) -> dict:
        # 1) явный запрос смены профиля / rate limit → SWITCH_PROFILE (без VP-5 routing)
        if signals.get("rate_limited") or signals.get("profile_switch"):
            reason = "RATE_LIMIT" if signals.get("rate_limited") else "PROFILE_SWITCH_REQUESTED"
            return self._decide(s, project_id, spec_id, "SWITCH_PROFILE", reason,
                                "Зафиксирован запрос смены профиля; маршрутизация — VP-5.",
                                work_order_ids,
                                "Записать запрос профиля, выполнить безопасную ротацию (не VP-5).",
                                actor, correlation_id)
        # 2) повтор/failed review → OWNER_REQUIRED (не гадаем)
        if int(signals.get("repeated_failures", 0)) >= 2 or signals.get("failed_review"):
            reason = "FAILED_REVIEW" if signals.get("failed_review") else "REPEATED_FAILURE"
            return self._decide(s, project_id, spec_id, "OWNER_REQUIRED", reason,
                                "Повтор/провал review: требуется решение владельца.",
                                work_order_ids, "Эскалировать владельцу с redacted-evidence.",
                                actor, correlation_id)
        if not wos:
            return self._decide(s, project_id, spec_id, "OWNER_REQUIRED", "NOTHING_TO_DO",
                                "Нет Work Order для оценки.", [],
                                "Создайте executable Work Order из VP Spec.", actor, correlation_id)
        # 3) несколько WO → merge, если совместимы; иначе OWNER_REQUIRED
        if len(wos) >= 2:
            ok, why = self._merge_compat(wos)
            if ok:
                return self._decide(s, project_id, spec_id, "MERGE_TASKS", "COMPATIBLE_MERGE",
                                    f"{len(wos)} совместимых Work Order можно объединить без потери критериев.",
                                    work_order_ids, "Подтвердите merge (preview → confirm).",
                                    actor, correlation_id)
            return self._decide(s, project_id, spec_id, "OWNER_REQUIRED", "MERGE_INCOMPATIBLE",
                                why, work_order_ids,
                                "Уточните Work Order или разрешите вручную; merge небезопасен.",
                                actor, correlation_id)
        # 4) один WO
        wo = wos[0]
        if signals.get("at_checkpoint") and wo.status == "checkpointed" and len(_criteria_of(wo)) >= 2:
            return self._decide(s, project_id, spec_id, "SPLIT_AT_CHECKPOINT", "CHECKPOINT_SPLITTABLE",
                                "На durable-checkpoint возможен split на два законченных результата.",
                                work_order_ids,
                                "Подготовьте split (preview → confirm) по двум группам критериев.",
                                actor, correlation_id)
        if wo.status in ("draft", "ready", "active", "checkpointed") and _criteria_of(wo) \
                and len(_criteria_of(wo)) <= _MAX_MERGED_CRITERIA:
            return self._decide(s, project_id, spec_id, "READY", "SINGLE_BOUNDED_EXECUTABLE",
                                "Один ограниченный executable Work Order готов к исполнению.",
                                work_order_ids, "Перевести Work Order в ready/active под одним writer.",
                                actor, correlation_id)
        return self._decide(s, project_id, spec_id, "OWNER_REQUIRED", "AMBIGUOUS",
                            "Намерение неоднозначно; не гадаем.", work_order_ids,
                            "Уточните Work Order у владельца.", actor, correlation_id)

    # --- запись решения ----------------------------------------------------
    def _decide(self, s, project_id, spec_id, decision, reason, explanation, affected,
                next_action, actor, correlation_id) -> dict:
        did = self._record(s, project_id, spec_id, decision, reason, explanation, affected,
                           next_action, actor, correlation_id)
        return {"id": did, "decision": decision, "reason_code": reason,
                "explanation": explanation, "affected_work_orders": affected,
                "exact_next_action": next_action}

    def _record(self, s, project_id, spec_id, decision, reason, explanation, affected,
                next_action, actor, correlation_id, affected_payload=None) -> str:
        # ВНИМАНИЕ: без audit.record внутри write-транзакции (SQLite WAL = один
        # writer). Аудит пишется вызывающим после commit.
        did = new_id("optd")
        s.add(OptimizerDecision(
            id=did, project_id=project_id, vp_spec_id=spec_id, decision=decision,
            reason_code=reason, explanation=_item(explanation),
            affected_json=canonical_json(affected_payload if affected_payload is not None else affected),
            exact_next_action=_item(next_action),
            inputs_hash=content_hash(sorted(affected) if isinstance(affected, list) else affected),
            actor=actor, correlation_id=correlation_id, created_at=_now()))
        return did

    def _archive_sources(self, s, wos, reason, actor, correlation_id) -> None:
        from sqlalchemy import update as _upd

        from .workorders import _now as _wnow
        for wo in wos:
            s.execute(_upd(WorkOrder).where(WorkOrder.id == wo.id, WorkOrder.version == wo.version)
                      .values(status="cancelled", version=wo.version + 1, updated_at=_wnow()))
            from .workorders import WorkOrderEvent
            s.add(WorkOrderEvent(id=new_id("woe"), work_order_id=wo.id, project_id=wo.project_id,
                                 from_status=wo.status, to_status="cancelled", reason_code=reason,
                                 note="archived by optimizer", actor=actor,
                                 correlation_id=correlation_id, created_at=_wnow()))

    def note(self, project_id: str, spec_id: str, decision: str, reason: str,
             explanation: str, affected: list[str], next_action: str, *,
             actor: str = "core", correlation_id: str = "") -> dict:
        """Записать решение оптимизатора отдельной транзакцией (для Governor)."""
        with session_scope() as s:
            dec = self._decide(s, project_id, spec_id, decision, reason, explanation,
                               affected, next_action, actor, correlation_id)
            s.commit()
        audit.record(f"workorders.optimizer.{decision.lower()}",
                     f"project={project_id} reason={reason} affected={affected}",
                     actor=actor, correlation_id=correlation_id)
        return dec

    def list_decisions(self, project_id: str, *, limit: int = 50) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(OptimizerDecision).where(OptimizerDecision.project_id == project_id)
                             .order_by(OptimizerDecision.created_at.desc()).limit(limit)).scalars().all()
            return [{"id": d.id, "decision": d.decision, "reason_code": d.reason_code,
                     "explanation": d.explanation, "affected": json.loads(d.affected_json),
                     "exact_next_action": d.exact_next_action, "created_at": _iso(d.created_at)}
                    for d in rows]


def criteria_ids_list(criteria: list[dict]) -> list[str]:
    return [c["id"] for c in criteria]


def _insert_wo_row(s, *, base: WorkOrder, content: dict, origin: str, parent_id: str,
                   actor: str, correlation_id: str) -> str:
    """Вставить производный Work Order (merge/split), наследуя привязку base."""
    from .orm import WorkOrder as _WO
    from .orm import WorkOrderEvent as _WOE
    from .workorders import WO_SCHEMA_VERSION
    wo_id = new_id("wo")
    ch = content_hash(content)
    now = _now()
    s.add(_WO(
        id=wo_id, project_id=base.project_id, vp_spec_id=base.vp_spec_id, vp_key=base.vp_key,
        wo_key=wo_id, role=base.role, status="draft", goal=content.get("goal", base.goal),
        parent_id=parent_id, origin=origin, approval_id=base.approval_id,
        spec_hash=base.spec_hash, spec_version=base.spec_version, brief_hash=base.brief_hash,
        map_hash=base.map_hash, envelope_hash=base.envelope_hash,
        baseline_branch=base.baseline_branch, baseline_head=base.baseline_head,
        content_json=canonical_json(content), content_hash=ch, schema_version=WO_SCHEMA_VERSION,
        version=1, actor=actor, correlation_id=correlation_id, created_at=now, updated_at=now))
    s.add(_WOE(id=new_id("woe"), work_order_id=wo_id, project_id=base.project_id,
               from_status="", to_status="draft", reason_code=origin,
               note=f"origin={origin} parent={parent_id}", actor=actor,
               correlation_id=correlation_id, created_at=now))
    return wo_id


__all__ = ["OptimizerService", "DECISIONS", "criteria_ids_list"]
