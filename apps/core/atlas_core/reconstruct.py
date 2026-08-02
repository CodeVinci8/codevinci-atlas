"""VP-4 fresh-session reconstruction и compact-fallback (Master Spec §16.4, §37 Phase H).

``run_fresh_session`` запускает РЕАЛЬНЫЙ изолированный процесс-потребитель
(``scripts/vp4_fresh_consumer.py``) в чистом окружении: без прошлого чата, без
credentials, без дампа репозитория — только принятый HandoffPackage и явные
safe-ссылки. Результат валидируется против ``contracts/schemas/run-result.json``.
Это интеграционное доказательство, не мок.

``compact_probe`` использует локальный детерминированный harness: сжимает
пакет, сохраняя ВСЕ immutable-ограничения и критерии; если безопасный compact
невозможен — fail-closed ``OWNER_REQUIRED``. Реальных provider-вызовов нет.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import audit
from .context_engine import _REQUIRED_KEYS as _JP_REQUIRED
from .context_engine import MAX_PACKAGE_BYTES, compact_content
from .productmap import canonical_json
from .schema_validate import validate as schema_validate
from .vp4handoff import HandoffService
from .workorders import WorkOrderError

_ROOT = Path(__file__).resolve().parents[3]
_CONSUMER = _ROOT / "scripts" / "vp4_fresh_consumer.py"
_RUN_RESULT_SCHEMA = _ROOT / "contracts" / "schemas" / "run-result.json"


def _load_run_result_schema() -> dict:
    return json.loads(_RUN_RESULT_SCHEMA.read_text(encoding="utf-8"))


class ReconstructService:
    def __init__(self, db_path: str | None = None):
        from .settings import load_settings
        self.db_path = db_path or load_settings().db_path
        self.handoff = HandoffService(self.db_path)

    def run_fresh_session(self, project_id: str, handoff_id: str, *, actual_head: str | None = None,
                          acknowledge: bool = True, actor: str = "consumer",
                          correlation_id: str = "") -> dict:
        """Запустить свежий изолированный процесс-потребитель по handoff (§37 Phase H)."""
        # 1) fail-closed: не запускать consumer для устаревшего/подделанного пакета
        verify = self.handoff.verify_handoff(project_id, handoff_id, actual_head=actual_head)
        hp = self.handoff.get_handoff(project_id, handoff_id)
        if not verify["ok"]:
            audit.record("workorders.reconstruct.rejected",
                         f"project={project_id} handoff={handoff_id} rej={[r['code'] for r in verify['rejections']]}",
                         actor=actor, correlation_id=correlation_id)
            return {"ok": False, "stage": "verify", "rejections": verify["rejections"],
                    "handoff_id": handoff_id}

        # 2) капсула: ТОЛЬКО принятый пакет + хеш (никаких credentials/repo/чата)
        workdir = tempfile.mkdtemp(prefix="atlas-fresh-")
        capsule = {"handoff": hp["content"], "content_hash": hp["content_hash"],
                   "run_id": f"run_reconstruct_{handoff_id[-8:]}", "safe_refs": []}
        capsule_path = os.path.join(workdir, "capsule.json")
        with open(capsule_path, "w", encoding="utf-8") as fh:
            json.dump(capsule, fh, ensure_ascii=False)

        # 3) чистое окружение: без ATLAS_*, credentials, HOME-профилей; cwd — пустой temp
        clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        proc = subprocess.run([sys.executable, str(_CONSUMER), capsule_path],
                              capture_output=True, text=True, timeout=60,
                              cwd=workdir, env=clean_env)
        stdout = (proc.stdout or "").strip()
        try:
            result = json.loads(stdout.splitlines()[-1]) if stdout else {}
        except (json.JSONDecodeError, IndexError):
            return {"ok": False, "stage": "consumer", "handoff_id": handoff_id,
                    "reason": "потребитель не вернул JSON", "stderr": (proc.stderr or "")[:400]}

        # 4) валидация против run-result контракта
        errors = schema_validate(result, _load_run_result_schema())
        run_result_valid = not errors
        reconstruction = result.get("structured_output", {})

        # 5) ack точного хеша (свежая сессия подтверждает пакет)
        ack = None
        if acknowledge and result.get("state") == "SUCCEEDED" and run_result_valid:
            ack = self.handoff.acknowledge(
                project_id, handoff_id,
                ack_hash=reconstruction.get("acknowledged_hash", ""),
                baseline_ack=reconstruction.get("baseline_ack", ""),
                actual_head=actual_head, actor=actor, correlation_id=correlation_id)

        out = {
            "ok": bool(result.get("state") == "SUCCEEDED" and run_result_valid),
            "handoff_id": handoff_id,
            "run_result_valid": run_result_valid,
            "schema_errors": errors,
            "isolated": True,
            "consumer_argv": [os.path.basename(sys.executable), "scripts/vp4_fresh_consumer.py", "capsule.json"],
            "workdir": workdir,
            "reconstruction": reconstruction,
            "next_action": reconstruction.get("exact_next_action"),
            "ack": ack,
            "run_result": result,
        }
        audit.record("workorders.reconstruct.ran",
                     f"project={project_id} handoff={handoff_id} valid={run_result_valid} "
                     f"ok={out['ok']}",
                     actor=actor, correlation_id=correlation_id)
        return out

    # --- compact fallback (локальный детерминированный harness) -------------
    def compact_probe(self, content: dict, *, max_bytes: int = MAX_PACKAGE_BYTES) -> dict:
        """Сжать пакет, сохранив все immutable-поля и критерии; иначе OWNER_REQUIRED."""
        original = len(canonical_json(content).encode("utf-8"))
        if original <= max_bytes:
            return {"ok": True, "compacted": False, "original_bytes": original,
                    "compact_bytes": original, "preserved": True}
        compact = compact_content(content)
        preserved = self._preserves_required(content, compact)
        compact_bytes = len(canonical_json(compact).encode("utf-8"))
        if not preserved or compact_bytes > max_bytes:
            raise WorkOrderError("OWNER_REQUIRED",
                                 "безопасный compact невозможен без потери обязательных полей")
        return {"ok": True, "compacted": True, "original_bytes": original,
                "compact_bytes": compact_bytes, "preserved": True, "compact": compact}

    def _preserves_required(self, original: dict, compact: dict) -> bool:
        for k in _JP_REQUIRED:
            if k in original and k not in compact:
                return False
        # критерии сохранены полностью (по ID)
        oc = {c.get("id") for c in original.get("acceptance_criteria", [])}
        cc = {c.get("id") for c in compact.get("acceptance_criteria", [])}
        if oc != cc:
            return False
        return True


__all__ = ["ReconstructService"]
