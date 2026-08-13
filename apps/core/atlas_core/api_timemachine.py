"""API VP-7 Time Machine: checkpoints, verify, compare, replay/restore/rollback
preview (Master Spec §21, §25).

Read-only маршруты (list/get/verify/compare/preview) не мутируют состояние.
Destructive rollback остаётся недоступным без отдельного grant.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import timemachine as TM

router = APIRouter(prefix="/api/v1", tags=["timemachine"])


@router.get("/checkpoints")
def list_checkpoints(project_id: str | None = Query(None),
                     limit: int = Query(100, ge=1, le=500)) -> JSONResponse:
    return JSONResponse({"checkpoints": TM.list_checkpoints(project_id=project_id, limit=limit)})


@router.get("/checkpoints/compare")
def compare(a: str = Query(...), b: str = Query(...)) -> JSONResponse:
    try:
        return JSONResponse({"compare": TM.compare(a, b)})
    except TM.InvalidCheckpointError as exc:
        # call-12 fix (finding 2): подделанный/протухший checkpoint → стабильный
        # INVALID_EVIDENCE (409), а не необработанное исключение/HTTP 500.
        return JSONResponse({"error": {"code": "INVALID_EVIDENCE", "reason": str(exc)}},
                            status_code=409)
    except TM.TimeMachineError as exc:
        return JSONResponse({"error": {"code": exc.code, "reason": exc.message}}, status_code=404)


@router.get("/checkpoints/{checkpoint_id}")
def get_checkpoint(checkpoint_id: str) -> JSONResponse:
    cp = TM.get_checkpoint(checkpoint_id)
    if cp is None:
        return JSONResponse({"error": {"code": "NOT_FOUND", "reason": "checkpoint не найден"}},
                            status_code=404)
    ok, reason = TM.verify_checkpoint(checkpoint_id)
    return JSONResponse({"checkpoint": cp, "verified": ok, "invalid_reason": reason})


class GrantRef(BaseModel):
    grant_id: str = ""
    profile_alias: str | None = None


@router.post("/checkpoints/{checkpoint_id}/replay-preview")
def replay_preview(checkpoint_id: str, req: GrantRef) -> JSONResponse:
    try:
        return JSONResponse({"preview": TM.replay_preview(
            checkpoint_id, grant_id=req.grant_id, profile_alias=req.profile_alias)})
    except TM.InvalidCheckpointError as exc:
        return JSONResponse({"error": {"code": "INVALID_EVIDENCE", "reason": str(exc)}},
                            status_code=409)


class ReplayRequest(BaseModel):
    grant_id: str = ""
    profile_alias: str | None = None
    repo: str | None = None  # опциональное ИМЯ repo (owner/name) для сверки со scope;
    # НЕ filesystem-путь — доверенный checkout выводится из durable-состояния проекта.


@router.post("/checkpoints/{checkpoint_id}/replay")
def replay(checkpoint_id: str, req: ReplayRequest) -> JSONResponse:
    """call-11 fix (finding 2): ПРОИЗВОДСТВЕННЫЙ replay — создаёт новый Run И
    материализует новую безопасную ветку из состояния (head_sha) checkpoint (не
    только preview, не «Run без ветки»). Доверенный checkout-путь выводится из
    durable Atlas-состояния проекта (Worktree/Project), а НЕ из аргументов каллера.
    Требует свежий valid grant с repo_write в выведенном scope; Emergency Stop
    запрещает; source-ветка не переписывается; verify хешей; credentials/transcript
    не восстанавливаются."""
    try:
        res = TM.replay_production(checkpoint_id, grant_id=req.grant_id,
                                  profile_alias=req.profile_alias, repo=req.repo,
                                  actor="owner")
        return JSONResponse({"replay": res})
    except TM.InvalidCheckpointError as exc:
        return JSONResponse({"error": {"code": "INVALID_EVIDENCE", "reason": str(exc)}},
                            status_code=409)
    except TM.TimeMachineError as exc:
        return JSONResponse({"error": {"code": exc.code, "reason": exc.message}}, status_code=409)


@router.post("/checkpoints/{checkpoint_id}/restore-preview")
def restore_preview(checkpoint_id: str) -> JSONResponse:
    try:
        return JSONResponse({"preview": TM.restore_state_preview(checkpoint_id)})
    except TM.InvalidCheckpointError as exc:
        return JSONResponse({"error": {"code": "INVALID_EVIDENCE", "reason": str(exc)}},
                            status_code=409)


@router.post("/checkpoints/{checkpoint_id}/rollback-preview")
def rollback_preview(checkpoint_id: str, req: GrantRef) -> JSONResponse:
    try:
        return JSONResponse({"preview": TM.rollback_preview(checkpoint_id, grant_id=req.grant_id)})
    except TM.InvalidCheckpointError as exc:
        return JSONResponse({"error": {"code": "INVALID_EVIDENCE", "reason": str(exc)}},
                            status_code=409)
