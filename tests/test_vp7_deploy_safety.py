"""VP-7 deploy-safety: структурные инварианты entrypoint и Compose.

Регрессия на crash-loop, который возник бы, если бы Core-контейнер выполнял
``alembic upgrade head`` на КАЖДОМ boot против живой bind-mount БД (env.py guard
теперь отказал бы без ATLAS_ALLOW_LIVE_MIGRATION). Безопасная модель:

* обычный boot НЕ мигрирует (нет безусловного ``alembic upgrade`` на верхнем уровне);
* живая миграция — только внутри ветки ``ATLAS_RUN_MIGRATIONS=1``;
* ``ATLAS_ALLOW_LIVE_MIGRATION`` поднимается ЛОКАЛЬНО только в команде миграции,
  а НЕ постоянной переменной Compose;
* обычный boot вызывает ``schema_check`` (fail-closed).
"""

from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINT = os.path.join(ROOT, "infra", "docker", "core-entrypoint.sh")
COMPOSE = os.path.join(ROOT, "compose.yaml")


def _code_lines(path: str) -> list[str]:
    """Строки скрипта без комментариев и пустых (только исполняемая логика)."""
    out = []
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    for raw in content.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(raw)
    return out


class TestEntrypointSafety(unittest.TestCase):
    def setUp(self):
        self.lines = _code_lines(ENTRYPOINT)
        self.text = "\n".join(self.lines)

    def test_migration_only_inside_run_migrations_guard(self):
        # Единственное вхождение alembic upgrade — и оно на одной строке с явным
        # локальным ATLAS_ALLOW_LIVE_MIGRATION=1 (не отдельным export/env).
        upgrades = [ln for ln in self.lines if "alembic upgrade" in ln]
        self.assertEqual(len(upgrades), 1, f"ожидалась ровно одна миграция, а не {upgrades}")
        self.assertIn("ATLAS_ALLOW_LIVE_MIGRATION=1 alembic upgrade", upgrades[0].strip(),
                      "живая миграция должна быть авторизована локально в той же команде")
        # Нет безусловной миграции на верхнем уровне: строка должна быть с отступом
        # (внутри if-ветки).
        self.assertTrue(upgrades[0].startswith((" ", "\t")),
                        "alembic upgrade не должен стоять на верхнем уровне (безусловно)")

    def test_run_migrations_gate_present(self):
        self.assertIn("ATLAS_RUN_MIGRATIONS", self.text,
                      "миграция должна быть за явным флагом ATLAS_RUN_MIGRATIONS")

    def test_allow_flag_never_exported_globally(self):
        # Нет строки, которая экспортирует/устанавливает ATLAS_ALLOW_LIVE_MIGRATION
        # как самостоятельную переменную окружения.
        for ln in self.lines:
            s = ln.strip()
            self.assertFalse(s.startswith("export ATLAS_ALLOW_LIVE_MIGRATION"), s)
            # допускается только префикс команды: "ATLAS_ALLOW_LIVE_MIGRATION=1 alembic ..."
            if s.startswith("ATLAS_ALLOW_LIVE_MIGRATION="):
                self.assertIn("alembic upgrade", s,
                              "ALLOW-флаг допустим только как инлайн-префикс команды миграции")

    def test_normal_boot_runs_schema_check(self):
        self.assertIn("atlas_core.schema_check", self.text,
                      "обычный boot должен проверять совместимость схемы (fail-closed)")

    def test_fail_closed_shell(self):
        self.assertIn("set -e", self.text, "entrypoint должен падать при ошибке (set -e)")


class TestComposeSafety(unittest.TestCase):
    def test_compose_has_no_permanent_live_migration_permission(self):
        import yaml
        with open(COMPOSE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        env = data["services"]["core"].get("environment", {}) or {}
        # environment может быть dict или list ("K=V"); нормализуем ключи.
        if isinstance(env, list):
            keys = {str(item).split("=", 1)[0] for item in env}
        else:
            keys = set(env.keys())
        self.assertNotIn("ATLAS_ALLOW_LIVE_MIGRATION", keys,
                         "Compose не должен постоянно хранить разрешение на живую миграцию")
        self.assertNotIn("ATLAS_RUN_MIGRATIONS", keys,
                         "Compose не должен постоянно включать одноразовую миграцию")

    def test_compose_text_has_no_allow_flag(self):
        with open(COMPOSE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("ATLAS_ALLOW_LIVE_MIGRATION", text)


if __name__ == "__main__":
    unittest.main()
