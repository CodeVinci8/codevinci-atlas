"""Общая база тестов VP-0.

Гарантирует, что пакеты Atlas на sys.path и каждый тест использует
изолированный ATLAS_DATA_DIR во временном каталоге (не трогает прод-пути).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for pkg in ("apps/core", "apps/runner"):
    p = str(_ROOT / pkg)
    if p not in sys.path:
        sys.path.insert(0, p)


class AtlasTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="atlas-vp0-")
        os.environ["ATLAS_DATA_DIR"] = self._tmp.name
        os.environ.pop("ATLAS_RUNNER_SOCKET", None)
        from atlas_core import config
        config.ensure_dirs()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("ATLAS_DATA_DIR", None)
