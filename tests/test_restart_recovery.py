"""Восстановление после рестарта Core (Master Spec §7.5, §33 acceptance №7)."""

from atlas_core.adapters.fake import FakeCodexAdapter, FaultInjection
from atlas_core.contracts import JobPackage, Provider, Role, RunState
from atlas_core.handoff import build_checkpoint
from atlas_core.leases import LeaseStore
from atlas_core.orchestrator import Candidate, Core
from atlas_core.profiles import ProfileState, create_profile_root
from atlas_core.store import Store
from atlas_test_base import AtlasTestCase


class TestCoreRestartRecovery(AtlasTestCase):
    def test_state_survives_restart_and_active_marked_interrupted(self):
        db = str(self.data_dir / "atlas.db")
        # --- сессия Core №1: запускаем и оставляем "активный" run ---
        store1 = Store(db)
        store1.upsert_run(run_id="run_active", state=RunState.RUNNING.value,
                          project_id="codevinci-atlas", vp_id="VP-0")
        ckpt = build_checkpoint(project_id="codevinci-atlas", vp_id="VP-0", branch="atlas/vp-0",
                                head="sha1", status_porcelain="", cause="core crash",
                                profile_alias="codex-plus-01", session_id="sess1")
        store1.save_checkpoint(ckpt.to_dict())
        store1.close()  # имитируем падение процесса Core

        # --- сессия Core №2: тот же файл БД ---
        store2 = Store(db)
        leases2 = LeaseStore(store2)
        core2 = Core(store2, leases2)
        rec = core2.recover_after_core_restart("codevinci-atlas")
        # активный run помечен INTERRUPTED
        self.assertIn("run_active", rec["interrupted_runs"])
        self.assertEqual(store2.get_run("run_active")["state"], "INTERRUPTED")
        # checkpoint доступен для продолжения
        self.assertIsNotNone(rec["checkpoint"])
        self.assertEqual(rec["checkpoint"]["session_id"], "sess1")
        # audit зафиксировал восстановление
        events = [e["event_type"] for e in store2.audit_events()]
        self.assertIn("core.restart.interrupted", events)
        store2.close()

    def test_continue_from_checkpoint_after_restart(self):
        db = str(self.data_dir / "atlas.db")
        pA = create_profile_root("codex-plus-01", "codex"); pA.state = ProfileState.READY
        pB = create_profile_root("codex-plus-02", "codex"); pB.state = ProfileState.READY

        # Core №1: A обрывается по rate limit на 2 из 5 → checkpoint+handoff в БД
        store1 = Store(db)
        core1 = Core(store1, LeaseStore(store1))
        job = JobPackage(goal="sum", role=Role.BUILDER, provider=Provider.CODEX,
                         inputs={"work_items": [10, 20, 30, 40, 50], "worker_label": "codex-plus-01"})
        try:
            core1.run_with_switch(job, project_id="codevinci-atlas", worktree="atlas/vp-0", vp_id="VP-0",
                                  candidates=[Candidate(pA, FakeCodexAdapter(FaultInjection(rate_limit_after=2)))])
        except Exception:
            pass
        store1.close()

        # Core №2 (рестарт): восстановить handoff и продолжить на B
        store2 = Store(db)
        core2 = Core(store2, LeaseStore(store2))
        rec = core2.recover_after_core_restart("codevinci-atlas")
        ckpt = rec["checkpoint"]
        self.assertIsNotNone(ckpt)
        # Реконструируем продолжение ИЗ persisted handoff (а не из хардкода):
        import json
        row = store2._conn.execute("SELECT payload_json FROM handoffs ORDER BY created_at DESC LIMIT 1").fetchone()
        handoff = json.loads(row["payload_json"])
        progress = handoff["progress"]
        self.assertEqual(progress["processed_index"], 2)
        self.assertEqual(progress["partial_sum"], 30)
        cont = JobPackage(goal="sum", role=Role.BUILDER, provider=Provider.CODEX,
                          inputs={"work_items": [10, 20, 30, 40, 50],
                                  "resume_from": progress["processed_index"],
                                  "partial_sum": progress["partial_sum"],
                                  "processed": progress["processed"],
                                  "worker_label": "codex-plus-02"})
        result, tele = core2.run_with_switch(cont, project_id="codevinci-atlas", worktree="atlas/vp-0",
                                             vp_id="VP-0", candidates=[Candidate(pB, FakeCodexAdapter())])
        self.assertEqual(result.result.structured_output["sum"], 150)
        self.assertEqual(tele.max_concurrent_writers, 1)
        store2.close()
