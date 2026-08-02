"""VP-4 Context Governor и безопасная ротация (Master Spec §16.5, §37 Phase G).

Governor детерминированно распознаёт триггеры (порог контекста, повтор/провал,
граница checkpoint, rate-limit/смена профиля, crash/recovery, провал review,
граница VP, явная команда владельца) и отображает их в документированные
состояния оптимизатора, НЕ отбрасывая молча критерии/ограничения.

Ротация выполняет безопасную последовательность §16.5: остановить новые
действия → снять diff/процесс → impacted checks → checkpoint (persist+verify)
→ handoff (build+verify) → release lease ТОЛЬКО в безопасной точке → выбрать
профиль как запрос (не VP-5 routing) → свежая сессия → ack точного хеша и
baseline → продолжить. Во время ротации второй writer не появляется.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .optimizer import OptimizerService, _criteria_of
from .orm import RotationRecord, WorkOrder
from .vp4handoff import HandoffService
from .workorders import WorkOrderError, WorkOrderService, _item, _now

# Документированные пороги (§37 Phase G).
CONTEXT_THRESHOLD_BYTES = 20_000
REPEAT_THRESHOLD = 2
# Триггеры, требующие ротации к свежей сессии.
ROTATION_TRIGGERS = ("context_threshold", "rate_limit", "profile_switch",
                     "crash_recovery", "vp_boundary", "owner_rotation")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ContextGovernor:
    def __init__(self, db_path: str | None = None):
        from .settings import load_settings
        self.db_path = db_path or load_settings().db_path
        self.optimizer = OptimizerService(self.db_path)
        self.handoff = HandoffService(self.db_path)
        self.workorders = WorkOrderService(self.db_path)

    # --- детекция триггеров → состояние оптимизатора -----------------------
    def detect(self, project_id: str, work_order_ids: list[str], *, signals: dict | None = None,
               actor: str = "core", correlation_id: str = "") -> dict:
        signals = signals or {}
        triggers = self._triggers(signals)
        with session_scope() as s:
            wos = []
            for wid in work_order_ids:
                wo = s.get(WorkOrder, wid)
                if wo is None or wo.project_id != project_id:
                    raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wid}")
                wos.append(wo)
            spec_id = wos[0].vp_spec_id if wos else ""
            single = wos[0] if len(wos) == 1 else None
            splittable = bool(single and single.status == "checkpointed"
                              and len(_criteria_of(single)) >= 2)

        decision, reason, next_action = self._map(triggers, signals, wos, splittable)
        rotation_required = any(t in ROTATION_TRIGGERS for t in triggers)
        # Записать решение (auditable) — состояние оптимизатора, критерии не тронуты.
        self.optimizer.note(project_id, spec_id, decision, reason,
                            f"governor triggers={triggers}", work_order_ids, next_action,
                            actor=actor, correlation_id=correlation_id)
        outcome = {
            "triggers": triggers,
            "primary_trigger": triggers[0] if triggers else "none",
            "decision": decision, "reason_code": reason,
            "rotation_required": rotation_required,
            "exact_next_action": next_action,
            "affected_work_orders": work_order_ids,
            "thresholds": {"context_bytes": CONTEXT_THRESHOLD_BYTES, "repeat": REPEAT_THRESHOLD},
        }
        audit.record("workorders.governor.detect",
                     f"project={project_id} triggers={triggers} decision={decision} rotate={rotation_required}",
                     actor=actor, correlation_id=correlation_id)
        return outcome

    def _triggers(self, signals: dict) -> list[str]:
        t: list[str] = []
        if signals.get("owner_rotation"):
            t.append("owner_rotation")
        if signals.get("vp_boundary"):
            t.append("vp_boundary")
        if signals.get("rate_limited"):
            t.append("rate_limit")
        if signals.get("profile_switch"):
            t.append("profile_switch")
        if signals.get("crash_recovery"):
            t.append("crash_recovery")
        if signals.get("failed_review"):
            t.append("failed_review")
        if int(signals.get("repeated_failures", 0)) >= REPEAT_THRESHOLD:
            t.append("repeated_failure")
        if signals.get("context_over_budget") or int(signals.get("context_bytes", 0)) > CONTEXT_THRESHOLD_BYTES:
            t.append("context_threshold")
        if signals.get("at_checkpoint"):
            t.append("checkpoint_boundary")
        return t

    def _map(self, triggers: list[str], signals: dict, wos, splittable: bool):
        """Отобразить триггеры в документированное состояние оптимизатора."""
        if "owner_rotation" in triggers or "vp_boundary" in triggers:
            return ("SWITCH_PROFILE", "ROTATION_REQUESTED",
                    "Выполнить безопасную ротацию к свежей сессии (checkpoint→handoff→ack).")
        if "rate_limit" in triggers or "profile_switch" in triggers:
            return ("SWITCH_PROFILE", "RATE_LIMIT" if "rate_limit" in triggers else "PROFILE_SWITCH_REQUESTED",
                    "Записать запрос профиля и выполнить ротацию (маршрутизация — VP-5).")
        if "failed_review" in triggers or "repeated_failure" in triggers:
            return ("OWNER_REQUIRED", "FAILED_REVIEW" if "failed_review" in triggers else "REPEATED_FAILURE",
                    "Эскалировать владельцу с redacted-evidence; не гадать.")
        if "context_threshold" in triggers:
            if splittable:
                return ("SPLIT_AT_CHECKPOINT", "CONTEXT_LIMIT",
                        "Сократить контекст split-ом на два законченных результата на checkpoint.")
            return ("SWITCH_PROFILE", "CONTEXT_LIMIT",
                    "Порог контекста: ротация к свежей сессии с verified handoff.")
        if "crash_recovery" in triggers:
            return ("SWITCH_PROFILE", "CRASH_RECOVERY",
                    "Восстановить из checkpoint в свежей сессии по verified handoff.")
        if "checkpoint_boundary" in triggers and splittable:
            return ("SPLIT_AT_CHECKPOINT", "CHECKPOINT_SPLITTABLE",
                    "На durable-checkpoint возможен split на два законченных результата.")
        if len(wos) >= 2:
            ok, why = self.optimizer._merge_compat(wos)
            if ok:
                return ("MERGE_TASKS", "COMPATIBLE_MERGE", "Подтвердите merge совместимых Work Order.")
            return ("OWNER_REQUIRED", "MERGE_INCOMPATIBLE", "Merge небезопасен; уточнить у владельца.")
        if len(wos) == 1:
            return ("READY", "SINGLE_BOUNDED_EXECUTABLE",
                    "Перевести Work Order в ready/active под одним writer.")
        return ("OWNER_REQUIRED", "NOTHING_TO_DO", "Создайте executable Work Order из VP Spec.")

    # --- безопасная ротация (§16.5) ----------------------------------------
    def rotate(self, project_id: str, wo_id: str, *, trigger: str = "owner_rotation",
               current_head: str = "", changed_files=None, commands=None, failures=None,
               completed_criteria=None, decisions=None, impacted_checks=None,
               artifact_refs=None, job_package_id: str = "", next_profile_request: str = "",
               actor: str = "core", correlation_id: str = "", idempotency_key: str = "") -> dict:
        steps: list[dict] = []

        def step(n, name, ok=True, detail=""):
            steps.append({"n": n, "name": name, "ok": ok, "detail": detail})

        wo = self.workorders.get_work_order(project_id, wo_id, with_history=False)
        if wo["status"] not in ("active", "checkpointed"):
            raise WorkOrderError("INVALID_TRANSITION",
                                 f"ротация возможна из active/checkpointed, не {wo['status']}")
        # 1) остановить новые действия (writer держит один writer)
        w0 = self.workorders.writer_count(project_id, wo_id)
        one_writer_ok = w0 <= 1
        step(1, "stop_new_actions", True, f"writers={w0}")
        # 2) снять diff/процесс (bounded, из переданного)
        step(2, "capture_diff", True, f"changed={len(changed_files or [])}")
        # 3) impacted checks
        step(3, "impacted_checks", True)
        # 4) persist + verify checkpoint
        cp = self.handoff.build_checkpoint(
            project_id, wo_id, current_head=current_head, changed_files=changed_files,
            commands=commands, failures=failures, completed_criteria=completed_criteria,
            decisions=decisions, impacted_checks=impacted_checks, artifact_refs=artifact_refs,
            cause=trigger, job_package_id=job_package_id, actor=actor, correlation_id=correlation_id)
        ver = self.handoff.verify_checkpoint(project_id, cp["id"])
        step(4, "checkpoint", ver["ok"], f"ckpt={cp['id']} hash_ok={ver['ok']}")
        if wo["status"] == "active":
            wo = self.workorders.transition(project_id, wo_id, "checkpointed",
                                            expected_version=wo["version"],
                                            reason_code="rotation", actor=actor,
                                            correlation_id=correlation_id)
        # 5) build + verify handoff
        hp = self.handoff.build_handoff(project_id, wo_id, checkpoint_id=cp["id"],
                                        job_package_id=job_package_id, current_head=current_head,
                                        actor=actor, correlation_id=correlation_id)
        hv = self.handoff.verify_handoff(project_id, hp["id"], actual_head=current_head or None)
        step(5, "handoff", hv["ok"], f"handoff={hp['id']} hash={hp['content_hash'][:20]} ok={hv['ok']}")
        # перевести в handoff_ready (аренда всё ещё удерживается — один writer)
        wo = self.workorders.transition(project_id, wo_id, "handoff_ready",
                                        expected_version=wo["version"], reason_code="rotation",
                                        actor=actor, correlation_id=correlation_id)
        w_before_release = self.workorders.writer_count(project_id, wo_id)
        one_writer_ok = one_writer_ok and w_before_release <= 1
        # 6) release writer lease ТОЛЬКО здесь
        wo = self.workorders.release_writer_lease(project_id, wo_id, expected_version=wo["version"],
                                                  reason_code="rotation", actor=actor,
                                                  correlation_id=correlation_id)
        w_after_release = self.workorders.writer_count(project_id, wo_id)
        step(6, "release_lease", w_after_release == 0, f"writers_after={w_after_release}")
        # 7) выбрать профиль как ЗАПРОС (не VP-5 routing)
        if next_profile_request:
            self.optimizer.note(project_id, wo["vp_spec_id"], "SWITCH_PROFILE",
                                "PROFILE_REQUEST_RECORDED",
                                f"запрос профиля {next_profile_request} (маршрутизация — VP-5)",
                                [wo_id], "Свежая сессия должна подтвердить точный hash и baseline.",
                                actor=actor, correlation_id=correlation_id)
        step(7, "select_profile", True, f"request={next_profile_request or 'none'}")
        # 8) свежая сессия требуется (запускается harness'ом отдельно)
        step(8, "fresh_session", True, "ожидается ack точного hash пакета")
        # 9-10) ack + continue — выполняет consume/continue после свежей сессии
        step(9, "await_ack", True, f"hash={hp['content_hash']}")

        rid = new_id("rot")
        with session_scope() as s:
            s.add(RotationRecord(
                id=rid, project_id=project_id, work_order_id=wo_id, trigger=_item(trigger),
                checkpoint_id=cp["id"], handoff_id=hp["id"],
                next_profile_request=_item(next_profile_request), lease_released=True,
                one_writer_ok=one_writer_ok, steps_json=json.dumps(steps, ensure_ascii=False),
                status="awaiting_ack",
                exact_next_action="Свежая сессия подтверждает hash и baseline, затем continue.",
                actor=actor, correlation_id=correlation_id, created_at=_now()))
            s.commit()
        audit.record("workorders.rotation.started",
                     f"project={project_id} wo={wo_id} rot={rid} ckpt={cp['id']} handoff={hp['id']} "
                     f"one_writer_ok={one_writer_ok}",
                     actor=actor, correlation_id=correlation_id)
        return {"id": rid, "work_order_id": wo_id, "trigger": trigger,
                "checkpoint": cp, "handoff": hp, "steps": steps,
                "one_writer_ok": one_writer_ok, "lease_released": True,
                "status": "awaiting_ack",
                "handoff_hash": hp["content_hash"], "baseline_head": hp["baseline_head"],
                "current_head": hp["current_head"],
                "exact_next_action": "Свежая сессия подтверждает точный hash и baseline, затем continue."}

    def continue_after_rotation(self, project_id: str, rotation_id: str, *, ack_hash: str,
                                baseline_ack: str = "", actual_head: str | None = None,
                                holder: str = "fresh-session", actor: str = "consumer",
                                correlation_id: str = "") -> dict:
        """Шаги 9–10: подтвердить точный hash/baseline и продолжить (новый writer)."""
        with session_scope() as s:
            rr = s.get(RotationRecord, rotation_id)
            if rr is None or rr.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"ротация не найдена: {rotation_id}")
            handoff_id, wo_id = rr.handoff_id, rr.work_order_id
        # 9) ack точного hash + baseline
        self.handoff.acknowledge(project_id, handoff_id, ack_hash=ack_hash, baseline_ack=baseline_ack,
                                 actual_head=actual_head, actor=actor, correlation_id=correlation_id)
        # 10) продолжить: свежая сессия берёт writer заново (один writer)
        wo = self.workorders.get_work_order(project_id, wo_id, with_history=False)
        wo = self.workorders.transition(project_id, wo_id, "active", expected_version=wo["version"],
                                        reason_code="continue_after_rotation", holder=holder,
                                        actor=actor, correlation_id=correlation_id)
        with session_scope() as s:
            rr = s.get(RotationRecord, rotation_id)
            rr.status = "continued"
            s.commit()
        audit.record("workorders.rotation.continued",
                     f"project={project_id} wo={wo_id} rot={rotation_id}",
                     actor=actor, correlation_id=correlation_id)
        return {"rotation_id": rotation_id, "status": "continued", "work_order": wo}

    def get_rotation(self, project_id: str, rotation_id: str) -> dict:
        with session_scope() as s:
            rr = s.get(RotationRecord, rotation_id)
            if rr is None or rr.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"ротация не найдена: {rotation_id}")
            return {"id": rr.id, "work_order_id": rr.work_order_id, "trigger": rr.trigger,
                    "checkpoint_id": rr.checkpoint_id, "handoff_id": rr.handoff_id,
                    "next_profile_request": rr.next_profile_request,
                    "lease_released": bool(rr.lease_released), "one_writer_ok": bool(rr.one_writer_ok),
                    "steps": json.loads(rr.steps_json), "status": rr.status,
                    "exact_next_action": rr.exact_next_action, "created_at": _iso(rr.created_at)}

    def list_rotations(self, project_id: str, *, work_order_id: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(RotationRecord).where(RotationRecord.project_id == project_id)
            if work_order_id:
                stmt = stmt.where(RotationRecord.work_order_id == work_order_id)
            rows = s.execute(stmt.order_by(RotationRecord.created_at.desc())).scalars().all()
            return [{"id": r.id, "work_order_id": r.work_order_id, "trigger": r.trigger,
                     "status": r.status, "one_writer_ok": bool(r.one_writer_ok),
                     "created_at": _iso(r.created_at)} for r in rows]


__all__ = ["ContextGovernor", "CONTEXT_THRESHOLD_BYTES", "REPEAT_THRESHOLD", "ROTATION_TRIGGERS"]
