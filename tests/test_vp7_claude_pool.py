"""VP-7 минимальный Claude Builder-пул (§17.2/§17.3): выбор из двух профилей,
sticky-назначение, эксклюзивная аренда/один writer, safe rate-limit handoff,
независимость Reviewer, консервативный fallback, отсутствие утечек. Selection —
детерминированный, без silent fallback."""

from __future__ import annotations

import os

from atlas_test_base import AtlasTestCase


class PoolBase(AtlasTestCase):
    """База с инициализированным движком БД (audit/ProfileService требуют сессию)."""

    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        s = load_settings()
        init_engine(s.db_url, s.db_path)
        Base.metadata.create_all(get_engine())


def _prof(alias, state="READY", cap_status="AVAILABLE", stale=False, windows=None,
          observed_at="2026-08-04T00:00:00+00:00", enabled=True, schedulable=True):
    return {"id": "id-" + alias, "alias": alias, "provider": "claude", "state": state,
            "enabled": enabled, "schedulable": schedulable,
            "capacity": {"status": cap_status, "stale": stale, "observed_at": observed_at,
                         "windows": windows or []}}


def _win(wid, remaining=None, status=None, reset_at=None):
    return {"id": wid, "label": wid, "used_pct": (None if remaining is None else 100 - remaining),
            "remaining_pct": remaining, "reset_at": reset_at, "window_mins": 300, "status": status}


class TestPoolSelection(PoolBase):
    def test_profile1_low_selects_profile2(self):
        from atlas_core.claude_pool import select_builder
        profiles = [_prof("claude-pro-01", cap_status="LOW"),
                    _prof("claude-pro-02", cap_status="AVAILABLE")]
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-02")  # AVAILABLE > LOW
        self.assertTrue(d.ok)

    def test_profile1_exhausted_selects_profile2(self):
        from atlas_core.claude_pool import select_builder
        profiles = [_prof("claude-pro-01", cap_status="EXHAUSTED",
                          windows=[_win("5h", status="rejected")]),
                    _prof("claude-pro-02", cap_status="AVAILABLE",
                          windows=[_win("5h", status="allowed")])]
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-02")  # exhausted исключён

    def test_weekly_exhaustion_overrides_healthy_5h(self):
        from atlas_core.claude_pool import select_builder
        # p1: 5h здоров (90% остаток) но 7d исчерпан → статус EXHAUSTED (min окна).
        profiles = [_prof("claude-pro-01", cap_status="EXHAUSTED",
                          windows=[_win("5h", remaining=90), _win("7d", status="rejected")]),
                    _prof("claude-pro-02", cap_status="AVAILABLE",
                          windows=[_win("5h", remaining=50)])]
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-02")

    def test_numeric_min_prefers_more_remaining_when_fresh(self):
        from atlas_core.claude_pool import select_builder
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE",
                          windows=[_win("5h", remaining=20), _win("7d", remaining=40)]),
                    _prof("claude-pro-02", cap_status="AVAILABLE",
                          windows=[_win("5h", remaining=70), _win("7d", remaining=60)])]
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-02")  # min(70,60)=60 > min(20,40)=20

    def test_stale_numbers_not_used_for_numeric_rank(self):
        from atlas_core.claude_pool import select_builder
        # p1 имеет больше остатка, но наблюдение STALE → числовой критерий не применяется,
        # оба AVAILABLE → LRU/alias решает (детерминированно p1 по alias).
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE", stale=True,
                          windows=[_win("5h", remaining=90)]),
                    _prof("claude-pro-02", cap_status="AVAILABLE", stale=True,
                          windows=[_win("5h", remaining=10)])]
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-01")  # alias tie-break, не по протухшим числам

    def test_sticky_assignment_keeps_active(self):
        from atlas_core.claude_pool import select_builder
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE"),
                    _prof("claude-pro-02", cap_status="AVAILABLE")]
        d = select_builder(profiles, sticky_alias="claude-pro-02")
        self.assertEqual(d.effective_profile, "claude-pro-02")  # affinity к активной сессии

    def test_both_unavailable_conservative_fallback(self):
        from atlas_core.claude_pool import pool_summary, select_builder
        profiles = [_prof("claude-pro-01", cap_status="UNKNOWN"),
                    _prof("claude-pro-02", cap_status="UNKNOWN")]
        d = select_builder(profiles)
        self.assertTrue(d.ok)  # READY + консервативный fallback (не выдумываем ёмкость)
        self.assertEqual(d.effective_profile, "claude-pro-01")  # LRU/alias детерминированно
        summ = pool_summary(profiles)
        self.assertTrue(summ["conservative_fallback"])  # причина раскрыта

    def test_no_eligible_when_both_exhausted(self):
        from atlas_core.claude_pool import select_builder
        profiles = [_prof("claude-pro-01", cap_status="EXHAUSTED"),
                    _prof("claude-pro-02", cap_status="EXHAUSTED")]
        d = select_builder(profiles)
        self.assertFalse(d.ok)
        self.assertEqual(d.reason_code, "NO_ELIGIBLE_PROFILE")  # без silent fallback


