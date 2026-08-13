"""VP-5 Profile/Model реестр (Master Spec §11, §17.2).

Сервисы поверх durable-таблиц ``agent_profiles/profile_states/profile_health/
capacity_observations`` и ``model_registry/discovery_snapshots``. Возвращают
только safe-представления: alias, provider, state, verified health/capacity,
current run/role/lease. НИКОГДА не отдают email, token, cookie, raw auth path.

Ёмкость: verified-значения только; отсутствие наблюдения → ``UNKNOWN``;
наблюдение старше ``CAPACITY_TTL_S`` → ``STALE`` (без выдуманных процентов).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import (
    AgentProfile,
    CapacityObservation,
    DiscoverySnapshot,
    ModelRegistry,
    ProfileHealth,
    ProfileState,
    RunLease,
)

CAPACITY_TTL_S = 900  # наблюдение ёмкости старше 15 минут → STALE
PROFILE_STATES = ("UNCONFIGURED", "AUTH_REQUIRED", "READY", "LEASED", "COOLDOWN",
                  "ERROR", "DRAINING", "DISABLED", "RETIRED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class RegistryError(Exception):
    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = reason
        self.http = {"NOT_FOUND": 404, "VERSION_CONFLICT": 409, "INVALID": 422}.get(code, 400)
        super().__init__(f"{code}: {reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


class ProfileService:
    def upsert_profile(self, alias: str, provider: str, *, unix_label: str = "",
                       auth_root_ref: str = "", schedulable: bool = True, enabled: bool = True) -> str:
        with session_scope() as s:
            row = s.execute(select(AgentProfile).where(AgentProfile.alias == alias)).scalars().first()
            if row is None:
                pid = new_id("aprof")
                s.add(AgentProfile(id=pid, alias=alias, provider=provider, unix_label=unix_label,
                                   auth_root_ref=auth_root_ref, schedulable=schedulable, enabled=enabled))
                s.add(ProfileState(id=new_id("pstate"), profile_id=pid, state="UNCONFIGURED",
                                   updated_at=_now(), version=1))
                s.commit()
                audit.record("profiles.profile.registered", f"alias={alias} provider={provider}")
                return pid
            row.provider = provider
            row.unix_label = unix_label or row.unix_label
            row.auth_root_ref = auth_root_ref or row.auth_root_ref
            row.schedulable = schedulable
            row.enabled = enabled
            s.commit()
            return row.id

    def set_state(self, profile_id: str, state: str, *, cooldown_until: datetime | None = None,
                  drain: bool | None = None, current_run_id: str | None = None,
                  current_role: str | None = None, next_action: str | None = None,
                  expected_version: int | None = None) -> dict:
        if state not in PROFILE_STATES:
            raise RegistryError("INVALID", f"неизвестное состояние: {state}")
        with session_scope() as s:
            row = s.get(ProfileState, self._state_id(s, profile_id))
            if row is None:
                raise RegistryError("NOT_FOUND", f"profile state не найден: {profile_id}")
            vals = {"state": state, "updated_at": _now(), "version": ProfileState.version + 1}
            if cooldown_until is not None:
                vals["cooldown_until"] = cooldown_until
            if drain is not None:
                vals["drain"] = drain
            if current_run_id is not None:
                vals["current_run_id"] = current_run_id
            if current_role is not None:
                vals["current_role"] = current_role
            if next_action is not None:
                vals["next_action"] = next_action
            where = [ProfileState.id == row.id]
            if expected_version is not None:
                where.append(ProfileState.version == expected_version)
            res = s.execute(update(ProfileState).where(*where).values(**vals))
            if res.rowcount != 1:
                raise RegistryError("VERSION_CONFLICT", "конкурентное изменение profile state")
            s.commit()
            s.refresh(row)
            return row.to_dict()

    def _state_id(self, s, profile_id: str) -> str | None:
        r = s.execute(select(ProfileState.id).where(ProfileState.profile_id == profile_id)).first()
        return r[0] if r else None

    def observe_health(self, profile_id: str, *, executable: str = "", cli_version: str = "",
                       auth: dict | None = None, permissions_ok: bool = False,
                       last_error: str = "") -> str:
        auth = auth or {}
        hid = new_id("phlth")
        with session_scope() as s:
            s.add(ProfileHealth(id=hid, profile_id=profile_id, executable=executable,
                                cli_version=cli_version, auth_status=str(auth.get("auth_status", "UNKNOWN")),
                                plan_label=str(auth.get("plan_label", "")),
                                permissions_ok=permissions_ok, last_error=last_error[:80],
                                observed_at=_now()))
            s.commit()
        return hid

    def observe_capacity(self, profile_id: str, *, status: str = "UNKNOWN",
                         five_h_used_pct: int | None = None, seven_d_used_pct: int | None = None,
                         reset_at: datetime | None = None, source: str = "unknown",
                         confidence: str = "unknown") -> str:
        cid = new_id("pcap")
        with session_scope() as s:
            s.add(CapacityObservation(id=cid, profile_id=profile_id, status=status,
                                      five_h_used_pct=five_h_used_pct, seven_d_used_pct=seven_d_used_pct,
                                      reset_at=reset_at, source=source, confidence=confidence,
                                      stale=False, observed_at=_now()))
            s.commit()
        return cid

    # --- safe-представления ------------------------------------------------
    def _latest_health(self, s, profile_id: str) -> ProfileHealth | None:
        return s.execute(select(ProfileHealth).where(ProfileHealth.profile_id == profile_id)
                         .order_by(ProfileHealth.observed_at.desc()).limit(1)).scalars().first()

    def _latest_capacity_view(self, s, profile_id: str) -> dict:
        row = s.execute(select(CapacityObservation).where(CapacityObservation.profile_id == profile_id)
                        .order_by(CapacityObservation.observed_at.desc()).limit(1)).scalars().first()
        if row is None:
            return {"status": "UNKNOWN", "five_h_used_pct": None, "seven_d_used_pct": None,
                    "reset_at": None, "source": "unknown", "observed_at": None,
                    "data_observed_at": None, "windows": [], "plan": "", "error_code": "",
                    "confidence": "none", "stale": False}
        d = row.to_dict()
        # Свежесть по возрасту ДАННЫХ (data_observed_at), а не по времени последней
        # проверки: протухшее наблюдение → STALE (кроме честного UNKNOWN).
        basis = _aware(row.data_observed_at) or _aware(row.observed_at)
        age = (_now() - basis).total_seconds()
        if age > CAPACITY_TTL_S and d["status"] not in ("UNKNOWN",):
            d["status"] = "STALE"
            d["stale"] = True
        return d

    def _active_lease(self, s, profile_id: str) -> dict | None:
        r = s.execute(select(RunLease).where(RunLease.profile_id == profile_id,
                      RunLease.released_at == "")).scalars().first()
        return {"run_id": r.run_id, "role": r.role, "worktree": r.worktree} if r else None

    def _view(self, s, prof: AgentProfile, state: ProfileState | None) -> dict:
        h = self._latest_health(s, prof.id)
        return {
            "id": prof.id, "alias": prof.alias, "provider": prof.provider,
            "unix_label": prof.unix_label, "schedulable": prof.schedulable, "enabled": prof.enabled,
            "state": state.state if state else "UNCONFIGURED",
            "cooldown_until": (state.to_dict()["cooldown_until"] if state else None),
            "drain": state.drain if state else False,
            "current_run_id": state.current_run_id if state else "",
            "current_role": state.current_role if state else "",
            "next_action": state.next_action if state else "",
            "health": (h.to_dict() if h else None),
            "capacity": self._latest_capacity_view(s, prof.id),
            "active_lease": self._active_lease(s, prof.id),
        }

    def list_profiles(self, *, provider: str | None = None, state: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(AgentProfile).order_by(AgentProfile.provider, AgentProfile.alias)
            if provider:
                stmt = stmt.where(AgentProfile.provider == provider)
            out = []
            for prof in s.execute(stmt).scalars().all():
                st = s.execute(select(ProfileState).where(
                    ProfileState.profile_id == prof.id)).scalars().first()
                if state and (st.state if st else "UNCONFIGURED") != state:
                    continue
                out.append(self._view(s, prof, st))
            return out

    def get_profile(self, profile_id: str) -> dict:
        with session_scope() as s:
            prof = s.get(AgentProfile, profile_id)
            if prof is None:
                raise RegistryError("NOT_FOUND", f"профиль не найден: {profile_id}")
            st = s.execute(select(ProfileState).where(
                ProfileState.profile_id == prof.id)).scalars().first()
            return self._view(s, prof, st)

    def summary_counts(self) -> dict:
        counts = {k: 0 for k in PROFILE_STATES}
        with session_scope() as s:
            rows = s.execute(select(ProfileState.state, func.count()).group_by(ProfileState.state)).all()
            for state, n in rows:
                counts[state] = int(n)
        return counts


class ModelService:
    def record_model(self, provider: str, model_id: str, *, alias: str = "", display: str = "",
                     efforts: list[str] | None = None, context_capability: str = "",
                     structured_capability: bool = False, availability: str = "unknown",
                     source: str = "unknown", confidence: str = "unknown") -> str:
        with session_scope() as s:
            row = s.execute(select(ModelRegistry).where(
                ModelRegistry.provider == provider, ModelRegistry.model_id == model_id)).scalars().first()
            if row is None:
                mid = new_id("model")
                s.add(ModelRegistry(id=mid, provider=provider, model_id=model_id, alias=alias,
                                    display=display, efforts_json=json.dumps(efforts or []),
                                    context_capability=context_capability,
                                    structured_capability=structured_capability,
                                    availability=availability, source=source, confidence=confidence,
                                    discovered_at=_now(), version=1))
                s.commit()
                return mid
            row.alias = alias or row.alias
            row.display = display or row.display
            row.efforts_json = json.dumps(efforts) if efforts is not None else row.efforts_json
            row.availability = availability
            row.source = source
            row.confidence = confidence
            row.discovered_at = _now()
            row.version = row.version + 1
            s.commit()
            return row.id

    def record_discovery(self, provider: str, models: list[dict], *, profile_id: str = "",
                         source: str = "unknown") -> str:
        did = new_id("disc")
        with session_scope() as s:
            s.add(DiscoverySnapshot(id=did, provider=provider, profile_id=profile_id,
                                    models_json=json.dumps(models, ensure_ascii=False),
                                    source=source, observed_at=_now()))
            s.commit()
        return did

    def list_models(self, *, provider: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(ModelRegistry).order_by(ModelRegistry.provider, ModelRegistry.model_id)
            if provider:
                stmt = stmt.where(ModelRegistry.provider == provider)
            return [m.to_dict() for m in s.execute(stmt).scalars().all()]
