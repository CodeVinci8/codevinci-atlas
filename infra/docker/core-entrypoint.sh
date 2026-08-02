#!/bin/sh
# Применить миграции Alembic (единственный путь создания таблиц в проде,
# §34) и затем запустить переданную команду.
set -e
cd /app
echo "[core-entrypoint] alembic upgrade head"
alembic upgrade head
echo "[core-entrypoint] starting: $*"
exec "$@"
