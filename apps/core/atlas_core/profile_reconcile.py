"""Идемпотентная сверка реестра профилей → durable-таблица (Master Spec §11, §17.2).

Факт (root-cause VP6-D2): `/api/v1/profiles` пуст, потому что
``scripts/profile-init.py`` записывал только non-secret **файловый** реестр
(:class:`atlas_core.profiles.ProfileRegistry`), а API читает durable-таблицу
``agent_profiles`` (заполняется лишь :meth:`ProfileService.upsert_profile`,
которая раньше вызывалась только тестами). Между реестром и БД не было шага
синхронизации.

Этот модуль — минимальный корректный production-путь: **идемпотентная**
reconciliation, которую вызывает deployment/startup (CLI ``atlas profiles
reconcile``). Она читает allowlisted файловый реестр и upsert-ит **только
safe-метаданные** (alias, provider, unix-метка, allowlist-ref auth root) в
``agent_profiles``/``profile_states``.

Жёсткие правила (§11.1/§11.2/§30):

* НЕ читает credential-файлы, НЕ копирует tokens/cookies/email/orgId;
* НЕ хранит raw auth path (только стабильный ``auth_root_ref``);
* НЕ стартует provider-сессии и НЕ делает provider-вызовов;
* НЕ сканирует произвольные Unix-home — только allowlisted реестр;
* idempotent и durable: повторный запуск не создаёт дублей и не портит
  runtime-состояние (LEASED/COOLDOWN/… не сбрасывается в AUTH_REQUIRED);
* capacity не наблюдает — остаётся ``UNKNOWN`` до отдельной verified-пробы.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from . import audit
from .agent_registry import ProfileService
from .db import session_scope
from .ids import new_id
from .orm import AgentProfile, ProfileState
from .profiles import ProfileRegistry


def auth_root_ref(provider: str, alias: str) -> str:
    """Стабильный allowlist-ref auth root — НЕ raw filesystem path (§11.2)."""

    return f"registry:{provider}/{alias}"


@dataclass
class ReconcileResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    total: int = 0
    by_provider: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "created": sorted(self.created),
            "updated": sorted(self.updated),
            "total": self.total,
            "by_provider": self.by_provider,
        }


def reconcile_profiles(registry: ProfileRegistry | None = None, *,
                       actor: str = "deploy") -> ReconcileResult:
    """Синхронизировать allowlisted файловый реестр → ``agent_profiles``.

    Возвращает :class:`ReconcileResult`. Идемпотентно: повторный вызов при том же
    реестре не меняет число профилей и не сбрасывает их runtime-состояние.
    """

    registry = registry or ProfileRegistry()
    svc = ProfileService()
    result = ReconcileResult()

    for prof in registry.list():
        # Существовал ли профиль до upsert (для точного created/updated).
        with session_scope() as s:
            existed = s.execute(select(AgentProfile.id).where(
                AgentProfile.alias == prof.alias)).first() is not None
        # Только safe-метаданные. unix_label — безопасная сервис-метка;
        # auth_root_ref — стабильный allowlist-ref (НЕ raw path).
        pid = svc.upsert_profile(
            prof.alias, prof.provider,
            unix_label=prof.runtime_user or "",
            auth_root_ref=auth_root_ref(prof.provider, prof.alias),
            schedulable=True, enabled=True,
        )
        if existed:
            result.updated.append(prof.alias)
        else:
            result.created.append(prof.alias)
            # Свежесозданный профиль: UNCONFIGURED → AUTH_REQUIRED (root есть,
            # credentials не проверялись здесь). Не трогаем уже существующие
            # runtime-состояния (LEASED/COOLDOWN/READY/…).
            _promote_unconfigured(pid)
        result.by_provider[prof.provider] = result.by_provider.get(prof.provider, 0) + 1

    result.total = len(result.created) + len(result.updated)

    _record_reconcile(result, actor=actor)
    audit.record("profiles.registry.reconciled",
                 f"total={result.total} created={len(result.created)} "
                 f"updated={len(result.updated)}", actor=actor)
    return result


def _promote_unconfigured(profile_id: str) -> None:
    """Свежий профиль UNCONFIGURED → AUTH_REQUIRED (root есть, auth не проверен).

    Идемпотентно и безопасно: меняет состояние ТОЛЬКО из UNCONFIGURED, поэтому
    не может затереть LEASED/COOLDOWN/READY/DISABLED и т.п.
    """

    with session_scope() as s:
        row = s.execute(select(ProfileState).where(
            ProfileState.profile_id == profile_id)).scalars().first()
        if row is not None and row.state == "UNCONFIGURED":
            row.state = "AUTH_REQUIRED"
            row.next_action = "Owner-логин в изолированный root профиля."
            s.commit()


def _record_reconcile(result: ReconcileResult, *, actor: str) -> None:
    """Append-only запись reconciliation (best-effort: таблица из 0006)."""

    try:
        from .orm import ProfileRegistryReconcile  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    try:
        with session_scope() as s:
            s.add(ProfileRegistryReconcile(
                id=new_id("preg"), created=len(result.created),
                updated=len(result.updated), total=result.total,
                by_provider_json=json.dumps(result.by_provider, ensure_ascii=False,
                                            sort_keys=True), actor=actor))
            s.commit()
    except Exception:  # noqa: BLE001 — таблицы ещё нет (до 0006): не критично
        pass
