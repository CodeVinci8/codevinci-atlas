"""Контрактные тесты fake-адаптеров и argv реальных (Master Spec §12, §32.3)."""

from atlas_test_base import AtlasTestCase

from atlas_core.adapters.fake import (FakeClaudeAdapter, FakeCodexAdapter, FaultInjection)
from atlas_core.adapters.real_claude import RealClaudeAdapter
from atlas_core.adapters.real_codex import RealCodexAdapter
from atlas_core.capacity import CapacityStatus
from atlas_core.contracts import JobPackage, Provider, Role, SessionCapability
from atlas_core.errors import AtlasError, ErrorCode


def _job(**over):
    inp = {"work_items": [1, 2, 3, 4], "worker_label": "A"}
    inp.update(over.pop("inputs", {}))
    return JobPackage(goal="sum", role=Role.BUILDER, provider=Provider.CODEX, inputs=inp, **over)


class TestFakeAdapters(AtlasTestCase):
    def test_success_structured_output(self):
        ad = FakeCodexAdapter()
        res = ad.start(_job(), profile_alias="codex-plus-01", root_path="/x")
        self.assertEqual(res.result.structured_output["sum"], 10)
        self.assertTrue(res.result.structured_output["complete"])
        self.assertTrue(res.result.output_hash.startswith("sha256:"))

    def test_capacity_unknown(self):
        cap = FakeClaudeAdapter().capacity("/x")
        self.assertEqual(cap.status, CapacityStatus.UNKNOWN)
        self.assertIsNone(cap.remaining_5h)

    def test_capabilities(self):
        caps = FakeCodexAdapter().discover_capabilities()
        self.assertIn(SessionCapability.NEW_SESSION, caps)
        self.assertIn(SessionCapability.FRESH_WITH_HANDOFF, caps)

    def _assert_fault(self, faults, code):
        ad = FakeCodexAdapter(faults)
        with self.assertRaises(AtlasError) as cm:
            ad.start(_job(), profile_alias="codex-plus-01", root_path="/x")
        self.assertEqual(cm.exception.classified.code, code)
        return cm.exception

    def test_fault_auth(self):
        self._assert_fault(FaultInjection(auth_required=True), ErrorCode.AUTH_REQUIRED)

    def test_fault_auth_expired(self):
        self._assert_fault(FaultInjection(auth_expired=True), ErrorCode.AUTH_EXPIRED)

    def test_fault_policy(self):
        self._assert_fault(FaultInjection(policy_denied=True), ErrorCode.POLICY_DENIED)

    def test_fault_invalid_output(self):
        self._assert_fault(FaultInjection(invalid_output=True), ErrorCode.OUTPUT_INVALID)

    def test_fault_rate_limit_after(self):
        exc = self._assert_fault(FaultInjection(rate_limit_after=2), ErrorCode.RATE_LIMITED)
        self.assertEqual(exc.partial_state["processed_index"], 2)

    def test_fault_network(self):
        self._assert_fault(FaultInjection(network_after=1), ErrorCode.NETWORK_ERROR)

    def test_fault_timeout(self):
        self._assert_fault(FaultInjection(timeout_after=1), ErrorCode.TIMEOUT)

    def test_fault_interrupt(self):
        self._assert_fault(FaultInjection(interrupt_after=1), ErrorCode.USER_INTERRUPTED)

    def test_auth_status_authenticated(self):
        self.assertTrue(FakeCodexAdapter().auth_status("/x")["authenticated"])
        self.assertFalse(FakeCodexAdapter(authenticated=False).auth_status("/x")["authenticated"])


class TestRealAdapterArgv(AtlasTestCase):
    def test_codex_start_argv(self):
        argv = RealCodexAdapter().build_start_argv(_job(output_schema_ref="contracts/schemas/run-result.json"))
        self.assertEqual(argv[:3], ["codex", "exec", "--json"])
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--output-schema", argv)
        self.assertIn("read-only", argv)  # -s read-only

    def test_codex_resume_argv(self):
        argv = RealCodexAdapter().build_resume_argv("SESSION123", _job())
        self.assertEqual(argv[:4], ["codex", "exec", "resume", "SESSION123"])
        self.assertIn("--json", argv)

    def test_claude_start_argv(self):
        argv = RealClaudeAdapter().build_start_argv(_job(inputs={"model": "opus"}))
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("--output-format", argv)
        self.assertIn("stream-json", argv)
        self.assertIn("--model", argv)

    def test_claude_resume_argv(self):
        argv = RealClaudeAdapter().build_resume_argv("SID", _job())
        self.assertIn("--resume", argv)
        self.assertIn("SID", argv)

    def test_codex_auth_status_honest(self):
        import shutil
        import tempfile
        adapter = RealCodexAdapter()
        if shutil.which("codex") is None:
            st = adapter.auth_status("/nonexistent-root")
            self.assertEqual(st["state"], "CLI_ABSENT")
            return
        # codex установлен: против пустого root — честный AUTH_REQUIRED, без account/email
        with tempfile.TemporaryDirectory() as d:
            st = adapter.auth_status(d)
        self.assertFalse(st["authenticated"])
        self.assertEqual(st["state"], "AUTH_REQUIRED")
        self.assertNotIn("@", st["detail"])  # никакого email в detail

    def test_claude_auth_status_no_account_leak(self):
        import shutil
        import tempfile
        if shutil.which("claude") is None:
            self.skipTest("claude не установлен")
        with tempfile.TemporaryDirectory() as d:
            st = RealClaudeAdapter().auth_status(d)
        self.assertIn(st["state"], ("AUTH_REQUIRED", "READY"))
        self.assertNotIn("@", st["detail"])  # detail не раскрывает email/account
