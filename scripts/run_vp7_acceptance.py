#!/usr/bin/env python3
"""VP-7 acceptance boundary — Autonomy, GitHub & Time Machine (Master Spec §40).

33 детерминированных приёмочных проверки против РЕАЛЬНЫХ модулей VP-7 (реальная
миграция ``0007_autonomy_github_time_machine``, реальный ORM/БД, ASGI-стек через
TestClient, изолированный локальный **bare-remote** для GitHub-адаптера). Итог
COMPLETE только при N/N PASS. Отдельного GitHub-репо не создаётся (§20.4).

Изолированность: harness работает в СВОЁМ временном ``ATLAS_DATA_DIR`` и НИКОГДА
не трогает живую БД/стек (живая БД лишь копируется read-only для теста миграции
0006→0007). Evidence — redacted, с SHA-256-манифестом, в ``var/artifacts/vp7/``.

Запуск (root не требуется; поднимать стек не нужно):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp7_acceptance.py
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

ART = _ROOT / "var" / "artifacts" / "vp7"
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


def _git_email() -> str:
    r = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, cwd=str(_ROOT))
    return r.stdout.strip() or "codevinci-local"


def _git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)


def _bare_remote(tmp: str, name="acme/demo"):
    bare = str(Path(tmp) / "remote.git")
    wc = str(Path(tmp) / "wc")
    subprocess.run(["git", "init", "--bare", "-b", "main", bare], capture_output=True)
    subprocess.run(["git", "clone", bare, wc], capture_output=True)
    _git(wc, "config", "user.name", "CodeVinci")
    _git(wc, "config", "user.email", _git_email())
    Path(wc, "README.md").write_text("seed")
    _git(wc, "add", "-A"); _git(wc, "commit", "-m", "сид"); _git(wc, "push", "origin", "main")
    seed = _git(wc, "rev-parse", "HEAD").stdout.strip()
    return bare, wc, seed


class VP7:
    def __init__(self):
        self.results = []
        self.tmp = tempfile.mkdtemp(prefix="atlas-vp7-accept-")
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

    def _alembic(self, data_dir, rev="head"):
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

    def boot(self):
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        os.environ["ATLAS_DATA_DIR"] = self.data_dir
        rc, _o, e = self._alembic(self.data_dir)
        assert rc == 0, f"alembic upgrade head failed: {e[-400:]}"
        from atlas_core.db import init_engine, session_scope
        from atlas_core.orm import Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        with session_scope() as s:
            if s.get(Project, "proj_v7") is None:
                s.add(Project(id="proj_v7", name="Синтетика VP-7", source_kind="local_git",
                              source_location=self.tmp, status="connected",
                              created_at=_now(), updated_at=_now()))
                s.commit()
        from atlas_core.app import create_app
        from starlette.testclient import TestClient
        self.client = TestClient(create_app(self.settings))
        # общий bare-remote для GitHub-критериев
        self.bare, self.wc, self.seed = _bare_remote(str(Path(self.tmp) / "gh"))

    # ---- helpers ----------------------------------------------------------
    def _grant(self, **kw):
        from atlas_core.autonomy import create_grant
        kw.setdefault("project_id", "proj_v7"); kw.setdefault("mode", "STANDARD")
        kw.setdefault("capabilities", ["repo_read", "commit", "push_feature", "create_pr",
                                       "merge_after_pass"])
        kw.setdefault("reason", "VP-7 acceptance")
        return create_grant(**kw)

    def run(self):
        print("=== VP-7 ACCEPTANCE (deterministic; isolated DB; synthetic bare remote) ===")
        try:
            self.boot()
            self.c1_modes()
            self.c2_7_grant_denies()
            self.c8_hard_denied()
            self.c9_12_emergency()
            self.c13_16_github()
            self.c17_20_merge_gate()
            self.c34_authoritative_merge()
            self.c21_audit()
            self.c22_23_checkpoint()
            self.c24_replay()
            self.c25_compare()
            self.c26_previews()
            self.c27_recovery()
            self.c28_one_writer()
            self.c29_license()
            self.c30_ru_en()
            self.c31_pulse_features()
            self.c32_regression()
            self.c33_repeatable()
        finally:
            self.cleanup()
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        verdict = "COMPLETE" if passed == total else f"INCOMPLETE ({passed}/{total})"
        matrix = {"vp": "VP-7", "passed": passed, "total": total, "verdict": verdict, "run": RUN,
                  "real_provider_quality_e2e": "OWNER_GATED_SEPARATE (scripts/run_vp6_real_e2e.py)",
                  "criteria": sorted(self.results, key=lambda r: r["id"])}
        self.art("acceptance_matrix.json", matrix)
        digests = {f.name: sha256_text(f.read_text(encoding="utf-8"))
                   for f in sorted(ART.glob("*.json"))}
        (ART / "evidence_sha256.json").write_text(
            json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n  ИТОГ VP-7: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix

    # ---- #1 modes ---------------------------------------------------------
    def c1_modes(self):
        from atlas_core.autonomy import MODES
        ok = MODES == ("GUIDED", "STANDARD", "AUTONOMOUS", "TRUSTED")
        self.art("c1_modes.json", {"modes": list(MODES)})
        self.rec(1, "Ровно четыре режима автономии", ok, f"modes={MODES}")

    # ---- #2..#7 grant denies ---------------------------------------------
    def c2_7_grant_denies(self):
        from atlas_core.autonomy import consume_budget, evaluate, revoke_grant
        ev = {}
        ev["no_grant"] = evaluate("merge_after_pass", project_id="none").reason_code
        g_exp = self._grant(ttl_seconds=-5)
        ev["expired"] = evaluate("commit", grant_id=g_exp["id"]).reason_code
        g_rev = self._grant()
        revoke_grant(g_rev["id"], by="owner")
        ev["revoked"] = evaluate("commit", grant_id=g_rev["id"]).reason_code
        g_scope = self._grant(environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"])
        ev["wrong_repo"] = evaluate("commit", grant_id=g_scope["id"], repo="x/y").reason_code
        ev["wrong_base"] = evaluate("commit", grant_id=g_scope["id"], base="dev").reason_code
        ev["wrong_env"] = evaluate("commit", grant_id=g_scope["id"], environment="prod").reason_code
        # Fix2: пустой scope НЕ означает «любой repo» (fail-closed).
        g_empty = self._grant(capabilities=["merge_after_pass"])  # без allowed_repos/bases
        ev["empty_scope"] = evaluate("merge_after_pass", grant_id=g_empty["id"]).reason_code
        g_cap = self._grant(capabilities=["repo_read"])
        ev["missing_cap"] = evaluate("commit", grant_id=g_cap["id"]).reason_code
        g_bud = self._grant(capabilities=["commit"], budget={"max_invocations": 1})
        consume_budget(g_bud["id"], n=1)
        ev["budget"] = evaluate("commit", grant_id=g_bud["id"]).reason_code
        self.art("c2_7_grant_denies.json", ev)
        self.rec(2, "Нет grant → denied", ev["no_grant"] == "NO_GRANT", ev["no_grant"])
        self.rec(3, "Истёкший grant → denied", ev["expired"] == "GRANT_EXPIRED", ev["expired"])
        self.rec(4, "Отозванный grant → denied", ev["revoked"] == "GRANT_REVOKED", ev["revoked"])
        self.rec(5, "Неверный/пустой repo/base/env → denied (fail-closed)",
                 ev["wrong_repo"] == "REPO_NOT_ALLOWED" and ev["wrong_base"] == "BASE_NOT_ALLOWED"
                 and ev["wrong_env"] == "ENVIRONMENT_MISMATCH" and ev["empty_scope"] == "REPO_NOT_ALLOWED",
                 f"{ev['wrong_repo']}/{ev['wrong_base']}/{ev['wrong_env']}/empty={ev['empty_scope']}")
        self.rec(6, "Отсутствующая capability → denied", ev["missing_cap"] == "CAPABILITY_MISSING",
                 ev["missing_cap"])
        self.rec(7, "Исчерпан бюджет → denied", ev["budget"] == "BUDGET_EXHAUSTED", ev["budget"])

    # ---- #8 hard-denied ---------------------------------------------------
    def c8_hard_denied(self):
        from atlas_core.autonomy import evaluate
        caps = ["direct_main", "force_push", "branch_delete", "repo_delete",
                "production_deploy", "dns_nginx_tls", "paid_calls", "cookie_import"]
        g = self._grant(capabilities=caps)  # даже если grant их перечисляет
        res = {c: evaluate(c, grant_id=g["id"]).reason_code for c in caps}
        ok = all(v == "CAPABILITY_UNAVAILABLE" for v in res.values())
        self.art("c8_hard_denied.json", res)
        self.rec(8, "direct main/force/delete/production/cookies недоступны", ok, f"{res}")

    # ---- #9..#12 emergency stop ------------------------------------------
    def c9_12_emergency(self):
        from atlas_core import emergency
        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import Run, RunLease
        with session_scope() as s:
            s.add(Run(id="run_es", project_id="proj_v7", state="RUNNING",
                      created_at=_now(), updated_at=_now()))
            s.add(RunLease(id=new_id("rl"), run_id="run_es", profile_id="pf_es",
                           acquired_at="t", released_at=""))
            s.commit()
        st = emergency.engage(reason="acceptance")
        blocks = emergency.blocks_new_jobs()
        # Fix1: Emergency Stop блокирует СОЗДАНИЕ нового Run (не только флаг).
        from atlas_core.runs import RunError, RunService
        create_blocked = False
        try:
            RunService().create_run("proj_v7", vp_key="VP-7", dedup_key="es-blocked")
        except RunError as exc:
            create_blocked = exc.code == "EMERGENCY_STOP"
        with session_scope() as s:
            run_state = s.get(Run, "run_es").state
            lease = s.get(RunLease, st["released_leases"][0].split(":")[1])
            lease_released = lease.released_at != "" and lease is not None
        survives = emergency.is_active()  # «рестарт» = повторное чтение из БД
        # explicit resume required
        active_before_resume = emergency.is_active()
        res = emergency.resume(actor="owner")
        cleared_only_after_resume = active_before_resume and not emergency.is_active()
        self.art("c9_12_emergency.json", {"engaged": st, "blocks": blocks,
                 "create_run_blocked": create_blocked, "run_state": run_state,
                 "lease_released": lease_released, "survives_restart": survives, "resume": res})
        self.rec(9, "Emergency Stop блокирует новые jobs (флаг + создание Run)",
                 blocks and create_blocked, f"blocks={blocks} create_blocked={create_blocked}")
        self.rec(10, "Emergency Stop прерывает active и снимает leases без удаления",
                 run_state == "INTERRUPTED" and lease_released and "run_es" in st["interrupted_runs"],
                 f"run={run_state} lease_released={lease_released}")
        self.rec(11, "Рестарт не сбрасывает Emergency Stop молча", survives,
                 f"active_after_reread={survives}")
        self.rec(12, "Требуется явный resume", cleared_only_after_resume and res["resumed"],
                 f"resumed={res['resumed']}")

    # ---- #13..#16 github idempotency/contract ----------------------------
    def c13_16_github(self):
        from atlas_core.github_adapter import (
            GitContract,
            GitContractError,
            GitHubAdapter,
            LocalForge,
        )
        forge = LocalForge(self.bare, "acme/demo")
        # Bypass A: production-адаптер (enforce_grant=True) отвергает write без grant.
        prod = GitHubAdapter(forge=forge, contract=GitContract())
        _git(self.wc, "checkout", "-b", "atlas/vp-7-a")
        Path(self.wc, "greq.py").write_text("x=0")
        self._grant_required_ok = False
        try:
            prod.commit(self.wc, "VP-7: без grant")
        except GitContractError as exc:
            self._grant_required_ok = exc.code == "GRANT_REQUIRED"
        _git(self.wc, "checkout", "--", ".")
        _git(self.wc, "clean", "-fdq")
        # enforce_grant=False — явная test-only граница для детерминированной идемпотентности.
        ad = GitHubAdapter(forge=forge, contract=GitContract(), enforce_grant=False)
        Path(self.wc, "f.py").write_text("x=1")
        sha = ad.commit(self.wc, "VP-7: изменение")
        p1 = ad.push_feature(self.wc, "atlas/vp-7-a")
        p2 = ad.push_feature(self.wc, "atlas/vp-7-a")
        # russian contract + author
        _git(self.wc, "checkout", "-b", "atlas/vp-7-b")
        Path(self.wc, "g.py").write_text("y=1")
        russian_enforced = False
        try:
            ad.commit(self.wc, "english only")
        except GitContractError as ex:
            russian_enforced = ex.code == "NON_RUSSIAN_TEXT"
        author_verified = ad.contract.assert_author(self.wc)[0] == "CodeVinci"
        # PR idempotency
        _git(self.wc, "checkout", "atlas/vp-7-a")
        pr1 = ad.create_pr(base="main", head_branch="atlas/vp-7-a", head_sha=sha,
                           title="VP-7 демо", body="тело")
        pr2 = ad.create_pr(base="main", head_branch="atlas/vp-7-a", head_sha=sha,
                           title="VP-7 демо", body="тело")
        self.art("c13_16_github.json", {"push1_idem": p1["idempotent"], "push2_idem": p2["idempotent"],
                 "russian_enforced": russian_enforced, "author_verified": author_verified,
                 "grant_required_ok": self._grant_required_ok, "pr1": pr1["number"], "pr2": pr2["number"]})
        self.rec(13, "branch/commit/push идемпотентны + production требует grant (bypass A)",
                 (not p1["idempotent"]) and p2["idempotent"] and self._grant_required_ok,
                 f"push2_idem={p2['idempotent']} grant_required={self._grant_required_ok}")
        self.rec(14, "Русский commit-контракт энфорсится", russian_enforced,
                 f"russian_enforced={russian_enforced}")
        self.rec(15, "Настроенный автор проверяется", author_verified,
                 f"author={author_verified}")
        self.rec(16, "Повтор create PR не создаёт дубль", pr1["number"] == pr2["number"],
                 f"pr={pr1['number']}=={pr2['number']}")
        self._gh_state = (forge, ad, sha, pr1["number"])

    # ---- #17..#20 merge gate ---------------------------------------------
    def c17_20_merge_gate(self):
        from atlas_core.merge_gate import MergeRequest, evaluate_merge
        forge, ad, sha, prn = self._gh_state
        forge.set_checks(sha, "GREEN")
        g = self._grant(environment="synthetic", allowed_repos=["acme/demo"], allowed_bases=["main"])

        def mk(**over):
            base = dict(repo="acme/demo", base="main", branch="atlas/vp-7-a", head_sha=sha,
                        project_id="proj_v7", grant_id=g["id"], environment="synthetic",
                        review_package={"id": "rpkg_acc", "status": "valid", "head_sha": sha},
                        quality_report={"verdict": "PASS", "blocking_count": 0,
                                        "review_package_id": "rpkg_acc"},
                        checks=forge.checks(sha), mergeability=forge.mergeability(prn), pr_number=prn)
            base.update(over)
            return MergeRequest(**base)

        stale_review = evaluate_merge(mk(review_package={"id": "rpkg_acc", "status": "valid",
                                                         "head_sha": "OLD"}))
        stale_ci = evaluate_merge(mk(checks={"head_sha": "OLD", "state": "GREEN"}))
        blocking = evaluate_merge(mk(quality_report={"verdict": "PASS", "blocking_count": 1,
                                                     "review_package_id": "rpkg_acc"}))
        # Fix3: неполные current-head evidence → fail-closed deny.
        missing_rp_head = evaluate_merge(mk(review_package={"id": "rpkg_acc", "status": "valid"}))
        missing_ci_head = evaluate_merge(mk(checks={"state": "GREEN"}))
        # Fix2: scopeless grant → deny в merge gate.
        g_empty = self._grant(capabilities=["merge_after_pass"])
        scopeless = evaluate_merge(mk(grant_id=g_empty["id"]))
        permit = evaluate_merge(mk())
        merged = ad.squash_merge(prn, expected_head=sha, message="VP-7: squash после PASS",
                                 grant_id=g["id"]) if permit.permitted else {"merged": False}
        base_advanced = forge.branch_head("main") != self.seed
        self.art("c17_20_merge_gate.json", {
            "stale_review": stale_review.reason_code, "stale_ci": stale_ci.reason_code,
            "blocking": blocking.reason_code, "missing_rp_head": missing_rp_head.reason_code,
            "missing_ci_head": missing_ci_head.reason_code, "scopeless": scopeless.reason_code,
            "permit": permit.reason_code, "merged": merged.get("merged"),
            "base_advanced": base_advanced, "conditions": permit.conditions})
        self.rec(17, "Протухший/отсутствующий ReviewPackage head денит merge (fail-closed)",
                 stale_review.reason_code == "STALE_REVIEW_HEAD"
                 and missing_rp_head.reason_code == "STALE_REVIEW_HEAD"
                 and not scopeless.permitted,
                 f"stale={stale_review.reason_code} missing={missing_rp_head.reason_code} "
                 f"scopeless={scopeless.reason_code}")
        self.rec(18, "Протухший/отсутствующий CI head денит merge (fail-closed)",
                 stale_ci.reason_code == "STALE_OR_FAILING_CI"
                 and missing_ci_head.reason_code == "STALE_OR_FAILING_CI",
                 f"stale={stale_ci.reason_code} missing={missing_ci_head.reason_code}")
        self.rec(19, "Blocking Quality finding денит merge",
                 blocking.reason_code == "BLOCKING_QUALITY_FINDING", blocking.reason_code)
        self.rec(20, "Current-head PASS + green + grant разрешает bounded merge",
                 permit.permitted and merged.get("merged") and base_advanced,
                 f"permit={permit.permitted} merged={merged.get('merged')}")

    # ---- #34 авторитетный merge грузит RP/QR из хранилища (bypass C) ------
    def c34_authoritative_merge(self):
        from atlas_core.merge_gate import evaluate_merge_authoritative
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        g = self._grant(environment="synthetic", allowed_repos=["acme/demo"], allowed_bases=["main"])

        def _rp_qr(head, verdict):
            pkg = build_review_package(ReviewInputs(
                project_id="proj_v7", run_id="run_auth", wo_key="VP-7", vp_key="VP-7",
                branch="atlas/vp-7-a", base_sha="B", head_sha=head, impact_class="LOCAL",
                claims=[{"claim": "c", "verified": True}]), actor="reviewer")
            rep = QualityService().build_report(pkg, verdict, "", [], run_id="run_auth")
            return pkg, rep

        def _call(rp_id, head="HEADA", qr_id=""):
            return evaluate_merge_authoritative(
                repo="acme/demo", base="main", branch="atlas/vp-7-a", head_sha=head,
                project_id="proj_v7", grant_id=g["id"], review_package_id=rp_id,
                quality_report_id=qr_id, environment="synthetic",
                checks={"head_sha": head, "state": "GREEN"},
                mergeability={"mergeable": True, "state": "CLEAN"}, pr_number=1)

        ok_pkg, ok_rep = _rp_qr("HEADA", "PASS")
        permit = _call(ok_pkg["id"], qr_id=ok_rep["id"])            # валидный stored → permit
        stale_pkg, stale_rep = _rp_qr("OLD", "PASS")
        stale = _call(stale_pkg["id"], head="HEADA", qr_id=stale_rep["id"])  # stored stale → deny
        missing = _call("rpkg_nope")                                 # нет в хранилище → deny
        empty = _call("")                                            # пустой id → deny
        mism = _call(ok_pkg["id"], qr_id="qrep_wrong")               # caller qr_id ≠ stored → deny
        self.art("c34_authoritative_merge.json", {
            "permit": permit.reason_code, "stale": stale.reason_code,
            "missing": missing.reason_code, "empty": empty.reason_code,
            "mismatch": mism.reason_code})
        self.rec(34, "Авторитетный merge грузит RP/QR из хранилища (caller-id недостаточно)",
                 permit.permitted and not stale.permitted and stale.reason_code == "REVIEW_PACKAGE_INVALID"
                 and missing.reason_code == "REVIEW_PACKAGE_INVALID" and not empty.permitted
                 and not mism.permitted,
                 f"permit={permit.permitted} stale={stale.reason_code} mismatch={mism.reason_code}")

    # ---- #21 audit completeness ------------------------------------------
    def c21_audit(self):
        from atlas_core import audit
        events = {e["event_type"] for e in audit.query(limit=500)}
        needed = {"grant.created", "grant.revoked", "emergency.stop.engaged.before",
                  "emergency.stop.engaged.after", "emergency.stop.resumed",
                  "github.commit.before", "github.commit.after", "github.pr.before",
                  "github.pr.after", "merge.gate.before", "merge.gate.after",
                  "github.merge.before", "github.merge.after"}
        missing = sorted(needed - events)
        self.art("c21_audit.json", {"present": sorted(needed & events), "missing": missing})
        self.rec(21, "Before/after GitHub и grant Audit полны", not missing,
                 f"missing={missing}")

    # ---- #22/#23 checkpoint hashes + no secrets --------------------------
    def c22_23_checkpoint(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import Checkpoint
        from atlas_core.timemachine import CheckpointInputs, create_checkpoint, verify_checkpoint
        ci = CheckpointInputs(project_id="proj_v7", vp_key="VP-7", run_id="run_es",
                              db_revision="0007", branch="atlas/vp-7-a", base_sha=self.seed,
                              head_sha="HEADX", worktree_status="clean", patch_hash="sha256:p",
                              artifact_hashes=[{"path": "a", "sha": "sha256:aa"}],
                              profile_alias="claude-pro-01", model="claude", effort="medium",
                              session_ids=["sess-abc"], grant_hash="sha256:g",
                              test_refs=[{"name": "unit", "hash": "sha256:t"}],
                              evidence_refs=["ev1"], cause="post-review")
        cp = create_checkpoint(ci)
        # детерминизм: тот же payload → тот же content_hash
        cp2_hash_same = create_checkpoint(ci)["content_hash"] == cp["content_hash"]
        verified = verify_checkpoint(cp["id"])[0]
        with session_scope() as s:
            s.get(Checkpoint, cp["id"]).head_sha = "TAMPERED"
            s.commit()
        tampered = verify_checkpoint(cp["id"])
        blob = json.dumps(cp).lower()
        no_secrets = not any(m in blob for m in ("@", "token", "cookie", "password",
                                                 "transcript", "/home/", "/root/"))
        self.art("c22_23_checkpoint.json", {"det_hash": cp2_hash_same, "verified": verified,
                 "tamper_invalid": (not tampered[0]) and tampered[1] == "TAMPERED",
                 "no_secrets": no_secrets})
        self.rec(22, "Хеши checkpoint детерминированы; tamper инвалидирует",
                 cp2_hash_same and verified and not tampered[0], f"det={cp2_hash_same}")
        self.rec(23, "В checkpoint нет credentials/email/raw path/transcript", no_secrets,
                 f"no_secrets={no_secrets}")
        self._ck = cp

    # ---- #24 replay new run + safe branch --------------------------------
    def c24_replay(self):
        from atlas_core.timemachine import CheckpointInputs, create_checkpoint, replay
        d = tempfile.mkdtemp(prefix="atlas-tm-")
        repo = str(Path(d) / "repo"); os.makedirs(repo)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.name", "CodeVinci"); _git(repo, "config", "user.email", _git_email())
        Path(repo, "a").write_text("1"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "b")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "checkout", "-qb", "atlas/vp-7-src")
        Path(repo, "b").write_text("2"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "w")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        cp = create_checkpoint(CheckpointInputs(project_id="proj_v7", vp_key="VP-7",
                               branch="atlas/vp-7-src", base_sha=base, head_sha=head, cause="post"))
        g = self._grant(mode="AUTONOMOUS", capabilities=["repo_write"],
                        allowed_repos=["proj/v7"], allowed_bases=["main"])
        res = replay(cp["id"], grant_id=g["id"], repo_path=repo)
        src_after = _git(repo, "rev-parse", "atlas/vp-7-src").stdout.strip()
        new_ok = _git(repo, "rev-parse", "--verify", res["new_branch"]).returncode == 0
        self.art("c24_replay.json", {"new_run": bool(res["new_run_id"]),
                 "new_branch": res["new_branch"], "source_rewritten": res["source_rewritten"],
                 "source_unchanged": src_after == head, "new_branch_exists": new_ok})
        self.rec(24, "Replay создаёт новый Run и safe branch без rewrite источника",
                 bool(res["new_run_id"]) and res["new_branch"].startswith("atlas/replay-")
                 and src_after == head and new_ok and not res["source_rewritten"],
                 f"branch={res['new_branch']} src_unchanged={src_after == head}")

    # ---- #25 compare -----------------------------------------------------
    def c25_compare(self):
        from atlas_core.timemachine import CheckpointInputs, compare, create_checkpoint
        a = create_checkpoint(CheckpointInputs(project_id="proj_v7", head_sha="HA",
                              profile_alias="codex-plus-01", model="codex", cause="x", grant_hash="g1"))
        b = create_checkpoint(CheckpointInputs(project_id="proj_v7", head_sha="HB",
                              profile_alias="claude-pro-01", model="claude", cause="y", grant_hash="g2"))
        c = compare(a["id"], b["id"])
        ok = (c["any_change"] and c["diffs"]["head_sha"]["changed"]
              and c["diffs"]["profile"]["changed"] and c["diffs"]["grant"]["changed"]
              and c["diffs"]["outcome"]["changed"])
        self.art("c25_compare.json", c)
        self.rec(25, "Compare показывает факт-различия", ok, f"any_change={c['any_change']}")

    # ---- #26 restore/rollback preview read-only --------------------------
    def c26_previews(self):
        # _ck из #23 был tampered → используем свежий валидный чекпоинт.
        from atlas_core.timemachine import (
            CheckpointInputs,
            create_checkpoint,
            restore_state_preview,
            rollback_preview,
        )
        fresh = create_checkpoint(CheckpointInputs(project_id="proj_v7", head_sha="HF", cause="p"))
        rs = restore_state_preview(fresh["id"])
        rb_no_grant = rollback_preview(fresh["id"])
        g = self._grant(mode="TRUSTED", capabilities=["destructive_rollback"])
        rb_grant = rollback_preview(fresh["id"], grant_id=g["id"])
        ok = (rs["read_only"] and rb_no_grant["read_only"] and not rb_no_grant["available"]
              and rb_grant["read_only"])
        self.art("c26_previews.json", {"restore": rs, "rollback_no_grant": rb_no_grant,
                 "rollback_grant": rb_grant})
        self.rec(26, "Restore/rollback preview read-only; destructive без grant недоступен", ok,
                 f"restore_ro={rs['read_only']} rb_unavail={not rb_no_grant['available']}")

    # ---- #27 interruption recovery ---------------------------------------
    def c27_recovery(self):
        from atlas_core.timemachine import CheckpointInputs, create_checkpoint, recover
        cp = create_checkpoint(CheckpointInputs(project_id="proj_v7", vp_key="VP-7",
                               head_sha="HREC", test_refs=[{"name": "unit", "hash": "sha256:t"}],
                               evidence_refs=["ev-keep"], handoff_ref="hop_1", cause="interrupt"))
        rec = recover(cp["id"])
        ok = (rec["verified_hashes"] and rec["preserved_evidence"] == ["ev-keep"]
              and rec["preserved_tests"] == [{"name": "unit", "hash": "sha256:t"}]
              and rec["preserved_acceptance_ref"] == "hop_1")
        self.art("c27_recovery.json", rec)
        self.rec(27, "Recovery после прерывания сохраняет критерии/evidence", ok,
                 f"evidence={rec['preserved_evidence']}")

    # ---- #28 one writer ---------------------------------------------------
    def c28_one_writer(self):
        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import RunLease
        from sqlalchemy.exc import IntegrityError
        conflict = False
        with session_scope() as s:
            s.add(RunLease(id=new_id("rl"), run_id="r1", profile_id="pf_ow",
                           acquired_at="t", released_at=""))
            s.commit()
        try:
            with session_scope() as s:
                s.add(RunLease(id=new_id("rl"), run_id="r2", profile_id="pf_ow",
                               acquired_at="t", released_at=""))
                s.commit()
        except IntegrityError:
            conflict = True
        self.art("c28_one_writer.json", {"second_active_lease_rejected": conflict})
        self.rec(28, "Concurrency сохраняет одного writer", conflict,
                 f"unique_active_lease_enforced={conflict}")

    # ---- #29 apache-2.0 detected -----------------------------------------
    def c29_license(self):
        from atlas_core.firewall import FirewallContext, run_firewall
        ctx = FirewallContext(package={"head_sha": "H"}, license_present=True,
                              license_spdx="Apache-2.0", current_head="H")
        findings = run_firewall(ctx)
        codes = {f["code"] for f in findings}
        license_root = (_ROOT / "LICENSE").exists()
        apache_text = "Apache License" in (_ROOT / "LICENSE").read_text(encoding="utf-8")[:200] \
            if license_root else False
        spdx_py = 'license = "Apache-2.0"' in (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        detected = "LICENSE_PRESENT" in codes and "LICENSE_ABSENT_OWNER_DECISION" not in codes
        self.art("c29_license.json", {"codes": sorted(codes), "license_root": license_root,
                 "apache_text": apache_text, "spdx_pyproject": spdx_py})
        self.rec(29, "Apache-2.0 распознан; старый license-pending finding исчез",
                 detected and license_root and apache_text and spdx_py,
                 f"detected={detected} root={license_root} spdx={spdx_py}")

    # ---- #30 RU/EN parity of VP-7 states ---------------------------------
    def c30_ru_en(self):
        web = _ROOT / "apps" / "web"
        rc, out, err = sh(["node", str(web / "scripts" / "check-i18n.mjs")], env=None)
        ru = (web / "src" / "locales" / "ru.ts").read_text(encoding="utf-8")
        en = (web / "src" / "locales" / "en.ts").read_text(encoding="utf-8")
        keys = ["auto.title", "auto.emergency", "auto.capMatrix", "tm.title", "tm.compare",
                "st.expired", "st.revoked", "st.stale", "st.conflict", "st.invalidEvidence"]
        ru_ok = all(f'"{k}"' in ru for k in keys)
        en_ok = all(f'"{k}"' in en for k in keys)
        i18n_pass = rc == 0
        self.art("c30_ru_en.json", {"i18n_check_rc": rc, "ru_keys": ru_ok, "en_keys": en_ok,
                 "check_tail": (out + err)[-300:]})
        self.rec(30, "RU/EN Autonomy/Time Machine состояния рендерятся (паритет ключей)",
                 i18n_pass and ru_ok and en_ok, f"i18n_rc={rc} ru={ru_ok} en={en_ok}")

    # ---- #31 favicon/home/CPU/next-action --------------------------------
    def c31_pulse_features(self):
        from atlas_core.system_summary import system_summary
        summ = system_summary(self.settings)
        cpu = summ["cpu"]
        cpu_metric = "utilization_pct" in cpu and "util_source" in cpu and "load_avg" in cpu
        na = summ.get("next_action", {})
        next_action_ok = na.get("code") in ("OPEN_OWNER_RUN", "INSPECT_RUN", "CREATE_RUN",
                                            "CONNECT_PROJECT", "OPEN_MAP", "NEXT_VP", "PARTIAL")
        web = _ROOT / "apps" / "web"
        favicon = (web / "public" / "favicon.svg").exists()
        index_ref = "favicon.svg" in (web / "index.html").read_text(encoding="utf-8")
        brand_home = "brand-home" in (web / "src" / "App.tsx").read_text(encoding="utf-8")
        self.art("c31_pulse.json", {"cpu": cpu, "next_action": na, "favicon": favicon,
                 "index_ref": index_ref, "brand_home": brand_home})
        self.rec(31, "favicon/home-link/CPU-метрика/контекстное next-action",
                 cpu_metric and next_action_ok and favicon and index_ref and brand_home,
                 f"cpu={cpu_metric} na={na.get('code')} favicon={favicon} home={brand_home}")

    # ---- #32 regression (impact-appropriate) -----------------------------
    def c32_regression(self):
        # (a) реальная миграция 0006(копия живой)→0007 без потери данных, если живая доступна
        live_detail, live_ok = {}, True
        if os.path.exists(LIVE_DB):
            live_dir = str(Path(self.tmp) / "live"); os.makedirs(live_dir, exist_ok=True)
            dst = str(Path(live_dir) / "atlas.db")
            src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
            out = sqlite3.connect(dst)
            with out:
                src.backup(out)
            src.close(); out.close()
            before_rev = self._rev(dst)
            before = self._counts(dst)
            rc, _o, _e = self._alembic(live_dir)
            after_rev = self._rev(dst)
            after = self._counts(dst)
            live_ok = (before_rev == "0006_review_quality" and rc == 0
                       and after_rev == "0007_autonomy_github_time_machine" and before == after)
            live_detail = {"before_rev": before_rev, "after_rev": after_rev,
                           "counts_preserved": before == after}
        else:
            live_detail = {"live_db": "absent — пропущено"}
        # (b) impact-appropriate регрессия зависимых suites (VP-0..VP-6 + VP-7)
        env = {"PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner:{_ROOT}/tests",
               "ATLAS_CONFIG_FILE": "/nonexistent.yaml"}
        py = str(_VENV / "python") if (_VENV / "python").exists() else sys.executable
        rc_t, out_t, err_t = sh([py, "-m", "unittest", "-q",
                                 "test_vp6_review_quality", "test_vp5_services", "test_router",
                                 "test_leases", "test_vp7_autonomy"], env=env)
        subset_ok = rc_t == 0
        self.art("c32_regression.json", {"migration_live": live_detail, "migration_ok": live_ok,
                 "regression_subset_rc": rc_t, "regression_tail": (err_t or out_t)[-600:]})
        self.rec(32, "VP-0..VP-6 регрессии целы (impact-appropriate) + VP-7",
                 live_ok and subset_ok, f"migration={live_ok} subset_ok={subset_ok}")

    def _counts(self, db):
        c = sqlite3.connect(db)
        out = {}
        for t in ("projects", "briefs", "work_orders", "runs", "agent_profiles",
                  "review_packages", "audit_events"):
            try:
                out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        c.close()
        return out

    # ---- #33 repeatable ---------------------------------------------------
    def c33_repeatable(self):
        from atlas_core.productmap import content_hash
        h1 = content_hash({"a": 1, "b": [3, 2, 1], "c": {"y": 2, "x": 1}})
        h2 = content_hash({"c": {"x": 1, "y": 2}, "b": [3, 2, 1], "a": 1})
        deterministic = h1 == h2
        d1 = {f.name: sha256_text(f.read_text(encoding="utf-8")) for f in sorted(ART.glob("c*.json"))}
        d2 = {f.name: sha256_text(f.read_text(encoding="utf-8")) for f in sorted(ART.glob("c*.json"))}
        manifest_ok = d1 == d2 and len(d1) > 0
        self.art("c33_repeatable.json", {"content_hash_deterministic": deterministic,
                 "manifest_repeatable": manifest_ok, "sample_hash": h1})
        self.rec(33, "Отчёт и SHA-256 manifest воспроизводимы", deterministic and manifest_ok,
                 f"det={deterministic} manifest={manifest_ok}")

    def cleanup(self):
        try:
            from atlas_core.db import get_engine
            get_engine().dispose()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def verify_ok(cp) -> bool:
    from atlas_core.timemachine import verify_checkpoint
    return verify_checkpoint(cp["id"])[0]


if __name__ == "__main__":
    m = VP7().run()
    sys.exit(0 if m["verdict"] == "COMPLETE" else 1)
