"""API Product Map (Master Spec §25, §36, VP-3).

Тонкий типизированный слой над :class:`atlas_core.productmap.ProductMapService`.
Тело запроса — данные (§30.2): текст/ссылки/факты не исполняются и не расширяют
права. Мутации принимают ``Idempotency-Key`` и optimistic ``expected_version``;
ошибки — стабильный код + correlation ID. Каждая мутация пишет Audit в сервисе.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import productmap_export as px
from .productmap import ProductMapError, ProductMapService

router = APIRouter(prefix="/api/v1/projects", tags=["product-map"])
portfolio_router = APIRouter(prefix="/api/v1", tags=["portfolio"])


def _svc() -> ProductMapService:
    return ProductMapService()


def _err(exc: Exception, correlation_id: str | None = None) -> JSONResponse:
    if isinstance(exc, ProductMapError):
        body = exc.to_dict()
        if correlation_id:
            body["correlation_id"] = correlation_id
        return JSONResponse({"error": body}, status_code=exc.http)
    raise exc


# --- модели запросов -------------------------------------------------------
class IntakeRequest(BaseModel):
    idea: str | None = None
    problem: str | None = None
    target_user: str | None = None
    desired_result: str | None = None
    constraints: list[str] | str | None = None
    risks: list[str] | str | None = None
    links: list[str] | None = None
    baseline_refs: list[str] | None = None
    permissions_notes: str | None = None
    parking_suggestions: list[dict] | list[str] | None = None
    facts: list[dict] | None = None


class ReviseRequest(BaseModel):
    changes: dict = {}
    expected_version: int | None = None


class DecideRequest(BaseModel):
    note: str | None = None
    expected_version: int | None = None


class ApproveRequest(BaseModel):
    expected_version: int | None = None
    map_version_id: str | None = None


class MapVersionRequest(BaseModel):
    nodes: list[dict] = []
    edges: list[dict] = []
    expected_version: int | None = None


class ActivateVpRequest(BaseModel):
    vp_key: str


class ParkingRequest(BaseModel):
    title: str
    reason: str | None = None
    return_condition: str | None = None


# --- intake ----------------------------------------------------------------
@router.post("/{project_id}/intake")
def submit_intake(project_id: str, req: IntakeRequest,
                  idem: str | None = Header(None, alias="Idempotency-Key"),
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        state = _svc().submit_intake(project_id, req.model_dump(),
                                     correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(state, status_code=201)


@router.get("/{project_id}/intake")
def get_intake(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"intake": _svc().get_intake(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/product-state")
def get_product_state(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().get_state(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- briefs ----------------------------------------------------------------
@router.get("/{project_id}/briefs")
def list_briefs(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"briefs": _svc().list_briefs(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/briefs/diff")
def diff_briefs(project_id: str, from_: int = Query(..., alias="from"),
                to: int = Query(...)) -> JSONResponse:
    try:
        return JSONResponse(_svc().diff_briefs(project_id, from_, to))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/briefs/{brief_id}")
def get_brief(project_id: str, brief_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().get_brief(project_id, entity_id=brief_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/briefs/{brief_id}/revise")
def revise_brief(project_id: str, brief_id: str, req: ReviseRequest,
                 idem: str | None = Header(None, alias="Idempotency-Key"),
                 corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().revise_brief(project_id, brief_id, req.changes,
                                  expected_version=req.expected_version,
                                  correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.post("/{project_id}/briefs/{brief_id}/approve")
def approve_brief(project_id: str, brief_id: str, req: ApproveRequest,
                  idem: str | None = Header(None, alias="Idempotency-Key"),
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().approve_brief(project_id, brief_id,
                                   expected_version=req.expected_version,
                                   map_version_id=req.map_version_id,
                                   correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


# --- decisions -------------------------------------------------------------
@router.get("/{project_id}/decisions")
def list_decisions(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"decisions": _svc().list_decisions(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/decisions/{decision_id}")
def get_decision(project_id: str, decision_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().get_decision(project_id, decision_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/decisions/{decision_id}/{action}")
def decide(project_id: str, decision_id: str, action: str, req: DecideRequest,
           idem: str | None = Header(None, alias="Idempotency-Key"),
           corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().decide(project_id, decision_id, action, note=req.note or "",
                            expected_version=req.expected_version,
                            correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


# --- parking lot -----------------------------------------------------------
@router.get("/{project_id}/parking-lot")
def list_parking(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"parking_lot": _svc().list_parking(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/parking-lot")
def add_parking(project_id: str, req: ParkingRequest,
                idem: str | None = Header(None, alias="Idempotency-Key"),
                corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().add_parking_item(project_id, req.title, reason=req.reason or "",
                                      return_condition=req.return_condition or "",
                                      correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


# --- project map -----------------------------------------------------------
@router.get("/{project_id}/map")
def get_map(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().get_map(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/map/versions")
def list_map_versions(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"versions": _svc().list_map_versions(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/map/diff")
def diff_maps(project_id: str, from_: int = Query(..., alias="from"),
              to: int = Query(...)) -> JSONResponse:
    try:
        return JSONResponse(_svc().diff_maps(project_id, from_, to))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/map/versions")
def create_map_version(project_id: str, req: MapVersionRequest,
                       idem: str | None = Header(None, alias="Idempotency-Key"),
                       corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().create_map_version(project_id, req.nodes, req.edges,
                                        expected_version=req.expected_version,
                                        correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/vp")
def get_active_vp(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().get_active_vp(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/map/vps/activate")
def activate_vp(project_id: str, req: ActivateVpRequest,
                idem: str | None = Header(None, alias="Idempotency-Key"),
                corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = _svc().activate_vp(project_id, req.vp_key,
                                 correlation_id=corr or "", idempotency_key=idem or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.post("/{project_id}/map/vps/deactivate")
def deactivate_vp(project_id: str,
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        return JSONResponse(_svc().deactivate_vp(project_id, correlation_id=corr or ""))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)


# --- export ----------------------------------------------------------------
@router.get("/{project_id}/export")
def export(project_id: str, format: str = Query("json"),
           version: int | None = Query(None)):
    try:
        payload = _svc().export_payload(project_id, version=version)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    if format == "md" or format == "markdown":
        return PlainTextResponse(px.render_markdown(payload), media_type="text/markdown; charset=utf-8")
    return JSONResponse(payload)


# --- portfolio -------------------------------------------------------------
@portfolio_router.get("/portfolio")
def portfolio() -> JSONResponse:
    return JSONResponse({"projects": _svc().portfolio()})
