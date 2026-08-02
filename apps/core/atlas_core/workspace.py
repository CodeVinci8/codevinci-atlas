"""Сервис Project Workspace (Master Spec §35, VP-2).

Оркестрирует подключение/отключение проектов, персистентность источника и
read-only baseline, безопасные worktree и writer-аренды, сборку Project
Overview. Содержимое репозитория/архива/инструкций/вывода — данные; они не
расширяют права и не хранят credentials (§30.2).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select

from . import audit, gitbaseline
from .db import session_scope
from .ids import new_id
from .orm import GitBaseline, Project, Worktree
from .redaction import contains_secret, redact
from .settings import Settings, load_settings
from .worktrees import compute_worktree_path, create_git_worktree
from .wsleases import WorktreeLeaseService
from .wspaths import PathTraversalError, WorkspaceGuard, default_roots

SOURCE_KINDS = ("local_git", "github", "archive", "empty")
_STALE_DEFAULT_S = 300


class WorkspaceError(Exception):
    code = "WORKSPACE_ERROR"

    def __init__(self, reason: str, *, code: str | None = None):
        if code:
            self.code = code
        self.reason = redact(reason)
        super().__init__(f"{self.code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


def parse_github(ref: str) -> tuple[str, str]:
    """Вернуть (sanitized_https_url, owner/repo). Отклоняет credential-URL."""
    raw = (ref or "").strip()
    if "@" in raw and "://" in raw and re.search(r"://[^/@]*@", raw):
        raise WorkspaceError("GitHub URL с credentials запрещён", code="POLICY_DENIED")
    if contains_secret(raw):
        raise WorkspaceError("GitHub URL содержит секрет", code="POLICY_DENIED")
    m = re.search(r"github\.com[/:]([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?/?$", raw)
    if not m:
        m = re.match(r"^([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?$", raw)
    if not m:
        raise WorkspaceError(f"не распознан GitHub-репозиторий: {raw!r}", code="OUTPUT_INVALID")
    owner, repo = m.group(1), m.group(2)
    return f"https://github.com/{owner}/{repo}", f"{owner}/{repo}"


class WorkspaceService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.roots = default_roots(self.settings.data_dir)
        self.roots.ensure_dirs()
        self.guard = WorkspaceGuard(self.roots.all())
        self.db_path = self.settings.db_path

    # --- подключение источников --------------------------------------------
    def connect_project(self, name: str, source_kind: str, *, path: str | None = None,
                        github_ref: str | None = None, archive_path: str | None = None,
                        collect: bool = True) -> dict:
        if source_kind not in SOURCE_KINDS:
            raise WorkspaceError(f"неизвестный тип источника: {source_kind!r}", code="OUTPUT_INVALID")
        name = (name or "").strip() or "project"
        pid = new_id("proj")
        location = ""
        ref = ""
        baseline_repo: str | None = None

        if source_kind == "local_git":
            if not path:
                raise WorkspaceError("для local_git требуется path", code="OUTPUT_INVALID")
            canonical = self.guard.ensure_within(path, must_exist=True)  # allowlist + realpath
            if not gitbaseline.is_git_repo(canonical):
                raise WorkspaceError("путь не является git-репозиторием", code="OUTPUT_INVALID")
            location = canonical
            baseline_repo = canonical

        elif source_kind == "github":
            location, ref = parse_github(github_ref or "")
            if path:  # опциональный локальный клон (read-only анализ)
                canonical = self.guard.ensure_within(path, must_exist=True)
                if gitbaseline.is_git_repo(canonical):
                    baseline_repo = canonical

        elif source_kind == "archive":
            if not archive_path or not os.path.isfile(archive_path):
                raise WorkspaceError("архив не найден", code="OUTPUT_INVALID")
            from .archives import safe_extract
            slug = re.sub(r"[^a-z0-9]+", "-", (name.lower()))[:32].strip("-") or "src"
            subdir = f"{slug}-{new_id('intk').split('_')[1][:10]}"
            summary = safe_extract(archive_path, self.roots.intake_root, subdir)
            location = summary["extracted_to"]  # read-only intake
            if gitbaseline.is_git_repo(location):
                baseline_repo = location

        elif source_kind == "empty":
            location = ""

        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Project(id=pid, name=name, source_kind=source_kind,
                          source_location=location, source_ref=ref,
                          status="connected", created_at=now, updated_at=now))
            s.commit()
        audit.record("project.connected", f"project={pid} kind={source_kind}")

        if collect and baseline_repo:
            self._collect_and_store(pid, baseline_repo)
        return self.overview(pid)

    # --- baseline -----------------------------------------------------------
    def _collect_and_store(self, project_id: str, repo_path: str) -> dict:
        bl = gitbaseline.collect_baseline(repo_path)
        with session_scope() as s:
            s.add(GitBaseline(
                id=new_id("bl"), project_id=project_id, branch=bl["branch"], head=bl["head"],
                remotes_json=json.dumps(bl["remotes"], ensure_ascii=False),
                dirty=bl["dirty"], porcelain_json=json.dumps(bl["porcelain"], ensure_ascii=False),
                porcelain_truncated=bl["porcelain_truncated"],
                tracked_total=bl["tracked_total"], tracked_changes=bl["tracked_changes"],
                untracked=bl["untracked"],
                instructions_json=json.dumps(bl["instructions"], ensure_ascii=False),
                package_managers_json=json.dumps(bl["package_managers"], ensure_ascii=False),
                baseline_commands_json=json.dumps(bl["baseline_commands"], ensure_ascii=False),
                secret_scan_json=json.dumps(bl["secret_scan"], ensure_ascii=False),
                content_hash=bl["content_hash"], observed_at=datetime.now(timezone.utc)))
            s.commit()
        audit.record("project.baseline", f"project={project_id} hash={bl['content_hash']} dirty={bl['dirty']}")
        return bl

    def refresh_baseline(self, project_id: str) -> dict:
        p = self._project(project_id)
        repo = self._repo_path(p)
        if not repo:
            raise WorkspaceError("у проекта нет анализируемого git-источника", code="OUTPUT_INVALID")
        self._collect_and_store(project_id, repo)
        return self.overview(project_id)

    # --- worktree + lease ---------------------------------------------------
    def create_worktree(self, project_id: str, branch: str, *, holder: str = "builder") -> dict:
        p = self._project(project_id)
        repo = self._repo_path(p)
        if not repo:
            raise WorkspaceError("нельзя создать worktree без git-источника", code="OUTPUT_INVALID")
        wt_path = compute_worktree_path(self.guard, self.roots.worktrees_root, project_id, branch)
        leases = WorktreeLeaseService(self.db_path)
        try:
            lease = leases.acquire(project_id=project_id, worktree=wt_path, holder=holder)
            try:
                create_git_worktree(repo, wt_path, branch)
            except Exception:
                leases.release(lease.id)  # откат аренды при неудаче git
                raise
            with session_scope() as s:
                s.add(Worktree(id=new_id("wt"), project_id=project_id, branch=branch,
                               path=wt_path, status="active",
                               created_at=datetime.now(timezone.utc)))
                s.commit()
            audit.record("worktree.created", f"project={project_id} branch={branch} lease={lease.id}")
        finally:
            leases.close()
        return self.overview(project_id)

    def try_acquire_writer(self, project_id: str, worktree_path: str, *, holder: str = "builder2") -> dict:
        """Попытка второго writer — должна детерминированно отклоняться."""
        leases = WorktreeLeaseService(self.db_path)
        try:
            lease = leases.acquire(project_id=project_id, worktree=worktree_path, holder=holder)
            return {"acquired": True, "lease_id": lease.id}
        finally:
            leases.close()

    # --- отключение (без удаления) -----------------------------------------
    def disconnect_project(self, project_id: str) -> dict:
        p = self._project(project_id)
        with session_scope() as s:
            row = s.get(Project, project_id)
            row.status = "disconnected"
            row.disconnected_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
            s.commit()
        audit.record("project.disconnected",
                     f"project={project_id} (источник/worktree/архив НЕ удалены)")
        _ = p
        return self.overview(project_id)

    # --- запросы ------------------------------------------------------------
    def list_projects(self) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()
            return [self._summary(r, s) for r in rows]

    def overview(self, project_id: str, *, stale_after_s: int = _STALE_DEFAULT_S) -> dict:
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise WorkspaceError(f"проект не найден: {project_id}", code="OUTPUT_INVALID")
            bl_row = s.execute(
                select(GitBaseline).where(GitBaseline.project_id == project_id)
                .order_by(GitBaseline.observed_at.desc()).limit(1)).scalars().first()
            wts = s.execute(
                select(Worktree).where(Worktree.project_id == project_id)
                .order_by(Worktree.created_at.desc())).scalars().all()
            worktrees = [w.to_dict() for w in wts]
            baseline = self._baseline_dict(bl_row) if bl_row else None
            project = p.to_dict()

        repo = self._repo_path(p)
        live = gitbaseline.quick_state(repo) if repo else {"accessible": False, "head": "", "dirty": False}
        lease_state = self._lease_state(worktrees)
        state = self._compute_state(project, baseline, live, repo)
        return {
            "project": project,
            "baseline": baseline,
            "live": live,
            "worktrees": worktrees,
            "lease": lease_state,
            "state": state,
            "stale": state == "stale",
            "next_action": self._next_action(state, project, lease_state),
        }

    # --- внутреннее ---------------------------------------------------------
    def _project(self, project_id: str) -> Project:
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise WorkspaceError(f"проект не найден: {project_id}", code="OUTPUT_INVALID")
            s.expunge(p)
            return p

    def _repo_path(self, p: Project) -> str | None:
        if p.source_kind in ("local_git", "archive") and p.source_location:
            if os.path.isdir(p.source_location) and gitbaseline.is_git_repo(p.source_location):
                return p.source_location
        if p.source_kind == "github" and p.source_location:
            # github без локального клона baseline не имеет; но если был собран —
            # baseline_repo хранится через canonical_path в baseline.
            pass
        return None

    def _baseline_dict(self, row: GitBaseline) -> dict:
        return {
            "branch": row.branch, "head": row.head,
            "remotes": json.loads(row.remotes_json),
            "dirty": row.dirty,
            "porcelain": json.loads(row.porcelain_json),
            "porcelain_truncated": row.porcelain_truncated,
            "tracked_total": row.tracked_total, "tracked_changes": row.tracked_changes,
            "untracked": row.untracked,
            "instructions": json.loads(row.instructions_json),
            "package_managers": json.loads(row.package_managers_json),
            "baseline_commands": json.loads(row.baseline_commands_json),
            "secret_scan": json.loads(row.secret_scan_json),
            "content_hash": row.content_hash,
            "observed_at": row.observed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

    def _lease_state(self, worktrees: list[dict]) -> dict:
        active = []
        leases = WorktreeLeaseService(self.db_path)
        try:
            for w in worktrees:
                lease = leases.active_lease(w["path"])
                if lease:
                    active.append({"worktree": w["path"], "lease_id": lease.id,
                                   "holder": lease.holder, "role": lease.role})
        finally:
            leases.close()
        return {"active": bool(active), "leases": active}

    def _summary(self, p: Project, s) -> dict:
        bl_row = s.execute(
            select(GitBaseline).where(GitBaseline.project_id == p.id)
            .order_by(GitBaseline.observed_at.desc()).limit(1)).scalars().first()
        d = p.to_dict()
        d["dirty"] = bool(bl_row.dirty) if bl_row else None
        d["branch"] = bl_row.branch if bl_row else None
        d["has_baseline"] = bl_row is not None
        return d

    @staticmethod
    def _compute_state(project: dict, baseline: dict | None, live: dict, repo: str | None) -> str:
        if project["status"] == "disconnected":
            return "disconnected"
        if project["source_kind"] == "empty":
            return "empty"
        if baseline is None:
            return "pending"
        if repo and not live["accessible"]:
            return "error"
        if repo and (live["head"] != baseline["head"] or live["dirty"] != baseline["dirty"]):
            return "stale"
        if baseline["dirty"]:
            return "dirty"
        return "clean"

    @staticmethod
    def _next_action(state: str, project: dict, lease_state: dict) -> str:
        if state == "disconnected":
            return "Проект отключён; переподключите источник для анализа."
        if state == "empty":
            return "Пустой проект: подключите git-путь, GitHub-репозиторий или архив."
        if state == "pending":
            return "Baseline не собран: укажите локальный клон для read-only анализа."
        if state == "error":
            return "Источник недоступен: проверьте путь репозитория (данные не изменялись)."
        if lease_state["active"]:
            return "Активна аренда writer; второй writer запрещён до release/reconcile."
        if state == "stale":
            return "Состояние изменилось: обновите baseline (refresh) перед действиями."
        if state == "dirty":
            return ("Есть незакоммиченные изменения (сохранены как есть); создайте "
                    "изолированный worktree atlas/vp-<n>-<slug>.")
        return "Готово: создайте worktree atlas/vp-<n>-<slug> для безопасной работы."


__all__ = ["WorkspaceService", "WorkspaceError", "parse_github", "SOURCE_KINDS",
           "PathTraversalError"]
