"""VP-5 adapter boundary — нормализация auth без PII, fresh-session argv,
безопасное построение argv (Master Spec §11.2, §12). Без реальных вызовов."""

from __future__ import annotations

import json

from atlas_core.adapters.authnorm import (
    PII_KEYS,
    assert_no_pii,
    normalize_claude_auth,
    normalize_codex_auth,
)
from atlas_core.adapters.real_claude import RealClaudeAdapter
from atlas_core.adapters.real_codex import RealCodexAdapter
from atlas_core.contracts import JobPackage, Provider, Role
from atlas_test_base import AtlasTestCase

# Реальная форма claude auth status --json (со ВСЕМИ PII-полями, которые надо отбросить).
CLAUDE_AUTH = json.dumps({
    "loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
    "email": "owner@example.com", "orgId": "34de8a39-b9e9-4c0d-b384-48cccdf8fb4c",
    "orgName": "owner@example.com's Organization", "subscriptionType": "pro",
})


class TestAuthNormalization(AtlasTestCase):
    def test_claude_normalized_drops_pii(self):
        n = normalize_claude_auth(CLAUDE_AUTH)
        self.assertTrue(n["authenticated"])
        self.assertEqual(n["auth_status"], "READY")
        self.assertEqual(n["auth_method"], "claude.ai")
        self.assertEqual(n["api_provider"], "firstParty")
        self.assertEqual(n["plan_label"], "Pro")  # verified plan-label
        # PII отсутствует в результате и его значениях.
        blob = json.dumps(n).lower()
        for token in ("owner@example.com", "34de8a39", "organization"):
            self.assertNotIn(token, blob)
        for k in n:
            self.assertNotIn(k.lower(), PII_KEYS)
        assert_no_pii(n)

    def test_claude_not_logged_in(self):
        n = normalize_claude_auth(json.dumps({"loggedIn": False}))
        self.assertFalse(n["authenticated"])
        self.assertEqual(n["auth_status"], "AUTH_REQUIRED")
        self.assertEqual(n["plan_label"], "")

    def test_claude_garbage_falls_back_safely(self):
        n = normalize_claude_auth("not json at all")
        self.assertIn(n["auth_status"], ("READY", "AUTH_REQUIRED"))
        assert_no_pii(n)

    def test_codex_text_does_not_echo_email(self):
        # Даже если codex login status напечатает email — normalize его НЕ возвращает.
        n = normalize_codex_auth("Logged in using ChatGPT (owner@example.com)")
        self.assertTrue(n["authenticated"])
        blob = json.dumps(n).lower()
        self.assertNotIn("owner@example.com", blob)
        assert_no_pii(n)

    def test_codex_not_logged_in(self):
        n = normalize_codex_auth("Not logged in")
        self.assertFalse(n["authenticated"])
        self.assertEqual(n["plan_label"], "")


def _job(role, provider):
    return JobPackage(goal="сделать X безопасно", role=Role(role), provider=Provider(provider),
                      inputs={"model": "m1", "cwd": "/wt"})


class TestArgvConstruction(AtlasTestCase):
    def test_codex_start_argv_is_arglist_no_shell(self):
        argv = RealCodexAdapter().build_start_argv(_job("planner", "codex"), "codex")
        self.assertEqual(argv[0], "codex")
        self.assertIn("--json", argv)
        self.assertIn("-s", argv)
        self.assertIn("read-only", argv)  # sandbox read-only для planner/reviewer
        # Prompt — отдельный элемент argv (нет shell-интерполяции).
        self.assertEqual(argv[-1], "сделать X безопасно")

    def test_codex_resume_argv(self):
        argv = RealCodexAdapter().build_resume_argv("SID-1", _job("builder", "codex"), "codex")
        self.assertEqual(argv[:4], ["codex", "exec", "resume", "SID-1"])

    def test_codex_fresh_equals_new_exec(self):
        a = RealCodexAdapter()
        self.assertEqual(a.build_fresh_argv(_job("planner", "codex"), "codex")[:2], ["codex", "exec"])

    def test_claude_start_argv_stream_json(self):
        argv = RealClaudeAdapter().build_start_argv(_job("builder", "claude"), "claude")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("stream-json", argv)
        self.assertIn("--verbose", argv)
        self.assertEqual(argv[-1], "сделать X безопасно")

    def test_claude_three_session_semantics_are_distinct(self):
        a = RealClaudeAdapter()
        job = _job("builder", "claude")
        # EXACT_RESUME: --resume SID, без --fork-session.
        resume = a.build_resume_argv("SID-EX", job, "claude")
        self.assertIn("--resume", resume)
        self.assertIn("SID-EX", resume)
        self.assertNotIn("--fork-session", resume)
        # FORK_SESSION: --resume origin + --fork-session (копирует историю, тот же профиль).
        fork = a.build_fork_argv("OLD-SID", job, "claude")
        self.assertIn("--resume", fork)
        self.assertIn("OLD-SID", fork)
        self.assertIn("--fork-session", fork)
        # FRESH_WITH_HANDOFF: НЕТ ни --resume, ни --fork-session (genuinely fresh).
        fresh = a.build_fresh_argv(job, executable="claude")
        self.assertNotIn("--resume", fresh)
        self.assertNotIn("--fork-session", fresh)
        self.assertIn("-p", fresh)

    def test_claude_fresh_optional_new_session_id(self):
        argv = RealClaudeAdapter().build_fresh_argv(
            _job("builder", "claude"), new_session_id="NEW-UUID", executable="claude")
        self.assertIn("--session-id", argv)
        self.assertIn("NEW-UUID", argv)
        self.assertNotIn("--fork-session", argv)


if __name__ == "__main__":
    import unittest
    unittest.main()
