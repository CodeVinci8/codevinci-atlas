#!/usr/bin/env bash
# Регрессия deploy-safety Core-образа (VP-7): production-like build/start.
#
# Доказывает на СОБРАННОМ образе, что:
#   1) обычный boot на несовместимой/пустой БД → fail-closed, БЕЗ неявной миграции
#      и без создания файла БД;
#   2) одноразовая guarded-миграция (ATLAS_RUN_MIGRATIONS=1) создаёт схему 0007;
#   3) обычный boot после 0007 → schema_check OK, команда исполняется, а ревизия
#      БД остаётся 0007 (нет неявной миграции при перезапуске).
#
# Использование: scripts/check_core_entrypoint.sh [image_tag]
set -euo pipefail
IMG="${1:-codevinciatlas-core:latest}"
HEAD="0007_autonomy_github_time_machine"
TMP="$(mktemp -d)"
chmod 0777 "$TMP"   # non-root пользователь образа (uid 10001) должен писать в /data
trap 'rm -rf "$TMP"' EXIT

run_core() {  # extra docker args... IMG cmd...
  docker run --rm \
    -e ATLAS_DATA_DIR=/data -e ATLAS_CONFIG_FILE=/nonexistent.yaml \
    -v "$TMP:/data" "$@"
}

echo "== 1) обычный boot на несовместимой/пустой БД → fail-closed =="
if run_core "$IMG" true; then
  echo "ОШИБКА: entrypoint НЕ сделал fail-closed на несовместимой схеме"; exit 1
fi
if [ -e "$TMP/atlas.db" ]; then
  echo "ОШИБКА: обычный boot создал/тронул БД (неявная миграция?)"; exit 1
fi
echo "  ok: fail-closed, БД не создана"

echo "== 2) одноразовая guarded-миграция (ATLAS_RUN_MIGRATIONS=1) → схема $HEAD =="
run_core -e ATLAS_RUN_MIGRATIONS=1 "$IMG" true
python3 - "$TMP/atlas.db" "$HEAD" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute("select version_num from alembic_version").fetchone()
assert r and r[0] == sys.argv[2], r
print("  ok: миграция создала схему", r[0])
PY

echo "== 3) обычный boot после $HEAD → schema_check OK, команда исполняется =="
OUT="$(run_core "$IMG" python -c "print('boot-ok')")"
echo "$OUT" | grep -q "boot-ok" || { echo "ОШИБКА: обычный boot не запустил команду"; exit 1; }
python3 - "$TMP/atlas.db" "$HEAD" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute("select version_num from alembic_version").fetchone()
assert r and r[0] == sys.argv[2], ("ревизия изменилась при обычном boot?", r)
print("  ok: обычный boot исполнил команду; ревизия неизменна", r[0])
PY

echo "CORE ENTRYPOINT SELFTEST OK: no-implicit-migration + fail-closed + one-shot migrate"
