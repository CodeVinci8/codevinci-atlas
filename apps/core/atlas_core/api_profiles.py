"""API VP-5 Profiles/Models (Master Spec §11, §25, §27.4).

Safe-представления профилей и моделей: только alias, provider, state, verified
health/capacity. Никаких email/token/cookie/raw path. Onboarding cookie-import
возвращает ``UNSUPPORTED`` (§11.4/§30.3); реальный login — под owner-гейтом
(здесь не выполняется).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .agent_registry import ModelService, ProfileService, RegistryError

router = APIRouter(prefix="/api/v1", tags=["profiles"])
_profiles = ProfileService()
_models = ModelService()


def _err(exc: Exception) -> JSONResponse:
    if isinstance(exc, RegistryError):
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.http)
    raise exc


@router.get("/profiles")
def list_profiles(provider: str | None = Query(None), state: str | None = Query(None)) -> JSONResponse:
    return JSONResponse({
        "profiles": _profiles.list_profiles(provider=provider, state=state),
        "summary": _profiles.summary_counts(),
    })


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str) -> JSONResponse:
    try:
        return JSONResponse({"profile": _profiles.get_profile(profile_id)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


class OnboardingRequest(BaseModel):
    method: str  # official | attach | cookie


@router.post("/profiles/{profile_id}/onboarding")
def onboarding(profile_id: str, req: OnboardingRequest) -> JSONResponse:
    method = (req.method or "").lower()
    if method == "cookie":
        # Cookie-import experimental и отключён до отдельного approved spike (§11.4/§30.3).
        return JSONResponse({"error": {"code": "COOKIE_UNSUPPORTED",
                             "reason": "cookie-import отключён до отдельного security-spike"}},
                            status_code=422)
    if method == "official":
        return JSONResponse({"onboarding": {"method": "official", "status": "OWNER_ACTION_REQUIRED",
                            "detail": "Официальный login выполняется под идентичностью профиля и "
                                      "требует подтверждения владельца; Core не завершает login сам."}})
    if method == "attach":
        return JSONResponse({"onboarding": {"method": "attach", "status": "OWNER_ACTION_REQUIRED",
                            "detail": "Привязка существующего allowlisted auth root без копирования; "
                                      "проверяются owner/permissions/status."}})
    return JSONResponse({"error": {"code": "INVALID", "reason": f"неизвестный метод: {req.method}"}},
                        status_code=422)


@router.get("/models")
def list_models(provider: str | None = Query(None)) -> JSONResponse:
    return JSONResponse({"models": _models.list_models(provider=provider)})
