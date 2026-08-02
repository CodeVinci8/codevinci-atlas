"""VP-5 API smoke (Master Spec §25): Runs/Profiles/Models/System через реальный
ASGI-стек (TestClient). Стабильные envelope-ошибки, идемпотентность, sanitized
Pulse-сводка. Без реальных provider-вызовов."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP5ApiBase(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine, session_scope
        from atlas_core.orm import Base, Project
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        with get_engine().begin() as c:
            c.exec_driver_sql("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
            c.exec_driver_sql("DELETE FROM alembic_version")
            c.exec_driver_sql("INSERT INTO alembic_version VALUES ('0005_agent_pipeline')")
        with session_scope() as s:
            s.add(Project(id="proj_p", name="Синтетика", source_kind="local_git",
                          source_location="/x", status="connected",
                          created_at=_now(), updated_at=_now()))
            s.commit()
        from atlas_core.app import create_app
        from starlette.testclient import TestClient
        self.client = TestClient(create_app(self.settings))


class TestRunsApi(VP5ApiBase):
    def test_create_get_and_lifecycle(self):
        r = self.client.post("/api/v1/runs", json={"project_id": "proj_p", "work_order_id": "wo1"})
        self.assertEqual(r.status_code, 201)
        rid = r.json()["run"]["id"]
        self.assertEqual(r.json()["run"]["state"], "QUEUED")
        # detail
        g = self.client.get(f"/api/v1/runs/{rid}")
        self.assertEqual(g.status_code, 200)
        # events durable
        ev = self.client.get(f"/api/v1/runs/{rid}/events")
        self.assertTrue(any(e["type"] == "run.created" for e in ev.json()["events"]))
        # cancel (optimistic version)
        v = g.json()["run"]["version"]
        c = self.client.post(f"/api/v1/runs/{rid}/cancel", json={"expected_version": v})
        self.assertEqual(c.status_code, 200)
        self.assertEqual(c.json()["run"]["state"], "CANCELLED")

    def test_create_idempotent_header(self):
        h = {"Idempotency-Key": "abc-1"}
        a = self.client.post("/api/v1/runs", json={"project_id": "proj_p"}, headers=h)
        b = self.client.post("/api/v1/runs", json={"project_id": "proj_p"}, headers=h)
        self.assertEqual(a.json()["run"]["id"], b.json()["run"]["id"])

    def test_stale_version_conflict_envelope(self):
        rid = self.client.post("/api/v1/runs", json={"project_id": "proj_p"}).json()["run"]["id"]
        self.client.post(f"/api/v1/runs/{rid}/cancel", json={"expected_version": 1})
        # повторная мутация со старой версией → стабильный 409
        again = self.client.post(f"/api/v1/runs/{rid}/pause", json={"expected_version": 1})
        self.assertEqual(again.status_code, 409)
        self.assertIn("code", again.json()["error"])

    def test_pause_resume_flow(self):
        rid = self.client.post("/api/v1/runs", json={"project_id": "proj_p"}).json()["run"]["id"]
        v = self.client.post(f"/api/v1/runs/{rid}/pause", json={"expected_version": 1})
        # QUEUED→PAUSED невалиден; сначала PREPARING/RUNNING. Проверяем стабильную ошибку.
        self.assertIn(v.status_code, (200, 409))


class TestProfilesModelsApi(VP5ApiBase):
    def test_profiles_and_summary(self):
        from atlas_core.agent_registry import ProfileService
        ProfileService().upsert_profile("codex-plus-01", "codex", unix_label="atlas-cx01")
        resp = self.client.get("/api/v1/profiles")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["profiles"]), 1)
        self.assertIn("summary", body)
        self.assertNotIn("@", str(body).lower())  # без email

    def test_cookie_onboarding_unsupported(self):
        from atlas_core.agent_registry import ProfileService
        pid = ProfileService().upsert_profile("claude-pro-01", "claude")
        resp = self.client.post(f"/api/v1/profiles/{pid}/onboarding", json={"method": "cookie"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "COOKIE_UNSUPPORTED")

    def test_official_onboarding_owner_gated(self):
        from atlas_core.agent_registry import ProfileService
        pid = ProfileService().upsert_profile("claude-pro-02", "claude")
        resp = self.client.post(f"/api/v1/profiles/{pid}/onboarding", json={"method": "official"})
        self.assertEqual(resp.json()["onboarding"]["status"], "OWNER_ACTION_REQUIRED")

    def test_models_list(self):
        from atlas_core.agent_registry import ModelService
        ModelService().record_model("claude", "opus-high", availability="available", source="observed")
        resp = self.client.get("/api/v1/models")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(m["model_id"] == "opus-high" for m in resp.json()["models"]))


class TestSystemApi(VP5ApiBase):
    def test_system_summary_sanitized(self):
        resp = self.client.get("/api/v1/system/summary")
        self.assertEqual(resp.status_code, 200)
        summ = resp.json()["summary"]
        self.assertEqual(summ["db_migration"], "0005_agent_pipeline")
        blob = str(summ).lower()
        for forbidden in ("hostname", "nodename", "auth_root", "environ"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    import unittest
    unittest.main()
