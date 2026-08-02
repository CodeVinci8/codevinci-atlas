"""VP-5 Agent Pipeline — детерминированный Planner → Builder → независимый
Reviewer на fake-адаптерах (Master Spec §17, §38): реальный артефакт, один
writer, независимый read-only Reviewer, один fix-loop, bounded rate-limit switch
без второго writer, auth без бесконечного ретрая, interruption-recovery с одной
безопасной continuation, pause/resume того же Run, no silent fallback."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP5PipeBase(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine, session_scope
        from atlas_core.orm import Base, Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        self.db_path = self.settings.db_path
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        with session_scope() as s:
            s.add(Project(id="proj_p", name="Синтетика", source_kind="local_git",
                          source_location="/x", status="connected",
                          created_at=_now(), updated_at=_now()))
            s.commit()
        from atlas_core.pipeline import PipelineService
        from atlas_core.router import Candidate
        from atlas_core.runs import RunService
        self.runs = RunService()
        self.pipe = PipelineService(self.db_path, run_svc=self.runs)
        self._wt = tempfile.TemporaryDirectory(prefix="atlas-wt-")
        self.worktree = self._wt.name
        self.work_items = [1, 2, 3, 4, 5]  # sum = 15
        self.candidates = {
            "planner": [Candidate("codex-plus-01", "codex"), Candidate("codex-plus-02", "codex")],
            "builder": [Candidate("claude-pro-01", "claude"), Candidate("claude-pro-02", "claude")],
            "reviewer": [Candidate("codex-plus-01", "codex"), Candidate("codex-plus-02", "codex")],
        }
        self.roots = {a: self._wt.name for a in
                      ("codex-plus-01", "codex-plus-02", "claude-pro-01", "claude-pro-02", "")}

    def tearDown(self):
        self._wt.cleanup()
        super().tearDown()

    def _run(self, **kw):
        r = self.runs.create_run("proj_p", work_order_id="wo1", vp_key="VP-5")
        return self.pipe.run_synthetic(
            r["id"], project_id="proj_p", worktree_path=self.worktree,
            work_items=self.work_items, candidates=self.candidates, profile_roots=self.roots, **kw), r["id"]


class TestHappyPath(VP5PipeBase):
    def test_full_pipeline_real_artifact(self):
        telem, rid = self._run()
        self.assertEqual(telem["final_state"], "SUCCEEDED")
        self.assertEqual(telem["verdict"], "PASS")
        # Реальный проверяемый артефакт на диске.
        art = Path(self.worktree) / "RESULT.json"
        self.assertTrue(art.exists())
        data = json.loads(art.read_text())
        self.assertEqual(data["sum"], 15)
        self.assertTrue(data["complete"])
        self.assertTrue(telem["artifact_sha"].startswith("sha256:"))
        # Один writer на протяжении всего прогона.
        self.assertEqual(telem["max_concurrent_writers"], 1)
        # Роли отработали; провайдер-сессии записаны.
        self.assertEqual(self.runs.get_run(rid)["state"], "SUCCEEDED")

    def test_reviewer_independent_and_readonly(self):
        telem, rid = self._run()
        self.assertTrue(telem["reviewer_independent"])
        # Reviewer — другой профиль и другая сессия, чем Builder.
        self.assertNotEqual(telem["reviewer_profile"], telem["builder_profile"])
        self.assertNotEqual(telem["reviewer_session"], telem["builder_session"])

    def test_events_and_router_decisions_persisted(self):
        telem, rid = self._run()
        evs = [e["type"] for e in self.runs.events(rid)]
        self.assertIn("router.decided", evs)
        self.assertIn("builder.artifact", evs)
        self.assertIn("reviewer.verdict", evs)
        decisions = self.runs.router_decisions(rid)
        roles = {d["role"] for d in decisions}
        self.assertEqual(roles, {"planner", "builder", "reviewer"})
        for d in decisions:
            self.assertTrue(d["effective_profile"])
            self.assertTrue(d["reason_code"])


class TestNoSilentFallback(VP5PipeBase):
    def test_owner_requested_unavailable_builder_profile_blocks(self):
        # Владелец требует несуществующий builder-профиль → НЕ молчаливая замена.
        telem, rid = self._run(requested={"builder": {"profile": "claude-pro-99"}})
        self.assertEqual(telem["final_state"], "OWNER_REQUIRED")
        self.assertEqual(telem["reason"], "OWNER_OVERRIDE_UNAVAILABLE")
        self.assertEqual(self.runs.get_run(rid)["state"], "OWNER_REQUIRED")


class TestFalseSuccessAndFixLoop(VP5PipeBase):
    def test_false_builder_success_not_pass_then_fix_pass(self):
        # Builder заявляет успех, но пишет неверный артефакт → Reviewer REVISE,
        # затем один fix-loop исправляет → PASS.
        telem, rid = self._run(builder_corrupt="first")
        self.assertEqual(telem["fix_loops"], 1)
        self.assertEqual(telem["final_state"], "SUCCEEDED")
        self.assertEqual(json.loads((Path(self.worktree) / "RESULT.json").read_text())["sum"], 15)

    def test_second_failed_review_blocks_owner_required(self):
        telem, rid = self._run(builder_corrupt="always")
        self.assertEqual(telem["final_state"], "OWNER_REQUIRED")
        self.assertEqual(telem["reason"], "SECOND_FIX_BLOCKED")


class TestRecovery(VP5PipeBase):
    def test_rate_limit_bounded_switch_no_second_writer(self):
        from atlas_core.adapters.fake import FaultInjection
        telem, rid = self._run(builder_faults=FaultInjection(rate_limit_after=2))
        self.assertEqual(telem["final_state"], "SUCCEEDED")
        self.assertEqual(len(telem["switches"]), 1)  # ровно один switch
        self.assertEqual(telem["max_concurrent_writers"], 1)  # никогда не два writer
        # Оба профиля внесли вклад — переключение реально продолжило работу.
        data = json.loads((Path(self.worktree) / "RESULT.json").read_text())
        self.assertEqual(data["sum"], 15)
        self.assertEqual(len(self.runs.retries(rid)), 1)
        # Состояние проходило через RATE_LIMITED (в событиях перехода).
        trans = [e for e in self.runs.events(rid) if e["type"] == "run.transition"]
        self.assertTrue(any(e["payload"]["to"] == "RATE_LIMITED" for e in trans))

    def test_auth_failure_does_not_loop(self):
        from atlas_core.adapters.fake import FaultInjection
        telem, rid = self._run(builder_faults=FaultInjection(auth_required=True))
        self.assertEqual(self.runs.get_run(rid)["state"], "AUTH_REQUIRED")
        # Ровно одна попытка Builder — без бесконечного ретрая.
        self.assertEqual(len(self.runs.retries(rid)), 1)

    def test_interruption_one_safe_continuation(self):
        from atlas_core.adapters.fake import FaultInjection
        telem, rid = self._run(builder_faults=FaultInjection(interrupt_after=2))
        self.assertEqual(telem["final_state"], "SUCCEEDED")
        # Одна безопасная continuation из checkpoint.
        links = self.runs.handoff_links(rid)
        self.assertTrue(any(x["kind"] == "checkpoint" for x in links))
        self.assertEqual(telem["max_concurrent_writers"], 1)
        data = json.loads((Path(self.worktree) / "RESULT.json").read_text())
        self.assertEqual(data["sum"], 15)


class TestPauseResume(VP5PipeBase):
    def test_pause_resume_continues_same_run(self):
        r = self.runs.create_run("proj_p")
        rid = r["id"]
        v = self.runs.transition(rid, "PREPARING", expected_version=1)["version"]
        v = self.runs.transition(rid, "RUNNING", expected_version=v)["version"]
        # Pause.
        self.runs.record_pause(rid, "pause", reason="owner pause")
        v = self.runs.transition(rid, "PAUSED", expected_version=v)["version"]
        self.assertEqual(self.runs.get_run(rid)["state"], "PAUSED")
        # Resume продолжает ТОТ ЖЕ Run.
        self.runs.record_pause(rid, "resume", reason="owner resume")
        v = self.runs.transition(rid, "RUNNING", expected_version=v)["version"]
        self.assertEqual(self.runs.get_run(rid)["state"], "RUNNING")
        kinds = [e["type"] for e in self.runs.events(rid)]
        self.assertIn("run.pause", kinds)
        self.assertIn("run.resume", kinds)


if __name__ == "__main__":
    import unittest
    unittest.main()
