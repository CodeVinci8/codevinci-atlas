#!/usr/bin/env python3
"""VP-4 fresh isolated consumer (Master Spec §16.4, §37 Phase H).

Полностью изолированный процесс: НЕ импортирует atlas_core, НЕ читает БД,
credentials, полный repo или прошлый chat. Единственный вход — файл-«капсула»
с принятым HandoffPackage (+ явно разрешённые safe-ссылки), путь к которому
передан в argv. Процесс реконструирует состояние и точное следующее действие,
подтверждает точный content-hash пакета и печатает результат, валидный по
контракту ``contracts/schemas/run-result.json``.

Запуск: python3 scripts/vp4_fresh_consumer.py <capsule.json>
Окружение вызывается минимальным (только PATH), cwd — временный каталог.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _provider_for_role(role: str) -> str:
    # Реконструкция провайдер-агностична; по умолчанию роль→провайдер (§17.1).
    return "codex" if role in ("planner", "reviewer") else "claude"


def reconstruct(capsule: dict) -> dict:
    handoff = capsule.get("handoff") or {}
    expected_hash = capsule.get("content_hash", "")
    run_id = capsule.get("run_id", "run_reconstruct")

    # 1) целостность пакета — пересчёт хеша тем же каноническим способом
    recomputed = _content_hash(handoff)
    if expected_hash and recomputed != expected_hash:
        return _fail(run_id, "HASH_MISMATCH",
                     f"content-hash капсулы не совпадает: {recomputed} != {expected_hash}")

    required = ("ids", "goal", "immutable_constraints", "acceptance_matrix",
               "exact_next_action", "prohibited_actions", "baseline_head")
    missing = [k for k in required if k not in handoff]
    if missing:
        return _fail(run_id, "OUTPUT_INVALID", f"в пакете нет полей: {missing}")

    ids = handoff.get("ids", {})
    matrix = handoff.get("acceptance_matrix", [])
    completed = [m["id"] for m in matrix if m.get("status") == "completed"]
    remaining = [m["id"] for m in matrix if m.get("status") != "completed"]
    role = handoff.get("role", "builder")

    # 2) реконструкция состояния ТОЛЬКО из пакета
    reconstruction = {
        "project_id": ids.get("project_id"),
        "vp_key": ids.get("vp_key"),
        "work_order_id": ids.get("work_order_id"),
        "goal": handoff.get("goal"),
        "finished_result": handoff.get("finished_result"),
        "immutable_constraints": handoff.get("immutable_constraints", []),
        "baseline_head": handoff.get("baseline_head"),
        "current_head": handoff.get("current_head"),
        "completed_criteria": completed,
        "remaining_criteria": remaining,
        "changed_files": handoff.get("changed_files", []),
        "command_outcomes": handoff.get("commands", []),
        "failures": handoff.get("failures", []),
        "blocker": (handoff.get("failures") or [{}])[0].get("reason", "") if handoff.get("failures") else "нет",
        "exact_next_action": handoff.get("exact_next_action"),
        "prohibited_actions": handoff.get("prohibited_actions", []),
        "acknowledged_hash": recomputed,
        "baseline_ack": handoff.get("baseline_head"),
        "reconstructed_from": "handoff_only",
        "used_prior_chat": False,
        "used_credentials": False,
        "used_full_repo": False,
    }
    output_hash = _content_hash(reconstruction)
    return {
        "run_id": run_id,
        "state": "SUCCEEDED",
        "provider": _provider_for_role(role),
        "profile_alias": "local-reconstruct",
        "session_id": None,
        "exit_code": 0,
        "structured_output": reconstruction,
        "output_hash": output_hash,
        "error_code": None,
        "error_evidence": None,
        "started_at": _now(),
        "finished_at": _now(),
    }


def _fail(run_id: str, code: str, evidence: str) -> dict:
    return {
        "run_id": run_id, "state": "FAILED", "provider": "claude",
        "profile_alias": "local-reconstruct", "session_id": None, "exit_code": 1,
        "structured_output": {}, "output_hash": None,
        "error_code": code, "error_evidence": evidence,
        "started_at": _now(), "finished_at": _now(),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps(_fail("run_reconstruct", "OUTPUT_INVALID", "нет пути к капсуле")))
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        capsule = json.load(fh)
    result = reconstruct(capsule)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
