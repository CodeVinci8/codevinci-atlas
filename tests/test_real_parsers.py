"""Тесты парсеров реальных CLI-форматов (сверено с живыми codex 0.146.0 / claude 2.1.220).

Фикстуры воспроизводят ПОДТВЕРЖДЁННЫЙ формат событий (без реальных вызовов и
без credentials): codex `thread.started`/`item.completed`, claude `result`.
Гарантирует, что адаптеры извлекают session-id и структурный результат.
"""

from atlas_test_base import AtlasTestCase

from atlas_core.adapters.real_claude import RealClaudeAdapter
from atlas_core.adapters.real_codex import RealCodexAdapter

# Реальный формат codex exec --json (ведущая не-JSON строка присутствует в жизни)
CODEX_STREAM = "\n".join([
    "Reading additional input from stdin...",
    '{"type":"thread.started","thread_id":"11111111-2222-3333-4444-555555555555"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"it_1","type":"agent_message","text":"{\\"partial\\": 5, \\"nonce\\": \\"ABC123\\", \\"step\\": \\"A\\"}"}}',
    '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
])

# Реальный формат claude -p --output-format stream-json
CLAUDE_STREAM = "\n".join([
    '{"type":"system","subtype":"init","session_id":"aaaa1111-bbbb-2222-cccc-333333333333","model":"claude"}',
    '{"type":"assistant","message":{"role":"assistant"},"session_id":"aaaa1111-bbbb-2222-cccc-333333333333"}',
    '{"type":"result","subtype":"success","is_error":false,"result":"{\\"final\\": 7, \\"nonce\\": \\"ABC123\\"}","session_id":"aaaa1111-bbbb-2222-cccc-333333333333","usage":{}}',
])


class TestCodexParser(AtlasTestCase):
    def test_extracts_thread_id_and_result(self):
        structured, sess = RealCodexAdapter()._parse_json_events(CODEX_STREAM)
        self.assertEqual(sess, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(structured.get("partial"), 5)
        self.assertEqual(structured.get("nonce"), "ABC123")

    def test_non_json_prefix_ignored(self):
        # ведущая строка "Reading additional input…" не ломает парсер
        structured, sess = RealCodexAdapter()._parse_json_events("garbage\n" + CODEX_STREAM)
        self.assertIsNotNone(sess)
        self.assertEqual(structured.get("step"), "A")


class TestClaudeParser(AtlasTestCase):
    def test_extracts_session_and_result(self):
        structured, sess, is_error = RealClaudeAdapter()._parse_stream_json(CLAUDE_STREAM)
        self.assertEqual(sess, "aaaa1111-bbbb-2222-cccc-333333333333")
        self.assertFalse(is_error)
        self.assertEqual(structured.get("final"), 7)
        self.assertEqual(structured.get("nonce"), "ABC123")

    def test_is_error_flag(self):
        err = '{"type":"result","is_error":true,"result":"boom","session_id":"x"}'
        structured, sess, is_error = RealClaudeAdapter()._parse_stream_json(err)
        self.assertTrue(is_error)


class TestArgvUsesExecutable(AtlasTestCase):
    def test_codex_argv_uses_explicit_executable(self):
        argv = RealCodexAdapter().build_start_argv(
            __import__("atlas_core.contracts", fromlist=["JobPackage"]).JobPackage(
                goal="hi", role=__import__("atlas_core.contracts", fromlist=["Role"]).Role.BUILDER,
                provider=__import__("atlas_core.contracts", fromlist=["Provider"]).Provider.CODEX,
                inputs={"cwd": "/x"}),
            "/var/lib/codevinci-atlas/profiles/codex/codex-plus-01/.local/bin/codex")
        self.assertTrue(argv[0].endswith("/.local/bin/codex"))
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("-C", argv)
