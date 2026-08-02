"""Безопасный intake архивов (Master Spec §30.1, §35, VP-2).

Архив трактуется как **враждебный вход**. Распаковка идёт вручную, поэлементно,
внутри единственного intake-корня из allowlist. Отклоняются: абсолютные пути,
``..``-traversal, Windows-разделители, symlink/hardlink-escape, устройства и
спец-файлы, превышение числа элементов и распакованного размера,
конфликтующие/дублирующиеся пути.

Гарантия приёмки: ни один файл не создаётся вне intake-каталога.
Intake — read-only: распакованные файлы становятся неизменяемыми (0444/0555).
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

from .redaction import redact
from .wspaths import UnsafeArchiveError, safe_extract_target

_MAX_ENTRIES = 5000
_MAX_TOTAL_BYTES = 256 * 1024 * 1024   # 256 MiB распакованного
_MAX_FILE_BYTES = 64 * 1024 * 1024     # 64 MiB на файл


class _Budget:
    def __init__(self) -> None:
        self.entries = 0
        self.total = 0

    def add_entry(self, name: str) -> None:
        self.entries += 1
        if self.entries > _MAX_ENTRIES:
            raise UnsafeArchiveError(f"слишком много элементов архива (>{_MAX_ENTRIES})")

    def add_bytes(self, n: int, name: str) -> None:
        if n > _MAX_FILE_BYTES:
            raise UnsafeArchiveError(f"файл превышает лимит: {redact(name)}")
        self.total += n
        if self.total > _MAX_TOTAL_BYTES:
            raise UnsafeArchiveError(f"распакованный размер превышает лимит (>{_MAX_TOTAL_BYTES})")


def _finalize_readonly(paths: list[str]) -> None:
    """Сделать распакованное read-only (сначала файлы, потом каталоги)."""
    for p in paths:
        try:
            if os.path.isdir(p):
                os.chmod(p, 0o555)
            else:
                os.chmod(p, 0o444)
        except OSError:
            pass


def _mkdir_within(intake_root: str, name: str, created: set[str]) -> str:
    target = safe_extract_target(intake_root, name)
    Path(target).mkdir(parents=True, exist_ok=True)
    created.add(target)
    return target


def _open_file_within(intake_root: str, name: str, seen_files: set[str]) -> str:
    target = safe_extract_target(intake_root, name)
    if target in seen_files:
        raise UnsafeArchiveError(f"дублирующийся путь в архиве: {redact(name)}")
    if os.path.isdir(target):
        raise UnsafeArchiveError(f"конфликт файл/каталог: {redact(name)}")
    seen_files.add(target)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    return target


def _extract_tar(archive_path: str, dest: str) -> dict:
    budget = _Budget()
    seen_files: set[str] = set()
    created: set[str] = set()
    with tarfile.open(archive_path, "r:*") as tar:
        for member in tar:
            budget.add_entry(member.name)
            if member.issym() or member.islnk():
                raise UnsafeArchiveError(f"symlink/hardlink в архиве запрещён: {redact(member.name)}")
            if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                raise UnsafeArchiveError(f"устройство/спец-файл в архиве запрещён: {redact(member.name)}")
            if member.isdir():
                _mkdir_within(dest, member.name, created)
                continue
            if not member.isfile():
                raise UnsafeArchiveError(f"неизвестный тип элемента архива: {redact(member.name)}")
            budget.add_bytes(member.size, member.name)
            target = _open_file_within(dest, member.name, seen_files)
            src = tar.extractfile(member)
            if src is None:
                raise UnsafeArchiveError(f"нечитаемый элемент архива: {redact(member.name)}")
            with open(target, "wb") as out:
                while chunk := src.read(65536):
                    out.write(chunk)
            created.add(target)
    return {"files": len(seen_files), "dirs": len(created) - len(seen_files),
            "entries": budget.entries, "total_bytes": budget.total, "created": sorted(created)}


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    # Unix-режим в верхних 16 битах external_attr; S_IFLNK == 0o120000.
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _extract_zip(archive_path: str, dest: str) -> dict:
    budget = _Budget()
    seen_files: set[str] = set()
    created: set[str] = set()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            budget.add_entry(info.filename)
            if _zip_is_symlink(info):
                raise UnsafeArchiveError(f"symlink в zip запрещён: {redact(info.filename)}")
            if info.filename.endswith("/"):
                _mkdir_within(dest, info.filename, created)
                continue
            budget.add_bytes(info.file_size, info.filename)
            target = _open_file_within(dest, info.filename, seen_files)
            with zf.open(info, "r") as src, open(target, "wb") as out:
                while chunk := src.read(65536):
                    out.write(chunk)
            created.add(target)
    return {"files": len(seen_files), "dirs": len(created) - len(seen_files),
            "entries": budget.entries, "total_bytes": budget.total, "created": sorted(created)}


def safe_extract(archive_path: str, intake_root: str, subdir: str) -> dict:
    """Безопасно распаковать архив в ``intake_root/subdir`` (read-only intake).

    ``intake_root`` должен быть каноническим корнем из allowlist. ``subdir`` —
    имя нового подкаталога (валидируется как относительное). Возвращает сводку.
    На любое нарушение поднимает :class:`UnsafeArchiveError`.
    """
    dest = safe_extract_target(intake_root, subdir)
    if os.path.exists(dest):
        raise UnsafeArchiveError(f"intake-каталог уже существует: {redact(subdir)}")
    Path(dest).mkdir(parents=True, exist_ok=False)

    try:
        if tarfile.is_tarfile(archive_path):
            summary = _extract_tar(archive_path, dest)
        elif zipfile.is_zipfile(archive_path):
            summary = _extract_zip(archive_path, dest)
        else:
            raise UnsafeArchiveError("неизвестный формат архива (ожидались tar/zip)")
    except UnsafeArchiveError:
        raise
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        raise UnsafeArchiveError(f"ошибка распаковки архива: {redact(str(exc))}") from exc

    _finalize_readonly(summary.pop("created"))
    summary["extracted_to"] = dest
    return summary
