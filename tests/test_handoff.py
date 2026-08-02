"""Тесты checkpoint/handoff и верификации (Master Spec §16.4, §21)."""

import time

from atlas_core.handoff import build_handoff, verify_handoff
from atlas_test_base import AtlasTestCase


def _mk(**over):
    base = dict(
        project_id="codevinci-atlas", vp_id="VP-0", goal="доказать handoff",
        immutable_constraints=["один writer", "без credentials"],
        baseline_head="sha_base", current_head="sha_now", changed_files=["a.py"],
        commands=[{"cmd": "pytest", "outcome": "ok"}], failures=[],
        acceptance_matrix=[{"criterion": "X", "status": "PENDING"}],
        decisions=["решение"], exact_next_action="продолжить с шага N",
        prohibited_actions=["force push"], from_profile_alias="codex-plus-01")
    base.update(over)
    return build_handoff(**base)


class TestHandoff(AtlasTestCase):
    def test_handoff_has_required_fields(self):
        h = _mk()
        d = h.to_dict()
        for key in ("goal", "immutable_constraints", "baseline_head", "current_head",
                    "changed_files", "commands", "failures", "acceptance_matrix",
                    "decisions", "exact_next_action", "prohibited_actions", "artifact_refs"):
            self.assertIn(key, d)

    def test_handoff_carries_no_full_chat_or_credentials(self):
        h = _mk()
        from atlas_core.redaction import contains_secret
        self.assertFalse(contains_secret(h.to_json()))

    def test_verify_actual_head_wins(self):
        h = _mk(current_head="sha_stale")
        report = verify_handoff(h, actual_head="sha_actual")
        self.assertFalse(report.ok)
        self.assertEqual(report.effective_head, "sha_actual")  # факт побеждает
        self.assertTrue(report.mismatches)

    def test_verify_ok_when_matches(self):
        h = _mk(current_head="sha_actual")
        report = verify_handoff(h, actual_head="sha_actual")
        self.assertTrue(report.ok)

    def test_stale_handoff_rejected(self):
        h = _mk()
        time.sleep(0.05)
        report = verify_handoff(h, actual_head=h.current_head, max_age_s=0.01)
        self.assertFalse(report.ok)
        self.assertTrue(any("устарел" in m for m in report.mismatches))

    def test_changed_files_mismatch_detected(self):
        h = _mk(changed_files=["a.py"])
        report = verify_handoff(h, actual_head=h.current_head, actual_changed_files=["a.py", "b.py"])
        self.assertFalse(report.ok)
