"""VP-7 deploy-safety: read-only проверка совместимости схемы при обычном старте Core.

Обычный boot Core больше НЕ мигрирует живую БД: он лишь сравнивает множество ревизий
БД с head-множеством кода и fail-closed при любом несовпадении. Проверяем (в т.ч.
call-22 findings):

* совместимая схема (единственный head) → OK, БД не изменена;
* старая схема (0006) → fail-closed, БД НЕ мигрирована;
* отсутствующая БД → fail-closed и файл БД НЕ создан;
* F1: head + лишняя (мультиголовая) ревизия → fail-closed (не ложное «совместимо»);
* F2: чтение строго read-only — запись в файл БД невозможна и не создаются
  побочные ``-wal``/``-shm`` при чистом состоянии (WAL-БД).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest


class TestSchemaCheck(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ATLAS_DATA_DIR", "ATLAS_CONFIG_FILE", "ATLAS_ALLOW_LIVE_MIGRATION")}
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        os.environ.pop("ATLAS_ALLOW_LIVE_MIGRATION", None)
        self.tmp = tempfile.mkdtemp(prefix="atlas-schemacheck-")
        os.environ["ATLAS_DATA_DIR"] = self.tmp

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _db_path(self) -> str:
        return os.path.join(self.tmp, "atlas.db")

    def _make_db(self, versions, *, wal=False):
        """versions: строка (одна ревизия) | список | None (нет строк)."""
        c = sqlite3.connect(self._db_path())
        if wal:
            c.execute("PRAGMA journal_mode=wal")
        c.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        if versions is not None:
            if isinstance(versions, str):
                versions = [versions]
            for v in versions:
                c.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (v,))
        c.commit()
        c.close()
        # чистое состояние: убрать любые sidecar после закрытия
        for s in ("-wal", "-shm"):
            p = self._db_path() + s
            if os.path.exists(p):
                os.remove(p)

    def _db_versions(self):
        c = sqlite3.connect(self._db_path())
        try:
            return sorted(r[0] for r in c.execute("SELECT version_num FROM alembic_version"))
        finally:
            c.close()

    def test_head_matches_returns_ok_and_leaves_db_unchanged(self):
        from atlas_core import schema_check
        head = schema_check.head_revision()
        self.assertEqual(head, "0007_autonomy_github_time_machine")
        self._make_db(head)
        self.assertEqual(schema_check.check(), schema_check.EXIT_OK)
        self.assertEqual(self._db_versions(), [head])

    def test_old_schema_fails_closed_without_migrating(self):
        from atlas_core import schema_check
        self._make_db("0006_review_quality")
        self.assertEqual(schema_check.check(), schema_check.EXIT_INCOMPATIBLE)
        self.assertEqual(self._db_versions(), ["0006_review_quality"])

    def test_missing_db_fails_closed_and_creates_no_file(self):
        from atlas_core import schema_check
        self.assertFalse(os.path.exists(self._db_path()))
        self.assertEqual(schema_check.check(), schema_check.EXIT_INCOMPATIBLE)
        self.assertFalse(os.path.exists(self._db_path()))

    # --- F1: мультиголовая БД не признаётся совместимой ---
    def test_head_plus_extra_revision_fails_closed(self):
        from atlas_core import schema_check
        head = schema_check.head_revision()
        # head присутствует, но есть ВТОРАЯ несовместимая ветка → множества не равны.
        self._make_db([head, "deadbeefbranch"])
        self.assertEqual(schema_check.check(), schema_check.EXIT_INCOMPATIBLE)
        # ничего не мигрировано/не стёрто.
        self.assertEqual(self._db_versions(), sorted([head, "deadbeefbranch"]))

    def test_current_revisions_returns_full_set(self):
        from atlas_core import schema_check
        head = schema_check.head_revision()
        self._make_db([head, "otherhead"])
        self.assertEqual(schema_check.current_revisions(self._db_path()),
                         frozenset({head, "otherhead"}))

    # --- F2: строго read-only, без side-writes ---
    def test_read_only_no_write_no_sidecars_on_wal_db(self):
        from atlas_core import schema_check
        head = schema_check.head_revision()
        self._make_db(head, wal=True)  # WAL-режим, sidecar убраны (чистое состояние)
        before = sorted(os.listdir(self.tmp))
        self.assertEqual(schema_check.current_revisions(self._db_path()), frozenset({head}))
        after = sorted(os.listdir(self.tmp))
        # чистое состояние → immutable=1: НИКАКИХ новых -wal/-shm, файл БД не изменён.
        self.assertEqual(before, after, f"побочные sidecar-файлы: {set(after) - set(before)}")

    def test_current_revisions_missing_file_returns_empty_no_side_effect(self):
        from atlas_core import schema_check
        self.assertEqual(schema_check.current_revisions(self._db_path()), frozenset())
        self.assertFalse(os.path.exists(self._db_path()))

    # --- call-23 F1: пустой набор code heads не даёт ложный OK ---
    def test_empty_code_heads_fails_closed(self):
        from unittest import mock

        from atlas_core import schema_check
        # Отсутствующая БД → current==frozenset(); если бы code_heads тоже был пуст,
        # наивное сравнение дало бы OK. Guard обязан fail-closed.
        with mock.patch.object(schema_check, "code_heads", return_value=frozenset()):
            self.assertFalse(os.path.exists(self._db_path()))
            self.assertEqual(schema_check.check(), schema_check.EXIT_INCOMPATIBLE)
        # и при наличии БД на head — тоже fail-closed, раз code heads пуст.
        head = schema_check.head_revision()
        self._make_db(head)
        with mock.patch.object(schema_check, "code_heads", return_value=frozenset()):
            self.assertEqual(schema_check.check(), schema_check.EXIT_INCOMPATIBLE)

    # --- call-23 F2: ветка mode=ro при реально «горячем» непустом WAL ---
    def test_hot_wal_read_via_mode_ro(self):
        from pathlib import Path

        from atlas_core import schema_check
        head = schema_check.head_revision()
        dbp = self._db_path()
        w = sqlite3.connect(dbp)
        w.execute("PRAGMA journal_mode=wal")
        w.execute("PRAGMA wal_autocheckpoint=0")  # не чекпоинтить — держать -wal горячим
        w.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        w.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (head,))
        w.commit()
        try:
            wal = dbp + "-wal"
            self.assertTrue(os.path.exists(wal) and os.path.getsize(wal) > 0,
                            "тест должен создать непустой -wal (горячий WAL)")
            # immutable=1 игнорирует WAL → таблица/строка ещё только в -wal → пусто.
            imm_uri = Path(dbp).as_uri() + "?mode=ro&immutable=1"
            imm = sqlite3.connect(imm_uri, uri=True)
            try:
                has_tbl = imm.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND "
                    "name='alembic_version'").fetchone()
            finally:
                imm.close()
            self.assertIsNone(has_tbl, "immutable не должен видеть данные, лежащие только в WAL")
            # current_revisions видит горячий WAL → выбирает mode=ro → читает ревизию.
            self.assertEqual(schema_check.current_revisions(dbp), frozenset({head}))
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
