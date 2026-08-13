"""VP-7 Autonomy, GitHub & Time Machine (Master Spec §19, §20, §21).

Юнит-тесты против РЕАЛЬНЫХ модулей: fail-closed оценка grant, Emergency Stop
(блокировка/прерывание/release без удаления, переживание рестарта), GitHub
git-контракт + идемпотентность + LocalForge merge, STANDARD merge gate
(current-head/stale деним), Time Machine checkpoints/tamper/replay/compare/preview,
read-only auth-health. Всё в изолированном ATLAS_DATA_DIR."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from atlas_test_base import AtlasTestCase


class VP7Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())

    def _register_evidence(self, head_sha, *, refs=("ev:accept", "ev:chrome")):
        """Записать РЕАЛЬНЫЕ evidence-файлы в изолированный data_dir и durable-
        зарегистрировать их под ``head_sha``. Возвращает ``(paths, evidence_refs,
        artifact_hashes)`` для сборки evidence-backed ReviewPackage."""
        from atlas_core.reviewpkg import register_merge_evidence
        evdir = self.data_dir / "evidence"
        evdir.mkdir(exist_ok=True)
        entries, paths = [], []
        for ref in refs:
            p = evdir / (ref.replace(":", "_") + f"_{head_sha}.txt")
            p.write_text(f"evidence {ref} для head {head_sha}\n", encoding="utf-8")
            entries.append({"ref": ref, "path": str(p), "kind": "artifact"})
            paths.append(str(p.resolve()))
        rows = register_merge_evidence(head_sha, entries)
        arts = [{"path": r["path"], "sha": r["sha256"]} for r in rows]
        return paths, list(refs), arts


# ---------------------------------------------------------------------------
class TestAutonomyGrants(VP7Base):
    def _grant(self, **kw):
        from atlas_core.autonomy import create_grant
        kw.setdefault("project_id", "p")
        kw.setdefault("mode", "STANDARD")
        kw.setdefault("capabilities", ["repo_read", "commit", "merge_after_pass"])
        kw.setdefault("reason", "test")
        return create_grant(**kw)

    def test_exactly_four_modes(self):
        from atlas_core.autonomy import MODES
        self.assertEqual(MODES, ("GUIDED", "STANDARD", "AUTONOMOUS", "TRUSTED"))

    def test_no_grant_denies(self):
        from atlas_core.autonomy import evaluate
        self.assertEqual(evaluate("merge_after_pass", project_id="p").reason_code, "NO_GRANT")

    def test_expired_denies(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(ttl_seconds=-5)
        self.assertEqual(evaluate("commit", grant_id=g["id"]).reason_code, "GRANT_EXPIRED")

    def test_revoked_denies(self):
        from atlas_core.autonomy import evaluate, revoke_grant
        g = self._grant()
        revoke_grant(g["id"], by="owner")
        self.assertEqual(evaluate("commit", grant_id=g["id"]).reason_code, "GRANT_REVOKED")

    def test_wrong_repo_base_env_denies(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"])
        self.assertEqual(evaluate("commit", grant_id=g["id"], repo="x/y").reason_code, "REPO_NOT_ALLOWED")
        self.assertEqual(evaluate("commit", grant_id=g["id"], base="dev").reason_code, "BASE_NOT_ALLOWED")
        self.assertEqual(evaluate("commit", grant_id=g["id"], environment="prod").reason_code,
                         "ENVIRONMENT_MISMATCH")

    def test_missing_capability_denies(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(capabilities=["repo_read"])
        self.assertEqual(evaluate("commit", grant_id=g["id"]).reason_code, "CAPABILITY_MISSING")

    def test_budget_exhaustion_denies(self):
        from atlas_core.autonomy import consume_budget, evaluate
        g = self._grant(capabilities=["commit"], budget={"max_invocations": 2})
        consume_budget(g["id"], n=2)
        self.assertEqual(evaluate("commit", grant_id=g["id"]).reason_code, "BUDGET_EXHAUSTED")

    def test_hard_denied_capabilities_unavailable(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(capabilities=["direct_main", "force_push", "branch_delete",
                                      "production_deploy", "cookie_import"])
        for cap in ("direct_main", "force_push", "branch_delete", "repo_delete",
                    "production_deploy", "dns_nginx_tls", "paid_calls", "cookie_import"):
            self.assertEqual(evaluate(cap, grant_id=g["id"]).reason_code, "CAPABILITY_UNAVAILABLE",
                             f"{cap} должна быть недоступна через автономию")

    def test_stale_version_conflict(self):
        from atlas_core.autonomy import evaluate
        g = self._grant()
        self.assertEqual(evaluate("commit", grant_id=g["id"], expected_version=999).reason_code,
                         "VERSION_CONFLICT")

    def test_active_grant_permits_in_scope(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"])
        d = evaluate("commit", grant_id=g["id"], repo="a/b", base="main", environment="synthetic")
        self.assertTrue(d.permitted)
        self.assertEqual(d.reason_code, "PERMITTED")

    # --- call-8 fix: ACTIVE grant с будущим starts_at ещё не действует ---
    def test_future_starts_at_denies_not_yet_active(self):
        from datetime import datetime, timedelta, timezone

        from atlas_core.autonomy import evaluate
        from atlas_core.db import session_scope
        from atlas_core.orm import Grant
        g = self._grant()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with session_scope() as s:
            row = s.get(Grant, g["id"])
            row.starts_at = datetime.fromisoformat(future)  # ACTIVE, но начнётся позже
            s.commit()
        d = evaluate("commit", grant_id=g["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "GRANT_NOT_YET_ACTIVE")  # временной scope соблюдён

    def test_past_starts_at_still_permits(self):
        from atlas_core.autonomy import evaluate
        g = self._grant()  # starts_at = now (в прошлом к моменту evaluate)
        self.assertTrue(evaluate("commit", grant_id=g["id"]).permitted)

    # --- call-10 fix (finding 1): consume_budget атомарно ре-валидирует grant ---
    def test_consume_budget_rejects_revoked_grant(self):
        from atlas_core.autonomy import ConflictError, consume_budget, revoke_grant
        g = self._grant()
        revoke_grant(g["id"], by="owner", reason="revoked mid-flight")
        with self.assertRaises(ConflictError):        # revoked → не списывается
            consume_budget(g["id"], n=1, expected_version=g["version"])

    def test_consume_budget_rejects_future_starts_at(self):
        from datetime import datetime, timedelta, timezone

        from atlas_core.autonomy import ConflictError, consume_budget
        from atlas_core.db import session_scope
        from atlas_core.orm import Grant
        g = self._grant()
        with session_scope() as s:
            row = s.get(Grant, g["id"])
            row.starts_at = datetime.now(timezone.utc) + timedelta(hours=1)
            s.commit()
            ver = row.version
        with self.assertRaises(ConflictError):        # ещё не активен → не списывается
            consume_budget(g["id"], n=1, expected_version=ver)

    # --- Fix2: пустой scope НЕ означает «любой repo» (fail-closed) ---
    def test_empty_scope_repo_capability_denies(self):
        from atlas_core.autonomy import evaluate
        # grant с merge_after_pass, но БЕЗ allowed_repos/bases → deny (fail-closed)
        g = self._grant(capabilities=["merge_after_pass"])
        self.assertEqual(evaluate("merge_after_pass", grant_id=g["id"]).reason_code, "REPO_NOT_ALLOWED")
        # даже если каллер указывает repo — пустой allowlist денит
        self.assertEqual(evaluate("push_feature", grant_id=self._grant(
            capabilities=["push_feature"])["id"], repo="a/b").reason_code, "REPO_NOT_ALLOWED")

    def test_empty_environment_denies_provided_env(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(capabilities=["commit"], allowed_repos=["a/b"], allowed_bases=["main"])
        # grant без environment → указанный environment должен быть отклонён
        self.assertEqual(evaluate("commit", grant_id=g["id"], environment="prod").reason_code,
                         "ENVIRONMENT_MISMATCH")

    # --- Fix2: явный grant не может авторизовать операцию другого проекта ---
    def test_explicit_grant_other_project_denies(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(project_id="projA", capabilities=["merge_after_pass"],
                        environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"])
        # совпадение repo/base/env НЕ компенсирует несовпадение проекта
        d = evaluate("merge_after_pass", grant_id=g["id"], project_id="projB",
                     repo="a/b", base="main", environment="synthetic")
        self.assertEqual(d.reason_code, "PROJECT_MISMATCH")

    def test_unbound_grant_denies_for_project_operation(self):
        from atlas_core.autonomy import create_grant, evaluate
        g = create_grant(project_id="", mode="STANDARD", capabilities=["commit"], reason="unbound")
        self.assertEqual(evaluate("commit", grant_id=g["id"], project_id="projB").reason_code,
                         "PROJECT_UNBOUND")

    def test_matching_project_permits(self):
        from atlas_core.autonomy import evaluate
        g = self._grant(project_id="projA", capabilities=["merge_after_pass"],
                        environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"])
        d = evaluate("merge_after_pass", grant_id=g["id"], project_id="projA",
                     repo="a/b", base="main", environment="synthetic")
        self.assertTrue(d.permitted)


# ---------------------------------------------------------------------------
class TestEmergencyStop(VP7Base):
    def test_blocks_interrupts_releases_no_delete(self):
        from datetime import datetime, timezone

        from atlas_core import emergency
        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import Run, RunLease
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Run(id="run_a", project_id="p", state="RUNNING", created_at=now, updated_at=now))
            s.add(RunLease(id=new_id("rl"), run_id="run_a", profile_id="pf1",
                           acquired_at="t", released_at=""))
            s.commit()
        st = emergency.engage(reason="test")
        self.assertTrue(emergency.is_active())
        self.assertTrue(emergency.blocks_new_jobs())
        self.assertIn("run_a", st["interrupted_runs"])
        self.assertEqual(len(st["released_leases"]), 1)
        with session_scope() as s:
            self.assertEqual(s.get(Run, "run_a").state, "INTERRUPTED")  # не удалён
            lease = s.get(RunLease, st["released_leases"][0].split(":")[1])
            self.assertNotEqual(lease.released_at, "")  # release, не delete

    def test_survives_restart_and_requires_explicit_resume(self):
        from atlas_core import emergency
        emergency.engage(reason="x")
        # «Рестарт»: новое чтение состояния из БД — по-прежнему активно.
        self.assertTrue(emergency.is_active())
        # Явный resume обязателен; после него — не активно и не реактивируется молча.
        res = emergency.resume(actor="owner")
        self.assertTrue(res["resumed"])
        self.assertFalse(emergency.is_active())

    # --- Fix1: Emergency Stop блокирует СОЗДАНИЕ новых Run ---
    def test_blocks_create_run(self):
        from datetime import datetime, timezone

        from atlas_core import emergency
        from atlas_core.db import session_scope
        from atlas_core.orm import Project
        from atlas_core.runs import RunError, RunService
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Project(id="pj", name="p", source_kind="local_git", source_location="/x",
                          status="connected", created_at=now, updated_at=now))
            s.commit()
        runs = RunService()
        runs.create_run("pj", vp_key="VP-7", dedup_key="ok1")  # до стопа — можно
        emergency.engage(reason="test")
        with self.assertRaises(RunError) as cm:
            runs.create_run("pj", vp_key="VP-7", dedup_key="blocked")
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")


# ---------------------------------------------------------------------------
def _bare_remote(tmp: str) -> tuple[str, str, str]:
    """Создать bare-remote + рабочий клон с seed-коммитом. Возвращает (bare, wc, seed_sha)."""
    bare = str(Path(tmp) / "remote.git")
    wc = str(Path(tmp) / "wc")
    subprocess.run(["git", "init", "--bare", "-b", "main", bare], capture_output=True)
    subprocess.run(["git", "clone", bare, wc], capture_output=True)
    for k, v in (("user.name", "CodeVinci"), ("user.email", "codevinci@example.invalid")):
        subprocess.run(["git", "-C", wc, "config", k, v], capture_output=True)
    Path(wc, "README.md").write_text("seed")
    subprocess.run(["git", "-C", wc, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", wc, "commit", "-m", "сид"], capture_output=True)
    subprocess.run(["git", "-C", wc, "push", "origin", "main"], capture_output=True)
    seed = subprocess.run(["git", "-C", wc, "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    return bare, wc, seed


class TestGithubAdapter(VP7Base):
    def setUp(self):
        super().setUp()
        self._d = tempfile.mkdtemp(prefix="atlas-gh-")
        self.bare, self.wc, self.seed = _bare_remote(self._d)
        from atlas_core.github_adapter import GitContract, GitHubAdapter, LocalForge
        self.forge = LocalForge(self.bare, "acme/demo")
        # enforce_grant=False — явная test-only граница для детерминированных
        # LocalForge-тестов, НЕ проверяющих grant-энфорсмент (bypass A).
        self.ad = GitHubAdapter(forge=self.forge, contract=GitContract(), enforce_grant=False)

    def _feature(self):
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-x"], capture_output=True)
        Path(self.wc, "f.py").write_text("x=1")
        return self.ad.commit(self.wc, "VP-7: изменение")

    def test_commit_push_idempotent(self):
        self._feature()
        p1 = self.ad.push_feature(self.wc, "atlas/vp-7-x")
        p2 = self.ad.push_feature(self.wc, "atlas/vp-7-x")
        self.assertFalse(p1["idempotent"])
        self.assertTrue(p2["idempotent"])

    def test_russian_contract_and_author(self):
        from atlas_core.github_adapter import GitContractError
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-y"], capture_output=True)
        Path(self.wc, "g.py").write_text("y=1")
        with self.assertRaises(GitContractError) as cm:
            self.ad.commit(self.wc, "english only")
        self.assertEqual(cm.exception.code, "NON_RUSSIAN_TEXT")

    def test_direct_main_force_delete_refused(self):
        from atlas_core.github_adapter import GitContractError
        for fn in (lambda: self.ad.push_feature(self.wc, "main"),
                   lambda: self.ad.push_feature(self.wc, "atlas/z", force=True),
                   lambda: self.ad.delete_branch("atlas/z")):
            with self.assertRaises(GitContractError):
                fn()

    def test_pr_idempotent(self):
        sha = self._feature()
        self.ad.push_feature(self.wc, "atlas/vp-7-x")
        pr1 = self.ad.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                                title="VP-7 демо", body="тело")
        pr2 = self.ad.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                                title="VP-7 демо", body="тело")
        self.assertEqual(pr1["number"], pr2["number"])

    def _prod(self, project_id="p"):
        from atlas_core.github_adapter import GitContract, GitHubAdapter
        return GitHubAdapter(forge=self.forge, contract=GitContract(), project_id=project_id)

    def _persist_rp_qr(self, head, verdict, project_id="p"):
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        _paths, refs, arts = self._register_evidence(head)
        pkg = build_review_package(ReviewInputs(
            project_id=project_id, run_id="r", wo_key="VP-7", vp_key="VP-7",
            branch="atlas/vp-7-x", base_sha="B", head_sha=head, impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, verdict, "", [], run_id="r")
        return pkg["id"], rep["id"]

    def _open_pr(self):
        """feature commit (self.ad, enforce_grant=False) + push + PR + green checks."""
        sha = self._feature()
        self.ad.push_feature(self.wc, "atlas/vp-7-x")
        self.forge.set_checks(sha, "GREEN")
        pr = self.ad.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                               title="VP-7 демо", body="тело")
        return sha, pr["number"]

    # --- Fix1: merge исполняется ТОЛЬКО через авторитетный merge_pull_request ---
    def test_merge_pull_request_executes_only_authoritatively(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        pass_rp, pass_qr = self._persist_rp_qr(sha, "PASS")
        revise_rp, revise_qr = self._persist_rp_qr(sha, "REVISE")
        # отдельные RP/QR для stale-head негатива: validate_review_package durable-
        # инвалидирует пакет при несовпадении head, поэтому не переиспользуем pass_rp.
        stale_rp, stale_qr = self._persist_rp_qr(sha, "PASS")

        def _merge(rp, qr, head=sha, grant=g["id"]):
            return prod.merge_pull_request(
                project_id="p", review_package_id=rp, quality_report_id=qr, pr_number=prn,
                expected_head=head, grant_id=grant, base="main",
                message="VP-7: squash после PASS")

        # НЕГАТИВНЫЕ — ни один не должен исполнить merge:
        with self.assertRaises(GitContractError) as c1:   # verdict REVISE
            _merge(revise_rp, revise_qr)
        self.assertEqual(c1.exception.code, "REVIEWER_NOT_PASS")
        with self.assertRaises(GitContractError):          # QR не найден по id
            _merge(pass_rp, "qrep_missing")
        with self.assertRaises(GitContractError):          # stale expected_head (свой RP)
            _merge(stale_rp, stale_qr, head="deadbeef")
        with self.assertRaises(GitContractError) as c4:    # без grant → GRANT_REQUIRED
            _merge(pass_rp, pass_qr, grant="")
        self.assertEqual(c4.exception.code, "GRANT_REQUIRED")
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # НИ ОДИН не смёржил
        # ПОЗИТИВНЫЙ: PASS + green + grant → фактический merge, base продвинулась.
        res = _merge(pass_rp, pass_qr)
        self.assertTrue(res["merged"])
        self.assertNotEqual(self.forge.branch_head("main"), self.seed)
        # PR смёржен → повторный merge того же PR больше не проходит.
        with self.assertRaises(GitContractError):
            _merge(pass_rp, pass_qr)

    def test_merge_requires_resolvable_evidence(self):
        """§2/§7: production merge_pull_request использует ту же evidence-backed RP,
        что и gate; неразрешимое evidence → deny, main не двигается."""
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        paths, refs, arts = self._register_evidence(sha)
        pkg = build_review_package(ReviewInputs(
            project_id="p", head_sha=sha, base_sha="B", impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, "PASS", "", [], run_id="r")
        os.remove(paths[0])  # evidence-файл исчез → авторитетный gate деним
        with self.assertRaises(GitContractError) as c:
            prod.merge_pull_request(project_id="p", review_package_id=pkg["id"],
                                    quality_report_id=rep["id"], pr_number=prn,
                                    expected_head=sha, grant_id=g["id"], base="main")
        self.assertEqual(c.exception.code, "REVIEW_PACKAGE_INVALID")
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # merge НЕ исполнен

    def test_no_public_raw_squash_merge_on_adapter(self):
        # Bypass закрыт: у адаптера нет сырого squash_merge(grant_id, expected_head).
        self.assertFalse(hasattr(self.ad, "squash_merge"))

    # --- call-10 fix (finding 4): Emergency Stop закрывает write/merge boundary ---
    def test_emergency_blocks_write_and_merge(self):
        from atlas_core import emergency
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD",
                         capabilities=["commit", "merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        emergency.engage(reason="stop", actor="owner")
        try:
            with self.assertRaises(GitContractError) as c1:      # commit заблокирован
                prod.commit(self.wc, "VP-7: x", grant_id=g["id"])
            self.assertEqual(c1.exception.code, "EMERGENCY_STOP")
            with self.assertRaises(GitContractError) as c2:      # merge заблокирован
                prod.merge_pull_request(project_id="p", review_package_id=rp,
                                        quality_report_id=qr, pr_number=prn, expected_head=sha,
                                        grant_id=g["id"], base="main")
            self.assertEqual(c2.exception.code, "EMERGENCY_STOP")
            self.assertEqual(self.forge.branch_head("main"), self.seed)  # не смёржено
        finally:
            emergency.resume(actor="owner")

    # --- call-10 fix (finding 2): строгие checks/mergeability (GhForge) ---
    def test_ghforge_checks_and_mergeability_strict(self):
        from atlas_core.github_adapter import GhForge

        class FakeGh(GhForge):
            def __init__(self, runs, merge_json):
                self.repo = "a/b"
                self._runs = runs
                self._merge = merge_json

            def _gh(self, *args, **kw):
                import json as _j

                class R:
                    returncode = 0
                out = R()
                out.stdout = _j.dumps(self._runs) if "check-runs" in " ".join(args) \
                    else _j.dumps(self._merge)
                out.stderr = ""
                return out

        # neutral-only completed runs → НЕ GREEN (нет success).
        f1 = FakeGh([{"name": "x", "status": "completed", "conclusion": "neutral"}], {})
        self.assertEqual(f1.checks("h")["state"], "FAILING")
        # failure среди runs → FAILING.
        f2 = FakeGh([{"name": "x", "status": "completed", "conclusion": "success"},
                     {"name": "y", "status": "completed", "conclusion": "failure"}], {})
        self.assertEqual(f2.checks("h")["state"], "FAILING")
        # все success → GREEN.
        f3 = FakeGh([{"name": "x", "status": "completed", "conclusion": "success"}], {})
        self.assertEqual(f3.checks("h")["state"], "GREEN")
        # mergeStateStatus BLOCKED → НЕ mergeable даже при mergeable=MERGEABLE/OPEN.
        f4 = FakeGh([], {"mergeable": "MERGEABLE", "state": "OPEN", "mergeStateStatus": "BLOCKED"})
        self.assertFalse(f4.mergeability(1)["mergeable"])
        f5 = FakeGh([], {"mergeable": "MERGEABLE", "state": "OPEN", "mergeStateStatus": "CLEAN"})
        self.assertTrue(f5.mergeability(1)["mergeable"])

    # --- call-11 audit (risk A): списание бюджета на ТОЙ ЖЕ version, что evaluate ---
    def test_consume_uses_evaluated_version_snapshot_no_toctou(self):
        """Между evaluate() и consume grant меняется (version бампается конкурентным
        consume при state=ACTIVE). Снимок dec.version устаревает → списание падает
        VERSION_CONFLICT ДО write; ветка на remote не создаётся. Проверяет именно
        снимок version (а не перечитанную более новую версию)."""
        from atlas_core import autonomy
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        self._feature()  # локальный commit (enforce_grant=False); ветка НЕ push-нута
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc], reason="p")
        real_eval = autonomy.evaluate

        def eval_then_mutate(*a, **k):
            dec = real_eval(*a, **k)
            if dec.permitted:  # КОНКУРЕНТНАЯ мутация после решения (version → +1, ACTIVE)
                autonomy.consume_budget(g["id"], n=1, expected_version=dec.version)
            return dec

        autonomy.evaluate = eval_then_mutate
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        finally:
            autonomy.evaluate = real_eval
        self.assertIn(cm.exception.code, ("VERSION_CONFLICT", "GRANT_CONFLICT"))
        self.assertFalse(self.forge.branch_exists("atlas/vp-7-x"))  # push не произошёл

    def test_consume_denies_when_grant_revoked_between_eval_and_consume(self):
        """Отзыв (снятие всего scope) между evaluate и consume → write не исполняется."""
        from atlas_core import autonomy
        from atlas_core.autonomy import create_grant, revoke_grant
        from atlas_core.github_adapter import GitContractError
        self._feature()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc], reason="p")
        real_eval = autonomy.evaluate

        def eval_then_revoke(*a, **k):
            dec = real_eval(*a, **k)
            if dec.permitted:
                revoke_grant(g["id"], expected_version=dec.version)
            return dec

        autonomy.evaluate = eval_then_revoke
        try:
            with self.assertRaises(GitContractError):
                prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        finally:
            autonomy.evaluate = real_eval
        self.assertFalse(self.forge.branch_exists("atlas/vp-7-x"))

    # --- call-11 audit (risk B): барьер Emergency Stop у необратимой forge-границы ---
    def _patch_emergency_after_consume(self):
        """Патчит emergency.blocks_new_jobs: False на 1-м вызове (_consume проходит,
        бюджет спишется), True далее (барьер у границы) — эмулирует engage(), начатый
        в окне между consume и необратимой операцией. Возвращает (restore, calls)."""
        from atlas_core import emergency
        calls = {"n": 0}
        real = emergency.blocks_new_jobs

        def patched():
            calls["n"] += 1
            return calls["n"] > 1

        emergency.blocks_new_jobs = patched

        def restore():
            emergency.blocks_new_jobs = real

        return restore, calls

    def test_emergency_barrier_blocks_push_after_consume(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        self._feature()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc], reason="p")
        restore, calls = self._patch_emergency_after_consume()
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        finally:
            restore()
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        self.assertFalse(self.forge.branch_exists("atlas/vp-7-x"))  # push не пересёк границу
        self.assertGreaterEqual(calls["n"], 2)  # был ВТОРОЙ (барьерный) чек после consume

    def test_emergency_barrier_blocks_commit_after_consume(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-c"],
                       capture_output=True)
        Path(self.wc, "cf.py").write_text("z=1")
        head0 = subprocess.run(["git", "-C", self.wc, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["commit"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc], reason="c")
        restore, calls = self._patch_emergency_after_consume()
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.commit(self.wc, "VP-7: изменение", grant_id=g["id"])
        finally:
            restore()
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        head1 = subprocess.run(["git", "-C", self.wc, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(head0, head1)  # коммит не создан

    def test_emergency_barrier_blocks_create_pr_after_consume(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha = self._feature()
        self.ad.push_feature(self.wc, "atlas/vp-7-x")  # push без emergency (enforce_grant=False)
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["create_pr"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="pr")
        restore, calls = self._patch_emergency_after_consume()
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                               title="VP-7 демо", body="тело", grant_id=g["id"])
        finally:
            restore()
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        self.assertIsNone(self.forge.get_pr(1))  # PR не создан

    def test_emergency_barrier_blocks_merge_after_consume(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        restore, calls = self._patch_emergency_after_consume()
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.merge_pull_request(project_id="p", review_package_id=rp,
                                        quality_report_id=qr, pr_number=prn, expected_head=sha,
                                        grant_id=g["id"], base="main")
        finally:
            restore()
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # merge не пересёк границу

    # --- call-11 audit (risk C): явная политика обязательных CI-контекстов ---
    def test_required_check_contexts_enforced(self):
        from atlas_core.github_adapter import classify_check_runs
        req = {"Python (lint, tests, migrations)", "Web (typecheck, i18n, build)",
               "Core image (fresh-session consumer в образе)", "Секрет-скан"}

        def run(name, concl, status="completed"):
            return {"name": name, "status": status, "conclusion": concl}

        full = [run(n, "success") for n in req]
        self.assertEqual(classify_check_runs(full, req)["state"], "GREEN")
        # одной обязательной job нет → FAILING + missing
        miss = classify_check_runs([r for r in full if r["name"] != "Секрет-скан"], req)
        self.assertEqual(miss["state"], "FAILING")
        self.assertIn("Секрет-скан", miss["missing"])
        # зелена лишь ПОСТОРОННЯЯ job → FAILING (обязательные отсутствуют)
        self.assertEqual(classify_check_runs([run("unrelated", "success")], req)["state"],
                         "FAILING")
        # дубли push+PR одной обязательной job не ломают счёт → GREEN
        self.assertEqual(classify_check_runs(full + full, req)["state"], "GREEN")
        # обязательная job pending → PENDING (head не устоялся)
        pend = [r for r in full if r["name"] != "Секрет-скан"] + \
            [run("Секрет-скан", None, status="in_progress")]
        self.assertEqual(classify_check_runs(pend, req)["state"], "PENDING")
        # обязательная job skipped/neutral НЕ засчитывается как success → FAILING
        for bad_concl in ("skipped", "neutral"):
            mixed = [r for r in full if r["name"] != "Секрет-скан"] + \
                [run("Секрет-скан", bad_concl)]
            self.assertEqual(classify_check_runs(mixed, req)["state"], "FAILING")

    def test_ghforge_applies_atlas_required_policy_for_prod_repo(self):
        from atlas_core.github_adapter import ATLAS_REPO, GhForge

        class FakeGh(GhForge):
            def __init__(self, repo, runs):
                super().__init__(repo)
                self._runs = runs

            def _gh(self, *args, **kw):
                import json as _j

                class R:
                    returncode = 0
                out = R()
                out.stdout = _j.dumps(self._runs)
                out.stderr = ""
                return out

        one_success = [{"name": "x", "status": "completed", "conclusion": "success"}]
        # production-repo Atlas: одиночный посторонний success НЕ зелёный (нужны required).
        self.assertEqual(FakeGh(ATLAS_REPO, one_success).checks("h")["state"], "FAILING")
        # произвольный repo без политики: legacy — одиночный success зелёный.
        self.assertEqual(FakeGh("a/b", one_success).checks("h")["state"], "GREEN")

    # --- call-13 fix (finding 1): commit/push требуют workspace-scope checkout ---
    def test_local_write_requires_workspace_scope(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        prod = self._prod("p")
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-ws"],
                       capture_output=True)
        Path(self.wc, "w.py").write_text("w=1")
        # grant нужного проекта, но БЕЗ этого worktree в workspace_allowlist → deny.
        g_no = create_grant(project_id="p", mode="STANDARD",
                            capabilities=["commit", "push_feature"],
                            allowed_repos=["acme/demo"], allowed_bases=["main"], reason="ws")
        with self.assertRaises(GitContractError) as cm:
            prod.commit(self.wc, "VP-7: изменение", grant_id=g_no["id"])
        self.assertEqual(cm.exception.code, "WORKSPACE_NOT_ALLOWED")
        # с явным workspace — commit/push проходят.
        g_ok = create_grant(project_id="p", mode="STANDARD",
                            capabilities=["commit", "push_feature"],
                            allowed_repos=["acme/demo"], allowed_bases=["main"],
                            workspace_allowlist=[self.wc], reason="ws")
        sha = prod.commit(self.wc, "VP-7: изменение", grant_id=g_ok["id"])
        self.assertTrue(sha)
        prod.push_feature(self.wc, "atlas/vp-7-ws", grant_id=g_ok["id"])
        self.assertTrue(self.forge.branch_exists("atlas/vp-7-ws"))

    # --- call-13 fix (finding 2): squash-merge передаёт --match-head-commit ---
    def test_ghforge_squash_merge_uses_match_head_commit(self):
        from atlas_core.github_adapter import GhForge, PullRequest
        captured = {}

        class FakeGh(GhForge):
            def __init__(self):
                super().__init__("a/b")

            def get_pr(self, number):
                return PullRequest(number=number, base="main", head_branch="atlas/x",
                                   head_sha="H", title="t", body="b", state="OPEN")

            def _gh(self, *args, **kw):
                captured["args"] = args

                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return R()

        FakeGh().squash_merge(1, expected_head="H")
        self.assertIn("--match-head-commit", captured["args"])
        self.assertIn("H", captured["args"])

    # --- call-13 fix (finding 3): ПОВТОРНЫЙ барьер Emergency перед squash ---
    def test_emergency_second_barrier_blocks_merge_before_squash(self):
        from atlas_core import emergency
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        calls = {"n": 0}
        real = emergency.blocks_new_jobs

        def patched():
            calls["n"] += 1
            return calls["n"] >= 3  # 1=_consume,2=первый барьер (проходят); 3=барьер перед squash

        emergency.blocks_new_jobs = patched
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.merge_pull_request(project_id="p", review_package_id=rp,
                                        quality_report_id=qr, pr_number=prn, expected_head=sha,
                                        grant_id=g["id"], base="main")
        finally:
            emergency.blocks_new_jobs = real
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # squash не исполнен
        self.assertGreaterEqual(calls["n"], 3)  # достигнут второй (пред-squash) барьер

    # --- call-9 fix (finding 2): реальный merge пишет durable delivery-историю ---
    def test_merge_records_authoritative_delivery(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.deliveries import list_deliveries
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        prod.merge_pull_request(project_id="p", review_package_id=rp, quality_report_id=qr,
                                pr_number=prn, expected_head=sha, grant_id=g["id"], base="main")
        # durable история доставки содержит АВТОРИТЕТНУЮ запись реального merge (PERMIT).
        dels = list_deliveries(project_id="p")
        merged = [d for d in dels if d.get("gate_decision") == "PERMIT"
                  and d.get("head_sha") == sha and d.get("pr_state") == "MERGED"]
        self.assertTrue(merged, "реальный merge должен записать durable delivery PERMIT/MERGED")
        self.assertEqual(merged[0]["actor"], "merge")

    # --- call-7 REVISE fix (CRITICAL): base сверяется с ЖИВЫМ pr.base ---
    def test_authoritative_base_mismatch_denies(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import authorize_merge_execution
        sha, prn = self._open_pr()  # PR фактически base=main
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main", "production"],
                         reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        # caller передаёт base=production (в grant есть), но живой PR идёт в main →
        # fail-closed deny: merge не должен уйти в фактический pr.base по чужой сверке.
        d = authorize_merge_execution(
            forge=self.forge, repo="acme/demo", project_id="p", review_package_id=rp,
            quality_report_id=qr, pr_number=prn, expected_head=sha, grant_id=g["id"],
            base="production", environment="")
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "GRANT_DENIED")
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # не смёржено

    def test_authoritative_base_matches_live_permits(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import authorize_merge_execution
        sha, prn = self._open_pr()
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        d = authorize_merge_execution(
            forge=self.forge, repo="acme/demo", project_id="p", review_package_id=rp,
            quality_report_id=qr, pr_number=prn, expected_head=sha, grant_id=g["id"],
            base="main", environment="")
        self.assertTrue(d.permitted)  # caller base совпал с живым pr.base

    # --- call-7 REVISE fix (HIGH): gate-условия выводятся из durable RP, не True ---
    def test_authoritative_baseline_derived_from_rp(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import authorize_merge_execution
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        sha, prn = self._open_pr()
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        # RP БЕЗ base_sha → baseline_known выводится False → deny (не безусловный True).
        _p, refs, arts = self._register_evidence(sha)
        pkg = build_review_package(ReviewInputs(
            project_id="p", run_id="r", wo_key="VP-7", vp_key="VP-7", branch="atlas/vp-7-x",
            base_sha="", head_sha=sha, impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, "PASS", "", [], run_id="r")
        d = authorize_merge_execution(
            forge=self.forge, repo="acme/demo", project_id="p", review_package_id=pkg["id"],
            quality_report_id=rep["id"], pr_number=prn, expected_head=sha, grant_id=g["id"],
            base="main", environment="")
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "BASELINE_UNKNOWN")

    def test_authoritative_scope_derived_from_rp(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import authorize_merge_execution
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        sha, prn = self._open_pr()
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        # RP БЕЗ impact_class → diff_in_scope выводится False → deny.
        _p, refs, arts = self._register_evidence(sha)
        pkg = build_review_package(ReviewInputs(
            project_id="p", run_id="r", wo_key="VP-7", vp_key="VP-7", branch="atlas/vp-7-x",
            base_sha="B", head_sha=sha, impact_class="",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, "PASS", "", [], run_id="r")
        d = authorize_merge_execution(
            forge=self.forge, repo="acme/demo", project_id="p", review_package_id=pkg["id"],
            quality_report_id=rep["id"], pr_number=prn, expected_head=sha, grant_id=g["id"],
            base="main", environment="")
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "DIFF_OUT_OF_SCOPE")

    # --- §1 audit: project-binding merge (unbound / caller-mismatch / RP-mismatch) ---
    def test_merge_unbound_adapter_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        unbound = self._prod("")   # адаптер без project_id
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        with self.assertRaises(GitContractError) as cm:
            unbound.merge_pull_request(project_id="p", review_package_id=rp, quality_report_id=qr,
                                       pr_number=prn, expected_head=sha, grant_id=g["id"], base="main")
        self.assertEqual(cm.exception.code, "ADAPTER_PROJECT_UNBOUND")
        self.assertEqual(self.forge.branch_head("main"), self.seed)

    def test_merge_caller_project_mismatch_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")   # адаптер привязан к p
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")
        with self.assertRaises(GitContractError) as cm:
            prod.merge_pull_request(project_id="other", review_package_id=rp, quality_report_id=qr,
                                    pr_number=prn, expected_head=sha, grant_id=g["id"], base="main")
        self.assertEqual(cm.exception.code, "PROJECT_MISMATCH")
        self.assertEqual(self.forge.branch_head("main"), self.seed)

    def test_merge_rp_project_mismatch_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        # RP принадлежит ДРУГОМУ проекту, чем адаптер/операция
        rp, qr = self._persist_rp_qr(sha, "PASS", project_id="other")
        with self.assertRaises(GitContractError) as cm:
            prod.merge_pull_request(project_id="p", review_package_id=rp, quality_report_id=qr,
                                    pr_number=prn, expected_head=sha, grant_id=g["id"], base="main")
        self.assertEqual(cm.exception.code, "GRANT_DENIED")
        self.assertEqual(self.forge.branch_head("main"), self.seed)

    def test_merge_toctou_ci_regresses_denied(self):
        # CI зелёный на момент authorize, но становится не-GREEN перед самим squash →
        # merge НЕ исполняется (§1 TOCTOU: перепроверяются checks+mergeability, не только head).
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        sha, prn = self._open_pr()
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="m")
        rp, qr = self._persist_rp_qr(sha, "PASS")

        orig_checks = prod.forge.checks
        calls = {"n": 0}

        def flaky_checks(head):   # первый вызов (authorize) GREEN, второй (TOCTOU) FAILING
            calls["n"] += 1
            return {"head_sha": head, "state": "GREEN" if calls["n"] == 1 else "FAILING"}

        prod.forge.checks = flaky_checks
        try:
            with self.assertRaises(GitContractError) as cm:
                prod.merge_pull_request(project_id="p", review_package_id=rp, quality_report_id=qr,
                                        pr_number=prn, expected_head=sha, grant_id=g["id"], base="main")
            self.assertEqual(cm.exception.code, "CI_NOT_GREEN_AT_MERGE")
        finally:
            prod.forge.checks = orig_checks
        self.assertEqual(self.forge.branch_head("main"), self.seed)  # не смёржено

    # --- Fix4: реальные write-операции списывают invocation-бюджет grant ---
    def test_push_consumes_budget_and_exhaustion_blocks(self):
        from atlas_core.autonomy import create_grant, get_grant
        from atlas_core.github_adapter import GitContractError
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc],
                         budget={"max_invocations": 1}, reason="budget")
        self._feature()
        # первый push с grant — списывает 1/1
        self.ad.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        self.assertEqual(get_grant(g["id"])["budget"]["used_invocations"], 1)
        # второй push — бюджет исчерпан → отказ
        with self.assertRaises(GitContractError) as cm:
            self.ad.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        self.assertEqual(cm.exception.code, "BUDGET_EXHAUSTED")

    # --- Bypass A: production-адаптер (enforce_grant=True) не может опустить grant ---
    def test_production_caller_cannot_omit_grant(self):
        from atlas_core.github_adapter import GitContract, GitContractError, GitHubAdapter
        prod = GitHubAdapter(forge=self.forge, contract=GitContract())  # enforce_grant=True
        self._feature()
        with self.assertRaises(GitContractError) as cm:
            prod.push_feature(self.wc, "atlas/vp-7-x")  # без grant_id
        self.assertEqual(cm.exception.code, "GRANT_REQUIRED")

    # --- Bypass A: enforce_grant=False недопустим с реальным GhForge (guard) ---
    def test_enforce_grant_false_forbidden_with_ghforge(self):
        from atlas_core.github_adapter import (
            GhForge,
            GitContract,
            GitContractError,
            GitHubAdapter,
        )
        with self.assertRaises(GitContractError) as cm:
            GitHubAdapter(forge=GhForge("owner/repo"), contract=GitContract(),
                          enforce_grant=False, project_id="p")
        self.assertEqual(cm.exception.code, "GRANT_ENFORCEMENT_REQUIRED")
        # с enforce_grant=True (default) + привязкой к проекту GhForge конструируется штатно
        GitHubAdapter(forge=GhForge("owner/repo"), contract=GitContract(), project_id="p")

    # --- §1 audit: production GhForge-адаптер обязан быть привязан к проекту ---
    def test_ghforge_adapter_requires_project_binding(self):
        from atlas_core.github_adapter import (
            GhForge,
            GitContract,
            GitContractError,
            GitHubAdapter,
        )
        with self.assertRaises(GitContractError) as cm:
            GitHubAdapter(forge=GhForge("owner/repo"), contract=GitContract())  # без project_id
        self.assertEqual(cm.exception.code, "ADAPTER_PROJECT_UNBOUND")

    # --- Bypass A: ВСЕ production write-категории требуют grant (не только push) ---
    def test_production_all_write_categories_require_grant(self):
        from atlas_core.github_adapter import GitContract, GitContractError, GitHubAdapter
        prod = GitHubAdapter(forge=self.forge, contract=GitContract())  # enforce_grant=True
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-x"], capture_output=True)
        Path(self.wc, "f.py").write_text("x=1")
        # commit
        with self.assertRaises(GitContractError) as c1:
            prod.commit(self.wc, "VP-7: без grant")
        self.assertEqual(c1.exception.code, "GRANT_REQUIRED")
        # create_pr
        with self.assertRaises(GitContractError) as c2:
            prod.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha="H",
                           title="VP-7 демо", body="тело")
        self.assertEqual(c2.exception.code, "GRANT_REQUIRED")
        # (merge без grant покрыт в test_merge_pull_request_executes_only_authoritatively)

    # --- Bypass A: exact repo/base scope энфорсится на write (не только капабилити) ---
    def test_production_exact_repo_scope_enforced(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContract, GitContractError, GitHubAdapter
        prod = GitHubAdapter(forge=self.forge, contract=GitContract())  # forge repo = acme/demo
        self._feature_prod(prod)
        # grant с нужной капабилити, но scoped к ДРУГОМУ repo → push денит REPO_NOT_ALLOWED
        g = create_grant(project_id="p", mode="STANDARD",
                         capabilities=["push_feature", "create_pr"],
                         allowed_repos=["other/repo"], allowed_bases=["main"], reason="scope")
        with self.assertRaises(GitContractError) as cm:
            prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        self.assertEqual(cm.exception.code, "REPO_NOT_ALLOWED")
        # base вне allowlist → create_pr денит BASE_NOT_ALLOWED
        g2 = create_grant(project_id="p", mode="STANDARD", capabilities=["create_pr"],
                          allowed_repos=["acme/demo"], allowed_bases=["release"], reason="scope")
        with self.assertRaises(GitContractError) as cm2:
            prod.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha="H",
                           title="VP-7 демо", body="тело", grant_id=g2["id"])
        self.assertEqual(cm2.exception.code, "BASE_NOT_ALLOWED")

    # --- Bypass A: все write-категории списывают бюджет одного grant ---
    def test_production_all_write_categories_consume_budget(self):
        from atlas_core.autonomy import create_grant, get_grant
        prod = self._prod("p")
        g = create_grant(project_id="p", mode="STANDARD",
                         capabilities=["commit", "push_feature", "create_pr", "merge_after_pass"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
                         workspace_allowlist=[self.wc],
                         budget={"max_invocations": 4}, reason="budget")
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-x"], capture_output=True)
        Path(self.wc, "f.py").write_text("x=1")
        sha = prod.commit(self.wc, "VP-7: изменение", grant_id=g["id"])          # 1
        prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])              # 2
        self.forge.set_checks(sha, "GREEN")
        pr = prod.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                            title="VP-7 демо", body="тело", grant_id=g["id"])     # 3
        rp, qr = self._persist_rp_qr(sha, "PASS")
        prod.merge_pull_request(project_id="p", review_package_id=rp, quality_report_id=qr,
                                pr_number=pr["number"], expected_head=sha, grant_id=g["id"],
                                base="main", message="VP-7: squash после PASS")     # 4
        self.assertEqual(get_grant(g["id"])["budget"]["used_invocations"], 4)

    # --- Fix2: production-адаптер, привязанный к проекту, отвергает grant чужого проекта ---
    def test_adapter_cross_project_grant_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.github_adapter import GitContractError
        prod = self._prod("p")   # адаптер привязан к проекту p
        self._feature_prod(prod)
        g = create_grant(project_id="other", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"], reason="x")
        with self.assertRaises(GitContractError) as cm:
            prod.push_feature(self.wc, "atlas/vp-7-x", grant_id=g["id"])
        self.assertEqual(cm.exception.code, "PROJECT_MISMATCH")

    def _feature_prod(self, prod):
        subprocess.run(["git", "-C", self.wc, "checkout", "-b", "atlas/vp-7-x"], capture_output=True)
        Path(self.wc, "f.py").write_text("x=1")
        subprocess.run(["git", "-C", self.wc, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", self.wc, "commit", "-qm", "VP-7: seed"], capture_output=True)


# ---------------------------------------------------------------------------
class TestMergeGate(VP7Base):
    def _setup(self, **over):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import MergeRequest
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"],
                         reason="r")
        base = dict(repo="a/b", base="main", branch="atlas/vp-7", head_sha="HEAD1",
                    project_id="p", grant_id=g["id"], environment="synthetic",
                    review_package={"id": "rpkg_1", "status": "valid", "head_sha": "HEAD1"},
                    quality_report={"verdict": "PASS", "blocking_count": 0,
                                    "review_package_id": "rpkg_1"},
                    checks={"head_sha": "HEAD1", "state": "GREEN"},
                    mergeability={"mergeable": True, "state": "CLEAN"}, pr_number=1)
        base.update(over)
        return MergeRequest(**base)

    def test_current_head_pass_permits(self):
        from atlas_core.merge_gate import evaluate_merge
        self.assertTrue(evaluate_merge(self._setup()).permitted)

    def test_stale_review_denies(self):
        from atlas_core.merge_gate import evaluate_merge
        d = evaluate_merge(self._setup(review_package={"status": "valid", "head_sha": "OLD"}))
        self.assertEqual(d.reason_code, "STALE_REVIEW_HEAD")

    def test_stale_ci_denies(self):
        from atlas_core.merge_gate import evaluate_merge
        d = evaluate_merge(self._setup(checks={"head_sha": "OLD", "state": "GREEN"}))
        self.assertEqual(d.reason_code, "STALE_OR_FAILING_CI")

    def test_blocking_finding_denies(self):
        from atlas_core.merge_gate import evaluate_merge
        d = evaluate_merge(self._setup(quality_report={"verdict": "PASS", "blocking_count": 1}))
        self.assertEqual(d.reason_code, "BLOCKING_QUALITY_FINDING")

    # --- Fix3: неполные current-head evidence → fail-closed deny ---
    def test_missing_review_head_denies(self):
        from atlas_core.merge_gate import evaluate_merge
        d = evaluate_merge(self._setup(review_package={"status": "valid"}))  # нет head_sha
        self.assertEqual(d.reason_code, "STALE_REVIEW_HEAD")

    def test_missing_ci_head_denies(self):
        from atlas_core.merge_gate import evaluate_merge
        d = evaluate_merge(self._setup(checks={"state": "GREEN"}))  # нет head_sha
        self.assertEqual(d.reason_code, "STALE_OR_FAILING_CI")

    def test_scopeless_merge_grant_denies(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import evaluate_merge
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         reason="scopeless")  # НЕТ allowed_repos/bases
        req = self._setup(grant_id=g["id"])
        self.assertFalse(evaluate_merge(req).permitted)

    # --- Bypass C: авторитетный merge грузит RP/QR из хранилища (не caller-dict) ---
    def _persist_rp_qr(self, *, head_sha: str, verdict: str = "PASS"):
        """Создать РЕАЛЬНЫЕ persisted ReviewPackage + QualityReport и вернуть их id."""
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        _paths, refs, arts = self._register_evidence(head_sha)
        pkg = build_review_package(ReviewInputs(
            project_id="p", run_id="run_x", wo_key="VP-7", vp_key="VP-7",
            branch="atlas/vp-7", base_sha="B", head_sha=head_sha, impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, verdict, "", [], run_id="run_x")
        return pkg, rep

    def _auth_grant(self):
        from atlas_core.autonomy import create_grant
        return create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                            environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"],
                            reason="auth")["id"]

    def _auth_call(self, *, review_package_id, head_sha="HEAD1", quality_report_id="", grant_id=None):
        from atlas_core.merge_gate import evaluate_merge_authoritative
        return evaluate_merge_authoritative(
            repo="a/b", base="main", branch="atlas/vp-7", head_sha=head_sha, project_id="p",
            grant_id=grant_id or self._auth_grant(), review_package_id=review_package_id,
            quality_report_id=quality_report_id, environment="synthetic",
            checks={"head_sha": head_sha, "state": "GREEN"},
            mergeability={"mergeable": True, "state": "CLEAN"}, pr_number=1)

    def test_authoritative_merge_from_storage_permits(self):
        pkg, rep = self._persist_rp_qr(head_sha="HEAD1", verdict="PASS")
        d = self._auth_call(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertTrue(d.permitted)

    def test_authoritative_merge_stale_rp_in_storage_denies(self):
        # RP в хранилище привязан к OLD head; авторитетная сверка с текущим head
        # инвалидирует пакет фактом → deny (caller лишь передал id, не содержимое).
        pkg, rep = self._persist_rp_qr(head_sha="OLD", verdict="PASS")
        d = self._auth_call(review_package_id=pkg["id"], head_sha="HEAD1",
                            quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")

    def test_authoritative_merge_missing_rp_denies(self):
        d = self._auth_call(review_package_id="rpkg_nonexistent")
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")

    def test_authoritative_merge_empty_rp_id_denies(self):
        d = self._auth_call(review_package_id="")
        self.assertEqual(d.reason_code, "STALE_REVIEW_HEAD")

    def test_authoritative_merge_no_report_denies(self):
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        _paths, refs, arts = self._register_evidence("HEAD1")
        pkg = build_review_package(ReviewInputs(
            project_id="p", head_sha="HEAD1", impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts), actor="reviewer")
        d = self._auth_call(review_package_id=pkg["id"])  # QR не создавали
        self.assertEqual(d.reason_code, "REVIEWER_NOT_PASS")

    def test_authoritative_merge_qr_id_mismatch_denies(self):
        pkg, _rep = self._persist_rp_qr(head_sha="HEAD1", verdict="PASS")
        d = self._auth_call(review_package_id=pkg["id"], quality_report_id="qrep_wrong")
        self.assertEqual(d.reason_code, "STALE_REVIEW_HEAD")

    def test_authoritative_merge_revise_report_denies(self):
        pkg, rep = self._persist_rp_qr(head_sha="HEAD1", verdict="REVISE")
        d = self._auth_call(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertEqual(d.reason_code, "REVIEWER_NOT_PASS")


# ---------------------------------------------------------------------------
class TestEvidenceGate(VP7Base):
    """§2: authoritative merge gate выводит evidence-факты из ДОВЕРЕННОГО durable
    store + пересчёта реальных файлов (не caller-claims). Все оси fail-closed:
    present→proceed, missing→MISSING_EVIDENCE, tampered→ARTIFACT_ALTERED, чужой
    head→deny, один список без store→deny, evidence-empty RP→deny."""

    def _mk_rp_qr(self, *, head_sha, refs, arts, verdict="PASS", project_id="p"):
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        pkg = build_review_package(ReviewInputs(
            project_id=project_id, head_sha=head_sha, base_sha="B", impact_class="LOCAL",
            evidence_refs=refs, artifact_hashes=arts,
            claims=[{"claim": "c", "verified": True}]), actor="reviewer")
        rep = QualityService().build_report(pkg, verdict, "", [], run_id="r")
        return pkg, rep

    def _auth(self, *, review_package_id, quality_report_id="", head_sha="HEAD1"):
        from atlas_core.autonomy import create_grant
        from atlas_core.merge_gate import evaluate_merge_authoritative
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"],
                         reason="auth")
        return evaluate_merge_authoritative(
            repo="a/b", base="main", branch="atlas/vp-7", head_sha=head_sha, project_id="p",
            grant_id=g["id"], review_package_id=review_package_id,
            quality_report_id=quality_report_id, environment="synthetic",
            checks={"head_sha": head_sha, "state": "GREEN"},
            mergeability={"mergeable": True, "state": "CLEAN"}, pr_number=1)

    def test_evidence_backed_present_proceeds(self):
        _p, refs, arts = self._register_evidence("HEAD1")
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=arts)
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertTrue(d.permitted, d.to_dict())

    def test_missing_evidence_denies(self):
        paths, refs, arts = self._register_evidence("HEAD1")
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=arts)
        os.remove(paths[0])  # реальный файл исчез → ссылка неразрешима
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("MISSING_EVIDENCE", d.conditions[0]["detail"])

    def test_tampered_artifact_denies(self):
        paths, refs, arts = self._register_evidence("HEAD1")
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=arts)
        Path(paths[0]).write_text("TAMPERED", encoding="utf-8")  # sha≠зарегистрированного
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("ARTIFACT_ALTERED", d.conditions[0]["detail"])

    def test_tampered_ref_without_artifact_hash_denies(self):
        # call-15 finding 1: evidence_ref зарегистрирован, но НЕ включён в artifact_hashes;
        # подмена файла деним ARTIFACT_ALTERED (store самодостаточен, независим от arts).
        paths, refs, _arts = self._register_evidence("HEAD1")
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=[])  # без artifact_hashes
        Path(paths[0]).write_text("TAMPERED-REF-ONLY", encoding="utf-8")
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("ARTIFACT_ALTERED", d.conditions[0]["detail"])

    def test_evidence_from_another_head_denies(self):
        _p, refs, arts = self._register_evidence("OTHERHEAD")   # зарегистрировано под чужим head
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=arts)
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("MISSING_EVIDENCE", d.conditions[0]["detail"])

    def test_caller_list_without_store_denies(self):
        # RP объявляет evidence_refs (просто список), но в durable store ничего нет —
        # одного списка недостаточно для авторитетной авторизации.
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=["ev:x", "ev:y"], arts=[])
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("MISSING_EVIDENCE", d.conditions[0]["detail"])

    def test_evidence_empty_rp_denies(self):
        # Пустой evidence нельзя выдать за пройденный evidence-backed финальный review.
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=[], arts=[])
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"])
        self.assertFalse(d.permitted)
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")
        self.assertIn("evidence-backed", d.conditions[0]["detail"])

    def test_stale_head_with_valid_evidence_denies(self):
        _p, refs, arts = self._register_evidence("HEAD1")
        pkg, rep = self._mk_rp_qr(head_sha="HEAD1", refs=refs, arts=arts)
        d = self._auth(review_package_id=pkg["id"], quality_report_id=rep["id"], head_sha="HEAD2")
        self.assertFalse(d.permitted)   # rp.head HEAD1 != expected HEAD2 → STALE
        self.assertEqual(d.reason_code, "REVIEW_PACKAGE_INVALID")


# ---------------------------------------------------------------------------
class TestDeliveryPersistence(VP7Base):
    def test_record_is_idempotent_and_captures_gate(self):
        from atlas_core.deliveries import list_deliveries, record_delivery
        d1 = record_delivery(project_id="p", repo="a/b", base="main", branch="atlas/vp-7",
                             head_sha="HEAD1", gate_decision="DENY", gate_reason="REVIEWER_NOT_PASS",
                             checks_state="GREEN")
        d2 = record_delivery(project_id="p", repo="a/b", base="main", branch="atlas/vp-7",
                             head_sha="HEAD1", gate_decision="PERMIT", gate_reason="MERGE_PERMITTED",
                             mergeable=True)
        # тот же ключ → та же строка (upsert), обновлённое решение
        self.assertEqual(d1["id"], d2["id"])
        self.assertEqual(d2["gate_decision"], "PERMIT")
        self.assertEqual(len(list_deliveries(project_id="p")), 1)
        # другой head → новая строка
        record_delivery(project_id="p", repo="a/b", base="main", branch="atlas/vp-7",
                        head_sha="HEAD2", gate_decision="PERMIT")
        self.assertEqual(len(list_deliveries(project_id="p")), 2)

    # --- Fix3: ключ содержит ПОЛНЫЙ head SHA (не первые 12 символов) ---
    def test_delivery_key_uses_full_head(self):
        from atlas_core.deliveries import delivery_key
        full = "0123456789abcdef0123456789abcdef01234567"
        k = delivery_key("a/b", "main", "atlas/vp-7", full)
        self.assertIn(full, k)
        # два head с одинаковым 12-префиксом различаются в ключе
        k2 = delivery_key("a/b", "main", "atlas/vp-7", "0123456789ab" + "f" * 28)
        self.assertNotEqual(k, k2)

    # --- Fix3: UNIQUE-индекс на idempotency_key (дубликат → IntegrityError) ---
    def test_delivery_unique_constraint(self):
        from datetime import datetime, timezone

        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import GithubDelivery
        from sqlalchemy.exc import IntegrityError
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(GithubDelivery(id=new_id("ghd"), idempotency_key="dupkey",
                                 created_at=now, updated_at=now))
            s.commit()
        with self.assertRaises(IntegrityError):
            with session_scope() as s:
                s.add(GithubDelivery(id=new_id("ghd"), idempotency_key="dupkey",
                                     created_at=now, updated_at=now))
                s.commit()

    # --- Fix3: конкурентные одинаковые доставки дают ровно ОДНУ строку ---
    def test_delivery_concurrent_upsert_single_row(self):
        import threading

        from atlas_core.deliveries import list_deliveries, record_delivery
        head = "C" * 40
        n = 6
        barrier = threading.Barrier(n)
        errors: list[Exception] = []
        results: list[dict] = []
        lock = threading.Lock()

        def worker(i):
            try:
                barrier.wait()
                out = record_delivery(project_id="p", repo="a/b", base="main", branch="atlas/vp-7",
                                      head_sha=head, gate_decision="PERMIT", gate_reason=f"r{i}")
                with lock:
                    results.append(out)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows = [d for d in list_deliveries(project_id="p") if d["head_sha"] == head]
        self.assertEqual(len(rows), 1)  # атомарный upsert → одна durable-строка
        # Truthful семантика: ровно одно логическое создание, все возвращают тот же id.
        self.assertEqual(sum(1 for r in results if r.get("created")), 1)
        self.assertEqual(len({r["id"] for r in results}), 1)

    def test_merge_gate_preview_api_persists_delivery(self):
        from atlas_core.app import create_app
        from atlas_core.autonomy import create_grant
        from atlas_core.deliveries import list_deliveries
        from atlas_core.settings import load_settings
        from starlette.testclient import TestClient
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["merge_after_pass"],
                         environment="synthetic", allowed_repos=["a/b"], allowed_bases=["main"],
                         reason="r")
        client = TestClient(create_app(load_settings()))
        resp = client.post("/api/v1/github/merge-gate/preview", json={
            "repo": "a/b", "base": "main", "branch": "atlas/vp-7", "head_sha": "HEADX",
            "project_id": "p", "grant_id": g["id"], "environment": "synthetic",
            "review_package": {"id": "rpkg_x", "status": "valid", "head_sha": "HEADX"},
            "quality_report": {"verdict": "PASS", "blocking_count": 0, "review_package_id": "rpkg_x"},
            "checks": {"head_sha": "HEADX", "state": "GREEN"},
            "mergeability": {"mergeable": True, "state": "CLEAN"}, "pr_number": 5})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["gate"]["permitted"])
        self.assertEqual(body["delivery"]["gate_decision"], "PERMIT")
        self.assertGreaterEqual(len(list_deliveries(project_id="p")), 1)


# ---------------------------------------------------------------------------
class TestTimeMachine(VP7Base):
    def _ckpt(self, **over):
        from atlas_core.timemachine import CheckpointInputs, create_checkpoint
        base = dict(project_id="p", vp_key="VP-7", run_id="r1", db_revision="0007",
                    branch="atlas/vp-7-src", base_sha="BASE", head_sha="HEAD",
                    worktree_status="clean", patch_hash="sha256:pp",
                    artifact_hashes=[{"path": "a", "sha": "sha256:aa"}],
                    profile_alias="claude-pro-01", model="claude", effort="medium",
                    session_ids=["sess-1"], grant_hash="sha256:g", cause="post-review")
        base.update(over)
        return create_checkpoint(CheckpointInputs(**base))

    def test_hash_deterministic_and_tamper_invalidates(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import Checkpoint
        from atlas_core.timemachine import verify_checkpoint
        cp = self._ckpt()
        self.assertTrue(verify_checkpoint(cp["id"])[0])
        with session_scope() as s:
            s.get(Checkpoint, cp["id"]).head_sha = "TAMPERED"
            s.commit()
        ok, reason = verify_checkpoint(cp["id"])
        self.assertFalse(ok)
        self.assertEqual(reason, "TAMPERED")

    def test_no_secrets_in_checkpoint(self):
        import json
        blob = json.dumps(self._ckpt()).lower()
        for marker in ("@", "token", "cookie", "password", "transcript", "/home/", "/root/"):
            self.assertNotIn(marker, blob)

    def _repo_with_base_head(self):
        d = tempfile.mkdtemp(prefix="atlas-tm-")
        repo = str(Path(d) / "repo")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], capture_output=True)
        for k, v in (("user.name", "CodeVinci"), ("user.email", "c@example.invalid")):
            subprocess.run(["git", "-C", repo, "config", k, v], capture_output=True)
        Path(repo, "a").write_text("1")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "b"], capture_output=True)
        base = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        subprocess.run(["git", "-C", repo, "checkout", "-qb", "atlas/vp-7-src"], capture_output=True)
        Path(repo, "checkpoint_file").write_text("2")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "w"], capture_output=True)
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        return repo, base, head

    # --- call-11 fix (finding 1): replay воспроизводит СОСТОЯНИЕ checkpoint (head) ---
    def test_replay_reproduces_checkpoint_head_state(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import replay
        repo, base, head = self._repo_with_base_head()
        cp = self._ckpt(branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        # local-мутация без github repo-имени → workspace-scope обязателен (call-12 finding 1).
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"],
                         workspace_allowlist=[repo], reason="r")
        res = replay(cp["id"], grant_id=g["id"], repo_path=repo)
        # новая ветка указывает на head_sha checkpoint (не baseline).
        new_head = subprocess.run(["git", "-C", repo, "rev-parse", res["new_branch"]],
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(new_head, head)   # состояние checkpoint, не base
        # и содержит файл, добавленный в checkpoint (изменения base..head сохранены).
        ls = subprocess.run(["git", "-C", repo, "ls-tree", "--name-only", res["new_branch"]],
                            capture_output=True, text=True).stdout
        self.assertIn("checkpoint_file", ls)

    # --- call-11 fix (finding 4): повторный replay → уникальная ветка + новый Run ---
    def test_repeated_replay_distinct_branch_and_run(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import replay
        repo, base, head = self._repo_with_base_head()
        cp = self._ckpt(branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"],
                         workspace_allowlist=[repo], reason="r")
        r1 = replay(cp["id"], grant_id=g["id"], repo_path=repo)
        r2 = replay(cp["id"], grant_id=g["id"], repo_path=repo)  # не падает на существующей ветке
        self.assertNotEqual(r1["new_branch"], r2["new_branch"])   # уникальные ветки
        self.assertNotEqual(r1["new_run_id"], r2["new_run_id"])   # разные Run

    # --- call-11 fix (finding 3): compare верифицирует checkpoints (tamper → INVALID) ---
    def test_compare_rejects_tampered_checkpoint(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import Checkpoint
        from atlas_core.timemachine import InvalidCheckpointError, compare
        a = self._ckpt(head_sha="H1")
        b = self._ckpt(head_sha="H2")
        compare(a["id"], b["id"])  # валидные — ок
        with session_scope() as s:
            s.get(Checkpoint, a["id"]).head_sha = "TAMPERED"
            s.commit()
        with self.assertRaises(InvalidCheckpointError):   # изменённый → fail-closed
            compare(a["id"], b["id"])

    # --- call-11 audit (finding 2): production HTTP replay материализует ветку ---
    def test_replay_production_http_endpoint(self):
        """POST /replay через реальный FastAPI: доверенный checkout выводится из
        durable-состояния (Worktree), создаётся новый Run И материализуется ветка
        в состоянии checkpoint; повтор → новые ветка/Run; source не переписан;
        подделанный evidence и Emergency Stop → отказ без ветки/Run."""
        from atlas_core import emergency
        from atlas_core.app import create_app
        from atlas_core.autonomy import create_grant
        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import Checkpoint, Project, Run, Worktree
        from atlas_core.settings import load_settings
        from sqlalchemy import select
        from starlette.testclient import TestClient

        repo, base, head = self._repo_with_base_head()
        with session_scope() as s:  # durable project (github-source) + активный Worktree
            s.add(Project(id="p", name="demo", source_kind="github",
                          source_location="https://example.invalid/a/b", source_ref="a/b",
                          status="connected"))
            s.add(Worktree(id=new_id("wt"), project_id="p", branch="atlas/vp-7-src",
                           path=repo, status="active"))
            s.commit()
        cp = self._ckpt(project_id="p", branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="replay")
        client = TestClient(create_app(load_settings()))

        def _count_runs():
            with session_scope() as s:
                return len(s.execute(select(Run).where(Run.project_id == "p")).scalars().all())

        def _replay_branches():
            r = subprocess.run(["git", "-C", repo, "branch", "--list", "atlas/replay-*"],
                               capture_output=True, text=True)
            return {x.strip("* ").strip() for x in r.stdout.splitlines() if x.strip()}

        def _rev(ref):
            return subprocess.run(["git", "-C", repo, "rev-parse", ref],
                                  capture_output=True, text=True).stdout.strip()

        runs0 = _count_runs()
        # (1)+(2)+(3) валидный checkpoint + свежий grant → POST → distinct Run
        r1 = client.post(f"/api/v1/checkpoints/{cp['id']}/replay", json={"grant_id": g["id"]})
        self.assertEqual(r1.status_code, 200, r1.text)
        b1 = r1.json()["replay"]["new_branch"]
        self.assertEqual(_count_runs(), runs0 + 1)              # (3) новый Run создан
        # (4)+(5)+(6) реальная ветка в доверенном repo, состояние head + файл checkpoint
        self.assertIn(b1, _replay_branches())                  # (4) ветка создана
        self.assertEqual(_rev(b1), head)                       # (5) ветка == head_sha
        ls = subprocess.run(["git", "-C", repo, "ls-tree", "--name-only", b1],
                            capture_output=True, text=True).stdout
        self.assertIn("checkpoint_file", ls)                   # (6) изменение base..head есть
        # (7) второй replay → другая ветка + другой Run
        r2 = client.post(f"/api/v1/checkpoints/{cp['id']}/replay", json={"grant_id": g["id"]})
        self.assertEqual(r2.status_code, 200, r2.text)
        b2 = r2.json()["replay"]["new_branch"]
        self.assertNotEqual(b1, b2)
        self.assertEqual(_count_runs(), runs0 + 2)
        self.assertNotEqual(r1.json()["replay"]["new_run_id"], r2.json()["replay"]["new_run_id"])
        # (8) source-ветка не переписана
        self.assertEqual(_rev("atlas/vp-7-src"), head)
        # (9) подделанный checkpoint → отказ INVALID_EVIDENCE
        with session_scope() as s:
            s.get(Checkpoint, cp["id"]).head_sha = "TAMPERED"
            s.commit()
        r3 = client.post(f"/api/v1/checkpoints/{cp['id']}/replay", json={"grant_id": g["id"]})
        self.assertEqual(r3.status_code, 409)
        self.assertEqual(r3.json()["error"]["code"], "INVALID_EVIDENCE")
        # (10) Emergency Stop блокирует без создания ветки и Run
        cp2 = self._ckpt(project_id="p", branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        runs_pre, br_pre = _count_runs(), _replay_branches()
        emergency.engage(reason="stop", actor="owner")
        try:
            r4 = client.post(f"/api/v1/checkpoints/{cp2['id']}/replay", json={"grant_id": g["id"]})
        finally:
            emergency.resume(actor="owner")
        self.assertEqual(r4.status_code, 409)
        self.assertEqual(r4.json()["error"]["code"], "EMERGENCY_STOP")
        self.assertEqual(_count_runs(), runs_pre)              # Run не создан
        self.assertEqual(_replay_branches(), br_pre)          # ветка не создана

    def test_replay_production_no_trusted_checkout_fails_closed(self):
        """finding 2: без доверенного checkout (нет Worktree/local_git) endpoint НЕ
        создаёт Run — fail-closed, а не «Run без ветки»."""
        from atlas_core.app import create_app
        from atlas_core.autonomy import create_grant
        from atlas_core.settings import load_settings
        from starlette.testclient import TestClient

        cp = self._ckpt(project_id="p-nowt", branch="atlas/vp-7-src")
        g = create_grant(project_id="p-nowt", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        client = TestClient(create_app(load_settings()))
        resp = client.post(f"/api/v1/checkpoints/{cp['id']}/replay", json={"grant_id": g["id"]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "NO_TRUSTED_CHECKOUT")

    def test_replay_preview_shows_pattern_not_exact_branch(self):
        """finding 2: preview показывает ПАТТЕРН ветки, не конкретное случайное имя,
        которое реальный replay не использует (согласованность UI/API-истины)."""
        from atlas_core.timemachine import replay_preview
        cp = self._ckpt()
        prev = replay_preview(cp["id"])
        self.assertNotIn("target_branch", prev)                # нет обещания точного имени
        self.assertIn("<уникальный-токен>", prev["target_branch_pattern"])

    # --- call-12 fix (finding 1): local_git replay требует workspace-scope ---
    def test_replay_local_git_requires_workspace_scope(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        repo, base, head = self._repo_with_base_head()
        cp = self._ckpt(project_id="p", branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        # repo-имя не выводимо (нет github Project) → grant с произвольными allowed_repos,
        # но БЕЗ workspace, НЕ разрешает мутацию checkout (fail-closed exact-scope).
        g_no = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                            allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g_no["id"], repo_path=repo)
        self.assertEqual(cm.exception.code, "WORKSPACE_NOT_ALLOWED")
        # grant, явно перечисляющий этот workspace, разрешает.
        g_ok = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                            allowed_repos=["a/b"], allowed_bases=["main"],
                            workspace_allowlist=[repo], reason="r")
        res = replay(cp["id"], grant_id=g_ok["id"], repo_path=repo)
        self.assertTrue(res["new_branch"].startswith("atlas/replay-"))

    # --- call-12 fix (finding 4): checkpoint без head_sha → fail-closed ---
    def test_replay_headless_checkpoint_fails_closed(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        repo, base, _head = self._repo_with_base_head()
        cp = self._ckpt(project_id="p", branch="atlas/vp-7-src", base_sha=base, head_sha="")
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"],
                         workspace_allowlist=[repo], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"], repo_path=repo)
        self.assertEqual(cm.exception.code, "CHECKPOINT_NO_HEAD")

    # --- call-12 fix (finding 2): compare endpoint tampered → 409 INVALID_EVIDENCE ---
    def test_compare_endpoint_tampered_returns_invalid_evidence(self):
        from atlas_core.app import create_app
        from atlas_core.db import session_scope
        from atlas_core.orm import Checkpoint
        from atlas_core.settings import load_settings
        from starlette.testclient import TestClient
        a = self._ckpt(head_sha="H1")
        b = self._ckpt(head_sha="H2")
        client = TestClient(create_app(load_settings()))
        ok = client.get(f"/api/v1/checkpoints/compare?a={a['id']}&b={b['id']}")
        self.assertEqual(ok.status_code, 200)
        with session_scope() as s:
            s.get(Checkpoint, a["id"]).head_sha = "TAMPERED"
            s.commit()
        bad = client.get(f"/api/v1/checkpoints/compare?a={a['id']}&b={b['id']}")
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(bad.json()["error"]["code"], "INVALID_EVIDENCE")

    def test_replay_new_run_safe_branch_no_rewrite(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import replay
        d = tempfile.mkdtemp(prefix="atlas-tm-")
        repo = str(Path(d) / "repo")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], capture_output=True)
        for k, v in (("user.name", "CodeVinci"), ("user.email", "c@example.invalid")):
            subprocess.run(["git", "-C", repo, "config", k, v], capture_output=True)
        Path(repo, "a").write_text("1")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "b"], capture_output=True)
        base = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        subprocess.run(["git", "-C", repo, "checkout", "-qb", "atlas/vp-7-src"], capture_output=True)
        Path(repo, "b").write_text("2")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "w"], capture_output=True)
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        cp = self._ckpt(branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"],
                         workspace_allowlist=[repo], reason="r")
        res = replay(cp["id"], grant_id=g["id"], repo_path=repo)
        self.assertTrue(res["new_run_id"])
        self.assertTrue(res["new_branch"].startswith("atlas/replay-"))
        self.assertFalse(res["source_rewritten"])
        src_head = subprocess.run(["git", "-C", repo, "rev-parse", "atlas/vp-7-src"],
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(src_head, head)  # источник не переписан

    def test_replay_refuses_stale_grant(self):
        from atlas_core.autonomy import create_grant, revoke_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt()
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        revoke_grant(g["id"], by="owner")
        with self.assertRaises(TimeMachineError):
            replay(cp["id"], grant_id=g["id"])

    # --- Fix5: replay требует capability repo_write в scope (не только «свежий») ---
    def test_replay_refuses_grant_without_repo_write(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt()
        # свежий grant, но БЕЗ repo_write capability → replay денит
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_read"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"])
        self.assertIn(cm.exception.code, ("CAPABILITY_MISSING", "REPO_NOT_ALLOWED"))

    def test_replay_refuses_when_emergency_active(self):
        from atlas_core import emergency
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt()
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        emergency.engage(reason="test")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"])
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")

    # --- call-15 fix (finding 2): Stop, начавшийся ПОСЛЕ первичной проверки, но ДО ---
    # --- создания Run, прерывает replay; orphan replay-ветка откатывается, Run нет. ---
    def test_replay_emergency_race_before_run_denies(self):
        from atlas_core import emergency, timemachine
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        repo, base, head = self._repo_with_base_head()
        cp = self._ckpt(branch="atlas/vp-7-src", base_sha=base, head_sha=head)
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"],
                         workspace_allowlist=[repo], reason="r")
        calls = {"n": 0}
        real = emergency.blocks_new_jobs

        def flaky():   # False на первичной проверке, True на повторном барьере перед Run
            calls["n"] += 1
            return calls["n"] > 1

        timemachine.emergency.blocks_new_jobs = flaky
        try:
            with self.assertRaises(TimeMachineError) as cm:
                replay(cp["id"], grant_id=g["id"], repo_path=repo)
        finally:
            timemachine.emergency.blocks_new_jobs = real
        self.assertEqual(cm.exception.code, "EMERGENCY_STOP")
        # orphan replay-ветка откатена; source-ветка не тронута; новый Run не создан.
        left = subprocess.run(["git", "-C", repo, "branch", "--list", "atlas/replay-*"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "", "replay-ветка должна быть удалена")
        src = subprocess.run(["git", "-C", repo, "rev-parse", "atlas/vp-7-src"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(src, head)

    # --- Bypass B: scope выводится из checkpoint/project, каллер не расширяет ---
    def test_replay_grant_for_other_project_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt(project_id="projA")
        # grant привязан к другому проекту → PROJECT_MISMATCH
        g = create_grant(project_id="projB", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"])
        self.assertEqual(cm.exception.code, "PROJECT_MISMATCH")

    def test_replay_repo_derived_from_project_not_caller(self):
        # checkpoint принадлежит github-проекту с repo owner/A; grant разрешает только
        # owner/B. Каллер, передав repo="owner/B", НЕ может расширить scope → deny.
        from datetime import datetime, timezone

        from atlas_core.autonomy import create_grant
        from atlas_core.db import session_scope
        from atlas_core.orm import Project
        from atlas_core.timemachine import TimeMachineError, replay
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Project(id="pjgh", name="gh", source_kind="github",
                          source_location="https://github.com/owner/A", source_ref="owner/A",
                          status="connected", created_at=now, updated_at=now))
            s.commit()
        cp = self._ckpt(project_id="pjgh")
        g = create_grant(project_id="pjgh", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["owner/B"], allowed_bases=["main"], reason="r")
        # доверенный repo (owner/A) не в allowlist grant (owner/B) → REPO_NOT_ALLOWED
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"], repo="owner/B")
        self.assertIn(cm.exception.code, ("REPO_NOT_ALLOWED", "REPO_MISMATCH"))

    def _github_project(self, pid="pjgh", repo="owner/A"):
        from datetime import datetime, timezone

        from atlas_core.db import session_scope
        from atlas_core.orm import Project
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(Project(id=pid, name="gh", source_kind="github",
                          source_location=f"https://github.com/{repo}", source_ref=repo,
                          status="connected", created_at=now, updated_at=now))
            s.commit()

    # #2 из 9: другой repo с ОПУЩЕННЫМ caller repo → всё равно deny (repo выводится).
    def test_replay_another_repo_caller_omitted_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        self._github_project("pjgh", "owner/A")
        cp = self._ckpt(project_id="pjgh")
        g = create_grant(project_id="pjgh", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["owner/B"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"])  # caller repo опущен
        self.assertEqual(cm.exception.code, "REPO_NOT_ALLOWED")

    # #3-вариант: caller repo при невыводимом доверенном repo → REPO_NOT_DERIVABLE.
    def test_replay_caller_repo_not_derivable_denied(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt(project_id="p")  # проект без github-source → repo не выводим
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["x/y"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"], repo="x/y")
        self.assertEqual(cm.exception.code, "REPO_NOT_DERIVABLE")

    # #5: base от каллера отвергается (не выводима из checkpoint) — не «тихо пропущена».
    def test_replay_caller_base_rejected(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt(project_id="p")
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"], base="release")
        self.assertEqual(cm.exception.code, "BASE_NOT_DERIVABLE")

    # #7: environment от каллера отвергается (replay не деплоит).
    def test_replay_caller_environment_rejected(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import TimeMachineError, replay
        cp = self._ckpt(project_id="p")
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        with self.assertRaises(TimeMachineError) as cm:
            replay(cp["id"], grant_id=g["id"], environment="prod")
        self.assertEqual(cm.exception.code, "ENVIRONMENT_NOT_DERIVABLE")

    # #4/#6: опущенные base/environment — штатный replay (не bypass, deny не срабатывает).
    def test_replay_omitted_base_and_environment_ok(self):
        from atlas_core.autonomy import create_grant
        from atlas_core.timemachine import replay
        cp = self._ckpt(project_id="p")
        g = create_grant(project_id="p", mode="AUTONOMOUS", capabilities=["repo_write"],
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
        res = replay(cp["id"], grant_id=g["id"])  # base/environment опущены
        self.assertTrue(res["new_run_id"])
        self.assertFalse(res["source_rewritten"])

    def test_compare_reports_differences(self):
        from atlas_core.timemachine import compare
        a = self._ckpt(head_sha="HEAD_A", profile_alias="codex-plus-01")
        b = self._ckpt(head_sha="HEAD_B", profile_alias="claude-pro-01")
        c = compare(a["id"], b["id"])
        self.assertTrue(c["any_change"])
        self.assertTrue(c["diffs"]["head_sha"]["changed"])
        self.assertTrue(c["diffs"]["profile"]["changed"])

    def test_rollback_preview_readonly_unavailable_without_grant(self):
        from atlas_core.timemachine import rollback_preview
        cp = self._ckpt()
        rb = rollback_preview(cp["id"])
        self.assertTrue(rb["read_only"])
        self.assertFalse(rb["available"])


# ---------------------------------------------------------------------------
class TestAuthHealth(VP7Base):
    def test_normalize_and_persist_readonly(self):
        from atlas_core.agent_registry import ProfileService
        from atlas_core.auth_health import run_auth_health
        from atlas_core.profiles import Profile
        svc = ProfileService()
        aliases = [("codex-plus-01", "codex", "atlas-cx01"),
                   ("claude-pro-01", "claude", "atlas-cl01")]
        for a, p, u in aliases:
            svc.upsert_profile(a, p, unix_label=u)

        class FakeReg:
            def list(self, provider=None):
                return [Profile(alias=a, provider=p, root_path="/x", runtime_user=u,
                                executable_path="/x/bin") for a, p, u in aliases]

        def prober(prof):
            m = {"codex-plus-01": {"authenticated": True, "state": "READY"},
                 "claude-pro-01": {"authenticated": False, "state": "AUTH_EXPIRED"}}
            return {"cli_version": "v", "auth": m[prof.alias]}

        rep = run_auth_health(registry=FakeReg(), prober=prober)
        by = {o["alias"]: o["auth_status"] for o in rep}
        self.assertEqual(by["codex-plus-01"], "READY")
        self.assertEqual(by["claude-pro-01"], "AUTH_EXPIRED")

    def test_unknown_when_cli_absent(self):
        from atlas_core.auth_health import normalize_state
        self.assertEqual(normalize_state({"authenticated": False, "state": "CLI_ABSENT"})[0], "UNKNOWN")