class TestRegistryDrivenPool(PoolBase):
    def test_disabled_profile_excluded_from_pool(self):
        from atlas_core.claude_pool import claude_pool_aliases, select_builder
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE"),
                    _prof("claude-pro-02", cap_status="AVAILABLE",
                          enabled=False, schedulable=False, state="DISABLED")]
        # registry-driven открытие: только активный профиль в пуле.
        self.assertEqual(claude_pool_aliases(profiles), ["claude-pro-01"])
        d = select_builder(profiles)
        self.assertEqual(d.effective_profile, "claude-pro-01")  # disabled не выбирается

    def test_single_active_profile_selected(self):
        from atlas_core.claude_pool import claude_pool_aliases, pool_summary
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE"),
                    _prof("claude-pro-02", enabled=False, schedulable=False, state="DISABLED")]
        self.assertEqual(claude_pool_aliases(profiles), ["claude-pro-01"])
        summ = pool_summary(profiles)
        self.assertEqual(summ["members"], ["claude-pro-01"])   # pro-02 не член
        self.assertEqual(summ["authorized_count"], 1)
        self.assertNotIn("claude-pro-02", summ["members"])     # исключён из UI-сводки

    def test_new_profile_added_without_code_change(self):
        # VP-8 attach: новый eligible профиль просто появляется в реестре → в пуле.
        from atlas_core.claude_pool import claude_pool_aliases
        profiles = [_prof("claude-pro-01"), _prof("claude-pro-03")]  # без правок кода
        self.assertEqual(claude_pool_aliases(profiles), ["claude-pro-01", "claude-pro-03"])


class TestSingleProfileRateLimit(PoolBase):
    def setUp(self):
        super().setUp()
        from atlas_core.agent_registry import ProfileService
        self.svc = ProfileService()
        pid = self.svc.upsert_profile("claude-pro-01", "claude", unix_label="u")
        self.svc.set_state(pid, "READY", expected_version=1)
        # единственный активный Claude; pro-02 disabled
        pid2 = self.svc.upsert_profile("claude-pro-02", "claude", unix_label="u",
                                       enabled=False, schedulable=False)
        self.svc.set_state(pid2, "AUTH_REQUIRED", expected_version=1)

    def test_single_profile_ratelimit_no_handoff(self):
        from atlas_core.claude_pool import handle_rate_limit
        # единственный активный профиль исчерпан → нет альтернативы → no handoff,
        # retry запрещён (Run уходит в owner-action; без retry-loop/фейкового handoff).
        r = handle_rate_limit(self.svc.list_profiles(), alias="claude-pro-01",
                              rate_limit_type="five_hour", resets_at_epoch=1785871800)
        self.assertEqual(r["exhausted_alias"], "claude-pro-01")
        self.assertFalse(r["ok"])
        self.assertFalse(r["retry_allowed"])
        self.assertEqual(r["next_effective"], "")


