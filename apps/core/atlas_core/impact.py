"""Impact engine VP-6 (Master Spec §18.5, §18.6).

Детерминированная классификация изменения в точные классы
``DOC_ONLY|LOCAL|INTEGRATION|SHARED|HIGH_RISK`` по набору изменённых путей, с
выбором соответствующих check-групп. Правило §18.6: micro-fix НЕ запускает
полную регрессию без **видимого** risk-повода. Чистая логика без БД.
"""

from __future__ import annotations

from dataclasses import dataclass, field

IMPACT_CLASSES = ("DOC_ONLY", "LOCAL", "INTEGRATION", "SHARED", "HIGH_RISK")

# Порядок серьёзности (для выбора максимума при смешанном diff).
_ORDER = {c: i for i, c in enumerate(IMPACT_CLASSES)}

# Check-группы по классу (§18.5). full_regression только для HIGH_RISK/risk-trigger.
CHECK_GROUPS = {
    "DOC_ONLY": ["markdown", "link", "render"],
    "LOCAL": ["unit_targeted", "lint"],
    "INTEGRATION": ["unit", "integration"],
    "SHARED": ["unit", "integration", "dependent_suites"],
    "HIGH_RISK": ["unit", "integration", "dependent_suites", "security", "full_relevant"],
}

# SHARED: общесистемные модули (schema/router/policy/contracts/общая инфра).
_SHARED_BASENAMES = {
    "orm.py", "router.py", "contracts.py", "settings.py", "config.py",
    "redaction.py", "errors.py", "ids.py", "db.py", "schema_validate.py",
    "audit.py", "styles.css", "i18n.ts",
}

# HIGH_RISK-сигналы в пути (auth/grant/migration/release/security/isolation/lease/backup).
_HIGH_RISK_TOKENS = (
    "/migrations/", "alembic", "auth", "grant", "release", "security",
    "isolation", "leases", "lease", "login", "backup", "secret",
    "compose", "dockerfile", ".service", "systemd", "/infra/",
)

# INTEGRATION-сигналы (API/DB/adapter/runner).
_INTEGRATION_TOKENS = ("api_", "/adapters/", "runner", "_health", "pipeline", "orchestr")


def _classify_one(path: str) -> str:
    p = path.replace("\\", "/").strip()
    low = p.lower()
    base = p.rsplit("/", 1)[-1]
    # DOC_ONLY — только документация/markdown/лицензия.
    if low.endswith(".md") or low.startswith("docs/") or base in ("LICENSE", "NOTICE"):
        return "DOC_ONLY"
    # HIGH_RISK — auth/grant/migration/release/security/infra.
    if any(tok in low for tok in _HIGH_RISK_TOKENS):
        return "HIGH_RISK"
    # SHARED — общесистемные schema/router/policy/contracts/инфра-модули.
    if base in _SHARED_BASENAMES or "/policy" in low or "schema" in base:
        return "SHARED"
    # INTEGRATION — API/DB/adapter/runner.
    if any(tok in low for tok in _INTEGRATION_TOKENS):
        return "INTEGRATION"
    # Остальное (обычный модуль/веб-компонент/тест) — LOCAL.
    return "LOCAL"


@dataclass
class ImpactResult:
    impact_class: str
    reason: str
    check_groups: list[str]
    full_regression: bool
    risk_trigger: str = ""
    per_path: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "impact_class": self.impact_class, "reason": self.reason,
            "check_groups": self.check_groups, "full_regression": self.full_regression,
            "risk_trigger": self.risk_trigger, "per_path": self.per_path,
        }


def classify_impact(changed_paths: list[str], *, risk_trigger: str = "") -> ImpactResult:
    """Классифицировать изменение. Итоговый класс — максимум по путям.

    ``risk_trigger`` (видимый повод) поднимает класс до ``HIGH_RISK`` и включает
    полную регрессию — это единственный способ запустить полную регрессию на
    micro-fix (§18.6), и он остаётся видимым в ReviewPackage/QualityReport.
    """

    per_path = {p: _classify_one(p) for p in changed_paths}
    if not per_path:
        top = "DOC_ONLY"
    else:
        top = max(per_path.values(), key=lambda c: _ORDER[c])
    trig = risk_trigger.strip()
    if trig:
        top = "HIGH_RISK"
    full_regression = top == "HIGH_RISK"
    n = len(changed_paths)
    reason = (f"{n} изменённых путь(ей); класс = максимум по путям = {top}"
              + (f"; risk-trigger: {trig}" if trig else ""))
    if top != "HIGH_RISK" and not trig:
        reason += "; полная регрессия не требуется без видимого risk-повода (§18.6)"
    return ImpactResult(impact_class=top, reason=reason,
                        check_groups=list(CHECK_GROUPS[top]),
                        full_regression=full_regression, risk_trigger=trig,
                        per_path=per_path)
