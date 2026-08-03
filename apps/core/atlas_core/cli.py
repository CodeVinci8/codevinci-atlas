"""CLI Atlas: doctor / status / backup (Master Spec §34).

Команды возвращают осмысленные коды выхода и НЕ печатают email, token,
cookie, содержимое auth или дампы окружения (§11.2, §13.2).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from .settings import load_settings


def _redacted_versions() -> dict:
    def ver(cmd):
        if shutil.which(cmd) is None:
            return "НЕ УСТАНОВЛЕН"
        try:
            out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=15)
            return (out.stdout or out.stderr).strip().splitlines()[0]
        except Exception:  # noqa: BLE001
            return "ошибка"
    return {c: ver(c) for c in ("python3", "docker", "git", "gh", "uv", "pnpm", "node")}


def _migration_state(settings) -> dict:
    db = settings.db_path
    if not os.path.exists(db):
        return {"db_exists": False, "current": None}
    try:
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cur = None
        if "alembic_version" in tables:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            cur = row[0] if row else None
        conn.close()
        return {"db_exists": True, "current": cur, "tables": sorted(tables)}
    except sqlite3.Error as exc:
        return {"db_exists": True, "error": str(exc)[:80]}


def _runner(settings) -> dict:
    from .runner_health import runner_health
    return runner_health(settings.runner_socket, settings.runner_token_file)


def _socket_perms(settings) -> dict:
    p = settings.runner_socket
    if not os.path.exists(p):
        return {"exists": False}
    import stat as st
    mode = st.S_IMODE(os.stat(p).st_mode)
    return {"exists": True, "mode": oct(mode), "is_0660": mode == 0o660}


def _profiles_redacted() -> list[dict]:
    """Готовность профилей без идентичностей (§11.2)."""
    try:
        from .profiles import ProfileRegistry, check_root_permissions
        reg = ProfileRegistry()
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for p in reg.list():
        perm = check_root_permissions(p)
        rows.append({"alias": p.alias, "provider": p.provider,
                     "root_is_0700": perm.get("is_0700"),
                     "owner_is_runtime_user": perm.get("owner_is_runtime_user"),
                     "has_executable": bool(p.executable_path)})
    return rows


def _compose_services() -> dict:
    if shutil.which("docker") is None:
        return {"available": False}
    try:
        out = subprocess.run(["docker", "compose", "ps", "--format", "json"],
                             capture_output=True, text=True, timeout=20,
                             cwd=str(Path(__file__).resolve().parents[3]))
        lines = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
        return {"available": True, "services": [
            {"name": s.get("Service"), "state": s.get("State"), "health": s.get("Health")}
            for s in lines]}
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "error": str(exc)[:80]}


def cmd_doctor(args) -> int:
    settings = load_settings()
    report = {
        "versions": _redacted_versions(),
        "migrations": _migration_state(settings),
        "runner": _runner(settings),
        "runner_socket": _socket_perms(settings),
        "compose": _compose_services(),
        "profiles": _profiles_redacted(),
        "data_dir_exists": os.path.isdir(settings.data_dir),
        "core_non_root": (os.geteuid() != 0) if hasattr(os, "geteuid") else True,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("atlas doctor\n")
        for k, v in report["versions"].items():
            print(f"  {k:<8}: {v}")
        m = report["migrations"]
        print(f"\n  БД: exists={m.get('db_exists')} alembic={m.get('current')}")
        print(f"  Runner: {report['runner'].get('status')} socket={report['runner_socket'].get('mode')}")
        c = report["compose"]
        print(f"  Compose: {'сервисы='+str(len(c.get('services',[]))) if c.get('available') else 'docker недоступен'}")
        print(f"  Профилей: {len(report['profiles'])} (root 0700: "
              f"{sum(1 for p in report['profiles'] if p['root_is_0700'])})")
        print(f"  Core non-root: {report['core_non_root']}")
    # exit code: проблемы миграции/runner offline → ненулевой (для мониторинга)
    ok = report["migrations"].get("db_exists") is not False
    return 0 if ok else 1


def cmd_status(args) -> int:
    settings = load_settings()
    rh = _runner(settings)
    mig = _migration_state(settings)
    core_db_ok = mig.get("db_exists") and not mig.get("error")
    status = {
        "core": {"db_ok": bool(core_db_ok), "version": settings.version,
                 "non_root": (os.geteuid() != 0) if hasattr(os, "geteuid") else True},
        "runner": {"status": rh.get("status"), "reason": rh.get("reason")},
        "migrations": {"current": mig.get("current")},
    }
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"Core: db={'ok' if core_db_ok else 'FAIL'} v{settings.version} "
              f"non_root={status['core']['non_root']}")
        print(f"Runner: {rh.get('status')}"
              + (f" ({rh.get('reason')})" if rh.get("reason") else ""))
        print(f"Migration: {mig.get('current')}")
    return 0 if core_db_ok and rh.get("status") == "READY" else 2


def cmd_backup(args) -> int:
    from .backup import create_backup, scan_backup_for_secrets
    settings = load_settings()
    out_dir = args.out or str(Path(settings.data_dir) / "backups")
    config_file = os.environ.get("ATLAS_CONFIG_FILE")
    manifest = create_backup(settings.data_dir, out_dir, config_file=config_file)
    scan = scan_backup_for_secrets(manifest["archive"]["path"])
    result = {"archive": manifest["archive"]["path"],
              "archive_sha256": manifest["archive"]["sha256"],
              "db_sha256": manifest["db"]["sha256"],
              "integrity_ok": manifest["integrity_ok"],
              "artifacts": len(manifest["artifacts"]),
              "secret_scan_clean": scan["clean"]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Backup: {result['archive']}")
        print(f"  db integrity: {'ok' if result['integrity_ok'] else 'FAIL'}")
        print(f"  archive sha256: {result['archive_sha256']}")
        print(f"  секрет-скан backup: {'ЧИСТО' if result['secret_scan_clean'] else 'НАХОДКИ'}")
    return 0 if manifest["integrity_ok"] and scan["clean"] else 1


def cmd_profiles(args) -> int:
    """Идемпотентная сверка/список профилей (Master Spec §11, VP6-D2).

    `reconcile` синхронизирует allowlisted файловый реестр → durable-таблицу
    `agent_profiles` (safe-метаданные), чтобы 4 профиля появились в UI. Не читает
    credentials и НЕ стартует provider-сессии.
    """

    settings = load_settings()
    from .db import init_engine
    init_engine(settings.db_url, settings.db_path)
    if args.action == "reconcile":
        from .profile_reconcile import reconcile_profiles
        res = reconcile_profiles(actor=args.actor or "deploy").to_dict()
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"Профили сверены: total={res['total']} "
                  f"создано={len(res['created'])} обновлено={len(res['updated'])}")
            print(f"  по провайдерам: {res['by_provider']}")
            print(f"  aliases: {sorted(res['created'] + res['updated'])}")
        return 0
    if args.action == "list":
        from .agent_registry import ProfileService
        rows = ProfileService().list_profiles()
        safe = [{"alias": r["alias"], "provider": r["provider"], "state": r["state"]}
                for r in rows]
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0
    if args.action == "auth-health":
        # VP-7: bounded read-only auth-проверки 4 профилей через официальные CLI
        # (codex login status / claude auth status). Без чтения credential-файлов,
        # без вывода token/cookie/email; результат — свежий факт (observed_at/source).
        from .auth_health import run_auth_health
        rep = run_auth_health(actor=args.actor or "owner")
        safe = [{"alias": o["alias"], "provider": o["provider"],
                 "auth_status": o["auth_status"], "reason": o["reason"],
                 "cli_version": o["cli_version"]} for o in rep]
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atlas", description="CodeVinci Atlas CLI")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
        ("doctor", cmd_doctor, "диагностика зависимостей/миграций/Runner/прав"),
        ("status", cmd_status, "краткое состояние Core/Runner/БД"),
        ("backup", cmd_backup, "онлайн-backup SQLite + манифест + проверка"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--json", action="store_true")
        if name == "backup":
            p.add_argument("--out", help="каталог для архива")
        p.set_defaults(func=fn)
    pp = sub.add_parser("profiles", help="сверка/список/auth-health профилей (safe-метаданные)")
    pp.add_argument("action", choices=["reconcile", "list", "auth-health"],
                    help="reconcile: реестр→БД; list: safe-список; auth-health: read-only проверка входа")
    pp.add_argument("--json", action="store_true")
    pp.add_argument("--actor", default="deploy", help="актор для Audit")
    pp.set_defaults(func=cmd_profiles)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
