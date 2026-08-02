"""API VP-5 System summary для Pulse (Master Spec §27.2, §30).

Правдивая sanitized-сводка среды выполнения Core. Не раскрывает IP, hostname,
Unix-имена, auth-root пути, env и credentials.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .settings import load_settings
from .system_summary import system_summary

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/system/summary")
def summary() -> JSONResponse:
    return JSONResponse({"summary": system_summary(load_settings())})
