"""Bounded read-only auth-health четырёх профилей (Master Spec §11.5, VP-7 D1).

`AUTH_REQUIRED` в UI — консервативное durable-состояние, а не доказательство, что
все логины истекли. Здесь выполняются **bounded read-only** пробы через
официальные CLI status/version интерфейсы (``codex login status`` /
``claude auth status --json`` / ``--version``):

* НЕ читаются credential-файлы;
* НЕ выводятся token/cookie/email/organization id/auth path;
* НЕ мутируется логин, НЕ стартует provider-чат;
* только safe aliases; фиксируются exit-состояние, нормализованное состояние и
  redacted safe-reason.

Нормализация: ``READY`` только если факт **доказывает** готовность; иначе
``AUTH_REQUIRED``/``AUTH_EXPIRED`` когда доказано; ``UNKNOWN`` когда CLI не может
доказать состояние. Свежесть: наблюдение старше TTL → ``STALE``. Результат
персистируется через supported profile-health путь (``ProfileService.observe_health``).
Reconcile остаётся session-free/credential-free; успех auth **не** выводит
quota/capacity.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .adapters.real_claude import RealClaudeAdapter
from .adapters.real_codex import RealCodexAdapter
from .agent_registry import ProfileService
from .db import session_scope
from .orm import AgentProfile, ProfileHealth, ProfileState
from .profiles import Profile, ProfileRegistry
from .redaction import redact

AUTH_HEALTH_TTL_S = 900  # auth-наблюдение старше 15 мин → STALE

# Состояния ProfileState (§11.3), которые безопасно синхронизировать с auth-health
# (не перетираем busy: LEASED/COOLDOWN/DRAINING/DISABLED/RETIRED).
_SYNCABLE_STATES = frozenset({"UNCONFIGURED", "AUTH_REQUIRED", "READY", "ERROR"})


def _adapter(provider: str):
    return {"codex": RealCodexAdapter, "claude": RealClaudeAdapter}[provider]()


def normalize_state(auth: dict) -> tuple[str, str]:
    """(normalized_state, safe_reason). READY только если доказано; иначе честный
    AUTH_REQUIRED/AUTH_EXPIRED/UNKNOWN. Никаких account-деталей."""
    state = auth.get("state")
    if auth.get("authenticated"):
        return "READY", "CLI подтвердил вход"
    if state == "CLI_ABSENT":
        return "UNKNOWN", "CLI недоступен — состояние не доказано"
    if state == "ERROR":
        return "UNKNOWN", "проба завершилась ошибкой — состояние не доказано"
    if state == "AUTH_EXPIRED":
        return "AUTH_EXPIRED", "CLI: срок авторизации истёк"
    if state == "AUTH_REQUIRED":
        return "AUTH_REQUIRED", "CLI: не авторизован"
    return "UNKNOWN", "состояние не доказано"


def _default_prober(profile: Profile) -> dict:
    """Реальная read-only проба через официальный CLI (status + version)."""
    adapter = _adapter(profile.provider)
    ver = adapter.cli_version(profile.executable_path, root_path=profile.root_path,
                              run_as_user=profile.runtime_user)
    auth = adapter.auth_status(profile.root_path, executable=profile.executable_path,
                               run_as_user=profile.runtime_user)
    return {"cli_version": ver or "", "auth": auth}


def check_profile(profile: Profile, *, prober=None) -> dict:
    """Одна bounded read-only проба. Возвращает safe-наблюдение без секретов."""
    prober = prober or _default_prober
    probe = prober(profile)
    auth = probe.get("auth", {})
    norm, reason = normalize_state(auth)
    return {
        "alias": profile.alias, "provider": profile.provider,
        "cli_version": redact(str(probe.get("cli_version", "")))[:80],
        "auth_status": norm, "reason": reason,
        "exit_state": auth.get("state", ""),
        "authenticated": bool(auth.get("authenticated")),
    }


def run_auth_health(*, registry: ProfileRegistry | None = None, prober=None,
                    actor: str = "owner", correlation_id: str = "") -> list[dict]:
    """Пробежать read-only auth-health по allowlisted профилям, персистировать
    через profile-health путь, безопасно синхронизировать ProfileState. Возвращает
    список наблюдений (safe)."""
    registry = registry or ProfileRegistry()
    svc = ProfileService()
    audit.record("profiles.authhealth.before", "read-only auth-health старт",
                 actor=actor, correlation_id=correlation_id)
    out: list[dict] = []
    for prof in registry.list():
        obs = check_profile(prof, prober=prober)
        pid = _profile_id(prof.alias)
        if pid:
            svc.observe_health(
                pid, executable=prof.executable_path or "", cli_version=obs["cli_version"],
                auth={"auth_status": obs["auth_status"]},
                permissions_ok=obs["auth_status"] == "READY",
                last_error=obs["reason"])
            _stamp_source(pid, "cli_status")
            _sync_state_safe(svc, pid, obs["auth_status"])
        obs["profile_id"] = pid or ""
        out.append(obs)
    audit.record("profiles.authhealth.after",
                 f"checked={len(out)} ready={sum(1 for o in out if o['auth_status']=='READY')}",
                 actor=actor, correlation_id=correlation_id)
    return out


def _profile_id(alias: str) -> str | None:
    with session_scope() as s:
        row = s.execute(select(AgentProfile.id).where(AgentProfile.alias == alias)).first()
        return row[0] if row else None


def _stamp_source(profile_id: str, source: str) -> None:
    """Проставить source последнего health-наблюдения (provenance)."""
    with session_scope() as s:
        row = s.execute(select(ProfileHealth).where(ProfileHealth.profile_id == profile_id)
                        .order_by(ProfileHealth.observed_at.desc()).limit(1)).scalars().first()
        if row is not None:
            row.source = source
            s.commit()


def _sync_state_safe(svc: ProfileService, profile_id: str, auth_status: str) -> None:
    """Безопасно синхронизировать ProfileState с доказанным auth (не перетирая
    busy-состояния). AUTH_EXPIRED → AUTH_REQUIRED в state-колонке (точный
    AUTH_EXPIRED остаётся в health). UNKNOWN не трогает state."""
    target = {"READY": "READY", "AUTH_REQUIRED": "AUTH_REQUIRED",
              "AUTH_EXPIRED": "AUTH_REQUIRED"}.get(auth_status)
    if target is None:  # UNKNOWN — не доказано, состояние не меняем
        return
    with session_scope() as s:
        row = s.execute(select(ProfileState).where(
            ProfileState.profile_id == profile_id)).scalars().first()
        if row is None or row.state not in _SYNCABLE_STATES or row.state == target:
            return
        current_version = row.version
    try:
        svc.set_state(profile_id, target, expected_version=current_version,
                      next_action=("Профиль готов к работе." if target == "READY"
                                   else "Owner-логин в изолированный root профиля."))
    except Exception:  # noqa: BLE001 — конкурентное изменение: не критично
        pass


def auth_health_report(*, profiles: list[dict] | None = None) -> list[dict]:
    """Read-only отчёт: последнее auth-health наблюдение на профиль + STALE по TTL."""
    now = datetime.now(timezone.utc)
    result: list[dict] = []
    with session_scope() as s:
        prof_rows = s.execute(select(AgentProfile).order_by(
            AgentProfile.provider, AgentProfile.alias)).scalars().all()
        for prof in prof_rows:
            h = s.execute(select(ProfileHealth).where(ProfileHealth.profile_id == prof.id)
                          .order_by(ProfileHealth.observed_at.desc()).limit(1)).scalars().first()
            if h is None:
                result.append({"alias": prof.alias, "provider": prof.provider,
                               "auth_status": "UNKNOWN", "observed_at": None,
                               "source": "", "reason": "нет наблюдений", "stale": False})
                continue
            d = h.to_dict()
            observed = _aware(h.observed_at)
            age = (now - observed).total_seconds() if observed else None
            status = d["auth_status"]
            stale = bool(age is not None and age > AUTH_HEALTH_TTL_S and status != "UNKNOWN")
            result.append({
                "alias": prof.alias, "provider": prof.provider,
                "auth_status": "STALE" if stale else status,
                "observed_at": d["observed_at"], "source": d.get("source", ""),
                "reason": d.get("last_error", ""), "cli_version": d.get("cli_version", ""),
                "stale": stale, "raw_status": status,
            })
    return result


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
