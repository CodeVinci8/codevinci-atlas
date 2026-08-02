#!/usr/bin/env python3
"""Локальный сервер для реальной Chrome-верификации VP-6 (не трогает живой стек).

Поднимает НОВЫЙ Core (реальная миграция 0006) в изолированном ATLAS_DATA_DIR,
сидит осмысленные фикстуры (проект, 4 сверенных профиля, Runs, Review & Quality
c findings/QualityReport) и монтирует собранный Web (`apps/web/dist`) на тот же
origin — чтобы Playwright снимал реальные наполненные экраны, а не пустые.

Запуск:
  ATLAS_DATA_DIR=<tmp> PYTHONPATH=apps/core:apps/runner \
    .venv/bin/python scripts/vp6_chrome_server.py <port>
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


def _now():
    return datetime.now(timezone.utc)


def seed(data_dir: str) -> None:
    os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
    os.environ["ATLAS_DATA_DIR"] = data_dir
    # реестр профилей (non-secret) для reconcile
    reg = Path(data_dir) / "profiles" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"profiles": {
        "codex-plus-01": {"alias": "codex-plus-01", "provider": "codex",
                          "root_path": "/x/codex-plus-01", "state": "AUTH_REQUIRED",
                          "runtime_user": "atlas-cx01"},
        "codex-plus-02": {"alias": "codex-plus-02", "provider": "codex",
                          "root_path": "/x/codex-plus-02", "state": "AUTH_REQUIRED",
                          "runtime_user": "atlas-cx02"},
        "claude-pro-01": {"alias": "claude-pro-01", "provider": "claude",
                          "root_path": "/x/claude-pro-01", "state": "AUTH_REQUIRED",
                          "runtime_user": "atlas-cl01"},
        "claude-pro-02": {"alias": "claude-pro-02", "provider": "claude",
                          "root_path": "/x/claude-pro-02", "state": "AUTH_REQUIRED",
                          "runtime_user": "atlas-cl02"},
    }}, ensure_ascii=False), encoding="utf-8")

    # миграция до head
    import subprocess
    venv = _ROOT / ".venv" / "bin"
    subprocess.run([str(venv / "alembic"), "upgrade", "head"], cwd=str(_ROOT), check=True,
                   env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH', '')}",
                        "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})

    from atlas_core.db import init_engine, session_scope
    from atlas_core.orm import Project, Run, RunRoleStep
    from atlas_core.settings import load_settings
    s = load_settings()
    init_engine(s.db_url, s.db_path)

    # проект
    with session_scope() as db:
        if db.get(Project, "proj_web") is None:
            db.add(Project(id="proj_web", name="CodeVinci Atlas (self)", source_kind="local_git",
                           source_location="/opt/CodeVinciAtlas", status="connected",
                           created_at=_now(), updated_at=_now()))
            db.commit()

    # профили → durable (reconcile)
    from atlas_core.profile_reconcile import reconcile_profiles
    from atlas_core.profiles import ProfileRegistry
    reconcile_profiles(ProfileRegistry(str(reg)), actor="chrome-fixture")

    # два Run (успешный + blocked) с reviewer-профилем
    from atlas_core.runs import RunService
    rs = RunService()
    for rid, vp, state, blocker in (("run_ok", "VP-6", "SUCCEEDED", ""),
                                    ("run_bl", "VP-6", "OWNER_REQUIRED", "SECOND_FIX_BLOCKED")):
        try:
            rs.create_run(project_id="proj_web", vp_key=vp, dedup_key=rid)
        except Exception:  # noqa: BLE001
            pass
    with session_scope() as db:
        # аккуратно проставим role step reviewer для alias
        for r in db.query(Run).all():
            db.add(RunRoleStep(id=f"rrs_{r.id}", run_id=r.id, project_id="proj_web",
                               role="reviewer", seq=3, effective_profile="codex-plus-02",
                               status="SUCCEEDED", verdict="PASS"))
        db.commit()

    # Review & Quality фикстуры: чистый PASS + BLOCKED(secret) + REVISE(false claim)
    from atlas_core.firewall import FirewallContext
    from atlas_core.quality import QualityService
    from atlas_core.reviewpkg import ReviewFacts, ReviewInputs, build_review_package
    q = QualityService()
    # PASS
    p1 = build_review_package(ReviewInputs(project_id="proj_web", run_id="run_ok", vp_key="VP-6",
        wo_key="wo-quality", branch="atlas/vp-6-review-quality", base_sha="b", head_sha="H",
        spec_hash="sha256:spec", impact_class="LOCAL",
        acceptance=[{"criterion": "Findings evidence-backed", "check": "firewall", "passed": True}],
        claims=[{"claim": "Все gates закрыты; findings evidence-backed"}],
        evidence_refs=["ev:firewall"], freshness={"brief": "FRESH", "baseline": "FRESH"}))
    q.review(p1, FirewallContext(package=p1, current_head="H", claim_ok=True, license_present=False,
             acceptance=p1["acceptance"], freshness=p1["freshness"]),
             ReviewFacts(current_head="H", evidence_present=["ev:firewall"]), run_id="run_ok")
    # BLOCKED (secret)
    wt = tempfile.mkdtemp(prefix="fx_secret_")
    with open(os.path.join(wt, "leak.py"), "w") as fh:
        fh.write('TOKEN = "sk-ant-' + "A" * 40 + '"\n')
    p2 = build_review_package(ReviewInputs(project_id="proj_web", run_id="run_bl", vp_key="VP-6",
        head_sha="H2", impact_class="HIGH_RISK", freshness={"dep:foo": "STALE"},
        claims=[{"claim": "Готово (ложно)"}]))
    q.review(p2, FirewallContext(package=p2, worktree=wt, current_head="H2", claim_ok=False,
             claim_detail="независимый пересчёт опроверг", license_present=False,
             security_check_present=False, freshness={"dep:foo": "STALE"}),
             ReviewFacts(current_head="H2"), run_id="run_bl")
    # REVISE (false claim, AI placeholder)
    wt2 = tempfile.mkdtemp(prefix="fx_ai_")
    with open(os.path.join(wt2, "stub.py"), "w") as fh:
        fh.write("def handler():\n    raise NotImplementedError  # TODO: реализовать\n")
    p3 = build_review_package(ReviewInputs(project_id="proj_web", run_id="run_ok", vp_key="VP-6",
        head_sha="H3", impact_class="LOCAL", claims=[{"claim": "handler реализован"}]))
    q.review(p3, FirewallContext(package=p3, worktree=wt2, current_head="H3", claim_ok=False,
             claim_detail="handler — заглушка", license_present=True),
             ReviewFacts(current_head="H3"), run_id="run_ok")


def build_app():
    from atlas_core.app import create_app
    from atlas_core.settings import load_settings
    from starlette.staticfiles import StaticFiles
    app = create_app(load_settings())
    dist = _ROOT / "apps/web/dist"
    # монтируем собранный Web последним — API-роуты имеют приоритет.
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    return app


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    data_dir = os.environ.get("ATLAS_DATA_DIR") or tempfile.mkdtemp(prefix="atlas-vp6-chrome-")
    if not (Path(data_dir) / "atlas.db").exists():
        seed(data_dir)
    else:
        os.environ.setdefault("ATLAS_CONFIG_FILE", "/nonexistent.yaml")
        os.environ["ATLAS_DATA_DIR"] = data_dir
    import uvicorn
    app = build_app()
    print(f"[vp6-chrome-server] data_dir={data_dir} port={port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
