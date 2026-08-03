"""STANDARD merge gate (Master Spec §20.2).

Merge в base без повторного owner-вопроса разрешён **только если истинны все 11
условий**. Протухший PASS/CI с прежнего head → deny. Оценка fail-closed:
первый несоблюдённый пункт даёт стабильный reason-код и точное next action;
before/after Audit фиксируется всегда.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import audit, autonomy, emergency
from .autonomy import Capability

# Стабильные reason-коды merge gate.
G_PERMITTED = "MERGE_PERMITTED"
G_EMERGENCY = "EMERGENCY_STOP"
G_GRANT = "GRANT_DENIED"  # включает repo/base/env/capability/expiry/revoke/budget
G_BASELINE = "BASELINE_UNKNOWN"
G_SCOPE = "DIFF_OUT_OF_SCOPE"
G_NOT_PASS = "REVIEWER_NOT_PASS"
G_BLOCKING = "BLOCKING_QUALITY_FINDING"
G_STALE_REVIEW = "STALE_REVIEW_HEAD"
G_INVALID_REVIEW = "REVIEW_PACKAGE_INVALID"
G_STALE_CI = "STALE_OR_FAILING_CI"
G_NOT_MERGEABLE = "PR_NOT_MERGEABLE"
G_OWNER_GATE = "OWNER_GATE_PENDING"

_NEXT: dict[str, str] = {
    G_PERMITTED: "Все 11 условий соблюдены — bounded squash-merge допустим.",
    G_EMERGENCY: "Активен Emergency Stop — снимите его явным owner-resume.",
    G_GRANT: "Grant не разрешает merge в этот repo/base/env — исправьте scope grant.",
    G_BASELINE: "Baseline неизвестен — соберите baseline перед merge.",
    G_SCOPE: "Diff выходит за scope активного VP — сузьте изменения или обновите envelope.",
    G_NOT_PASS: "Нет независимого Reviewer PASS — дождитесь PASS текущего head.",
    G_BLOCKING: "Есть blocking Quality finding — устраните перед merge.",
    G_STALE_REVIEW: "ReviewPackage/PASS с прежнего head — пересоберите на текущем SHA.",
    G_INVALID_REVIEW: "ReviewPackage невалиден (INVALID_EVIDENCE) — пересоберите.",
    G_STALE_CI: "CI не зелёный для текущего SHA — дождитесь green checks именно этого head.",
    G_NOT_MERGEABLE: "PR не mergeable — обновите ветку/разрешите конфликты.",
    G_OWNER_GATE: "Остался owner-гейт — требуется явное решение владельца.",
}


def next_action(code: str) -> str:
    return _NEXT.get(code, "Проверьте условия merge gate.")


@dataclass
class MergeRequest:
    repo: str
    base: str
    branch: str
    head_sha: str                    # фактический текущий head ветки
    project_id: str
    grant_id: str
    environment: str = ""
    review_package: dict = field(default_factory=dict)  # ReviewPackage.to_dict()
    quality_report: dict = field(default_factory=dict)  # {verdict, blocking_count,...}
    checks: dict = field(default_factory=dict)          # {head_sha, state}
    mergeability: dict = field(default_factory=dict)     # {mergeable, state}
    baseline_known: bool = True
    diff_in_scope: bool = True
    owner_gate_pending: bool = False
    pr_number: int = 0


@dataclass
class MergeGateDecision:
    permitted: bool
    reason_code: str
    next_action: str
    conditions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"permitted": self.permitted, "reason_code": self.reason_code,
                "next_action": self.next_action, "conditions": self.conditions}


def evaluate_merge(req: MergeRequest, *, correlation_id: str = "") -> MergeGateDecision:
    """Оценить STANDARD merge gate. Возвращает решение с покондиционным списком."""
    audit.record("merge.gate.before",
                 f"repo={req.repo} base={req.base} head={req.head_sha[:12]} pr=#{req.pr_number}",
                 correlation_id=correlation_id)

    conditions: list[dict] = []

    def _fail(code: str, detail: str = "") -> MergeGateDecision:
        conditions.append({"code": code, "ok": False, "detail": detail})
        audit.record("merge.gate.after", f"decision=DENY code={code} head={req.head_sha[:12]}",
                     correlation_id=correlation_id)
        return MergeGateDecision(False, code, next_action(code), conditions)

    def _ok(code: str, detail: str = "") -> None:
        conditions.append({"code": code, "ok": True, "detail": detail})

    # 0. Emergency Stop блокирует любой merge (новый job).
    if emergency.is_active():
        return _fail(G_EMERGENCY)
    _ok(G_EMERGENCY, "Emergency Stop не активен")

    # 1+2. repo/base allowlist + активный unexpired grant разрешает merge.
    dec = autonomy.evaluate(Capability.MERGE_AFTER_PASS.value, grant_id=req.grant_id,
                            project_id=req.project_id, repo=req.repo, base=req.base,
                            environment=req.environment or None)
    if not dec.permitted:
        conditions.append({"code": G_GRANT, "ok": False,
                           "detail": f"{dec.reason_code}: {dec.detail}"})
        audit.record("merge.gate.after",
                     f"decision=DENY code={G_GRANT}/{dec.reason_code}",
                     correlation_id=correlation_id)
        return MergeGateDecision(False, G_GRANT, dec.next_action, conditions)
    _ok(G_GRANT, f"grant={dec.grant_id}")

    # 3. baseline известен.
    if not req.baseline_known:
        return _fail(G_BASELINE)
    _ok(G_BASELINE)

    # 4. diff в scope VP.
    if not req.diff_in_scope:
        return _fail(G_SCOPE)
    _ok(G_SCOPE)

    # 7a. ReviewPackage валиден (не INVALID_EVIDENCE).
    rp = req.review_package or {}
    if rp.get("status") not in ("valid", None, ""):
        return _fail(G_INVALID_REVIEW, f"status={rp.get('status')}")
    _ok(G_INVALID_REVIEW, "ReviewPackage valid")

    # 5. независимый Reviewer PASS.
    qr = req.quality_report or {}
    if qr.get("verdict") != "PASS":
        return _fail(G_NOT_PASS, f"verdict={qr.get('verdict')}")
    _ok(G_NOT_PASS, "verdict=PASS")

    # 6. нет blocking Quality finding.
    if int(qr.get("blocking_count", 0) or 0) > 0:
        return _fail(G_BLOCKING, f"blocking={qr.get('blocking_count')}")
    _ok(G_BLOCKING, "blocking=0")

    # 7b. ReviewPackage привязан к точному текущему head, а QualityReport — к
    #     этому ReviewPackage (QR не несёт head_sha, привязка идёт QR→RP→head).
    #     Fail-closed (VP-7 D-fix): пустой rp.head_sha, пустой rp.id или несовпадение
    #     QR.review_package_id → deny. «Пусто» ≠ «актуально».
    rp_head = rp.get("head_sha", "")
    if not rp_head or rp_head != req.head_sha:
        return _fail(G_STALE_REVIEW,
                     f"rp_head={rp_head[:12] or '(пусто)'} != {req.head_sha[:12]}")
    rp_id = rp.get("id", "")
    qr_rp = qr.get("review_package_id", "")
    if not rp_id or not qr_rp or qr_rp != rp_id:
        return _fail(G_STALE_REVIEW,
                     f"QualityReport не привязан к текущему ReviewPackage "
                     f"(qr.rp={qr_rp or '(пусто)'} != rp.id={rp_id or '(пусто)'})")
    _ok(G_STALE_REVIEW, f"head={req.head_sha[:12]} rp={rp_id[:12]}")

    # 8. required CI checks зелёные ИМЕННО для текущего head. Fail-closed:
    #    отсутствующий ci.head_sha НЕ подменяется req.head_sha → deny.
    ci = req.checks or {}
    if ci.get("state") != "GREEN" or ci.get("head_sha", "") != req.head_sha:
        return _fail(G_STALE_CI,
                     f"state={ci.get('state')} ci_head={ci.get('head_sha','') [:12] or '(пусто)'}")
    _ok(G_STALE_CI, "checks GREEN на текущем head")

    # 9. PR mergeable.
    mg = req.mergeability or {}
    if not mg.get("mergeable"):
        return _fail(G_NOT_MERGEABLE, f"state={mg.get('state')}")
    _ok(G_NOT_MERGEABLE, "mergeable")

    # 10. нет остаточного owner-гейта.
    if req.owner_gate_pending:
        return _fail(G_OWNER_GATE)
    _ok(G_OWNER_GATE, "нет owner-гейта")

    # 11. audit after (permit).
    conditions.append({"code": G_PERMITTED, "ok": True, "detail": "все условия соблюдены"})
    audit.record("merge.gate.after",
                 f"decision=PERMIT head={req.head_sha[:12]} pr=#{req.pr_number}",
                 correlation_id=correlation_id)
    return MergeGateDecision(True, G_PERMITTED, next_action(G_PERMITTED), conditions)
