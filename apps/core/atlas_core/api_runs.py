"""API VP-5 Runs (Master Spec §25, §38).

Тонкий типизированный слой над :class:`RunService`. Тело запроса — данные
(§30.2): не исполняется и не расширяет права. Мутации принимают
``Idempotency-Key`` и optimistic ``expected_version``; ошибки — стабильный код +
correlation ID. Сырой provider-payload/HTML не рендерится.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .runs import RunError, RunService

router = APIRouter(prefix="/api/v1", tags=["runs"])
_runs = RunService()


def _err(exc: Exception, correlation_id: str | None = None) -> JSONResponse:
    if isinstance(exc, RunError):
        body = exc.to_dict()
        if correlation_id:
            body["correlation_id"] = correlation_id
        return JSONResponse({"error": body}, status_code=exc.http)
    raise exc


class CreateRunRequest(BaseModel):
    project_id: str
    work_order_id: str = ""
    vp_key: str = ""
    preset: str = ""
    owner_override: dict | None = None
    dedup_key: str = ""
    correlation_id: str = ""


class TransitionRequest(BaseModel):
    expected_version: int
    reason: str = ""


@router.get("/runs")
def list_runs(project_id: str | None = Query(None), state: str | None = Query(None),
              limit: int = Query(50)) -> JSONResponse:
    return JSONResponse({"runs": _runs.list_runs(project_id=project_id, state=state, limit=limit)})


@router.post("/runs")
def create_run(req: CreateRunRequest,
               idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> JSONResponse:
    try:
        run = _runs.create_run(req.project_id, work_order_id=req.work_order_id, vp_key=req.vp_key,
                               preset=req.preset, owner_override=req.owner_override,
                               dedup_key=req.dedup_key, idempotency_key=idempotency_key or "",
                               correlation_id=req.correlation_id)
        return JSONResponse({"run": run}, status_code=201)
    except Exception as exc:  # noqa: BLE001
        return _err(exc, req.correlation_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    try:
        return JSONResponse({"run": _runs.get_run(run_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, after_seq: int = Query(0), limit: int = Query(500)) -> JSONResponse:
    return JSONResponse({"events": _runs.events(run_id, after_seq=after_seq, limit=limit)})


@router.get("/runs/{run_id}/router")
def run_router(run_id: str) -> JSONResponse:
    return JSONResponse({"decisions": _runs.router_decisions(run_id),
                         "sessions": _runs.provider_sessions(run_id)})


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str, req: TransitionRequest) -> JSONResponse:
    try:
        _runs.record_pause(run_id, "pause", reason=req.reason)
        return JSONResponse({"run": _runs.transition(run_id, "PAUSED",
                            expected_version=req.expected_version, reason=req.reason or "owner pause")})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, req: TransitionRequest) -> JSONResponse:
    try:
        _runs.record_pause(run_id, "resume", reason=req.reason)
        return JSONResponse({"run": _runs.transition(run_id, "RUNNING",
                            expected_version=req.expected_version, reason=req.reason or "owner resume")})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, req: TransitionRequest) -> JSONResponse:
    try:
        return JSONResponse({"run": _runs.transition(run_id, "CANCELLED",
                            expected_version=req.expected_version, reason=req.reason or "owner cancel")})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
