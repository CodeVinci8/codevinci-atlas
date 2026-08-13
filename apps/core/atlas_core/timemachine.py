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

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from . import audit, autonomy, emergency, redaction
from .autonomy import Capability
from .db import session_scope
from .ids import new_id
from .orm import Checkpoint, Run, _iso
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


def _canon_cause(cause: str) -> str:
    """Канонический ``cause`` = ровно то, что хранится в БД (``String(80)``).

    call-19 finding 2: раньше content_hash считался по ПОЛНОМУ ``cause``, а строка
    хранила ``cause[:80]`` — любой ``cause`` длиннее 80 давал мгновенный ``TAMPERED``.
    Единая канонизация до хеша и записи устраняет расхождение."""
    return (cause or "")[:80]


def _payload_from_inputs(i: CheckpointInputs, *, actor: str, correlation_id: str,
                         created_at: datetime) -> dict:
    """Canonical immutable-payload для content_hash. ДОЛЖЕН совпадать
    поле-в-поле с :meth:`orm.Checkpoint.immutable_payload` (сверяется при verify).

    call-19 finding 2/3: ``cause`` канонизируется как хранится (``[:80]``); ``actor``/
    ``correlation_id``/``created_at`` включены в хеш (§21 immutability — их подмена в
    durable-состоянии теперь даёт ``TAMPERED``)."""
    return {
        "project_id": i.project_id, "vp_key": i.vp_key,
        "work_order_id": i.work_order_id, "run_id": i.run_id,
        "db_revision": i.db_revision, "branch": i.branch, "base_sha": i.base_sha,
        "head_sha": i.head_sha, "worktree_status": i.worktree_status,
        "patch_hash": i.patch_hash, "artifact_hashes": i.artifact_hashes,
        "profile_alias": i.profile_alias, "model": i.model, "effort": i.effort,
        "session_ids": i.session_ids, "grant_id": i.grant_id, "grant_hash": i.grant_hash,
        "test_refs": i.test_refs, "evidence_refs": i.evidence_refs,
        "handoff_ref": i.handoff_ref, "cause": _canon_cause(i.cause),
        "actor": actor, "correlation_id": correlation_id, "created_at": _iso(created_at),
    }


def _validate_ref_schema(i: CheckpointInputs) -> None:
    """Fail-closed схема файловых ссылок (call-19 finding 1). Любая запись, объявляющая
    файловый ``path`` без ожидаемого хеша — ``CHECKPOINT_MALFORMED_REF``: иначе
    ``verify_checkpoint`` молча пропустил бы её (обход verified-hashes)."""
    for j, a in enumerate(i.artifact_hashes or []):
        if not isinstance(a, dict) or not a.get("path") or not a.get("sha"):
            raise TimeMachineError("CHECKPOINT_MALFORMED_REF",
                                   f"artifact_hashes[{j}] требует непустые path и sha")
    for j, t in enumerate(i.test_refs or []):
        if not isinstance(t, dict):
            raise TimeMachineError("CHECKPOINT_MALFORMED_REF", f"test_refs[{j}] должен быть объектом")
        if t.get("path") and not (t.get("hash") or t.get("sha")):
            raise TimeMachineError("CHECKPOINT_MALFORMED_REF",
                                   f"test_refs[{j}] с path требует hash/sha")
        if not t.get("name") and not t.get("path"):
            raise TimeMachineError("CHECKPOINT_MALFORMED_REF", f"test_refs[{j}] требует name или path")
    for j, e in enumerate(i.evidence_refs or []):
        if isinstance(e, dict):
            if e.get("path") and not (e.get("sha") or e.get("hash")):
                raise TimeMachineError("CHECKPOINT_MALFORMED_REF",
                                       f"evidence_refs[{j}] с path требует sha/hash")
            if not e.get("ref") and not e.get("path"):
                raise TimeMachineError("CHECKPOINT_MALFORMED_REF",
                                       f"evidence_refs[{j}] требует ref или path")
        elif not isinstance(e, str):
            raise TimeMachineError("CHECKPOINT_MALFORMED_REF",
                                   f"evidence_refs[{j}] должен быть строкой или объектом")


