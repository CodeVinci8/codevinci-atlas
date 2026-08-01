"""Тесты редактирования и сканера secret-markers (Master Spec §30)."""

import os

from atlas_test_base import AtlasTestCase

from atlas_core.redaction import (SECRET_MARKER, contains_secret, redact,
                                  scan_for_secrets, scan_paths)


class TestRedaction(AtlasTestCase):
    def test_github_token(self):
        self.assertNotIn("ghp_", redact("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"))

    def test_openai_and_anthropic_keys(self):
        self.assertIn("[REDACTED]", redact("sk-ant-abcdefghijklmnop12345"))
        self.assertIn("[REDACTED]", redact("sk-abcdefghijklmnop12345"))

    def test_oauth_json(self):
        red = redact('{"access_token": "abcdef123456ghijkl", "x": 1}')
        self.assertNotIn("abcdef123456ghijkl", red)

    def test_cookie(self):
        self.assertIn("[REDACTED]", redact("Cookie: sessionKey=abcdef1234567890"))

    def test_email(self):
        self.assertNotIn("user@example.com", redact("owner user@example.com"))

    def test_credential_path(self):
        red = redact("/var/lib/codevinci-atlas/profiles/codex/codex-plus-01/auth.json")
        self.assertIn("[REDACTED_PATH]", red)
        self.assertNotIn("auth.json", red)

    def test_contains_secret(self):
        self.assertTrue(contains_secret("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"))
        self.assertTrue(contains_secret(SECRET_MARKER))
        self.assertFalse(contains_secret("обычный текст без секретов"))

    def test_scan_marker(self):
        hits = scan_for_secrets(f"line1\nsome {SECRET_MARKER} here\nline3")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 2)

    def test_scan_paths_finds_and_skips_binaries(self):
        d = self.data_dir / "scanme"
        d.mkdir()
        (d / "leak.txt").write_text(f"contains {SECRET_MARKER}\n", encoding="utf-8")
        (d / "clean.txt").write_text("nothing here\n", encoding="utf-8")
        (d / "img.png").write_bytes(b"\x89PNG" + SECRET_MARKER.encode())  # бинарь пропускается
        hits = scan_paths([str(d)])
        files = {os.path.basename(h.path) for h in hits}
        self.assertIn("leak.txt", files)
        self.assertNotIn("clean.txt", files)

    def test_scan_paths_does_not_follow_symlink_outside(self):
        outside = self.data_dir / "outside.txt"
        outside.write_text(SECRET_MARKER, encoding="utf-8")
        d = self.data_dir / "scandir"
        d.mkdir()
        try:
            os.symlink(outside, d / "link.txt")
        except OSError:
            self.skipTest("symlink недоступен")
        hits = scan_paths([str(d)], follow_symlinks=False)
        self.assertEqual([h for h in hits if "link.txt" in h.path], [])
