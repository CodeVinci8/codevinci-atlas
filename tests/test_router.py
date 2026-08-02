"""VP-5 router (Master Spec §17.2/§17.3): детерминированный выбор профиля/модели
с reason-кодами и БЕЗ silent fallback."""

import unittest

from atlas_core.contracts import Role
from atlas_core.router import Candidate, ReasonCode, resolve_model, route_profile


def _c(alias, provider, **kw):
    return Candidate(alias=alias, provider=provider, **kw)


class TestRouteProfile(unittest.TestCase):
    def test_role_default_provider_builder_is_claude(self):
        # Для builder выбирается claude-профиль, codex-профиль не подходит по роли.
        d = route_profile(Role.BUILDER, [_c("codex-plus-01", "codex"),
                                         _c("claude-pro-01", "claude")])
        self.assertTrue(d.ok)
        self.assertEqual(d.effective_profile, "claude-pro-01")

    def test_planner_and_reviewer_are_codex(self):
        cands = [_c("codex-plus-01", "codex"), _c("claude-pro-01", "claude")]
        self.assertEqual(route_profile(Role.PLANNER, cands).effective_profile, "codex-plus-01")
        self.assertEqual(route_profile(Role.REVIEWER, cands).effective_profile, "codex-plus-01")

    def test_owner_override_eligible(self):
        d = route_profile(Role.REVIEWER, [_c("codex-plus-01", "codex"),
                                          _c("codex-plus-02", "codex")],
                          requested_profile="codex-plus-02")
        self.assertTrue(d.ok)
        self.assertEqual(d.effective_profile, "codex-plus-02")
        self.assertEqual(d.reason_code, ReasonCode.OWNER_OVERRIDE)

    def test_owner_override_unavailable_NO_silent_fallback(self):
        # Запрошен профиль в COOLDOWN → НЕ подставляем другой; effective пуст.
        d = route_profile(Role.REVIEWER, [_c("codex-plus-01", "codex", state="COOLDOWN"),
                                          _c("codex-plus-02", "codex", state="READY")],
                          requested_profile="codex-plus-01")
        self.assertFalse(d.ok)
        self.assertEqual(d.effective_profile, "")
        self.assertEqual(d.effective_model, "")
        self.assertEqual(d.reason_code, ReasonCode.OWNER_OVERRIDE_UNAVAILABLE)

    def test_owner_override_missing_profile_no_fallback(self):
        d = route_profile(Role.BUILDER, [_c("claude-pro-01", "claude")],
                          requested_profile="claude-pro-99")
        self.assertFalse(d.ok)
        self.assertEqual(d.reason_code, ReasonCode.OWNER_OVERRIDE_UNAVAILABLE)

    def test_excludes_cooldown_error_exhausted_unschedulable(self):
        d = route_profile(Role.BUILDER, [
            _c("claude-pro-01", "claude", state="COOLDOWN"),
            _c("claude-pro-02", "claude", state="ERROR"),
            _c("claude-pro-03", "claude", capacity_status="EXHAUSTED"),
            _c("claude-pro-04", "claude", schedulable=False),
            _c("claude-pro-05", "claude", state="READY"),
        ])
        self.assertTrue(d.ok)
        self.assertEqual(d.effective_profile, "claude-pro-05")

    def test_no_eligible_profile(self):
        d = route_profile(Role.BUILDER, [_c("claude-pro-01", "claude", state="AUTH_REQUIRED"),
                                         _c("codex-plus-01", "codex", state="READY")])
        self.assertFalse(d.ok)
        self.assertEqual(d.reason_code, ReasonCode.NO_ELIGIBLE_PROFILE)

    def test_affinity_preferred_over_non_affinity(self):
        d = route_profile(Role.BUILDER, [
            _c("claude-pro-01", "claude", capacity_status="AVAILABLE"),
            _c("claude-pro-02", "claude", affinity=True, capacity_status="UNKNOWN"),
        ])
        self.assertEqual(d.effective_profile, "claude-pro-02")
        self.assertEqual(d.reason_code, ReasonCode.ROLE_READY_AFFINITY)

    def test_capacity_preferred_when_no_affinity(self):
        d = route_profile(Role.BUILDER, [
            _c("claude-pro-01", "claude", capacity_status="UNKNOWN", last_used_ms=1),
            _c("claude-pro-02", "claude", capacity_status="AVAILABLE", last_used_ms=2),
        ])
        self.assertEqual(d.effective_profile, "claude-pro-02")
        self.assertEqual(d.reason_code, ReasonCode.ROLE_READY_CAPACITY)

    def test_lru_breaks_equal_capacity(self):
        d = route_profile(Role.BUILDER, [
            _c("claude-pro-01", "claude", capacity_status="AVAILABLE", last_used_ms=500),
            _c("claude-pro-02", "claude", capacity_status="AVAILABLE", last_used_ms=100),
        ])
        # оба AVAILABLE, без affinity → выбирается least-recently-used (меньший last_used_ms)
        self.assertEqual(d.effective_profile, "claude-pro-02")
        self.assertEqual(d.reason_code, ReasonCode.ROLE_READY_LRU)

    def test_deterministic_tie_by_alias(self):
        # Полностью равные кандидаты → стабильный tie-break по alias, reason DETERMINISTIC_TIE.
        d = route_profile(Role.BUILDER, [
            _c("claude-pro-02", "claude", capacity_status="AVAILABLE", last_used_ms=0),
            _c("claude-pro-01", "claude", capacity_status="AVAILABLE", last_used_ms=0),
        ])
        self.assertEqual(d.effective_profile, "claude-pro-01")
        self.assertEqual(d.reason_code, ReasonCode.DETERMINISTIC_TIE)

    def test_decision_is_deterministic(self):
        cands = [_c("claude-pro-01", "claude", capacity_status="LOW", last_used_ms=3),
                 _c("claude-pro-02", "claude", capacity_status="AVAILABLE", last_used_ms=9)]
        a = route_profile(Role.BUILDER, cands).effective_profile
        b = route_profile(Role.BUILDER, cands).effective_profile
        self.assertEqual(a, b)


class TestResolveModel(unittest.TestCase):
    def test_requested_available(self):
        m, r = resolve_model(Role.BUILDER, ["opus-high", "opus-xhigh"], requested_model="opus-high")
        self.assertEqual(m, "opus-high")
        self.assertEqual(r, ReasonCode.MODEL_REQUESTED)

    def test_requested_unavailable_NO_fallback(self):
        m, r = resolve_model(Role.BUILDER, ["opus-high"], requested_model="opus-max")
        self.assertEqual(m, "")
        self.assertEqual(r, ReasonCode.MODEL_REQUESTED_UNAVAILABLE)

    def test_preset_default_when_not_requested(self):
        m, r = resolve_model(Role.BUILDER, ["opus-high", "opus-xhigh"],
                             preset_prefs=["opus-xhigh", "opus-high"])
        self.assertEqual(m, "opus-xhigh")
        self.assertEqual(r, ReasonCode.MODEL_PRESET_DEFAULT)

    def test_none_available(self):
        m, r = resolve_model(Role.BUILDER, [], requested_model="")
        self.assertEqual(m, "")
        self.assertEqual(r, ReasonCode.MODEL_NONE_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
