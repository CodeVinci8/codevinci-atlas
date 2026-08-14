"""Read-only проверка совместимости схемы БД при обычном старте Core (VP-7 deploy-safety).

Обычный boot Core **не** мигрирует живую БД: он лишь СРАВНИВАЕТ текущую ревизию
Alembic в БД с head-ревизией кода и fail-closed с явной ошибкой при несовпадении.
Так Core никогда не стартует на несовместимой/старой схеме молча и не выполняет
неявную живую миграцию при каждой перезагрузке контейнера (§34, deploy-safety).

Живая миграция выполняется ТОЛЬКО отдельной одноразовой авторизованной deploy-
командой (``ATLAS_RUN_MIGRATIONS=1`` в entrypoint, после backup-first, §8). Здесь
миграции не запускаются — только чтение ``alembic_version``. Никаких секретов в
вывод не попадает (только имена ревизий).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from .settings import load_settings

# Код успешного выхода / несовместимости (fail-closed).
EXIT_OK = 0
EXIT_INCOMPATIBLE = 3


def head_revision() -> str:
    """Head-ревизия кода. Резолвится от каталога миграций рядом с этим модулем
    (``atlas_core/migrations``), поэтому не зависит от cwd и alembic.ini."""
    migrations = Path(__file__).resolve().parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:  # pragma: no cover - в репозитории всегда есть head
        raise RuntimeError("schema_check: не удалось определить head-ревизию кода")
    return head


def current_revision(db_path: str) -> str | None:
    """Текущая ревизия Alembic в БД (``version_num``) или ``None``, если БД/таблицы нет.

    Read-only: если файла БД нет, движок НЕ создаётся (иначе SQLite создал бы пустой
    файл как побочный эффект). Возврат ``None`` трактуется как несовместимость."""
    if not os.path.exists(db_path):
        return None
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        insp = inspect(engine)
        if "alembic_version" not in insp.get_table_names():
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


def check() -> int:
    """Сравнить ревизию БД с head. 0 — совместимо; ``EXIT_INCOMPATIBLE`` — fail-closed."""
    settings = load_settings()
    db_path = settings.db_path
    head = head_revision()
    current = current_revision(db_path)
    if current == head:
        print(f"[schema_check] OK: схема БД на head {head}")
        return EXIT_OK
    print(
        f"[schema_check] НЕСОВМЕСТИМАЯ СХЕМА: БД на ревизии {current!r}, код требует "
        f"{head!r}. Core не стартует на несовместимой схеме и не мигрирует её неявно. "
        f"Выполните авторизованную одноразовую guarded-миграцию (ATLAS_RUN_MIGRATIONS=1) "
        f"ПОСЛЕ backup, затем перезапустите Core.",
        file=sys.stderr,
    )
    return EXIT_INCOMPATIBLE


def main() -> int:
    return check()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
