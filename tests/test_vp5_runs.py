"""VP-5 Runs/leases — durable lifecycle, идемпотентность, optimistic concurrency,
durable-события через свежий процесс Core, persist router-решений, ссылки на
provider-сессии без секретов, и эксклюзивность аренд (worktree + profile)
через конкурентные негативные проверки (Master Spec §13.4, §17.4, §30)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP5Base(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine, session_scope
        from atlas_core.orm import Base, Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        self.db_path = self.settings.db_path
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        with session_scope() as s:
            s.add(Project(id="proj_p", name="Синтетика", source_kind="local_git",
                          source_location="/x", status="connected",
                          created_at=_now(), updated_at=_now()))
            s.add(Project(id="proj_off", name="Off", source_kind="local_git",
                          source_location="/y", status="disconnected",
                          created_at=_now(), updated_at=_now()))
            s.commit()
        from atlas_core.runs import RunService
        self.runs = RunService()


class TestRunLifecycle(VP5Base):
    def test_create_run_and_state(self):
        r = self.runs.create_run("proj_p", work_order_id="wo1", vp_key="VP-5")
        self.assertEqual(r["state"], "QUEUED")
        self.assertEqual(r["version"], 1)

    def test_create_run_project_must_be_available(self):
        from atlas_core.runs import RunError
        with self.assertRaises(RunError) as cm:
            self.runs.create_run("proj_off")
        self.assertEqual(cm.exception.code, "PROJECT_NOT_AVAILABLE")

    def test_create_run_idempotent_by_dedup_key(self):
        a = self.runs.create_run("proj_p", dedup_key="k-1")
        b = self.runs.create_run("proj_p", dedup_key="k-1")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.runs.list_runs(project_id="proj_p")), 1)

    def test_create_run_idempotency_key(self):
        a = self.runs.create_run("proj_p", idempotency_key="idem-9")
        b = self.runs.create_run("proj_p", idempotency_key="idem-9")
        self.assertEqual(a["id"], b["id"])

    def test_valid_transition_increments_version(self):
        r = self.runs.create_run("proj_p")
        r2 = self.runs.transition(r["id"], "PREPARING", expected_version=1)
        self.assertEqual(r2["state"], "PREPARING")
        self.assertEqual(r2["version"], 2)

    def test_invalid_transition_rejected(self):
        from atlas_core.runs import RunError
        r = self.runs.create_run("proj_p")
        with self.assertRaises(RunError) as cm:
            self.runs.transition(r["id"], "SUCCEEDED", expected_version=1)
        self.assertEqual(cm.exception.code, "INVALID_TRANSITION")

    def test_optimistic_concurrency_stale_version_conflict(self):
        from atlas_core.runs import RunError
        r = self.runs.create_run("proj_p")
        self.runs.transition(r["id"], "PREPARING", expected_version=1)  # → v2
        # Повторный переход со СТАРОЙ версией отклонён без перезаписи.
        with self.assertRaises(RunError) as cm:
            self.runs.transition(r["id"], "RUNNING", expected_version=1)
        self.assertEqual(cm.exception.code, "VERSION_CONFLICT")
        self.assertEqual(self.runs.get_run(r["id"])["state"], "PREPARING")

    def test_terminal_states_are_terminal(self):
        from atlas_core.runs import RunError
        r = self.runs.create_run("proj_p")
        self.runs.transition(r["id"], "CANCELLED", expected_version=1)
        with self.assertRaises(RunError):
            self.runs.transition(r["id"], "PREPARING", expected_version=2)


class TestDurableEvents(VP5Base):
    def test_events_ordered_and_survive_fresh_core(self):
        r = self.runs.create_run("proj_p")
        rid = r["id"]
        self.runs.append_event(rid, "a.one", {"n": 1})
        self.runs.append_event(rid, "a.two", {"n": 2})
        self.runs.transition(rid, "PREPARING", expected_version=1)  # добавляет событие run.transition
        # Свежий процесс Core: заново инициализируем движок на том же файле.
        from atlas_core.db import init_engine
        init_engine(self.settings.db_url, self.settings.db_path)
        from atlas_core.runs import RunService
        fresh = RunService()
        evs = fresh.events(rid)
        seqs = [e["seq"] for e in evs]
        self.assertEqual(seqs, sorted(seqs))
        self.assertGreaterEqual(len(evs), 4)  # run.created + a.one + a.two + run.transition
        types = [e["type"] for e in evs]
        self.assertIn("a.one", types)
        self.assertIn("run.transition", types)

    def test_provider_session_rejects_secret_and_stores_handle(self):
        from atlas_core.runs import RunError
        r = self.runs.create_run("proj_p")
        with self.assertRaises(RunError) as cm:
            self.runs.record_provider_session(
                r["id"], provider="claude",
                session_id="sk-ant-abcdef0123456789abcdef0123456789", role="builder")
        self.assertEqual(cm.exception.code, "SECRET_LEAK")
        # Обычный session-handle сохраняется; transcript/credentials не хранятся.
        self.runs.record_provider_session(r["id"], provider="claude",
                                          session_id="sess_ABC123", role="builder", profile_id="cx01")
        ps = self.runs.provider_sessions(r["id"])
        self.assertEqual(ps[0]["session_id"], "sess_ABC123")
        self.assertNotIn("transcript", ps[0])

    def test_router_decision_persisted_requested_effective_reason(self):
        from atlas_core.contracts import Role
        from atlas_core.router import Candidate, route_profile
        r = self.runs.create_run("proj_p")
        d = route_profile(Role.BUILDER, [Candidate("claude-pro-01", "claude")])
        self.runs.record_router_decision(r["id"], d)
        rows = self.runs.router_decisions(r["id"])
        self.assertEqual(rows[0]["effective_profile"], "claude-pro-01")
        self.assertIn("reason_code", rows[0])


class TestLeaseExclusivity(VP5Base):
    def test_profile_lease_exclusive_concurrent(self):
        from atlas_core.errors import AtlasError
        from atlas_core.run_leases import RunLeaseService
        a = RunLeaseService(self.db_path)
        b = RunLeaseService(self.db_path)
        try:
            a.acquire(profile_id="cx01", run_id="run_a", role="builder")
            # Вторая одновременная аренда того же профиля отклонена (нет второго writer).
            with self.assertRaises(AtlasError):
                b.acquire(profile_id="cx01", run_id="run_b", role="builder")
            self.assertEqual(a.active_count("cx01"), 1)
        finally:
            a.close(); b.close()

    def test_profile_release_before_acquire_no_two_writers(self):
        from atlas_core.run_leases import RunLeaseService
        svc = RunLeaseService(self.db_path)
        try:
            lease = svc.acquire(profile_id="cx01", run_id="run_a", role="builder")
            svc.release(lease.id)
            self.assertEqual(svc.active_count("cx01"), 0)
            # После release тот же профиль свободен → повторный acquire успешен.
            svc.acquire(profile_id="cx01", run_id="run_a", role="builder")
            self.assertEqual(svc.active_count("cx01"), 1)
        finally:
            svc.close()

    def test_worktree_writer_exclusive_concurrent(self):
        from atlas_core.errors import AtlasError
        from atlas_core.wsleases import WorktreeLeaseService
        a = WorktreeLeaseService(self.db_path)
        b = WorktreeLeaseService(self.db_path)
        try:
            a.acquire(project_id="proj_p", worktree="/wt/x", role="builder")
            with self.assertRaises(AtlasError):
                b.acquire(project_id="proj_p", worktree="/wt/x", role="builder")
            self.assertEqual(a.active_count("/wt/x"), 1)
        finally:
            a.close(); b.close()

    def test_stale_lease_not_auto_stolen(self):
        # Просроченная аренда с потерянным heartbeat не угоняется без reconcile.
        from atlas_core.errors import AtlasError
        from atlas_core.run_leases import RunLeaseService
        svc = RunLeaseService(self.db_path, ttl_s=0.0, stale_grace_s=0.0)
        other = RunLeaseService(self.db_path, ttl_s=0.0, stale_grace_s=0.0)
        try:
            svc.acquire(profile_id="cx01", run_id="run_a", role="builder")
            with self.assertRaises(AtlasError):
                other.acquire(profile_id="cx01", run_id="run_b", role="builder")
        finally:
            svc.close(); other.close()


if __name__ == "__main__":
    import unittest
    unittest.main()
