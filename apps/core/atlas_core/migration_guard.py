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
