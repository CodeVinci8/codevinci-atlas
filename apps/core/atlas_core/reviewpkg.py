"""ReviewPackage VP-6 (Master Spec §18.1) — immutable, SHA-bound.

Пакет идентифицируется ``content_hash = sha256:`` над canonical-JSON immutable-
содержимого. **Валидность проверяется сверкой с фактом** (Git/FS/DB), а не
доверием отчёту Builder: протухший base/head SHA, изменённый артефакт (хеш),
отсутствующее/неразрешимое evidence или несовпадающий Work Order → вердикт
``INVALID_EVIDENCE`` (VP6-D4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import ReviewPackage
from .productmap import canonical_json, content_hash
from .redaction import redact


def sha256_file(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class ReviewInputs:
    """Входы для сборки ReviewPackage. Все ссылки/хеши точные (§18.1)."""

    project_id: str
    run_id: str = ""
    work_order_id: str = ""
    vp_key: str = ""
    wo_key: str = ""
    correlation_id: str = ""
    branch: str = ""
    base_sha: str = ""
    head_sha: str = ""
    spec_hash: str = ""
    brief_hash: str = ""
    map_hash: str = ""
    diff_summary: dict = field(default_factory=dict)
    artifact_hashes: list[dict] = field(default_factory=list)   # [{path, sha}]
    acceptance: list[dict] = field(default_factory=list)        # [{criterion, check}]
    claims: list[dict] = field(default_factory=list)            # заявления Builder
    impact_class: str = ""
    checks: list[dict] = field(default_factory=list)            # [{command, version, result, cache}]
    evidence_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    grant_snapshot: dict = field(default_factory=dict)
    freshness: dict = field(default_factory=dict)


def _immutable_payload(i: ReviewInputs) -> dict:
    """Canonical immutable-содержимое, покрываемое content_hash."""

    return {
        "project_id": i.project_id, "run_id": i.run_id,
        "work_order_id": i.work_order_id, "vp_key": i.vp_key, "wo_key": i.wo_key,
        "branch": i.branch, "base_sha": i.base_sha, "head_sha": i.head_sha,
        "spec_hash": i.spec_hash, "brief_hash": i.brief_hash, "map_hash": i.map_hash,
        "diff_summary": i.diff_summary, "artifact_hashes": i.artifact_hashes,
        "acceptance": i.acceptance, "claims": i.claims, "impact_class": i.impact_class,
        "checks": i.checks, "evidence_refs": i.evidence_refs,
        "limitations": i.limitations, "grant_snapshot": i.grant_snapshot,
        "freshness": i.freshness,
    }


def build_review_package(i: ReviewInputs, *, actor: str = "core") -> dict:
    """Собрать и персистировать immutable ReviewPackage. Возвращает to_dict()."""

    ch = content_hash(_immutable_payload(i))
    pid = new_id("rpkg")
    with session_scope() as s:
        row = ReviewPackage(
            id=pid, project_id=i.project_id, run_id=i.run_id,
            work_order_id=i.work_order_id, vp_key=i.vp_key, wo_key=i.wo_key,
            correlation_id=i.correlation_id, branch=i.branch, base_sha=i.base_sha,
            head_sha=i.head_sha, spec_hash=i.spec_hash, brief_hash=i.brief_hash,
            map_hash=i.map_hash, diff_summary_json=canonical_json(i.diff_summary),
            artifact_hashes_json=canonical_json(i.artifact_hashes),
            acceptance_json=canonical_json(i.acceptance),
            claims_json=canonical_json(i.claims), impact_class=i.impact_class,
            checks_json=canonical_json(i.checks),
            evidence_refs_json=canonical_json(i.evidence_refs),
            limitations_json=canonical_json(i.limitations),
            grant_snapshot_json=canonical_json(i.grant_snapshot),
            freshness_json=canonical_json(i.freshness), content_hash=ch,
            status="valid", actor=actor)
        s.add(row)
        s.commit()
        out = row.to_dict()
    audit.record("review.package.built", f"rpkg={pid} hash={ch[:20]}", actor=actor)
    return out


@dataclass
class ReviewFacts:
    """Факты для сверки (Git/FS/DB). Побеждают отчёт Builder."""

    current_head: str | None = None
    artifacts: dict = field(default_factory=dict)          # {path: actual_sha}
    evidence_present: list[str] = field(default_factory=list)
    expected_wo_key: str | None = None
    expected_spec_hash: str | None = None


def validate_review_package(pkg_id: str, facts: ReviewFacts) -> tuple[bool, str, str]:
    """Сверить ReviewPackage с фактами. При расхождении → ``INVALID_EVIDENCE``.

    Возвращает ``(valid, code, reason)``. При невалидности durable-статус пакета
    переводится в ``invalid`` (immutable-содержимое не мутируется).
    """

    with session_scope() as s:
        row = s.get(ReviewPackage, pkg_id)
        if row is None:
            return False, "NOT_FOUND", "ReviewPackage не найден"
        d = row.to_dict()

    code, reason = "", ""
    # 1. Протухший head SHA (сверка с фактическим Git).
    if facts.current_head is not None and d["head_sha"] and d["head_sha"] != facts.current_head:
        code, reason = "STALE_SHA", (
            f"head в пакете {d['head_sha'][:12]} != фактический {facts.current_head[:12]}")
    # 2. Изменённый артефакт (хеш файла не совпадает).
    if not code:
        for art in d["artifact_hashes"]:
            path, sha = art.get("path"), art.get("sha")
            actual = facts.artifacts.get(path)
            if actual is not None and actual != sha:
                code, reason = "ARTIFACT_ALTERED", f"артефакт {path}: хеш изменён"
                break
    # 3. Отсутствующее/неразрешимое evidence.
    if not code:
        missing = [r for r in d["evidence_refs"] if r not in facts.evidence_present]
        if missing:
            code, reason = "MISSING_EVIDENCE", f"неразрешимые evidence: {missing[:5]}"
    # 4. Несовпадающий Work Order (spec_hash/wo_key).
    if not code:
        if facts.expected_wo_key is not None and d["wo_key"] and facts.expected_wo_key != d["wo_key"]:
            code, reason = "WORK_ORDER_MISMATCH", (
                f"wo_key {d['wo_key']} != ожидаемый {facts.expected_wo_key}")
        elif (facts.expected_spec_hash is not None and d["spec_hash"]
              and facts.expected_spec_hash != d["spec_hash"]):
            code, reason = "WORK_ORDER_MISMATCH", "spec_hash не совпадает с Work Order"

    if code:
        with session_scope() as s:
            row = s.get(ReviewPackage, pkg_id)
            row.status = "invalid"
            row.invalid_code = code
            row.invalid_reason = redact(reason)[:400]
            s.commit()
        audit.record("review.package.invalid", f"rpkg={pkg_id} code={code}")
        return False, code, reason
    return True, "", ""


def get_review_package(pkg_id: str) -> dict | None:
    with session_scope() as s:
        row = s.get(ReviewPackage, pkg_id)
        return row.to_dict() if row else None


def find_by_hash(content_hash_value: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(select(ReviewPackage).where(
            ReviewPackage.content_hash == content_hash_value)).scalars().first()
        return row.to_dict() if row else None
