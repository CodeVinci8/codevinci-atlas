"""VP-7 call-8 F: guard изоляции живой БД для миграционных тестов/фикстур.

Проверяет, что попытка мигрировать против живого data_dir (/var/lib/codevinci-atlas)
или без изолированного ATLAS_DATA_DIR — ОТКАЗ (LiveMigrationRefused), а изолированный
временный каталог — разрешён. Никакие alembic-команды тут не запускаются."""

from __future__ import annotations

import os
import tempfile
import unittest


class TestMigrationGuard(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ATLAS_DATA_DIR", "ATLAS_ALLOW_LIVE_MIGRATION", "ATLAS_CONFIG_FILE")}
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_refuses_live_data_dir(self):
        from atlas_core.migration_guard import LiveMigrationRefused, assert_isolated
        os.environ["ATLAS_DATA_DIR"] = "/var/lib/codevinci-atlas"
        os.environ.pop("ATLAS_ALLOW_LIVE_MIGRATION", None)
        with self.assertRaises(LiveMigrationRefused):
            assert_isolated(purpose="test")

    def test_refuses_without_isolated_data_dir(self):
        from atlas_core.migration_guard import LiveMigrationRefused, assert_isolated
        os.environ.pop("ATLAS_DATA_DIR", None)  # дефолт = живой каталог
        os.environ.pop("ATLAS_ALLOW_LIVE_MIGRATION", None)
        with self.assertRaises(LiveMigrationRefused):
            assert_isolated(purpose="test")

    def test_allows_isolated_tmp(self):
        from atlas_core.migration_guard import assert_isolated
        d = tempfile.mkdtemp(prefix="atlas-guard-ok-")
        os.environ["ATLAS_DATA_DIR"] = d
        os.environ.pop("ATLAS_ALLOW_LIVE_MIGRATION", None)
        self.assertEqual(assert_isolated(purpose="test"), os.path.realpath(d))
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_explicit_allow_flag_bypasses_for_deploy(self):
        from atlas_core.migration_guard import assert_isolated
        # Явная авторизованная живая миграция (deploy) — единственный обход.
        os.environ["ATLAS_DATA_DIR"] = "/var/lib/codevinci-atlas"
        os.environ["ATLAS_ALLOW_LIVE_MIGRATION"] = "1"
        # Не поднимает: возвращает путь (deploy сам отвечает за backup).
        assert_isolated(purpose="deploy")
