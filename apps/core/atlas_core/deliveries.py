"""GitHub delivery persistence (Master Spec §20, §26, §31).

Идемпотентно сохраняет состояние доставки/merge-gate в durable-таблицу
``github_deliveries`` (ключ ``idempotency_key = repo+base+branch+head``), чтобы
экран Автономия/top-bar показывали **реальную** историю доставки, а не только
фикстуры. Token НЕ хранится. Повторный вызов с тем же ключом обновляет строку,
а не создаёт дубль.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import GithubDelivery


def _now() -> datetime:
    return datetime.now(timezone.utc)


def delivery_key(repo: str, base: str, branch: str, head_sha: str) -> str:
    """Каноничный ключ идемпотентности: repo+base+branch+**полный** head SHA
    (Fix3). Полный SHA исключает коллизии близких head; на этот ключ в схеме есть
    UNIQUE-индекс, а запись идёт атомарным upsert."""
    return f"{repo}@{base}<-{branch}:{head_sha}"


def record_delivery(*, project_id: str, repo: str, base: str, branch: str, head_sha: str,
                    run_id: str = "", pr_number: int | None = None, pr_url: str = "",
                    pr_state: str = "NONE", checks_state: str = "UNKNOWN",
                    checks_head_sha: str = "", mergeable: bool = False, merge_state: str = "",
                    review_package_id: str = "", quality_report_id: str = "",
                    gate_decision: str = "", gate_reason: str = "", grant_id: str = "",
                    correlation_id: str = "", actor: str = "core") -> dict:
    """**Атомарный** upsert строки доставки по idempotency_key (Fix3). Один INSERT
    … ON CONFLICT DO UPDATE вместо SELECT→INSERT — конкурентные одинаковые доставки
    дают ровно одну durable-строку (UNIQUE-индекс). «Липкие» поля сохраняют старое
    значение, если новое пустое (COALESCE/NULLIF)."""
    key = delivery_key(repo, base, branch, head_sha)
    now = _now()
    values = dict(
        id=new_id("ghd"), project_id=project_id, run_id=run_id, repo=repo, base=base,
        branch=branch, head_sha=head_sha, pr_number=pr_number, pr_url=pr_url,
        pr_state=pr_state, checks_state=checks_state, checks_head_sha=checks_head_sha or head_sha,
        mergeable=mergeable, merge_state=merge_state, review_package_id=review_package_id,
        quality_report_id=quality_report_id, gate_decision=gate_decision, gate_reason=gate_reason,
        grant_id=grant_id, idempotency_key=key, correlation_id=correlation_id, actor=actor,
        created_at=now, updated_at=now)
    ins = sqlite_insert(GithubDelivery).values(**values)
    ex, T = ins.excluded, GithubDelivery

    def sticky(col_ex, col_tbl):  # сохранить старое, если новое пустое
        return func.coalesce(func.nullif(col_ex, ""), col_tbl)

    upsert = ins.on_conflict_do_update(
        index_elements=[T.idempotency_key],
        set_={
            "project_id": ex.project_id, "repo": ex.repo, "base": ex.base, "branch": ex.branch,
            "head_sha": ex.head_sha, "pr_number": func.coalesce(ex.pr_number, T.pr_number),
            "pr_url": sticky(ex.pr_url, T.pr_url), "pr_state": ex.pr_state,
            "checks_state": ex.checks_state, "checks_head_sha": ex.checks_head_sha,
            "mergeable": ex.mergeable, "merge_state": ex.merge_state,
            "review_package_id": sticky(ex.review_package_id, T.review_package_id),
            "quality_report_id": sticky(ex.quality_report_id, T.quality_report_id),
            "gate_decision": sticky(ex.gate_decision, T.gate_decision),
            "gate_reason": sticky(ex.gate_reason, T.gate_reason),
            "grant_id": sticky(ex.grant_id, T.grant_id), "run_id": sticky(ex.run_id, T.run_id),
            "correlation_id": sticky(ex.correlation_id, T.correlation_id),
            "actor": ex.actor, "updated_at": ex.updated_at,
        })
    created = False
    last_exc: OperationalError | None = None
    for attempt in range(5):  # bounded retry на кратковременный SQLite lock
        try:
            with session_scope() as s:
                created = s.execute(select(T.id).where(T.idempotency_key == key)).first() is None
                s.execute(upsert)
                s.commit()
                out = s.execute(select(T).where(T.idempotency_key == key)).scalars().first().to_dict()
            break
        except OperationalError as exc:  # database is locked — повторить
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
    else:
        raise last_exc  # noqa: RSE102
    audit.record("github.delivery.recorded" if created else "github.delivery.updated",
                 f"repo={repo} branch={branch} head={head_sha[:12]} gate={gate_decision}",
                 actor=actor, correlation_id=correlation_id)
    return out


def get_delivery(repo: str, base: str, branch: str, head_sha: str) -> dict | None:
    key = delivery_key(repo, base, branch, head_sha)
    with session_scope() as s:
        row = s.execute(select(GithubDelivery)
                        .where(GithubDelivery.idempotency_key == key)).scalars().first()
        return row.to_dict() if row else None


def list_deliveries(*, project_id: str | None = None, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        stmt = select(GithubDelivery).order_by(GithubDelivery.created_at.desc(),
                                               GithubDelivery.id.desc())
        if project_id:
            stmt = stmt.where(GithubDelivery.project_id == project_id)
        rows = s.execute(stmt.limit(max(1, min(limit, 200)))).scalars().all()
        return [r.to_dict() for r in rows]
