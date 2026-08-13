"""VP-5 Runs — durable-сервис жизненного цикла запуска Agent Pipeline
(Master Spec §17.4, §25, §30.2).

Идемпотентное создание Run (dedup_key + partial-unique), типизированные атомарные
переходы с оптимистичной ``version`` и стабильными конфликтами, упорядоченные
нормализованные события (переживают свежий процесс Core), persist router-решений
с requested/effective и reason_code, ссылки на provider-сессии БЕЗ transcript/
credentials, bounded-ретраи с классификацией, pause/interruption и handoff-связи.
Каждое материальное решение/переход — append-only Audit (§31). Секреты в durable
не попадают (redaction-guard, §30).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import (
    HandoffLink,
    Project,
    ProviderSession,
    Run,
    RunEvent,
    RunPause,
    RunRetry,
    RunRoleStep,
)
from .orm import (
    RouterDecision as RouterDecisionRow,
)
from .orm import (
    RunLease as RunLeaseRow,
)
from .redaction import contains_secret, redact

# Явный жизненный цикл (§17.4). Невалидный переход → INVALID_TRANSITION.
VALID_RUN_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"RUNNING", "RATE_LIMITED", "AUTH_REQUIRED", "PAUSED",
                  "INTERRUPTED", "FAILED", "CANCELLED", "OWNER_REQUIRED"},
    "RUNNING": {"COLLECTING", "RATE_LIMITED", "AUTH_REQUIRED", "PAUSED",
                "INTERRUPTED", "FAILED", "CANCELLED", "OWNER_REQUIRED"},
    # RUNNING/PREPARING допустимы из COLLECTING для одного fix-loop после REVISE.
    "COLLECTING": {"SUCCEEDED", "RUNNING", "PREPARING", "FAILED", "INTERRUPTED",
                   "CANCELLED", "OWNER_REQUIRED"},
    "RATE_LIMITED": {"PREPARING", "RUNNING", "OWNER_REQUIRED", "FAILED", "CANCELLED"},
    "AUTH_REQUIRED": {"PREPARING", "OWNER_REQUIRED", "FAILED", "CANCELLED"},
    "PAUSED": {"RUNNING", "PREPARING", "OWNER_REQUIRED", "CANCELLED"},
    "INTERRUPTED": {"PREPARING", "RUNNING", "OWNER_REQUIRED", "FAILED", "CANCELLED"},
    "OWNER_REQUIRED": {"PREPARING", "RUNNING", "CANCELLED", "FAILED"},
    "SUCCEEDED": set(),  # терминальный
    "FAILED": set(),     # терминальный
    "CANCELLED": set(),  # терминальный
}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
ROLES = ("planner", "builder", "reviewer")


class RunError(Exception):
    """Ошибка VP-5 со стабильным кодом и HTTP-статусом."""

    _HTTP = {
        "VERSION_CONFLICT": 409, "INVALID_TRANSITION": 409, "RUN_CONFLICT": 409,
        "NO_ELIGIBLE_PROFILE": 409, "SILENT_FALLBACK_FORBIDDEN": 409,
        "OWNER_REQUIRED": 409, "AUTH_REQUIRED": 409, "RATE_LIMITED": 409,
        "SECOND_FIX_BLOCKED": 409, "PROJECT_NOT_AVAILABLE": 409,
        "REVIEWER_NOT_INDEPENDENT": 409, "SECRET_LEAK": 422, "NOT_FOUND": 404,
        "EMERGENCY_STOP": 409,
    }

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = redact(reason)
        self.http = self._HTTP.get(code, 400)
        super().__init__(f"{code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guard(*values: str) -> None:
    for v in values:
        if v and contains_secret(str(v)):
            raise RunError("SECRET_LEAK", "секрет запрещён в durable Run-состоянии")


class RunService:
    # --- создание (идемпотентно) -------------------------------------------
    def create_run(self, project_id: str, *, work_order_id: str = "", vp_key: str = "",
                   correlation_id: str = "", preset: str = "", owner_override: dict | None = None,
                   dedup_key: str = "", idempotency_key: str = "", actor: str = "owner",
                   allow_emergency: bool = False) -> dict:
        # VP-7 (§19): Emergency Stop немедленно запрещает НОВЫЕ jobs. Проверяем на
        # основном пути создания Run (кроме явных внутренних вызовов recovery).
        if not allow_emergency:
            from . import emergency
            if emergency.blocks_new_jobs():
                raise RunError("EMERGENCY_STOP",
                               "Emergency Stop активен: новые jobs запрещены до явного owner-resume")
        key = (idempotency_key or dedup_key or "")[:120]
        override_json = json.dumps(owner_override or {}, ensure_ascii=False, sort_keys=True)
        _guard(work_order_id, vp_key, override_json, key)
        with session_scope() as s:
            self._require_available(s, project_id)
            if key:
                existing = s.execute(select(Run).where(
                    Run.dedup_key == key, Run.project_id == project_id)).scalars().first()
                if existing:
                    return existing.to_dict()
            rid = new_id("run")
            s.add(Run(id=rid, project_id=project_id, work_order_id=work_order_id, vp_key=vp_key,
                      correlation_id=correlation_id, state="QUEUED", preset=preset,
                      owner_override_json=override_json, dedup_key=key, version=1))
            try:
                s.commit()
            except Exception:  # noqa: BLE001 — гонка по dedup_key
                s.rollback()
                if key:
                    existing = s.execute(select(Run).where(
                        Run.dedup_key == key, Run.project_id == project_id)).scalars().first()
                    if existing:
                        return existing.to_dict()
                raise RunError("RUN_CONFLICT", "конкурентное создание Run")
            row = s.get(Run, rid)
            out = row.to_dict()
        self._event(rid, "run.created", {"project_id": project_id, "work_order_id": work_order_id},
                    project_id=project_id)
        audit.record("runs.run.created", f"run={rid} project={project_id} wo={work_order_id}",
                     actor=actor, correlation_id=correlation_id)
        return out

    def _require_available(self, s, project_id: str) -> Project:
        p = s.get(Project, project_id)
        if p is None:
            raise RunError("NOT_FOUND", f"проект не найден: {project_id}")
        if p.status not in ("connected", "ready"):
            raise RunError("PROJECT_NOT_AVAILABLE", f"проект недоступен: {p.status}")
        return p

    def get_run(self, run_id: str) -> dict:
        with session_scope() as s:
            row = s.get(Run, run_id)
            if row is None:
                raise RunError("NOT_FOUND", f"run не найден: {run_id}")
            d = row.to_dict()
            d["role_steps"] = [x.to_dict() for x in s.execute(
                select(RunRoleStep).where(RunRoleStep.run_id == run_id)
                .order_by(RunRoleStep.seq)).scalars().all()]
            d["events_count"] = int(s.execute(select(func.count()).select_from(RunEvent)
                                              .where(RunEvent.run_id == run_id)).scalar_one())
            d["active_lease"] = self._active_lease_summary(s, run_id)
            return d

    def _active_lease_summary(self, s, run_id: str) -> list[dict]:
        rows = s.execute(select(RunLeaseRow).where(
            RunLeaseRow.run_id == run_id, RunLeaseRow.released_at == "")).scalars().all()
        return [{"profile_id": r.profile_id, "role": r.role, "worktree": r.worktree} for r in rows]

    def list_runs(self, *, project_id: str | None = None, state: str | None = None,
                  limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 200))
        with session_scope() as s:
            stmt = select(Run).order_by(Run.created_at.desc(), Run.id.desc())
            if project_id:
                stmt = stmt.where(Run.project_id == project_id)
            if state:
                stmt = stmt.where(Run.state == state)
            return [r.to_dict() for r in s.execute(stmt.limit(limit)).scalars().all()]

    # --- переходы (атомарно + optimistic version) --------------------------
    def transition(self, run_id: str, to_state: str, *, expected_version: int,
                   reason: str = "", actor: str = "core", correlation_id: str = "",
                   next_action: str = "", blocker: str = "", failure_class: str = "") -> dict:
        _guard(reason, next_action, blocker)
        with session_scope() as s:
            row = s.get(Run, run_id)
            if row is None:
                raise RunError("NOT_FOUND", f"run не найден: {run_id}")
            frm = row.state
            if to_state != frm and to_state not in VALID_RUN_TRANSITIONS.get(frm, set()):
                raise RunError("INVALID_TRANSITION", f"{frm} → {to_state} запрещён")
            res = s.execute(update(Run).where(
                Run.id == run_id, Run.version == expected_version).values(
                    state=to_state, version=Run.version + 1,
                    next_action=next_action or row.next_action,
                    blocker=blocker, failure_class=failure_class or row.failure_class,
                    updated_at=_now()))
            if res.rowcount != 1:
                raise RunError("VERSION_CONFLICT", "конкурентное изменение Run")
            s.commit()
            s.refresh(row)
            out = row.to_dict()
        self._event(run_id, "run.transition",
                    {"from": frm, "to": to_state, "reason": reason}, project_id=out["project_id"])
        audit.record("runs.run.transition", f"run={run_id} {frm}->{to_state} reason={reason}",
                     actor=actor, correlation_id=correlation_id)
        return out

    # --- нормализованные события (durable, упорядоченные) -------------------
    def append_event(self, run_id: str, event_type: str, payload: dict | None = None, *,
                     project_id: str = "") -> dict:
        return self._event(run_id, event_type, payload or {}, project_id=project_id)

    def _event(self, run_id: str, event_type: str, payload: dict, *, project_id: str = "") -> dict:
        body = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        if contains_secret(body):
            body = redact(body)
        eid = new_id("rev")
        with session_scope() as s:
            nxt = int(s.execute(select(func.coalesce(func.max(RunEvent.seq), 0)).where(
                RunEvent.run_id == run_id)).scalar_one()) + 1
            s.add(RunEvent(id=eid, run_id=run_id, project_id=project_id, seq=nxt,
                           event_type=event_type, payload_json=body, schema_version=1))
            s.commit()
            row = s.get(RunEvent, eid)
            return row.to_dict()

    def events(self, run_id: str, *, after_seq: int = 0, limit: int = 500) -> list[dict]:
        limit = max(1, min(limit, 2000))
        with session_scope() as s:
            rows = s.execute(select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                .order_by(RunEvent.seq).limit(limit)).scalars().all()
            return [r.to_dict() for r in rows]

    # --- router-решение (requested/effective + reason) ---------------------
    def record_router_decision(self, run_id: str, decision) -> str:
        d = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
        _guard(d.get("effective_profile", ""), d.get("requested_profile", ""))
        did = new_id("rtr")
        with session_scope() as s:
            s.add(RouterDecisionRow(
                id=did, run_id=run_id, role=d.get("role", ""),
                requested_model=d.get("requested_model", ""), requested_profile=d.get("requested_profile", ""),
                effective_model=d.get("effective_model", ""), effective_profile=d.get("effective_profile", ""),
                reason_code=d.get("reason_code", ""),
                candidates_json=json.dumps(d.get("candidates", []), ensure_ascii=False)))
            s.commit()
        self._event(run_id, "router.decided", {
            "role": d.get("role", ""), "requested_profile": d.get("requested_profile", ""),
            "effective_profile": d.get("effective_profile", ""),
            "requested_model": d.get("requested_model", ""), "effective_model": d.get("effective_model", ""),
            "reason_code": d.get("reason_code", "")})
        return did

    def router_decisions(self, run_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(RouterDecisionRow).where(
                RouterDecisionRow.run_id == run_id).order_by(RouterDecisionRow.decided_at)).scalars().all()
            return [r.to_dict() for r in rows]

    # --- шаги ролей --------------------------------------------------------
    def create_role_step(self, run_id: str, role: str, seq: int, *, provider: str = "",
                         requested_model: str = "", effective_model: str = "",
                         requested_profile: str = "", effective_profile: str = "",
                         reason_code: str = "", project_id: str = "") -> str:
        _guard(effective_profile, requested_profile)
        sid = new_id("rst")
        with session_scope() as s:
            s.add(RunRoleStep(id=sid, run_id=run_id, project_id=project_id, role=role, seq=seq,
                              provider=provider, requested_model=requested_model,
                              effective_model=effective_model, requested_profile=requested_profile,
                              effective_profile=effective_profile, reason_code=reason_code,
                              status="PENDING", version=1))
            s.commit()
        return sid

    def update_role_step(self, step_id: str, *, expected_version: int, status: str | None = None,
                         verdict: str | None = None, session_ref: str | None = None,
                         reason_code: str | None = None, builder_session_ref: str | None = None) -> dict:
        _guard(session_ref or "", builder_session_ref or "")
        with session_scope() as s:
            row = s.get(RunRoleStep, step_id)
            if row is None:
                raise RunError("NOT_FOUND", f"role step не найден: {step_id}")
            vals = {"version": RunRoleStep.version + 1}
            if status is not None:
                vals["status"] = status
            if verdict is not None:
                vals["verdict"] = verdict
            if session_ref is not None:
                vals["session_ref"] = session_ref
            if reason_code is not None:
                vals["reason_code"] = reason_code
            if builder_session_ref is not None:
                vals["builder_session_ref"] = builder_session_ref
            res = s.execute(update(RunRoleStep).where(
                RunRoleStep.id == step_id, RunRoleStep.version == expected_version).values(**vals))
            if res.rowcount != 1:
                raise RunError("VERSION_CONFLICT", "конкурентное изменение role step")
            s.commit()
            s.refresh(row)
            return row.to_dict()

    # --- provider-сессия (handle, без transcript/credentials) --------------
    def record_provider_session(self, run_id: str, *, provider: str, session_id: str,
                                role: str = "", profile_id: str = "") -> str:
        _guard(session_id, profile_id)
        pid = new_id("psx")
        with session_scope() as s:
            s.add(ProviderSession(id=pid, run_id=run_id, role=role, provider=provider,
                                  profile_id=profile_id, session_id=session_id, status="active"))
            s.commit()
        self._event(run_id, "session.started", {"provider": provider, "role": role})
        return pid

    def provider_sessions(self, run_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(ProviderSession).where(
                ProviderSession.run_id == run_id).order_by(ProviderSession.started_at)).scalars().all()
            return [r.to_dict() for r in rows]

    # --- ретраи / pause / handoff ------------------------------------------
    def record_retry(self, run_id: str, *, role: str, attempt: int, error_class: str,
                     backoff_ms: int = 0) -> str:
        rid = new_id("rry")
        with session_scope() as s:
            s.add(RunRetry(id=rid, run_id=run_id, role=role, attempt=attempt,
                           error_class=error_class, backoff_ms=backoff_ms))
            s.commit()
        return rid

    def retries(self, run_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(RunRetry).where(RunRetry.run_id == run_id)
                             .order_by(RunRetry.created_at)).scalars().all()
            return [{"role": r.role, "attempt": r.attempt, "error_class": r.error_class,
                     "backoff_ms": r.backoff_ms} for r in rows]

    def record_pause(self, run_id: str, kind: str, *, reason: str = "",
                     safe_continuation_ref: str = "") -> str:
        _guard(reason)
        pid = new_id("rpz")
        with session_scope() as s:
            s.add(RunPause(id=pid, run_id=run_id, kind=kind, reason=reason,
                           safe_continuation_ref=safe_continuation_ref))
            s.commit()
        self._event(run_id, f"run.{kind}", {"reason": reason})
        return pid

    def record_handoff_link(self, run_id: str, handoff_package_id: str, kind: str) -> str:
        hid = new_id("hlk")
        with session_scope() as s:
            s.add(HandoffLink(id=hid, run_id=run_id, handoff_package_id=handoff_package_id, kind=kind))
            s.commit()
        self._event(run_id, "handoff.linked", {"handoff": handoff_package_id, "kind": kind})
        return hid

    def handoff_links(self, run_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(HandoffLink).where(HandoffLink.run_id == run_id)
                             .order_by(HandoffLink.created_at)).scalars().all()
            return [{"handoff_package_id": r.handoff_package_id, "kind": r.kind} for r in rows]
