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

    def test_parse_claude_rate_limit_events(self):
        # Официальный stream-json Claude: берём rateLimitType/status/resetsAt,
        # отбрасываем session_id/uuid/request_id и текст.
        from atlas_core.capacity import _claude_windows_from_events, parse_claude_rate_limit_events
        stream = "\n".join([
            '{"type":"system","subtype":"init","session_id":"s1"}',
            '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
            '"resetsAt":1785873000,"rateLimitType":"five_hour"},"session_id":"s1","uuid":"u1"}',
            '{"is_error":false,"result":"ok","type":"result","session_id":"s1"}'])
        events = parse_claude_rate_limit_events(stream)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rateLimitType"], "five_hour")
        self.assertEqual(events[0]["status"], "allowed")
        wins = {w["id"]: w for w in _claude_windows_from_events(events)}
        self.assertIn("5h", wins)
        self.assertEqual(wins["5h"]["status"], "allowed")
        self.assertIsNone(wins["5h"]["used_pct"])            # % не выдумываем
        self.assertTrue(wins["5h"]["reset_at"].startswith("2026-"))

    def test_rate_limit_rejected_maps_exhausted(self):
        from atlas_core.capacity import (
            _claude_windows_from_events,
            capacity_status_from_windows,
            parse_claude_rate_limit_events,
        )
        stream = ('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
                  '"resetsAt":1785871800,"rateLimitType":"five_hour"}}')
        wins = _claude_windows_from_events(parse_claude_rate_limit_events(stream))
        self.assertEqual(wins[0]["status"], "rejected")
        self.assertEqual(wins[0]["used_pct"], 100.0)
        self.assertEqual(capacity_status_from_windows(wins), "EXHAUSTED")

    def test_rate_limit_no_identifiers_leak(self):
        from atlas_core.capacity import parse_claude_rate_limit_events
        stream = ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
                  '"resetsAt":1785873000,"rateLimitType":"seven_day"},'
                  '"session_id":"secret-sess","uuid":"secret-uuid","request_id":"req_secret"}')
        events = parse_claude_rate_limit_events(stream)
        blob = json.dumps(events)
        for marker in ("secret-sess", "secret-uuid", "req_secret", "session_id", "uuid"):
            self.assertNotIn(marker, blob)


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


class TestStaleFallback(CapBase):
    """success → failed refresh → stale carry-forward → later recovery (§11.6)."""

    def _profile(self, alias="codex-plus-01", provider="codex"):
        from atlas_core.agent_registry import ProfileService
        return ProfileService().upsert_profile(alias=alias, provider=provider,
                                               unix_label="u", enabled=True, schedulable=True)

    def _ok(self, used=68.0):
        return {"provider": "codex", "plan": "plus", "auth_ok": True, "source": "codex-app-server",
                "checked_at": "2026-08-04T00:00:00+00:00", "error_code": "",
                "windows": [{"id": "7d", "label": "Неделя (7 дн)", "used_pct": used,
                             "remaining_pct": round(100 - used, 1),
                             "reset_at": "2026-08-08T21:39:57+00:00", "window_mins": 10080}]}

    def _fail(self, code="CODEX_APPSERVER_IO_FAILED"):
        return {"provider": "codex", "plan": "plus", "auth_ok": True, "source": "codex-probe",
                "checked_at": "2026-08-04T01:00:00+00:00", "error_code": code, "windows": []}

    def test_success_then_fail_preserves_last_valid_as_stale(self):
        from atlas_core.agent_registry import ProfileService
        from atlas_core.capacity import persist_capacity
        pid = self._profile()
        persist_capacity(pid, self._ok(68.0))                 # успешное числовое
        rec = persist_capacity(pid, self._fail())             # неудачный refresh
        self.assertEqual(rec["status"], "STALE")
        self.assertTrue(rec["stale"])
        self.assertEqual(rec["error_code"], "CODEX_APPSERVER_IO_FAILED")  # новый код ошибки
        self.assertEqual(rec["seven_d_used_pct"], 68)          # прежние валидные числа
        self.assertEqual(len(rec["windows"]), 1)               # окна сохранены
        # view отдаёт STALE с числами, а не пустой свежий UNKNOWN
        cap = ProfileService().get_profile(pid)["capacity"]
        self.assertEqual(cap["status"], "STALE")
        self.assertEqual(cap["windows"][0]["used_pct"], 68.0)

    def test_recovery_after_stale_returns_available(self):
        from atlas_core.agent_registry import ProfileService
        from atlas_core.capacity import persist_capacity
        pid = self._profile()
        persist_capacity(pid, self._ok(68.0))
        persist_capacity(pid, self._fail())                    # stale
        persist_capacity(pid, self._ok(70.0))                  # восстановление
        cap = ProfileService().get_profile(pid)["capacity"]
        self.assertEqual(cap["status"], "AVAILABLE")
        self.assertFalse(cap["stale"])
        self.assertEqual(cap["seven_d_used_pct"], 70)

    def test_no_prior_valid_gives_exact_unavailable(self):
        from atlas_core.capacity import persist_capacity
        pid = self._profile(alias="claude-pro-01", provider="claude")
        rec = persist_capacity(pid, {"provider": "claude", "plan": "pro", "auth_ok": True,
                                     "source": "claude-auth-status", "windows": [],
                                     "error_code": "CLAUDE_USAGE_TUI_NOT_HEADLESS"})
        self.assertEqual(rec["status"], "UNKNOWN")             # не STALE, чисел не было
        self.assertFalse(rec["stale"])
        self.assertEqual(rec["error_code"], "CLAUDE_USAGE_TUI_NOT_HEADLESS")
        self.assertEqual(rec["plan"], "pro")                  # план всё равно виден

    def test_capacity_failure_does_not_change_auth_state(self):
        from atlas_core.agent_registry import ProfileService
        from atlas_core.capacity import persist_capacity
        svc = ProfileService()
        pid = self._profile()
        svc.set_state(pid, "READY", expected_version=1)        # авторизован
        persist_capacity(pid, self._ok(68.0))
        persist_capacity(pid, self._fail("CODEX_RATELIMITS_FAILED"))  # сбой ёмкости
        # auth-состояние НЕ деградировало из-за сбоя ёмкости
        self.assertEqual(svc.get_profile(pid)["state"], "READY")


