"""Изоляция профилей: roots, права, реестр, кросс-чтение (Master Spec §11, §30.4).

Покрывает критерии приёмки VP-0 №1 (2 Codex + 2 Claude, aliased, isolated) и
№2 (cross-read blocked) — через РЕАЛЬНЫЕ идентичности исполнения, а не через
`nobody` против root-owned 0700.
"""

import json
import unittest

from atlas_core import isolation
from atlas_core.profiles import (
    ROOT_ENV_VAR,
    ProfileRegistry,
    ProfileState,
    assert_no_cross_owner,
    check_root_permissions,
    create_profile_root,
    isolated_env,
    runtime_user_for,
)
from atlas_core.redaction import contains_secret
from atlas_test_base import AtlasTestCase

ALIASES = {
    "codex": ["codex-plus-01", "codex-plus-02"],
    "claude": ["claude-pro-01", "claude-pro-02"],
}


class TestProfileIsolationCore(AtlasTestCase):
    """Core-уровень изоляции (чистый Python, во временном ATLAS_DATA_DIR)."""

    def _make_all(self):
        made = []
        for provider, aliases in ALIASES.items():
            for alias in aliases:
                made.append(create_profile_root(alias, provider))
        return made

    def test_four_isolated_roots_0700(self):
        profiles = self._make_all()
        self.assertEqual(len(profiles), 4)
        roots = set()
        for p in profiles:
            perm = check_root_permissions(p)
            self.assertTrue(perm["exists"])
            self.assertTrue(perm["is_0700"], f"{p.alias}: {perm['mode']}")
            self.assertFalse(perm["world_readable"])
            # владелец root — отдельная идентичность профиля (если создана)
            if perm.get("owner_is_runtime_user") is not None:
                self.assertTrue(perm["owner_is_runtime_user"], f"{p.alias} owner={perm['owner']}")
            roots.add(p.root_path)
        self.assertEqual(len(roots), 4)

    def test_runtime_user_derivation(self):
        self.assertEqual(runtime_user_for("codex-plus-01", "codex"), "atlas-cx01")
        self.assertEqual(runtime_user_for("codex-plus-02", "codex"), "atlas-cx02")
        self.assertEqual(runtime_user_for("claude-pro-01", "claude"), "atlas-cl01")
        self.assertEqual(runtime_user_for("claude-pro-02", "claude"), "atlas-cl02")

    def test_env_var_per_provider(self):
        self.assertEqual(ROOT_ENV_VAR["codex"], "CODEX_HOME")
        self.assertEqual(ROOT_ENV_VAR["claude"], "CLAUDE_CONFIG_DIR")

    def test_isolated_env_has_only_own_root(self):
        p_codex = create_profile_root("codex-plus-01", "codex")
        p_claude = create_profile_root("claude-pro-01", "claude")
        env = isolated_env(p_codex, base_env={
            "CODEX_HOME": "/leak/old", "CLAUDE_CONFIG_DIR": p_claude.root_path, "PATH": "/usr/bin"})
        self.assertEqual(env["CODEX_HOME"], p_codex.root_path)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)  # чужой root удалён
        assert_no_cross_owner(env, p_codex)

    def test_registry_contains_no_secrets(self):
        profiles = self._make_all()
        reg = ProfileRegistry()
        for p in profiles:
            p.state = ProfileState.READY
            reg.upsert(p)
        raw = reg.path.read_text(encoding="utf-8")
        self.assertFalse(contains_secret(raw), "реестр не должен содержать секретов")
        data = json.loads(raw)
        for alias, rec in data["profiles"].items():
            self.assertNotIn("email", rec)
            self.assertNotIn("token", rec)
            self.assertNotIn("cookie", rec)
            self.assertNotIn("account", rec)

    def test_public_dict_hides_raw_path(self):
        p = create_profile_root("codex-plus-01", "codex")
        self.assertEqual(p.to_public_dict()["root_path"], "[REDACTED_PATH]")

    def test_cross_provider_env_guard_raises(self):
        from atlas_core.profiles import ProfileError
        p_codex = create_profile_root("codex-plus-01", "codex")
        bad_env = {"CODEX_HOME": p_codex.root_path, "CLAUDE_CONFIG_DIR": "/some/claude"}
        with self.assertRaises(ProfileError):
            assert_no_cross_owner(bad_env, p_codex)


class TestRealIdentityIsolation(unittest.TestCase):
    """Реальная OS-граница: идентичность профиля A не читает credentials B.

    Работает против канонического runtime-layout (/var/lib/codevinci-atlas) с
    созданными per-profile идентичностями. Пропускается, если недоступно.
    """

    def setUp(self):
        if not isolation.available():
            self.skipTest("нужен root + runuser")
        import os
        os.environ.pop("ATLAS_DATA_DIR", None)  # реальный layout, не temp
        self.profiles = ProfileRegistry().list()
        if len(self.profiles) < 2 or not all(p.runtime_user for p in self.profiles):
            self.skipTest("реальные профили с идентичностями не инициализированы")
        if not all(check_root_permissions(p).get("owner_is_runtime_user") for p in self.profiles):
            self.skipTest("root профилей не во владении своих идентичностей (нужен profile-init на реальном layout)")

    def test_cross_profile_and_service_reads_denied(self):
        rep = isolation.prove_isolation(self.profiles)
        self.assertTrue(rep["available"])
        cross = [m for m in rep["matrix"] if m["kind"] == "cross_profile"]
        svc = [m for m in rep["matrix"] if m["kind"] == "service_user"]
        own = [m for m in rep["matrix"] if m["kind"] == "own_read"]
        self.assertTrue(cross and svc and own)
        for m in cross + svc:
            self.assertTrue(m["denied"], m)
            self.assertFalse(m["leaked"], m)
        for m in own:
            self.assertTrue(m["leaked"], m)  # свой probe читается
        self.assertTrue(rep["ok"])
