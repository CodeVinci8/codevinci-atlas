"""VP-7 production runtime wiring (§17): Run-start путь реально маршрутизирует
Builder из durable-реестра, ведёт аренду/сессию, обрабатывает rate-limit.

E2E входит через ``start_builder_run`` (production-метод), а НЕ через select_builder
напрямую. Adapter инъектируется на границе провайдера (реальные вызовы — отдельный
bounded live-smoke)."""

from __future__ import annotations

import os

from atlas_test_base import AtlasTestCase


class _Res:
    def __init__(self, session_id="sess-x"):
        self.session_id = session_id
        self.structured_output = {"ok": True}


class _AdapterResult:
    def __init__(self, session_id="sess-x"):
        self.result = _Res(session_id)


class OkAdapter:
    """Успешный Builder-ответ."""
    def __init__(self):
        self.calls = []

    def start(self, job, *, profile_alias, root_path, **kw):
        self.calls.append(profile_alias)
        return _AdapterResult(f"sess-{profile_alias}")


class RateLimitAdapter:
    """Первый профиль → rate-limit; остальные → успех (для проверки switch)."""
    def __init__(self, exhaust):
        self.exhaust = exhaust
        self.calls = []

    def start(self, job, *, profile_alias, root_path, **kw):
        from atlas_core.errors import AtlasError, ErrorCode, classify
        self.calls.append(profile_alias)
        if profile_alias == self.exhaust:
            try:
                raise AtlasError(classify("rate limit exceeded"))
            except AtlasError as e:
                # гарантируем код RATE_LIMITED
                if getattr(getattr(e, "classified", None), "code", None) != ErrorCode.RATE_LIMITED:
                    e.classified.code = ErrorCode.RATE_LIMITED
                raise
        return _AdapterResult(f"sess-{profile_alias}")


class RuntimeBase(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        from atlas_core.agent_registry import ProfileService
        from atlas_core.runs import RunService
        self.svc = ProfileService()
        self.runs = RunService()

    def _project(self):
        from datetime import datetime, timezone

        from atlas_core.db import session_scope
        from atlas_core.orm import Project
        with session_scope() as s:
            if s.get(Project, "p1") is None:
                s.add(Project(id="p1", name="P", source_kind="local_git", source_location="/x",
                              status="connected", created_at=datetime.now(timezone.utc),
                              updated_at=datetime.now(timezone.utc)))
                s.commit()

    def _claude(self, alias, *, enabled=True, sched=True, state="READY"):
        pid = self.svc.upsert_profile(alias, "claude", unix_label="u", enabled=enabled,
                                      schedulable=sched)
        self.svc.set_state(pid, state, expected_version=1)
        return pid

    def _run(self):
        self._project()
        return self.runs.create_run("p1", vp_key="VP-7")["id"]


class TestRuntimeDispatch(RuntimeBase):
    def test_start_run_routes_registry_builder(self):
        # Единственный активный Claude — claude-pro-01 (registry-driven выбор).
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01")
        self._claude("claude-pro-02", enabled=False, sched=False, state="AUTH_REQUIRED")
        rid = self._run()
        ad = OkAdapter()
        r = start_builder_run(rid, adapter=ad, actor="test")
        self.assertTrue(r["ok"])
        self.assertEqual(r["effective"], "claude-pro-01")   # registry выбрал активного
        self.assertEqual(ad.calls, ["claude-pro-01"])       # disabled НЕ вызван
        # durable: провайдер-сессия и router-decision записаны; одна аренда.
        self.assertTrue(self.runs.provider_sessions(rid))
        self.assertTrue(self.runs.router_decisions(rid))
        self.assertEqual(self.runs.get_run(rid)["state"], "COLLECTING")

    def test_one_writer_one_lease(self):
        from atlas_core.db import session_scope
        from atlas_core.orm import RunLease
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01")
        rid = self._run()
        start_builder_run(rid, adapter=OkAdapter(), actor="test")
        with session_scope() as s:
            leases = s.query(RunLease).filter(RunLease.run_id == rid).all()
            # ровно одна аренда, освобождена после успеха (released_at != '')
            self.assertEqual(len(leases), 1)
            self.assertNotEqual(leases[0].released_at, "")

    def test_single_profile_rate_limit_owner_action(self):
        # Один активный Claude исчерпан → нет альтернативы → OWNER_REQUIRED, без loop.
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01")
        self._claude("claude-pro-02", enabled=False, sched=False, state="AUTH_REQUIRED")
        rid = self._run()
        ad = RateLimitAdapter(exhaust="claude-pro-01")
        r = start_builder_run(rid, adapter=ad, actor="test")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "OWNER_ACTION_REQUIRED")
        self.assertEqual(ad.calls, ["claude-pro-01"])       # без второй попытки
        self.assertEqual(self.runs.get_run(rid)["state"], "OWNER_REQUIRED")

    def test_two_profile_one_safe_switch(self):
        # Два активных: первый rate-limit → РОВНО один switch на второй → успех.
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01")
        self._claude("claude-pro-03")  # тестовый второй eligible (VP-8 attach имитация)
        rid = self._run()
        ad = RateLimitAdapter(exhaust="claude-pro-01")
        r = start_builder_run(rid, adapter=ad, actor="test")
        self.assertTrue(r["ok"])
        self.assertEqual(r["switches"], 1)
        self.assertEqual(r["effective"], "claude-pro-03")   # switch на альтернативу
        self.assertEqual(len(ad.calls), 2)                  # ровно две попытки (≤1 switch)
        self.assertEqual(self.runs.get_run(rid)["state"], "COLLECTING")

    def test_no_active_claude_owner_required(self):
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01", enabled=False, sched=False, state="DISABLED")
        rid = self._run()
        ad = OkAdapter()
        r = start_builder_run(rid, adapter=ad, actor="test")
        self.assertFalse(r["ok"])
        self.assertEqual(ad.calls, [])                      # никто не вызван
        self.assertEqual(self.runs.get_run(rid)["state"], "OWNER_REQUIRED")

    def test_not_queued_is_noop(self):
        from atlas_core.runtime import start_builder_run
        self._claude("claude-pro-01")
        rid = self._run()
        start_builder_run(rid, adapter=OkAdapter(), actor="test")  # → COLLECTING
        r2 = start_builder_run(rid, adapter=OkAdapter(), actor="test")
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["reason"], "RUN_NOT_QUEUED")    # рестарт не дублирует