def _reject_secrets(i: CheckpointInputs, *, actor: str, correlation_id: str) -> None:
    """Fail-closed §30 (call-18 finding 2): ни одно durable-поле checkpoint не
    должно содержать секрет, email, cookie, transcript-маркер или raw auth/
    credential/profile path. Проверяем КАЖДОЕ поле (не только фикстуру) ДО записи и
    **отвергаем**, а не молча редактируем — immutable content-addressed запись не
    имеет права хранить секрет ни для одного внутреннего caller."""
    def _check(label: str, value: str | None) -> None:
        if value and redaction.is_sensitive(str(value)):
            raise TimeMachineError(
                "SECRET_IN_CHECKPOINT",
                f"поле {label} содержит потенциальный секрет/raw auth path — "
                "checkpoint отклонён (§30)")
    _check("actor", actor)
    _check("correlation_id", correlation_id)
    for label in ("project_id", "vp_key", "work_order_id", "run_id", "db_revision",
                  "branch", "base_sha", "head_sha", "worktree_status", "patch_hash",
                  "profile_alias", "model", "effort", "grant_id", "grant_hash",
                  "handoff_ref", "cause"):
        _check(label, getattr(i, label))
    for j, sid in enumerate(i.session_ids or []):
        _check(f"session_ids[{j}]", str(sid))
    for j, ev in enumerate(i.evidence_refs or []):
        if isinstance(ev, dict):
            _check(f"evidence_refs[{j}].ref", str(ev.get("ref", "")))
            _check(f"evidence_refs[{j}].path", str(ev.get("path", "")))
        else:
            _check(f"evidence_refs[{j}]", str(ev))
    for j, a in enumerate(i.artifact_hashes or []):
        if isinstance(a, dict):
            _check(f"artifact_hashes[{j}].path", str(a.get("path", "")))
    for j, t in enumerate(i.test_refs or []):
        if isinstance(t, dict):
            _check(f"test_refs[{j}].name", str(t.get("name", "")))
            _check(f"test_refs[{j}].path", str(t.get("path", "")))


