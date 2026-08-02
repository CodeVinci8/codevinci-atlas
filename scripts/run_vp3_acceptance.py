#!/usr/bin/env python3
"""VP-3 acceptance boundary — Product Map (Master Spec §36).

Прогоняет 26 приёмочных проверок против РЕАЛЬНО развёрнутого стека (Compose
Core/Web + systemd Runner) и синтетических git-фикстур, пишет redacted-evidence
с SHA-256 в var/artifacts/vp3/. Итог COMPLETE только при 26/26 PASS.

Фикстуры уникальны по префиксу RUN; чистка удаляет ТОЛЬКО созданные этим
прогоном записи (по точным project_id) и каталоги — таблицы не truncate.

Запуск (root, стек поднят): PYTHONPATH=apps/core python3 scripts/run_vp3_acceptance.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))

ART = _ROOT / "var" / "artifacts" / "vp3"
ART.mkdir(parents=True, exist_ok=True)
WEB = "http://127.0.0.1:3210"
DATA_DIR = "/var/lib/codevinci-atlas"
DB_PATH = f"{DATA_DIR}/atlas.db"
WORKSPACES = f"{DATA_DIR}/workspaces"
RUN = time.strftime("%H%M%S")
_UVBIN = f"{os.environ.get('HOME', '/root')}/.local/bin:{os.environ['PATH']}"
_VENV = _ROOT / ".venv" / "bin"
PRE_0002 = ART / "pre_migration_0002.db"  # снимок живой 0002 БД (до rebuild)
_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Atlas Test", "GIT_AUTHOR_EMAIL": "atlas@local",
            "GIT_COMMITTER_NAME": "Atlas Test", "GIT_COMMITTER_EMAIL": "atlas@local",
            "GIT_TERMINAL_PROMPT": "0"}

# VP-3 таблицы для точечной чистки (по project_id). НИКОГДА не truncate.
_PM_TABLES = ("product_intakes", "briefs", "map_versions", "map_nodes", "map_edges",
              "decisions", "decision_events", "parking_items", "approvals",
              "vp_activations", "idempotency_keys",
              "git_baselines", "worktrees", "worktree_leases")


def _atlas_ids() -> tuple[int, int]:
    uid = int(subprocess.run(["id", "-u", "atlas"], capture_output=True, text=True).stdout.strip())
    gid = int(subprocess.run(["id", "-g", "atlas"], capture_output=True, text=True).stdout.strip())
    return uid, gid


ATLAS_UID, ATLAS_GID = _atlas_ids()


def sh(cmd, timeout=180, env=None, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd or str(_ROOT), env={**os.environ, **(env or {})})
    return r.returncode, r.stdout, r.stderr


def _loads(raw):
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"error": {"code": "NON_JSON", "reason": (raw or "")[:80]}}


def api(method, path, body=None, timeout=40, headers=None, raw=False):
    url = WEB + path
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json", "Accept": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            txt = resp.read().decode("utf-8", "replace")
            return resp.status, (txt if raw else _loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        return e.code, (txt if raw else _loads(txt))
    except Exception as exc:  # noqa: BLE001
        return None, {"error": {"code": "TRANSPORT", "reason": str(exc)}}


def fetch_text(path, timeout=15):
    try:
        with urllib.request.urlopen(WEB + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def chown_atlas(path):
    subprocess.run(["chown", "-R", f"{ATLAS_UID}:{ATLAS_GID}", path], check=False)


def make_repo(name):
    path = os.path.join(WORKSPACES, name)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], env=_GIT_ENV)
    Path(path, "README.md").write_text("# synthetic vp3\n")
    Path(path, "package.json").write_text('{"name":"syn","scripts":{"build":"vite build"}}\n')
    Path(path, "AGENTS.md").write_text("Root instructions.\n")
    subprocess.run(["git", "-C", path, "add", "-A"], env=_GIT_ENV)
    subprocess.run(["git", "-C", path, "commit", "-q", "-m", "initial"], env=_GIT_ENV)
    chown_atlas(path)
    return path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VP3:
    def __init__(self):
        self.results = []
        self.projects = []   # созданные project_id (для точечной чистки)
        self.repos = []      # созданные каталоги репо

    def art(self, name, content):
        p = ART / name
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                     if isinstance(content, (dict, list)) else str(content), encoding="utf-8")
        return p.name

    def rec(self, n, name, ok, note, ev=None):
        self.results.append({"id": n, "criterion": name, "status": "PASS" if ok else "FAIL",
                             "note": note, "evidence": ev or []})
        print(f"  [{'PASS' if ok else 'FAIL'}] #{n:>2} {name} — {note}")

    def connect_project(self, slug):
        repo = make_repo(f"vp3-{slug}-{RUN}")
        self.repos.append(repo)
        st, ov = api("POST", "/api/v1/projects",
                     {"name": f"vp3-{slug}-{RUN}", "source_kind": "local_git", "path": repo})
        pid = ov.get("project", {}).get("id", "")
        if pid:
            self.projects.append(pid)
        bl_hash = (ov.get("baseline") or {}).get("content_hash", "")
        return pid, bl_hash

    def intake_body(self):
        return {"idea": f"Инструмент наблюдения {RUN}", "target_user": "владелец-одиночка",
                "desired_result": "Видеть карту продукта\nЭкспортировать состояние",
                "constraints": ["без внешних вызовов"], "risks": ["scope creep"],
                "links": ["https://example.com/spec?token=x#frag"],
                "parking_suggestions": [{"title": "Мобильный вид", "reason": "позже",
                                         "return_condition": "после MVP"}]}

    # 1-3: intake -> draft brief + map + truth statuses
    def c1_c3(self):
        pid, bl = self.connect_project("main")
        self._p1 = pid
        self._bl = bl
        st, state = api("POST", f"/api/v1/projects/{pid}/intake", self.intake_body(),
                        headers={"X-Correlation-ID": f"c1-{RUN}"})
        self._brief1 = state.get("brief", {}) or {}
        ok1 = st == 201 and self._brief1.get("version") == 1
        map_nodes = (state.get("map") or {}).get("nodes", [])
        node_statuses = {n["truth_status"] for n in map_nodes}
        ok2 = bool(state.get("brief")) and bool(state.get("map")) and len(state.get("decisions", [])) >= 2
        # добавить версию карты со всеми не-VERIFIED статусами, проверить персист+различимость
        want = ["OWNER_PROVIDED", "INFERRED", "HYPOTHESIS", "STALE", "UNKNOWN"]
        nodes = [{"node_key": f"n{i}", "node_type": "blocker" if s == "STALE" else "goal",
                  "title": f"node {s}", "truth_status": s} for i, s in enumerate(want)]
        st3, mv = api("POST", f"/api/v1/projects/{pid}/map/versions",
                      {"nodes": nodes, "edges": [], "expected_version": 1})
        persisted = {n["truth_status"] for n in (mv.get("nodes") or [])}
        ok3 = st3 == 201 and set(want).issubset(persisted)
        ev = self.art("c1_c3_intake_truth.json",
                      {"intake_http": st, "brief_version": self._brief1.get("version"),
                       "map_node_statuses": sorted(node_statuses),
                       "extra_map_http": st3, "persisted_statuses": sorted(persisted)})
        self.rec(1, "Подключённый проект принимает bounded intake", ok1, f"http={st}", [ev])
        self.rec(2, "Intake создаёт Draft Brief и Draft Map с truth-status", ok2,
                 f"brief={bool(state.get('brief'))} map_nodes={len(map_nodes)}", [ev])
        self.rec(3, "OWNER_PROVIDED/INFERRED/HYPOTHESIS/STALE/UNKNOWN персистятся и различимы",
                 ok3, f"persisted={sorted(persisted)}", [ev])

    # 4-9: evidence, immutability, parent link, stale, diff
    def c4_c9(self):
        pid = self._p1
        b1 = self._brief1
        h1_before = b1.get("content_hash")
        # 4: VERIFIED bad evidence -> 422
        st4, r4 = api("POST", f"/api/v1/projects/{pid}/briefs/{b1['id']}/revise",
                      {"changes": {"confirmed_facts": [{"text": "факт", "truth_status": "VERIFIED",
                       "evidence_ref": "git_baseline:latest", "evidence_hash": "sha256:" + "0" * 64}]},
                       "expected_version": 1})
        code4 = (r4.get("error") or {}).get("code")
        # 5: VERIFIED valid evidence -> 201 v2
        st5, b2 = api("POST", f"/api/v1/projects/{pid}/briefs/{b1['id']}/revise",
                      {"changes": {"confirmed_facts": [{"text": "baseline подтверждён",
                       "truth_status": "VERIFIED", "evidence_ref": "git_baseline:latest",
                       "evidence_hash": self._bl}],
                       "promised_result": "Обновлённый обещанный результат"},
                       "expected_version": 1})
        self._brief2 = b2
        verified_ok = any(f.get("truth_status") == "VERIFIED"
                          for f in (b2.get("content", {}).get("confirmed_facts", [])))
        # 6: v1 immutable
        _, v1_again = api("GET", f"/api/v1/projects/{pid}/briefs/{b1['id']}")
        h1_after = v1_again.get("content_hash")
        # 7: parent link
        parent_ok = b2.get("parent_id") == b1["id"] and b2.get("version") == 2
        # 8: stale expected version -> 409
        st8, r8 = api("POST", f"/api/v1/projects/{pid}/briefs/{b1['id']}/revise",
                      {"changes": {"promised_result": "конфликт"}, "expected_version": 1})
        code8 = (r8.get("error") or {}).get("code")
        # 9: diff exact fields
        _, diff = api("GET", f"/api/v1/projects/{pid}/briefs/diff?from=1&to=2")
        changed = set((diff.get("content") or {}).get("changed", {}).keys())
        ev = self.art("c4_c9_evidence_versions.json",
                      {"verified_bad_http": st4, "verified_bad_code": code4,
                       "verified_ok_http": st5, "verified_persisted": verified_ok,
                       "v1_hash_before": h1_before, "v1_hash_after": h1_after,
                       "parent_ok": parent_ok, "stale_http": st8, "stale_code": code8,
                       "diff_changed_fields": sorted(changed)})
        self.rec(4, "VERIFIED без валидного evidence отклонён",
                 st4 == 422 and code4 == "EVIDENCE_INVALID", f"http={st4} code={code4}", [ev])
        self.rec(5, "Валидный evidence ID/hash поддерживает VERIFIED",
                 st5 == 201 and verified_ok, f"http={st5} verified={verified_ok}", [ev])
        self.rec(6, "Первая версия Brief immutable",
                 bool(h1_before) and h1_before == h1_after, f"unchanged={h1_before == h1_after}", [ev])
        self.rec(7, "Правка создаёт вторую версию, связанную с родителем", parent_ok,
                 f"v={b2.get('version')} parent_ok={parent_ok}", [ev])
        self.rec(8, "Устаревшая expected-версия отклонена без тихой перезаписи",
                 st8 == 409 and code8 == "VERSION_CONFLICT", f"http={st8} code={code8}", [ev])
        self.rec(9, "Diff версий сообщает точные изменённые поля",
                 "confirmed_facts" in changed and "promised_result" in changed,
                 f"changed={sorted(changed)}", [ev])

    # 10-12: decisions + approval binding
    def c10_c12(self):
        pid = self._p1
        _, decs = api("GET", f"/api/v1/projects/{pid}/decisions")
        decisions = decs.get("decisions", [])
        required = [d for d in decisions if d["required"]]
        optional = [d for d in decisions if not d["required"]]
        # 10: reject one (optional) with note, then accept required
        hist_ok = True
        if optional:
            d0 = optional[0]
            api("POST", f"/api/v1/projects/{pid}/decisions/{d0['id']}/reject",
                {"note": "пока не нужно", "expected_version": d0["version"]})
            _, got = api("GET", f"/api/v1/projects/{pid}/decisions/{d0['id']}")
            hist_ok = got.get("status") == "rejected" and len(got.get("history", [])) >= 2 \
                and got.get("note") == "пока не нужно"
        # 11: approval blocked while required unresolved
        b2 = self._brief2
        st11, r11 = api("POST", f"/api/v1/projects/{pid}/briefs/{b2['id']}/approve",
                        {"expected_version": 2})
        code11 = (r11.get("error") or {}).get("code")
        # resolve required
        for d in required:
            api("POST", f"/api/v1/projects/{pid}/decisions/{d['id']}/accept",
                {"expected_version": d["version"]})
        # 12: approve now binds hashes
        st12, ap = api("POST", f"/api/v1/projects/{pid}/briefs/{b2['id']}/approve",
                       {"expected_version": 2})
        bind_ok = (st12 == 201 and ap.get("brief_hash") == b2.get("content_hash")
                   and ap.get("map_version_id") and ap.get("envelope_hash")
                   and ap.get("decisions_hash"))
        self._approval = ap
        ev = self.art("c10_c12_decisions_approval.json",
                      {"decisions": len(decisions), "reject_history_ok": hist_ok,
                       "approve_blocked_http": st11, "approve_blocked_code": code11,
                       "approve_http": st12, "bind_ok": bool(bind_ok),
                       "approval": ap})
        self.rec(10, "Решения принимаются/отклоняются поштучно с историей", hist_ok,
                 f"history_ok={hist_ok}", [ev])
        self.rec(11, "Approval заблокирован при неразрешённых required-решениях",
                 st11 == 409 and code11 == "DECISION_UNRESOLVED", f"http={st11} code={code11}", [ev])
        self.rec(12, "Approval связывает точные Brief/Map/envelope/decisions-hash", bool(bind_ok),
                 f"http={st12} bind_ok={bool(bind_ok)}", [ev])

    # 13-14: map dangling + parking
    def c13_c14(self):
        pid = self._p1
        # 13: dangling edge -> 422 MAP_INVALID (карта уже v2 после c3)
        _, mv = api("GET", f"/api/v1/projects/{pid}/map")
        cur = mv.get("version", 1)
        st13, r13 = api("POST", f"/api/v1/projects/{pid}/map/versions",
                        {"nodes": [{"node_key": "a", "node_type": "goal", "title": "A"}],
                         "edges": [{"src_key": "a", "dst_key": "ghost", "edge_type": "next"}],
                         "expected_version": cur})
        code13 = (r13.get("error") or {}).get("code")
        # 14: parking outside scope + survives versioning
        api("POST", f"/api/v1/projects/{pid}/parking-lot",
            {"title": f"park-{RUN}", "reason": "позже", "return_condition": "после MVP"})
        _, before = api("GET", f"/api/v1/projects/{pid}/parking-lot")
        b2 = self._brief2
        # новая версия brief (v3) — parking должен пережить
        api("POST", f"/api/v1/projects/{pid}/briefs/{b2['id']}/revise",
            {"changes": {"main_scenario": "сценарий"}, "expected_version": 2})
        _, after = api("GET", f"/api/v1/projects/{pid}/parking-lot")
        _, latest = api("GET", f"/api/v1/projects/{pid}/briefs")
        latest_brief = latest.get("briefs", [])[-1]
        _, lb = api("GET", f"/api/v1/projects/{pid}/briefs/{latest_brief['id']}")
        mvp = set(lb.get("content", {}).get("mvp_scope", []))
        park_titles = {p["title"] for p in after.get("parking_lot", [])}
        survived = len(after.get("parking_lot", [])) == len(before.get("parking_lot", [])) >= 1
        outside = not (park_titles & mvp)
        ev = self.art("c13_c14_map_parking.json",
                      {"dangling_http": st13, "dangling_code": code13,
                       "parking_before": len(before.get("parking_lot", [])),
                       "parking_after": len(after.get("parking_lot", [])),
                       "survived": survived, "outside_scope": outside})
        self.rec(13, "Невалидные/висячие рёбра Map отклонены",
                 st13 == 422 and code13 == "MAP_INVALID", f"http={st13} code={code13}", [ev])
        self.rec(14, "Parking вне активного scope и переживает версионирование",
                 survived and outside, f"survived={survived} outside={outside}", [ev])

    # 15: one active VP + bounded concurrency
    def c15(self):
        pid, _ = self.connect_project("vp")
        api("POST", f"/api/v1/projects/{pid}/intake", {"idea": "vp test"})
        results = {"ok": 0, "conflict": 0, "other": 0}
        lock = threading.Lock()

        def worker(k):
            st, r = api("POST", f"/api/v1/projects/{pid}/map/vps/activate", {"vp_key": f"VP-{k}"})
            with lock:
                if st == 201:
                    results["ok"] += 1
                elif st == 409 and (r.get("error") or {}).get("code") == "ACTIVE_VP_CONFLICT":
                    results["conflict"] += 1
                else:
                    results["other"] += 1

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, active = api("GET", f"/api/v1/projects/{pid}/vp")
        ok = results["ok"] == 1 and results["conflict"] == 7 and results["other"] == 0 \
            and active.get("active_vp")
        ev = self.art("c15_one_active_vp.json", {**results, "active_vp": active.get("active_vp")})
        self.rec(15, "Ровно один активный VP; вторая активация отклонена (concurrency)", bool(ok),
                 f"ok={results['ok']} conflict={results['conflict']} other={results['other']}", [ev])

    # 16: restart durability
    def c16(self):
        pid = self._p1
        _, before = api("GET", f"/api/v1/projects/{pid}/product-state")
        rc, _o, _e = sh(["docker", "compose", "restart", "core"], timeout=120)
        healthy = False
        for _ in range(30):
            s, _b = api("GET", "/api/v1/health", timeout=6)
            if s == 200:
                healthy = True
                break
            time.sleep(1)
        _, after = api("GET", f"/api/v1/projects/{pid}/product-state")
        _, ap = api("GET", f"/api/v1/projects/{pid}/briefs")
        durable = (after.get("approved_brief_version") == before.get("approved_brief_version")
                   == 2 and len(after.get("decisions", [])) == len(before.get("decisions", []))
                   and after.get("map"))
        ev = self.art("c16_restart_durability.json",
                      {"restart_rc": rc, "healthy": healthy,
                       "approved_before": before.get("approved_brief_version"),
                       "approved_after": after.get("approved_brief_version"),
                       "decisions": len(after.get("decisions", []))})
        self.rec(16, "Brief/Map/decisions/approval переживают рестарт Core",
                 rc == 0 and healthy and bool(durable), f"healthy={healthy} durable={bool(durable)}", [ev])

    # 17: portfolio across >=3 states, no fabricated data
    def c17(self):
        # P1 approved+active нет; создать draft-проект и intake_pending-проект
        pd, _ = self.connect_project("draft")
        api("POST", f"/api/v1/projects/{pd}/intake", {"idea": "draft only"})
        pe, _ = self.connect_project("pending")  # без intake
        st, pf = api("GET", "/api/v1/portfolio")
        rows = {r["project_id"]: r for r in pf.get("projects", [])}
        p1 = rows.get(self._p1, {})
        draft = rows.get(pd, {})
        pending = rows.get(pe, {})
        # никаких выдуманных полей прогресса/капасити/последнего запуска
        forbidden = {"progress", "percent", "capacity", "last_run", "activity"}
        clean = all(not (forbidden & set(r.keys())) for r in pf.get("projects", []))
        ok = (st == 200 and p1.get("stage") == "approved"
              and draft.get("stage") == "draft"
              and pending.get("stage") == "intake_pending"
              and pending.get("active_vp") == "UNKNOWN" and clean)
        ev = self.art("c17_portfolio.json",
                      {"http": st, "p1_stage": p1.get("stage"), "draft_stage": draft.get("stage"),
                       "pending_stage": pending.get("stage"),
                       "pending_active_vp": pending.get("active_vp"),
                       "no_fabricated_fields": clean, "rows": len(pf.get("projects", []))})
        self.rec(17, "Portfolio Map корректна на ≥3 состояниях и не выдумывает данные", bool(ok),
                 f"p1={p1.get('stage')} draft={draft.get('stage')} pending={pending.get('stage')}", [ev])

    # 18: exports MD+JSON same version, no secrets
    def c18(self):
        pid = self._p1
        stj, ej = api("GET", f"/api/v1/projects/{pid}/export?format=json")
        stm, em_text = fetch_text(f"/api/v1/projects/{pid}/export?format=md")
        # экспорт JSON использует принятую версию (approval brief_id -> version 2)
        brief_v = (ej.get("brief") or {}).get("version")
        md_has_hash = "content-hash" in em_text.lower() or "content hash" in em_text.lower()
        from atlas_core.redaction import SECRET_MARKER, scan_for_secrets
        json_text = json.dumps(ej, ensure_ascii=False)
        secret_hits = len(scan_for_secrets(json_text)) + len(scan_for_secrets(em_text))
        no_secret = SECRET_MARKER not in json_text and SECRET_MARKER not in em_text and secret_hits == 0
        (ART / f"export_{RUN}.json").write_text(json_text, encoding="utf-8")
        (ART / f"export_{RUN}.md").write_text(em_text, encoding="utf-8")
        same = brief_v == 2
        ev = self.art("c18_exports.json",
                      {"json_http": stj, "md_http": stm, "brief_version": brief_v,
                       "md_has_hash": md_has_hash, "secret_hits": secret_hits,
                       "json_sha256": sha256_text(json_text), "md_sha256": sha256_text(em_text)})
        self.rec(18, "Экспорт MD и JSON — та же принятая версия, без секрет-маркеров",
                 stj == 200 and stm == 200 and same and no_secret and md_has_hash,
                 f"json={stj} md={stm} v={brief_v} secrets={secret_hits}", [ev])

    # 19: RU/EN parity + accepted UI states markers
    def c19(self):
        import re
        rc, _o, _e = sh(["pnpm", "check:i18n"], env={"PATH": _UVBIN}, cwd=str(_ROOT / "apps/web"))
        ru = (_ROOT / "apps/web/src/locales/ru.ts").read_text()
        en = (_ROOT / "apps/web/src/locales/en.ts").read_text()
        rk = set(re.findall(r'"([a-zA-Z0-9_.]+)"\s*:', ru))
        ek = set(re.findall(r'"([a-zA-Z0-9_.]+)"\s*:', en))
        src = ((_ROOT / "apps/web/src/productmap.tsx").read_text()
               + (_ROOT / "apps/web/src/App.tsx").read_text())
        # принятые UI-состояния в исходнике: loading/offline/notAvailable/conflict/diff-none/error
        states = all(m in src for m in ("common.loading", "common.offline", "pm.notAvailable",
                                        "PROJECT_NOT_AVAILABLE", "pm.diff.none", 'role="alert"'))
        vp3_keys = {"truth.VERIFIED", "portfolio.title", "pm.intake.title", "nav.portfolio"}
        ok = rc == 0 and rk == ek and vp3_keys.issubset(rk) and vp3_keys.issubset(ek) and states
        ev = self.art("c19_i18n.json", {"check_rc": rc, "ru_keys": len(rk), "en_keys": len(ek),
                                        "equal": rk == ek, "ui_states_present": states,
                                        "vp3_keys_present": vp3_keys.issubset(rk)})
        self.rec(19, "RU/EN контролы, паритет каталогов и UI-состояния", bool(ok),
                 f"rc={rc} ru={len(rk)} en={len(ek)} equal={rk == ek} states={states}", [ev])

    # 20: dark default + a11y + responsive (CSS/DOM, strongest available boundary)
    def c20(self):
        st, page = fetch_text("/")
        import re
        mjs = re.search(r'src="(/assets/index-[A-Za-z0-9_]+\.js)"', page)
        mcss = re.search(r'href="(/assets/index-[A-Za-z0-9_]+\.css)"', page)
        js = fetch_text(mjs.group(1))[1] if mjs else ""
        css = fetch_text(mcss.group(1))[1] if mcss else ""
        css_nq = css.replace('"', "")  # минификатор убирает кавычки в [attr=val]
        # dark — утверждённый default: селектор [data-theme=dark] + JS-логика atlas.theme
        dark_default = "data-theme=dark" in css_nq and "atlas.theme" in js
        reduced = "prefers-reduced-motion" in css
        responsive = css.count("@media") >= 3 and "max-width:460px" in css.replace(" ", "")
        focus = "focus-visible" in css
        non_color = ".tbadge" in css  # символ+текст+цвет, не только цвет
        skip = "skip-link" in css and "skip" in js.lower()
        ok = st == 200 and dark_default and reduced and responsive and focus and non_color
        ev = self.art("c20_ui_a11y.json",
                      {"page_http": st, "dark_default": dark_default, "reduced_motion": reduced,
                       "responsive_media": responsive, "focus_visible": focus,
                       "non_color_badges": non_color, "skip_link": skip,
                       "note": "Браузер-автоматизация недоступна; проверены CSS/DOM-границы. "
                               "Финальный визуальный обзор — за владельцем."})
        self.rec(20, "Dark default, keyboard/focus/non-color, responsive (CSS/DOM)", bool(ok),
                 f"dark={dark_default} reduced={reduced} responsive={responsive} focus={focus}", [ev])

    # 21: migration empty + from live 0002 copy without losing VP-2 data
    def c21(self):
        alembic = str(_VENV / "alembic") if (_VENV / "alembic").exists() else "alembic"
        base_env = {"ATLAS_CONFIG_FILE": "/nonexistent.yaml",
                    "PATH": f"{_VENV}:{_UVBIN}", "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"}
        # (a) empty
        empty = ART / "mig_empty"
        if empty.exists():
            shutil.rmtree(empty)
        empty.mkdir(parents=True)
        rc_e, _o, e_e = sh([alembic, "upgrade", "head"], env={**base_env, "ATLAS_DATA_DIR": str(empty)})
        tabs_e = self._tables(str(empty / "atlas.db"))
        empty_ok = rc_e == 0 and {"briefs", "map_versions", "decisions", "approvals"}.issubset(tabs_e)
        # (b) from live 0002 snapshot (снят до rebuild)
        live_ok = False
        detail = {}
        if PRE_0002.exists():
            copy_dir = ART / "mig_live"
            if copy_dir.exists():
                shutil.rmtree(copy_dir)
            copy_dir.mkdir(parents=True)
            dst = copy_dir / "atlas.db"
            shutil.copy(PRE_0002, dst)
            before_rev = self._rev(str(dst))
            before_counts = self._vp2_counts(str(dst))
            rc_l, _o2, e_l = sh([alembic, "upgrade", "head"],
                                env={**base_env, "ATLAS_DATA_DIR": str(copy_dir)})
            after_rev = self._rev(str(dst))
            after_counts = self._vp2_counts(str(dst))
            live_ok = (before_rev == "0002_project_workspace" and rc_l == 0
                       and after_rev == "0003_product_map" and before_counts == after_counts)
            detail = {"before_rev": before_rev, "after_rev": after_rev,
                      "before_counts": before_counts, "after_counts": after_counts}
        else:
            detail = {"note": "pre_migration_0002.db snapshot отсутствует"}
        ev = self.art("c21_migrations.json",
                      {"empty_rc": rc_e, "empty_tables_ok": empty_ok, "live": detail})
        self.rec(21, "Миграция из пустой БД и из копии живой 0002 без потери данных VP-2",
                 empty_ok and live_ok, f"empty={empty_ok} live={live_ok}", [ev])

    def _tables(self, db):
        try:
            c = sqlite3.connect(db)
            t = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            c.close()
            return t
        except sqlite3.Error:
            return set()

    def _rev(self, db):
        try:
            c = sqlite3.connect(db)
            row = c.execute("SELECT version_num FROM alembic_version").fetchone()
            c.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _vp2_counts(self, db):
        c = sqlite3.connect(db)
        out = {}
        for t in ("projects", "git_baselines", "worktrees", "audit_events"):
            try:
                out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        c.close()
        return out

    # 22: VP-1/VP-2 regression
    def c22(self):
        _, h = api("GET", "/api/v1/health")
        st_a, audit = api("GET", "/api/v1/audit?event_type=productmap.brief.approved&limit=5")
        # VP-2: connect + baseline + worktree + second writer denied
        pid, _bl = self.connect_project("reg")
        stw, ovw = api("POST", f"/api/v1/projects/{pid}/worktrees", {"branch": "atlas/vp-3-reg"})
        wt = (ovw.get("worktrees") or [{}])[0].get("path", "")
        st2w, r2w = api("POST", f"/api/v1/projects/{pid}/worktrees/acquire", {"worktree_path": wt})
        from atlas_core.runner_health import runner_health
        good = runner_health("/run/codevinci-atlas/runner.sock", "/run/codevinci-atlas/runner.token")
        badf = ART / "c22_badtoken"
        badf.write_text("WRONG")
        bad = runner_health("/run/codevinci-atlas/runner.sock", str(badf))
        badf.unlink()
        rc_b, o_b, _ = sh([str(_VENV / "python"), "-m", "atlas_core.cli", "backup", "--json",
                           "--out", str(ART / "backups")],
                          env={"ATLAS_DATA_DIR": DATA_DIR, "ATLAS_CONFIG_FILE": "/nonexistent.yaml",
                               "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})
        try:
            man = json.loads(o_b)
        except json.JSONDecodeError:
            man = {}
        ok = (h.get("status") == "READY" and st_a == 200 and audit.get("events")
              and stw == 201 and st2w == 409
              and (r2w.get("error") or {}).get("code") == "WORKTREE_CONFLICT"
              and good.get("status") == "READY" and bad.get("status") == "UNAUTHORIZED"
              and rc_b == 0 and man.get("integrity_ok") and man.get("secret_scan_clean"))
        ev = self.art("c22_regression.json",
                      {"health": h.get("status"), "audit_http": st_a,
                       "worktree_http": stw, "second_writer_http": st2w,
                       "uds_good": good.get("status"), "uds_bad": bad.get("status"),
                       "backup_rc": rc_b, "backup_integrity": man.get("integrity_ok"),
                       "backup_clean": man.get("secret_scan_clean")})
        self.rec(22, "Нет регрессий VP-1 (health/audit/UDS/backup) и VP-2 (worktree/lease)", bool(ok),
                 f"health={h.get('status')} 2writer={st2w} uds={good.get('status')}/{bad.get('status')}", [ev])

    # 23: non-root + healthy
    def c23(self):
        core_uid = sh(["docker", "compose", "exec", "-T", "core", "id", "-u"])[1].strip()
        web_uid = sh(["docker", "compose", "exec", "-T", "web", "id", "-u"])[1].strip()
        rpid = sh(["systemctl", "show", "-p", "MainPID", "--value", "codevinci-atlas-runner.service"])[1].strip()
        ruser = sh(["ps", "-o", "user=", "-p", rpid])[1].strip() if rpid not in ("0", "") else "?"
        _, h = api("GET", "/api/v1/health")
        ok = core_uid not in ("0", "") and web_uid not in ("0", "") and ruser == "atlas" \
            and h.get("status") == "READY"
        ev = self.art("c23_nonroot_health.json",
                      {"core_uid": core_uid, "web_uid": web_uid, "runner_user": ruser,
                       "health": h.get("status")})
        self.rec(23, "Core/Web/Runner non-root и healthy", ok,
                 f"core={core_uid} web={web_uid} runner={ruser} health={h.get('status')}", [ev])

    # 24: exactly one active VP-gate in repo
    def c24(self):
        nxt = (_ROOT / "docs/NEXT.md").read_text()
        low = " ".join(nxt.lower().split())  # схлопнуть переносы строк
        vp2_done = "vp-2" in low and ("смёрж" in low or "заверш" in low)
        vp3_active = "vp-3" in low and "активн" in low
        vp4_not = "vp-4" in low and ("не начин" in low or "не начат" in low)
        ok = vp2_done and vp3_active and vp4_not
        ev = self.art("c24_vp_gate.json",
                      {"vp2_done": vp2_done, "vp3_active": vp3_active, "vp4_not_started": vp4_not})
        self.rec(24, "Ровно один активный VP-гейт (VP-3)", ok,
                 f"vp2_done={vp2_done} vp3_active={vp3_active} vp4_not={vp4_not}", [ev])

    # 25: 3210 serves accepted VP-3 UI via web proxy
    def c25(self):
        st, page = fetch_text("/")
        import re
        m = re.search(r'src="(/assets/index-[A-Za-z0-9_]+\.js)"', page)
        markers = False
        bundle = m.group(1) if m else ""
        if m:
            js = fetch_text(m.group(1))[1]
            markers = all(x in js for x in ("nav.portfolio", "portfolio.title", "pm.intake.title",
                                            "truth.VERIFIED", "pm.tab.map"))
        st_api, _ = api("GET", "/api/v1/portfolio")
        ok = st == 200 and st_api == 200 and markers
        ev = self.art("c25_web_ui.json",
                      {"page_http": st, "portfolio_api_http": st_api, "bundle": bundle,
                       "vp3_markers": markers})
        self.rec(25, "127.0.0.1:3210 отдаёт принятый VP-3 UI через Web-прокси", ok,
                 f"page={st} api={st_api} markers={markers}", [ev])

    # 26: final secret scan clean
    def c26(self):
        from atlas_core.secret_scan import scan_repo
        extra = [DATA_DIR, str(_ROOT / "var"), WORKSPACES, str(ART), str(ART / "backups")]
        rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if os.path.exists(e)])
        d = rep.to_dict()
        ev = self.art("c26_secret_scan.json", d)
        self.rec(26, "Финальный секрет-скан чист (дерево/история/БД/логи/artifacts/exports)",
                 rep.clean, f"real={len(d['real_hits'])}, history={len(d['git_history_hits'])}", [ev])

    def cleanup(self):
        # Точечная чистка: только созданные этим прогоном project_id + каталоги.
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("PRAGMA busy_timeout=8000;")
            for pid in self.projects:
                for t in _PM_TABLES:
                    col = "id" if t == "projects" else "project_id"
                    try:
                        conn.execute(f"DELETE FROM {t} WHERE {col}=?", (pid,))
                    except sqlite3.Error:
                        pass
                conn.execute("DELETE FROM projects WHERE id=?", (pid,))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            print(f"  [cleanup] предупреждение БД: {str(exc)[:80]}")
        for repo in self.repos:
            try:
                if os.path.isdir(repo):
                    shutil.rmtree(repo)
            except OSError:
                pass

    def run(self):
        print("=== VP-3 ACCEPTANCE ===")
        try:
            self.c1_c3()
            self.c4_c9()
            self.c10_c12()
            self.c13_c14()
            self.c15()
            self.c16()
            self.c17()
            self.c18()
            self.c19()
            self.c20()
            self.c21()
            self.c22()
            self.c23()
            self.c24()
            self.c25()
            self.c26()
        finally:
            self.cleanup()
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        verdict = "COMPLETE" if passed == total else f"INCOMPLETE ({passed}/{total})"
        matrix = {"vp": "VP-3", "passed": passed, "total": total, "verdict": verdict,
                  "run": RUN, "criteria": sorted(self.results, key=lambda r: r["id"])}
        self.art("acceptance_matrix.json", matrix)
        print(f"\n  ИТОГ VP-3: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix


if __name__ == "__main__":
    VP3().run()
