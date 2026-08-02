"""E2E: обрыв Runner → reconciliation → продолжение до успеха (Master Spec §7.5).

Сильное доказательство критерия приёмки VP-0 №8: недостаточно записать
INTERRUPTED — задача должна быть реально продолжена до одного успешного
результата без второго writer и без дублей.
"""

import tempfile
import unittest

from atlas_runner.recovery_demo import prove_recovery_to_success
from atlas_test_base import AtlasTestCase  # noqa: F401 (sys.path)


class TestRunnerRecoveryE2E(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_reconcile_continue_to_success(self):
        with tempfile.TemporaryDirectory(prefix="atlas-rec-e2e-") as tmp:
            rep = await prove_recovery_to_success(tmp)
        # прервано на середине
        self.assertTrue(0 < len(rep["processed_at_interrupt"]) < 6)
        # жёсткий краш: нет записи finished
        self.assertTrue(rep["hard_crash_no_finished_record"])
        # Runner #2 обнаружил незавершённый job при старте
        self.assertIn("req_recovery", rep["recovered_on_restart"])
        # reconciliation освободила осиротевший lease (после гибели писателя)
        self.assertTrue(rep["reconciled"])
        self.assertEqual(rep["active_after_reconcile"], 0)
        # продолжено до ОДНОГО успешного результата
        self.assertEqual(rep["final_run_state"], "SUCCEEDED")
        self.assertTrue(rep["one_final_success"])
        # все элементы обработаны ровно один раз — нет второго writer
        self.assertEqual(rep["final_processed"], [0, 1, 2, 3, 4, 5])
        self.assertFalse(rep["duplicate_processing"])
        self.assertEqual(rep["max_concurrent_writers"], 1)
        self.assertTrue(rep["single_writer_ok"])
        self.assertTrue(rep["ok"])
