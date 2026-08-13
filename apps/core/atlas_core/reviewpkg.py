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
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import MergeEvidence, ReviewPackage
from .productmap import canonical_json, content_hash
from .redaction import redact


def sha256_file(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


# --- Durable, head-bound evidence store (§18.1, §20.2 authoritative gate) ----
#
# Проблема, которую закрывает store: ``authorize_merge_execution`` (execution
# boundary) обязан выводить факты (present/tamper) из **доверенного durable
# Atlas-состояния и пересчёта реальных файлов**, а не из caller-supplied
# ``evidence_present``/``artifacts``. Раньше он вызывал ``validate_review_package``
# с пустыми фактами, из-за чего evidence-backed RP ВСЕГДА падал в MISSING_EVIDENCE,
# а ``artifact_hashes`` вообще не сверялись (tamper незаметен). Store регистрирует
# для точного head реальные файлы (path+sha256); резолвер перечитывает их с диска.


def register_merge_evidence(head_sha: str, entries: list[dict], *, actor: str = "core",
                            correlation_id: str = "") -> list[dict]:
    """Зарегистрировать durable evidence для точного ``head_sha``.

    ``entries``: ``[{ref, path, kind?}]``. sha256 вычисляется из **реального файла**
    (fail-closed: отсутствующий файл → ``FileNotFoundError``, регистрация не
    фабрикует хеш). Идемпотентно по ``(head_sha, ref)`` — повторная регистрация
    обновляет path/sha/размер. Возвращает список ``to_dict()`` строк."""
    if not head_sha:
        raise ValueError("head_sha обязателен для регистрации evidence")
    out: list[dict] = []
    for e in entries:
        ref = e["ref"]
        path = e["path"]
        kind = e.get("kind", "artifact")
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"evidence-файл отсутствует/не файл: {path}")
        sha = sha256_file(p)
        size = p.stat().st_size
        with session_scope() as s:
            row = s.execute(select(MergeEvidence).where(
                MergeEvidence.head_sha == head_sha,
                MergeEvidence.ref == ref)).scalars().first()
            if row is None:
                row = MergeEvidence(
                    id=new_id("mev"), head_sha=head_sha, ref=ref, path=str(p.resolve()),
                    sha256=sha, kind=kind, size_bytes=size, actor=actor,
                    correlation_id=correlation_id, created_at=_now())
                s.add(row)
            else:
                row.path = str(p.resolve())
                row.sha256 = sha
                row.kind = kind
                row.size_bytes = size
            s.commit()
            out.append(row.to_dict())
    audit.record("review.evidence.registered",
                 f"head={head_sha[:12]} refs={len(entries)}", actor=actor,
                 correlation_id=correlation_id)
    return out


def list_merge_evidence(head_sha: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(MergeEvidence).where(
            MergeEvidence.head_sha == head_sha)).scalars().all()
        return [r.to_dict() for r in rows]


def resolve_review_facts(rp: dict, *, expected_head: str | None = None) -> ReviewFacts:
    """Построить :class:`ReviewFacts` из ДОВЕРЕННОГО durable evidence-store, не из
    caller-claims (§20.2, §2.C).

    Для каждого зарегистрированного под ``rp["head_sha"]`` evidence файл
    **перечитывается с диска** и хешируется заново:

    * файл существует → ссылка ``present``; ``artifacts[path] = актуальный sha``
      (перезаписанный/tampered файл даст sha≠зарегистрированного → в связке с
      ``rp.artifact_hashes`` это ``ARTIFACT_ALTERED``);
    * файл отсутствует/незарегистрирован под этим head → ссылка НЕ present →
      ``MISSING_EVIDENCE``;
    * evidence, зарегистрированное под другим head, невидимо (привязка к head).

    ``current_head`` берётся из ``expected_head`` (доверенный факт вызывающего
    boundary — фактический head PR), а НЕ из RP."""
    facts = ReviewFacts(current_head=expected_head)
    head = (rp or {}).get("head_sha") or None
    if not head:
        return facts
    present: list[str] = []
    artifacts: dict[str, str] = {}
    for r in list_merge_evidence(head):
        p = Path(r["path"])
        if not p.exists() or not p.is_file():
            continue  # файл отсутствует → ссылка останется missing (fail-closed)
        actual = sha256_file(p)                  # пересчёт реального файла
        artifacts[r["path"]] = actual            # для перекрёстной сверки с artifact_hashes
        # present ТОЛЬКО при совпадении с ЗАРЕГИСТРИРОВАННЫМ в store sha256: tampered
        # ref-only файл (не входящий в artifact_hashes) НЕ считается present (fail-closed,
        # call-15 finding 1 — store самодостаточно защищает от подмены).
        if actual == r["sha256"]:
            present.append(r["ref"])
    facts.evidence_present = present
    facts.artifacts = artifacts
    return facts


def verify_evidence_for_refs(head_sha: str, refs: list[str]) -> tuple[bool, str, str]:
    """Fail-closed сверка КАЖДОЙ объявленной evidence-ссылки с durable store и
    РЕАЛЬНЫМ файлом — **независимо** от ``artifact_hashes`` (call-15 finding 1).

    Возвращает ``(ok, code, detail)``: ссылка не зарегистрирована под этим head или
    файл отсутствует → ``MISSING_EVIDENCE``; текущий sha файла ≠ сохранённого
    ``MergeEvidence.sha256`` → ``ARTIFACT_ALTERED``. Это делает store
    самодостаточным: подмена зарегистрированного файла деним даже если он не
    перечислен в ``ReviewPackage.artifact_hashes``."""
    if not head_sha:
        return False, "MISSING_EVIDENCE", "нет head_sha для сверки evidence"
    rows = {r["ref"]: r for r in list_merge_evidence(head_sha)}
    for ref in refs:
        row = rows.get(ref)
        if row is None:
            return False, "MISSING_EVIDENCE", f"evidence не зарегистрировано под head: {ref}"
        p = Path(row["path"])
        if not p.exists() or not p.is_file():
            return False, "MISSING_EVIDENCE", f"evidence-файл отсутствует: {ref}"
        if sha256_file(p) != row["sha256"]:
            return False, "ARTIFACT_ALTERED", f"evidence изменён относительно store: {ref}"
    return True, "", ""
