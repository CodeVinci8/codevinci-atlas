"""VP-4 Checkpoint и HandoffPackage — построение, верификация, ack/reject
(Master Spec §16.4, §16.5, §37 Phase G).

Checkpoint durable и hash-verifiable (переживает рестарт Core). HandoffPackage
immutable, содержит все обязательные поля и детерминированный content-hash, без
credentials и полного чата. Свежая сессия сверяет пакет с фактическим Git/DB:
фактическое состояние побеждает, устаревший/подделанный/несовпадающий handoff
отклоняется стабильным кодом (HASH_MISMATCH, HANDOFF_STALE, SCOPE_DRIFT,
SOURCE_STALE, PROJECT_NOT_AVAILABLE, CAPABILITY_DENIED).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import (
    HandoffAck,
    HandoffPackage,
    IdempotencyKey,
    Project,
    VpSpec,
    WoCheckpoint,
    WorkOrder,
)
from .productmap import canonical_json, content_hash
from .redaction import SECRET_MARKER, contains_secret
from .workorders import (
    CAPABILITY_ALLOWLIST,
    PROHIBITED_CAPABILITIES,
    WorkOrderError,
    _item,
    _now,
    _str_list,
)

HANDOFF_SCHEMA_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = 1
# Обязательные поля HandoffPackage (§16.4).
HANDOFF_REQUIRED_FIELDS = (
    "schema_version", "ids", "goal", "finished_result", "immutable_constraints",
    "source_of_truth", "baseline_head", "current_head", "changed_files", "commands",
    "failures", "acceptance_matrix", "decisions", "exact_next_action",
    "prohibited_actions", "artifact_refs", "capabilities", "binding",
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _guard(*blobs: str) -> None:
    for b in blobs:
        if isinstance(b, str) and (SECRET_MARKER in b or contains_secret(b)):
            raise WorkOrderError("WO_INVALID", "содержимое содержит секрет — отклонено")


class HandoffService:
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

    # --- checkpoint --------------------------------------------------------
    def build_checkpoint(self, project_id: str, wo_id: str, *, current_head: str = "",
                         changed_files=None, commands=None, failures=None,
                         completed_criteria=None, decisions=None, impacted_checks=None,
                         artifact_refs=None, cause: str = "checkpoint",
                         exact_next_action: str = "", job_package_id: str = "",
                         actor: str = "core", correlation_id: str = "",
                         idempotency_key: str = "") -> dict:
        with session_scope() as s:
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self._checkpoint_dict(s.get(WoCheckpoint, seen))
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            wc = json.loads(wo.content_json)
            all_crit = wc.get("acceptance_criteria", [])
            completed = _str_list(completed_criteria)
            remaining = [c["id"] for c in all_crit if c["id"] not in set(completed)]
            impacted = _str_list(impacted_checks) or [c.get("id") for c in wc.get("required_checks", [])]
            lease_state = {"lease_active": bool(wo.lease_id), "holder": wo.writer_holder,
                           "lease_key": f"wt:{wo.project_id}:{wo.baseline_branch or wo.vp_key}"}
            body = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "project_id": project_id, "work_order_id": wo_id, "vp_key": wo.vp_key,
                "job_package_id": job_package_id,
                "baseline_head": wo.baseline_head, "current_head": _item(current_head),
                "changed_files": _str_list(changed_files),
                "commands": _bounded_dicts(commands),
                "failures": _bounded_dicts(failures),
                "completed_criteria": completed, "remaining_criteria": remaining,
                "decisions": _str_list(decisions), "impacted_checks": impacted,
                "artifact_refs": _str_list(artifact_refs),
                "lease_state": lease_state, "writer_holder": wo.writer_holder,
                "exact_next_action": _item(exact_next_action) or wc.get("exact_next_action", ""),
                "cause": _item(cause),
            }
            _guard(canonical_json(body))
            ch = content_hash(body)
            cid = new_id("ckpt")
            s.add(WoCheckpoint(
                id=cid, project_id=project_id, work_order_id=wo_id,
                job_package_id=job_package_id, vp_key=wo.vp_key,
                baseline_head=wo.baseline_head, current_head=body["current_head"],
                changed_files_json=canonical_json(body["changed_files"]),
                commands_json=canonical_json(body["commands"]),
                failures_json=canonical_json(body["failures"]),
                completed_criteria_json=canonical_json(completed),
                remaining_criteria_json=canonical_json(remaining),
                decisions_json=canonical_json(body["decisions"]),
                impacted_checks_json=canonical_json(impacted),
                artifact_refs_json=canonical_json(body["artifact_refs"]),
                lease_state_json=canonical_json(lease_state), writer_holder=wo.writer_holder,
                exact_next_action=body["exact_next_action"], cause=body["cause"],
                content_hash=ch, actor=actor, correlation_id=correlation_id, created_at=_now()))
            self._idem_store(s, idempotency_key, "checkpoint.build", project_id, cid)
            s.commit()
            out = self._checkpoint_dict(s.get(WoCheckpoint, cid))
        audit.record("workorders.checkpoint.built",
                     f"project={project_id} wo={wo_id} ckpt={cid} hash={ch}",
                     actor=actor, correlation_id=correlation_id)
        return out

    def _checkpoint_body(self, cp: WoCheckpoint) -> dict:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "project_id": cp.project_id, "work_order_id": cp.work_order_id, "vp_key": cp.vp_key,
            "job_package_id": cp.job_package_id,
            "baseline_head": cp.baseline_head, "current_head": cp.current_head,
            "changed_files": json.loads(cp.changed_files_json),
            "commands": json.loads(cp.commands_json),
            "failures": json.loads(cp.failures_json),
            "completed_criteria": json.loads(cp.completed_criteria_json),
            "remaining_criteria": json.loads(cp.remaining_criteria_json),
            "decisions": json.loads(cp.decisions_json),
            "impacted_checks": json.loads(cp.impacted_checks_json),
            "artifact_refs": json.loads(cp.artifact_refs_json),
            "lease_state": json.loads(cp.lease_state_json), "writer_holder": cp.writer_holder,
            "exact_next_action": cp.exact_next_action, "cause": cp.cause,
        }

    def _checkpoint_dict(self, cp: WoCheckpoint) -> dict:
        body = self._checkpoint_body(cp)
        return {"id": cp.id, "content_hash": cp.content_hash, "created_at": _iso(cp.created_at),
                **body}

    def verify_checkpoint(self, project_id: str, checkpoint_id: str) -> dict:
        with session_scope() as s:
            cp = s.get(WoCheckpoint, checkpoint_id)
            if cp is None or cp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"checkpoint не найден: {checkpoint_id}")
            recomputed = content_hash(self._checkpoint_body(cp))
            ok = recomputed == cp.content_hash
            return {"checkpoint_id": checkpoint_id, "ok": ok,
                    "stored_hash": cp.content_hash, "recomputed_hash": recomputed}

    def get_checkpoint(self, project_id: str, checkpoint_id: str) -> dict:
        with session_scope() as s:
            cp = s.get(WoCheckpoint, checkpoint_id)
            if cp is None or cp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"checkpoint не найден: {checkpoint_id}")
            return self._checkpoint_dict(cp)

    def list_checkpoints(self, project_id: str, *, work_order_id: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(WoCheckpoint).where(WoCheckpoint.project_id == project_id)
            if work_order_id:
                stmt = stmt.where(WoCheckpoint.work_order_id == work_order_id)
            rows = s.execute(stmt.order_by(WoCheckpoint.created_at.desc())).scalars().all()
            return [{"id": c.id, "work_order_id": c.work_order_id, "content_hash": c.content_hash,
                     "current_head": c.current_head, "cause": c.cause,
                     "created_at": _iso(c.created_at)} for c in rows]

    # --- handoff -----------------------------------------------------------
    def build_handoff(self, project_id: str, wo_id: str, *, checkpoint_id: str,
                      job_package_id: str = "", current_head: str = "",
                      actor: str = "core", correlation_id: str = "",
                      idempotency_key: str = "", allow_compact: bool = True) -> dict:
        from .context_engine import MAX_PACKAGE_BYTES, compact_content
        with session_scope() as s:
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self._handoff_dict(s.get(HandoffPackage, seen))
            wo = s.get(WorkOrder, wo_id)
            if wo is None or wo.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"Work Order не найден: {wo_id}")
            cp = s.get(WoCheckpoint, checkpoint_id)
            if cp is None or cp.work_order_id != wo_id:
                raise WorkOrderError("NOT_FOUND", "checkpoint не найден для этого Work Order")
            spec = s.get(VpSpec, wo.vp_spec_id)
            spec_content = json.loads(spec.content_json) if spec else {}
            wc = json.loads(wo.content_json)
            completed = set(json.loads(cp.completed_criteria_json))
            matrix = [{"id": c["id"], "text": c["text"], "required": bool(c.get("required")),
                       "shared": bool(c.get("shared")),
                       "status": "completed" if c["id"] in completed else "remaining"}
                      for c in wc.get("acceptance_criteria", [])]
            content = {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "ids": {"project_id": project_id, "vp_key": wo.vp_key,
                        "vp_spec_id": wo.vp_spec_id, "work_order_id": wo_id,
                        "job_package_id": job_package_id or cp.job_package_id,
                        "checkpoint_id": checkpoint_id, "correlation_id": correlation_id},
                "role": wo.role,
                "goal": wo.goal,
                "finished_result": spec_content.get("result", ""),
                "immutable_constraints": wc.get("immutable_constraints", []),
                "source_of_truth": wc.get("source_of_truth", []),
                "baseline_head": wo.baseline_head,
                "current_head": _item(current_head) or cp.current_head,
                "changed_files": json.loads(cp.changed_files_json),
                "commands": json.loads(cp.commands_json),
                "failures": json.loads(cp.failures_json),
                "acceptance_matrix": matrix,
                "decisions": json.loads(cp.decisions_json),
                "exact_next_action": cp.exact_next_action or wc.get("exact_next_action", ""),
                "prohibited_actions": wc.get("prohibited_actions", []),
                "artifact_refs": json.loads(cp.artifact_refs_json),
                "capabilities": wc.get("capabilities", []),
                "binding": {"spec_version": wo.spec_version, "spec_hash": wo.spec_hash,
                            "brief_hash": wo.brief_hash, "map_hash": wo.map_hash,
                            "envelope_hash": wo.envelope_hash, "approval_id": wo.approval_id},
            }
            missing = [k for k in HANDOFF_REQUIRED_FIELDS if k not in content]
            if missing:
                raise WorkOrderError("WO_INVALID", f"handoff без обязательных полей: {missing}")
            _guard(canonical_json(content))
            compacted = False
            if len(canonical_json(content).encode("utf-8")) > MAX_PACKAGE_BYTES:
                if not allow_compact:
                    raise WorkOrderError("CONTEXT_LIMIT", "HandoffPackage превысил предел")
                content = _compact_handoff(content, compact_content)
                compacted = True
                if len(canonical_json(content).encode("utf-8")) > MAX_PACKAGE_BYTES:
                    raise WorkOrderError("CONTEXT_LIMIT",
                                         "compact не сохранил обязательные поля в пределах бюджета")
            ch = content_hash(content)
            hid = new_id("hand")
            s.add(HandoffPackage(
                id=hid, project_id=project_id, vp_key=wo.vp_key, vp_spec_id=wo.vp_spec_id,
                work_order_id=wo_id, job_package_id=job_package_id or cp.job_package_id,
                checkpoint_id=checkpoint_id, schema_version=HANDOFF_SCHEMA_VERSION,
                content_json=canonical_json(content), content_hash=ch,
                baseline_head=wo.baseline_head, current_head=content["current_head"],
                spec_version=wo.spec_version, brief_hash=wo.brief_hash, map_hash=wo.map_hash,
                approval_id=wo.approval_id, status="issued", compact=compacted,
                actor=actor, correlation_id=correlation_id, created_at=_now()))
            self._idem_store(s, idempotency_key, "handoff.build", project_id, hid)
            s.commit()
            out = self._handoff_dict(s.get(HandoffPackage, hid))
        audit.record("workorders.handoff.built",
                     f"project={project_id} wo={wo_id} handoff={hid} hash={ch} compact={compacted}",
                     actor=actor, correlation_id=correlation_id)
        return out

    def _handoff_dict(self, hp: HandoffPackage) -> dict:
        return {"id": hp.id, "project_id": hp.project_id, "vp_key": hp.vp_key,
                "work_order_id": hp.work_order_id, "checkpoint_id": hp.checkpoint_id,
                "job_package_id": hp.job_package_id, "schema_version": hp.schema_version,
                "content_hash": hp.content_hash, "baseline_head": hp.baseline_head,
                "current_head": hp.current_head, "status": hp.status, "compact": bool(hp.compact),
                "content": json.loads(hp.content_json), "created_at": _iso(hp.created_at)}

    def get_handoff(self, project_id: str, handoff_id: str) -> dict:
        with session_scope() as s:
            hp = s.get(HandoffPackage, handoff_id)
            if hp is None or hp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"handoff не найден: {handoff_id}")
            return self._handoff_dict(hp)

    def list_handoffs(self, project_id: str, *, work_order_id: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(HandoffPackage).where(HandoffPackage.project_id == project_id)
            if work_order_id:
                stmt = stmt.where(HandoffPackage.work_order_id == work_order_id)
            rows = s.execute(stmt.order_by(HandoffPackage.created_at.desc())).scalars().all()
            return [{"id": h.id, "work_order_id": h.work_order_id, "content_hash": h.content_hash,
                     "status": h.status, "compact": bool(h.compact),
                     "created_at": _iso(h.created_at)} for h in rows]

    # --- верификация stale/tamper ------------------------------------------
    def verify_handoff(self, project_id: str, handoff_id: str, *, actual_head: str | None = None) -> dict:
        """Сверить handoff с фактическим Git/DB. Фактическое побеждает (§16.4)."""
        rejections: list[dict] = []
        with session_scope() as s:
            hp = s.get(HandoffPackage, handoff_id)
            if hp is None or hp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"handoff не найден: {handoff_id}")
            content = json.loads(hp.content_json)
            # 1) целостность хеша (подделка)
            if content_hash(content) != hp.content_hash:
                rejections.append({"code": "HASH_MISMATCH", "reason": "content-hash не совпадает"})
            # 2) обязательные поля
            missing = [k for k in HANDOFF_REQUIRED_FIELDS if k not in content]
            if missing:
                rejections.append({"code": "WO_INVALID", "reason": f"нет полей: {missing}"})
            # 3) проект доступен
            p = s.get(Project, project_id)
            if p is None or p.status != "connected":
                rejections.append({"code": "PROJECT_NOT_AVAILABLE", "reason": "проект недоступен"})
            # 4) Work Order + версия spec актуальны
            wo = s.get(WorkOrder, hp.work_order_id)
            if wo is None:
                rejections.append({"code": "SOURCE_STALE", "reason": "Work Order исчез"})
            else:
                if wo.spec_version != hp.spec_version:
                    rejections.append({"code": "HANDOFF_STALE",
                                       "reason": f"версия VP Spec устарела ({hp.spec_version}≠{wo.spec_version})"})
                if wo.brief_hash != hp.brief_hash or wo.map_hash != hp.map_hash:
                    rejections.append({"code": "SCOPE_DRIFT", "reason": "Brief/Map hash разошёлся"})
                if wo.approval_id != hp.approval_id:
                    rejections.append({"code": "SOURCE_STALE", "reason": "approval изменился"})
                # role/capabilities не превышают Work Order
                wc = json.loads(wo.content_json)
                extra = set(content.get("capabilities", [])) - set(wc.get("capabilities", []))
                if extra:
                    rejections.append({"code": "CAPABILITY_DENIED",
                                       "reason": f"capabilities сверх Work Order: {sorted(extra)}"})
            for cap in content.get("capabilities", []):
                if cap in PROHIBITED_CAPABILITIES or cap not in CAPABILITY_ALLOWLIST:
                    rejections.append({"code": "CAPABILITY_DENIED", "reason": f"недопустимая capability: {cap}"})
            # 5) checkpoint известен и не заменён
            cp = s.get(WoCheckpoint, hp.checkpoint_id)
            if cp is None:
                rejections.append({"code": "HANDOFF_STALE", "reason": "checkpoint неизвестен"})
            # 6) согласованность baseline/current HEAD с фактом (факт побеждает)
            eff_head = actual_head
            if actual_head is not None and content.get("current_head") not in ("", actual_head):
                rejections.append({"code": "HANDOFF_STALE",
                                   "reason": f"current HEAD в handoff ({content.get('current_head')}) ≠ факт ({actual_head})"})
            # статус
            if hp.status in ("rejected", "superseded"):
                rejections.append({"code": "HANDOFF_STALE", "reason": f"handoff {hp.status}"})
            return {"handoff_id": handoff_id, "ok": not rejections, "rejections": rejections,
                    "effective_head": eff_head, "content_hash": hp.content_hash}

    # --- ack / reject ------------------------------------------------------
    def acknowledge(self, project_id: str, handoff_id: str, *, ack_hash: str,
                    baseline_ack: str = "", actual_head: str | None = None,
                    actor: str = "consumer", correlation_id: str = "") -> dict:
        verify = self.verify_handoff(project_id, handoff_id, actual_head=actual_head)
        with session_scope() as s:
            hp = s.get(HandoffPackage, handoff_id)
            if hp is None or hp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"handoff не найден: {handoff_id}")
            if ack_hash != hp.content_hash:
                self._record_ack(s, handoff_id, project_id, "REJECT", "HASH_MISMATCH",
                                 ack_hash, baseline_ack, "ack-hash не совпадает", actor, correlation_id)
                s.commit()
                raise WorkOrderError("HASH_MISMATCH", "подтверждён неверный content-hash")
            if not verify["ok"]:
                code = verify["rejections"][0]["code"]
                self._record_ack(s, handoff_id, project_id, "REJECT", code, ack_hash,
                                 baseline_ack, verify["rejections"][0]["reason"], actor, correlation_id)
                s.execute(_upd_handoff(handoff_id, "rejected"))
                s.commit()
                raise WorkOrderError(code if code in WorkOrderError._HTTP else "HANDOFF_STALE",
                                     verify["rejections"][0]["reason"])
            self._record_ack(s, handoff_id, project_id, "ACK", "", ack_hash, baseline_ack,
                             "acknowledged", actor, correlation_id)
            s.execute(_upd_handoff(handoff_id, "acknowledged"))
            s.commit()
        audit.record("workorders.handoff.acknowledged",
                     f"project={project_id} handoff={handoff_id} hash={ack_hash}",
                     actor=actor, correlation_id=correlation_id)
        return {"handoff_id": handoff_id, "result": "ACK", "content_hash": ack_hash}

    def reject(self, project_id: str, handoff_id: str, *, reason_code: str = "OWNER_REQUIRED",
               note: str = "", actor: str = "consumer", correlation_id: str = "") -> dict:
        with session_scope() as s:
            hp = s.get(HandoffPackage, handoff_id)
            if hp is None or hp.project_id != project_id:
                raise WorkOrderError("NOT_FOUND", f"handoff не найден: {handoff_id}")
            self._record_ack(s, handoff_id, project_id, "REJECT", reason_code, "", "",
                             note, actor, correlation_id)
            s.execute(_upd_handoff(handoff_id, "rejected"))
            s.commit()
        audit.record("workorders.handoff.rejected",
                     f"project={project_id} handoff={handoff_id} reason={reason_code}",
                     actor=actor, correlation_id=correlation_id)
        return {"handoff_id": handoff_id, "result": "REJECT", "reason_code": reason_code}

    def _record_ack(self, s, handoff_id, project_id, result, reason_code, ack_hash,
                    baseline_ack, note, actor, correlation_id) -> None:
        s.add(HandoffAck(id=new_id("hack"), handoff_id=handoff_id, project_id=project_id,
                         result=result, reason_code=reason_code, ack_hash=ack_hash,
                         baseline_ack=baseline_ack, note=_item(note), actor=actor,
                         correlation_id=correlation_id, created_at=_now()))

    def list_acks(self, project_id: str, handoff_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(HandoffAck).where(
                HandoffAck.project_id == project_id, HandoffAck.handoff_id == handoff_id)
                .order_by(HandoffAck.created_at.asc())).scalars().all()
            return [{"id": a.id, "result": a.result, "reason_code": a.reason_code,
                     "ack_hash": a.ack_hash, "baseline_ack": a.baseline_ack,
                     "note": a.note, "created_at": _iso(a.created_at)} for a in rows]


def _upd_handoff(handoff_id: str, status: str):
    from sqlalchemy import update
    return update(HandoffPackage).where(HandoffPackage.id == handoff_id).values(status=status)


def _bounded_dicts(items, limit: int = 40) -> list[dict]:
    out = []
    for x in list(items or [])[:limit]:
        if isinstance(x, dict):
            out.append({k: _item(str(v)) for k, v in list(x.items())[:8]})
        else:
            out.append({"value": _item(str(x))})
    return out


def _compact_handoff(content: dict, compact_content) -> dict:
    """Compact handoff, сохраняя ВСЕ обязательные поля и критерии (§37 Phase H)."""
    out = dict(content)
    # убрать дубли/длинную прозу из необязательных секций
    if "commands" in out:
        out["commands"] = [{"cmd": c.get("cmd", c.get("value", "")), "outcome": c.get("outcome", "")}
                           for c in out["commands"]][:20]
    if "source_of_truth" in out:
        out["source_of_truth"] = out["source_of_truth"][:8]
    if "changed_files" in out:
        out["changed_files"] = out["changed_files"][:40]
    out["_compacted"] = True
    return out


__all__ = ["HandoffService", "HANDOFF_SCHEMA_VERSION", "CHECKPOINT_SCHEMA_VERSION",
           "HANDOFF_REQUIRED_FIELDS"]
