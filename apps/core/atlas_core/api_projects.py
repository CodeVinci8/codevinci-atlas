"""API Project Workspace (Master Spec §25, §35, VP-2).

Тонкий слой над :class:`atlas_core.workspace.WorkspaceService`. Тело запроса —
данные (§30.2): произвольные строки репозитория/архива не исполняются и не
расширяют права. Ошибки безопасности/политики отражаются честными кодами.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .errors import AtlasError, ErrorCode
from .workspace import WorkspaceError, WorkspaceService
from .worktrees import WorktreeError
from .wspaths import WorkspaceSecurityError

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ConnectRequest(BaseModel):
    name: str
    source_kind: str  # local_git | github | archive | empty
    path: str | None = None
    github_ref: str | None = None
    archive_path: str | None = None


class WorktreeRequest(BaseModel):
    branch: str


class AcquireRequest(BaseModel):
    worktree_path: str
    holder: str | None = None


def _svc() -> WorkspaceService:
    return WorkspaceService()


def _err(exc: Exception) -> JSONResponse:
    if isinstance(exc, WorkspaceSecurityError):
        return JSONResponse({"error": exc.to_dict()}, status_code=400)
    if isinstance(exc, WorktreeError):
        return JSONResponse({"error": exc.to_dict()}, status_code=400)
    if isinstance(exc, AtlasError):
        code = exc.classified.code
        http = 409 if code == ErrorCode.WORKTREE_CONFLICT else 400
        return JSONResponse({"error": exc.classified.to_dict()}, status_code=http)
    if isinstance(exc, WorkspaceError):
        http = {"POLICY_DENIED": 403, "OUTPUT_INVALID": 400}.get(exc.code, 400)
        if "не найден" in exc.reason:
            http = 404
        return JSONResponse({"error": exc.to_dict()}, status_code=http)
    raise exc


@router.get("")
def list_projects() -> JSONResponse:
    return JSONResponse({"projects": _svc().list_projects()})


@router.post("")
def connect_project(req: ConnectRequest) -> JSONResponse:
    try:
        ov = _svc().connect_project(
            req.name, req.source_kind, path=req.path,
            github_ref=req.github_ref, archive_path=req.archive_path)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return JSONResponse(ov, status_code=201)


@router.get("/{project_id}")
def get_project(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().overview(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/baseline/refresh")
def refresh_baseline(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().refresh_baseline(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/worktrees")
def create_worktree(project_id: str, req: WorktreeRequest) -> JSONResponse:
    try:
        return JSONResponse(_svc().create_worktree(project_id, req.branch), status_code=201)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@router.post("/{project_id}/worktrees/acquire")
def acquire_writer(project_id: str, req: AcquireRequest) -> JSONResponse:
    try:
        res = _svc().try_acquire_writer(project_id, req.worktree_path,
                                        holder=req.holder or "builder2")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return JSONResponse(res)


@router.delete("/{project_id}")
def disconnect_project(project_id: str) -> JSONResponse:
    try:
        return JSONResponse(_svc().disconnect_project(project_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
