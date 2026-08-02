"""Нормализация auth-состояния провайдера (Master Spec §11.2, §11.5, §12).

Единственный источник — официальный вывод CLI (``claude auth status --json`` /
``codex login status``). Мы НИКОГДА не читаем credential/session-файлы и НИКОГДА
не возвращаем PII: email, orgId, orgName, token, cookie, raw auth path. Из claude
берём только ``loggedIn/authMethod/apiProvider/subscriptionType``; из codex —
только факт авторизации (текст не эхо-им, чтобы не протащить email).
"""

from __future__ import annotations

import json

# Ключи, которые НИКОГДА не попадают в нормализованный результат.
PII_KEYS = frozenset({"email", "orgid", "org_id", "orgname", "org_name", "account",
                      "account_id", "token", "access_token", "cookie", "path", "authroot",
                      "auth_root", "name"})

# subscriptionType → человекочитаемый verified plan-label.
_CLAUDE_PLAN = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise",
                "free": "Free"}


def normalize_claude_auth(stdout: str) -> dict:
    """``claude auth status --json`` → нормализованный dict без PII."""
    try:
        data = json.loads(stdout or "")
    except (json.JSONDecodeError, TypeError):
        low = (stdout or "").lower()
        authed = "logged in" in low and "not logged in" not in low
        return {"authenticated": authed, "auth_status": "READY" if authed else "AUTH_REQUIRED",
                "auth_method": "", "api_provider": "", "plan_label": ""}
    if not isinstance(data, dict):
        return {"authenticated": False, "auth_status": "AUTH_REQUIRED",
                "auth_method": "", "api_provider": "", "plan_label": ""}
    authed = bool(data.get("loggedIn"))
    sub = str(data.get("subscriptionType") or "").lower()
    return {
        "authenticated": authed,
        "auth_status": "READY" if authed else "AUTH_REQUIRED",
        "auth_method": str(data.get("authMethod") or ""),       # claude.ai|console — не PII
        "api_provider": str(data.get("apiProvider") or ""),     # firstParty|... — не PII
        "plan_label": _CLAUDE_PLAN.get(sub, sub.capitalize() if sub else ""),
    }


def normalize_codex_auth(stdout: str) -> dict:
    """``codex login status`` (текст) → нормализованный dict без PII.

    Текст НЕ эхо-ится (может содержать email); возвращаем только факт авторизации.
    """
    low = (stdout or "").lower()
    authed = ("logged in" in low) and ("not logged in" not in low)
    return {
        "authenticated": authed,
        "auth_status": "READY" if authed else "AUTH_REQUIRED",
        "auth_method": "chatgpt" if authed else "",  # обобщённо, без account
        "api_provider": "openai" if authed else "",
        "plan_label": "",  # план/лимиты Codex через CLI недоступны → пусто (UNKNOWN на уровне ёмкости)
    }


def assert_no_pii(normalized: dict) -> None:
    """Защита: в нормализованном результате нет PII-ключей (для тестов/guard)."""
    for k in normalized:
        if k.lower() in PII_KEYS:
            raise ValueError(f"PII-ключ в нормализованном auth: {k}")