def create_checkpoint(i: CheckpointInputs, *, actor: str = "core",
                      correlation_id: str = "") -> dict:
    """Создать immutable content-addressed checkpoint."""
    import json as _json
    _reject_secrets(i, actor=actor, correlation_id=correlation_id)
    _validate_ref_schema(i)  # malformed файловая ссылка → отказ (call-19 finding 1)
    created = _utcnow()
    # content_hash покрывает и actor/correlation_id/created_at, и канонический cause —
    # payload ДОЛЖЕН совпасть с Checkpoint.immutable_payload() (иначе verify → TAMPERED).
    ch = content_hash(_payload_from_inputs(i, actor=actor, correlation_id=correlation_id,
                                           created_at=created))
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
            handoff_ref=i.handoff_ref, cause=_canon_cause(i.cause), actor=actor,
            correlation_id=correlation_id, content_hash=ch, created_at=created)
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


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_artifact_path(raw: str, root: str | None) -> Path:
    """Разрешить путь артефакта: абсолютный — как есть; относительный — от
    доверенного ``root`` (repo checkpoint), иначе от cwd."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (Path(root) / p) if root else p


def _referenced_artifacts(cp: dict) -> list[tuple[str, str, str]]:
    """Список ``(label, path, expected_sha)`` для КАЖДОЙ записи checkpoint,
    объявляющей файловый ``path`` c ожидаемым хешем — по всем трём коллекциям
    (``artifact_hashes``/``test_refs``/``evidence_refs``). Чисто логические ссылки
    (``test_refs`` без path, строковые ``evidence_refs``) файлового артефакта не
    имеют и покрываются только ``content_hash`` (их значение нельзя изменить, не
    сломав content_hash)."""
    # call-19 finding 1: включаем КАЖДУЮ запись с файловым path, даже без ожидаемого
    # хеша (expected=""), чтобы verify не «молча пропустил» malformed-ссылку, а
    # инвалидировал checkpoint. Пустой expected → MALFORMED_ARTIFACT_REF в verify.
    out: list[tuple[str, str, str]] = []
    for a in cp.get("artifact_hashes", []) or []:
        if isinstance(a, dict) and a.get("path"):
            out.append(("artifact", str(a["path"]), str(a.get("sha") or "")))
    for t in cp.get("test_refs", []) or []:
        if isinstance(t, dict) and t.get("path"):
            out.append(("test", str(t["path"]), str(t.get("hash") or t.get("sha") or "")))
    for e in cp.get("evidence_refs", []) or []:
        if isinstance(e, dict) and e.get("path"):
            out.append(("evidence", str(e["path"]), str(e.get("sha") or e.get("hash") or "")))
    return out


def _verify_referenced_artifacts(cp: dict, root: str | None) -> tuple[bool, str]:
    """Пересчитать sha256 КАЖДОГО объявленного файлового артефакта checkpoint и
    сверить с записанным (call-18 finding 1). Отсутствующий файл → ``ARTIFACT_MISSING``,
    изменённый → ``ARTIFACT_ALTERED``. Это делает «verified hashes» §21 фактическим,
    а не сверкой строки БД самой с собой: удаление/подмена реального артефакта теперь
    инвалидирует checkpoint (INVALID_EVIDENCE)."""
    for _label, raw, expected in _referenced_artifacts(cp):
        if not expected:
            return False, "MALFORMED_ARTIFACT_REF"  # path без хеша — не verified (finding 1)
        p = _resolve_artifact_path(raw, root)
        if not p.exists() or not p.is_file():
            return False, "ARTIFACT_MISSING"
        try:
            actual = _sha256_file(p)
        except OSError:
            return False, "ARTIFACT_MISSING"
        if actual != expected:
            return False, "ARTIFACT_ALTERED"
    return True, ""


def verify_checkpoint(checkpoint_id: str, *, root: str | None = None,
                      verify_artifacts: bool = True) -> tuple[bool, str]:
    """Проверить целостность checkpoint. Два уровня:

    1. пересчёт ``content_hash`` над immutable-payload (tamper строки БД → ``TAMPERED``);
    2. **пересчёт реальных файловых артефактов** по записанным хешам (call-18
       finding 1): изменённый/удалённый артефакт → ``ARTIFACT_ALTERED``/
       ``ARTIFACT_MISSING`` = **invalid evidence** (§21 «verified hashes»).

    Относительные пути артефактов разрешаются от ``root`` (по умолчанию — доверенный
    repo проекта checkpoint), иначе от cwd. ``verify_artifacts=False`` оставляет
    только проверку content_hash (для контекстов без доступа к дереву артефактов)."""
    with session_scope() as s:
        row = s.get(Checkpoint, checkpoint_id)
        if row is None:
            return False, "NOT_FOUND"
        recomputed = content_hash(row.immutable_payload())
        if recomputed != row.content_hash:
            return False, "TAMPERED"
        cp = row.to_dict()
    if not verify_artifacts or not _referenced_artifacts(cp):
        return True, ""
    art_root = root
    if art_root is None:
        try:
            art_root = _trusted_repo_path(cp.get("project_id") or None)
        except Exception:
            art_root = None
    return _verify_referenced_artifacts(cp, art_root)


def _verify_or_raise(checkpoint_id: str, *, root: str | None = None) -> dict:
    ok, reason = verify_checkpoint(checkpoint_id, root=root)
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
    """Безопасное УНИКАЛЬНОЕ имя новой feature-ветки для replay (§21). Никогда не
    source. call-11 fix (finding 4): каждый replay одного checkpoint получает НОВУЮ
    ветку (свежий token), поэтому повторный replay не падает на существующей ветке
    и создаёт новый Run/безопасную ветку по контракту."""
    import uuid
    ck = checkpoint_id.split("_")[-1][:6].lower()
    uniq = uuid.uuid4().hex[:8]  # энтропийный токен на каждый replay (без коллизий)
    return f"atlas/replay-{ck}-{uniq}"


def _replay_branch_pattern(checkpoint_id: str) -> str:
    """Паттерн имени replay-ветки для preview (не конкретное случайное имя). call-11
    audit (finding 2): реальный replay берёт свежий уникальный token на каждый вызов,
    поэтому preview НЕ должен обещать точное имя, которое реальный replay не
    использует — показываем стабильный паттерн, согласованный с UI/API."""
    ck = checkpoint_id.split("_")[-1][:6].lower()
    return f"atlas/replay-{ck}-<уникальный-токен>"


def _trusted_repo_path(project_id: str | None) -> str | None:
    """Доверенный локальный checkout/worktree-путь проекта из **durable Atlas-
    состояния** (§13.4/§35), НЕ из аргументов каллера (call-11 audit, finding 2):
    активный Worktree проекта, иначе canonical ``source_location`` для local_git.
    Путь обязан быть git-репозиторием; иначе None → replay fail-closed (не создаёт
    Run без материализации ветки)."""
    if not project_id:
        return None
    from .orm import Project, Worktree
    path: str | None = None
    with session_scope() as s:
        wt = s.execute(
            select(Worktree)
            .where(Worktree.project_id == project_id, Worktree.status == "active")
            .order_by(Worktree.created_at.desc())).scalars().first()
        if wt is not None and wt.path:
            path = wt.path
        else:
            p = s.get(Project, project_id)
            if p is not None and p.source_kind == "local_git" and p.source_location:
                path = p.source_location
    if not path:
        return None
    # Валидируем, что путь — рабочее git-дерево (без этого branch-материализация
    # невозможна; лучше явный отказ, чем «Run без ветки»).
    return path if _git(path, "rev-parse", "--git-dir").returncode == 0 else None


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
    # verified hashes (§21): относительные пути артефактов checkpoint разрешаются от
    # replay-repo; изменённый/удалённый артефакт → InvalidCheckpointError (fail-closed).
    cp = _verify_or_raise(checkpoint_id, root=repo_path)

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
    # call-12 fix (finding 1): для local_git (repo-имя не выводимо, eval_repo=None)
    # авторитетный exact-scope — это МУТИРУЕМЫЙ checkout (workspace). Иначе grant того
    # же project_id с произвольными allowed_repos/allowed_bases разрешил бы ветку в
    # любом локальном checkout. Требуем, чтобы grant ЯВНО перечислял этот workspace в
    # workspace_allowlist (fail-closed WORKSPACE_NOT_ALLOWED). Для github repo-имя уже
    # даёт exact-scope, поэтому workspace там не навязываем.
    eval_workspace = repo_path if (eval_repo is None and repo_path) else None
    dec = autonomy.evaluate(Capability.REPO_WRITE.value, grant_id=grant_id,
                            project_id=trusted_project, repo=eval_repo, base=None,
                            environment=None, workspace=eval_workspace)
    if not dec.permitted:
        raise TimeMachineError(dec.reason_code,
                               f"replay требует capability repo_write в выведенном scope ({dec.reason_code})")

    # call-12 fix (finding 4): checkpoint без head_sha НЕвоспроизводим — fail-closed
    # ДО создания ветки/Run (не baseline-подмена). Contract §21: replay воспроизводит
    # именно head-состояние checkpoint. Пустой head_sha при валидном хеше → отказ.
    if not cp["head_sha"]:
        raise TimeMachineError("CHECKPOINT_NO_HEAD",
                               "checkpoint без head_sha — состояние checkpoint невоспроизводимо")

    new_branch = _safe_replay_branch(cp["branch"], checkpoint_id)
    source_head_before = None
    if repo_path:
        source_head_before = _git_head(repo_path, cp["branch"])
        # call-11 fix (finding 1): воспроизводим СОСТОЯНИЕ checkpoint (head_sha), а не
        # baseline. Ветка создаётся строго от head_sha (см. CHECKPOINT_NO_HEAD выше) —
        # иначе изменения base..head теряются.
        r = _git(repo_path, "branch", new_branch, cp["head_sha"])
        if r.returncode != 0:
            raise TimeMachineError("BRANCH_FAILED", redact((r.stderr or r.stdout))[:160])
        # Проверяем, что новая ветка указывает именно на состояние checkpoint (head_sha).
        new_head = _git_head(repo_path, new_branch)
        if new_head and new_head != cp["head_sha"]:
            raise TimeMachineError("REPLAY_STATE_MISMATCH",
                                   "новая ветка не соответствует head_sha checkpoint")
        source_head_after = _git_head(repo_path, cp["branch"])
        if source_head_before is not None and source_head_after != source_head_before:
            raise TimeMachineError("SOURCE_REWRITTEN", "source-ветка была изменена — откат")

    # call-15 fix (finding 2): Emergency Stop мог начаться ПОСЛЕ первичной проверки
    # (выше) и до создания Run — иначе replay материализовал бы ветку и создал новый
    # QUEUED Run уже при активном Stop (нарушение «запрещены новые jobs»). Барьер
    # НЕПОСРЕДСТВЕННО перед созданием Run; уже материализованную replay-ветку (branch
    # без Run — не job) откатываем. source-ветка не трогается.
    if emergency.blocks_new_jobs():
        _rollback_replay_branch(repo_path, new_branch)
        raise TimeMachineError(
            "EMERGENCY_STOP",
            "Emergency Stop активен перед созданием Run: replay прерван (новый job запрещён)")

    # Новый Run (QUEUED); provider session/transcript НЕ восстанавливаются.
    # call-16 fix (finding 1): создание Run и проверка Stop НЕ атомарны. engage(), чей
    # снимок active-runs (_interrupt_active_runs) прошёл ДО durable-INSERT этого Run,
    # оставил бы Run QUEUED при активном Stop (job избежал бы прерывания). Поэтому:
    # (а) при ошибке INSERT откатываем ветку; (б) ПОСЛЕ INSERT повторно проверяем
    # blocks_new_jobs() и при видимом Stop отменяем только что созданный Run
    # (QUEUED→CANCELLED, данные целы) и откатываем ветку. _ENGAGING (ставится engage()
    # ДО снимка) и durable active держат blocks_new_jobs() НЕПРЕРЫВНО от начала engage,
    # поэтому любой Stop, перекрывший INSERT, здесь замечается — окно закрыто с двух
    # сторон (снимок engage ловит Run, вставленные до него; re-check ловит вставленные
    # во время/после снимка).
    try:
        new_run = _create_replay_run(cp, new_branch, profile_alias or cp["profile_alias"],
                                     grant_id, cause, actor, correlation_id)
    except Exception:
        _rollback_replay_branch(repo_path, new_branch)
        raise
    if emergency.blocks_new_jobs():
        _cancel_replay_run(new_run)
        _rollback_replay_branch(repo_path, new_branch)
        raise TimeMachineError(
            "EMERGENCY_STOP",
            "Emergency Stop активен сразу после создания Run: replay откатан "
            "(Run CANCELLED, ветка удалена)")
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


def replay_production(checkpoint_id: str, *, grant_id: str, profile_alias: str | None = None,
                      repo: str | None = None, actor: str = "owner",
                      correlation_id: str = "") -> dict:
    """ПРОИЗВОДСТВЕННЫЙ replay для HTTP-границы (call-11 audit, finding 2). Доверенный
    checkout-путь **выводится из durable Atlas-состояния** проекта checkpoint
    (:func:`_trusted_repo_path`), а НЕ из аргументов каллера — поэтому endpoint не
    принимает произвольный filesystem-путь. Материализует новую git-ветку из
    head_sha checkpoint **и** создаёт новый Run (не «Run без ветки»); fail-closed,
    если доверенный checkout не выводим."""
    cp = get_checkpoint(checkpoint_id)  # verify выполнит replay(); нам нужен project_id
    if cp is None:
        raise TimeMachineError("NOT_FOUND", "checkpoint не найден")
    trusted_path = _trusted_repo_path(cp.get("project_id"))
    if not trusted_path:
        raise TimeMachineError(
            "NO_TRUSTED_CHECKOUT",
            "доверенный checkout проекта не выводим из durable-состояния — "
            "replay не создаёт Run без материализации ветки")
    return replay(checkpoint_id, grant_id=grant_id, profile_alias=profile_alias,
                  repo_path=trusted_path, repo=repo, actor=actor,
                  correlation_id=correlation_id)


def replay_preview(checkpoint_id: str, *, grant_id: str = "",
                   profile_alias: str | None = None) -> dict:
    """Read-only превью replay (ничего не создаёт): verify + целевой безопасный
    паттерн ветки + свежесть grant. Для Web «replay preview».

    call-11 audit (finding 2): показываем ``target_branch_pattern`` (паттерн), а не
    конкретное случайное имя — реальный replay выберет свежий уникальный token, и
    обещать точное имя здесь означало бы рассинхронизацию UI/API-истины."""
    cp = _verify_or_raise(checkpoint_id)
    pattern = _replay_branch_pattern(checkpoint_id)
    fresh, why = (_grant_is_fresh(grant_id) if grant_id else (False, "NO_GRANT"))
    return {
        "read_only": True, "checkpoint_id": checkpoint_id, "verified_hashes": True,
        "source_branch": cp["branch"], "target_branch_pattern": pattern,
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
    profile/model, artifacts, test/evidence, outcome(cause).

    call-11 fix (finding 3): ОБА checkpoint верифицируются (content-hash) через
    _verify_or_raise — изменённый checkpoint не принимается как источник сравнения
    (fail-closed: tampered → INVALID_EVIDENCE), а не читается как валидный."""
    a = _verify_or_raise(cp_a)
    b = _verify_or_raise(cp_b)

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


def _rollback_replay_branch(repo_path: str | None, branch: str | None) -> None:
    """Откат orphan replay-ветки (branch без Run — не job). source не трогается."""
    if repo_path and branch:
        _git(repo_path, "branch", "-D", branch)


def _cancel_replay_run(run_id: str) -> None:
    """Отменить только что созданный QUEUED replay-Run (→CANCELLED, данные целы)."""
    with session_scope() as s:
        r = s.get(Run, run_id)
        if r is not None and r.state not in ("SUCCEEDED", "FAILED", "CANCELLED"):
            r.state = "CANCELLED"
            r.blocker = "Emergency Stop"
            r.updated_at = _utcnow()
            r.version = (r.version or 1) + 1
            s.commit()


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
