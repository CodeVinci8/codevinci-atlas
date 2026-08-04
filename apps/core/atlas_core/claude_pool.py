"""Минимальный пул из двух Claude-профилей как один логический Builder-пул
(Master Spec §17.2/§17.3, VP-7). Это НЕ полный VP-8 operations console — только
вертикальный срез, чтобы безопасно использовать обе owner-авторизованные подписки.

Границы (жёсткие):

* профили ``claude-pro-01`` и ``claude-pro-02`` сохраняют раздельные Unix-иден-
  тичности, HOME, config и credentials; ничего не копируется и не сливается;
* Router видит их как один Claude execution-пул, но Builder на конкретный
  Run/сессию — РОВНО один (sticky в пределах активной Builder-сессии);
* один writer на worktree; смена профиля НЕ во время ответа модели/tool-call;
* независимость Reviewer от Builder-alias сохраняется;
* при точном rate-limit ответе провайдера окно помечается исчерпанным, Run и
  verified handoff/checkpoint сохраняются, освобождается только safe profile lease,
  следующий Builder-ход ретраится максимум один раз на другом профиле;
* нет фиктивного «объединённого процента»/«200%» — это эффективная маршрутная
  ёмкость, не одна слитая подписка Anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import audit
from .router import Candidate, ReasonCode, RouterDecision, route_profile

CLAUDE_POOL = ("claude-pro-01", "claude-pro-02")
_PROVIDER = "claude"


def _remaining_min(cap: dict) -> float | None:
    """min(остаток%) по числовым окнам; None если чисел нет (Claude status-окна)."""
    rems = [w.get("remaining_pct") for w in (cap.get("windows") or [])
            if w.get("remaining_pct") is not None]
    return min(rems) if rems else None


def _is_fresh(cap: dict) -> bool:
    """Свежее наблюдение с реальным статусом (не STALE/UNKNOWN)."""
    return not cap.get("stale") and cap.get("status") not in (None, "UNKNOWN", "STALE")


def _next_reset(cap: dict) -> str | None:
    resets = [w.get("reset_at") for w in (cap.get("windows") or []) if w.get("reset_at")]
    return min(resets) if resets else None


@dataclass
class PoolMember:
    alias: str
    state: str
    capacity_status: str
    fresh: bool
    remaining_min: float | None
    next_reset: str | None
    observed_at: str | None


def _members_from_profiles(profiles: list[dict]) -> list[PoolMember]:
    out: list[PoolMember] = []
    for p in profiles:
        if p.get("provider") != _PROVIDER or p.get("alias") not in CLAUDE_POOL:
            continue
        cap = p.get("capacity") or {}
        out.append(PoolMember(
            alias=p["alias"], state=p.get("state", "UNCONFIGURED"),
            capacity_status=cap.get("status", "UNKNOWN"), fresh=_is_fresh(cap),
            remaining_min=_remaining_min(cap), next_reset=_next_reset(cap),
            observed_at=cap.get("observed_at")))
    return out


def _to_candidates(members: list[PoolMember], *, active_alias: str = "",
                   exclude: set[str] | None = None,
                   last_used: dict[str, int] | None = None) -> list[Candidate]:
    exclude = exclude or set()
    last_used = last_used or {}
    cands: list[Candidate] = []
    for m in members:
        if m.alias in exclude:
            continue
        cands.append(Candidate(
            alias=m.alias, provider=_PROVIDER, state=m.state,
            capacity_status=m.capacity_status, affinity=(m.alias == active_alias),
            last_used_ms=last_used.get(m.alias, 0), schedulable=True,
            remaining_min=m.remaining_min, fresh=m.fresh))
    return cands


def select_builder(profiles: list[dict], *, requested: str = "", sticky_alias: str = "",
                   exclude: set[str] | None = None, last_used: dict[str, int] | None = None,
                   actor: str = "router") -> RouterDecision:
    """Выбрать Builder-профиль из Claude-пула (§17.3, без silent fallback).

    ``sticky_alias`` — активная Builder-сессия липнет к своему профилю (affinity).
    ``exclude`` — профили, исключённые из выбора (напр. только что получивший
    rate-limit при safe-handoff). Консервативный fallback: если ёмкость обоих
    неизвестна, но профиль READY — выбор по READY+LRU (раскрываем причину)."""
    members = _members_from_profiles(profiles)
    cands = _to_candidates(members, active_alias=sticky_alias, exclude=exclude,
                           last_used=last_used)
    decision = route_profile("builder", cands, requested_profile=requested)
    audit.record("router.pool.select",
                 f"pool=claude effective={decision.effective_profile or '-'} "
                 f"reason={decision.reason_code} ok={decision.ok}", actor=actor)
    return decision


def reviewer_independent(builder_alias: str, reviewer_alias: str) -> bool:
    """Reviewer независим от Builder, если это НЕ тот же alias (§17.1). Codex-
    Reviewer против Claude-Builder независим всегда (разные провайдеры/пулы)."""
    if not reviewer_alias:
        return False
    return reviewer_alias != builder_alias


# Отображение окна rate-limit ответа провайдера → id окна Atlas.
_RL_WINDOW = {"five_hour": "5h", "seven_day": "7d"}


def handle_rate_limit(profiles: list[dict], *, alias: str, rate_limit_type: str,
                      resets_at_epoch=None, last_used: dict[str, int] | None = None,
                      actor: str = "router") -> dict:
    """Safe-handoff при точном rate-limit ответе провайдера для ``alias``:

    * помечает соответствующее окно профиля исчерпанным (durable observation);
    * НЕ трогает Run/handoff/checkpoint (их сохраняет вызывающий);
    * выбирает следующий Builder-профиль, исключая ``alias`` (ретрай ≤1);
    * пишет Audit-событие с причиной. Не дублирует уже выполненный side-effect."""
    from .agent_registry import ProfileService
    from .capacity import _epoch_iso, _mk_window, persist_capacity
    wid = _RL_WINDOW.get(rate_limit_type, "5h")
    label = {"5h": "Сессия (5 ч)", "7d": "Неделя (7 дн)"}[wid]
    mins = 300 if wid == "5h" else 10080
    # Персист исчерпанного окна (status=rejected → EXHAUSTED), без фикции процента.
    w = _mk_window(win_id=wid, label=label, used_pct=100.0,
                   reset_at=_epoch_iso(resets_at_epoch) if resets_at_epoch else None,
                   window_mins=mins)
    w["status"] = "rejected"
    svc = ProfileService()
    pid = None
    for p in svc.list_profiles():
        if p.get("alias") == alias:
            pid = p["id"]
            break
    if pid:
        persist_capacity(pid, {"provider": _PROVIDER, "plan": "", "auth_ok": True,
                               "source": "claude-stream-json", "error_code": "",
                               "windows": [w]})
    fresh_profiles = svc.list_profiles()
    decision = select_builder(fresh_profiles, exclude={alias}, last_used=last_used,
                              actor=actor)
    audit.record("router.pool.ratelimit",
                 f"exhausted={alias} window={wid} handoff={decision.effective_profile or '-'} "
                 f"reason={decision.reason_code}", actor=actor)
    return {"exhausted_alias": alias, "window": wid,
            "next_effective": decision.effective_profile, "reason_code": decision.reason_code,
            "ok": decision.ok, "retry_allowed": decision.ok}


def pool_summary(profiles: list[dict] | None = None, *, last_reason: str = "",
                 active_alias: str = "") -> dict:
    """Компактная сводка Claude-пула для UI: авторизовано/eligible/активный/
    ближайший reset/последняя причина роутинга. Без фиктивного combined-процента."""
    if profiles is None:
        from .agent_registry import ProfileService
        profiles = ProfileService().list_profiles()
    members = _members_from_profiles(profiles)
    authorized = [m for m in members if m.state in ("READY", "LEASED", "COOLDOWN")]
    eligible = [m for m in members
                if m.state == "READY" and m.capacity_status != "EXHAUSTED"]
    resets = [m.next_reset for m in members if m.next_reset]
    return {
        "pool": "claude",
        "members": [m.alias for m in members],
        "authorized_count": len(authorized),
        "eligible_count": len(eligible),
        "active_alias": active_alias,
        "next_reset": min(resets) if resets else None,
        "last_reason": last_reason,
        # Раскрываем консервативный fallback явно (не выдумываем ёмкость).
        "conservative_fallback": any(m.capacity_status == "UNKNOWN" for m in eligible),
    }


def _active_claude_alias() -> str:
    """Активный назначенный Claude Builder = держатель активной run-lease на
    claude-профиль (durable → переживает рестарт Core)."""
    from sqlalchemy import select

    from .db import session_scope
    from .orm import AgentProfile, RunLease
    with session_scope() as s:
        rows = s.execute(select(RunLease.profile_id, RunLease.role)
                         .where(RunLease.released_at == "")).all()
        for pid, _role in rows:
            prof = s.get(AgentProfile, pid)
            if prof and prof.alias in CLAUDE_POOL:
                return prof.alias
    return ""


def _last_routing_reason() -> str:
    """Последняя причина роутинга пула из durable audit (переживает рестарт)."""
    from sqlalchemy import select

    from .db import session_scope
    from .orm import AuditEvent
    with session_scope() as s:
        row = s.execute(select(AuditEvent.message)
                        .where(AuditEvent.event_type == "router.pool.select")
                        .order_by(AuditEvent.created_at.desc()).limit(1)).first()
        return row[0] if row else ""


def pool_summary_live() -> dict:
    """Сводка пула из durable-источников (профили/leases/audit) — для API/UI."""
    return pool_summary(last_reason=_last_routing_reason(), active_alias=_active_claude_alias())


# Экспорт для тестов/интеграции.
__all__ = ["CLAUDE_POOL", "select_builder", "reviewer_independent", "handle_rate_limit",
           "pool_summary", "pool_summary_live", "ReasonCode"]
