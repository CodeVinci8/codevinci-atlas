"""Регрессия упаковки VP-4 runtime (Master Spec §16.4, §37 Phase H).

Ловит дефект, из-за которого Core-образ НЕ содержал изолированный
fresh-session consumer и контракт ``run-result.json``, из-за чего
reconstruction внутри контейнера падал (c17–c19 acceptance):

1. Dockerfile Core обязан копировать ``scripts/vp4_fresh_consumer.py`` и
   ``contracts/`` в образ (детерминированные пути ``/app/...``).
2. Consumer запускается ИЗОЛИРОВАННО (чистый env, пустой cwd, без atlas_core)
   и печатает результат, валидный по ``contracts/schemas/run-result.json``.
3. Все runtime-схемы VP-4 остаются в поддержанном подмножестве нашего
   валидатора — мы не выдаём его за полный draft 2020-12.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from atlas_core.schema_validate import validate as schema_validate

_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _ROOT / "infra" / "docker" / "core.Dockerfile"
_CONSUMER = _ROOT / "scripts" / "vp4_fresh_consumer.py"
_CONTRACTS = _ROOT / "contracts" / "schemas"
_RUN_RESULT_SCHEMA = _CONTRACTS / "run-result.json"

# Ключевые слова, поддержанные нашим минимальным валидатором + метаданные.
_SUPPORTED_KEYWORDS = {
    "type", "enum", "pattern", "minimum", "maximum", "required",
    "properties", "additionalProperties", "items",
    "$schema", "$id", "title", "description",
}


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _sample_handoff() -> dict:
    """Минимальный, но полный по контракту HandoffPackage-контент."""
    return {
        "ids": {"project_id": "proj_pack", "vp_key": "VP-X", "work_order_id": "wo_pack"},
        "role": "builder",
        "goal": "восстановить состояние из пакета",
        "finished_result": "частично готово",
        "immutable_constraints": ["не расширять scope", "один writer"],
        "acceptance_matrix": [
            {"id": "a1", "status": "completed"},
            {"id": "a2", "status": "pending"},
        ],
        "exact_next_action": "продолжить с критерия a2",
        "prohibited_actions": ["force push"],
        "baseline_head": "0" * 40,
        "current_head": "0" * 40,
        "changed_files": [],
        "commands": [],
        "failures": [],
    }


class TestCoreImagePackaging(unittest.TestCase):
    def test_dockerfile_ships_consumer_and_contracts(self):
        """Регрессия: образ Core должен копировать consumer и contracts."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("COPY scripts/vp4_fresh_consumer.py /app/scripts/vp4_fresh_consumer.py", text,
                      "Core.Dockerfile не копирует fresh-session consumer в образ")
        self.assertIn("COPY contracts /app/contracts", text,
                      "Core.Dockerfile не копирует contracts/ (нужен run-result.json) в образ")

    def test_required_runtime_files_exist(self):
        self.assertTrue(_CONSUMER.is_file(), f"нет {_CONSUMER}")
        self.assertTrue(_RUN_RESULT_SCHEMA.is_file(), f"нет {_RUN_RESULT_SCHEMA}")


class TestConsumerRunsIsolated(unittest.TestCase):
    def test_consumer_runs_isolated_and_matches_run_result_schema(self):
        """Consumer запускается в чистом окружении и выдаёт валидный run-result."""
        handoff = _sample_handoff()
        capsule = {"handoff": handoff, "content_hash": _content_hash(handoff),
                   "run_id": "run_pack_test", "safe_refs": []}
        workdir = tempfile.mkdtemp(prefix="atlas-pack-test-")
        capsule_path = os.path.join(workdir, "capsule.json")
        with open(capsule_path, "w", encoding="utf-8") as fh:
            json.dump(capsule, fh, ensure_ascii=False)

        # Изоляция как в reconstruct.py: только PATH/LANG, пустой cwd.
        clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, str(_CONSUMER), capsule_path],
                              capture_output=True, text=True, timeout=60,
                              cwd=workdir, env=clean_env)
        self.assertEqual(proc.returncode, 0, f"consumer завершился с ошибкой: {proc.stderr}")
        result = json.loads((proc.stdout or "").strip().splitlines()[-1])
        self.assertEqual(result["state"], "SUCCEEDED", result)

        schema = json.loads(_RUN_RESULT_SCHEMA.read_text(encoding="utf-8"))
        errors = schema_validate(result, schema)
        self.assertEqual(errors, [], f"run-result не валиден по контракту: {errors}")

        # Изоляционные инварианты: без chat/credentials/полного repo.
        out = result["structured_output"]
        self.assertFalse(out["used_prior_chat"])
        self.assertFalse(out["used_credentials"])
        self.assertFalse(out["used_full_repo"])
        self.assertEqual(out["reconstructed_from"], "handoff_only")

    def test_consumer_rejects_tampered_capsule(self):
        """Подделанный content-hash → FAILED (fail-closed)."""
        handoff = _sample_handoff()
        capsule = {"handoff": handoff, "content_hash": "sha256:" + "0" * 64,
                   "run_id": "run_tamper", "safe_refs": []}
        workdir = tempfile.mkdtemp(prefix="atlas-pack-tamper-")
        capsule_path = os.path.join(workdir, "capsule.json")
        with open(capsule_path, "w", encoding="utf-8") as fh:
            json.dump(capsule, fh, ensure_ascii=False)
        clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, str(_CONSUMER), capsule_path],
                              capture_output=True, text=True, timeout=60,
                              cwd=workdir, env=clean_env)
        result = json.loads((proc.stdout or "").strip().splitlines()[-1])
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["error_code"], "HASH_MISMATCH")


class TestSchemasWithinSupportedSubset(unittest.TestCase):
    def _keywords(self, schema, acc):
        if not isinstance(schema, dict):
            return
        for key, val in schema.items():
            if key == "properties" and isinstance(val, dict):
                for sub in val.values():
                    self._keywords(sub, acc)
                acc.add(key)
            elif key == "items":
                self._keywords(val, acc)
                acc.add(key)
            else:
                acc.add(key)

    def test_vp4_schemas_use_only_supported_keywords(self):
        names = ["vp-spec.json", "work-order.json", "job-package.json",
                 "handoff-package.json", "run-result.json"]
        for name in names:
            path = _CONTRACTS / name
            self.assertTrue(path.is_file(), f"нет схемы {name}")
            schema = json.loads(path.read_text(encoding="utf-8"))
            used: set[str] = set()
            self._keywords(schema, used)
            unsupported = used - _SUPPORTED_KEYWORDS
            self.assertEqual(unsupported, set(),
                             f"{name} использует неподдержанные ключевые слова: {sorted(unsupported)}")


if __name__ == "__main__":
    unittest.main()
