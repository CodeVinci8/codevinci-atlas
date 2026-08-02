"""VP-3 Product Map: intake→draft, truth/evidence, версии/concurrency, решения,
approval, node/edge-валидация, один активный VP, parking, экспорт, portfolio,
durability и идемпотентность (Master Spec §36).

Проверяет реальную логику сервиса против временной SQLite-БД (не моки).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP3Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base, GitBaseline, Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        from atlas_core.db import session_scope
        self.bl_hash = "sha256:" + "a" * 64
        with session_scope() as s:
            s.add(Project(id="proj_t", name="Тест", source_kind="local_git",
                          source_location="/x", status="connected",
                          created_at=_now(), updated_at=_now()))
            s.add(GitBaseline(id="bl_t", project_id="proj_t", branch="main", head="h",
                              content_hash=self.bl_hash, observed_at=_now()))
            # disconnected project (для PROJECT_NOT_AVAILABLE)
            s.add(Project(id="proj_off", name="Off", source_kind="local_git",
                          source_location="/y", status="disconnected",
                          created_at=_now(), updated_at=_now()))
            s.commit()
        from atlas_core.productmap import ProductMapService
        self.svc = ProductMapService()

    def _intake(self, **over):
        body = {"idea": "идея", "target_user": "solo", "desired_result": "карта\nэкспорт",
                "risks": ["scope creep"], "baseline_refs": ["bl_t"],
                "parking_suggestions": [{"title": "мобилка", "reason": "позже",
                                         "return_condition": "после MVP"}]}
        body.update(over)
        return self.svc.submit_intake("proj_t", body)


class TestIntakeAndTruth(VP3Base):
    def test_intake_creates_draft_brief_map_decisions(self):
        st = self._intake()
        self.assertEqual(st["stage"], "draft")
        self.assertEqual(st["brief"]["version"], 1)
        self.assertEqual(len(st["decisions"]), 3)
        self.assertTrue(st["map"])
        # truth statuses присутствуют и различимы
        statuses = {n["truth_status"] for n in st["map"]["nodes"]}
        self.assertIn("OWNER_PROVIDED", statuses)
        self.assertTrue({"HYPOTHESIS", "INFERRED", "UNKNOWN"} & statuses)
        # parking из intake
        self.assertEqual(len(st["parking_lot"]), 1)

    def test_project_not_available(self):
        from atlas_core.productmap import ProductMapError
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.submit_intake("proj_off", {"idea": "x"})
        self.assertEqual(ctx.exception.code, "PROJECT_NOT_AVAILABLE")

    def test_intake_requires_content(self):
        from atlas_core.productmap import ProductMapError
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.submit_intake("proj_t", {})
        self.assertEqual(ctx.exception.code, "INTAKE_INVALID")

    def test_secret_marker_rejected(self):
        from atlas_core.productmap import ProductMapError
        from atlas_core.redaction import SECRET_MARKER
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.submit_intake("proj_t", {"idea": f"secret {SECRET_MARKER} here"})
        self.assertEqual(ctx.exception.code, "INTAKE_INVALID")

    def test_token_redacted_in_storage(self):
        st = self._intake(idea="key ghp_ABCDEFGHIJKLMNOPQRSTUVWX here")
        content = st["brief"]["content"]["product_statement"]
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWX", content)
        self.assertIn("[REDACTED]", content)


class TestEvidenceAndVersions(VP3Base):
    def test_verified_without_evidence_rejected(self):
        from atlas_core.productmap import ProductMapError
        st = self._intake()
        bid = st["brief"]["id"]
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.revise_brief("proj_t", bid, {"confirmed_facts": [
                {"text": "f", "truth_status": "VERIFIED", "evidence_ref": "bl_t",
                 "evidence_hash": "sha256:" + "b" * 64}]}, expected_version=1)
        self.assertEqual(ctx.exception.code, "EVIDENCE_INVALID")

    def test_verified_with_evidence_and_parent_link(self):
        st = self._intake()
        bid = st["brief"]["id"]
        r = self.svc.revise_brief("proj_t", bid, {"confirmed_facts": [
            {"text": "f", "truth_status": "VERIFIED", "evidence_ref": "bl_t",
             "evidence_hash": self.bl_hash}]}, expected_version=1)
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["parent_id"], bid)
        # факт остался VERIFIED
        self.assertEqual(r["content"]["confirmed_facts"][0]["truth_status"], "VERIFIED")

    def test_v1_immutable(self):
        st = self._intake()
        h1 = st["brief"]["content_hash"]
        self.svc.revise_brief("proj_t", st["brief"]["id"], {"promised_result": "new"},
                              expected_version=1)
        again = self.svc.get_brief("proj_t", version=1)
        self.assertEqual(again["content_hash"], h1)

    def test_stale_write_rejected(self):
        from atlas_core.productmap import ProductMapError
        st = self._intake()
        bid = st["brief"]["id"]
        self.svc.revise_brief("proj_t", bid, {"promised_result": "v2"}, expected_version=1)
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.revise_brief("proj_t", bid, {"promised_result": "conflict"},
                                  expected_version=1)
        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")

    def test_brief_field_level_diff(self):
        st = self._intake()
        self.svc.revise_brief("proj_t", st["brief"]["id"],
                              {"promised_result": "изменённый результат"}, expected_version=1)
        d = self.svc.diff_briefs("proj_t", 1, 2)
        self.assertIn("promised_result", d["content"]["changed"])
        self.assertEqual(d["content"]["changed"]["promised_result"]["to"], "изменённый результат")

    def test_idempotent_intake_no_duplicate(self):
        self.svc.submit_intake("proj_t", {"idea": "x"}, idempotency_key="k1")
        self.svc.submit_intake("proj_t", {"idea": "x"}, idempotency_key="k1")
        self.assertEqual(len(self.svc.list_briefs("proj_t")), 1)


class TestDecisionsAndApproval(VP3Base):
    def _resolve_required(self):
        for d in self.svc.list_decisions("proj_t"):
            if d["required"]:
                self.svc.decide("proj_t", d["id"], "accept", expected_version=d["version"])

    def test_decision_accept_reject_history(self):
        self._intake()
        d = next(x for x in self.svc.list_decisions("proj_t") if x["required"])
        self.svc.decide("proj_t", d["id"], "reject", note="не сейчас", expected_version=d["version"])
        got = self.svc.get_decision("proj_t", d["id"])
        self.assertEqual(got["status"], "rejected")
        self.assertEqual(got["note"], "не сейчас")
        self.assertGreaterEqual(len(got["history"]), 2)

    def test_decision_version_conflict(self):
        from atlas_core.productmap import ProductMapError
        self._intake()
        d = next(x for x in self.svc.list_decisions("proj_t") if x["required"])
        self.svc.decide("proj_t", d["id"], "accept", expected_version=d["version"])
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.decide("proj_t", d["id"], "reject", expected_version=d["version"])
        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")

    def test_approval_blocked_unresolved(self):
        from atlas_core.productmap import ProductMapError
        st = self._intake()
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.approve_brief("proj_t", st["brief"]["id"], expected_version=1)
        self.assertEqual(ctx.exception.code, "DECISION_UNRESOLVED")

    def test_approval_binds_hashes(self):
        st = self._intake()
        self._resolve_required()
        ap = self.svc.approve_brief("proj_t", st["brief"]["id"], expected_version=1)
        b = self.svc.get_brief("proj_t", version=1)
        self.assertEqual(ap["brief_hash"], b["content_hash"])
        self.assertTrue(ap["map_version_id"])
        self.assertTrue(ap["envelope_hash"])
        self.assertTrue(ap["decisions_hash"])

    def test_approval_envelope_invalid(self):
        from atlas_core.productmap import ProductMapError
        st = self._intake()
        self._resolve_required()
        # перезаписать envelope на противоречивый (overlap in/out)
        self.svc.revise_brief("proj_t", st["brief"]["id"],
                              {"envelope": {"in_scope": ["x"], "out_of_scope": ["x"]}},
                              expected_version=1)
        latest = self.svc.list_briefs("proj_t")[-1]
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.approve_brief("proj_t", latest["id"], expected_version=2)
        self.assertEqual(ctx.exception.code, "ENVELOPE_INVALID")

    def test_revise_after_approval_keeps_approved(self):
        # Approve v1, затем правка → v2 draft. Утверждённой остаётся v1
        # (Approval-record неизменяем; новый черновик не «разутверждает»).
        st = self._intake()
        self._resolve_required()
        self.svc.approve_brief("proj_t", st["brief"]["id"], expected_version=1)
        self.svc.revise_brief("proj_t", st["brief"]["id"], {"main_scenario": "s"},
                              expected_version=1)
        state = self.svc.get_state("proj_t")
        self.assertEqual(state["approved_brief_version"], 1)
        self.assertEqual(state["brief"]["version"], 2)  # последняя — черновик v2
        self.assertEqual(self.svc.get_brief("proj_t", version=1)["status"], "approved")


class TestMapAndVp(VP3Base):
    def test_dangling_edge_rejected(self):
        from atlas_core.productmap import ProductMapError
        self._intake()
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.create_map_version("proj_t",
                [{"node_key": "a", "node_type": "goal", "title": "A"}],
                [{"src_key": "a", "dst_key": "ghost", "edge_type": "next"}],
                expected_version=1)
        self.assertEqual(ctx.exception.code, "MAP_INVALID")

    def test_bad_node_type_and_cycle_rejected(self):
        from atlas_core.productmap import ProductMapError
        self._intake()
        with self.assertRaises(ProductMapError):
            self.svc.create_map_version("proj_t",
                [{"node_key": "a", "node_type": "bogus", "title": "A"}], [], expected_version=1)
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.create_map_version("proj_t",
                [{"node_key": "a", "node_type": "goal", "title": "A"},
                 {"node_key": "b", "node_type": "goal", "title": "B"}],
                [{"src_key": "a", "dst_key": "b", "edge_type": "next"},
                 {"src_key": "b", "dst_key": "a", "edge_type": "next"}], expected_version=1)
        self.assertEqual(ctx.exception.code, "MAP_INVALID")

    def test_map_diff(self):
        self._intake()
        self.svc.create_map_version("proj_t",
            [{"node_key": "goal", "node_type": "goal", "title": "Новая"},
             {"node_key": "na", "node_type": "next_action", "title": "шаг"}],
            [{"src_key": "goal", "dst_key": "na", "edge_type": "next"}], expected_version=1)
        d = self.svc.diff_maps("proj_t", 1, 2)
        self.assertTrue(d["nodes"]["removed"])
        self.assertTrue(d["nodes"]["added"] or d["nodes"]["changed"])

    def test_one_active_vp_second_denied(self):
        from atlas_core.productmap import ProductMapError
        self._intake()
        self.svc.activate_vp("proj_t", "VP-3")
        with self.assertRaises(ProductMapError) as ctx:
            self.svc.activate_vp("proj_t", "VP-4")
        self.assertEqual(ctx.exception.code, "ACTIVE_VP_CONFLICT")
        self.assertEqual(self.svc.get_active_vp("proj_t")["active_vp"], "VP-3")

    def test_one_active_vp_concurrent(self):
        from atlas_core.productmap import ProductMapError
        self._intake()
        results = {"ok": 0, "conflict": 0, "other": 0}
        lock = threading.Lock()

        def worker(k):
            try:
                self.svc.activate_vp("proj_t", f"VP-{k}")
                with lock:
                    results["ok"] += 1
            except ProductMapError as e:
                with lock:
                    results["conflict" if e.code == "ACTIVE_VP_CONFLICT" else "other"] += 1

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results["ok"], 1)
        self.assertEqual(results["other"], 0)
        self.assertEqual(results["conflict"], 7)


class TestParkingExportPortfolio(VP3Base):
    def test_parking_survives_versioning_and_outside_scope(self):
        st = self._intake()
        self.svc.add_parking_item("proj_t", "ещё идея", reason="r", return_condition="c")
        self.svc.revise_brief("proj_t", st["brief"]["id"], {"promised_result": "v2"},
                              expected_version=1)
        parking = self.svc.list_parking("proj_t")
        self.assertEqual(len(parking), 2)  # пережили новую версию
        # не входят в mvp_scope брифа
        latest = self.svc.get_brief("proj_t")
        titles = {p["title"] for p in parking}
        self.assertFalse(titles & set(latest["content"]["mvp_scope"]))

    def test_export_deterministic_and_no_secret(self):
        from atlas_core import productmap_export as px
        from atlas_core.redaction import SECRET_MARKER
        self._intake()
        p1 = self.svc.export_payload("proj_t")
        p2 = self.svc.export_payload("proj_t")
        self.assertEqual(p1["_generated"]["payload_hash"], p2["_generated"]["payload_hash"])
        md = px.render_markdown(p1)
        js = px.render_json(p1)
        self.assertNotIn(SECRET_MARKER, md)
        self.assertNotIn(SECRET_MARKER, js)
        self.assertIn("Product Map", md)

    def test_portfolio_unknown_values(self):
        # proj_t без intake → intake_pending, active_vp UNKNOWN
        pf = {r["project_id"]: r for r in self.svc.portfolio()}
        self.assertIn("proj_t", pf)
        self.assertEqual(pf["proj_t"]["active_vp"], "UNKNOWN")
        self.assertEqual(pf["proj_t"]["stage"], "intake_pending")

    def test_restart_durability(self):
        st = self._intake()
        self._resolve_and_approve(st["brief"]["id"])
        # «рестарт Core»: reinit engine на тот же файл
        from atlas_core.db import init_engine
        init_engine(self.settings.db_url, self.settings.db_path)
        from atlas_core.productmap import ProductMapService
        svc2 = ProductMapService()
        state = svc2.get_state("proj_t")
        self.assertEqual(state["approved_brief_version"], 1)
        self.assertTrue(svc2.latest_approval("proj_t"))

    def _resolve_and_approve(self, bid):
        for d in self.svc.list_decisions("proj_t"):
            if d["required"]:
                self.svc.decide("proj_t", d["id"], "accept", expected_version=d["version"])
        self.svc.approve_brief("proj_t", bid, expected_version=1)


if __name__ == "__main__":
    import unittest
    unittest.main()