class TestBoundedRefresh(CapBase):
    def setUp(self):
        super().setUp()
        import atlas_core.capacity as cap
        cap._cap_last.clear()  # детерминизм для cooldown/single-flight
        self._cap = cap

    def _reg(self):
        reg = {"profiles": {"codex-plus-01": {"provider": "codex", "root_path": "/x",
               "executable_path": "/x/codex", "runtime_user": "u1"}}}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(reg, f)
        f.close()
        from atlas_core.agent_registry import ProfileService
        ProfileService().upsert_profile(alias="codex-plus-01", provider="codex",
                                        unix_label="u", enabled=True, schedulable=True)
        return f.name

    def _prober(self, provider, root, exe, user, timeout):
        return {"provider": "codex", "plan": "plus", "auth_ok": True, "source": "codex-app-server",
                "error_code": "", "windows": [{"id": "7d", "label": "Неделя", "used_pct": 10.0,
                "remaining_pct": 90.0, "reset_at": None, "window_mins": 10080}]}

    def test_cooldown_blocks_rapid_refresh(self):
        path = self._reg()
        first = self._cap.reconcile_capacity(prober=self._prober, registry_path=path, force=False)
        self.assertEqual(first[0]["state"], "REFRESHED")
        second = self._cap.reconcile_capacity(prober=self._prober, registry_path=path, force=False)
        self.assertEqual(second[0]["state"], "COOLDOWN")       # интервал не обойдён
        self.assertIn("cooldown_remaining_s", second[0])
        os.unlink(path)

    def test_force_bypasses_cooldown_trusted_path(self):
        path = self._reg()
        self._cap.reconcile_capacity(prober=self._prober, registry_path=path, force=False)
        forced = self._cap.reconcile_capacity(prober=self._prober, registry_path=path, force=True)
        self.assertEqual(forced[0]["state"], "REFRESHED")      # доверенный deploy-путь
        os.unlink(path)

    def test_single_flight_reports_in_progress(self):
        path = self._reg()
        self._cap._cap_lock.acquire()  # эмулируем идущий refresh
        try:
            out = self._cap.reconcile_capacity(prober=self._prober, registry_path=path,
                                               aliases=["codex-plus-01"], force=True)
        finally:
            self._cap._cap_lock.release()
        self.assertEqual(out[0]["state"], "REFRESH_IN_PROGRESS")
        os.unlink(path)


