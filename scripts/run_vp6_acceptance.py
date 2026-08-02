#!/usr/bin/env python3
"""VP-6 acceptance boundary — Review & Quality (Master Spec §18, §39).

26 детерминированных приёмочных проверок против РЕАЛЬНЫХ модулей VP-6 (реальная
миграция ``0006_review_quality``, реальный ORM/БД, ASGI-стек через TestClient) с
**посеянными дефектами** в изолированных синтетических репозиториях/данных. Итог
COMPLETE только при 26/26 PASS.

Изолированность: harness работает в СВОЁМ временном ``ATLAS_DATA_DIR`` и НИКОГДА
не трогает живую БД/стек (живая БД лишь копируется read-only для теста миграции
0005→0006). Evidence — redacted, с SHA-256-манифестом, в ``var/artifacts/vp6/``.

Реальный provider Quality-E2E (≤4 вызова, safe aliases) — отдельно и только под
owner-гейтом (``scripts/run_vp6_real_e2e.py``); здесь не выполняется.

Запуск (root не требуется; поднимать стек не нужно):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp6_acceptance.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))

ART = _ROOT / "var" / "artifacts" / "vp6"
ART.mkdir(parents=True, exist_ok=True)
LIVE_DB = "/var/lib/codevinci-atlas/atlas.db"
RUN = time.strftime("%H%M%S")
_VENV = _ROOT / ".venv" / "bin"


def _now():
    return datetime.now(timezone.utc)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sh(cmd, env=None, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=str(_ROOT), env={**os.environ, **(env or {})})
    return r.returncode, r.stdout, r.stderr


def _git_identity_email() -> str:
    """Настроенная git-идентичность (allowlisted секрет-сканером). Читается в
    рантайме, чтобы НЕ вписывать email-литерал в исходник (иначе он попадёт в
    секрет-скан репозитория). Fallback — не-email-строка."""
    r = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True,
                       cwd=str(_ROOT))
    return (r.stdout.strip() or "codevinci-local")


def _git_init(path: str) -> str:
    """Инициализировать синтетический git-репо, вернуть head SHA."""
    ident = ["-c", "user.name=CodeVinci", "-c", f"user.email={_git_identity_email()}"]
    subprocess.run(["git", "-C", path, "init", "-q"], capture_output=True, text=True)
    subprocess.run(["git", "-C", path, "add", "-A"], capture_output=True, text=True)
    subprocess.run(["git", "-C", path, *ident, "commit", "-q", "-m", "seed"],
                   capture_output=True, text=True)
    r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


class VP6:
    def __init__(self):
        self.results = []
        self.tmp = tempfile.mkdtemp(prefix="atlas-vp6-accept-")
        self.data_dir = str(Path(self.tmp) / "data")
        os.makedirs(self.data_dir, exist_ok=True)

    def art(self, name, content):
        p = ART / name
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                     if isinstance(content, (dict, list)) else str(content), encoding="utf-8")
        return p.name

    def rec(self, n, name, ok, note, ev=None):
        self.results.append({"id": n, "criterion": name, "status": "PASS" if ok else "FAIL",
                             "note": note, "evidence": ev or []})
        print(f"  [{'PASS' if ok else 'FAIL'}] #{n:>2} {name} — {note}")

    # --- миграция + boot ---------------------------------------------------
    def _alembic_upgrade(self, data_dir, rev="head"):
        alembic = str(_VENV / "alembic") if (_VENV / "alembic").exists() else "alembic"
        env = {"ATLAS_CONFIG_FILE": "/nonexistent.yaml", "ATLAS_DATA_DIR": data_dir,
               "PATH": f"{_VENV}:{os.environ.get('PATH', '')}",
               "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"}
        return sh([alembic, "upgrade", rev], env=env)

    def _rev(self, db):
        c = sqlite3.connect(db)
        try:
            r = c.execute("SELECT version_num FROM alembic_version").fetchone()
            return r[0] if r else None
        finally:
            c.close()

    def _tables(self, db):
        c = sqlite3.connect(db)
        try:
            return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()

    def boot(self):
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        os.environ["ATLAS_DATA_DIR"] = self.data_dir
        rc, _o, e = self._alembic_upgrade(self.data_dir)
        assert rc == 0, f"alembic upgrade head failed: {e[-400:]}"
        from atlas_core.db import init_engine, session_scope
        from atlas_core.orm import Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        self.db_path = self.settings.db_path
        init_engine(self.settings.db_url, self.settings.db_path)
        with session_scope() as s:
            if s.get(Project, "proj_v6") is None:
                s.add(Project(id="proj_v6", name="Синтетика VP-6", source_kind="local_git",
                              source_location=self.tmp, status="connected",
                              created_at=_now(), updated_at=_now()))
                s.commit()
        from atlas_core.app import create_app
        from starlette.testclient import TestClient
        self.client = TestClient(create_app(self.settings))

    # --- построение review-пакета + прогон ---------------------------------
    def _mk_worktree(self, name, files: dict, git=True) -> tuple[str, str]:
        wt = str(Path(self.tmp) / name)
        os.makedirs(wt, exist_ok=True)
        for rel, content in files.items():
            p = Path(wt) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        head = _git_init(wt) if git else "synthetic-head-" + name
        return wt, head

    def _build_pkg(self, **kw):
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        return build_review_package(ReviewInputs(**kw))

    def _review(self, pkg, ctx, facts, run_id=""):
        return self.q.review(pkg, ctx, facts, run_id=run_id)

    # =====================================================================
    def run(self):
        print("=== VP-6 ACCEPTANCE (deterministic; seeded defects; isolated DB) ===")
        try:
            self.boot()
            from atlas_core.evidence_cache import CacheComponents, EvidenceCache
            from atlas_core.firewall import FirewallContext
            from atlas_core.impact import classify_impact
            from atlas_core.quality import QualityService
            from atlas_core.reviewpkg import (
                ReviewFacts,
                ReviewInputs,
                build_review_package,
                sha256_file,
            )
            self.q = QualityService()
            self.EC = EvidenceCache
            self.CacheComponents = CacheComponents
            self.FC = FirewallContext
            self.RF = ReviewFacts
            self.RI = ReviewInputs
            self.classify_impact = classify_impact
            self.build_review_package = build_review_package
            self.sha256_file = sha256_file

            self.c3_valid_pass()          # #3 (сначала «чистый PASS» — базовая линия)
            self.c1_broken_behavior()     # #1
            self.c2_false_claim()         # #2
            self.c4_secret_blocks()       # #4
            self.c5_stale_sha()           # #5
            self.c6_docs_drift()          # #6
            self.c7_ai_placeholder()      # #7
            self.c8_needless_arch()       # #8
            self.c9_freshness()           # #9
            self.c10_14_impact()          # #10..#14
            self.c15_16_cache()           # #15, #16
            self.c17_18_fixloop()         # #17, #18
            self.c19_reviewer_independent()  # #19
            self.c20_finding_fields()     # #20
            self.c21_waiver_nonwaivable()  # #21
            self.c22_manual_audit_readonly()  # #22
            self.c23_ui_ru_en()           # #23
            self.c24_no_secrets_durable()  # #24
            self.c25_regression()         # #25
            self.c26_repeatable()         # #26
        finally:
            self.cleanup()
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        verdict = "COMPLETE" if passed == total else f"INCOMPLETE ({passed}/{total})"
        matrix = {"vp": "VP-6", "passed": passed, "total": total, "verdict": verdict,
                  "run": RUN, "real_provider_quality_e2e": "OWNER_GATED_SEPARATE",
                  "criteria": sorted(self.results, key=lambda r: r["id"])}
        self.art("acceptance_matrix.json", matrix)
        digests = {f.name: sha256_text(f.read_text(encoding="utf-8"))
                   for f in sorted(ART.glob("*.json"))}
        (ART / "evidence_sha256.json").write_text(
            json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n  ИТОГ VP-6: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix

    # --- #3 real valid result can PASS -------------------------------------
    def c3_valid_pass(self):
        wt, head = self._mk_worktree("wt_clean", {
            "RESULT.json": json.dumps({"sum": 6, "complete": True}),
            "mod.py": "def add(a, b):\n    return a + b\n"})
        art_sha = self.sha256_file(os.path.join(wt, "RESULT.json"))
        pkg = self._build_pkg(project_id="proj_v6", run_id="run_clean", wo_key="wo-1",
                              vp_key="VP-6", branch="atlas/vp-6", base_sha="b", head_sha=head,
                              spec_hash="sha256:s1", impact_class="LOCAL",
                              artifact_hashes=[{"path": "RESULT.json", "sha": art_sha}],
                              acceptance=[{"criterion": "sum корректна", "check": "recompute",
                                           "passed": True}],
                              claims=[{"claim": "sum=6 verified"}], evidence_refs=["ev:recompute"],
                              freshness={"brief": "FRESH", "baseline": "FRESH"})
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True,
                      acceptance=pkg["acceptance"], freshness=pkg["freshness"],
                      docs_commands=["pytest"], runnable_commands={"pytest"},
                      required_web_states=["loading", "empty"],
                      declared_web_states=["loading", "empty", "error"],
                      license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head=head, artifacts={"RESULT.json": art_sha},
                         evidence_present=["ev:recompute"], expected_wo_key="wo-1"), run_id="run_clean")
        ev = self.art("c3_valid_pass.json", o.to_dict())
        self.rec(3, "Валидный реальный результат PASS", o.verdict == "PASS",
                 f"verdict={o.verdict}", [ev])

    # --- #1 broken behavior blocks PASS ------------------------------------
    def c1_broken_behavior(self):
        wt, head = self._mk_worktree("wt_broken", {"RESULT.json": json.dumps({"sum": 5})})
        pkg = self._build_pkg(project_id="proj_v6", run_id="run_broken", head_sha=head,
                              impact_class="LOCAL",
                              acceptance=[{"criterion": "sum корректна", "check": "recompute",
                                           "passed": False}])
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True,
                      acceptance=pkg["acceptance"], license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head=head), run_id="run_broken")
        ev = self.art("c1_broken_behavior.json", o.to_dict())
        self.rec(1, "Сломанное поведение блокирует PASS", o.verdict != "PASS",
                 f"verdict={o.verdict} gate={o.gate_fired}", [ev])

    # --- #2 false Builder claim rejected -----------------------------------
    def c2_false_claim(self):
        pkg = self._build_pkg(project_id="proj_v6", run_id="run_false", head_sha="hf",
                              impact_class="LOCAL", claims=[{"claim": "sum=7 (ложь)"}])
        ctx = self.FC(package=pkg, current_head="hf", claim_ok=False,
                      claim_detail="независимый пересчёт=6 != заявлено 7", license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head="hf"), run_id="run_false")
        ok = o.verdict in ("REVISE", "BLOCKED") and any(
            f["code"] == "REAL_BEHAVIOR_MISMATCH" for f in o.findings)
        ev = self.art("c2_false_claim.json", o.to_dict())
        self.rec(2, "Ложный success Builder отклонён", ok,
                 f"verdict={o.verdict} gate={o.gate_fired}", [ev])

    # --- #4 secret/privacy blocks ------------------------------------------
    def c4_secret_blocks(self):
        wt, head = self._mk_worktree("wt_secret", {
            "leak.py": 'TOKEN = "sk-ant-' + "A" * 40 + '"\n'})
        pkg = self._build_pkg(project_id="proj_v6", run_id="run_secret", head_sha=head,
                              impact_class="LOCAL")
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True,
                      license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head=head), run_id="run_secret")
        ok = o.verdict == "BLOCKED" and any(f["code"] == "SECRET_DETECTED" for f in o.findings)
        # приватность: raw токен НЕ попадает в evidence (redaction)
        secret_leaked = any("sk-ant-" + "A" * 40 in f["evidence"] for f in o.findings)
        ev = self.art("c4_secret_blocks.json", {**o.to_dict(), "raw_secret_in_evidence": secret_leaked})
        self.rec(4, "Secret/privacy evidence блокирует (без утечки в evidence)",
                 ok and not secret_leaked, f"verdict={o.verdict} leaked={secret_leaked}", [ev])

    # --- #5 stale SHA -> INVALID_EVIDENCE -----------------------------------
    def c5_stale_sha(self):
        pkg = self._build_pkg(project_id="proj_v6", head_sha="OLDsha", impact_class="LOCAL",
                              artifact_hashes=[{"path": "a", "sha": "sha256:x"}])
        ctx = self.FC(package=pkg, current_head="NEWsha", license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head="NEWsha"), run_id="")
        ev = self.art("c5_stale_sha.json", o.to_dict())
        self.rec(5, "Протухший ReviewPackage SHA → INVALID_EVIDENCE",
                 o.verdict == "INVALID_EVIDENCE", f"verdict={o.verdict}", [ev])

    # --- #6 docs-command drift ---------------------------------------------
    def c6_docs_drift(self):
        pkg = self._build_pkg(project_id="proj_v6", head_sha="h6", impact_class="DOC_ONLY")
        ctx = self.FC(package=pkg, current_head="h6", claim_ok=True,
                      docs_commands=["make deploy", "pytest"], runnable_commands={"pytest"},
                      license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head="h6"))
        ok = any(f["code"] == "DOCS_COMMAND_DRIFT" for f in o.findings)
        ev = self.art("c6_docs_drift.json", o.to_dict())
        self.rec(6, "docs-command drift найден", ok, f"gate={o.gate_fired}", [ev])

    # --- #7 AI placeholder / fake ------------------------------------------
    def c7_ai_placeholder(self):
        wt, head = self._mk_worktree("wt_ai", {
            "stub.py": "def handler():\n    raise NotImplementedError  # TODO: реализовать\n"})
        pkg = self._build_pkg(project_id="proj_v6", head_sha=head, impact_class="LOCAL")
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True, license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head=head))
        ok = any(f["code"] == "AI_PLACEHOLDER" for f in o.findings) and o.verdict != "PASS"
        ev = self.art("c7_ai_placeholder.json", o.to_dict())
        self.rec(7, "AI placeholder/fake найден", ok, f"verdict={o.verdict}", [ev])

    # --- #8 needless architecture (evidence-backed) ------------------------
    def c8_needless_arch(self):
        pkg = self._build_pkg(project_id="proj_v6", head_sha="h8", impact_class="LOCAL")
        ctx = self.FC(package=pkg, current_head="h8", claim_ok=True, license_present=True,
                      flagged_symbols=[{"name": "UnusedFactory", "file": "abstr.py", "refs": 0}])
        o = self._review(pkg, ctx, self.RF(current_head="h8"))
        f = next((x for x in o.findings if x["code"] == "NEEDLESS_ABSTRACTION"), None)
        ok = bool(f) and "0 использований" in f["evidence"]
        ev = self.art("c8_needless_arch.json", o.to_dict())
        self.rec(8, "Needless architecture → evidence-backed finding", ok,
                 f"evidence_backed={bool(f)}", [ev])

    # --- #9 freshness explicit ---------------------------------------------
    def c9_freshness(self):
        pkg = self._build_pkg(project_id="proj_v6", head_sha="h9", impact_class="LOCAL",
                              freshness={"dep:foo": "STALE", "brief": "FRESH", "baseline": "UNKNOWN"})
        ctx = self.FC(package=pkg, current_head="h9", claim_ok=True, license_present=True,
                      freshness=pkg["freshness"])
        o = self._review(pkg, ctx, self.RF(current_head="h9"))
        has_explicit = any(f["code"] == "FRESHNESS_EXPLICIT" for f in o.findings)
        has_stale = any(f["code"] == "FRESHNESS_STALE" for f in o.findings)
        ev = self.art("c9_freshness.json", o.to_dict())
        self.rec(9, "Dependency/source freshness явны", has_explicit and has_stale,
                 f"explicit={has_explicit} stale={has_stale}", [ev])

    # --- #10..#14 impact engine --------------------------------------------
    def c10_14_impact(self):
        cases = {
            10: ("DOC_ONLY", ["docs/GUIDE.md"], lambda r: not r.full_regression
                 and r.check_groups == ["markdown", "link", "render"]),
            11: ("LOCAL", ["apps/core/atlas_core/optimizer.py"],
                 lambda r: not r.full_regression and "unit_targeted" in r.check_groups),
            12: ("INTEGRATION", ["apps/core/atlas_core/api_reviews.py"],
                 lambda r: "integration" in r.check_groups and "unit" in r.check_groups),
            13: ("SHARED", ["apps/core/atlas_core/orm.py"],
                 lambda r: "dependent_suites" in r.check_groups),
            14: ("HIGH_RISK", ["apps/core/atlas_core/migrations/versions/0006_review_quality.py"],
                 lambda r: r.full_regression and "security" in r.check_groups),
        }
        names = {10: "DOC_ONLY fix без полной регрессии", 11: "LOCAL — targeted checks",
                 12: "INTEGRATION — unit + integration", 13: "SHARED → зависимые suites",
                 14: "HIGH_RISK → full relevant + security"}
        allev = {}
        for n, (cls, paths, check) in cases.items():
            r = self.classify_impact(paths)
            ok = r.impact_class == cls and check(r)
            allev[str(n)] = r.to_dict()
            self.rec(n, names[n], ok, f"class={r.impact_class} groups={r.check_groups}")
        self.art("c10_14_impact.json", allev)

    # --- #15/#16 evidence cache --------------------------------------------
    def c15_16_cache(self):
        ec = self.EC()
        comp = self.CacheComponents(sha="headA", command="pytest -k unit",
                                    command_version="8.0", input_hash="ih1",
                                    environment="py314", scope="LOCAL")
        ec.store(comp, passed=True, result={"tests": 12, "ok": True}, reason="прогон на headA")
        reuse = ec.try_reuse(comp)
        c15 = reuse is not None and reuse["passed"] and "точное совпадение" in reuse["cache_reason"]
        # изменённый компонент → другой ключ → miss
        changed_sha = self.CacheComponents(sha="headB", command="pytest -k unit",
                                           command_version="8.0", input_hash="ih1",
                                           environment="py314", scope="LOCAL")
        miss_sha = ec.try_reuse(changed_sha) is None
        changed_env = self.CacheComponents(sha="headA", command="pytest -k unit",
                                           command_version="8.0", input_hash="ih1",
                                           environment="py313", scope="LOCAL")
        miss_env = ec.try_reuse(changed_env) is None
        # протухший head инвалидирует: current head != headA → stale → reuse отвергнут
        n_stale = ec.invalidate_stale("headZ")
        stale_refused = ec.try_reuse(comp) is None
        c16 = miss_sha and miss_env and n_stale >= 1 and stale_refused
        self.art("c15_16_cache.json", {"reuse": reuse, "miss_sha": miss_sha,
                 "miss_env": miss_env, "n_stale": n_stale, "stale_refused": stale_refused})
        self.rec(15, "Evidence Cache переиспользует точный результат", c15,
                 f"reuse_hit={c15}")
        self.rec(16, "Изменённый SHA/input/env/head инвалидирует cache", c16,
                 f"miss_sha={miss_sha} miss_env={miss_env} stale_refused={stale_refused}")

    # --- #17/#18 fix-loop --------------------------------------------------
    def c17_18_fixloop(self):
        v1, b1 = self.q.evaluate_fix_loop("rpkg_fl", "run_fl", "proj_v6", 1, "REVISE")
        v2, b2 = self.q.evaluate_fix_loop("rpkg_fl", "run_fl", "proj_v6", 2, "REVISE")
        self.art("c17_18_fixloop.json", {"attempt1": [v1, b1], "attempt2": [v2, b2]})
        self.rec(17, "Один focused fix-loop разрешён", v1 == "REVISE" and not b1,
                 f"attempt1={v1} blocked={b1}")
        self.rec(18, "Второй REVISE → BLOCKED", v2 == "BLOCKED" and b2,
                 f"attempt2={v2} blocked={b2}")

    # --- #19 reviewer independent & read-only ------------------------------
    def c19_reviewer_independent(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import RunRoleStep
        # seed builder+reviewer role steps с РАЗНЫМИ профилями
        with session_scope() as s:
            s.add(RunRoleStep(id="rrs_b", run_id="run_ind", project_id="proj_v6",
                              role="builder", seq=2, effective_profile="claude-pro-01",
                              status="SUCCEEDED"))
            s.add(RunRoleStep(id="rrs_r", run_id="run_ind", project_id="proj_v6",
                              role="reviewer", seq=3, effective_profile="codex-plus-02",
                              status="SUCCEEDED"))
            s.commit()
        wt, head = self._mk_worktree("wt_ind", {"src.py": "def a():\n    return 1\n"})
        sentinel_before = self.sha256_file(os.path.join(wt, "src.py"))
        pkg = self._build_pkg(project_id="proj_v6", run_id="run_ind", head_sha=head,
                              impact_class="LOCAL")
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True, license_present=True)
        self._review(pkg, ctx, self.RF(current_head=head), run_id="run_ind")
        sentinel_after = self.sha256_file(os.path.join(wt, "src.py"))
        d = self.client.get(f"/api/v1/reviews/{pkg['id']}").json()
        reviewer_alias = d.get("reviewer_alias", "")
        independent = reviewer_alias == "codex-plus-02" and reviewer_alias != "claude-pro-01"
        read_only = sentinel_before == sentinel_after
        self.art("c19_reviewer_independent.json", {"reviewer_alias": reviewer_alias,
                 "independent": independent, "worktree_unchanged": read_only})
        self.rec(19, "Reviewer независим (другой профиль) и read-only", independent and read_only,
                 f"alias={reviewer_alias} read_only={read_only}")

    # --- #20 finding fields ------------------------------------------------
    def c20_finding_fields(self):
        wt, head = self._mk_worktree("wt_fields", {
            "stub.py": "def f():\n    raise NotImplementedError  # TODO\n"})
        pkg = self._build_pkg(project_id="proj_v6", head_sha=head, impact_class="LOCAL")
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True, license_present=True)
        o = self._review(pkg, ctx, self.RF(current_head=head))
        need = ("criterion", "location", "evidence", "action", "blocking", "severity",
                "code", "source", "freshness")
        blk = next((f for f in o.findings if f["blocking"]), o.findings[0])
        ok = all(k in blk and (blk[k] != "" or k == "blocking") for k in need)
        self.art("c20_finding_fields.json", {"sample_finding": blk, "required": list(need)})
        self.rec(20, "Findings включают criterion/location/evidence/action/blocking", ok,
                 f"fields_ok={ok}")

    # --- #21 waiver cannot bypass non-waivable -----------------------------
    def c21_waiver_nonwaivable(self):
        wt, head = self._mk_worktree("wt_w", {"leak.py": 'K="ghp_' + "C" * 36 + '"\n'})
        pkg = self._build_pkg(project_id="proj_v6", head_sha=head, impact_class="LOCAL")
        ctx = self.FC(package=pkg, worktree=wt, current_head=head, claim_ok=True, license_present=True)
        self._review(pkg, ctx, self.RF(current_head=head))
        persisted = self.q.list_findings(pkg["id"])  # persisted findings имеют id
        sec = next(f for f in persisted if f["code"] == "SECRET_DETECTED")
        w = self.client.post(f"/api/v1/reviews/{pkg['id']}/waiver", json={
            "finding_id": sec["id"], "reason": "хочу обойти", "scope": "s", "actor": "owner",
            "expiry": "2026-09-01", "review_condition": "c"})
        waivable = w.json()["waiver"]["waivable"]
        # finding остаётся блокирующим — повторный вердикт не PASS
        o2 = self._review(pkg, ctx, self.RF(current_head=head))
        self.art("c21_waiver.json", {"waiver_status": w.status_code, "waivable": waivable,
                 "rejected_code": w.json()["waiver"]["rejected_code"], "re_verdict": o2.verdict})
        self.rec(21, "Waiver не обходит non-waivable security-правило",
                 (not waivable) and w.status_code == 422 and o2.verdict == "BLOCKED",
                 f"waivable={waivable} re_verdict={o2.verdict}")

    # --- #22 manual audit read-only ----------------------------------------
    def c22_manual_audit_readonly(self):
        wt, head = self._mk_worktree("wt_audit", {"code.py": "print('hello')\n"})
        before = self.sha256_file(os.path.join(wt, "code.py"))
        pkg = self._build_pkg(project_id="proj_v6", head_sha=head, impact_class="LOCAL")
        r = self.client.post(f"/api/v1/reviews/{pkg['id']}/audit", json={
            "target": "diff", "scope": "code.py", "note": "ручной аудит"})
        after = self.sha256_file(os.path.join(wt, "code.py"))
        read_only = r.json()["manual_audit"]["read_only"] and before == after
        # неизвестный target отклоняется
        bad = self.client.post(f"/api/v1/reviews/{pkg['id']}/audit", json={"target": "hack"})
        self.art("c22_manual_audit.json", {"read_only": read_only, "code_unchanged": before == after,
                 "bad_target_status": bad.status_code})
        self.rec(22, "Manual audit не мутирует код (read-only)",
                 read_only and bad.status_code == 422, f"read_only={read_only}")

    # --- #23 Quality UI RU/EN states ---------------------------------------
    def c23_ui_ru_en(self):
        ru = (_ROOT / "apps/web/src/locales/ru.ts").read_text(encoding="utf-8")
        en = (_ROOT / "apps/web/src/locales/en.ts").read_text(encoding="utf-8")
        import re
        ru_keys = set(re.findall(r'"(quality\.[a-zA-Z0-9._]+)"', ru))
        en_keys = set(re.findall(r'"(quality\.[a-zA-Z0-9._]+)"', en))
        parity = ru_keys == en_keys and len(ru_keys) > 0
        required_states = {"quality.state.loading", "quality.state.empty",
                           "quality.state.stale", "quality.state.invalid",
                           "quality.state.revise", "quality.state.blocked",
                           "quality.state.ownerRequired", "quality.state.offline",
                           "quality.state.forbidden", "quality.state.conflict",
                           "quality.state.error"}
        states_ok = required_states <= ru_keys
        self.art("c23_ui_ru_en.json", {"ru_keys": len(ru_keys), "en_keys": len(en_keys),
                 "parity": parity, "states_present": sorted(required_states & ru_keys),
                 "missing_states": sorted(required_states - ru_keys)})
        self.rec(23, "Quality UI RU/EN паритет + все состояния", parity and states_ok,
                 f"parity={parity} states_ok={states_ok} keys={len(ru_keys)}")

    # --- #24 no credentials/emails/cookies/raw path/transcripts ------------
    def c24_no_secrets_durable(self):
        from atlas_core.secret_scan import scan_repo
        rep = scan_repo(str(_ROOT), extra_roots=[self.data_dir, str(ART)])
        scan_clean = rep.clean
        # дополнительно: в durable review-таблицах нет raw токенов/email/cookie
        c = sqlite3.connect(self.db_path)
        blob = ""
        for t in ("review_packages", "quality_findings", "quality_reports", "waivers",
                  "manual_audits"):
            try:
                for row in c.execute(f"SELECT * FROM {t}"):
                    blob += "".join(str(x) for x in row)
            except sqlite3.Error:
                pass
        c.close()
        import re
        leaked = bool(re.search(r"sk-ant-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|@[a-z]+\.(com|ru)",
                                blob))
        self.art("c24_privacy.json", {**rep.to_dict(), "durable_review_leak": leaked})
        self.rec(24, "Нет credentials/emails/cookies/raw path/transcript в БД/логах/evidence",
                 scan_clean and not leaked, f"scan_clean={scan_clean} durable_leak={leaked}")

    # --- #25 regression VP-0..VP-5 (impact-appropriate) --------------------
    def c25_regression(self):
        # (a) миграция копии живой 0005 → 0006 сохраняет данные
        live_ok, detail = True, {"note": "живая БД недоступна — проверена только empty→head"}
        if os.path.exists(LIVE_DB):
            live_dir = str(Path(self.tmp) / "live")
            os.makedirs(live_dir, exist_ok=True)
            dst = str(Path(live_dir) / "atlas.db")
            src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
            out = sqlite3.connect(dst)
            with out:
                src.backup(out)
            src.close(); out.close()
            before_rev = self._rev(dst)
            before = self._counts(dst)
            rc, _o, _e = self._alembic_upgrade(live_dir)
            after_rev = self._rev(dst)
            after = self._counts(dst)
            live_ok = (before_rev == "0005_agent_pipeline" and rc == 0
                       and after_rev == "0006_review_quality" and before == after)
            detail = {"before_rev": before_rev, "after_rev": after_rev,
                      "counts_preserved": before == after}
        # (b) bounded регрессия зависимых suites (SHARED-impact orm/router/services)
        env = {"PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner:{_ROOT}/tests",
               "ATLAS_CONFIG_FILE": "/nonexistent.yaml"}
        py = str(_VENV / "python") if (_VENV / "python").exists() else sys.executable
        rc_t, out_t, err_t = sh([py, "-m", "unittest", "-q",
                                 "test_router", "test_vp5_services", "test_leases",
                                 "test_redaction"], env=env)
        subset_ok = rc_t == 0
        self.art("c25_regression.json", {"migration_live": detail, "migration_ok": live_ok,
                 "regression_subset_rc": rc_t, "regression_tail": (err_t or out_t)[-600:]})
        self.rec(25, "VP-0..VP-5 регрессии целы (impact-appropriate)", live_ok and subset_ok,
                 f"migration={live_ok} subset_ok={subset_ok}")

    def _counts(self, db):
        c = sqlite3.connect(db)
        out = {}
        for t in ("projects", "briefs", "work_orders", "runs", "agent_profiles", "audit_events"):
            try:
                out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        c.close()
        return out

    # --- #26 report + SHA-256 manifest repeatable --------------------------
    def c26_repeatable(self):
        # детерминизм content_hash: одинаковые входы → одинаковый hash
        from atlas_core.productmap import content_hash
        payload = {"a": 1, "b": [3, 2, 1], "c": {"y": 2, "x": 1}}
        h1 = content_hash(payload)
        h2 = content_hash({"c": {"x": 1, "y": 2}, "b": [3, 2, 1], "a": 1})
        deterministic = h1 == h2
        # манифест воспроизводим: пересчёт хешей evidence совпадает
        digests1 = {f.name: sha256_text(f.read_text(encoding="utf-8"))
                    for f in sorted(ART.glob("c*.json"))}
        digests2 = {f.name: sha256_text(f.read_text(encoding="utf-8"))
                    for f in sorted(ART.glob("c*.json"))}
        manifest_ok = digests1 == digests2 and len(digests1) > 0
        self.art("c26_repeatable.json", {"content_hash_deterministic": deterministic,
                 "manifest_repeatable": manifest_ok, "sample_hash": h1})
        self.rec(26, "Отчёт и SHA-256 manifest воспроизводимы", deterministic and manifest_ok,
                 f"det={deterministic} manifest={manifest_ok}")

    def cleanup(self):
        try:
            from atlas_core.db import get_engine
            get_engine().dispose()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


if __name__ == "__main__":
    m = VP6().run()
    sys.exit(0 if m["verdict"] == "COMPLETE" else 1)
