"""Резервное копирование Core (Master Spec §31, §34).

Использует онлайн-backup SQLite (`sqlite3.Connection.backup`), включает
безопасный config и манифест artifacts с SHA-256, проверяет целостность и
НЕ включает credentials (профильные auth-root исключаются; backup сканируется
на secret-markers).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _online_sqlite_backup(src_db: str, dst_db: str) -> None:
    src = sqlite3.connect(src_db)
    dst = sqlite3.connect(dst_db)
    try:
        with dst:
            src.backup(dst)  # атомарный онлайн-снимок (не копирование файла)
    finally:
        src.close()
        dst.close()


def _integrity_ok(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def create_backup(data_dir: str, out_dir: str, *, config_file: str | None = None) -> dict:
    """Создать backup-архив. Вернуть манифест (без секретов)."""

    data = Path(data_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_src = str(data / "atlas.db")

    with tempfile.TemporaryDirectory() as tmp:
        db_snap = os.path.join(tmp, "atlas.db")
        _online_sqlite_backup(db_src, db_snap)
        db_ok = _integrity_ok(db_snap)
        db_hash = _sha256_file(db_snap)

        # Манифест artifacts (только метаданные/хеши, содержимое не читаем как секрет).
        artifacts_dir = data / "artifacts"
        artifact_manifest = []
        if artifacts_dir.exists():
            for p in sorted(artifacts_dir.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(data)
                    artifact_manifest.append({"path": str(rel), "sha256": _sha256_file(str(p)),
                                              "bytes": p.stat().st_size})

        # Безопасный config (если задан и без секретов).
        config_included = False
        config_copy = os.path.join(tmp, "config.yaml")
        if config_file and os.path.exists(config_file):
            from .redaction import contains_secret
            text = Path(config_file).read_text(encoding="utf-8")
            if not contains_secret(text):
                Path(config_copy).write_text(text, encoding="utf-8")
                config_included = True

        manifest = {
            "created_at": ts,
            "db": {"sha256": db_hash, "integrity_ok": db_ok},
            "artifacts": artifact_manifest,
            "config_included": config_included,
            "excludes": ["profiles/ (auth-root credentials)", "runner tokens", "logs"],
            "version": 1,
        }
        manifest_path = os.path.join(tmp, "manifest.json")
        Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        archive = out / f"atlas-backup-{ts}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(db_snap, arcname="atlas.db")
            tar.add(manifest_path, arcname="manifest.json")
            if config_included:
                tar.add(config_copy, arcname="config.yaml")
            if artifacts_dir.exists():
                # добавляем сами artifacts (content-addressed, без credentials)
                for entry in artifact_manifest:
                    tar.add(str(data / entry["path"]), arcname=entry["path"])

    archive_hash = _sha256_file(str(archive))
    manifest["archive"] = {"path": str(archive), "sha256": archive_hash,
                           "bytes": archive.stat().st_size}
    manifest["integrity_ok"] = db_ok
    return manifest


def scan_backup_for_secrets(archive_path: str) -> dict:
    """Проверить, что backup не содержит secret-markers (§30.4)."""

    from .redaction import scan_for_secrets
    hits = []
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            try:
                content = f.read().decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue  # бинарь (напр. atlas.db) — как текст секрет не найдём
            for h in scan_for_secrets(content, source=member.name):
                hits.append({"file": member.name, "rule": h.rule})
    return {"clean": not hits, "hits": hits}
