"""Жёсткий guard изоляции живой БД для миграционных тестов/фикстур (VP-7, call-8 F).

Предыстория: в прошлой сессии тест миграции случайно затронул живую bind-mount БД
(``migrations/env.py`` форсит ``settings.db_url``). Guard запрещает тестам/фикстурам
запускать миграции против:

* ``/var/lib/codevinci-atlas`` (production data dir);
* Compose bind-mounted production каталога;
* живого файла БД по inode/пути.

Требуется явный изолированный временный ``ATLAS_DATA_DIR``. Легитимная живая
миграция (deploy) допускается ТОЛЬКО с явным ``ATLAS_ALLOW_LIVE_MIGRATION=1``.
"""

from __future__ import annotations

import os

LIVE_DATA_DIRS = ("/var/lib/codevinci-atlas",)
LIVE_DB = "/var/lib/codevinci-atlas/atlas.db"
ALLOW_ENV = "ATLAS_ALLOW_LIVE_MIGRATION"


class LiveMigrationRefused(RuntimeError):
    """Поднимается, когда тест/фикстура пытается мигрировать живую БД без явного
    разрешения — вместо молчаливого повреждения production."""


def _real(p: str) -> str:
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def is_live_target() -> bool:
    """True, если текущие настройки (``ATLAS_DATA_DIR``/config) указывают на живой
    production data_dir или на живой файл БД (по пути или inode)."""
    from .settings import load_settings
    s = load_settings()
    data_dir = _real(s.data_dir)
    if data_dir in {_real(p) for p in LIVE_DATA_DIRS}:
        return True
    try:
        db_path = s.db_path
        if os.path.exists(LIVE_DB) and os.path.exists(db_path) and os.path.samefile(db_path, LIVE_DB):
            return True
    except OSError:
        pass
    return False


def assert_live_migration_allowed(*, purpose: str = "migration") -> None:
    """Fail-closed guard на границе alembic ``env.py`` (call-19 F): если цель миграции
    — живой production data_dir/DB, миграция допускается ТОЛЬКО при явном
    ``ATLAS_ALLOW_LIVE_MIGRATION=1`` (авторизованный deploy, отвечающий за backup-first
    путь §8). Иначе — :class:`LiveMigrationRefused`.

    В отличие от :func:`assert_isolated` (строгая тест-политика, требующая явный
    изолированный ``ATLAS_DATA_DIR``), этот guard блокирует **только живую цель** и не
    мешает изолированным/CI/dev-миграциям (temp data_dir), поэтому его безопасно
    вызывать из ``env.py`` для КАЖДОГО запуска alembic. Именно этот путь раньше был
    незащищён: ``env.py`` не звал guard, и raw ``alembic upgrade`` против живого
    каталога проходил молча."""
    if os.environ.get(ALLOW_ENV) == "1":
        return  # явная авторизованная живая миграция (deploy сам делает backup)
    if is_live_target():
        raise LiveMigrationRefused(
            f"{purpose}: ОТКАЗ мигрировать живой production data_dir/DB без "
            f"{ALLOW_ENV}=1. Живая миграция допустима только на guarded backup-first "
            "пути deploy (§8).")


def assert_isolated(*, purpose: str = "migration-test") -> str:
    """Убедиться, что текущий ``ATLAS_DATA_DIR`` изолирован (не живая БД).

    Возвращает разрешённый data_dir. Поднимает :class:`LiveMigrationRefused`, если
    цель — живой каталог/БД, и ``ATLAS_ALLOW_LIVE_MIGRATION`` != ``1``."""
    if os.environ.get(ALLOW_ENV) == "1":
        return os.environ.get("ATLAS_DATA_DIR", "")  # явная авторизованная живая миграция

    from .settings import load_settings
    s = load_settings()
    data_dir = _real(s.data_dir)
    live_dirs = {_real(p) for p in LIVE_DATA_DIRS}
    if data_dir in live_dirs:
        raise LiveMigrationRefused(
            f"{purpose}: ОТКАЗ мигрировать живой data_dir {data_dir}. Задайте изолированный "
            f"временный ATLAS_DATA_DIR (или {ALLOW_ENV}=1 только для авторизованного deploy).")
    # Дополнительно — сравнение по inode с живым файлом БД (защита от alias-путей).
    try:
        db_path = s.db_path
        if os.path.exists(LIVE_DB) and os.path.exists(db_path) and os.path.samefile(db_path, LIVE_DB):
            raise LiveMigrationRefused(
                f"{purpose}: ОТКАЗ — db_path указывает на живой файл БД по inode. "
                f"Задайте изолированный ATLAS_DATA_DIR.")
    except OSError:
        pass
    if not os.environ.get("ATLAS_DATA_DIR"):
        raise LiveMigrationRefused(
            f"{purpose}: требуется явный изолированный ATLAS_DATA_DIR для миграционного теста.")
    return data_dir
