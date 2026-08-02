"""VP-5 Profile/Model реестр + system summary (Master Spec §11, §17.2, §27.2, §30):
safe-представления без PII, STALE-ёмкость, provenance моделей, sanitized-сводка."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from atlas_test_base import AtlasTestCase


def _now():
    return datetime.now(timezone.utc)


class VP5SvcBase(AtlasTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        from atlas_core.db import get_engine, init_engine
        from atlas_core.orm import Base
        from atlas_core.settings import load_settings
        self.settings = load_settings()
        init_engine(self.settings.db_url, self.settings.db_path)
        Base.metadata.create_all(get_engine())
        # Проставляем alembic_version, как реальная миграция (create_all её не создаёт).
        from sqlalchemy import text
        with get_engine().begin() as c:
            c.exec_driver_sql("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
            c.exec_driver_sql("DELETE FROM alembic_version")
            c.execute(text("INSERT INTO alembic_version VALUES ('0005_agent_pipeline')"))
        from atlas_core.agent_registry import ModelService, ProfileService
        self.profiles = ProfileService()
        self.models = ModelService()


class TestProfileService(VP5SvcBase):
    def test_upsert_and_view_no_pii(self):
        pid = self.profiles.upsert_profile("codex-plus-01", "codex", unix_label="atlas-cx01",
                                           auth_root_ref="allowlist:codex/codex-plus-01")
        view = self.profiles.get_profile(pid)
        self.assertEqual(view["alias"], "codex-plus-01")
        self.assertEqual(view["state"], "UNCONFIGURED")
        # Safe-представление не содержит email/raw path/auth_root_ref.
        blob = json.dumps(view).lower()
        for token in ("@", "/var/lib", "auth_root", "codex-plus-01/"):
            self.assertNotIn(token, blob)

    def test_set_state_optimistic(self):
        from atlas_core.agent_registry import RegistryError
        pid = self.profiles.upsert_profile("claude-pro-01", "claude")
        self.profiles.set_state(pid, "AUTH_REQUIRED", expected_version=1)
        self.profiles.set_state(pid, "READY", expected_version=2)
        with self.assertRaises(RegistryError) as cm:
            self.profiles.set_state(pid, "LEASED", expected_version=1)  # устаревшая версия
        self.assertEqual(cm.exception.code, "VERSION_CONFLICT")

    def test_capacity_unknown_without_observation(self):
        pid = self.profiles.upsert_profile("codex-plus-02", "codex")
        self.assertEqual(self.profiles.get_profile(pid)["capacity"]["status"], "UNKNOWN")

    def test_capacity_becomes_stale(self):
        from atlas_core.db import session_scope
        from atlas_core.ids import new_id
        from atlas_core.orm import CapacityObservation
        pid = self.profiles.upsert_profile("claude-pro-02", "claude")
        old = _now() - timedelta(seconds=1000)  # старше CAPACITY_TTL_S (900)
        with session_scope() as s:
            s.add(CapacityObservation(id=new_id("pcap"), profile_id=pid, status="AVAILABLE",
                                      five_h_used_pct=12, source="official_structured",
                                      confidence="high", stale=False, observed_at=old))
            s.commit()
        self.assertEqual(self.profiles.get_profile(pid)["capacity"]["status"], "STALE")

    def test_capacity_fresh_verified_kept(self):
        pid = self.profiles.upsert_profile("claude-pro-03", "claude")
        self.profiles.observe_capacity(pid, status="AVAILABLE", five_h_used_pct=20,
                                       source="official_structured", confidence="high")
        cap = self.profiles.get_profile(pid)["capacity"]
        self.assertEqual(cap["status"], "AVAILABLE")
        self.assertEqual(cap["five_h_used_pct"], 20)

    def test_summary_counts(self):
        p1 = self.profiles.upsert_profile("codex-plus-01", "codex")
        self.profiles.set_state(p1, "READY", expected_version=1)
        self.profiles.upsert_profile("claude-pro-01", "claude")
        counts = self.profiles.summary_counts()
        self.assertEqual(counts["READY"], 1)
        self.assertEqual(counts["UNCONFIGURED"], 1)

    def test_health_stores_only_normalized_auth(self):
        pid = self.profiles.upsert_profile("claude-pro-04", "claude")
        self.profiles.observe_health(pid, executable="/x/claude", cli_version="2.1.220",
                                     auth={"auth_status": "READY", "plan_label": "Pro"},
                                     permissions_ok=True)
        h = self.profiles.get_profile(pid)["health"]
        self.assertEqual(h["auth_status"], "READY")
        self.assertEqual(h["plan_label"], "Pro")
        self.assertNotIn("email", json.dumps(h).lower())


class TestModelService(VP5SvcBase):
    def test_record_and_list_with_provenance(self):
        self.models.record_model("claude", "opus-high", display="Opus (high)",
                                 efforts=["high", "xhigh"], availability="available",
                                 source="observed", confidence="high")
        self.models.record_model("codex", "sol-max", availability="unknown", source="unknown")
        rows = self.models.list_models()
        by = {(m["provider"], m["model_id"]): m for m in rows}
        self.assertEqual(by[("claude", "opus-high")]["availability"], "available")
        self.assertEqual(by[("claude", "opus-high")]["source"], "observed")
        self.assertIn("discovered_at", by[("claude", "opus-high")])
        self.assertEqual(by[("codex", "sol-max")]["availability"], "unknown")

    def test_discovery_snapshot_recorded(self):
        did = self.models.record_discovery("claude", [{"model_id": "opus-high"}], source="observed")
        self.assertTrue(did)


class TestSystemSummary(VP5SvcBase):
    def test_summary_is_sanitized(self):
        from atlas_core.system_summary import system_summary
        summ = system_summary(self.settings)
        blob = json.dumps(summ)
        # Реальный hostname/nodename НЕ должен просачиваться.
        nodename = os.uname().nodename
        if nodename:
            self.assertNotIn(nodename, blob)
        # Запрещённые ключи отсутствуют в структуре.
        for forbidden in ("hostname", "nodename", "ip_address", "public_ip", "username",
                          "auth_root", "environ", "cookie", "token"):
            self.assertNotIn(forbidden, blob.lower())
        # machine_id либо None, либо sanitized-хеш.
        mid = summ["os"]["machine_id"]
        self.assertTrue(mid is None or mid.startswith("m-"))
        # Правдивые поля присутствуют.
        self.assertEqual(summ["atlas_version"], self.settings.version)
        self.assertEqual(summ["db_migration"], "0005_agent_pipeline")
        self.assertIn("cpu", summ)
        self.assertIn("runs", summ)
        self.assertEqual(summ["services"]["web"]["status"], "UNKNOWN")


if __name__ == "__main__":
    import unittest
    unittest.main()
