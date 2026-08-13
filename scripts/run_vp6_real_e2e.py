#!/usr/bin/env python3
"""Отложенный РЕАЛЬНЫЙ VP-6 Quality E2E (Master Spec §18, §39; закрывается в VP-7).

Один малый реальный provider-сценарий Quality через существующий Atlas
Runner/Pipeline/Review путь: реальный Codex Planner → Claude Builder →
независимый Codex Reviewer (read-only), затем сборка **реального** SHA-bound
ReviewPackage из фактического артефакта и прогон Quality Firewall →
**реальный** QualityReport (verdict + объяснение). Потолок — **4 подписочных
вызова** (пайплайн тратит 3; Quality-слой детерминированный, 0 вызовов).

Жёсткие правила: safe aliases; изолированный синтетический git-репо; один writer;
Planner/Builder/Reviewer различны; Reviewer read-only; недоступность провайдера
**не** превращается в PASS; в durable/evidence нет transcript/credentials/email/
cookie/raw path. Если профиль реально требует логина — стоп с точной safe-командой.

Запуск (root; профили READY):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp6_real_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))
sys.path.insert(0, str(_ROOT / "scripts"))

import run_vp5_real_e2e as v5  # noqa: E402 — после sys.path; реальный pipeline

ART = _ROOT / "var" / "artifacts" / "vp6" / "real_e2e"
ART.mkdir(parents=True, exist_ok=True)
MAX_CALLS = 4
TS = v5.TS


class QualityE2E(v5.RealE2E):
    def __init__(self):
        super().__init__()
        self.budget = v5.Budget(MAX_CALLS)          # VP-6 потолок 4
        self.repo = Path(f"/tmp/atlas-vp6-e2e-{TS}")

    def run6(self):
        print(f"=== VP-6 REAL QUALITY E2E (потолок {MAX_CALLS} вызовов) ===")
        # precheck READY (read-only, без затрат)
        for alias in ("codex-plus-01", "claude-pro-01", "codex-plus-02"):
            root, user, exe, prov = self._p(alias)
            st = self._adapter(prov).auth_status(root, executable=exe, run_as_user=user)
            print(f"  precheck {alias}: authed={st.get('authenticated')} state={st.get('state')}")
            if not st.get("authenticated"):
                print(f"  BLOCKER: профиль {alias} не READY. Owner-действие: "
                      f"войти в изолированный root профиля официальным CLI "
                      f"({'codex login' if prov == 'codex' else 'claude /login'}).")
                return {"ok": False, "blocker": f"{alias} not authenticated"}

        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        db_dir = self.repo.parent / f"vp6-e2e-db-{TS}"
        db_dir.mkdir(parents=True, exist_ok=True)
        os.environ["ATLAS_DATA_DIR"] = str(db_dir)
        venv = _ROOT / ".venv" / "bin"
        alembic = str(venv / "alembic") if (venv / "alembic").exists() else "alembic"
        mig = subprocess.run([alembic, "upgrade", "head"], cwd=str(_ROOT), capture_output=True,
                             text=True, env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH', '')}",
                             "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})
        if mig.returncode != 0:
            print("  BLOCKER: миграция изолированной БД не удалась")
            return {"ok": False, "blocker": "isolated migration failed"}

        from datetime import datetime, timezone

        from atlas_core.db import init_engine, session_scope
        from atlas_core.orm import Project
        from atlas_core.runs import RunService
        from atlas_core.settings import load_settings
        settings = load_settings()
        self.db_path = settings.db_path
        init_engine(settings.db_url, settings.db_path)
        runs = RunService()
        head = self._make_repo()
        base_sha = head
        worktree = str(self.repo)
        with session_scope() as s:
            if s.get(Project, "proj_e2e") is None:
                now = datetime.now(timezone.utc)
                s.add(Project(id="proj_e2e", name="VP-6 real Quality E2E", source_kind="local_git",
                              source_location=worktree, status="connected", created_at=now, updated_at=now))
                s.commit()

        run = runs.create_run("proj_e2e", work_order_id="wo-q", vp_key="VP-6",
                              dedup_key=f"vp6-e2e-{TS}")["id"]
        runs.transition(run, "PREPARING", expected_version=runs.get_run(run)["version"], reason="prepare")
        runs.transition(run, "RUNNING", expected_version=runs.get_run(run)["version"], reason="planner")

        # реальный pipeline (3 вызова): Planner → Builder → независимый Reviewer
        _plan, bounded = self.planner(run, runs, "codex-plus-01")
        b = self.builder(run, runs, ["claude-pro-01", "claude-pro-02"], worktree)
        if not b["wrote"] and b.get("auth_error"):
            runs.transition(run, "AUTH_REQUIRED", expected_version=runs.get_run(run)["version"],
                            reason="claude auth expired", blocker="CLAUDE_AUTH_EXPIRED",
                            next_action="owner: claude /login в root профиля")
            return self._finish_blocker(run, runs, "CLAUDE_AUTH_EXPIRED",
                                        "claude auth status=loggedIn, но API 401; нужен owner re-login")
        runs.transition(run, "COLLECTING", expected_version=runs.get_run(run)["version"], reason="collect")
        head_after = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
        verdict_reviewer, independent = self.reviewer(run, runs, "codex-plus-02", b["profile"], b["session"])

        # --- VP-6 Quality-слой: реальный ReviewPackage + QualityReport (0 вызовов) ---
        from atlas_core.firewall import FirewallContext
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import (
            ReviewFacts,
            ReviewInputs,
            build_review_package,
            sha256_file,
        )
        art_path = self.repo / b.get("path", "calc.py")
        art_sha = sha256_file(art_path) if art_path.exists() else ""
        # независимая проверка поведения: реально импортируем artifact и считаем add(2,3)==5
        claim_ok = None
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("e2e_calc", str(art_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            claim_ok = hasattr(mod, "add") and mod.add(2, 3) == 5 and mod.add(-1, 1) == 0
        except Exception as exc:  # noqa: BLE001
            claim_ok = False
            self.evidence.setdefault("quality", {})["claim_error"] = type(exc).__name__

        pkg = build_review_package(ReviewInputs(
            project_id="proj_e2e", run_id=run, wo_key="wo-q", vp_key="VP-6",
            branch="atlas/vp-6-e2e", base_sha=base_sha, head_sha=head_after,
            spec_hash="sha256:e2e-spec", impact_class="LOCAL",
            artifact_hashes=[{"path": b.get("path", "calc.py"), "sha": art_sha}],
            acceptance=[{"criterion": "add(a,b)=a+b", "check": "recompute", "passed": bool(claim_ok)}],
            claims=[{"claim": "add реализована и корректна", "verified": bool(claim_ok)}],
            evidence_refs=["ev:recompute-add"], freshness={"brief": "FRESH", "baseline": "FRESH"}),
            actor="reviewer:codex-plus-02")
        ctx = FirewallContext(package=pkg, worktree=worktree, current_head=head_after,
                              claim_ok=bool(claim_ok),
                              claim_detail="independent recompute add(2,3)==5" if claim_ok
                              else "recompute mismatch/недоступно",
                              acceptance=pkg["acceptance"], freshness=pkg["freshness"],
                              docs_commands=[], runnable_commands=set(),
                              required_web_states=[], declared_web_states=[],
                              license_present=True, license_spdx="Apache-2.0")
        facts = ReviewFacts(current_head=head_after, artifacts={b.get("path", "calc.py"): art_sha},
                            evidence_present=["ev:recompute-add"])
        outcome = QualityService().review(pkg, ctx, facts, run_id=run, actor="reviewer:codex-plus-02")

        # честная классификация: реальный provider-verdict + реальный Quality-verdict
        final = "SUCCEEDED" if outcome.verdict == "PASS" else "OWNER_REQUIRED"
        runs.transition(run, final, expected_version=runs.get_run(run)["version"],
                        reason=f"quality {outcome.verdict}",
                        next_action="owner: обзор реального Quality-отчёта")
        sess = runs.provider_sessions(run)
        privacy_ok = all("transcript" not in json.dumps(x) for x in sess)

        self.evidence.update({
            "run_id": run, "bounded_planner_package": bounded,
            "builder_wrote_artifact": b["wrote"], "artifact_sha": art_sha,
            "max_concurrent_writers": b["max_writers"], "reviewer_independent": independent,
            "reviewer_verdict": verdict_reviewer, "quality_verdict": outcome.verdict,
            "quality_gate_fired": outcome.gate_fired,
            "review_package_id": pkg["id"], "review_package_hash": pkg["content_hash"],
            "quality_report_id": outcome.report["id"], "quality_report_hash": outcome.report["content_hash"],
            "quality_report_next_action": outcome.report["next_action"],
            "independent_claim_recompute_ok": bool(claim_ok),
            "final_state": final, "provider_calls_used": self.budget.used,
            "call_log": self.budget.log, "privacy_no_transcript": privacy_ok,
            "provider_unavailability_became_pass": False,
        })
        ok = (bounded and b["wrote"] and independent and privacy_ok
              and outcome.report["content_hash"] and pkg["content_hash"]
              and self.budget.used <= MAX_CALLS)
        self.evidence["ok"] = bool(ok)
        (ART / f"vp6_quality_e2e_{TS}.json").write_text(
            json.dumps(self.evidence, ensure_ascii=False, indent=2, sort_keys=True))
        digests = {p.name: v5.sha256_file(p) for p in sorted(ART.glob("*")) if p.is_file()}
        (ART / "manifest_sha256.json").write_text(
            json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"\n  Реальный provider Quality E2E: reviewer={verdict_reviewer} "
              f"quality={outcome.verdict} gate={outcome.gate_fired} "
              f"claim_recompute={claim_ok} calls={self.budget.used}/{MAX_CALLS}")
        print(f"  ReviewPackage={pkg['content_hash'][:20]} QualityReport={outcome.report['content_hash'][:20]}")
        print(f"  Evidence: {ART}")
        return {"ok": bool(ok), "quality_verdict": outcome.verdict, "calls": self.budget.used,
                "review_package_hash": pkg["content_hash"], "quality_report_hash": outcome.report["content_hash"]}

    def _finish_blocker(self, run, runs, code, detail):
        self.evidence.update({"run_id": run, "blocker": code, "blocker_detail": detail,
                              "provider_calls_used": self.budget.used, "call_log": self.budget.log,
                              "provider_unavailability_became_pass": False, "ok": False})
        (ART / f"vp6_quality_e2e_{TS}.json").write_text(
            json.dumps(self.evidence, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"\n  BLOCKER: {code} — {detail}. calls={self.budget.used}/{MAX_CALLS}. Evidence: {ART}")
        return {"ok": False, "blocker": code, "calls": self.budget.used}


if __name__ == "__main__":
    r = QualityE2E().run6()
    sys.exit(0 if r.get("ok") else 1)
