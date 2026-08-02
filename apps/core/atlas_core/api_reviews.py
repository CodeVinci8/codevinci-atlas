"""API VP-6 Review & Quality (Master Spec §18, §25, §27.7).

Safe-представления Review & Quality: ReviewPackage (SHA-bound), findings,
QualityReport, impact, cache-reuse, manual audit, waiver, focused fix Work Order.
Reviewer виден только по safe alias. Ответы не рендерят raw provider payload/HTML;
секреты/email/cookie/raw path не отдаются.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import (
    ImpactAssessment,
    ManualAudit,
    QualityFinding,
    QualityReport,
    ReviewPackage,
    RunRoleStep,
    Waiver,
    WorkOrder,
)
from .quality import QualityService

router = APIRouter(prefix="/api/v1", tags=["reviews"])
_q = QualityService()

_MANUAL_AUDIT_TARGETS = {"project", "vp", "diff", "screen", "dependencies", "docs", "ai_waste"}


def _reviewer_alias(run_id: str) -> str:
    """Safe alias независимого Reviewer из run_role_steps (без email/raw)."""

    if not run_id:
        return ""
    with session_scope() as s:
        row = s.execute(select(RunRoleStep).where(
            RunRoleStep.run_id == run_id, RunRoleStep.role == "reviewer")
            .order_by(RunRoleStep.seq.desc()).limit(1)).scalars().first()
        return row.effective_profile if row else ""


def _latest_report(s, rpkg_id: str) -> QualityReport | None:
    return s.execute(select(QualityReport).where(
        QualityReport.review_package_id == rpkg_id)
        .order_by(QualityReport.created_at.desc()).limit(1)).scalars().first()


def _summary_view(s, pkg: ReviewPackage) -> dict:
    rep = _latest_report(s, pkg.id)
    findings = s.execute(select(QualityFinding).where(
        QualityFinding.review_package_id == pkg.id)).scalars().all()
    blocking = sum(1 for f in findings if f.blocking and not f.waived)
    stale = any(f.freshness == "STALE" for f in findings) or pkg.status == "invalid"
    severities = sorted({f.severity for f in findings})
    return {
        "id": pkg.id, "project_id": pkg.project_id, "run_id": pkg.run_id,
        "vp_key": pkg.vp_key, "wo_key": pkg.wo_key, "branch": pkg.branch,
        "head_sha": pkg.head_sha, "content_hash": pkg.content_hash,
        "status": pkg.status, "impact_class": pkg.impact_class,
        "verdict": rep.verdict if rep else "",
        "blocking_count": blocking, "findings_count": len(findings),
        "severities": severities, "freshness": "STALE" if stale else "FRESH",
        "reviewer_alias": _reviewer_alias(pkg.run_id),
        "created_at": pkg.to_dict()["created_at"],
    }


@router.get("/reviews")
def list_reviews(verdict: str | None = Query(None), severity: str | None = Query(None),
                 project: str | None = Query(None), vp: str | None = Query(None),
                 freshness: str | None = Query(None),
                 limit: int = Query(100, ge=1, le=500)) -> JSONResponse:
    with session_scope() as s:
        stmt = select(ReviewPackage).order_by(ReviewPackage.created_at.desc()).limit(limit)
        if project:
            stmt = stmt.where(ReviewPackage.project_id == project)
        if vp:
            stmt = stmt.where(ReviewPackage.vp_key == vp)
        rows = [_summary_view(s, p) for p in s.execute(stmt).scalars().all()]
    if verdict:
        rows = [r for r in rows if r["verdict"] == verdict]
    if severity:
        rows = [r for r in rows if severity in r["severities"]]
    if freshness:
        rows = [r for r in rows if r["freshness"] == freshness]
    counts: dict = {}
    for r in rows:
        counts[r["verdict"] or "—"] = counts.get(r["verdict"] or "—", 0) + 1
    return JSONResponse({"reviews": rows, "summary": counts})


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> JSONResponse:
    with session_scope() as s:
        pkg = s.get(ReviewPackage, review_id)
        if pkg is None:
            return JSONResponse({"error": {"code": "REVIEW_NOT_FOUND",
                                "reason": f"review не найден: {review_id}"}}, status_code=404)
        package = pkg.to_dict()
        rep = _latest_report(s, review_id)
        findings = [f.to_dict() for f in s.execute(select(QualityFinding).where(
            QualityFinding.review_package_id == review_id)
            .order_by(QualityFinding.blocking.desc(), QualityFinding.created_at)).scalars().all()]
        impact = s.execute(select(ImpactAssessment).where(
            ImpactAssessment.review_package_id == review_id)
            .order_by(ImpactAssessment.created_at.desc()).limit(1)).scalars().first()
        audits = [a.to_dict() for a in s.execute(select(ManualAudit).where(
            ManualAudit.review_package_id == review_id)
            .order_by(ManualAudit.created_at.desc())).scalars().all()]
        waivers = [w.to_dict() for w in s.execute(select(Waiver).where(
            Waiver.review_package_id == review_id)
            .order_by(Waiver.created_at.desc())).scalars().all()]
    # cache-reuse причины — из checks пакета (видимые в ReviewPackage).
    cache_reuse = [c for c in package.get("checks", []) if c.get("cache")]
    return JSONResponse({
        "package": package,
        "report": rep.to_dict() if rep else None,
        "findings": findings,
        "impact": impact.to_dict() if impact else None,
        "manual_audits": audits,
        "waivers": waivers,
        "cache_reuse": cache_reuse,
        "reviewer_alias": _reviewer_alias(package.get("run_id", "")),
    })


class AuditRequest(BaseModel):
    target: str
    scope: str = ""
    findings: list[dict] = []
    note: str = ""


@router.post("/reviews/{review_id}/audit")
def create_manual_audit(review_id: str, req: AuditRequest) -> JSONResponse:
    if req.target not in _MANUAL_AUDIT_TARGETS:
        return JSONResponse({"error": {"code": "INVALID",
                            "reason": f"target вне множества: {req.target}"}}, status_code=422)
    with session_scope() as s:
        pkg = s.get(ReviewPackage, review_id)
        project_id = pkg.project_id if pkg else ""
    # Manual audit — read-only: только чтение+запись наблюдений, код не мутируется.
    result = {"target": req.target, "scope": req.scope,
              "findings": req.findings, "note": req.note[:500]}
    out = _q.manual_audit(review_id, project_id, req.target, req.scope, result)
    return JSONResponse({"manual_audit": out})


class WaiverRequest(BaseModel):
    finding_id: str
    reason: str
    scope: str
    actor: str = "owner"
    expiry: str
    review_condition: str


@router.post("/reviews/{review_id}/waiver")
def create_waiver(review_id: str, req: WaiverRequest) -> JSONResponse:
    with session_scope() as s:
        pkg = s.get(ReviewPackage, review_id)
        project_id = pkg.project_id if pkg else ""
    out = _q.waiver(review_id, req.finding_id, project_id, reason=req.reason,
                    scope=req.scope, actor=req.actor, expiry=req.expiry,
                    review_condition=req.review_condition)
    status = 200 if out["waivable"] else 422
    return JSONResponse({"waiver": out}, status_code=status)


class FixWorkOrderRequest(BaseModel):
    finding_id: str = ""
    goal: str = ""


@router.post("/reviews/{review_id}/fix-work-order")
def create_fix_work_order(review_id: str, req: FixWorkOrderRequest) -> JSONResponse:
    """Создать focused fix Work Order из блокирующего finding (§18.8)."""

    with session_scope() as s:
        pkg = s.get(ReviewPackage, review_id)
        if pkg is None:
            return JSONResponse({"error": {"code": "REVIEW_NOT_FOUND",
                                "reason": review_id}}, status_code=404)
        finding = s.get(QualityFinding, req.finding_id) if req.finding_id else None
        goal = req.goal or (finding.action if finding else "focused fix по review")
        wo_id = new_id("wo")
        wo = WorkOrder(
            id=wo_id, project_id=pkg.project_id, vp_spec_id="", vp_key=pkg.vp_key,
            wo_key=f"fix-{review_id[-6:]}", role="builder", status="draft",
            goal=goal[:500], origin="fix", correlation_id=pkg.correlation_id)
        s.add(wo)
        s.commit()
    _q.record_fix_loop(review_id, pkg.run_id, pkg.project_id, attempt=1,
                       verdict="REVISE", fix_work_order_id=wo_id)
    audit.record("review.fix_work_order.created", f"rpkg={review_id} wo={wo_id}")
    return JSONResponse({"fix_work_order": {"id": wo_id, "goal": goal[:500],
                        "status": "draft", "role": "builder"}})
