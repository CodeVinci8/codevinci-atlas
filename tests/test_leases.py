"""Тесты одного writer и reconciliation (Master Spec §13.4, §30.4)."""

import time

from atlas_core.errors import AtlasError, ErrorCode
from atlas_core.leases import LeaseStore
from atlas_core.store import Store
from atlas_test_base import AtlasTestCase


class TestLeases(AtlasTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.leases = LeaseStore(self.store, ttl_s=1.0, stale_grace_s=0.5)

    def tearDown(self):
        self.store.close()
        super().tearDown()

    def test_single_writer(self):
        self.leases.acquire(project_id="p", worktree="wt", holder="A")
        self.assertEqual(self.leases.active_count("p", "wt"), 1)

    def test_second_acquire_conflicts(self):
        self.leases.acquire(project_id="p", worktree="wt", holder="A")
        with self.assertRaises(AtlasError) as cm:
            self.leases.acquire(project_id="p", worktree="wt", holder="B")
        self.assertEqual(cm.exception.classified.code, ErrorCode.WORKTREE_CONFLICT)
        # по-прежнему ровно один writer
        self.assertEqual(self.leases.active_count("p", "wt"), 1)

    def test_release_allows_new_writer(self):
        l = self.leases.acquire(project_id="p", worktree="wt", holder="A")
        self.leases.release(l.id)
        self.assertEqual(self.leases.active_count("p", "wt"), 0)
        self.leases.acquire(project_id="p", worktree="wt", holder="B")
        self.assertEqual(self.leases.active_count("p", "wt"), 1)

    def test_stale_lease_not_auto_stolen(self):
        self.leases.acquire(project_id="p", worktree="wt", holder="A")
        time.sleep(1.7)  # просрочка + потеря heartbeat
        # автоугон запрещён: новый acquire всё равно конфликтует до reconciliation
        with self.assertRaises(AtlasError) as cm:
            self.leases.acquire(project_id="p", worktree="wt", holder="B")
        self.assertEqual(cm.exception.classified.code, ErrorCode.WORKTREE_CONFLICT)

    def test_reconcile_requires_dead_process_and_clean_git(self):
        self.leases.acquire(project_id="p", worktree="wt", holder="A")
        time.sleep(1.7)
        # процесс жив → reconciliation отклоняется
        self.assertFalse(self.leases.reconcile(
            project_id="p", worktree="wt",
            process_alive=lambda l: True, git_clean=lambda l: True))
        # git грязный → reconciliation отклоняется
        self.assertFalse(self.leases.reconcile(
            project_id="p", worktree="wt",
            process_alive=lambda l: False, git_clean=lambda l: False))
        # процесс мёртв и git чист → освобождение
        self.assertTrue(self.leases.reconcile(
            project_id="p", worktree="wt",
            process_alive=lambda l: False, git_clean=lambda l: True))
        self.assertEqual(self.leases.active_count("p", "wt"), 0)
        # теперь новый writer допустим
        self.leases.acquire(project_id="p", worktree="wt", holder="B")
        self.assertEqual(self.leases.active_count("p", "wt"), 1)

    def test_heartbeat_extends_lease(self):
        l = self.leases.acquire(project_id="p", worktree="wt", holder="A")
        time.sleep(0.6)
        self.leases.heartbeat(l.id)
        time.sleep(0.6)
        # живой heartbeat → аренда не просрочена, второй writer запрещён
        with self.assertRaises(AtlasError):
            self.leases.acquire(project_id="p", worktree="wt", holder="B")