class TestCodexProbeHandshake(CapBase):
    """probe_codex_capacity читает и result, и error через fake app-server."""

    def _fake_server(self, script_body: str) -> str:
        import stat
        f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        f.write("#!/usr/bin/python3\n" + script_body)
        f.close()
        os.chmod(f.name, os.stat(f.name).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return f.name

    def test_success_windows(self):
        from atlas_core.capacity import probe_codex_capacity
        body = r'''
import sys, json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    m=json.loads(line)
    if m.get("method")=="initialize":
        print(json.dumps({"id":1,"result":{"userAgent":"x"}}),flush=True)
    elif m.get("method")=="account/read":
        print(json.dumps({"id":2,"result":{"account":{"type":"chatgpt","email":"a@b.com","planType":"plus"}}}),flush=True)
    elif m.get("method")=="account/rateLimits/read":
        print(json.dumps({"id":3,"result":{"rateLimits":{"primary":{"usedPercent":68,"windowDurationMins":10080,"resetsAt":1786225197},"secondary":None}}}),flush=True)
'''
        exe = self._fake_server(body)
        res = probe_codex_capacity("/tmp", executable=exe, run_as_user=None, timeout=10.0)
        os.unlink(exe)
        self.assertEqual(res["plan"], "plus")
        self.assertTrue(res["auth_ok"])
        self.assertEqual(res["error_code"], "")
        self.assertEqual(len(res["windows"]), 1)               # только primary (secondary=null)
        self.assertEqual(res["windows"][0]["used_pct"], 68.0)
        self.assertNotIn("@", json.dumps(res))                 # email не утёк

    def test_ratelimits_error_distinguished(self):
        from atlas_core.capacity import probe_codex_capacity
        body = r'''
import sys, json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    m=json.loads(line)
    if m.get("method")=="initialize":
        print(json.dumps({"id":1,"result":{"ok":True}}),flush=True)
    elif m.get("method")=="account/read":
        print(json.dumps({"id":2,"result":{"account":{"planType":"plus"}}}),flush=True)
    elif m.get("method")=="account/rateLimits/read":
        print(json.dumps({"id":3,"error":{"code":-32000,"message":"rate limit backend down"}}),flush=True)
'''
        exe = self._fake_server(body)
        res = probe_codex_capacity("/tmp", executable=exe, run_as_user=None, timeout=10.0)
        os.unlink(exe)
        self.assertEqual(res["error_code"], "CODEX_RATELIMITS_FAILED")   # error распознан
        self.assertTrue(res["auth_ok"])
        self.assertEqual(res["plan"], "plus")
        self.assertEqual(res["windows"], [])

    def test_init_error_distinguished(self):
        from atlas_core.capacity import probe_codex_capacity
        body = r'''
import sys, json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    m=json.loads(line)
    if m.get("method")=="initialize":
        print(json.dumps({"id":1,"error":{"code":-32601,"message":"bad init"}}),flush=True)
'''
        exe = self._fake_server(body)
        res = probe_codex_capacity("/tmp", executable=exe, run_as_user=None, timeout=10.0)
        os.unlink(exe)
        self.assertEqual(res["error_code"], "CODEX_INIT_FAILED")


class TestClaudeProbe(CapBase):
    def _fake_claude(self, auth_json: str, stream_body: str = "") -> str:
        """Fake claude: `auth status --json` печатает auth_json; `-p … stream-json`
        печатает stream_body (официальные rate_limit_event строки)."""
        import stat
        f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        f.write("#!/usr/bin/python3\nimport sys\nargs=sys.argv[1:]\n"
                f"if 'auth' in args:\n    print({auth_json!r})\n"
                f"elif '-p' in args:\n    sys.stdout.write({stream_body!r})\n")
        f.close()
        os.chmod(f.name, os.stat(f.name).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return f.name

    def test_auth_only_without_start_window(self):
        from atlas_core.capacity import probe_claude_capacity
        exe = self._fake_claude('{"loggedIn": true, "subscriptionType": "pro"}')
        res = probe_claude_capacity("/tmp", executable=exe, run_as_user=None, start_window=False)
        os.unlink(exe)
        self.assertTrue(res["auth_ok"])
        self.assertEqual(res["plan"], "pro")
        self.assertEqual(res["windows"], [])
        self.assertEqual(res["error_code"], "CLAUDE_NEEDS_START_WINDOW")  # без затрат подписки

    def test_start_window_parses_official_event(self):
        from atlas_core.capacity import probe_claude_capacity
        stream = ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
                  '"resetsAt":1785873000,"rateLimitType":"five_hour"},"session_id":"s"}\n'
                  '{"is_error":false,"result":"ok","type":"result"}\n')
        exe = self._fake_claude('{"loggedIn": true, "subscriptionType": "pro"}', stream)
        # timeout=2 → status-line проба быстро исчерпывает дедлайн (fake exe не
        # интерактивен) и падает в rate_limit_event fallback.
        res = probe_claude_capacity("/tmp", executable=exe, run_as_user=None,
                                    start_window=True, timeout=2.0)
        os.unlink(exe)
        self.assertEqual(res["source"], "claude-stream-json")  # fallback
        self.assertEqual(len(res["windows"]), 1)
        self.assertEqual(res["windows"][0]["status"], "allowed")
        self.assertEqual(res["error_code"], "")  # fallback успешен → без диагностики

    def test_statusline_numeric_preferred(self):
        # Числовой status-line (rate_limits) предпочтительнее rate_limit_event.
        import json as _j

        from atlas_core.capacity import _statusline_from_spool
        spool = _j.dumps({"rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
            "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}})
        wins = {w["id"]: w for w in _statusline_from_spool(spool)}
        self.assertEqual(wins["5h"]["used_pct"], 23.5)
        self.assertEqual(wins["5h"]["remaining_pct"], 76.5)   # 100 - 23.5
        self.assertEqual(wins["7d"]["used_pct"], 41.2)
        self.assertEqual(wins["5h"]["status"], "allowed")

    def test_not_authenticated(self):
        from atlas_core.capacity import probe_claude_capacity
        exe = self._fake_claude('{"loggedIn": false}')
        res = probe_claude_capacity("/tmp", executable=exe, run_as_user=None, start_window=True)
        os.unlink(exe)
        self.assertFalse(res["auth_ok"])
        self.assertEqual(res["error_code"], "CLAUDE_NOT_AUTHENTICATED")
