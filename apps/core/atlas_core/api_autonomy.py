"""API VP-7 Autonomy: grants, modes, capabilities, Emergency Stop, merge gate
preview, GitHub delivery state (Master Spec §19, §20, §25).

Только safe-представления. Token/credentials не возвращаются. Оценка grant и
merge gate — fail-closed со стабильными reason-кодами.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from . import autonomy, emergency
from .autonomy import ALL_CAPABILITIES, MODES, VP7_HARD_DENIED
from .db import session_scope
from .merge_gate import MergeRequest, evaluate_merge
from .orm import GithubDelivery

router = APIRouter(prefix="/api/v1", tags=["autonomy"])


def _capability_matrix() -> list[dict]:
    """Матрица capabilities: раздельно, с пометкой недоступных через автономию."""
    labels = {
        "repo_read": "Чтение репозитория", "repo_write": "Запись в репозиторий",
        "commands": "Выполнение команд", "deps_install": "Установка зависимостей",
        "commit": "Коммит", "push_feature": "Push feature-ветки",
        "create_pr": "Создание PR", "merge_after_pass": "Merge после PASS",
        "direct_main": "Прямой push в main", "force_push": "Force push",
        "branch_delete": "Удаление ветки", "repo_delete": "Удаление репозитория",
        "production_deploy": "Production deploy", "dns_nginx_tls": "DNS/Nginx/TLS",
        "paid_calls": "Платные вызовы", "cookie_import": "Импорт cookie",
        "destructive_rollback": "Destructive rollback",
    }
    return [{"code": c, "label": labels.get(c, c),
             "available_via_autonomy": c not in VP7_HARD_DENIED,
             "separate_grant": c == "destructive_rollback"}
            for c in ALL_CAPABILITIES]


@router.get("/autonomy/summary")
def autonomy_summary(project_id: str | None = Query(None)) -> JSONResponse:
    grants = autonomy.list_grants(project_id=project_id)
    active = [g for g in grants if g["state"] == "ACTIVE"]
    return JSONResponse({
        "modes": list(MODES),
        "capability_matrix": _capability_matrix(),
        "grants": grants,
        "active_count": len(active),
        "emergency": emergency.status(),
    })


@router.get("/grants")
def list_grants(project_id: str | None = Query(None), state: str | None = Query(None)) -> JSONResponse:
    return JSONResponse({"grants": autonomy.list_grants(project_id=project_id, state=state)})


@router.get("/grants/{grant_id}")
def get_grant(grant_id: str) -> JSONResponse:
    g = autonomy.get_grant(grant_id)
    if g is None:
        return JSONResponse({"error": {"code": "NOT_FOUND", "reason": "grant не найден"}},
                            status_code=404)
    return JSONResponse({"grant": g})


class CreateGrant(BaseModel):
    project_id: str
    mode: str
    capabilities: list[str]
    environment: str = ""
    allowed_repos: list[str] = []
    allowed_bases: list[str] = []
    workspace_allowlist: list[str] = []
    budget: dict = {}
    reason: str = ""
    ttl_seconds: int | None = 3600
    owner_ref: str = "owner"


@router.post("/grants")
def create_grant(req: CreateGrant) -> JSONResponse:
    try:
        g = autonomy.create_grant(
            project_id=req.project_id, mode=req.mode, capabilities=req.capabilities,
            environment=req.environment, allowed_repos=req.allowed_repos,
            allowed_bases=req.allowed_bases, workspace_allowlist=req.workspace_allowlist,
            budget=req.budget, reason=req.reason, ttl_seconds=req.ttl_seconds,
            owner_ref=req.owner_ref)
        return JSONResponse({"grant": g}, status_code=201)
    except ValueError as exc:
        return JSONResponse({"error": {"code": "INVALID", "reason": str(exc)}}, status_code=422)


class RevokeGrant(BaseModel):
    reason: str = ""
    by: str = "owner"
    expected_version: int | None = None


@router.post("/grants/{grant_id}/revoke")
def revoke_grant(grant_id: str, req: RevokeGrant) -> JSONResponse:
    try:
        g = autonomy.revoke_grant(grant_id, by=req.by, reason=req.reason,
                                  expected_version=req.expected_version)
        return JSONResponse({"grant": g})
    except KeyError:
        return JSONResponse({"error": {"code": "NOT_FOUND", "reason": "grant не найден"}},
                            status_code=404)
    except autonomy.ConflictError:
        return JSONResponse({"error": {"code": "VERSION_CONFLICT",
                            "reason": "grant изменён параллельно"}}, status_code=409)


class EvaluateReq(BaseModel):
    capability: str
    grant_id: str | None = None
    project_id: str | None = None
    repo: str | None = None
    base: str | None = None
    environment: str | None = None
    workspace: str | None = None
    expected_version: int | None = None


@router.post("/autonomy/evaluate")
def evaluate(req: EvaluateReq) -> JSONResponse:
    dec = autonomy.evaluate(req.capability, grant_id=req.grant_id, project_id=req.project_id,
                            repo=req.repo, base=req.base, environment=req.environment,
                            workspace=req.workspace, expected_version=req.expected_version)
    return JSONResponse({"decision": dec.to_dict()})


@router.get("/autonomy/emergency")
def emergency_status() -> JSONResponse:
    return JSONResponse({"emergency": emergency.status()})


class EmergencyReq(BaseModel):
    reason: str = ""
    actor: str = "owner"


@router.post("/autonomy/emergency/engage")
def emergency_engage(req: EmergencyReq) -> JSONResponse:
    return JSONResponse({"emergency": emergency.engage(reason=req.reason, actor=req.actor)})


@router.post("/autonomy/emergency/resume")
def emergency_resume(req: EmergencyReq) -> JSONResponse:
    return JSONResponse({"emergency": emergency.resume(actor=req.actor)})


class MergeGatePreview(BaseModel):
    repo: str
    base: str
    branch: str
    head_sha: str
    project_id: str
    grant_id: str
    environment: str = ""
    review_package: dict = {}
    quality_report: dict = {}
    checks: dict = {}
    mergeability: dict = {}
    baseline_known: bool = True
    diff_in_scope: bool = True
    owner_gate_pending: bool = False
    pr_number: int = 0


@router.post("/github/merge-gate/preview")
def merge_gate_preview(req: MergeGatePreview) -> JSONResponse:
    dec = evaluate_merge(MergeRequest(**req.model_dump()))
    return JSONResponse({"gate": dec.to_dict()})


@router.get("/github/deliveries")
def list_deliveries(project_id: str | None = Query(None),
                    limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    with session_scope() as s:
        stmt = select(GithubDelivery).order_by(GithubDelivery.created_at.desc(),
                                               GithubDelivery.id.desc())
        if project_id:
            stmt = stmt.where(GithubDelivery.project_id == project_id)
        rows = s.execute(stmt.limit(limit)).scalars().all()
        return JSONResponse({"deliveries": [r.to_dict() for r in rows]})
