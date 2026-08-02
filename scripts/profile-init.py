#!/usr/bin/env python3
"""profile-init — создать изолированные root профилей и записать non-secret реестр.

Master Spec §11.1/§11.4/§7.3. Идемпотентно: повторный запуск не портит уже
созданные root. Ничего не логинит и не копирует credentials — только готовит
изолированные каталоги 0700 и alias-реестр.

Использование:
    PYTHONPATH=apps/core python3 scripts/profile-init.py \
        [--codex codex-plus-01 codex-plus-02] [--claude claude-pro-01 claude-pro-02]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))

from atlas_core.profiles import (  # noqa: E402
    ProfileRegistry,
    check_root_permissions,
    create_profile_root,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex", nargs="*", default=["codex-plus-01", "codex-plus-02"])
    ap.add_argument("--claude", nargs="*", default=["claude-pro-01", "claude-pro-02"])
    args = ap.parse_args()

    reg = ProfileRegistry()
    made = []
    for alias in args.codex:
        made.append(create_profile_root(alias, "codex"))
    for alias in args.claude:
        made.append(create_profile_root(alias, "claude"))
    for p in made:
        reg.upsert(p)

    print("Созданы изолированные root профилей (credentials НЕ создавались):\n")
    for p in made:
        perm = check_root_permissions(p)
        # Путь показывается владельцу для операции логина — это каталог, не секрет.
        print(f"  {p.alias:<16} [{p.provider}]  {p.env_var}={p.root_path}")
        print(f"                    права: {perm['mode']} владелец: {perm['owner']} 0700: {perm['is_0700']}")
    print(f"\nРеестр (non-secret): {reg.path}")
    print("Следующий шаг — owner-логин: scripts/login-gate.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
