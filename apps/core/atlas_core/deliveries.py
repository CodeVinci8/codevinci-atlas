"""GitHub delivery persistence (Master Spec §20, §26, §31).

Идемпотентно сохраняет состояние доставки/merge-gate в durable-таблицу
``github_deliveries`` (ключ ``idempotency_key = repo+base+branch+head``), чтобы
экран Автономия/top-bar показывали **реальную** историю доставки, а не только
фикстуры. Token НЕ хранится. Повторный вызов с тем же ключом обновляет строку,
а не создаёт дубль.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import GithubDelivery


def _now() -> datetime:
    return datetime.now(timezone.utc)


def delivery_key(repo: str, base: str, branch: str, head_sha: str) -> str:
    return f"{repo}@{base}<-{branch}:{head_sha[:12]}"


def record_delivery(*, project_id: str, repo: str, base: str, branch: str, head_sha: str,
                    run_id: str = "", pr_number: int | None = None, pr_url: str = "",
                    pr_state: str = "NONE", checks_state: str = "UNKNOWN",
                    checks_head_sha: str = "", mergeable: bool = False, merge_state: str = "",
                    review_package_id: str = "", quality_report_id: str = "",
                    gate_decision: str = "", gate_reason: str = "", grant_id: str = "",
                    correlation_id: str = "", actor: str = "core") -> dict:
    """Upsert строки доставки по idempotency_key. Возвращает to_dict()."""
    key = delivery_key(repo, base, branch, head_sha)
    now = _now()
    with session_scope() as s:
        row = s.execute(select(GithubDelivery)
                        .where(GithubDelivery.idempotency_key == key)).scalars().first()
        created = row is None
        if row is None:
            row = GithubDelivery(id=new_id("ghd"), idempotency_key=key, created_at=now)
            s.add(row)
        row.project_id = project_id
        row.run_id = run_id or row.run_id
        row.repo = repo
        row.base = base
        row.branch = branch
        row.head_sha = head_sha
        if pr_number is not None:
            row.pr_number = pr_number
        row.pr_url = pr_url or row.pr_url
        row.pr_state = pr_state
        row.checks_state = checks_state
        row.checks_head_sha = checks_head_sha or head_sha
        row.mergeable = mergeable
        row.merge_state = merge_state
        row.review_package_id = review_package_id or row.review_package_id
        row.quality_report_id = quality_report_id or row.quality_report_id
        row.gate_decision = gate_decision or row.gate_decision
        row.gate_reason = gate_reason or row.gate_reason
        row.grant_id = grant_id or row.grant_id
        row.correlation_id = correlation_id or row.correlation_id
        row.actor = actor
        row.updated_at = now
        s.commit()
        out = row.to_dict()
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
