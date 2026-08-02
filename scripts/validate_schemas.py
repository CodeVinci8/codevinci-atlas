#!/usr/bin/env python3
"""Валидация контрактных JSON-схем Atlas против РЕАЛЬНО сгенерированных инстансов
(Master Spec §25, §37 Phase I/L).

Проверяет: (1) все схемы в contracts/schemas/ — валидный JSON; (2) реальные
VP Spec / Work Order / JobPackage / HandoffPackage / RunResult, произведённые
сервисами, соответствуют своим схемам. Ловит дрейф между кодом и контрактом.

Запуск: PYTHONPATH=apps/core:apps/runner python3 scripts/validate_schemas.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))
SCHEMAS = _ROOT / "contracts" / "schemas"


def _now():
    return datetime.now(timezone.utc)


def _setup_project():
    os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
    os.environ["ATLAS_DATA_DIR"] = tempfile.mkdtemp(prefix="atlas-schema-")
    from atlas_core.db import get_engine, init_engine, session_scope
    from atlas_core.orm import Base, GitBaseline, Project
    from atlas_core.productmap import ProductMapService, content_hash
    from atlas_core.settings import load_settings
    st = load_settings()
    init_engine(st.db_url, st.db_path)
    Base.metadata.create_all(get_engine())
    with session_scope() as s:
        s.add(Project(id="p", name="T", source_kind="local_git", source_location="/x",
                      status="connected", created_at=_now(), updated_at=_now()))
        s.add(GitBaseline(id="b", project_id="p", branch="main", head="HEAD0",
                          content_hash=content_hash({"x": 1}),
                          instructions_json=json.dumps([{"path": "AGENTS.md", "scope": "root",
                                                         "precedence": 1}]),
                          observed_at=_now()))
        s.commit()
    pm = ProductMapService()
    pm.submit_intake("p", {"idea": "инст", "target_user": "solo", "desired_result": "A\nB\nC"})
    stt = pm.get_state("p")
    bf = stt["brief"]
    for d in stt["decisions"]:
        if d["required"]:
            pm.decide("p", d["id"], "accept", expected_version=d["version"])
    pm.approve_brief("p", bf["id"], expected_version=bf["version"])
    return st


def main() -> int:
    from atlas_core.schema_validate import validate

    failures: list[str] = []
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMAS.glob("*.json")):
        try:
            schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{path.name}: невалидный JSON — {exc}")
    print(f"Схем найдено: {len(schemas)}")

    _setup_project()
    from atlas_core.context_engine import ContextEngine
    from atlas_core.vp4handoff import HandoffService
    from atlas_core.workorders import WorkOrderService

    wos, ce, hs = WorkOrderService(), ContextEngine(), HandoffService()
    spec = wos.create_vp_spec("p", "VP-4")
    wo = wos.create_work_order("p", spec["id"], goal="Собрать VP-4")
    jp = ce.build_job_package("p", wo["id"])
    wo = wos.transition("p", wo["id"], "ready", expected_version=wo["version"])
    wo = wos.transition("p", wo["id"], "active", expected_version=wo["version"])
    cp = hs.build_checkpoint("p", wo["id"], current_head="H1", completed_criteria=["ac1"])
    wo = wos.transition("p", wo["id"], "checkpointed", expected_version=wo["version"])
    hp = hs.build_handoff("p", wo["id"], checkpoint_id=cp["id"], current_head="H1")

    # RunResult из свежего потребителя
    from atlas_core.reconstruct import ReconstructService
    rr = ReconstructService().run_fresh_session("p", hp["id"], actual_head="H1")["run_result"]

    cases = [
        ("vp-spec.json", spec["content"]),
        ("work-order.json", wo["content"]),
        ("job-package.json", jp["content"]),
        ("handoff-package.json", hp["content"]),
        ("run-result.json", rr),
    ]
    for schema_name, instance in cases:
        if schema_name not in schemas:
            failures.append(f"схема отсутствует: {schema_name}")
            continue
        errs = validate(instance, schemas[schema_name])
        if errs:
            failures.append(f"{schema_name}: {errs[:5]}")
        else:
            print(f"  OK {schema_name}")

    if failures:
        print("\nПРОВАЛ валидации схем:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nВсе контрактные схемы валидны и совпадают с реальными инстансами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
