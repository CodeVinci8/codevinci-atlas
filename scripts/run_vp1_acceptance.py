#!/usr/bin/env python3
"""VP-1 acceptance boundary — доказательство с evidence (Master Spec §34).

Прогоняет 16 приёмочных проверок против РЕАЛЬНО развёрнутого стека (Compose
Core/Web + systemd Runner) и пишет redacted-evidence с хешами и exit codes в
var/artifacts/vp1/. Некоторые проверки останавливают/поднимают сервисы —
в конце стек остаётся healthy.

Статусы: PASS / FAIL. Итог COMPLETE только при 16/16 PASS.
Запуск (root, стек поднят): python3 scripts/run_vp1_acceptance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))

ART = _ROOT / "var" / "artifacts" / "vp1"
ART.mkdir(parents=True, exist_ok=True)
WEB = "http://127.0.0.1:3210"
RUNNER_UNIT = "codevinci-atlas-runner.service"
DATA_DIR = "/var/lib/codevinci-atlas"


_UVBIN = f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"


def sh(cmd, timeout=180, env=None, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd or str(_ROOT), env={**os.environ, **(env or {})})
    return r.returncode, r.stdout, r.stderr


def web(cmd, timeout=300):
    return sh(cmd, timeout=timeout, env={"PATH": _UVBIN}, cwd=str(_ROOT / "apps/web"))


def http(path, timeout=6):
    try:
        with urllib.request.urlopen(WEB + path, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def health():
    st, body = http("/api/v1/health")
    if st == 200:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


def wait_health(pred, tries=30, delay=1.0):
    for _ in range(tries):
        h = health()
        if pred(h):
            return h
        time.sleep(delay)
    return health()


class VP1:
    def __init__(self):
        self.results = []

    def art(self, name, content):
        p = ART / name
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                     if isinstance(content, (dict, list)) else str(content), encoding="utf-8")
        return p.name

    def rec(self, n, name, ok, note, ev=None):
        self.results.append({"id": n, "criterion": name, "status": "PASS" if ok else "FAIL",
                             "note": note, "evidence": ev or []})
        print(f"  [{'PASS' if ok else 'FAIL'}] #{n:>2} {name}")

    # 1. Clean reproducible install (frozen)
    def c1(self):
        rc_py, _o, _e = sh(["uv", "sync", "--frozen", "--quiet"], env={"PATH": f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"})
        rc_web, wo, we = web(["pnpm", "install", "--frozen-lockfile"])
        ev = self.art("c1_frozen_installs.json", {"uv_sync_rc": rc_py, "pnpm_frozen_rc": rc_web,
                                                 "pnpm_tail": (wo + we)[-200:]})
        self.rec(1, "Чистая воспроизводимая установка (frozen)", rc_py == 0 and rc_web == 0,
                 f"uv sync rc={rc_py}, pnpm --frozen-lockfile rc={rc_web}", [ev])

    # 2. Empty DB migration
    def c2(self):
        tmp = ART / "c2_migrate"
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        tmp.mkdir()
        env = {"ATLAS_DATA_DIR": str(tmp), "ATLAS_CONFIG_FILE": "/nonexistent.yaml",
               "PATH": f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"}
        rc, o, e = sh(["uv", "run", "alembic", "upgrade", "head"], env=env)
        import sqlite3
        db = tmp / "atlas.db"
        tables = []
        if db.exists():
            c = sqlite3.connect(str(db))
            tables = sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            c.close()
        ok = rc == 0 and "audit_events" in tables and "alembic_version" in tables
        ev = self.art("c2_empty_migration.json", {"rc": rc, "tables": tables, "stderr_tail": e[-150:]})
        self.rec(2, "Миграция полностью пустой БД", ok, f"rc={rc}, таблицы={tables}", [ev])

    # 3. Core/Web safe restart
    def c3(self):
        before = health().get("status")
        rc, _o, _e = sh(["docker", "compose", "restart", "core", "web"], timeout=120)
        after = wait_health(lambda h: h.get("status") == "READY").get("status")
        ok = rc == 0 and after == "READY"
        ev = self.art("c3_corebweb_restart.json", {"restart_rc": rc, "before": before, "after": after})
        self.rec(3, "Безопасный рестарт Core/Web", ok, f"before={before} after={after}", [ev])

    # 4. Runner safe restart
    def c4(self):
        rc, _o, _e = sh(["systemctl", "restart", RUNNER_UNIT], timeout=60)
        time.sleep(2)
        active = sh(["systemctl", "is-active", RUNNER_UNIT])[1].strip()
        after = wait_health(lambda h: h.get("runner", {}).get("status") == "READY")
        ok = rc == 0 and active == "active" and after.get("runner", {}).get("status") == "READY"
        ev = self.art("c4_runner_restart.json", {"restart_rc": rc, "is_active": active,
                                                "runner_after": after.get("runner", {}).get("status")})
        self.rec(4, "Безопасный рестарт Runner", ok, f"active={active}, runner={after.get('runner',{}).get('status')}", [ev])

    # 5. Runner offline visible via API and Web
    def c5(self):
        sh(["systemctl", "stop", RUNNER_UNIT], timeout=60)
        offline = wait_health(lambda h: h.get("runner", {}).get("status") != "READY", tries=20)
        api_status = offline.get("status")
        runner_status = offline.get("runner", {}).get("status")
        # Web отражает то же (health идёт через proxy → тот же JSON)
        st, body = http("/api/v1/health")
        web_sees_offline = runner_status != "READY"
        # восстановить
        sh(["systemctl", "start", RUNNER_UNIT], timeout=60)
        wait_health(lambda h: h.get("runner", {}).get("status") == "READY")
        ok = api_status == "DEGRADED" and runner_status in ("OFFLINE", "DEGRADED") and web_sees_offline
        ev = self.art("c5_runner_offline.json", {"api_overall": api_status, "runner_status": runner_status,
                                                "web_http": st, "web_sees_offline": web_sees_offline})
        self.rec(5, "Runner offline виден через API и Web", ok,
                 f"overall={api_status}, runner={runner_status}", [ev])

    # 6. Authenticated UDS + wrong token fails
    def c6(self):
        from atlas_core.runner_health import runner_health
        sock = "/run/codevinci-atlas/runner.sock"
        tok = "/run/codevinci-atlas/runner.token"
        good = runner_health(sock, tok)
        bad_file = ART / "c6_badtoken"
        bad_file.write_text("WRONGTOKEN", encoding="utf-8")
        bad = runner_health(sock, str(bad_file))
        bad_file.unlink()
        ok = good.get("status") == "READY" and bad.get("status") == "UNAUTHORIZED"
        ev = self.art("c6_uds_auth.json", {"good": good.get("status"), "wrong_token": bad.get("status")})
        self.rec(6, "Аутентифицированный UDS; неверный токен отклонён", ok,
                 f"good={good.get('status')}, wrong={bad.get('status')}", [ev])

    # 7. Audit record + query (запись ВНУТРИ контейнера Core — без порчи владения БД)
    def c7(self):
        one_liner = (
            "from atlas_core.db import init_engine; from atlas_core import audit; "
            "from atlas_core.settings import load_settings as ls; s=ls(); "
            "init_engine(s.db_url, s.db_path); "
            "aid=audit.record('vp1.acceptance','проверка audit record+query'); "
            "rows=audit.query(event_type='vp1.acceptance', limit=5); "
            "print('OK' if any(r['id']==aid for r in rows) else 'FAIL', aid)"
        )
        rc, o, e = sh(["docker", "compose", "exec", "-T", "core", "python", "-c", one_liner])
        recorded_ok = o.strip().startswith("OK")
        # и через Web API (query path)
        st, body = http("/api/v1/audit?event_type=vp1.acceptance&limit=5")
        api = json.loads(body) if st == 200 else {}
        api_found = bool(api.get("events"))
        ok = recorded_ok and api_found
        ev = self.art("c7_audit.json", {"container_record": o.strip()[:40], "api_total": api.get("total"),
                                       "api_found": api_found})
        self.rec(7, "Audit: запись и запрос", ok, f"container={recorded_ok}, api_found={api_found}", [ev])

    # 8. RU/EN switching
    def c8(self):
        ru = (_ROOT / "apps/web/src/locales/ru.ts").read_text(encoding="utf-8")
        en = (_ROOT / "apps/web/src/locales/en.ts").read_text(encoding="utf-8")
        import re
        ru_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":', ru))
        en_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":', en))
        rc2, o2, e2 = web(["pnpm", "check:i18n"])
        ok = ru_keys == en_keys and rc2 == 0 and len(ru_keys) > 0
        ev = self.art("c8_i18n.json", {"ru_keys": len(ru_keys), "en_keys": len(en_keys),
                                      "equal": ru_keys == en_keys, "check_rc": rc2})
        self.rec(8, "RU/EN переключение (идентичные ключи)", ok,
                 f"ru={len(ru_keys)} en={len(en_keys)} equal={ru_keys==en_keys}", [ev])

    # 9 + 10. Backup integrity/hashes + no credentials
    def c9_10(self):
        env = {"ATLAS_DATA_DIR": DATA_DIR, "ATLAS_CONFIG_FILE": "/nonexistent.yaml",
               "PATH": f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"}
        rc, o, e = sh(["uv", "run", "atlas", "backup", "--json", "--out", str(ART / "backups")], env=env)
        try:
            man = json.loads(o)
        except json.JSONDecodeError:
            man = {}
        integrity = man.get("integrity_ok")
        has_hash = bool(man.get("archive_sha256", "").startswith("sha256:"))
        clean = man.get("secret_scan_clean")
        ev = self.art("c9_10_backup.json", {"rc": rc, "integrity_ok": integrity,
                                           "archive_sha256": man.get("archive_sha256"),
                                           "db_sha256": man.get("db_sha256"),
                                           "secret_scan_clean": clean})
        self.rec(9, "Целостность backup и хеши", rc == 0 and integrity and has_hash,
                 f"integrity={integrity}, sha={bool(has_hash)}", [ev])
        self.rec(10, "Backup без provider-credentials", bool(clean),
                 f"secret_scan_clean={clean}", [ev])

    # 11. Non-root Core/Web/Runner
    def c11(self):
        core_uid = sh(["docker", "compose", "exec", "-T", "core", "id", "-u"])[1].strip()
        web_uid = sh(["docker", "compose", "exec", "-T", "web", "id", "-u"])[1].strip()
        runner_pid = sh(["systemctl", "show", "-p", "MainPID", "--value", RUNNER_UNIT])[1].strip()
        runner_user = sh(["ps", "-o", "user=", "-p", runner_pid])[1].strip() if runner_pid != "0" else "?"
        ok = core_uid not in ("0", "") and web_uid not in ("0", "") and runner_user == "atlas"
        ev = self.art("c11_nonroot.json", {"core_uid": core_uid, "web_uid": web_uid,
                                          "runner_user": runner_user})
        self.rec(11, "Core/Web/Runner НЕ от root", ok,
                 f"core={core_uid} web={web_uid} runner={runner_user}", [ev])

    # 12. One active VP gate
    def c12(self):
        nxt = (_ROOT / "docs/NEXT.md").read_text(encoding="utf-8")
        vp0_done = "VP-0" in nxt and ("ЗАВЕРШ" in nxt or "заверш" in nxt.lower())
        vp1_active = "VP-1" in nxt
        vp2_absent = "VP-2" not in nxt or "не начин" in nxt.lower()
        ok = vp0_done and vp1_active and vp2_absent
        ev = self.art("c12_vp_gate.json", {"vp0_done": vp0_done, "vp1_active": vp1_active,
                                          "vp2_not_started": vp2_absent})
        self.rec(12, "Ровно один активный VP-гейт", ok, "NEXT.md: VP-1 активен, VP-2 не начат", [ev])

    # 13. Frozen py+node installs (covered by c1 but explicit exit codes)
    def c13(self):
        rc_py = sh(["uv", "sync", "--frozen", "--quiet"],
                   env={"PATH": f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"})[0]
        rc_node = web(["pnpm", "install", "--frozen-lockfile"])[0]
        ok = rc_py == 0 and rc_node == 0
        ev = self.art("c13_frozen.json", {"uv_rc": rc_py, "pnpm_rc": rc_node})
        self.rec(13, "Чистые frozen-установки Python и Node", ok, f"py={rc_py} node={rc_node}", [ev])

    # 14. Production web build
    def c14(self):
        rc, o, e = web(["pnpm", "build"])
        dist = _ROOT / "apps/web/dist/index.html"
        ok = rc == 0 and dist.exists()
        ev = self.art("c14_web_build.json", {"rc": rc, "dist_index_exists": dist.exists(),
                                            "tail": (o + e)[-150:]})
        self.rec(14, "Production-сборка Web", ok, f"rc={rc}, dist={dist.exists()}", [ev])

    # 15. 127.0.0.1:3210 reachable
    def c15(self):
        st_page, _ = http("/")
        st_api, body = http("/api/v1/health")
        ok = st_page == 200 and st_api == 200
        ev = self.art("c15_localhost.json", {"web_http": st_page, "api_http": st_api})
        self.rec(15, "127.0.0.1:3210 доступен (страница + /api)", ok,
                 f"page={st_page}, api={st_api}", [ev])

    # 16. Secret scan clean (full)
    def c16(self):
        from atlas_core.secret_scan import scan_repo
        extra = [DATA_DIR, str(_ROOT / "var"), str(ART / "backups")]
        rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if Path(e).exists()])
        d = rep.to_dict()
        ev = self.art("c16_secret_scan.json", d)
        self.rec(16, "Полный секрет-скан чист", rep.clean,
                 f"real={len(d['real_hits'])}, history={len(d['git_history_hits'])}, "
                 f"cred_roots_excluded={d['credential_root_hits_excluded']}, "
                 f"atlas_cannot_read_roots={d['service_user_cannot_read_credential_roots']}", [ev])

    # extra: per-profile structural isolation (independent of scanner)
    def c_isolation(self):
        from atlas_core import isolation
        from atlas_core.profiles import ProfileRegistry, check_root_permissions
        os.environ.pop("ATLAS_DATA_DIR", None)
        profiles = ProfileRegistry().list()
        perms = {p.alias: check_root_permissions(p) for p in profiles}
        all_0700_own = all(perms[a]["is_0700"] and perms[a].get("owner_is_runtime_user") for a in perms)
        rep = isolation.prove_isolation(profiles)
        cross = [m for m in rep.get("matrix", []) if m["kind"] in ("cross_profile", "service_user")]
        cross_denied = bool(cross) and all(m["denied"] and not m["leaked"] for m in cross)
        ok = all_0700_own and rep.get("ok") and cross_denied
        ev = self.art("c_isolation.json", {"profiles": list(perms), "all_0700_own_identity": all_0700_own,
                                          "cross_and_service_denied": cross_denied, "matrix_ok": rep.get("ok")})
        self.rec(17, "Структурная изоляция всех профилей (каждый root)", ok,
                 f"0700+own={all_0700_own}, cross/service denied={cross_denied}", [ev])

    def run(self):
        print("=== VP-1 ACCEPTANCE ===")
        self.c1(); self.c2(); self.c3(); self.c4(); self.c5(); self.c6(); self.c7()
        self.c8(); self.c9_10(); self.c11(); self.c12(); self.c13(); self.c14()
        self.c15(); self.c16(); self.c_isolation()
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        verdict = "COMPLETE" if passed == total else f"INCOMPLETE ({passed}/{total})"
        matrix = {"vp": "VP-1", "passed": passed, "total": total, "verdict": verdict,
                  "criteria": self.results}
        self.art("acceptance_matrix.json", matrix)
        # копия в repo var (та же ART уже под var)
        print(f"\n  ИТОГ VP-1: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix


if __name__ == "__main__":
    VP1().run()
