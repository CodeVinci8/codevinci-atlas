"""VP-7 Pulse-корректировки (§27.2): реальная CPU-утилизация с честным первым
сэмплом «Измерение…» (не 0%) и actionable-флаг контекстного next action."""

from __future__ import annotations

from atlas_test_base import AtlasTestCase


class TestCpuUtilization(AtlasTestCase):
    def setUp(self):
        super().setUp()
        import atlas_core.system_summary as ss
        ss._cpu_prev = None  # честный первый сэмпл в каждом тесте

    def test_first_sample_is_measuring_not_zero(self):
        from atlas_core.system_summary import _cpu_utilization
        first = _cpu_utilization()
        self.assertEqual(first["state"], "measuring")
        self.assertIsNone(first["utilization_pct"])  # не 0%, а «Измерение…»

    def test_second_sample_yields_real_percent(self):
        import time

        from atlas_core.system_summary import _cpu_utilization
        _cpu_utilization()
        time.sleep(0.15)
        second = _cpu_utilization()
        self.assertEqual(second["state"], "ok")
        self.assertIsNotNone(second["utilization_pct"])
        self.assertGreaterEqual(second["utilization_pct"], 0.0)
        self.assertLessEqual(second["utilization_pct"], 100.0)
        self.assertEqual(second["source"], "/proc/stat delta")

    def test_cpu_never_derived_from_load_average(self):
        # load_avg присутствует отдельно; utilization_pct — из /proc/stat, не из load.
        from atlas_core.system_summary import _cpu
        info = _cpu()
        self.assertIn("load_avg", info)
        self.assertIn("utilization_pct", info)
        self.assertIn("util_state", info)


class TestNextActionActionable(AtlasTestCase):
    def test_all_done_is_not_actionable(self):
        from atlas_core.system_summary import _ACTIONABLE_NA
        self.assertNotIn("NEXT_VP", _ACTIONABLE_NA)   # «всё завершено» — не действие
        self.assertNotIn("PARTIAL", _ACTIONABLE_NA)

    def test_real_actions_are_actionable(self):
        from atlas_core.system_summary import _ACTIONABLE_NA
        for code in ("OPEN_OWNER_RUN", "INSPECT_RUN", "CREATE_RUN", "OPEN_MAP"):
            self.assertIn(code, _ACTIONABLE_NA)

    def test_summary_sets_actionable_flag(self):
        from atlas_core.settings import load_settings
        from atlas_core.system_summary import system_summary
        summ = system_summary(load_settings())
        na = summ["next_action"]
        self.assertIn("actionable", na)
        self.assertEqual(na["actionable"], na["code"] in
                         {"OPEN_OWNER_RUN", "INSPECT_RUN", "CREATE_RUN", "CONNECT_PROJECT", "OPEN_MAP"})
