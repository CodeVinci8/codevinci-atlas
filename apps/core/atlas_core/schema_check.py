"""Read-only проверка совместимости схемы БД при обычном старте Core (VP-7 deploy-safety).

Обычный boot Core **не** мигрирует живую БД: он лишь СРАВНИВАЕТ ревизии Alembic в БД
с head-ревизиями кода и fail-closed при любом несовпадении. Так Core никогда не
стартует на несовместимой/старой/мультиголовой схеме молча и не выполняет неявную
живую миграцию при каждой перезагрузке контейнера (§34, deploy-safety).

Инварианты (call-22 findings):
* сравниваются **множества** ревизий: весь ``alembic_version`` (все current heads БД)
  против всех head-ревизий кода — совместимо ТОЛЬКО при точном равенстве множеств
  (лишняя/чужая/вторая ветка в БД → fail-closed, а не ложное «совместимо»);
* открытие БД **строго read-only** через SQLite URI: ``immutable=1`` в чистом
  состоянии (без каких-либо side-writes ``-wal``/``-shm``), ``mode=ro`` только при
  реально «горячем» непустом WAL (чтение закоммиченного состояния). В файл БД запись
  невозможна.

Живая миграция выполняется ТОЛЬКО отдельной одноразовой авторизованной deploy-
командой (``ATLAS_RUN_MIGRATIONS=1``, после backup-first, §8). Секреты в вывод не
попадают (только имена ревизий).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from .settings import load_settings

EXIT_OK = 0
EXIT_INCOMPATIBLE = 3


def _script() -> ScriptDirectory:
    """Alembic ScriptDirectory. Резолвится от каталога миграций рядом с этим модулем
    (``atlas_core/migrations``), поэтому не зависит от cwd и alembic.ini."""
    migrations = Path(__file__).resolve().parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
    return ScriptDirectory.from_config(cfg)


def code_heads() -> frozenset[str]:
    """Все head-ревизии кода (обычно ровно одна). Мультиголовье кода тоже отражается."""
    return frozenset(_script().get_heads())


def head_revision() -> str:
    """Единственный head кода (для сообщений/тестов). Fail-closed при мультиголовье."""
    heads = code_heads()
    if len(heads) != 1:
        raise RuntimeError(
            f"schema_check: ожидался ровно один code head, найдено {sorted(heads)}")
    return next(iter(heads))


def current_revisions(db_path: str) -> frozenset[str]:
    """ВСЕ ревизии в ``alembic_version`` (мультиголовье БД тоже видно) как множество.

    Пустое множество, если файла/таблицы нет. Строго read-only, без side-writes:
    * файл БД не существует → ``frozenset()`` (движок не создаётся);
    * чистое состояние → ``immutable=1`` (не создаёт ``-wal``/``-shm``);
    * реально «горячий» непустой WAL → ``mode=ro`` (чтение закоммиченного WAL-состояния;
      ``-shm`` — индекс, не запись в файл БД). Запись в файл БД невозможна в обоих случаях."""
    if not os.path.exists(db_path):
        return frozenset()
    wal = db_path + "-wal"
    hot_wal = os.path.exists(wal) and os.path.getsize(wal) > 0
    suffix = "?mode=ro" if hot_wal else "?mode=ro&immutable=1"
    uri = Path(db_path).as_uri() + suffix
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return frozenset()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
        if cur.fetchone() is None:
            return frozenset()
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        return frozenset(r[0] for r in rows)
    finally:
        conn.close()


def check() -> int:
    """Сравнить множество ревизий БД с head-множеством кода. 0 — точное совпадение;
    ``EXIT_INCOMPATIBLE`` — любое расхождение (fail-closed)."""
    settings = load_settings()
    db_path = settings.db_path
    heads = code_heads()
    current = current_revisions(db_path)
    if current == heads:
        print(f"[schema_check] OK: схема БД на head {sorted(heads)}")
        return EXIT_OK
    print(
        f"[schema_check] НЕСОВМЕСТИМАЯ СХЕМА: ревизии БД {sorted(current) or '(нет)'}, "
        f"код требует {sorted(heads)}. Core не стартует на несовместимой схеме и не "
        f"мигрирует её неявно. Выполните авторизованную одноразовую guarded-миграцию "
        f"(ATLAS_RUN_MIGRATIONS=1) ПОСЛЕ backup, затем перезапустите Core.",
        file=sys.stderr,
    )
    return EXIT_INCOMPATIBLE


def main() -> int:
    return check()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
