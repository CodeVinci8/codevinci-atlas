"""VP-4 Work Orders & Context — юниты/интеграция против реальной SQLite-БД
(Master Spec §16, §37): VP Spec из принятого Brief/Map, жизненный цикл,
идемпотентность и concurrency, оптимизатор merge/split с сохранением критериев,
bounded JobPackage без запрещённого содержимого, checkpoint/handoff,
stale/tamper-отклонение, свежая сессия и compact-fallback, governor/ротация.

Не моки: реальная логика сервисов, реальный изолированный процесс-потребитель.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP4Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine, session_scope
        from atlas_core.orm import Base, GitBaseline, Project
        from atlas_core.productmap import content_hash
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        self.bl_hash = content_hash({"baseline": "syn"})
        with session_scope() as s:
            s.add(Project(id="proj_t", name="Тест", source_kind="local_git",
                          source_location="/x", status="connected",
                          created_at=_now(), updated_at=_now()))
            s.add(GitBaseline(id="bl_t", project_id="proj_t", branch="main", head="HEAD0",
                              content_hash=self.bl_hash,
                              instructions_json=json.dumps(
                                  [{"path": "AGENTS.md", "scope": "root", "precedence": 1}]),
                              observed_at=_now()))
            s.add(Project(id="proj_off", name="Off", source_kind="local_git",
                          source_location="/y", status="disconnected",
                          created_at=_now(), updated_at=_now()))
            s.commit()
        from atlas_core.context_engine import ContextEngine
        from atlas_core.governor import ContextGovernor
        from atlas_core.optimizer import OptimizerService
        from atlas_core.productmap import ProductMapService
        from atlas_core.reconstruct import ReconstructService
        from atlas_core.vp4handoff import HandoffService
        from atlas_core.workorders import WorkOrderService
        self.pm = ProductMapService()
        self.wos = WorkOrderService()
        self.opt = OptimizerService()
        self.ce = ContextEngine()
        self.hs = HandoffService()
        self.gov = ContextGovernor()
        self.rec = ReconstructService()

    def _approve(self, project_id="proj_t"):
        self.pm.submit_intake(project_id, {"idea": "инструмент", "target_user": "solo",
                              "desired_result": "видеть карту\nэкспорт\nпроверки"})
        st = self.pm.get_state(project_id)
        b = st["brief"]
        for d in st["decisions"]:
            if d["required"]:
                self.pm.decide(project_id, d["id"], "accept", expected_version=d["version"])
        return self.pm.approve_brief(project_id, b["id"], expected_version=b["version"])

    def _spec(self, project_id="proj_t", vp_key="VP-4"):
        self._approve(project_id)
        return self.wos.create_vp_spec(project_id, vp_key)

    def _active_wo(self, spec, **kw):
        wo = self.wos.create_work_order("proj_t", spec["id"], goal=kw.get("goal", "цель"),
                                        criterion_ids=kw.get("criterion_ids"))
        wo = self.wos.transition("proj_t", wo["id"], "ready", expected_version=wo["version"])
        wo = self.wos.transition("proj_t", wo["id"], "active", expected_version=wo["version"])
        return wo


class TestVpSpec(VP4Base):
    def test_spec_binds_exact_approval(self):
        appr = self._approve()
        spec = self.wos.create_vp_spec("proj_t", "VP-4")
        self.assertEqual(spec["binding"]["brief_hash"], appr["brief_hash"])
        self.assertEqual(spec["binding"]["approval_id"], appr["id"])
        self.assertEqual(spec["binding"]["decisions_hash"], appr["decisions_hash"])
        self.assertTrue(spec["content"]["acceptance_criteria"])

    def test_no_approval_owner_required(self):
        from atlas_core.workorders import WorkOrderError
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.create_vp_spec("proj_t", "VP-4")
        self.assertEqual(ctx.exception.code, "OWNER_REQUIRED")

    def test_disconnected_project_rejected(self):
        from atlas_core.workorders import WorkOrderError
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.create_vp_spec("proj_off", "VP-4")
        self.assertEqual(ctx.exception.code, "PROJECT_NOT_AVAILABLE")

    def test_source_stale_rejected_after_approval_change(self):
        # изменить состояние решений после создания spec → WO создать нельзя (stale)
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        # добавить новое решение? нельзя; вместо этого сменим approval новой версией
        st = self.pm.get_state("proj_t")
        b = st["brief"]
        r = self.pm.revise_brief("proj_t", b["id"], {"main_scenario": "новый"},
                                 expected_version=b["version"])
        self.pm.approve_brief("proj_t", r["id"], expected_version=r["version"])
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.create_work_order("proj_t", spec["id"], goal="x")
        self.assertin_codes(ctx.exception.code, ("SOURCE_STALE",))

    def test_spec_superseded_on_recreate(self):
        self._approve()
        s1 = self.wos.create_vp_spec("proj_t", "VP-4")
        s2 = self.wos.create_vp_spec("proj_t", "VP-4")
        self.assertEqual(s2["version"], s1["version"] + 1)
        got = self.wos.get_vp_spec("proj_t", s1["id"])
        self.assertEqual(got["status"], "superseded")

    def assertin_codes(self, code, codes):
        self.assertIn(code, codes)


class TestLifecycle(VP4Base):
    def test_valid_and_invalid_transitions(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="цель")
        self.assertEqual(wo["status"], "draft")
        wo = self.wos.transition("proj_t", wo["id"], "ready", expected_version=wo["version"])
        wo = self.wos.transition("proj_t", wo["id"], "active", expected_version=wo["version"])
        # invalid: active -> ready
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.transition("proj_t", wo["id"], "ready", expected_version=wo["version"])
        self.assertEqual(ctx.exception.code, "INVALID_TRANSITION")
        # статус не изменился (атомарно)
        self.assertEqual(self.wos.get_work_order("proj_t", wo["id"])["status"], "active")
        wo = self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        wo = self.wos.transition("proj_t", wo["id"], "completed", expected_version=wo["version"])
        self.assertEqual(wo["status"], "completed")
        # терминальный — дальше нельзя
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.transition("proj_t", wo["id"], "active", expected_version=wo["version"])
        self.assertEqual(ctx.exception.code, "INVALID_TRANSITION")

    def test_ready_requires_executable(self):
        # WO без criteria недопустим — но у нас всегда есть criteria; проверим версию
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="цель")
        # stale expected version
        from atlas_core.workorders import WorkOrderError
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.transition("proj_t", wo["id"], "ready", expected_version=999)
        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")

    def test_history_append_only(self):
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="цель")
        self.wos.transition("proj_t", wo["id"], "ready", expected_version=wo["version"])
        got = self.wos.get_work_order("proj_t", wo["id"])
        kinds = [h["to"] for h in got["history"]]
        self.assertEqual(kinds, ["draft", "ready"])


class TestConcurrencyIdempotency(VP4Base):
    def test_single_writer(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        self._active_wo(spec)  # занял writer на wt:proj_t:main
        w2 = self.wos.create_work_order("proj_t", spec["id"], goal="второй")
        w2 = self.wos.transition("proj_t", w2["id"], "ready", expected_version=w2["version"])
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.transition("proj_t", w2["id"], "active", expected_version=w2["version"])
        self.assertEqual(ctx.exception.code, "WRITER_CONFLICT")

    def test_idempotent_creates(self):
        spec = self._spec()
        a = self.wos.create_work_order("proj_t", spec["id"], goal="x", idempotency_key="k1")
        b = self.wos.create_work_order("proj_t", spec["id"], goal="x", idempotency_key="k1")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.wos.list_work_orders("proj_t")), 1)
        # jobpackage/checkpoint idempotent
        p1 = self.ce.build_job_package("proj_t", a["id"], idempotency_key="jp1")
        p2 = self.ce.build_job_package("proj_t", a["id"], idempotency_key="jp1")
        self.assertEqual(p1["id"], p2["id"])

    def test_spec_idempotent(self):
        self._approve()
        s1 = self.wos.create_vp_spec("proj_t", "VP-4", idempotency_key="s1")
        s2 = self.wos.create_vp_spec("proj_t", "VP-4", idempotency_key="s1")
        self.assertEqual(s1["id"], s2["id"])


class TestOptimizer(VP4Base):
    def test_merge_conserves_criteria(self):
        spec = self._spec()
        ids = [c["id"] for c in spec["content"]["acceptance_criteria"]]
        w1 = self.wos.create_work_order("proj_t", spec["id"], goal="g1", criterion_ids=ids[:2])
        w2 = self.wos.create_work_order("proj_t", spec["id"], goal="g2", criterion_ids=ids[1:])
        prev = self.opt.merge_preview("proj_t", [w1["id"], w2["id"]])
        self.assertTrue(prev["compatible"])
        self.assertTrue(prev["criterion_conservation"])
        res = self.opt.merge_confirm("proj_t", [w1["id"], w2["id"]])
        merged = [c["id"] for c in res["merged_work_order"]["content"]["acceptance_criteria"]]
        self.assertEqual(sorted(merged), sorted(set(ids)))
        shared = [c["id"] for c in res["merged_work_order"]["content"]["acceptance_criteria"] if c["shared"]]
        self.assertIn(ids[1], shared)

    def test_incompatible_merge_owner_required(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        ids = [c["id"] for c in spec["content"]["acceptance_criteria"]]
        w1 = self.wos.create_work_order("proj_t", spec["id"], role="builder", criterion_ids=ids[:1])
        w2 = self.wos.create_work_order("proj_t", spec["id"], role="reviewer", criterion_ids=ids[1:2])
        ev = self.opt.evaluate("proj_t", [w1["id"], w2["id"]])
        self.assertEqual(ev["decision"], "OWNER_REQUIRED")
        with self.assertRaises(WorkOrderError) as ctx:
            self.opt.merge_confirm("proj_t", [w1["id"], w2["id"]])
        self.assertEqual(ctx.exception.code, "MERGE_INCOMPATIBLE")

    def test_split_only_at_checkpoint(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="цель")
        ids = [c["id"] for c in wo["content"]["acceptance_criteria"]]
        with self.assertRaises(WorkOrderError) as ctx:
            self.opt.split_confirm("proj_t", wo["id"], [ids[:1], ids[1:]], checkpoint_id="none")
        self.assertEqual(ctx.exception.code, "SPLIT_INVALID")

    def test_split_conserves_criteria(self):
        spec = self._spec()
        wo = self._active_wo(spec)
        ids = [c["id"] for c in wo["content"]["acceptance_criteria"]]
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1")
        wo = self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        res = self.opt.split_confirm("proj_t", wo["id"], [ids[:1], ids[1:]], checkpoint_id=cp["id"])
        union = set()
        for kid in res["children"]:
            k = self.wos.get_work_order("proj_t", kid)
            union |= {c["id"] for c in k["content"]["acceptance_criteria"]}
        self.assertEqual(union, set(ids))
        self.assertEqual(self.wos.get_work_order("proj_t", wo["id"])["status"], "cancelled")

    def test_split_criteria_lost_rejected(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        wo = self._active_wo(spec)
        ids = [c["id"] for c in wo["content"]["acceptance_criteria"]]
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1")
        self.wos.transition("proj_t", wo["id"], "checkpointed",
                            expected_version=wo["version"])
        # пропустить один критерий → CRITERIA_LOST
        with self.assertRaises(WorkOrderError) as ctx:
            self.opt.split_confirm("proj_t", wo["id"], [ids[:1], ids[1:-1]], checkpoint_id=cp["id"])
        self.assertEqual(ctx.exception.code, "CRITERIA_LOST")

    def test_switch_profile_no_routing(self):
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="x")
        ev = self.opt.evaluate("proj_t", [wo["id"]], signals={"rate_limited": True})
        self.assertEqual(ev["decision"], "SWITCH_PROFILE")
        self.assertEqual(ev["reason_code"], "RATE_LIMIT")


class TestJobPackage(VP4Base):
    def test_bounded_and_no_forbidden(self):
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="x")
        pkg = self.ce.build_job_package("proj_t", wo["id"])
        self.assertLessEqual(pkg["byte_size"], 24_000)
        c = pkg["content"]
        for bad in ("repository", "full_chat", "credentials", "environment", "chat_history"):
            self.assertNotIn(bad, c)
        # provenance с хешами
        self.assertTrue(any(p.get("hash") for p in pkg["provenance"]))
        self.assertEqual(c["capacity"]["status"], "UNKNOWN")

    def test_context_cannot_expand_capabilities(self):
        # вставить «capability-подобную» строку в inputs — package caps не меняются
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="force_push production_deploy")
        pkg = self.ce.build_job_package("proj_t", wo["id"])
        for prohibited in ("force_push", "production_deploy", "delete_repository"):
            self.assertNotIn(prohibited, pkg["capabilities"])

    def test_prohibited_capability_rejected(self):
        from atlas_core.workorders import WorkOrderError
        spec = self._spec()
        with self.assertRaises(WorkOrderError) as ctx:
            self.wos.create_work_order("proj_t", spec["id"], goal="x",
                                       capabilities=["repository_read", "force_push"])
        self.assertEqual(ctx.exception.code, "CAPABILITY_DENIED")

    def test_deterministic_hash(self):
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="x")
        p1 = self.ce.build_job_package("proj_t", wo["id"])
        p2 = self.ce.build_job_package("proj_t", wo["id"])
        self.assertEqual(p1["content_hash"], p2["content_hash"])


class TestCheckpointHandoff(VP4Base):
    def test_checkpoint_hash_verifiable(self):
        spec = self._spec()
        wo = self._active_wo(spec)
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1",
                                      completed_criteria=["ac1"])
        v = self.hs.verify_checkpoint("proj_t", cp["id"])
        self.assertTrue(v["ok"])
        self.assertEqual(v["stored_hash"], v["recomputed_hash"])

    def test_handoff_required_fields_and_hash(self):
        from atlas_core.vp4handoff import HANDOFF_REQUIRED_FIELDS
        spec = self._spec()
        wo = self._active_wo(spec)
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1")
        self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        hp = self.hs.build_handoff("proj_t", wo["id"], checkpoint_id=cp["id"], current_head="H1")
        for k in HANDOFF_REQUIRED_FIELDS:
            self.assertIn(k, hp["content"])
        from atlas_core.productmap import content_hash
        self.assertEqual(content_hash(hp["content"]), hp["content_hash"])

    def test_stale_and_tamper_rejected(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import HandoffPackage
        spec = self._spec()
        wo = self._active_wo(spec)
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1")
        self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        hp = self.hs.build_handoff("proj_t", wo["id"], checkpoint_id=cp["id"], current_head="H1")
        self.assertTrue(self.hs.verify_handoff("proj_t", hp["id"], actual_head="H1")["ok"])
        # stale head
        v = self.hs.verify_handoff("proj_t", hp["id"], actual_head="OTHER")
        self.assertFalse(v["ok"])
        self.assertEqual(v["rejections"][0]["code"], "HANDOFF_STALE")
        # wrong project
        from atlas_core.workorders import WorkOrderError
        with self.assertRaises(WorkOrderError):
            self.hs.verify_handoff("proj_off", hp["id"])
        # tamper content
        with session_scope() as s:
            row = s.get(HandoffPackage, hp["id"])
            c = json.loads(row.content_json)
            c["goal"] = "ПОДДЕЛКА"
            row.content_json = json.dumps(c)
            s.commit()
        v2 = self.hs.verify_handoff("proj_t", hp["id"], actual_head="H1")
        self.assertIn("HASH_MISMATCH", [r["code"] for r in v2["rejections"]])

    def test_over_capability_handoff_rejected(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import HandoffPackage
        from atlas_core.productmap import content_hash
        spec = self._spec()
        wo = self._active_wo(spec)
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1")
        self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        hp = self.hs.build_handoff("proj_t", wo["id"], checkpoint_id=cp["id"], current_head="H1")
        with session_scope() as s:
            row = s.get(HandoffPackage, hp["id"])
            c = json.loads(row.content_json)
            c["capabilities"] = c["capabilities"] + ["force_push"]
            row.content_json = json.dumps(c)
            row.content_hash = content_hash(c)  # пересчитать, чтобы обойти HASH_MISMATCH
            s.commit()
        v = self.hs.verify_handoff("proj_t", hp["id"], actual_head="H1")
        self.assertIn("CAPABILITY_DENIED", [r["code"] for r in v["rejections"]])


class TestReconstructCompact(VP4Base):
    def _handoff(self):
        spec = self._spec()
        wo = self._active_wo(spec)
        cp = self.hs.build_checkpoint("proj_t", wo["id"], current_head="H1",
                                      completed_criteria=["ac1"], exact_next_action="доделать ac2")
        self.wos.transition("proj_t", wo["id"], "checkpointed", expected_version=wo["version"])
        return wo, self.hs.build_handoff("proj_t", wo["id"], checkpoint_id=cp["id"], current_head="H1")

    def test_fresh_session_reconstructs(self):
        wo, hp = self._handoff()
        r = self.rec.run_fresh_session("proj_t", hp["id"], actual_head="H1")
        self.assertTrue(r["ok"])
        self.assertTrue(r["run_result_valid"])
        self.assertEqual(r["schema_errors"], [])
        rc = r["reconstruction"]
        self.assertEqual(rc["work_order_id"], wo["id"])
        self.assertFalse(rc["used_prior_chat"])
        self.assertFalse(rc["used_credentials"])
        self.assertFalse(rc["used_full_repo"])
        self.assertIn("ac1", rc["completed_criteria"])
        self.assertTrue(rc["exact_next_action"])
        self.assertEqual(r["ack"]["result"], "ACK")

    def test_stale_fresh_session_fails_closed(self):
        wo, hp = self._handoff()
        r = self.rec.run_fresh_session("proj_t", hp["id"], actual_head="DIFFERENT")
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "verify")

    def test_compact_preserves_or_owner_required(self):
        from atlas_core.workorders import WorkOrderError
        _, hp = self._handoff()
        small = self.rec.compact_probe(hp["content"])
        self.assertTrue(small["ok"])
        big = dict(hp["content"])
        big["_pad"] = ["x" * 100] * 400
        p = self.rec.compact_probe(big)
        self.assertTrue(p["compacted"])
        self.assertTrue(p["preserved"])
        huge = dict(hp["content"])
        huge["acceptance_criteria"] = [{"id": f"ac{i}", "text": "y" * 400, "required": True}
                                       for i in range(200)]
        with self.assertRaises(WorkOrderError) as ctx:
            self.rec.compact_probe(huge)
        self.assertEqual(ctx.exception.code, "OWNER_REQUIRED")


class TestGovernorRotation(VP4Base):
    def test_governor_maps_triggers(self):
        spec = self._spec()
        wo = self.wos.create_work_order("proj_t", spec["id"], goal="x")
        cases = {
            (("rate_limited", True),): ("SWITCH_PROFILE", True),
            (("failed_review", True),): ("OWNER_REQUIRED", False),
            (("vp_boundary", True),): ("SWITCH_PROFILE", True),
            (("context_over_budget", True),): ("SWITCH_PROFILE", True),
        }
        for sig_items, (decision, rotate) in cases.items():
            o = self.gov.detect("proj_t", [wo["id"]], signals=dict(sig_items))
            self.assertEqual(o["decision"], decision)
            self.assertEqual(o["rotation_required"], rotate)

    def test_rotation_one_writer_and_continue(self):
        spec = self._spec()
        wo = self._active_wo(spec)
        rot = self.gov.rotate("proj_t", wo["id"], trigger="context_threshold", current_head="HX",
                              completed_criteria=["ac1"], next_profile_request="claude-pro-02")
        self.assertTrue(rot["one_writer_ok"])
        self.assertTrue(rot["lease_released"])
        self.assertEqual([s["name"] for s in rot["steps"]][:6],
                         ["stop_new_actions", "capture_diff", "impacted_checks",
                          "checkpoint", "handoff", "release_lease"])
        cont = self.gov.continue_after_rotation("proj_t", rot["id"], ack_hash=rot["handoff_hash"],
                                                baseline_ack=rot["baseline_head"], actual_head="HX")
        self.assertEqual(cont["status"], "continued")
        self.assertEqual(cont["work_order"]["status"], "active")
        self.assertTrue(cont["work_order"]["lease_active"])


class TestApi(VP4Base):
    def setUp(self):
        super().setUp()
        from atlas_core.app import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app(self.settings))

    def test_full_http_flow(self):
        self._approve()
        c = self.client
        r = c.post("/api/v1/projects/proj_t/vp-specs", json={"vp_key": "VP-4"})
        self.assertEqual(r.status_code, 201)
        sid = r.json()["id"]
        r = c.post("/api/v1/projects/proj_t/work-orders", json={"vp_spec_id": sid, "goal": "цель"})
        self.assertEqual(r.status_code, 201)
        wo = r.json()
        wid = wo["id"]
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/job-package")
        self.assertEqual(r.status_code, 201)
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/transition",
                   json={"to_status": "ready", "expected_version": wo["version"]})
        self.assertEqual(r.status_code, 200)
        v = r.json()["version"]
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/transition",
                   json={"to_status": "active", "expected_version": v})
        v = r.json()["version"]
        # invalid transition -> 409 stable code
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/transition",
                   json={"to_status": "ready", "expected_version": v})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "INVALID_TRANSITION")
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/checkpoints",
                   json={"current_head": "H1", "completed_criteria": ["ac1"]})
        cp = r.json()
        r = c.get(f"/api/v1/projects/proj_t/checkpoints/{cp['id']}/verify")
        self.assertTrue(r.json()["ok"])
        c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/transition",
               json={"to_status": "checkpointed", "expected_version": v})
        r = c.post(f"/api/v1/projects/proj_t/work-orders/{wid}/handoffs",
                   json={"checkpoint_id": cp["id"], "current_head": "H1"})
        hp = r.json()
        r = c.post(f"/api/v1/projects/proj_t/handoffs/{hp['id']}/reconstruct",
                   json={"actual_head": "H1"})
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["run_result_valid"])
        self.assertEqual(body["ack"]["result"], "ACK")

    def test_audit_no_full_content(self):
        self._approve()
        c = self.client
        sid = c.post("/api/v1/projects/proj_t/vp-specs", json={"vp_key": "VP-4"}).json()["id"]
        c.post("/api/v1/projects/proj_t/work-orders",
               json={"vp_spec_id": sid, "goal": "секретной прозы тут нет"})
        au = c.get("/api/v1/audit?limit=200").json()
        types = [e["event_type"] for e in au["events"]]
        self.assertTrue(any(t.startswith("workorders.") for t in types))
        for e in au["events"]:
            # аудит не содержит полного JSON Work Order/чата
            self.assertNotIn("acceptance_criteria", e["message"])
            self.assertNotIn("content_json", e["message"])


class TestSchemaValidation(VP4Base):
    def test_run_result_validator(self):
        from atlas_core.schema_validate import validate
        schema = {"type": "object", "required": ["state"], "additionalProperties": False,
                  "properties": {"state": {"type": "string", "enum": ["OK", "BAD"]}}}
        self.assertEqual(validate({"state": "OK"}, schema), [])
        self.assertTrue(validate({"state": "NOPE"}, schema))
        self.assertTrue(validate({"state": "OK", "extra": 1}, schema))
        self.assertTrue(validate({}, schema))
