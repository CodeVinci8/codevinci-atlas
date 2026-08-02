"""QualityService VP-6 (Master Spec §18, §39).

Оркестрация Review & Quality: сверка ReviewPackage (SHA-bound), прогон Quality
Firewall, персист findings, вычисление вердикта, сборка объясняющего
QualityReport, ограниченный fix-loop (второй REVISE → BLOCKED), read-only manual
audit и waiver (не обходит non-waivable-правила).

Вердикты (§18.2): ``PASS``, ``REVISE``, ``BLOCKED``, ``OWNER_REQUIRED``,
``INVALID_EVIDENCE``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import audit
from .db import session_scope
from .firewall import NON_WAIVABLE_CODES, FirewallContext, run_firewall
from .ids import new_id
from .impact import CHECK_GROUPS
from .orm import FixLoop, ManualAudit, QualityFinding, QualityReport, Waiver
from .productmap import content_hash
from .redaction import redact
from .reviewpkg import ReviewFacts, validate_review_package

VERDICTS = ("PASS", "REVISE", "BLOCKED", "OWNER_REQUIRED", "INVALID_EVIDENCE")

# Жёсткие блокеры → BLOCKED (не fixable одним loop).
_HARD_BLOCK = {"SECRET_DETECTED", "CREDENTIAL_EXPOSURE", "STALE_REVIEW_HEAD",
               "ONE_WRITER_VIOLATION", "UNAUTHORIZED_EXTERNAL_ACTION"}
# Требуют владельца → OWNER_REQUIRED.
_OWNER_BLOCK = {"LICENSE_REQUIRED_UNRESOLVED"}


@dataclass
class ReviewOutcome:
    verdict: str
    gate_fired: str
    findings: list[dict]
    report: dict
    review_package_id: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "gate_fired": self.gate_fired,
            "findings": self.findings, "report": self.report,
            "review_package_id": self.review_package_id,
        }


class QualityService:
    # --- persist findings --------------------------------------------------
    def persist_findings(self, review_package_id: str, project_id: str,
                         findings: list[dict]) -> list[str]:
        ids = []
        with session_scope() as s:
            for f in findings:
                fid = new_id("qfnd")
                s.add(QualityFinding(
                    id=fid, review_package_id=review_package_id, project_id=project_id,
                    gate=f.get("gate", ""), code=f.get("code", ""),
                    severity=f.get("severity", "minor"),
                    criterion=f.get("criterion", ""), location=f.get("location", ""),
                    evidence=redact(f.get("evidence", ""))[:400], action=f.get("action", ""),
                    blocking=bool(f.get("blocking")), source=f.get("source", "firewall"),
                    freshness=f.get("freshness", "")))
                ids.append(fid)
            s.commit()
        return ids

    # --- verdict -----------------------------------------------------------
    def compute_verdict(self, findings: list[dict], *, package_valid: bool,
                        invalid_code: str = "") -> tuple[str, str]:
        if not package_valid:
            return "INVALID_EVIDENCE", invalid_code or "INVALID_EVIDENCE"
        blocking = [f for f in findings if f.get("blocking") and not f.get("waived")]
        if not blocking:
            return "PASS", "none"
        codes = {f.get("code") for f in blocking}
        if codes & _HARD_BLOCK:
            top = next(f for f in blocking if f.get("code") in _HARD_BLOCK)
            return "BLOCKED", f"{top['gate']}:{top['code']}"
        if codes & _OWNER_BLOCK:
            top = next(f for f in blocking if f.get("code") in _OWNER_BLOCK)
            return "OWNER_REQUIRED", f"{top['gate']}:{top['code']}"
        top = blocking[0]
        return "REVISE", f"{top['gate']}:{top['code']}"

    # --- QualityReport -----------------------------------------------------
    def build_report(self, package: dict, verdict: str, gate_fired: str,
                     findings: list[dict], *, run_id: str = "",
                     actor: str = "reviewer") -> dict:
        claims = package.get("claims", [])
        impact = package.get("impact_class", "") or "LOCAL"
        groups = CHECK_GROUPS.get(impact, [])
        blocking = [f for f in findings if f.get("blocking") and not f.get("waived")]
        proved = [f["criterion"] for f in findings if f["severity"] == "info"]
        disproved = [f"{f['criterion']} ({f['code']})" for f in blocking]

        evidence_summary = (
            f"Проверено {len(findings)} findings; блокирующих {len(blocking)}. "
            + (f"Опровергнуто: {disproved}. " if disproved else "Опровержений нет. ")
            + (f"Подтверждено (info): {proved[:5]}." if proved else ""))
        sufficiency = (
            f"Impact={impact} → выбраны проверки {groups}. Достаточно, т.к. класс "
            f"покрывает затронутые поверхности; полная регрессия "
            + ("включена (HIGH_RISK/risk-trigger)." if impact == "HIGH_RISK"
               else "не требуется без видимого risk-повода (§18.6)."))
        next_action = {
            "PASS": "Owner: финальный обзор артефакта/экрана; можно продвигать VP.",
            "REVISE": "Один focused fix по блокирующему finding → impacted checks → "
                      "независимый re-review.",
            "BLOCKED": "Устранить жёсткий блокер (secrets/stale-head/one-writer); "
                       "повторный REVISE недопустим.",
            "OWNER_REQUIRED": "Требуется решение владельца (напр. LICENSE).",
            "INVALID_EVIDENCE": "Пересобрать ReviewPackage на текущем факте (SHA/"
                                "артефакт/evidence/Work Order).",
        }[verdict]
        stop_reason = (
            "Полировка прекращается: все критерии VP покрыты выбранными по impact "
            "проверками; дальнейшие правки вне блокирующих findings не меняют вердикт "
            "и не оправданы risk-поводом (anti-endless-polish, §18.8/§45).")

        report_payload = {
            "review_package_hash": package.get("content_hash"), "verdict": verdict,
            "claims": claims, "evidence_summary": evidence_summary,
            "gate_fired": gate_fired, "sufficiency_reason": sufficiency,
            "next_action": next_action, "stop_reason": stop_reason,
            "blocking_count": len(blocking), "findings_count": len(findings),
        }
        ch = content_hash(report_payload)
        rid = new_id("qrep")
        import json as _json
        with session_scope() as s:
            row = QualityReport(
                id=rid, review_package_id=package["id"], project_id=package["project_id"],
                run_id=run_id, verdict=verdict, claims_json=_json.dumps(claims, ensure_ascii=False),
                evidence_summary=evidence_summary, gate_fired=gate_fired,
                sufficiency_reason=sufficiency, next_action=next_action,
                stop_reason=stop_reason, blocking_count=len(blocking),
                findings_count=len(findings), content_hash=ch, actor=actor)
            s.add(row)
            s.commit()
            out = row.to_dict()
        audit.record("review.report.built", f"rpkg={package['id']} verdict={verdict}",
                     actor=actor)
        return out

    # --- полный проход review ----------------------------------------------
    def review(self, package: dict, ctx: FirewallContext, facts: ReviewFacts, *,
               run_id: str = "", actor: str = "reviewer") -> ReviewOutcome:
        """Один проход: сверка пакета → firewall → findings → вердикт → report."""

        pkg_id = package["id"]
        valid, code, reason = validate_review_package(pkg_id, facts)
        findings: list[dict] = []
        if not valid:
            # Пакет невалиден фактом → INVALID_EVIDENCE. Firewall не запускаем над
            # непроверяемым содержимым, но фиксируем finding для UI.
            findings = [{
                "gate": "review_package", "code": code, "severity": "blocker",
                "criterion": "ReviewPackage валиден по факту", "location": pkg_id,
                "evidence": reason, "action": "Пересобрать пакет на текущем факте.",
                "blocking": True, "source": "reviewpkg", "freshness": "STALE"}]
        else:
            findings = run_firewall(ctx)
        self.persist_findings(pkg_id, package["project_id"], findings)
        verdict, gate_fired = self.compute_verdict(
            findings, package_valid=valid, invalid_code=code)
        report = self.build_report(package, verdict, gate_fired, findings,
                                   run_id=run_id, actor=actor)
        return ReviewOutcome(verdict=verdict, gate_fired=gate_fired, findings=findings,
                             report=report, review_package_id=pkg_id)

    # --- fix-loop (§18.8) --------------------------------------------------
    def record_fix_loop(self, review_package_id: str, run_id: str, project_id: str,
                        attempt: int, verdict: str, *, blocked: bool = False,
                        fix_work_order_id: str = "") -> str:
        fid = new_id("fixl")
        with session_scope() as s:
            s.add(FixLoop(id=fid, review_package_id=review_package_id, run_id=run_id,
                          project_id=project_id, attempt=attempt, verdict=verdict,
                          blocked=blocked, fix_work_order_id=fix_work_order_id))
            s.commit()
        return fid

    def evaluate_fix_loop(self, review_package_id: str, run_id: str, project_id: str,
                          attempt: int, verdict: str) -> tuple[str, bool]:
        """Контроллер fix-loop: второй REVISE → BLOCKED (§18.8).

        attempt=1 → первый review; attempt=2 → после одного focused fix. Возвращает
        ``(final_verdict, blocked)``.
        """

        if verdict == "REVISE" and attempt >= 2:
            final, blocked = "BLOCKED", True
        else:
            final, blocked = verdict, verdict == "BLOCKED"
        self.record_fix_loop(review_package_id, run_id, project_id, attempt, verdict,
                             blocked=blocked)
        if blocked and final == "BLOCKED" and verdict == "REVISE":
            audit.record("review.fixloop.second_revise_blocked",
                         f"rpkg={review_package_id} attempt={attempt}")
        return final, blocked

    # --- manual audit (read-only) ------------------------------------------
    def manual_audit(self, review_package_id: str, project_id: str, target: str,
                     scope: str, result: dict, *, actor: str = "owner",
                     correlation_id: str = "") -> dict:
        """Записать read-only manual audit. НЕ мутирует код (только чтение+запись)."""

        import json as _json
        aid = new_id("maud")
        findings_n = len(result.get("findings", []))
        with session_scope() as s:
            row = ManualAudit(
                id=aid, review_package_id=review_package_id, project_id=project_id,
                target=target, scope=scope, read_only=True,
                result_json=_json.dumps(result, ensure_ascii=False),
                findings_count=findings_n, actor=actor, correlation_id=correlation_id)
            s.add(row)
            s.commit()
            out = row.to_dict()
        audit.record("review.manual_audit", f"rpkg={review_package_id} target={target} "
                     f"read_only=True", actor=actor)
        return out

    # --- waiver ------------------------------------------------------------
    def waiver(self, review_package_id: str, finding_id: str, project_id: str, *,
               reason: str, scope: str, actor: str, expiry: str,
               review_condition: str) -> dict:
        """Создать waiver. Не обходит non-waivable-правила (§18.4).

        Возвращает dict; ``waivable=False`` + ``rejected_code`` если правило
        non-waivable — при этом finding остаётся блокирующим.
        """

        with session_scope() as s:
            finding = s.get(QualityFinding, finding_id)
            f_code = finding.code if finding else ""
        waivable = f_code not in NON_WAIVABLE_CODES
        # Обязательные поля waiver.
        if not (reason and scope and actor and expiry and review_condition):
            waivable = False
            rejected = "MISSING_REQUIRED_FIELDS"
        else:
            rejected = "" if waivable else "NON_WAIVABLE_RULE"
        wid = new_id("waiv")
        audit_ref = audit.record(
            "review.waiver" + ("" if waivable else ".rejected"),
            f"rpkg={review_package_id} finding={finding_id} code={f_code} "
            f"waivable={waivable}", actor=actor)
        with session_scope() as s:
            row = Waiver(id=wid, review_package_id=review_package_id, finding_id=finding_id,
                         project_id=project_id, reason=redact(reason)[:400], scope=scope,
                         actor=actor, expiry=expiry, review_condition=review_condition,
                         audit_ref=audit_ref, waivable=waivable, rejected_code=rejected)
            s.add(row)
            # Помечаем finding waived ТОЛЬКО если правило waivable.
            if waivable and finding_id:
                f = s.get(QualityFinding, finding_id)
                if f is not None:
                    f.waived = True
            s.commit()
            out = row.to_dict()
        return out

    # --- запросы для API ---------------------------------------------------
    def list_findings(self, review_package_id: str) -> list[dict]:
        from sqlalchemy import select
        with session_scope() as s:
            rows = s.execute(select(QualityFinding).where(
                QualityFinding.review_package_id == review_package_id)
                .order_by(QualityFinding.blocking.desc(), QualityFinding.created_at)).scalars().all()
            return [r.to_dict() for r in rows]

    def latest_report(self, review_package_id: str) -> dict | None:
        from sqlalchemy import select
        with session_scope() as s:
            row = s.execute(select(QualityReport).where(
                QualityReport.review_package_id == review_package_id)
                .order_by(QualityReport.created_at.desc()).limit(1)).scalars().first()
            return row.to_dict() if row else None
