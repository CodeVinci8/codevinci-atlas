"""FastAPI-приложение Core (Master Spec §25, §34).

Минимальный VP-1 Core: health (Core + Runner через UDS), audit-запрос,
settings/locale. Правдивые degraded/offline состояния. Не запускается от root
в проде (проверяется на старте и отражается в health).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import audit
from .db import get_engine, init_engine
from .ids import utcnow_iso
from .runner_health import runner_health
from .settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_engine(settings.db_url, settings.db_path)

    app = FastAPI(title="CodeVinci Atlas Core", version=settings.version,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    from .api_projects import router as projects_router
    app.include_router(projects_router)

    from .api_productmap import portfolio_router
    from .api_productmap import router as productmap_router
    app.include_router(productmap_router)
    app.include_router(portfolio_router)

    from .api_workorders import router as workorders_router
    app.include_router(workorders_router)

    # VP-5 Agent Pipeline: runs, profiles/models, system summary.
    from .api_profiles import router as profiles_router_v5
    from .api_runs import router as runs_router
    from .api_system import router as system_router
    app.include_router(runs_router)
    app.include_router(profiles_router_v5)
    app.include_router(system_router)

    # VP-6 Review & Quality: reviews, findings, QualityReport, audit, waiver, fix WO.
    from .api_reviews import router as reviews_router
    app.include_router(reviews_router)

    @app.get("/api/v1/health")
    def health() -> JSONResponse:
        # Core проверяет собственную БД и Runner (честный degraded).
        db_ok = True
        db_reason = "ok"
        try:
            with get_engine().connect() as c:
                c.exec_driver_sql("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            db_ok = False
            from .redaction import redact
            db_reason = redact(str(exc))[:80]

        rh = runner_health(settings.runner_socket, settings.runner_token_file)
        core_non_root = os.geteuid() != 0 if hasattr(os, "geteuid") else True

        overall = "READY"
        if not db_ok:
            overall = "DEGRADED"
        runner_status = rh.get("status", "OFFLINE")
        if runner_status != "READY":
            # Runner offline не «убивает» Core, но честно виден как degraded.
            overall = "DEGRADED" if overall == "READY" else overall

        return JSONResponse({
            "status": overall,
            "version": settings.version,
            "time": utcnow_iso(),
            "core": {"non_root": core_non_root, "db": {"ok": db_ok, "reason": db_reason}},
            "runner": rh,
        })

    @app.get("/api/v1/audit")
    def audit_query(event_type: str | None = None,
                    limit: int = Query(100, ge=1, le=500),
                    offset: int = Query(0, ge=0)) -> dict:
        return {"total": audit.count(),
                "events": audit.query(event_type=event_type, limit=limit, offset=offset)}

    @app.get("/api/v1/settings")
    def get_settings() -> dict:
        # Только non-secret настройки (§11.2).
        return {"default_locale": settings.default_locale,
                "locales": ["ru", "en"],
                "env": settings.env,
                "version": settings.version,
                "web_origin": settings.web_origin}

    @app.on_event("startup")
    def _startup() -> None:
        audit.record("core.started", f"Core {settings.version} env={settings.env}")
        # Идемпотентная сверка allowlisted реестра профилей → durable-таблица
        # (VP6-D2): чтобы 4 профиля появлялись в UI после deploy/рестарта. Только
        # safe-метаданные; не читает credentials и не стартует provider-сессии.
        # Best-effort: не роняет старт Core, если таблицы/реестра ещё нет.
        try:
            from sqlalchemy import inspect as _inspect
            if _inspect(get_engine()).has_table("agent_profiles"):
                from .profile_reconcile import reconcile_profiles
                res = reconcile_profiles(actor="startup")
                audit.record("profiles.registry.reconciled.startup",
                             f"total={res.total}")
        except Exception as exc:  # noqa: BLE001
            from .redaction import redact
            audit.record("profiles.registry.reconcile.error", redact(str(exc))[:80])

    return app
