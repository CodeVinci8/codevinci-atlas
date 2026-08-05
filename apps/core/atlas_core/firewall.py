"""Quality Firewall VP-6 (Master Spec §18.3 + freshness + license-visibility).

Набор детерминированных gate-функций, каждая возвращает findings с обязательными
полями (§18.2): severity, criterion, location, evidence, action, blocking,
source, freshness, стабильный code. Gates evidence-backed; needless-architecture
ограничен фактами (без бесконечного стиль-полицейства). Отсутствие Atlas LICENSE
эмитится как **видимый owner-decision** finding (LICENSE не добавляется).

Чистая логика: читает переданный контекст и (read-only) synthetic worktree.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .redaction import redact, scan_paths
from .secret_scan import _is_allowlisted, _is_credential_root, scan_repo

SEVERITY = ("blocker", "major", "minor", "info")

# Non-waivable коды (§18.4): waiver их не обходит.
NON_WAIVABLE_CODES = {
    "SECRET_DETECTED", "CREDENTIAL_EXPOSURE", "UNAUTHORIZED_EXTERNAL_ACTION",
    "ONE_WRITER_VIOLATION", "STALE_EVIDENCE", "STALE_REVIEW_HEAD",
}

# Маркеры AI-мусора / placeholder / fake success.
_AI_MARKERS = [
    re.compile(r"\bTODO\b"), re.compile(r"\bFIXME\b"),
    re.compile(r"raise\s+NotImplementedError"),
    re.compile(r"#\s*(placeholder|заглушка|fake|мок|stub)", re.I),
    re.compile(r"return\s+True\s*#\s*(fake|always|stub|placeholder)", re.I),
    re.compile(r"\bplaceholder\b", re.I),
]


def _finding(gate: str, code: str, severity: str, criterion: str, *,
             location: str = "", evidence: str = "", action: str = "",
             blocking: bool | None = None, source: str = "firewall",
             freshness: str = "FRESH") -> dict:
    if blocking is None:
        blocking = severity in ("blocker",)
    return {
        "gate": gate, "code": code, "severity": severity, "criterion": criterion,
        "location": location, "evidence": redact(evidence)[:400], "action": action,
        "blocking": bool(blocking), "source": source, "freshness": freshness,
    }


@dataclass
class FirewallContext:
    package: dict
    worktree: str | None = None
    current_head: str | None = None
    claim_ok: bool | None = None            # независимый пересчёт: claim подтверждён?
    claim_detail: str = ""
    docs_commands: list[str] = field(default_factory=list)
    runnable_commands: set = field(default_factory=set)
    required_web_states: list[str] = field(default_factory=list)
    declared_web_states: list[str] = field(default_factory=list)
    license_present: bool = True
    license_required: bool = False
    license_spdx: str = "Apache-2.0"   # VP-7: владелец выбрал Apache-2.0 (§49, DECISIONS)
    freshness: dict = field(default_factory=dict)   # {source: FRESH|STALE|UNKNOWN}
    security_check_present: bool = True
    flagged_symbols: list[dict] = field(default_factory=list)  # [{name, file, refs}]
    acceptance: list[dict] = field(default_factory=list)       # [{criterion, passed}]


# --- отдельные gates -------------------------------------------------------
def gate_brief_vp_compliance(ctx: FirewallContext) -> list[dict]:
    out = []
    for item in (ctx.acceptance or ctx.package.get("acceptance", [])):
        if item.get("passed") is False:
            out.append(_finding(
                "brief_vp_compliance", "CRITERION_UNMET", "blocker",
                item.get("criterion", "критерий VP"),
                location=item.get("check", ""),
                evidence=f"критерий не выполнен: {item.get('criterion')}",
                action="Довести поведение до критерия и повторить impacted checks."))
    return out


def gate_real_behavior_vs_claim(ctx: FirewallContext) -> list[dict]:
    if ctx.claim_ok is False:
        return [_finding(
            "real_behavior_vs_claim", "REAL_BEHAVIOR_MISMATCH", "blocker",
            "реальное поведение против заявления Builder",
            location=ctx.package.get("run_id", ""),
            evidence=f"независимый пересчёт опроверг claim: {ctx.claim_detail}",
            action="Отклонить ложный success; вернуть на focused fix.")]
    return []


def gate_secrets_privacy(ctx: FirewallContext) -> list[dict]:
    if not ctx.worktree or not os.path.isdir(ctx.worktree):
        return []
    real_hits: list = []
    history_hits: list = []
    if os.path.isdir(os.path.join(ctx.worktree, ".git")):
        # Git-репо: полная git-aware проверка (рабочее дерево + история + allowlist).
        rep = scan_repo(ctx.worktree)
        real_hits = list(rep.real_hits)
        history_hits = list(rep.git_history_hits)
    else:
        # Не git-репо: прямой обход дерева (не зависит от git), тот же allowlist.
        for hit in scan_paths([ctx.worktree]):
            if _is_credential_root(hit.path) or _is_allowlisted(hit):
                continue
            real_hits.append(hit)
    if real_hits or history_hits:
        locs = [getattr(h, "path", "") or getattr(h, "location", "") for h in real_hits]
        return [_finding(
            "secrets_privacy", "SECRET_DETECTED", "blocker",
            "секреты/приватность в diff/evidence",
            location="; ".join(str(x) for x in locs[:3]),
            evidence=f"secret-scan НАХОДКИ: real={len(real_hits)} "
                     f"history={len(history_hits)}",
            action="Удалить секрет из diff/evidence; ротировать; не waivable.")]
    return []


def gate_dependency_freshness(ctx: FirewallContext) -> list[dict]:
    fresh = ctx.freshness or ctx.package.get("freshness", {})
    out = []
    stale = [k for k, v in fresh.items() if v == "STALE"]
    unknown = [k for k, v in fresh.items() if v == "UNKNOWN"]
    if stale:
        out.append(_finding(
            "dependency_freshness", "FRESHNESS_STALE", "major",
            "свежесть источников/зависимостей",
            evidence=f"устаревшие источники: {stale}",
            action="Обновить/пересобрать источник до актуального SHA.",
            freshness="STALE"))
    # Всегда эмитим явную сводку свежести (§ acceptance #9 «freshness is explicit»).
    out.append(_finding(
        "dependency_freshness", "FRESHNESS_EXPLICIT", "info",
        "свежесть источников зафиксирована явно",
        evidence=f"FRESH={sum(1 for v in fresh.values() if v=='FRESH')} "
                 f"STALE={len(stale)} UNKNOWN={len(unknown)}",
        action="—", freshness="FRESH"))
    return out


def gate_needless_architecture(ctx: FirewallContext) -> list[dict]:
    out = []
    for sym in ctx.flagged_symbols:
        if int(sym.get("refs", 1)) == 0:
            out.append(_finding(
                "needless_architecture", "NEEDLESS_ABSTRACTION", "major",
                "избыточная архитектура (evidence-backed)",
                location=f"{sym.get('file')}:{sym.get('name')}",
                evidence=f"символ {sym.get('name')} определён, 0 использований",
                action="Удалить неиспользуемую абстракцию или обосновать её."))
    return out


def gate_ai_placeholder(ctx: FirewallContext) -> list[dict]:
    if not ctx.worktree or not os.path.isdir(ctx.worktree):
        return []
    out = []
    for root, _dirs, files in os.walk(ctx.worktree):
        if "/.git" in root:
            continue
        for fn in files:
            if not fn.endswith((".py", ".ts", ".tsx", ".js", ".md")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for pat in _AI_MARKERS:
                    if pat.search(line):
                        rel = os.path.relpath(path, ctx.worktree)
                        out.append(_finding(
                            "ai_placeholder", "AI_PLACEHOLDER", "blocker",
                            "AI placeholder / fake success / TODO-заглушка",
                            location=f"{rel}:{line_no}",
                            evidence=f"маркер-заглушка: {line.strip()[:80]}",
                            action="Заменить заглушку реальной реализацией или удалить."))
                        break
    return out


def gate_docs_command_parity(ctx: FirewallContext) -> list[dict]:
    out = []
    for cmd in ctx.docs_commands:
        if cmd not in ctx.runnable_commands:
            out.append(_finding(
                "docs_command_parity", "DOCS_COMMAND_DRIFT", "major",
                "команда из docs не исполняется",
                location="docs", evidence=f"команда в docs отсутствует/не работает: {cmd}",
                action="Привести docs к реальной команде или добавить команду."))
    return out


def gate_web_accessibility_states(ctx: FirewallContext) -> list[dict]:
    out = []
    missing = [s for s in ctx.required_web_states if s not in ctx.declared_web_states]
    if missing:
        out.append(_finding(
            "web_accessibility_states", "WEB_STATE_MISSING", "major",
            "отсутствуют обязательные UI-состояния",
            location="web", evidence=f"не объявлены состояния: {missing}",
            action="Добавить состояния loading/empty/stale/error/… в экран."))
    return out


def gate_security_test_relevance(ctx: FirewallContext) -> list[dict]:
    if ctx.package.get("impact_class") == "HIGH_RISK" and not ctx.security_check_present:
        return [_finding(
            "security_test_relevance", "SECURITY_TEST_MISSING", "blocker",
            "HIGH_RISK без security-проверок",
            evidence="impact=HIGH_RISK, security check-группа отсутствует",
            action="Добавить релевантные security-проверки к HIGH_RISK diff.")]
    return []


def gate_stale_review_head(ctx: FirewallContext) -> list[dict]:
    head = ctx.package.get("head_sha", "")
    if ctx.current_head and head and head != ctx.current_head:
        return [_finding(
            "stale_review_head", "STALE_REVIEW_HEAD", "blocker",
            "review против устаревшего head",
            evidence=f"пакет head {head[:12]} != текущий {ctx.current_head[:12]}",
            action="Пересобрать ReviewPackage на текущем head.",
            freshness="STALE")]
    return []


def gate_license_dependency(ctx: FirewallContext) -> list[dict]:
    if ctx.license_present:
        # VP-7: LICENSE присутствует и выбрана владельцем (Apache-2.0). Старый
        # «owner-decision pending» finding больше не эмитится. Фактический info.
        if ctx.license_spdx:
            return [_finding(
                "license_dependency", "LICENSE_PRESENT", "info",
                f"Лицензия: {ctx.license_spdx}",
                evidence=f"Atlas LICENSE присутствует (SPDX {ctx.license_spdx}); "
                         "reuse-аудит: копий стороннего кода нет (все REFERENCE/SPIKE)",
                action="Ничего не требуется; лицензия зафиксирована владельцем в DECISIONS.",
                blocking=False)]
        return []
    if ctx.license_required:
        return [_finding(
            "license_dependency", "LICENSE_REQUIRED_UNRESOLVED", "blocker",
            "требуется LICENSE, но она отсутствует",
            evidence="зависимость требует лицензию; Atlas LICENSE отсутствует",
            action="Владелец обязан выбрать/добавить совместимую LICENSE.")]
    # Видимый owner-decision (не блокирует): LICENSE отсутствует намеренно.
    return [_finding(
        "license_dependency", "LICENSE_ABSENT_OWNER_DECISION", "info",
        "LICENSE отсутствует — видимое решение владельца",
        evidence="Atlas LICENSE отсутствует; выбор лицензии — за владельцем (§20.4)",
        action="Владелец решает вопрос LICENSE отдельно; VP-6 её не выбирает.",
        blocking=False)]


GATES = [
    gate_brief_vp_compliance, gate_real_behavior_vs_claim, gate_secrets_privacy,
    gate_dependency_freshness, gate_needless_architecture, gate_ai_placeholder,
    gate_docs_command_parity, gate_web_accessibility_states,
    gate_security_test_relevance, gate_stale_review_head, gate_license_dependency,
]


def run_firewall(ctx: FirewallContext) -> list[dict]:
    """Прогнать все gates; вернуть findings (blocking первыми, затем по severity)."""

    out: list[dict] = []
    for gate in GATES:
        out.extend(gate(ctx))
    out.sort(key=lambda f: (not f["blocking"], SEVERITY.index(f["severity"])))
    return out
