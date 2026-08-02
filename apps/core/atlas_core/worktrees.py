"""Безопасное создание worktree (Master Spec §13.4, §35, VP-2).

Ветка обязана соответствовать ``atlas/vp-<n>-<slug>``; путь worktree —
канонический, внутри allowlist worktrees-корня; существующий worktree не
перезаписывается. ``git worktree add`` создаёт **новую** ветку и связанный
рабочий каталог, НЕ трогая рабочее дерево и dirty-state оригинала. Удаление
worktree не выполняется неявно.
"""

from __future__ import annotations

import os
import re
import subprocess

from .redaction import redact
from .wspaths import WorkspaceGuard

BRANCH_RE = re.compile(r"^atlas/vp-\d+-[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_GIT_TIMEOUT = 30


class WorktreeError(Exception):
    code = "WORKTREE_ERROR"

    def __init__(self, reason: str):
        self.reason = redact(reason)
        super().__init__(f"{self.code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


def validate_branch(branch: str) -> None:
    if not BRANCH_RE.match(branch or ""):
        raise WorktreeError(
            f"ветка должна соответствовать atlas/vp-<n>-<slug>: {branch!r}")


def _git_env() -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


def compute_worktree_path(guard: WorkspaceGuard, worktrees_root: str,
                          project_id: str, branch: str) -> str:
    """Каноническая цель worktree внутри allowlist (иначе PathTraversalError)."""
    validate_branch(branch)
    leaf = branch.split("/", 1)[1]  # vp-<n>-<slug>, слэшей внутри нет
    candidate = os.path.join(worktrees_root, project_id, leaf)
    return guard.ensure_within(candidate)


def create_git_worktree(repo_path: str, worktree_path: str, branch: str) -> None:
    """Создать новый worktree+ветку. Оригинал не изменяется. Без перезаписи."""
    validate_branch(branch)
    if os.path.exists(worktree_path):
        raise WorktreeError(f"worktree уже существует, перезапись запрещена: {worktree_path}")
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
    r = subprocess.run(
        ["git", "-C", repo_path, "worktree", "add", worktree_path, "-b", branch],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=_git_env(), check=False,
    )
    if r.returncode != 0:
        raise WorktreeError(f"git worktree add не удался: {r.stderr.strip()[:200]}")


def remove_git_worktree(repo_path: str, worktree_path: str) -> None:
    """Явное удаление worktree (никогда не вызывается неявно)."""
    subprocess.run(
        ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=_git_env(), check=False,
    )
