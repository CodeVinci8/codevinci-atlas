"""Router профиля/модели для Agent Pipeline (Master Spec §17.2, §17.3).

Детерминированный выбор эффективного профиля и модели с reason-кодами и БЕЗ
silent fallback. Ключевое правило §17.2: если owner явно потребовал профиль или
модель, недоступные сейчас, роутер НЕ подставляет молчаливую замену — он
возвращает решение с пустым ``effective`` и reason-кодом *_UNAVAILABLE, а вызов
переходит в ``OWNER_REQUIRED``. Requested + effective + reason_code + время
наблюдения persist'ятся в ``router_decisions``.

Приоритет (§17.3): 1 owner override · 2 role compatibility · 3 profile READY ·
4 safe affinity · 5 verified capacity · 6 cooldown/error · 7 least recently used
· 8 deterministic tie-break. Модуль оперирует простыми структурами (не ORM),
поэтому детерминирован и тестируем.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Role
from .ids import utcnow_iso

# Роль → провайдер по умолчанию: Codex Planner → Claude Builder → Codex Reviewer.
DEFAULT_ROLE_PROVIDER: dict[str, str] = {
    Role.PLANNER.value: "codex",
    Role.BUILDER.value: "claude",
    Role.REVIEWER.value: "codex",
}


class ReasonCode:
    OWNER_OVERRIDE = "OWNER_OVERRIDE"
    OWNER_OVERRIDE_UNAVAILABLE = "OWNER_OVERRIDE_UNAVAILABLE"  # requested profile недоступен → без fallback
    ROLE_READY_ONLY = "ROLE_READY_ONLY"  # единственный совместимый READY-профиль
    ROLE_READY_AFFINITY = "ROLE_READY_AFFINITY"
    ROLE_READY_CAPACITY = "ROLE_READY_CAPACITY"
    ROLE_READY_LRU = "ROLE_READY_LRU"
    DETERMINISTIC_TIE = "DETERMINISTIC_TIE"
    NO_ELIGIBLE_PROFILE = "NO_ELIGIBLE_PROFILE"
    # модель
    MODEL_REQUESTED = "MODEL_REQUESTED"
    MODEL_REQUESTED_UNAVAILABLE = "MODEL_REQUESTED_UNAVAILABLE"  # requested модель недоступна → без fallback
    MODEL_PRESET_DEFAULT = "MODEL_PRESET_DEFAULT"
    MODEL_NONE_AVAILABLE = "MODEL_NONE_AVAILABLE"


# Меньше — предпочтительнее. EXHAUSTED исключается до сортировки.
_CAPACITY_RANK = {"AVAILABLE": 0, "LOW": 1, "UNKNOWN": 2}


@dataclass
class Candidate:
    """Кандидат-профиль для роутинга (снимок наблюдаемого состояния)."""

    alias: str
    provider: str
    state: str = "READY"  # READY|LEASED|COOLDOWN|AUTH_REQUIRED|ERROR|DRAINING|DISABLED|RETIRED
    capacity_status: str = "UNKNOWN"  # AVAILABLE|LOW|EXHAUSTED|UNKNOWN
    affinity: bool = False  # тот же профиль, что на предыдущем совместимом шаге (safe affinity)
    last_used_ms: int = 0  # для LRU; 0 = никогда не использовался (наиболее предпочтителен)
    schedulable: bool = True


@dataclass
class RouterDecision:
    role: str
    requested_profile: str
    effective_profile: str
    requested_model: str
    effective_model: str
    reason_code: str
    candidates: list[dict] = field(default_factory=list)
    decided_at: str = ""
    ok: bool = False

    def to_dict(self) -> dict:
        return {
            "role": self.role, "requested_profile": self.requested_profile,
            "effective_profile": self.effective_profile,
            "requested_model": self.requested_model, "effective_model": self.effective_model,
            "reason_code": self.reason_code, "candidates": self.candidates,
            "decided_at": self.decided_at, "ok": self.ok,
        }


def _role_value(role) -> str:
    return role.value if isinstance(role, Role) else str(role)


def _eligible(c: Candidate, provider: str) -> bool:
    return (c.provider == provider and c.state == "READY" and c.schedulable
            and c.capacity_status != "EXHAUSTED")


def route_profile(role, candidates: list[Candidate], *,
                  requested_profile: str = "", requested_model: str = "",
                  effective_model: str = "") -> RouterDecision:
    """Выбрать эффективный профиль (§17.3). Без silent fallback."""
    role_val = _role_value(role)
    provider = DEFAULT_ROLE_PROVIDER.get(role_val, "")
    snapshot = [{"alias": c.alias, "provider": c.provider, "state": c.state,
                 "capacity": c.capacity_status, "affinity": c.affinity} for c in candidates]

    def decide(effective_profile, reason, ok):
        return RouterDecision(role=role_val, requested_profile=requested_profile,
                              effective_profile=effective_profile, requested_model=requested_model,
                              effective_model=(effective_model or requested_model) if ok else "",
                              reason_code=reason, candidates=snapshot,
                              decided_at=utcnow_iso(), ok=ok)

    # 1. Owner override профиля.
    if requested_profile:
        match = [c for c in candidates if c.alias == requested_profile]
        if match and _eligible(match[0], provider):
            return decide(match[0].alias, ReasonCode.OWNER_OVERRIDE, True)
        # Явно запрошенный профиль недоступен → НЕ молчаливая замена.
        return decide("", ReasonCode.OWNER_OVERRIDE_UNAVAILABLE, False)

    # 2-3-6. role compatibility + READY + не cooldown/error + не EXHAUSTED + schedulable.
    eligible = [c for c in candidates if _eligible(c, provider)]
    if not eligible:
        return decide("", ReasonCode.NO_ELIGIBLE_PROFILE, False)

    # 4 affinity · 5 capacity · 7 LRU · 8 deterministic tie (alias).
    def sort_key(c: Candidate):
        return (0 if c.affinity else 1,
                _CAPACITY_RANK.get(c.capacity_status, 3),
                c.last_used_ms,
                c.alias)

    eligible.sort(key=sort_key)
    chosen = eligible[0]
    # Reason = первый компонент ключа, которым chosen отличается от ближайшего
    # конкурента (eligible[1]); если отличается только alias — deterministic tie.
    if len(eligible) == 1:
        reason = ReasonCode.ROLE_READY_ONLY
    else:
        k0, k1 = sort_key(chosen), sort_key(eligible[1])
        if k0[0] != k1[0]:
            reason = ReasonCode.ROLE_READY_AFFINITY
        elif k0[1] != k1[1]:
            reason = ReasonCode.ROLE_READY_CAPACITY
        elif k0[2] != k1[2]:
            reason = ReasonCode.ROLE_READY_LRU
        else:
            reason = ReasonCode.DETERMINISTIC_TIE
    return decide(chosen.alias, reason, True)


def resolve_model(role, available_models: list[str], *,
                  requested_model: str = "", preset_prefs: list[str] | None = None) -> tuple[str, str]:
    """Выбрать эффективную модель (§17.2). Возвращает ``(effective_model, reason_code)``.

    Без silent fallback: явно запрошенная, но недоступная модель → пустой
    ``effective`` + ``MODEL_REQUESTED_UNAVAILABLE`` (вызов уходит в OWNER_REQUIRED).
    Если модель не запрошена — берётся первый доступный из preset_prefs (видимый
    дефолт, не молчаливая замена).
    """
    avail = list(available_models or [])
    if requested_model:
        if requested_model in avail:
            return requested_model, ReasonCode.MODEL_REQUESTED
        return "", ReasonCode.MODEL_REQUESTED_UNAVAILABLE
    for pref in (preset_prefs or []):
        if pref in avail:
            return pref, ReasonCode.MODEL_PRESET_DEFAULT
    if avail:
        return sorted(avail)[0], ReasonCode.MODEL_PRESET_DEFAULT
    return "", ReasonCode.MODEL_NONE_AVAILABLE
