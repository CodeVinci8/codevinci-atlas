"""VP-7 числовые лимиты подписок (§11.6): нормализация, redaction, персист,
reconcile. Провайдерские probes инъектируются (детерминированно, без CLI)."""

from __future__ import annotations

import json
import os
import tempfile

from atlas_test_base import AtlasTestCase


class CapBase(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())


class TestCapacityNormalize(CapBase):
    def test_remaining_and_status(self):
        from atlas_core.capacity import _mk_window, capacity_status_from_windows
        w = _mk_window(win_id="7d", label="Неделя", used_pct=68, reset_at=None, window_mins=10080)
        self.assertEqual(w["used_pct"], 68.0)
        self.assertEqual(w["remaining_pct"], 32.0)  # 100 - 68
        self.assertEqual(capacity_status_from_windows([w]), "AVAILABLE")
        self.assertEqual(capacity_status_from_windows(
            [_mk_window(win_id="5h", label="s", used_pct=96, reset_at=None, window_mins=300)]), "LOW")
        self.assertEqual(capacity_status_from_windows(
            [_mk_window(win_id="5h", label="s", used_pct=100, reset_at=None, window_mins=300)]),
            "EXHAUSTED")
        self.assertEqual(capacity_status_from_windows([]), "UNKNOWN")

    def test_window_label_by_duration(self):
        from atlas_core.capacity import _window_label
        self.assertEqual(_window_label(300)[0], "5h")
        self.assertEqual(_window_label(10080)[0], "7d")

    def test_redaction_removes_email(self):
        from atlas_core.capacity import _safe
        self.assertNotIn("@", _safe("account user@example.com plus"))
        self.assertIn("<redacted>", _safe("x user@example.com y"))

    def test_epoch_iso(self):
        from atlas_core.capacity import _epoch_iso
        self.assertTrue(_epoch_iso(1786225197).startswith("2026-"))
        self.assertIsNone(_epoch_iso(None))

    def test_parse_claude_usage(self):
        from atlas_core.capacity import _parse_claude_usage
        text = "Current session 42% used  Resets in 3h | Current week 71% used  Resets Tue"
        wins = {w["id"]: w for w in _parse_claude_usage(text)}
        self.assertEqual(wins["5h"]["used_pct"], 42.0)
        self.assertEqual(wins["5h"]["remaining_pct"], 58.0)
        self.assertEqual(wins["7d"]["used_pct"], 71.0)


class TestCapacityPersistReconcile(CapBase):
    def _profile(self, alias="codex-plus-01", provider="codex"):
        from atlas_core.agent_registry import ProfileService
        return ProfileService().upsert_profile(alias=alias, provider=provider,
                                               unix_label="u", enabled=True, schedulable=True)

    def test_persist_capacity_windows(self):
        from atlas_core.capacity import persist_capacity
        pid = self._profile()
        cap = {"provider": "codex", "plan": "plus", "auth_ok": True, "source": "codex-app-server",
               "checked_at": "2026-08-04T00:00:00+00:00", "error_code": "",
               "windows": [{"id": "7d", "label": "Неделя (7 дн)", "used_pct": 68.0,
                            "remaining_pct": 32.0, "reset_at": "2026-08-08T21:39:57+00:00",
                            "window_mins": 10080}]}
        rec = persist_capacity(pid, cap)
        self.assertEqual(rec["plan"], "plus")
        self.assertEqual(rec["status"], "AVAILABLE")
        self.assertEqual(rec["seven_d_used_pct"], 68)
        self.assertEqual(len(rec["windows"]), 1)
        self.assertEqual(rec["error_code"], "")

    def test_persist_capacity_plan_only_error(self):
        from atlas_core.capacity import persist_capacity
        pid = self._profile(alias="claude-pro-01", provider="claude")
        cap = {"provider": "claude", "plan": "pro", "auth_ok": True, "source": "claude-auth-status",
               "checked_at": "2026-08-04T00:00:00+00:00",
               "error_code": "CLAUDE_USAGE_TUI_NOT_HEADLESS", "windows": []}
        rec = persist_capacity(pid, cap)
        self.assertEqual(rec["plan"], "pro")
        self.assertEqual(rec["status"], "UNKNOWN")     # нет чисел
        self.assertEqual(rec["error_code"], "CLAUDE_USAGE_TUI_NOT_HEADLESS")  # точная причина

    def test_reconcile_with_injected_prober_persists_and_views(self):
        from atlas_core.agent_registry import ProfileService
        from atlas_core.capacity import reconcile_capacity
        self._profile(alias="codex-plus-01", provider="codex")
        self._profile(alias="claude-pro-01", provider="claude")
        reg = {"profiles": {
            "codex-plus-01": {"provider": "codex", "root_path": "/x", "executable_path": "/x/codex",
                              "runtime_user": "u1"},
            "claude-pro-01": {"provider": "claude", "root_path": "/y", "executable_path": "/y/claude",
                              "runtime_user": "u2"}}}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(reg, f)
        f.close()

        def fake_prober(provider, root, exe, user, timeout):
            if provider == "codex":
                return {"provider": "codex", "plan": "plus", "auth_ok": True,
                        "source": "codex-app-server", "checked_at": "2026-08-04T00:00:00+00:00",
                        "error_code": "", "windows": [{"id": "7d", "label": "Неделя (7 дн)",
                        "used_pct": 68.0, "remaining_pct": 32.0,
                        "reset_at": "2026-08-08T21:39:57+00:00", "window_mins": 10080}]}
            return {"provider": "claude", "plan": "pro", "auth_ok": True,
                    "source": "claude-auth-status", "checked_at": "2026-08-04T00:00:00+00:00",
                    "error_code": "CLAUDE_USAGE_TUI_NOT_HEADLESS", "windows": []}

        out = reconcile_capacity(prober=fake_prober, registry_path=f.name, force=True)
        os.unlink(f.name)
        self.assertEqual(len(out), 2)
        # профильный view отдаёт реальные окна/план/ошибку
        views = {p["alias"]: p for p in ProfileService().list_profiles()}
        cx = views["codex-plus-01"]["capacity"]
        self.assertEqual(cx["plan"], "plus")
        self.assertEqual(cx["seven_d_used_pct"], 68)
        self.assertEqual(cx["windows"][0]["remaining_pct"], 32.0)
        cl = views["claude-pro-01"]["capacity"]
        self.assertEqual(cl["plan"], "pro")
        self.assertEqual(cl["error_code"], "CLAUDE_USAGE_TUI_NOT_HEADLESS")

    def test_no_secrets_in_persisted_capacity(self):
        from atlas_core.capacity import persist_capacity
        pid = self._profile()
        cap = {"provider": "codex", "plan": "plus", "auth_ok": True, "source": "codex-app-server",
               "checked_at": "2026-08-04T00:00:00+00:00", "error_code": "", "detail": "reached=None",
               "windows": [{"id": "7d", "label": "Неделя", "used_pct": 68.0, "remaining_pct": 32.0,
                            "reset_at": None, "window_mins": 10080}]}
        rec = persist_capacity(pid, cap)
        blob = json.dumps(rec).lower()
        for marker in ("@", "token", "cookie", "/root/", "/home/", "email"):
            self.assertNotIn(marker, blob)
