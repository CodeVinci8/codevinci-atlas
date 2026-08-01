"""Юнит-тесты классификации ошибок (Master Spec §12.4)."""

from atlas_test_base import AtlasTestCase

from atlas_core.errors import ErrorCode, classify


class TestErrorClassification(AtlasTestCase):
    def test_rate_limit(self):
        c = classify("Error 429: usage limit reached, reset_at 2026-08-01")
        self.assertEqual(c.code, ErrorCode.RATE_LIMITED)
        self.assertIn("checkpoint", c.next_action.lower())

    def test_auth_required(self):
        self.assertEqual(classify("Error: not logged in").code, ErrorCode.AUTH_REQUIRED)

    def test_auth_expired(self):
        self.assertEqual(classify("token expired, please reauth").code, ErrorCode.AUTH_EXPIRED)

    def test_network(self):
        self.assertEqual(classify("connection refused: ECONNREFUSED").code, ErrorCode.NETWORK_ERROR)

    def test_timeout_exception(self):
        self.assertEqual(classify(exception=TimeoutError("deadline")).code, ErrorCode.TIMEOUT)

    def test_permission_exception(self):
        self.assertEqual(classify(exception=PermissionError("denied")).code, ErrorCode.PERMISSION_DENIED)

    def test_invalid_output(self):
        self.assertEqual(classify("invalid json: JSONDecodeError").code, ErrorCode.OUTPUT_INVALID)

    def test_interruption(self):
        self.assertEqual(classify("interrupted: sigterm").code, ErrorCode.USER_INTERRUPTED)

    def test_policy_denied(self):
        self.assertEqual(classify("action outside grant: policy denied").code, ErrorCode.POLICY_DENIED)

    def test_worktree_conflict(self):
        self.assertEqual(classify("another writer holds the lease").code, ErrorCode.WORKTREE_CONFLICT)

    def test_exit_code_toolfailed(self):
        self.assertEqual(classify("weird message", exit_code=2).code, ErrorCode.TOOL_FAILED)

    def test_unknown(self):
        self.assertEqual(classify("совершенно непонятное сообщение").code, ErrorCode.UNKNOWN)

    def test_evidence_is_redacted(self):
        c = classify("auth failed with token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        self.assertNotIn("ghp_ABCDEF", c.evidence)
        self.assertIn("[REDACTED]", c.evidence)

    def test_retryable_flags(self):
        self.assertTrue(classify("network reset").retryable)
        self.assertFalse(classify("policy denied").retryable)

    def test_every_code_has_next_action(self):
        from atlas_core.errors import _NEXT_ACTION
        for code in ErrorCode:
            self.assertIn(code, _NEXT_ACTION)
            self.assertTrue(_NEXT_ACTION[code])
