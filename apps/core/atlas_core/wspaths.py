"""Безопасные пути рабочих пространств VP-2 (Master Spec §30, §35).

Все операции с путями проектов/worktree/intake проходят через
:class:`WorkspaceGuard`: канонизация (``realpath`` резолвит symlink) + проверка
вхождения в **явный allowlist** корней. Защита от traversal (``..``),
абсолютных путей, symlink-escape и Windows-стиля разделителей.

Содержимое репозитория/архива/инструкций/вывода модели — это **данные**; оно
никогда не расширяет allowlist и не повышает права (§30.2).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .redaction import redact


class WorkspaceSecurityError(Exception):
    """Базовая ошибка безопасности путей рабочего пространства."""

    code = "WORKSPACE_SECURITY"

    def __init__(self, reason: str):
        self.reason = redact(reason)
        super().__init__(f"{self.code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


class PathTraversalError(WorkspaceSecurityError):
    """Путь выходит за пределы разрешённых корней (traversal/symlink escape)."""

    code = "PATH_TRAVERSAL"


class UnsafeArchiveError(WorkspaceSecurityError):
    """Небезопасная запись архива (абсолютный путь, ``..``, symlink, device)."""

    code = "ARCHIVE_UNSAFE"


def _canon(path: str | os.PathLike) -> str:
    """Каноничный абсолютный путь с резолвом symlink (для несуществующих —
    резолвит существующую часть пути; так ловится symlink-escape родителя)."""
    return os.path.realpath(os.path.abspath(os.fspath(path)))


@dataclass(frozen=True)
class WorkspaceRoots:
    """Разрешённые корни рабочего пространства (§35).

    Все — внутри единого data_dir; для тестов это временный ``ATLAS_DATA_DIR``.
    """

    projects_root: str   # куда допускаются локальные git-источники/empty-проекты
    intake_root: str     # куда (и только куда) распаковываются архивы
    worktrees_root: str  # где создаются изолированные worktree

    def all(self) -> tuple[str, ...]:
        return (self.projects_root, self.intake_root, self.worktrees_root)

    def ensure_dirs(self) -> None:
        for d in self.all():
            Path(d).mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o750)
            except PermissionError:
                pass


def default_roots(data_dir: str) -> WorkspaceRoots:
    base = Path(data_dir)
    return WorkspaceRoots(
        projects_root=_canon(base / "workspaces"),
        intake_root=_canon(base / "intake"),
        worktrees_root=_canon(base / "worktrees"),
    )


class WorkspaceGuard:
    """Проверка путей против явного allowlist канонических корней."""

    def __init__(self, roots: Iterable[str]):
        self._roots = tuple(_canon(r) for r in roots)
        if not self._roots:
            raise ValueError("allowlist корней пуст")

    @property
    def roots(self) -> tuple[str, ...]:
        return self._roots

    def _within_any(self, canonical: str) -> bool:
        for root in self._roots:
            if canonical == root or canonical.startswith(root + os.sep):
                return True
        return False

    def ensure_within(self, path: str | os.PathLike, *, must_exist: bool = False) -> str:
        """Вернуть канонический путь, если он внутри allowlist; иначе поднять
        :class:`PathTraversalError`. ``realpath`` резолвит symlink — цель ссылки
        наружу тоже отклоняется."""
        canonical = _canon(path)
        if not self._within_any(canonical):
            raise PathTraversalError(
                f"путь вне разрешённых корней рабочего пространства: {path!s}"
            )
        if must_exist and not os.path.exists(canonical):
            raise PathTraversalError(f"путь не существует: {path!s}")
        return canonical


def safe_member_relpath(name: str) -> str:
    """Нормализовать имя элемента архива в безопасный относительный путь.

    Отклоняет: пустое имя, NUL, абсолютные пути, Windows-диск (``C:``),
    обратные слэши (Windows-traversal) и любой компонент ``..``.
    """
    if not name or "\x00" in name:
        raise UnsafeArchiveError(f"недопустимое имя элемента архива: {name!r}")
    if "\\" in name:
        raise UnsafeArchiveError(f"Windows-разделитель в имени архива: {name!r}")
    if name.startswith("/") or os.path.isabs(name):
        raise UnsafeArchiveError(f"абсолютный путь в архиве: {name!r}")
    if len(name) >= 2 and name[1] == ":":  # C:\ или C:/
        raise UnsafeArchiveError(f"Windows-диск в имени архива: {name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeArchiveError(f"traversal (..) в имени архива: {name!r}")
    if not parts:
        raise UnsafeArchiveError(f"пустой относительный путь в архиве: {name!r}")
    return "/".join(parts)


def safe_extract_target(intake_root: str, name: str) -> str:
    """Каноническая цель распаковки внутри intake_root или ошибка.

    Проверяет и относительный путь, и итоговую канонизацию (двойная защита от
    symlink-escape через уже созданные ссылки-каталоги)."""
    rel = safe_member_relpath(name)
    root_canon = _canon(intake_root)
    target = os.path.join(root_canon, rel)
    target_canon = _canon(target)
    if target_canon != root_canon and not target_canon.startswith(root_canon + os.sep):
        raise UnsafeArchiveError(f"цель распаковки вне intake-корня: {name!r}")
    return target_canon
