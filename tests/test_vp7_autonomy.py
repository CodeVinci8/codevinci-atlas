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

    def test_squash_merge_after_pass_and_stale_head(self):
        from atlas_core.github_adapter import GitContractError
        sha = self._feature()
        self.ad.push_feature(self.wc, "atlas/vp-7-x")
        self.forge.set_checks(sha, "GREEN")
        pr = self.ad.create_pr(base="main", head_branch="atlas/vp-7-x", head_sha=sha,
                               title="VP-7 демо", body="тело")
        res = self.ad.squash_merge(pr["number"], expected_head=sha, message="VP-7: squash после PASS")
        self.assertTrue(res["merged"])
        self.assertNotEqual(self.forge.branch_head("main"), self.seed)  # base продвинулась
        with self.assertRaises(GitContractError):
            self.ad.squash_merge(pr["number"], expected_head="deadbeef", message="повтор")

    # --- Fix4: реальные write-операции списывают invocation-бюджет grant ---
    def test_push_consumes_budget_and_exhaustion_blocks(self):
        from atlas_core.autonomy import create_grant, get_grant
        from atlas_core.github_adapter import GitContractError
        g = create_grant(project_id="p", mode="STANDARD", capabilities=["push_feature"],
                         allowed_repos=["acme/demo"], allowed_bases=["main"],
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
                         allowed_repos=["a/b"], allowed_bases=["main"], reason="r")
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
