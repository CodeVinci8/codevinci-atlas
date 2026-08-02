"""Simulated rate limit → смена профиля без второго writer.

Master Spec §5.3, §33 acceptance №3,4,5,6. Доказывается для обоих провайдеров.
"""

from atlas_core.adapters.fake import FakeClaudeAdapter, FakeCodexAdapter, FaultInjection
from atlas_core.contracts import JobPackage, Provider, Role
from atlas_core.leases import LeaseStore
from atlas_core.orchestrator import Candidate, Core
from atlas_core.profiles import ProfileState, create_profile_root
from atlas_core.store import Store
from atlas_test_base import AtlasTestCase


class TestRateLimitSwitch(AtlasTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.core = Core(self.store, LeaseStore(self.store))

    def tearDown(self):
        self.store.close()
        super().tearDown()

    def _prove(self, provider, adapter_cls, aliasA, aliasB):
        pA = create_profile_root(aliasA, provider); pA.state = ProfileState.READY
        pB = create_profile_root(aliasB, provider); pB.state = ProfileState.READY
        job = JobPackage(goal="sum", role=Role.BUILDER, provider=Provider(provider),
                         inputs={"work_items": [1, 2, 3, 4, 5, 6], "worker_label": aliasA})
        result, tele = self.core.run_with_switch(
            job, project_id="codevinci-atlas", worktree=f"atlas/vp-0-{provider}", vp_id="VP-0",
            candidates=[Candidate(pA, adapter_cls(FaultInjection(rate_limit_after=3))),
                        Candidate(pB, adapter_cls())])
        so = result.result.structured_output
        # результат полный и собран из вкладов обоих профилей
        self.assertEqual(so["sum"], 21)
        self.assertEqual(so["processed_by"][aliasA], [0, 1, 2])
        self.assertEqual(so["processed_by"][aliasB], [3, 4, 5])
        # переключение произошло по RATE_LIMITED
        self.assertEqual([s["code"] for s in tele.switches], ["RATE_LIMITED"])
        # НИКОГДА не было двух writer
        self.assertEqual(tele.max_concurrent_writers, 1)
        self.assertTrue(tele.single_writer_ok)
        # был создан ровно один handoff/checkpoint при смене
        self.assertEqual(len(tele.handoff_ids), 1)
        self.assertEqual(len(tele.checkpoint_ids), 1)
        # аренда освобождена в конце
        self.assertEqual(self.core.leases.active_count("codevinci-atlas", f"atlas/vp-0-{provider}"), 0)

    def test_codex_rate_limit_switch(self):
        self._prove("codex", FakeCodexAdapter, "codex-plus-01", "codex-plus-02")

    def test_claude_rate_limit_switch(self):
        self._prove("claude", FakeClaudeAdapter, "claude-pro-01", "claude-pro-02")

    def test_no_second_writer_when_switch(self):
        # Явная проверка: пока A "держит" аренду, второй acquire невозможен.
        pA = create_profile_root("codex-plus-01", "codex"); pA.state = ProfileState.READY
        l = self.core.leases.acquire(project_id="codevinci-atlas", worktree="wt", holder="codex-plus-01")
        from atlas_core.errors import AtlasError
        with self.assertRaises(AtlasError):
            self.core.leases.acquire(project_id="codevinci-atlas", worktree="wt", holder="codex-plus-02")
        self.core.leases.release(l.id)
