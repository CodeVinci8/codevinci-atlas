"""Time Machine — immutable content-addressed checkpoints (Master Spec §21).

Checkpoint неизменяем и адресуется по содержимому (``content_hash`` = sha256 над
canonical immutable-payload). Хранит DB revision, project/VP/WO/Run, branch/
base/head/worktree status, хеш патча, хеши артефактов, safe alias/model/effort,
provider session id (БЕЗ transcript), snapshot grant (хеш), ссылки/хеши тестов и
evidence, handoff-ref, причину, actor, correlation, UTC.

Операции: resume, replay (другой профиль), fork, compare, restore-preview,
rollback-preview, recovery. Defaults §21: replay → **новый Run + новая
безопасная feature-ветка**, **без rewrite/reset источника**, **без stale grant**,
verify хешей, **без credentials/transcripts**; destructive rollback недоступен
без **отдельного** grant. Протухший/изменённый checkpoint → invalid evidence.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit, autonomy, emergency
from .autonomy import Capability
from .db import session_scope
from .ids import new_id
from .orm import Checkpoint, Run
from .productmap import content_hash
from .redaction import redact

_GIT_TIMEOUT = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CheckpointInputs:
    project_id: str = ""
    vp_key: str = ""
    work_order_id: str = ""
    run_id: str = ""
    db_revision: str = ""
    branch: str = ""
    base_sha: str = ""
    head_sha: str = ""
    worktree_status: str = ""            # clean|dirty
    patch_hash: str = ""
    artifact_hashes: list[dict] = field(default_factory=list)  # [{path, sha}]
    profile_alias: str = ""
    model: str = ""
    effort: str = ""
    session_ids: list[str] = field(default_factory=list)        # id, НЕ transcript
    grant_id: str = ""
    grant_hash: str = ""
    test_refs: list[dict] = field(default_factory=list)         # [{name, hash}]
    evidence_refs: list[str] = field(default_factory=list)
    handoff_ref: str = ""
    cause: str = ""


def _payload_from_inputs(i: CheckpointInputs) -> dict:
    return {
        "project_id": i.project_id, "vp_key": i.vp_key,
        "work_order_id": i.work_order_id, "run_id": i.run_id,
        "db_revision": i.db_revision, "branch": i.branch, "base_sha": i.base_sha,
        "head_sha": i.head_sha, "worktree_status": i.worktree_status,
        "patch_hash": i.patch_hash, "artifact_hashes": i.artifact_hashes,
        "profile_alias": i.profile_alias, "model": i.model, "effort": i.effort,
        "session_ids": i.session_ids, "grant_id": i.grant_id, "grant_hash": i.grant_hash,
        "test_refs": i.test_refs, "evidence_refs": i.evidence_refs,
        "handoff_ref": i.handoff_ref, "cause": i.cause,
    }


def create_checkpoint(i: CheckpointInputs, *, actor: str = "core",
                      correlation_id: str = "") -> dict:
    """Создать immutable content-addressed checkpoint."""
    import json as _json
    ch = content_hash(_payload_from_inputs(i))
    cid = new_id("ckpt")
    with session_scope() as s:
        row = Checkpoint(
            id=cid, project_id=i.project_id, vp_key=i.vp_key,
            work_order_id=i.work_order_id, run_id=i.run_id, db_revision=i.db_revision,
            branch=i.branch, base_sha=i.base_sha, head_sha=i.head_sha,
            worktree_status=i.worktree_status, patch_hash=i.patch_hash,
            artifact_hashes_json=_json.dumps(i.artifact_hashes, ensure_ascii=False),
            profile_alias=i.profile_alias, model=i.model, effort=i.effort,
            session_ids_json=_json.dumps(i.session_ids, ensure_ascii=False),
            grant_id=i.grant_id, grant_hash=i.grant_hash,
            test_refs_json=_json.dumps(i.test_refs, ensure_ascii=False),
            evidence_refs_json=_json.dumps(i.evidence_refs, ensure_ascii=False),
            handoff_ref=i.handoff_ref, cause=i.cause[:80], actor=actor,
            correlation_id=correlation_id, content_hash=ch, created_at=_utcnow())
        s.add(row)
        s.commit()
        out = row.to_dict()
    audit.record("checkpoint.created", f"ckpt={cid} hash={ch[:20]} cause={i.cause[:40]}",
                 actor=actor, correlation_id=correlation_id)
    return out


def get_checkpoint(checkpoint_id: str) -> dict | None:
    with session_scope() as s:
        row = s.get(Checkpoint, checkpoint_id)
        return row.to_dict() if row else None


def list_checkpoints(*, project_id: str | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        stmt = select(Checkpoint).order_by(Checkpoint.created_at.desc(), Checkpoint.id.desc())
        if project_id:
            stmt = stmt.where(Checkpoint.project_id == project_id)
        rows = s.execute(stmt.limit(max(1, min(limit, 500)))).scalars().all()
        return [r.to_dict() for r in rows]


def verify_checkpoint(checkpoint_id: str) -> tuple[bool, str]:
    """Пересчитать content_hash над immutable-payload и сверить с хранимым.
    Расхождение → tamper/протухший checkpoint = **invalid evidence**."""
    with session_scope() as s:
        row = s.get(Checkpoint, checkpoint_id)
        if row is None:
            return False, "NOT_FOUND"
        recomputed = content_hash(row.immutable_payload())
        if recomputed != row.content_hash:
            return False, "TAMPERED"
    return True, ""


def _verify_or_raise(checkpoint_id: str) -> dict:
    ok, reason = verify_checkpoint(checkpoint_id)
    if not ok:
        audit.record("checkpoint.invalid", f"ckpt={checkpoint_id} reason={reason}")
        raise InvalidCheckpointError(reason)
    return get_checkpoint(checkpoint_id)


class InvalidCheckpointError(Exception):
    pass


class TimeMachineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _project_repo(project_id: str | None) -> str | None:
    """Доверенное имя repo (owner/repo) из хранимого project/source. Только для
    github-источника; иначе None (repo-scope не выводится из локального пути)."""
    if not project_id:
        return None
    from .orm import Project
    with session_scope() as s:
        p = s.get(Project, project_id)
        if p is None or p.source_kind != "github":
            return None
        return (p.source_ref or "").strip() or None


def _grant_is_fresh(grant_id: str) -> tuple[bool, str]:
    """Grant валиден и не протух (для replay/fork — не переиспользуем stale grant)."""
    d = autonomy.get_grant(grant_id) if grant_id else None
    if not d:
        return False, "NO_GRANT"
    if d["state"] == "REVOKED":
        return False, "GRANT_REVOKED"
    if autonomy._is_expired(d) or d["state"] == "EXPIRED":
        return False, "GRANT_EXPIRED"
    if d["state"] != "ACTIVE":
        return False, "GRANT_INACTIVE"
    return True, ""


def _safe_replay_branch(source_branch: str, checkpoint_id: str) -> str:
    """Безопасное имя новой feature-ветки для replay (§21). Никогда не source."""
    token = checkpoint_id.split("_")[-1][:6].lower() or new_id("x").split("_")[-1][:6].lower()
    return f"atlas/replay-{token}"


def replay(checkpoint_id: str, *, grant_id: str, profile_alias: str | None = None,
           repo_path: str | None = None, repo: str | None = None, base: str | None = None,
           environment: str | None = None, actor: str = "owner",
           cause: str = "replay", correlation_id: str = "") -> dict:
    """Replay checkpoint: создаёт **новый Run** и **новую безопасную feature-
    ветку**, НЕ переписывая/не сбрасывая source-ветку, НЕ переиспользуя stale
    grant, verify хешей, без восстановления credentials/transcripts.

    Fail-closed (VP-7 D-fix, bypass B): весь authoritative scope **выводится из
    checkpoint и хранимого project/source** (доверенный источник), а НЕ из
    аргументов каллера. Каллер-supplied ``repo`` может только **совпасть** с
    доверенным (или быть опущен) — никогда не расширяет и не подменяет scope.
    ``base``/``environment`` для replay из checkpoint **не выводятся** (в
    checkpoint есть только ``base_sha`` — не base-ветка; replay не деплоит),
    поэтому любое непустое caller-значение отвергается fail-closed
    (``BASE_NOT_DERIVABLE``/``ENVIRONMENT_NOT_DERIVABLE``) — не «тихо пропускаем
    сравнение». Grant должен разрешать ``repo_write`` в выведенном scope через
    :func:`autonomy.evaluate`. Emergency Stop (§19) запрещает replay как создание
    нового job."""
    cp = _verify_or_raise(checkpoint_id)

    # Emergency Stop: replay создаёт новый Run → запрещён при активном стопе.
    if emergency.blocks_new_jobs():
        raise TimeMachineError("EMERGENCY_STOP",
                               "Emergency Stop активен: replay (новый job) запрещён")

    # Никогда не переиспользуем stale grant checkpoint — требуется свежий valid.
    fresh, why = _grant_is_fresh(grant_id)
    if not fresh:
        raise TimeMachineError(why, f"replay требует свежий valid grant ({why})")

    # Доверенный scope из checkpoint/project. project_id — из checkpoint; repo —
    # из хранимого source (github). Grant, привязанный к другому проекту, отвергается.
    trusted_project = cp.get("project_id") or None
    trusted_repo = _project_repo(trusted_project)
    g = autonomy.get_grant(grant_id) or {}
    if g.get("project_id") and trusted_project and g["project_id"] != trusted_project:
        raise TimeMachineError("PROJECT_MISMATCH",
                               "grant привязан к другому проекту, чем checkpoint")
    # Каллер-repo может только совпасть с доверенным; если доверенный repo не
    # выводим (не github-source) — caller-repo недопустим (не даём утверждать то,
    # что нельзя проверить). eval_repo — ТОЛЬКО доверенный (без fallback на каллера).
    if repo is not None and trusted_repo and repo != trusted_repo:
        raise TimeMachineError("REPO_MISMATCH",
                               "caller repo не совпадает с repo checkpoint/project")
    if repo is not None and not trusted_repo:
        raise TimeMachineError("REPO_NOT_DERIVABLE",
                               "доверенный repo не выводим из project — caller repo недопустим")
    eval_repo = trusted_repo  # None → repo-scope каллером не утверждается

    # base/environment не выводятся из checkpoint → непустое caller-значение = отказ.
    if base:
        raise TimeMachineError(
            "BASE_NOT_DERIVABLE",
            "replay не принимает caller base: доверенная base-ветка не выводима из checkpoint")
    if environment:
        raise TimeMachineError(
            "ENVIRONMENT_NOT_DERIVABLE",
            "replay не деплоит: caller environment недопустим (scope не выводится из каллера)")

    # Grant должен явно разрешать repo_write в выведенном scope (не только «свежий»).
    # base/environment=None: authoritative-dimension для replay отсутствует.
    dec = autonomy.evaluate(Capability.REPO_WRITE.value, grant_id=grant_id,
                            project_id=trusted_project, repo=eval_repo, base=None,
                            environment=None)
    if not dec.permitted:
        raise TimeMachineError(dec.reason_code,
                               f"replay требует capability repo_write в выведенном scope ({dec.reason_code})")

    new_branch = _safe_replay_branch(cp["branch"], checkpoint_id)
    source_head_before = None
    if repo_path:
        source_head_before = _git_head(repo_path, cp["branch"])
        base = cp["base_sha"] or cp["head_sha"]
        r = _git(repo_path, "branch", new_branch, base)
        if r.returncode != 0:
            raise TimeMachineError("BRANCH_FAILED", redact((r.stderr or r.stdout))[:160])
        source_head_after = _git_head(repo_path, cp["branch"])
        if source_head_before is not None and source_head_after != source_head_before:
            raise TimeMachineError("SOURCE_REWRITTEN", "source-ветка была изменена — откат")

    # Новый Run (QUEUED); provider session/transcript НЕ восстанавливаются.
    new_run = _create_replay_run(cp, new_branch, profile_alias or cp["profile_alias"],
                                 grant_id, cause, actor, correlation_id)
    audit.record("checkpoint.replayed",
                 f"ckpt={checkpoint_id} run={new_run} branch={new_branch} grant={grant_id}",
                 actor=actor, correlation_id=correlation_id)
    return {
        "checkpoint_id": checkpoint_id, "new_run_id": new_run,
        "new_branch": new_branch, "source_branch": cp["branch"],
        "source_rewritten": False, "grant_id": grant_id,
        "profile_alias": profile_alias or cp["profile_alias"],
        "verified_hashes": True, "restored_credentials": False,
        "restored_transcript": False, "cause": cause,
    }


def replay_preview(checkpoint_id: str, *, grant_id: str = "",
                   profile_alias: str | None = None) -> dict:
    """Read-only превью replay (ничего не создаёт): verify + целевая безопасная
    ветка + свежесть grant. Для Web «replay preview»."""
    cp = _verify_or_raise(checkpoint_id)
    new_branch = _safe_replay_branch(cp["branch"], checkpoint_id)
    fresh, why = (_grant_is_fresh(grant_id) if grant_id else (False, "NO_GRANT"))
    return {
        "read_only": True, "checkpoint_id": checkpoint_id, "verified_hashes": True,
        "source_branch": cp["branch"], "target_branch": new_branch,
        "creates_new_run": True, "rewrites_source": False,
        "grant_id": grant_id, "grant_fresh": fresh,
        "blocker": ("" if fresh else why),
        "profile_alias": profile_alias or cp["profile_alias"],
        "restores_credentials": False, "restores_transcript": False,
    }


def fork(checkpoint_id: str, *, grant_id: str, profile_alias: str | None = None,
         repo_path: str | None = None, actor: str = "owner",
         correlation_id: str = "") -> dict:
    """Fork — как replay, но с явной причиной ``fork`` (дивергентная ветка)."""
    return replay(checkpoint_id, grant_id=grant_id, profile_alias=profile_alias,
                  repo_path=repo_path, actor=actor, cause="fork",
                  correlation_id=correlation_id)


def resume(checkpoint_id: str, *, grant_id: str = "", correlation_id: str = "") -> dict:
    """Resume-план из checkpoint (read model + verify). Не переписывает источник,
    не восстанавливает credentials/transcript. Если grant указан — проверяем
    свежесть (для продолжения работы), иначе только verify + план."""
    cp = _verify_or_raise(checkpoint_id)
    plan = {
        "checkpoint_id": checkpoint_id, "verified_hashes": True,
        "resume_run_id": cp["run_id"], "branch": cp["branch"],
        "head_sha": cp["head_sha"], "restored_credentials": False,
        "restored_transcript": False,
    }
    if grant_id:
        fresh, why = _grant_is_fresh(grant_id)
        plan["grant_fresh"] = fresh
        if not fresh:
            plan["blocker"] = why
    audit.record("checkpoint.resume.preview", f"ckpt={checkpoint_id}",
                 correlation_id=correlation_id)
    return plan


def compare(cp_a: str, cp_b: str) -> dict:
    """Сравнить два checkpoint. Показывает факт-различия §21: SHA, grant,
    profile/model, artifacts, test/evidence, outcome(cause)."""
    a = get_checkpoint(cp_a)
    b = get_checkpoint(cp_b)
    if a is None or b is None:
        raise TimeMachineError("NOT_FOUND", "checkpoint не найден")

    def _art_set(d):
        return {(x.get("path"), x.get("sha")) for x in d.get("artifact_hashes", [])}

    def _test_set(d):
        return {(x.get("name"), x.get("hash")) for x in d.get("test_refs", [])}

    diffs = {
        "head_sha": {"changed": a["head_sha"] != b["head_sha"], "a": a["head_sha"], "b": b["head_sha"]},
        "base_sha": {"changed": a["base_sha"] != b["base_sha"], "a": a["base_sha"], "b": b["base_sha"]},
        "grant": {"changed": a["grant_hash"] != b["grant_hash"],
                  "a": a["grant_id"], "b": b["grant_id"]},
        "profile": {"changed": a["profile_alias"] != b["profile_alias"],
                    "a": a["profile_alias"], "b": b["profile_alias"]},
        "model": {"changed": (a["model"], a["effort"]) != (b["model"], b["effort"]),
                  "a": f"{a['model']}/{a['effort']}", "b": f"{b['model']}/{b['effort']}"},
        "artifacts": {"changed": _art_set(a) != _art_set(b),
                      "only_a": sorted(str(x) for x in _art_set(a) - _art_set(b)),
                      "only_b": sorted(str(x) for x in _art_set(b) - _art_set(a))},
        "tests_evidence": {"changed": (_test_set(a) != _test_set(b))
                           or (set(a["evidence_refs"]) != set(b["evidence_refs"]))},
        "outcome": {"changed": a["cause"] != b["cause"], "a": a["cause"], "b": b["cause"]},
    }
    return {"a": cp_a, "b": cp_b, "diffs": diffs,
            "any_change": any(v.get("changed") for v in diffs.values())}


def restore_state_preview(checkpoint_id: str) -> dict:
    """Read-only превью восстановления состояния (ничего не мутирует)."""
    cp = _verify_or_raise(checkpoint_id)
    return {
        "read_only": True, "checkpoint_id": checkpoint_id,
        "would_set": {"branch": cp["branch"], "base_sha": cp["base_sha"],
                      "head_sha": cp["head_sha"], "db_revision": cp["db_revision"],
                      "worktree_status": cp["worktree_status"]},
        "restores_credentials": False, "restores_transcript": False,
        "note": "Превью только для чтения; фактическое восстановление требует явного действия.",
    }


def rollback_preview(checkpoint_id: str, *, grant_id: str = "") -> dict:
    """Read-only превью destructive rollback. **Недоступен без отдельного grant**
    с capability ``destructive_rollback`` (§21). Никогда не мутирует."""
    cp = _verify_or_raise(checkpoint_id)
    available = False
    reason = "DESTRUCTIVE_ROLLBACK_UNAVAILABLE"
    next_action = ("Destructive rollback требует отдельного grant с capability "
                   "destructive_rollback (§21).")
    if grant_id:
        dec = autonomy.evaluate(Capability.DESTRUCTIVE_ROLLBACK.value, grant_id=grant_id)
        available = dec.permitted
        if dec.permitted:
            reason = "PERMITTED_PREVIEW_ONLY"
            next_action = "Превью доступно; фактический rollback — отдельным явным действием."
        else:
            reason = dec.reason_code
            next_action = dec.next_action
    return {"read_only": True, "checkpoint_id": checkpoint_id, "available": available,
            "reason": reason, "next_action": next_action,
            "target_head": cp["head_sha"], "target_base": cp["base_sha"]}


def recover(checkpoint_id: str, *, grant_id: str = "", repo_path: str | None = None,
            actor: str = "owner", correlation_id: str = "") -> dict:
    """Recovery после прерывания: verify checkpoint и построить план восстановления
    в новой безопасной ветке, сохраняя критерии/evidence (не теряя их)."""
    cp = _verify_or_raise(checkpoint_id)
    result = replay(checkpoint_id, grant_id=grant_id, repo_path=repo_path, actor=actor,
                    cause="recovery", correlation_id=correlation_id) if grant_id else None
    return {
        "checkpoint_id": checkpoint_id, "verified_hashes": True,
        "preserved_evidence": cp["evidence_refs"], "preserved_tests": cp["test_refs"],
        "preserved_acceptance_ref": cp["handoff_ref"],
        "recovery": result,
        "note": "Критерии и evidence сохранены; recovery идёт в новую ветку/Run.",
    }


def _create_replay_run(cp: dict, branch: str, profile_alias: str, grant_id: str,
                       cause: str, actor: str, correlation_id: str) -> str:
    rid = new_id("run")
    with session_scope() as s:
        s.add(Run(
            id=rid, project_id=cp["project_id"], work_order_id=cp["work_order_id"],
            vp_key=cp["vp_key"], correlation_id=correlation_id or cp["correlation_id"],
            state="QUEUED", preset=profile_alias,
            dedup_key=f"replay:{cp['id']}:{branch}",
            next_action=f"Replay из checkpoint {cp['id']} в ветке {branch} ({cause}).",
            created_at=_utcnow(), updated_at=_utcnow(), version=1))
        s.commit()
    return rid


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          timeout=_GIT_TIMEOUT, check=False,
                          env={"PATH": _path(), "HOME": "/tmp", "GIT_TERMINAL_PROMPT": "0",
                               "LC_ALL": "C"})


def _git_head(repo: str, branch: str) -> str | None:
    r = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    return r.stdout.strip() if r.returncode == 0 else None


def _path() -> str:
    import os
    return os.environ.get("PATH", "/usr/bin:/bin")