class TestReviewerIndependence(PoolBase):
    def test_reviewer_differs_from_builder(self):
        from atlas_core.claude_pool import reviewer_independent
        self.assertTrue(reviewer_independent("claude-pro-01", "codex-plus-01"))
        self.assertTrue(reviewer_independent("claude-pro-01", "claude-pro-02"))
        self.assertFalse(reviewer_independent("claude-pro-01", "claude-pro-01"))
        self.assertFalse(reviewer_independent("claude-pro-01", ""))


class TestRateLimitHandoff(PoolBase):
    def setUp(self):
        super().setUp()
        from atlas_core.agent_registry import ProfileService
        self.svc = ProfileService()
        for a in ("claude-pro-01", "claude-pro-02"):
            pid = self.svc.upsert_profile(a, "claude", unix_label="u")
            self.svc.set_state(pid, "READY", expected_version=1)

    def test_ratelimit_marks_exhausted_and_hands_off(self):
        from atlas_core.claude_pool import handle_rate_limit
        # p1 получает точный five_hour rate-limit → исчерпан, handoff на p2.
        r = handle_rate_limit(self.svc.list_profiles(), alias="claude-pro-01",
                              rate_limit_type="five_hour", resets_at_epoch=1785871800)
        self.assertEqual(r["exhausted_alias"], "claude-pro-01")
        self.assertEqual(r["window"], "5h")
        self.assertEqual(r["next_effective"], "claude-pro-02")  # ретрай на другом
        self.assertTrue(r["retry_allowed"])
        # durable: p1 теперь EXHAUSTED в capacity view
        views = {p["alias"]: p for p in self.svc.list_profiles()}
        self.assertEqual(views["claude-pro-01"]["capacity"]["status"], "EXHAUSTED")

    def test_no_retry_when_other_also_exhausted(self):
        from atlas_core.claude_pool import handle_rate_limit
        # исчерпываем p2 заранее
        handle_rate_limit(self.svc.list_profiles(), alias="claude-pro-02",
                          rate_limit_type="seven_day", resets_at_epoch=1785871800)
        r = handle_rate_limit(self.svc.list_profiles(), alias="claude-pro-01",
                              rate_limit_type="five_hour", resets_at_epoch=1785871800)
        self.assertFalse(r["ok"])            # некуда передавать
        self.assertFalse(r["retry_allowed"])  # нет второго ретрая
        self.assertEqual(r["next_effective"], "")

    def test_no_credential_or_identifier_leak(self):
        import json

        from atlas_core.claude_pool import handle_rate_limit, pool_summary_live
        handle_rate_limit(self.svc.list_profiles(), alias="claude-pro-01",
                          rate_limit_type="five_hour", resets_at_epoch=1785871800)
        blob = json.dumps(pool_summary_live()) + json.dumps(self.svc.list_profiles())
        for marker in ("@", "token", "cookie", "/root/", "/home/", "session_id", "oauth"):
            self.assertNotIn(marker, blob.lower())


class TestPoolSummary(PoolBase):
    def test_summary_counts_and_no_fake_combined_pct(self):
        from atlas_core.claude_pool import pool_summary
        profiles = [_prof("claude-pro-01", cap_status="AVAILABLE",
                          windows=[_win("5h", status="allowed", reset_at="2026-08-04T19:50:00+00:00")]),
                    _prof("claude-pro-02", state="LEASED", cap_status="EXHAUSTED",
                          windows=[_win("5h", status="rejected", reset_at="2026-08-04T19:30:00+00:00")])]
        summ = pool_summary(profiles, last_reason="reason", active_alias="claude-pro-02")
        self.assertEqual(summ["authorized_count"], 2)   # READY + LEASED
        self.assertEqual(summ["eligible_count"], 1)     # только p1 (p2 exhausted)
        self.assertEqual(summ["active_alias"], "claude-pro-02")
        self.assertEqual(summ["next_reset"], "2026-08-04T19:30:00+00:00")  # ближайший
        self.assertNotIn("combined", summ)              # нет фиктивного объединённого %
        self.assertNotIn("total_pct", summ)
