"""API VP-4 Work Orders & Context (Master Spec §25, §37 Phase I).

Тонкий типизированный слой над сервисами VP-4. Тело запроса — данные (§30.2):
текст/ссылки не исполняются и не расширяют права. Мутации принимают
``Idempotency-Key`` и optimistic ``expected_version``; ошибки — стабильный код
+ correlation ID. Каждая мутация пишет append-only Audit в сервисе (actor,
project, correlation, entity/version/hash, переход/решение, redacted-summary);
полные Work Order/чаты/credentials/raw-вывод в Audit не попадают.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .context_engine import ContextEngine
from .governor import ContextGovernor
from .optimizer import OptimizerService
from .reconstruct import ReconstructService
from .vp4handoff import HandoffService
from .workorders import WorkOrderError, WorkOrderService

router = APIRouter(prefix="/api/v1/projects", tags=["work-orders"])


def _err(exc: Exception, correlation_id: str | None = None) -> JSONResponse:
    if isinstance(exc, WorkOrderError):
        body = exc.to_dict()
        if correlation_id:
            body["correlation_id"] = correlation_id
        return JSONResponse({"error": body}, status_code=exc.http)
    raise exc


# --- модели запросов -------------------------------------------------------
class VpSpecRequest(BaseModel):
    vp_key: str


class WorkOrderRequest(BaseModel):
    vp_spec_id: str
    role: str = "builder"
    goal: str = ""
    criterion_ids: list[str] | None = None
    scope: dict | None = None
    capabilities: list[str] | None = None
    test_impact: list[str] | None = None
    wo_key: str = ""


class TransitionRequest(BaseModel):
    to_status: str
    expected_version: int | None = None
    reason_code: str = ""
    note: str = ""
    holder: str = "builder"


class EvaluateRequest(BaseModel):
    work_order_ids: list[str] = []
    signals: dict | None = None


class MergeRequest(BaseModel):
    work_order_ids: list[str]
    goal: str = ""


class SplitRequest(BaseModel):
    work_order_id: str
    groups: list[list[str]]
    checkpoint_id: str | None = None
    goals: list[str] | None = None


class CheckpointRequest(BaseModel):
    current_head: str = ""
    changed_files: list[str] | None = None
    commands: list[dict] | None = None
    failures: list[dict] | None = None
    completed_criteria: list[str] | None = None
    decisions: list[str] | None = None
    impacted_checks: list[str] | None = None
    artifact_refs: list[str] | None = None
    cause: str = "checkpoint"
    exact_next_action: str = ""
    job_package_id: str = ""


class HandoffRequest(BaseModel):
    checkpoint_id: str
    job_package_id: str = ""
    current_head: str = ""


class AckRequest(BaseModel):
    ack_hash: str
    baseline_ack: str = ""
    actual_head: str | None = None


class RejectRequest(BaseModel):
    reason_code: str = "OWNER_REQUIRED"
    note: str = ""


class ReconstructRequest(BaseModel):
    actual_head: str | None = None
    acknowledge: bool = True


class RotateRequest(BaseModel):
    trigger: str = "owner_rotation"
    current_head: str = ""
    changed_files: list[str] | None = None
    commands: list[dict] | None = None
    failures: list[dict] | None = None
    completed_criteria: list[str] | None = None
    decisions: list[str] | None = None
    impacted_checks: list[str] | None = None
    artifact_refs: list[str] | None = None
    job_package_id: str = ""
    next_profile_request: str = ""


class ContinueRequest(BaseModel):
    ack_hash: str
    baseline_ack: str = ""
    actual_head: str | None = None
    holder: str = "fresh-session"


class DetectRequest(BaseModel):
    work_order_ids: list[str] = []
    signals: dict | None = None


class CompactRequest(BaseModel):
    job_package_id: str | None = None
    content: dict | None = None


def _hdr(idem, corr):
    return {"idempotency_key": idem or "", "correlation_id": corr or ""}


# --- VP Specs --------------------------------------------------------------
@router.post("/{project_id}/vp-specs")
def create_vp_spec(project_id: str, req: VpSpecRequest,
                   idem: str | None = Header(None, alias="Idempotency-Key"),
                   corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = WorkOrderService().create_vp_spec(project_id, req.vp_key, **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/vp-specs")
def list_vp_specs(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"vp_specs": WorkOrderService().list_vp_specs(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/vp-specs/{spec_id}")
def get_vp_spec(project_id: str, spec_id: str) -> JSONResponse:
    try:
        return JSONResponse(WorkOrderService().get_vp_spec(project_id, spec_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- Work Orders -----------------------------------------------------------
@router.post("/{project_id}/work-orders")
def create_work_order(project_id: str, req: WorkOrderRequest,
                      idem: str | None = Header(None, alias="Idempotency-Key"),
                      corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = WorkOrderService().create_work_order(
            project_id, req.vp_spec_id, role=req.role, goal=req.goal,
            criterion_ids=req.criterion_ids, scope=req.scope, capabilities=req.capabilities,
            test_impact=req.test_impact, wo_key=req.wo_key, **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/work-orders")
def list_work_orders(project_id: str, vp_spec_id: str | None = Query(None),
                     status: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse({"work_orders": WorkOrderService().list_work_orders(
            project_id, vp_spec_id=vp_spec_id, status=status)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/work-orders/{wo_id}")
def get_work_order(project_id: str, wo_id: str) -> JSONResponse:
    try:
        return JSONResponse(WorkOrderService().get_work_order(project_id, wo_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/work-orders/{wo_id}/transition")
def transition(project_id: str, wo_id: str, req: TransitionRequest,
               corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = WorkOrderService().transition(
            project_id, wo_id, req.to_status, expected_version=req.expected_version,
            reason_code=req.reason_code, note=req.note, holder=req.holder, correlation_id=corr or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


# --- Optimizer -------------------------------------------------------------
@router.post("/{project_id}/optimizer/evaluate")
def optimizer_evaluate(project_id: str, req: EvaluateRequest,
                       corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        return JSONResponse(OptimizerService().evaluate(
            project_id, req.work_order_ids, signals=req.signals, correlation_id=corr or ""))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)


@router.post("/{project_id}/optimizer/merge/preview")
def merge_preview(project_id: str, req: MergeRequest) -> JSONResponse:
    try:
        return JSONResponse(OptimizerService().merge_preview(project_id, req.work_order_ids))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/optimizer/merge/confirm")
def merge_confirm(project_id: str, req: MergeRequest,
                  idem: str | None = Header(None, alias="Idempotency-Key"),
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = OptimizerService().merge_confirm(project_id, req.work_order_ids, goal=req.goal,
                                               **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.post("/{project_id}/optimizer/split/preview")
def split_preview(project_id: str, req: SplitRequest) -> JSONResponse:
    try:
        return JSONResponse(OptimizerService().split_preview(project_id, req.work_order_id, req.groups))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/optimizer/split/confirm")
def split_confirm(project_id: str, req: SplitRequest,
                  idem: str | None = Header(None, alias="Idempotency-Key"),
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    if not req.checkpoint_id:
        return _err(WorkOrderError("SPLIT_INVALID", "checkpoint_id обязателен для split"), corr)
    try:
        out = OptimizerService().split_confirm(
            project_id, req.work_order_id, req.groups, checkpoint_id=req.checkpoint_id,
            goals=req.goals, **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/optimizer/decisions")
def list_decisions(project_id: str) -> JSONResponse:
    try:
        return JSONResponse({"decisions": OptimizerService().list_decisions(project_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- Context / JobPackage --------------------------------------------------
@router.post("/{project_id}/work-orders/{wo_id}/job-package")
def build_job_package(project_id: str, wo_id: str,
                      idem: str | None = Header(None, alias="Idempotency-Key"),
                      corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = ContextEngine().build_job_package(project_id, wo_id, **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/job-packages")
def list_job_packages(project_id: str, work_order_id: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse({"job_packages": ContextEngine().list_job_packages(
            project_id, work_order_id=work_order_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/job-packages/{pkg_id}")
def get_job_package(project_id: str, pkg_id: str) -> JSONResponse:
    try:
        return JSONResponse(ContextEngine().get_job_package(project_id, pkg_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/context/compact-probe")
def compact_probe(project_id: str, req: CompactRequest) -> JSONResponse:
    try:
        content = req.content
        if content is None and req.job_package_id:
            content = ContextEngine().get_job_package(project_id, req.job_package_id)["content"]
        if content is None:
            return _err(WorkOrderError("WO_INVALID", "нужен content или job_package_id"))
        return JSONResponse(ReconstructService().compact_probe(content))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- Checkpoints -----------------------------------------------------------
@router.post("/{project_id}/work-orders/{wo_id}/checkpoints")
def build_checkpoint(project_id: str, wo_id: str, req: CheckpointRequest,
                     idem: str | None = Header(None, alias="Idempotency-Key"),
                     corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = HandoffService().build_checkpoint(
            project_id, wo_id, current_head=req.current_head, changed_files=req.changed_files,
            commands=req.commands, failures=req.failures, completed_criteria=req.completed_criteria,
            decisions=req.decisions, impacted_checks=req.impacted_checks,
            artifact_refs=req.artifact_refs, cause=req.cause,
            exact_next_action=req.exact_next_action, job_package_id=req.job_package_id,
            **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/checkpoints")
def list_checkpoints(project_id: str, work_order_id: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse({"checkpoints": HandoffService().list_checkpoints(
            project_id, work_order_id=work_order_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/checkpoints/{ckpt_id}")
def get_checkpoint(project_id: str, ckpt_id: str) -> JSONResponse:
    try:
        return JSONResponse(HandoffService().get_checkpoint(project_id, ckpt_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/checkpoints/{ckpt_id}/verify")
def verify_checkpoint(project_id: str, ckpt_id: str) -> JSONResponse:
    try:
        return JSONResponse(HandoffService().verify_checkpoint(project_id, ckpt_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- Handoffs --------------------------------------------------------------
@router.post("/{project_id}/work-orders/{wo_id}/handoffs")
def build_handoff(project_id: str, wo_id: str, req: HandoffRequest,
                  idem: str | None = Header(None, alias="Idempotency-Key"),
                  corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = HandoffService().build_handoff(
            project_id, wo_id, checkpoint_id=req.checkpoint_id,
            job_package_id=req.job_package_id, current_head=req.current_head, **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.get("/{project_id}/handoffs")
def list_handoffs(project_id: str, work_order_id: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse({"handoffs": HandoffService().list_handoffs(
            project_id, work_order_id=work_order_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/handoffs/{handoff_id}")
def get_handoff(project_id: str, handoff_id: str) -> JSONResponse:
    try:
        return JSONResponse(HandoffService().get_handoff(project_id, handoff_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/handoffs/{handoff_id}/verify")
def verify_handoff(project_id: str, handoff_id: str,
                   actual_head: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse(HandoffService().verify_handoff(project_id, handoff_id,
                                                            actual_head=actual_head))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/handoffs/{handoff_id}/acknowledge")
def acknowledge(project_id: str, handoff_id: str, req: AckRequest,
                corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = HandoffService().acknowledge(project_id, handoff_id, ack_hash=req.ack_hash,
                                           baseline_ack=req.baseline_ack, actual_head=req.actual_head,
                                           correlation_id=corr or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


@router.post("/{project_id}/handoffs/{handoff_id}/reject")
def reject(project_id: str, handoff_id: str, req: RejectRequest,
           corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = HandoffService().reject(project_id, handoff_id, reason_code=req.reason_code,
                                     note=req.note, correlation_id=corr or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


@router.post("/{project_id}/handoffs/{handoff_id}/reconstruct")
def reconstruct(project_id: str, handoff_id: str, req: ReconstructRequest,
                corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = ReconstructService().run_fresh_session(
            project_id, handoff_id, actual_head=req.actual_head, acknowledge=req.acknowledge,
            correlation_id=corr or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


@router.get("/{project_id}/handoffs/{handoff_id}/acks")
def list_acks(project_id: str, handoff_id: str) -> JSONResponse:
    try:
        return JSONResponse({"acks": HandoffService().list_acks(project_id, handoff_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- Governor / Rotation ---------------------------------------------------
@router.post("/{project_id}/governor/detect")
def governor_detect(project_id: str, req: DetectRequest,
                    corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        return JSONResponse(ContextGovernor().detect(project_id, req.work_order_ids,
                                                     signals=req.signals, correlation_id=corr or ""))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)


@router.post("/{project_id}/work-orders/{wo_id}/rotate")
def rotate(project_id: str, wo_id: str, req: RotateRequest,
           idem: str | None = Header(None, alias="Idempotency-Key"),
           corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = ContextGovernor().rotate(
            project_id, wo_id, trigger=req.trigger, current_head=req.current_head,
            changed_files=req.changed_files, commands=req.commands, failures=req.failures,
            completed_criteria=req.completed_criteria, decisions=req.decisions,
            impacted_checks=req.impacted_checks, artifact_refs=req.artifact_refs,
            job_package_id=req.job_package_id, next_profile_request=req.next_profile_request,
            **_hdr(idem, corr))
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out, status_code=201)


@router.post("/{project_id}/rotations/{rotation_id}/continue")
def continue_rotation(project_id: str, rotation_id: str, req: ContinueRequest,
                      corr: str | None = Header(None, alias="X-Correlation-ID")) -> JSONResponse:
    try:
        out = ContextGovernor().continue_after_rotation(
            project_id, rotation_id, ack_hash=req.ack_hash, baseline_ack=req.baseline_ack,
            actual_head=req.actual_head, holder=req.holder, correlation_id=corr or "")
    except Exception as exc:  # noqa: BLE001
        return _err(exc, corr)
    return JSONResponse(out)


@router.get("/{project_id}/rotations")
def list_rotations(project_id: str, work_order_id: str | None = Query(None)) -> JSONResponse:
    try:
        return JSONResponse({"rotations": ContextGovernor().list_rotations(
            project_id, work_order_id=work_order_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.get("/{project_id}/rotations/{rotation_id}")
def get_rotation(project_id: str, rotation_id: str) -> JSONResponse:
    try:
        return JSONResponse(ContextGovernor().get_rotation(project_id, rotation_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
