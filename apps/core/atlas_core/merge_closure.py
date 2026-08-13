"""Preserved, call/attempt-scoped closure store для авторитетного VP-7 merge (§3).

Финальный authoritative merge собирает durable ``ReviewPackage`` / ``QualityReport``
/ grant / delivery в изолированной 0007-БД. Раньше эта БД создавалась анонимным
``tempfile.mkdtemp()`` (одноразовая, невосстановимая). §3 требует **сохраняемый**
каталог со строгими правами и manifest, чтобы:

* прерванный (технически) финальный вызов мог **возобновить** авторизацию merge из
  сохранённой БД **без повторного вызова Reviewer и без инъекции вердикта** — QR уже
  хранит genuine PASS, resume только перечитывает его;
* точные RP/QR/grant/delivery, использованные для merge, были воспроизводимо
  зафиксированы (location, schema, checksum, безопасные row-counts);
* ни одна предыдущая попытка не перезаписывалась (attempt-scoped каталог).

Права: каталог ``0700``; БД и manifest ``0600``. Manifest НЕ содержит секретов —
только идентификаторы, checksum и счётчики строк.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from sqlalchemy import text

from .db import init_engine, session_scope
from .settings import load_settings

_MANIFEST = "closure_manifest.json"
_SAFE_TABLES = ("review_packages", "quality_reports", "merge_evidence",
                "grants", "github_deliveries")


class ClosureError(Exception):
    code = "CLOSURE_ERROR"

    def __init__(self, message: str, code: str = "CLOSURE_ERROR"):
        super().__init__(message)
        self.code = code
        self.message = message


def prepare_closure_dir(parent: str | Path, attempt: str) -> tuple[Path, bool]:
    """Подготовить attempt-scoped каталог закрытия (``<parent>/closure-<attempt>``).

    Возвращает ``(path, resumed)``. ``resumed=True`` если каталог уже содержит
    ``closure_manifest.json`` (сценарий возобновления — БД не пересоздаётся). Пустой
    существующий каталог допустим (переиспользуется). Непустой каталог БЕЗ manifest
    — конфликт (не перезаписываем чужую попытку)."""
    d = Path(parent) / f"closure-{attempt}"
    if d.exists():
        if (d / _MANIFEST).is_file():
            return d, True
        if any(d.iterdir()):
            raise ClosureError(f"каталог попытки не пуст и без manifest: {d}",
                               "CLOSURE_DIR_CONFLICT")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, stat.S_IRWXU)  # 0700
    return d, False


def _sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _safe_row_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with session_scope() as s:
        for tbl in _SAFE_TABLES:
            try:
                counts[tbl] = int(s.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() or 0)
            except Exception:  # noqa: BLE001 — таблица может отсутствовать в старой схеме
                counts[tbl] = -1
    return counts


def write_closure_manifest(closure_dir: str | Path, db_path: str | Path, *,
                           review_package_id: str, quality_report_id: str,
                           grant_id: str, delivery_id: str | None,
                           repo: str, base: str, head: str, pr: int,
                           schema: str, project_id: str,
                           environment: str = "") -> dict:
    """Зафиксировать closure-manifest (location/schema/checksum/safe row counts +
    точные RP/QR/grant/delivery). БД и manifest получают права ``0600``."""
    d = Path(closure_dir)
    dbp = Path(db_path)
    manifest = {
        "schema_version": schema,
        "location": str(d.resolve()),
        "db_path": str(dbp.resolve()),
        "db_sha256": _sha256_file(dbp) if dbp.is_file() else "",
        "row_counts": _safe_row_counts(),
        "review_package_id": review_package_id,
        "quality_report_id": quality_report_id,
        "grant_id": grant_id,
        "delivery_id": delivery_id,
        "project_id": project_id,
        "repo": repo, "base": base, "head_sha": head, "pr_number": pr,
        "environment": environment,
    }
    mp = d / _MANIFEST
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                  encoding="utf-8")
    for f in (mp, dbp):
        if f.is_file():
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return manifest


def load_closure_manifest(closure_dir: str | Path) -> dict:
    mp = Path(closure_dir) / _MANIFEST
    if not mp.is_file():
        raise ClosureError(f"closure manifest отсутствует: {mp}", "CLOSURE_NO_MANIFEST")
    return json.loads(mp.read_text(encoding="utf-8"))


def bind_engine_to_closure(closure_dir: str | Path):
    """Перепривязать engine к сохранённой closure-БД (по manifest.db_path).

    Используется при возобновлении в свежем процессе: ATLAS_DATA_DIR указывает на
    caller-БД, поэтому берём точный ``db_path`` из manifest напрямую."""
    manifest = load_closure_manifest(closure_dir)
    db_path = manifest["db_path"]
    os.environ["ATLAS_DATA_DIR"] = str(Path(db_path).parent)
    os.environ.setdefault("ATLAS_CONFIG_FILE", "/nonexistent.yaml")
    load_settings()  # применить изолированную config-раскладку (без live-путей)
    # Форсируем ТОЧНЫЙ файл БД из manifest (раскладка могла отличаться).
    init_engine(f"sqlite:///{db_path}", db_path)
    return manifest


def resume_authorization(closure_dir: str | Path, *, forge,
                         correlation_id: str = ""):
    """Возобновить авторитетную авторизацию merge из сохранённой closure-БД **без**
    повторного вызова Reviewer и **без** инъекции вердикта.

    Перечитывает точные RP/QR/grant по id из БД, живой head/CI/mergeability — из
    forge, и возвращает :class:`merge_gate.MergeGateDecision`. Verdict PASS уже
    хранится в QR — resume его только читает."""
    from .merge_gate import authorize_merge_execution
    manifest = bind_engine_to_closure(closure_dir)
    return authorize_merge_execution(
        forge=forge, repo=manifest["repo"], project_id=manifest["project_id"],
        review_package_id=manifest["review_package_id"],
        quality_report_id=manifest["quality_report_id"],
        pr_number=int(manifest["pr_number"]), expected_head=manifest["head_sha"],
        grant_id=manifest["grant_id"], base=manifest["base"],
        environment=manifest.get("environment", ""), correlation_id=correlation_id)
