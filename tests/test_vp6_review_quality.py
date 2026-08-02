"""VP-6 Review & Quality (Master Spec §18, §39): impact engine, Evidence Cache,
SHA-bound ReviewPackage + инвалидация фактом, Quality Firewall gates, вердикт,
fix-loop, manual audit (read-only), waiver (non-waivable), profile reconcile."""

from __future__ import annotations

import os
import tempfile

from atlas_test_base import AtlasTestCase


class VP6Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        from sqlalchemy import text
        with get_engine().begin() as c:
            c.exec_driver_sql("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
            c.exec_driver_sql("DELETE FROM alembic_version")
            c.execute(text("INSERT INTO alembic_version VALUES ('0006_review_quality')"))

    def _mk_pkg(self, **kw):
        from atlas_core.reviewpkg import ReviewInputs, build_review_package
        kw.setdefault("project_id", "p")
        return build_review_package(ReviewInputs(**kw))

    def _review(self, pkg, **ctx_kw):
        from atlas_core.firewall import FirewallContext
        from atlas_core.quality import QualityService
        from atlas_core.reviewpkg import ReviewFacts
        facts_kw = ctx_kw.pop("facts", {})
        ctx = FirewallContext(package=pkg, **ctx_kw)
        return QualityService().review(pkg, ctx, ReviewFacts(**facts_kw))


class TestImpactEngine(VP6Base):
    def test_classes_and_groups(self):
        from atlas_core.impact import classify_impact
        self.assertEqual(classify_impact(["docs/X.md"]).impact_class, "DOC_ONLY")
        self.assertFalse(classify_impact(["docs/X.md"]).full_regression)
        self.assertEqual(classify_impact(["apps/core/atlas_core/optimizer.py"]).impact_class, "LOCAL")
        self.assertEqual(classify_impact(["apps/core/atlas_core/api_reviews.py"]).impact_class, "INTEGRATION")
        self.assertEqual(classify_impact(["apps/core/atlas_core/orm.py"]).impact_class, "SHARED")
        hr = classify_impact(["apps/core/atlas_core/migrations/versions/0006_review_quality.py"])
        self.assertEqual(hr.impact_class, "HIGH_RISK")
        self.assertTrue(hr.full_regression)
        self.assertIn("security", hr.check_groups)

    def test_mixed_takes_max(self):
        from atlas_core.impact import classify_impact
        r = classify_impact(["docs/X.md", "apps/core/atlas_core/orm.py"])
        self.assertEqual(r.impact_class, "SHARED")

    def test_risk_trigger_forces_high_risk(self):
        from atlas_core.impact import classify_impact
        r = classify_impact(["docs/X.md"], risk_trigger="релиз-миграция")
        self.assertEqual(r.impact_class, "HIGH_RISK")
        self.assertTrue(r.full_regression)


class TestEvidenceCache(VP6Base):
    def _c(self, **kw):
        from atlas_core.evidence_cache import CacheComponents
        base = dict(sha="h1", command="pytest", command_version="8", input_hash="i1",
                    environment="py314", scope="LOCAL")
        base.update(kw)
        return CacheComponents(**base)

    def test_reuse_exact(self):
        from atlas_core.evidence_cache import EvidenceCache
        ec = EvidenceCache()
        ec.store(self._c(), passed=True, result={"ok": True}, reason="run")
        r = ec.try_reuse(self._c())
        self.assertIsNotNone(r)
        self.assertTrue(r["passed"])

    def test_changed_component_misses(self):
        from atlas_core.evidence_cache import EvidenceCache
        ec = EvidenceCache()
        ec.store(self._c(), passed=True, result={}, reason="run")
        self.assertIsNone(ec.try_reuse(self._c(sha="h2")))
        self.assertIsNone(ec.try_reuse(self._c(environment="py313")))
        self.assertIsNone(ec.try_reuse(self._c(input_hash="i2")))

    def test_stale_head_refused(self):
        from atlas_core.evidence_cache import EvidenceCache
        ec = EvidenceCache()
        ec.store(self._c(), passed=True, result={}, reason="run")
        n = ec.invalidate_stale("hZ")
        self.assertGreaterEqual(n, 1)
        self.assertIsNone(ec.try_reuse(self._c()))


class TestReviewPackage(VP6Base):
    def test_content_hash_deterministic(self):
        from atlas_core.productmap import content_hash
        p1 = self._mk_pkg(head_sha="h", claims=[{"claim": "x"}])
        p2 = self._mk_pkg(head_sha="h", claims=[{"claim": "x"}])
        # разные id, но одинаковое immutable-содержимое → одинаковый content_hash
        self.assertEqual(p1["content_hash"], p2["content_hash"])
        self.assertTrue(content_hash({"a": 1}).startswith("sha256:"))

    def test_stale_sha_invalid(self):
        from atlas_core.reviewpkg import ReviewFacts, validate_review_package
        pkg = self._mk_pkg(head_sha="OLD")
        ok, code, _ = validate_review_package(pkg["id"], ReviewFacts(current_head="NEW"))
        self.assertFalse(ok)
        self.assertEqual(code, "STALE_SHA")

    def test_artifact_tamper_invalid(self):
        from atlas_core.reviewpkg import ReviewFacts, validate_review_package
        pkg = self._mk_pkg(head_sha="H", artifact_hashes=[{"path": "a", "sha": "sha256:good"}])
        ok, code, _ = validate_review_package(
            pkg["id"], ReviewFacts(current_head="H", artifacts={"a": "sha256:BAD"}))
        self.assertFalse(ok)
        self.assertEqual(code, "ARTIFACT_ALTERED")

    def test_missing_evidence_invalid(self):
        from atlas_core.reviewpkg import ReviewFacts, validate_review_package
        pkg = self._mk_pkg(head_sha="H", evidence_refs=["ev:1"])
        ok, code, _ = validate_review_package(
            pkg["id"], ReviewFacts(current_head="H", evidence_present=[]))
        self.assertFalse(ok)
        self.assertEqual(code, "MISSING_EVIDENCE")

    def test_work_order_mismatch_invalid(self):
        from atlas_core.reviewpkg import ReviewFacts, validate_review_package
        pkg = self._mk_pkg(head_sha="H", wo_key="wo-1")
        ok, code, _ = validate_review_package(
            pkg["id"], ReviewFacts(current_head="H", expected_wo_key="wo-2"))
        self.assertFalse(ok)
        self.assertEqual(code, "WORK_ORDER_MISMATCH")


class TestFirewallAndVerdict(VP6Base):
    def test_clean_pass(self):
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL", freshness={"b": "FRESH"})
        o = self._review(pkg, current_head="H", claim_ok=True, license_present=True,
                         freshness={"b": "FRESH"}, facts={"current_head": "H"})
        self.assertEqual(o.verdict, "PASS")

    def test_secret_blocks_nonwaivable(self):
        wt = tempfile.mkdtemp()
        with open(os.path.join(wt, "leak.py"), "w") as fh:
            fh.write('K="sk-ant-' + "A" * 40 + '"\n')
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        o = self._review(pkg, worktree=wt, current_head="H", claim_ok=True,
                         license_present=True, facts={"current_head": "H"})
        self.assertEqual(o.verdict, "BLOCKED")
        self.assertTrue(any(f["code"] == "SECRET_DETECTED" for f in o.findings))

    def test_false_claim_revise(self):
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        o = self._review(pkg, current_head="H", claim_ok=False, claim_detail="mismatch",
                         license_present=True, facts={"current_head": "H"})
        self.assertEqual(o.verdict, "REVISE")

    def test_ai_placeholder_found(self):
        wt = tempfile.mkdtemp()
        with open(os.path.join(wt, "s.py"), "w") as fh:
            fh.write("def f():\n    raise NotImplementedError  # TODO\n")
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        o = self._review(pkg, worktree=wt, current_head="H", claim_ok=True,
                         license_present=True, facts={"current_head": "H"})
        self.assertTrue(any(f["code"] == "AI_PLACEHOLDER" for f in o.findings))

    def test_finding_fields_present(self):
        wt = tempfile.mkdtemp()
        with open(os.path.join(wt, "s.py"), "w") as fh:
            fh.write("x = 1  # TODO\n")
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        o = self._review(pkg, worktree=wt, current_head="H", claim_ok=True,
                         license_present=True, facts={"current_head": "H"})
        f = next(x for x in o.findings if x["blocking"])
        for key in ("criterion", "location", "evidence", "action", "blocking",
                    "severity", "code", "source", "freshness"):
            self.assertIn(key, f)

    def test_license_absent_visible_owner_decision(self):
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        o = self._review(pkg, current_head="H", claim_ok=True, license_present=False,
                         facts={"current_head": "H"})
        self.assertTrue(any(f["code"] == "LICENSE_ABSENT_OWNER_DECISION" for f in o.findings))
        # info-finding не блокирует
        self.assertEqual(o.verdict, "PASS")


class TestFixLoopWaiverAudit(VP6Base):
    def test_second_revise_blocks(self):
        from atlas_core.quality import QualityService
        q = QualityService()
        v1, b1 = q.evaluate_fix_loop("r", "run", "p", 1, "REVISE")
        v2, b2 = q.evaluate_fix_loop("r", "run", "p", 2, "REVISE")
        self.assertEqual((v1, b1), ("REVISE", False))
        self.assertEqual((v2, b2), ("BLOCKED", True))

    def test_waiver_nonwaivable_rejected(self):
        from atlas_core.quality import QualityService
        q = QualityService()
        # создаём finding SECRET_DETECTED через firewall
        wt = tempfile.mkdtemp()
        with open(os.path.join(wt, "leak.py"), "w") as fh:
            fh.write('K="ghp_' + "B" * 36 + '"\n')
        pkg = self._mk_pkg(head_sha="H", impact_class="LOCAL")
        self._review(pkg, worktree=wt, current_head="H", claim_ok=True,
                     license_present=True, facts={"current_head": "H"})
        fid = next(f["id"] for f in q.list_findings(pkg["id"]) if f["code"] == "SECRET_DETECTED")
        w = q.waiver(pkg["id"], fid, "p", reason="x", scope="s", actor="owner",
                     expiry="2026-09-01", review_condition="c")
        self.assertFalse(w["waivable"])
        self.assertEqual(w["rejected_code"], "NON_WAIVABLE_RULE")

    def test_manual_audit_read_only(self):
        from atlas_core.quality import QualityService
        q = QualityService()
        out = q.manual_audit("rpkg", "p", "diff", "scope", {"findings": []})
        self.assertTrue(out["read_only"])


class TestProfileReconcile(VP6Base):
    def test_reconcile_idempotent_no_raw_path(self):
        import json

        from atlas_core.profile_reconcile import reconcile_profiles
        from atlas_core.profiles import ProfileRegistry
        reg_path = self.data_dir / "profiles" / "registry.json"
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps({"profiles": {
            "codex-plus-01": {"alias": "codex-plus-01", "provider": "codex",
                              "root_path": "/x/codex-plus-01", "state": "AUTH_REQUIRED",
                              "runtime_user": "atlas-cx01"},
            "claude-pro-01": {"alias": "claude-pro-01", "provider": "claude",
                              "root_path": "/x/claude-pro-01", "state": "AUTH_REQUIRED",
                              "runtime_user": "atlas-cl01"},
        }}), encoding="utf-8")
        reg = ProfileRegistry(str(reg_path))
        r1 = reconcile_profiles(reg)
        self.assertEqual(r1.total, 2)
        self.assertEqual(len(r1.created), 2)
        r2 = reconcile_profiles(reg)  # идемпотентно
        self.assertEqual(len(r2.created), 0)
        self.assertEqual(len(r2.updated), 2)
        # safe-метаданные: raw path не хранится
        from atlas_core.agent_registry import ProfileService
        for v in ProfileService().list_profiles():
            self.assertNotIn("/x/", str(v))
            self.assertTrue(v["state"] in ("AUTH_REQUIRED", "UNCONFIGURED"))
