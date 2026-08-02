#!/usr/bin/env python3
"""Полный секрет-скан VP-0 (Master Spec §30.4, §32.6).

Сканирует рабочее дерево, историю Git, БД, логи, artifacts, отчёты и конфиг.
Возвращает ненулевой код при РЕАЛЬНЫХ находках (аллоуслист — только
синтетические фикстуры tests/ и определения в redaction.py/secret_scan.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))

from atlas_core import config  # noqa: E402
from atlas_core.secret_scan import scan_repo  # noqa: E402


def main() -> int:
    extra = [str(config.PROD_DATA_DIR), str(_ROOT / "var")]
    rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if Path(e).exists()])
    d = rep.to_dict()
    print("=== ПОЛНЫЙ СЕКРЕТ-СКАН VP-0 ===")
    print(f"  Цели: {d['targets']}")
    print(f"  Git-коммитов: {d['git_commits']}  история просканирована: {d['git_history_scanned']}")
    print(f"  Реальных находок: {len(d['real_hits'])}")
    print(f"  Аллоуслист (синтетические фикстуры): {d['allowlisted_hits_count']}")
    print(f"  Credential-root исключено (auth-store): {d['credential_root_hits_excluded']}")
    print(f"  Сервисный atlas НЕ читает credential-root: {d['service_user_cannot_read_credential_roots']}")
    print(f"  Находок в истории Git: {len(d['git_history_hits'])}")
    print(f"  Примечание: {d['note']}")
    if d["real_hits"]:
        print("  !!! РЕАЛЬНЫЕ НАХОДКИ:")
        for h in d["real_hits"][:20]:
            print(f"    {h.get('rule')} {h.get('path')}:{h.get('line')}")
    art = config.artifacts_dir() / "vp0"
    try:
        art.mkdir(parents=True, exist_ok=True)
        (art / "secret_scan.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Отчёт: {art/'secret_scan.json'}")
    except OSError:
        pass
    print(f"\n  РЕЗУЛЬТАТ: {'ЧИСТО' if rep.clean else 'ЕСТЬ РЕАЛЬНЫЕ НАХОДКИ'}")
    return 0 if rep.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
