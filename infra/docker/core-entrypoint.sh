#!/bin/sh
# Core entrypoint (VP-7 deploy-safety).
#
# Обычный boot/restart НЕ мигрирует живую БД: он лишь проверяет совместимость
# схемы (atlas_core.schema_check) и fail-closed при несовпадении. Так контейнер
# не выполняет неявную живую миграцию при каждой перезагрузке и не стартует на
# несовместимой/старой схеме молча.
#
# Живая миграция выполняется ТОЛЬКО явной одноразовой deploy-командой с
# ATLAS_RUN_MIGRATIONS=1 (после проверенного backup, §8). Разрешение на живую
# миграцию (ATLAS_ALLOW_LIVE_MIGRATION) НЕ хранится в Compose постоянно — оно
# поднимается локально только внутри ветки миграции ниже.
set -e
cd /app

if [ "${ATLAS_RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "[core-entrypoint] одноразовая авторизованная миграция (ATLAS_RUN_MIGRATIONS=1)"
  ATLAS_ALLOW_LIVE_MIGRATION=1 alembic upgrade head
  echo "[core-entrypoint] миграция завершена"
fi

echo "[core-entrypoint] проверка совместимости схемы (без неявной миграции)"
python -m atlas_core.schema_check

echo "[core-entrypoint] схема OK; запуск: $*"
exec "$@"
