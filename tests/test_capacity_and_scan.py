"""Честная ёмкость UNKNOWN и защита БД от секретов (Master Spec §11.6, §23.2)."""

from atlas_test_base import AtlasTestCase

from atlas_core.capacity import CapacitySource, CapacityStatus, unknown_capacity
from atlas_core.redaction import SECRET_MARKER, scan_paths
from atlas_core.store import SecretLeakError, Store


class TestCapacityUnknown(AtlasTestCase):
    def test_unknown_is_honest(self):
        cap = unknown_capacity()
        self.assertEqual(cap.status, CapacityStatus.UNKNOWN)
        self.assertEqual(cap.source, CapacitySource.UNKNOWN)
        self.assertIsNone(cap.remaining_5h)
        self.assertIsNone(cap.remaining_7d)
        self.assertIsNone(cap.reset_at)

    def test_dict_has_null_remaining(self):
        d = unknown_capacity().to_dict()
        self.assertIsNone(d["5h_remaining"])
        self.assertIsNone(d["7d_remaining"])


class TestStoreSecretGuard(AtlasTestCase):
    def test_store_rejects_secret_marker(self):
        store = Store()
        with self.assertRaises(SecretLeakError):
            store.upsert_run(run_id="r1", state="RUNNING", profile_alias=SECRET_MARKER)
        store.close()

    def test_store_rejects_token_in_checkpoint(self):
        store = Store()
        with self.assertRaises(SecretLeakError):
            store.save_checkpoint({"project_id": "p", "note": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"})
        store.close()

    def test_audit_message_redacted(self):
        store = Store()
        store.audit("test", "email owner@example.com and token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        msg = store.audit_events()[-1]["message"]
        self.assertNotIn("owner@example.com", msg)
        self.assertNotIn("ghp_ABCDEF", msg)
        store.close()

    def test_db_file_has_no_markers(self):
        store = Store()
        store.upsert_run(run_id="r1", state="RUNNING", profile_alias="codex-plus-01")
        store.audit("run", "обычное сообщение")
        store.close()
        hits = scan_paths([str(self.data_dir / "atlas.db")])
        self.assertEqual(hits, [])
