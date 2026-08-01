#!/usr/bin/env python3
"""manual-real — реальные provider-probe A→B после owner-логина (Master Spec §32.4).

НЕ часть обычной CI. Запускается владельцем после логина. До логина честно
печатает GATE и ничего не выдумывает. Для каждого провайдера с двумя
авторизованными профилями выполняет реальную цепочку A→B (см.
``atlas_core.real_probe``), затем сканирует durable-состояние на secret-markers.

Не провоцирует реальный лимит. Не читает и не печатает token/cookie/email/account.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for pkg in ("apps/core", "apps/runner"):
    sys.path.insert(0, str(_ROOT / pkg))

from atlas_core import config  # noqa: E402
from atlas_core.adapters.real_claude import RealClaudeAdapter  # noqa: E402
from atlas_core.adapters.real_codex import RealCodexAdapter  # noqa: E402
from atlas_core.profiles import ProfileRegistry  # noqa: E402
from atlas_core.real_probe import probe_provider  # noqa: E402
from atlas_core.redaction import scan_paths  # noqa: E402
from atlas_core.store import Store  # noqa: E402


def _adapter(provider):
    return RealCodexAdapter() if provider == "codex" else RealClaudeAdapter()


def main() -> int:
    reg = ProfileRegistry()
    profiles = reg.list()
    if not profiles:
        print("Профилей нет. Сначала: sudo bash scripts/atlas-runtime-setup.sh && "
              "PYTHONPATH=apps/core python3 scripts/profile-init.py")
        return 2

    print("=== manual-real: auth status (реальные probe, под идентичностью профиля) ===")
    ready: dict[str, list] = {"codex": [], "claude": []}
    for p in profiles:
        st = _adapter(p.provider).auth_status(p.root_path, executable=p.executable_path,
                                              run_as_user=p.runtime_user)
        print(f"  {p.alias:<16} {p.provider:<7} -> {'READY' if st['authenticated'] else st['state']}")
        if st.get("authenticated"):
            ready[p.provider].append(p)

    config.ensure_dirs()
    store = Store()
    results = []
    any_real = False
    print("\n=== A→B реальный probe ===")
    for provider in ("codex", "claude"):
        provs = ready[provider]
        if len(provs) < 2:
            print(f"  [GATE] {provider}: нужно 2 авторизованных профиля (есть {len(provs)}). "
                  f"Выполните scripts/login-gate.sh")
            continue
        any_real = True
        print(f"  [{provider}] A={provs[0].alias} B={provs[1].alias}: запуск реальной цепочки…")
        rep = probe_provider(provider, provs[0], provs[1], store)
        results.append(rep)
        print(f"    ok={rep['ok']} steps={json.dumps(rep['steps'], ensure_ascii=False)}")

    print("\n=== пост-скан secret-markers (durable state) ===")
    hits = scan_paths([str(config.db_path()), str(config.logs_dir()), str(config.artifacts_dir())])
    print(f"  находок: {len(hits)}")

    # evidence
    art = config.artifacts_dir() / "vp0"
    art.mkdir(parents=True, exist_ok=True)
    (art / "real_probes.json").write_text(
        json.dumps({"results": results, "secret_hits": len(hits)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    store.close()

    if not any_real:
        print("\nИтог: реальная часть — OWNER GATE. Механизм доказан "
              "(scripts/run_acceptance.py). После логина этот скрипт выполнит реальные probe.")
        return 0
    all_ok = all(r["ok"] for r in results) and len(results) == 2
    print(f"\nИтог: реальные probe {'ПРОЙДЕНЫ' if all_ok else 'НЕ полностью пройдены'}. "
          f"Evidence: {art/'real_probes.json'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
