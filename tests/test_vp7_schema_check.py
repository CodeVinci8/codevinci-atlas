"""VP-7 deploy-safety: read-only проверка совместимости схемы при обычном старте Core.

Обычный boot Core больше НЕ мигрирует живую БД: он лишь сравнивает ревизию БД с head
и fail-closed при несовпадении. Здесь проверяем:

* совместимая схема (head) → OK, БД не изменена;
* старая схема (0006) → fail-closed (EXIT_INCOMPATIBLE), БД НЕ мигрирована;
* отсутствующая БД → fail-closed и файл БД НЕ создан (read-only, без побочных эффектов).
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

    def _make_db(self, version):
        c = sqlite3.connect(self._db_path())
        c.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        if version is not None:
            c.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (version,))
        c.commit()
        c.close()

    def _db_version(self):
        c = sqlite3.connect(self._db_path())
        try:
            r = c.execute("SELECT version_num FROM alembic_version").fetchone()
            return r[0] if r else None
        finally:
            c.close()

    def test_head_matches_returns_ok_and_leaves_db_unchanged(self):
        from atlas_core import schema_check
        head = schema_check.head_revision()
        self.assertEqual(head, "0007_autonomy_github_time_machine")
        self._make_db(head)
        self.assertEqual(schema_check.check(), schema_check.EXIT_OK)
        # обычный boot НЕ мигрирует: ревизия не изменилась.
        self.assertEqual(self._db_version(), head)

    def test_old_schema_fails_closed_without_migrating(self):
        from atlas_core import schema_check
        self._make_db("0006_review_quality")
        rc = schema_check.check()
        self.assertEqual(rc, schema_check.EXIT_INCOMPATIBLE)
        # fail-closed: НЕ мигрировано молча, осталось 0006.
        self.assertEqual(self._db_version(), "0006_review_quality")

    def test_missing_db_fails_closed_and_creates_no_file(self):
        from atlas_core import schema_check
        self.assertFalse(os.path.exists(self._db_path()))
        rc = schema_check.check()
        self.assertEqual(rc, schema_check.EXIT_INCOMPATIBLE)
        # read-only: проверка НЕ создаёт пустой файл БД как побочный эффект.
        self.assertFalse(os.path.exists(self._db_path()))

    def test_current_revision_missing_file_returns_none_no_side_effect(self):
        from atlas_core import schema_check
        self.assertIsNone(schema_check.current_revision(self._db_path()))
        self.assertFalse(os.path.exists(self._db_path()))


if __name__ == "__main__":
    unittest.main()
