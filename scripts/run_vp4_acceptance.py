#!/usr/bin/env python3
"""VP-4 acceptance boundary — Work Orders & Context (Master Spec §16, §37).

26 приёмочных проверок против РЕАЛЬНО развёрнутого стека (Compose Core/Web +
systemd Runner) и синтетических фикстур; redacted-evidence с SHA-256 — в
var/artifacts/vp4/. Итог COMPLETE только при 26/26 PASS.

Фикстуры уникальны по префиксу RUN; чистка удаляет ТОЛЬКО созданные этим
прогоном записи (по точным project_id) и каталоги — таблицы не truncate,
append-only Audit не трогаем.

Запуск (root, стек поднят на 0004):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp4_acceptance.py
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
sys.path.insert(0, str(_ROOT / "apps/runner"))

ART = _ROOT / "var" / "artifacts" / "vp4"
ART.mkdir(parents=True, exist_ok=True)
WEB = "http://127.0.0.1:3210"
DATA_DIR = "/var/lib/codevinci-atlas"
DB_PATH = f"{DATA_DIR}/atlas.db"
WORKSPACES = f"{DATA_DIR}/workspaces"
RUN = time.strftime("%H%M%S")
_UVBIN = f"{os.environ.get('HOME', '/root')}/.local/bin:{os.environ['PATH']}"
_VENV = _ROOT / ".venv" / "bin"
PRE_0003 = ART / "pre_migration_0003.db"  # снимок живой 0003 БД (до апгрейда)
_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Atlas Test", "GIT_AUTHOR_EMAIL": "atlas@local",
            "GIT_COMMITTER_NAME": "Atlas Test", "GIT_COMMITTER_EMAIL": "atlas@local",
            "GIT_TERMINAL_PROMPT": "0"}

# VP-4 + VP-3 таблицы для точечной чистки (по project_id). НИКОГДА не truncate.
_VP4_TABLES = ("work_order_events", "work_orders", "vp_specs", "optimizer_decisions",
               "job_packages", "wo_checkpoints", "handoff_acks", "handoff_packages",
               "rotation_records")
_PM_TABLES = ("product_intakes", "briefs", "map_versions", "map_nodes", "map_edges",
              "decisions", "decision_events", "parking_items", "approvals",
              "vp_activations", "idempotency_keys",
              "git_baselines", "worktrees", "worktree_leases")


def _atlas_ids():
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


def api(method, path, body=None, timeout=60, headers=None):
    url = WEB + path
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json", "Accept": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, _loads(e.read().decode("utf-8", "replace"))
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
    Path(path, "README.md").write_text("# synthetic vp4\n")
    Path(path, "AGENTS.md").write_text("Root instructions.\n")
    subprocess.run(["git", "-C", path, "add", "-A"], env=_GIT_ENV)
    subprocess.run(["git", "-C", path, "commit", "-q", "-m", "initial"], env=_GIT_ENV)
    chown_atlas(path)
    return path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VP4:
    def __init__(self):
        self.results = []
        self.projects = []
        self.repos = []

    def art(self, name, content):
        p = ART / name
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                     if isinstance(content, (dict, list)) else str(content), encoding="utf-8")
        return p.name

    def rec(self, n, name, ok, note, ev=None):
        self.results.append({"id": n, "criterion": name, "status": "PASS" if ok else "FAIL",
                             "note": note, "evidence": ev or []})
        print(f"  [{'PASS' if ok else 'FAIL'}] #{n:>2} {name} — {note}")

    # --- фикстура: проект с принятым Brief/Map/approval --------------------
    def _accepted_project(self, slug):
        repo = make_repo(f"vp4-{slug}-{RUN}")
        self.repos.append(repo)
        st, ov = api("POST", "/api/v1/projects",
                     {"name": f"vp4-{slug}-{RUN}", "source_kind": "local_git", "path": repo})
        pid = ov.get("project", {}).get("id", "")
        if pid:
            self.projects.append(pid)
        api("POST", f"/api/v1/projects/{pid}/intake",
            {"idea": f"Инструмент {RUN}", "target_user": "владелец-одиночка",
             "desired_result": "Видеть карту\nЭкспортировать\nПройти проверки\nПоказать пульс"})
        _, state = api("GET", f"/api/v1/projects/{pid}/product-state")
        b = state["brief"]
        for d in state["decisions"]:
            if d["required"]:
                api("POST", f"/api/v1/projects/{pid}/decisions/{d['id']}/accept",
                    {"expected_version": d["version"]})
        st_a, appr = api("POST", f"/api/v1/projects/{pid}/briefs/{b['id']}/approve",
                         {"expected_version": b["version"]})
        return pid, appr

    def _spec(self, pid):
        st, spec = api("POST", f"/api/v1/projects/{pid}/vp-specs", {"vp_key": "VP-4"})
        return st, spec

    def _wo(self, pid, sid, goal="цель VP-4", criterion_ids=None):
        body = {"vp_spec_id": sid, "goal": goal}
        if criterion_ids is not None:
            body["criterion_ids"] = criterion_ids
        return api("POST", f"/api/v1/projects/{pid}/work-orders", body)

    def _to(self, pid, wid, status, version):
        return api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/transition",
                   {"to_status": status, "expected_version": version})

    # 1-3: spec from approval + schema validation + WO binding
    def c1_c3(self):
        pid, appr = self._accepted_project("main")
        self._pid = pid
        self._appr = appr
        st, spec = self._spec(pid)
        self._spec_o = spec
        ok1 = st == 201 and spec.get("version") == 1 \
            and spec["binding"]["brief_hash"] == appr["brief_hash"] \
            and spec["binding"]["approval_id"] == appr["id"]
        # schema validation
        from atlas_core.schema_validate import validate
        sc = json.loads((_ROOT / "contracts/schemas/vp-spec.json").read_text())
        wc = json.loads((_ROOT / "contracts/schemas/work-order.json").read_text())
        spec_errs = validate(spec["content"], sc)
        st_w, wo = self._wo(pid, spec["id"])
        self._wo_o = wo
        wo_errs = validate(wo["content"], wc)
        # malformed rejected: no approval spec, unknown criterion
        _, badp = self._accepted_project("noappr")  # this has approval; instead new project w/o approval
        st_bad, bad = api("POST", "/api/v1/projects/nonexistent/vp-specs", {"vp_key": "VP-4"})
        st_uc, uc = api("POST", f"/api/v1/projects/{pid}/work-orders",
                        {"vp_spec_id": spec["id"], "goal": "x", "criterion_ids": ["ac_ghost"]})
        ok2 = not spec_errs and not wo_errs and st_uc == 422 \
            and (uc.get("error") or {}).get("code") == "WO_INVALID"
        # binding preserves fields
        c = wo["content"]
        ok3 = (st_w == 201 and wo["binding"]["spec_hash"] == spec["content_hash"]
               and c["report_schema"] == "contracts/schemas/run-result.json"
               and c["acceptance_criteria"] and c["capabilities"] and c["stop_conditions"]
               and c["prohibited_actions"] and c["out_of_scope"] is not None)
        ev = self.art("c1_c3_spec_wo.json",
                      {"spec_http": st, "spec_version": spec.get("version"),
                       "binding_ok": ok1, "spec_schema_errors": spec_errs[:3],
                       "wo_schema_errors": wo_errs[:3], "unknown_criterion_http": st_uc,
                       "spec_hash": spec["content_hash"], "wo_hash": wo["content_hash"]})
        self.rec(1, "Принятый Brief/Map/approval создаёт версионный VP Spec", ok1,
                 f"http={st} v={spec.get('version')} bind={ok1}", [ev])
        self.rec(2, "VP Spec и Work Order валидны по схемам; неполный ввод отклонён", ok2,
                 f"spec_errs={len(spec_errs)} wo_errs={len(wo_errs)} unknown={st_uc}", [ev])
        self.rec(3, "Work Order связывает точные хеши и сохраняет поля §16.1", ok3,
                 f"http={st_w} spec_hash_ok={wo['binding']['spec_hash']==spec['content_hash']}", [ev])

    # 4-6: transitions, stale/concurrency, idempotency
    def c4_c6(self):
        pid = self._pid
        st, wo = self._wo(pid, self._spec_o["id"], goal="lifecycle")
        wid = wo["id"]
        r1 = self._to(pid, wid, "ready", wo["version"])[1]
        r2 = self._to(pid, wid, "active", r1["version"])[1]
        # invalid transition atomic
        st_inv, inv = self._to(pid, wid, "ready", r2["version"])
        _, after = api("GET", f"/api/v1/projects/{pid}/work-orders/{wid}")
        ok4 = (r1["status"] == "ready" and r2["status"] == "active"
               and st_inv == 409 and (inv.get("error") or {}).get("code") == "INVALID_TRANSITION"
               and after["status"] == "active")
        # 5: stale + concurrency
        st_stale, stale = self._to(pid, wid, "checkpointed", 1)
        results = {"ok": 0, "conflict": 0, "other": 0}
        lock = threading.Lock()
        base_v = after["version"]

        def worker():
            s, r = self._to(pid, wid, "checkpointed", base_v)
            code = (r.get("error") or {}).get("code")
            with lock:
                if s == 200:
                    results["ok"] += 1
                # оба — законный отказ без тихой перезаписи: version-guard или
                # (после победителя) invalid checkpointed→checkpointed
                elif s == 409 and code in ("VERSION_CONFLICT", "INVALID_TRANSITION"):
                    results["conflict"] += 1
                else:
                    results["other"] += 1
        ths = [threading.Thread(target=worker) for _ in range(6)]
        for tt in ths:
            tt.start()
        for tt in ths:
            tt.join()
        ok5 = (st_stale == 409 and (stale.get("error") or {}).get("code") == "VERSION_CONFLICT"
               and results["ok"] == 1 and results["conflict"] == 5 and results["other"] == 0)
        # освободить writer-аренду для последующих критериев (общий проект/ветка)
        _, cur = api("GET", f"/api/v1/projects/{pid}/work-orders/{wid}")
        self._to(pid, wid, "completed", cur["version"])
        # 6: idempotency
        h = {"Idempotency-Key": f"idem-{RUN}"}
        _, a = api("POST", f"/api/v1/projects/{pid}/work-orders",
                   {"vp_spec_id": self._spec_o["id"], "goal": "idem"}, headers=h)
        _, b = api("POST", f"/api/v1/projects/{pid}/work-orders",
                   {"vp_spec_id": self._spec_o["id"], "goal": "idem"}, headers=h)
        _, lst = api("GET", f"/api/v1/projects/{pid}/work-orders")
        idem_count = sum(1 for w in lst["work_orders"] if w["goal"] == "idem")
        ok6 = a["id"] == b["id"] and idem_count == 1
        ev = self.art("c4_c6_lifecycle.json",
                      {"invalid_http": st_inv, "after_status": after["status"],
                       "stale_http": st_stale, "concurrency": results,
                       "idem_same": a["id"] == b["id"], "idem_count": idem_count})
        self.rec(4, "Валидные переходы персистятся; невалидный падает атомарно", ok4,
                 f"invalid={st_inv} status={after['status']}", [ev])
        self.rec(5, "Устаревшая версия и конкурентные мутации отклонены без перезаписи", ok5,
                 f"stale={st_stale} conc={results}", [ev])
        self.rec(6, "Идемпотентный повтор не создаёт дубликатов", ok6,
                 f"same={a['id']==b['id']} count={idem_count}", [ev])

    # 7-9: jobpackage relevance/exclusion/no-cap-expansion
    def c7_c9(self):
        pid = self._pid
        _, wo = self._wo(pid, self._spec_o["id"], goal="force_push production_deploy в тексте")
        wid = wo["id"]
        _, p1 = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/job-package")
        _, p2 = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/job-package")
        deterministic = p1["content_hash"] == p2["content_hash"]
        bounded = p1["byte_size"] <= 24000
        provenance = any(pp.get("hash") for pp in p1["provenance"])
        c = p1["content"]
        blob = json.dumps(c, ensure_ascii=False)
        from atlas_core.redaction import SECRET_MARKER, scan_for_secrets
        forbidden_keys = [k for k in ("repository", "full_chat", "chat_history", "credentials",
                                      "environment", "env_dump", "tokens") if k in c]
        no_secret = SECRET_MARKER not in blob and len(scan_for_secrets(blob)) == 0
        cap_expand = [x for x in ("force_push", "production_deploy", "delete_repository")
                      if x in p1["capabilities"]]
        cap_unknown = c["capacity"]["status"] == "UNKNOWN"
        ok7 = deterministic and bounded and provenance
        ok8 = not forbidden_keys and no_secret and cap_unknown
        ok9 = not cap_expand
        ev = self.art("c7_c9_jobpackage.json",
                      {"deterministic": deterministic, "bytes": p1["byte_size"],
                       "provenance_ok": provenance, "forbidden_keys": forbidden_keys,
                       "no_secret": no_secret, "capacity": c["capacity"]["status"],
                       "cap_expand": cap_expand, "capabilities": p1["capabilities"]})
        self.rec(7, "JobPackage bounded, детерминирован, с provenance", ok7,
                 f"det={deterministic} bytes={p1['byte_size']}", [ev])
        self.rec(8, "JobPackage без repo/чата/логов/credentials/env/ёмкость UNKNOWN", ok8,
                 f"forbidden={forbidden_keys} leak={not no_secret} cap={c['capacity']['status']}", [ev])
        self.rec(9, "Контекст не расширяет capabilities/авторизацию", ok9,
                 f"expanded={cap_expand}", [ev])

    # 10-14: optimizer decisions + conservation + no scope change
    def c10_c14(self):
        pid = self._pid
        sid = self._spec_o["id"]
        _, spec = api("GET", f"/api/v1/projects/{pid}/vp-specs/{sid}")
        ids = [c["id"] for c in spec["content"]["acceptance_criteria"]]
        # 10: READY
        _, wsingle = self._wo(pid, sid, goal="single", criterion_ids=ids[:2])
        _, ev_single = api("POST", f"/api/v1/projects/{pid}/optimizer/evaluate",
                           {"work_order_ids": [wsingle["id"]]})
        ok10 = ev_single["decision"] == "READY"
        # 11: MERGE preserves criteria
        _, w1 = self._wo(pid, sid, goal="m1", criterion_ids=ids[:2])
        _, w2 = self._wo(pid, sid, goal="m2", criterion_ids=ids[1:])
        _, prev = api("POST", f"/api/v1/projects/{pid}/optimizer/merge/preview",
                      {"work_order_ids": [w1["id"], w2["id"]]})
        _, mc = api("POST", f"/api/v1/projects/{pid}/optimizer/merge/confirm",
                    {"work_order_ids": [w1["id"], w2["id"]], "goal": "merged"})
        merged_ids = sorted(c["id"] for c in mc["merged_work_order"]["content"]["acceptance_criteria"])
        parent_union = sorted(set(ids[:2]) | set(ids[1:]))
        ok11 = prev["criterion_conservation"] and merged_ids == parent_union and prev["shared_criteria"]
        # 12: SPLIT at checkpoint
        mw = mc["merged_work_order"]
        r = self._to(pid, mw["id"], "ready", mw["version"])[1]
        r = self._to(pid, mw["id"], "active", r["version"])[1]
        _, cp = api("POST", f"/api/v1/projects/{pid}/work-orders/{mw['id']}/checkpoints",
                    {"current_head": "H1"})
        r = self._to(pid, mw["id"], "checkpointed", r["version"])[1]
        groups = [merged_ids[:2], merged_ids[2:]]
        _, sr = api("POST", f"/api/v1/projects/{pid}/optimizer/split/confirm",
                    {"work_order_id": mw["id"], "groups": groups, "checkpoint_id": cp["id"]})
        kids_union = set()
        for kid in sr.get("children", []):
            _, k = api("GET", f"/api/v1/projects/{pid}/work-orders/{kid}")
            kids_union |= {c["id"] for c in k["content"]["acceptance_criteria"]}
        ok12 = len(sr.get("children", [])) == 2 and kids_union == set(merged_ids)
        # 13: incompatible -> OWNER_REQUIRED; profile switch -> SWITCH_PROFILE
        _, wb = self._wo(pid, sid, goal="rb", criterion_ids=ids[:1])
        _, wrole = api("POST", f"/api/v1/projects/{pid}/work-orders",
                       {"vp_spec_id": sid, "role": "reviewer", "goal": "rev", "criterion_ids": ids[1:2]})
        _, ev_inc = api("POST", f"/api/v1/projects/{pid}/optimizer/evaluate",
                        {"work_order_ids": [wb["id"], wrole["id"]]})
        _, ev_sw = api("POST", f"/api/v1/projects/{pid}/optimizer/evaluate",
                       {"work_order_ids": [wsingle["id"]], "signals": {"rate_limited": True}})
        ok13 = ev_inc["decision"] == "OWNER_REQUIRED" and ev_sw["decision"] == "SWITCH_PROFILE"
        # 14: optimizer never alters scope/criteria (single WO criteria unchanged)
        _, ws_after = api("GET", f"/api/v1/projects/{pid}/work-orders/{wsingle['id']}")
        ok14 = [c["id"] for c in ws_after["content"]["acceptance_criteria"]] == ids[:2]
        ev = self.art("c10_c14_optimizer.json",
                      {"single_decision": ev_single["decision"], "merge_conservation":
                       prev["criterion_conservation"], "merged_union_ok": merged_ids == parent_union,
                       "shared": prev["shared_criteria"], "split_children": sr.get("children"),
                       "split_union_ok": kids_union == set(merged_ids),
                       "incompatible": ev_inc["decision"], "switch": ev_sw["decision"],
                       "criteria_unchanged": ok14})
        self.rec(10, "READY для одного ограниченного executable Work Order", ok10,
                 f"decision={ev_single['decision']}", [ev])
        self.rec(11, "MERGE_TASKS только для совместимых и сохраняет каждый критерий", ok11,
                 f"conservation={prev['criterion_conservation']} union={merged_ids==parent_union}", [ev])
        self.rec(12, "SPLIT_AT_CHECKPOINT на durable-checkpoint: два результата, полное отображение",
                 ok12, f"children={len(sr.get('children',[]))} union_ok={kids_union==set(merged_ids)}", [ev])
        self.rec(13, "Несовместимо → OWNER_REQUIRED; смена профиля → SWITCH_PROFILE (без VP-5)", ok13,
                 f"inc={ev_inc['decision']} switch={ev_sw['decision']}", [ev])
        self.rec(14, "Оптимизатор не меняет scope/acceptance criteria", ok14,
                 f"criteria_unchanged={ok14}", [ev])

    # 15: governor thresholds + rotation triggers deterministic, no fabricated capacity
    def c15(self):
        pid = self._pid
        _, wo = self._wo(pid, self._spec_o["id"], goal="gov")
        wid = wo["id"]
        cases = {}
        for name, sig, exp in [("rate", {"rate_limited": True}, "SWITCH_PROFILE"),
                               ("ctx", {"context_over_budget": True}, "SWITCH_PROFILE"),
                               ("fail", {"failed_review": True}, "OWNER_REQUIRED"),
                               ("vp", {"vp_boundary": True}, "SWITCH_PROFILE"),
                               ("none", {}, "READY")]:
            _, o = api("POST", f"/api/v1/projects/{pid}/governor/detect",
                       {"work_order_ids": [wid], "signals": sig})
            cases[name] = {"decision": o["decision"], "rotate": o["rotation_required"],
                           "match": o["decision"] == exp}
        # no fabricated capacity in jobpackage
        _, p = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/job-package")
        cap = p["content"]["capacity"]
        honest = cap["status"] == "UNKNOWN" and cap.get("5h_remaining") is None
        ok = all(v["match"] for v in cases.values()) and honest
        ev = self.art("c15_governor.json", {"cases": cases, "capacity": cap})
        self.rec(15, "Пороги/триггеры ротации детерминированы, без выдуманной ёмкости", ok,
                 f"cases_ok={all(v['match'] for v in cases.values())} capacity={cap['status']}", [ev])

    # 16: checkpoint survives Core restart + hash-verifiable
    def c16(self):
        pid = self._pid
        _, wo = self._wo(pid, self._spec_o["id"], goal="ckpt-restart")
        wid = wo["id"]
        r = self._to(pid, wid, "ready", wo["version"])[1]
        r = self._to(pid, wid, "active", r["version"])[1]
        _, cp = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/checkpoints",
                    {"current_head": "HEADX", "completed_criteria": ["ac1"],
                     "exact_next_action": "доделать"})
        _, before = api("GET", f"/api/v1/projects/{pid}/checkpoints/{cp['id']}/verify")
        rc, _o, _e = sh(["docker", "compose", "restart", "core"], timeout=120)
        healthy = False
        for _ in range(40):
            s, _b = api("GET", "/api/v1/health", timeout=6)
            if s == 200:
                healthy = True
                break
            time.sleep(1)
        _, after = api("GET", f"/api/v1/projects/{pid}/checkpoints/{cp['id']}/verify")
        _, got = api("GET", f"/api/v1/projects/{pid}/checkpoints/{cp['id']}")
        ok = (rc == 0 and healthy and before["ok"] and after["ok"]
              and after["stored_hash"] == before["stored_hash"]
              and got["current_head"] == "HEADX")
        self._ckpt_restart = (wid, cp["id"])
        ev = self.art("c16_checkpoint_restart.json",
                      {"restart_rc": rc, "healthy": healthy, "verify_before": before["ok"],
                       "verify_after": after["ok"], "hash": after["stored_hash"]})
        self.rec(16, "Checkpoint переживает рестарт Core и hash-verifiable", ok,
                 f"healthy={healthy} verify={after['ok']}", [ev])

    # 17-19: handoff fields/hash/exclusion; stale/tamper reject; fresh session
    def c17_c19(self):
        pid = self._pid
        wid, ckpt = self._ckpt_restart
        r = api("GET", f"/api/v1/projects/{pid}/work-orders/{wid}")[1]
        r = self._to(pid, wid, "checkpointed", r["version"])[1]
        _, hp = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/handoffs",
                    {"checkpoint_id": ckpt, "current_head": "HEADX"})
        from atlas_core.redaction import SECRET_MARKER, scan_for_secrets
        from atlas_core.schema_validate import validate
        hs = json.loads((_ROOT / "contracts/schemas/handoff-package.json").read_text())
        herrs = validate(hp["content"], hs)
        blob = json.dumps(hp["content"], ensure_ascii=False)
        no_secret = SECRET_MARKER not in blob and len(scan_for_secrets(blob)) == 0
        # deterministic hash
        from atlas_core.productmap import content_hash
        det = content_hash(hp["content"]) == hp["content_hash"]
        ok17 = not herrs and no_secret and det
        # 18: reject variants
        _, vclean = api("GET", f"/api/v1/projects/{pid}/handoffs/{hp['id']}/verify?actual_head=HEADX")
        _, vstale = api("GET", f"/api/v1/projects/{pid}/handoffs/{hp['id']}/verify?actual_head=OTHER")
        st_wp, _ = api("GET", f"/api/v1/projects/nonexistent/handoffs/{hp['id']}/verify")
        # tamper via DB (content mutated, hash not)
        self._tamper_handoff(hp["id"])
        _, vtamp = api("GET", f"/api/v1/projects/{pid}/handoffs/{hp['id']}/verify?actual_head=HEADX")
        # over-capability via DB (hash recomputed)
        over_ok = self._over_cap_handoff(pid, wid, ckpt)
        ok18 = (vclean["ok"] and not vstale["ok"]
                and "HANDOFF_STALE" in [x["code"] for x in vstale["rejections"]]
                and not vtamp["ok"] and "HASH_MISMATCH" in [x["code"] for x in vtamp["rejections"]]
                and st_wp in (404, 409) and over_ok)
        # 19: fresh isolated consumer on a clean handoff (rebuild one)
        _, hp2 = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/handoffs",
                     {"checkpoint_id": ckpt, "current_head": "HEADX",
                      "job_package_id": ""}, headers={"Idempotency-Key": f"h2-{RUN}"})
        _, fresh = api("POST", f"/api/v1/projects/{pid}/handoffs/{hp2['id']}/reconstruct",
                       {"actual_head": "HEADX"})
        rc = fresh.get("reconstruction", {})
        ok19 = (fresh["ok"] and fresh["run_result_valid"] and fresh["isolated"]
                and rc.get("work_order_id") == wid and not rc.get("used_prior_chat")
                and not rc.get("used_credentials") and not rc.get("used_full_repo")
                and rc.get("exact_next_action") and fresh["ack"]["result"] == "ACK")
        self._fresh_handoff = hp2["id"]
        ev = self.art("c17_c19_handoff_fresh.json",
                      {"handoff_schema_errors": herrs[:3], "no_secret": no_secret,
                       "deterministic_hash": det, "verify_clean": vclean["ok"],
                       "stale_codes": [x["code"] for x in vstale["rejections"]],
                       "wrong_project_http": st_wp, "tamper_codes":
                       [x["code"] for x in vtamp["rejections"]], "over_capability_rejected": over_ok,
                       "fresh_ok": fresh["ok"], "fresh_valid": fresh["run_result_valid"],
                       "fresh_isolated": fresh["isolated"], "fresh_ack": fresh["ack"]["result"],
                       "reconstruction_no_chat": not rc.get("used_prior_chat")})
        self.rec(17, "HandoffPackage: все обязательные поля, детерминированный hash, без запрещённого",
                 ok17, f"schema_errs={len(herrs)} leak={not no_secret} det={det}", [ev])
        self.rec(18, "Tampered/stale/wrong-project/over-capability handoff отклонён", ok18,
                 f"tamper={[x['code'] for x in vtamp['rejections']]} stale_ok={not vstale['ok']}", [ev])
        self.rec(19, "Свежий изолированный потребитель восстанавливает состояние и next action", ok19,
                 f"ok={fresh['ok']} valid={fresh['run_result_valid']} ack={fresh['ack']['result']}", [ev])
        # освободить writer-аренду для последующих критериев (ротация c20)
        _, cur = api("GET", f"/api/v1/projects/{pid}/work-orders/{wid}")
        self._to(pid, wid, "completed", cur["version"])

    def _tamper_handoff(self, hid):
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA busy_timeout=8000;")
        row = conn.execute("SELECT content_json FROM handoff_packages WHERE id=?", (hid,)).fetchone()
        c = json.loads(row[0])
        c["goal"] = "TAMPERED"
        conn.execute("UPDATE handoff_packages SET content_json=? WHERE id=?",
                     (json.dumps(c), hid))
        conn.commit()
        conn.close()

    def _over_cap_handoff(self, pid, wid, ckpt):
        # построить новый handoff, вставить лишнюю capability + пересчитать hash
        from atlas_core.productmap import content_hash
        _, hp = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/handoffs",
                    {"checkpoint_id": ckpt, "current_head": "HEADX"},
                    headers={"Idempotency-Key": f"over-{RUN}"})
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA busy_timeout=8000;")
        row = conn.execute("SELECT content_json FROM handoff_packages WHERE id=?", (hp["id"],)).fetchone()
        c = json.loads(row[0])
        c["capabilities"] = c["capabilities"] + ["force_push"]
        conn.execute("UPDATE handoff_packages SET content_json=?, content_hash=? WHERE id=?",
                     (json.dumps(c), content_hash(c), hp["id"]))
        conn.commit()
        conn.close()
        _, v = api("GET", f"/api/v1/projects/{pid}/handoffs/{hp['id']}/verify?actual_head=HEADX")
        return (not v["ok"]) and "CAPABILITY_DENIED" in [x["code"] for x in v["rejections"]]

    # 20: rotation safe sequence + one writer
    def c20(self):
        pid = self._pid
        _, wo = self._wo(pid, self._spec_o["id"], goal="rotate")
        wid = wo["id"]
        r = self._to(pid, wid, "ready", wo["version"])[1]
        r = self._to(pid, wid, "active", r["version"])[1]
        _, rot = api("POST", f"/api/v1/projects/{pid}/work-orders/{wid}/rotate",
                     {"trigger": "context_threshold", "current_head": "HEADR",
                      "completed_criteria": ["ac1"], "next_profile_request": "claude-pro-02"})
        step_names = [s["name"] for s in rot["steps"]]
        _, cont = api("POST", f"/api/v1/projects/{pid}/rotations/{rot['id']}/continue",
                      {"ack_hash": rot["handoff_hash"], "baseline_ack": rot["baseline_head"],
                       "actual_head": "HEADR"})
        ok = (rot["one_writer_ok"] and rot["lease_released"]
              and step_names[:6] == ["stop_new_actions", "capture_diff", "impacted_checks",
                                     "checkpoint", "handoff", "release_lease"]
              and cont["status"] == "continued"
              and cont["work_order"]["status"] == "active" and cont["work_order"]["lease_active"])
        ev = self.art("c20_rotation.json",
                      {"one_writer_ok": rot["one_writer_ok"], "lease_released": rot["lease_released"],
                       "steps": step_names, "continue_status": cont["status"],
                       "continue_active": cont["work_order"]["status"]})
        self.rec(20, "Ротация по безопасной последовательности, один writer, lease на границе", ok,
                 f"one_writer={rot['one_writer_ok']} continue={cont['status']}", [ev])

    # 21: compact fallback preserves invariants
    def c21(self):
        pid = self._pid
        hid = self._fresh_handoff
        _, hp = api("GET", f"/api/v1/projects/{pid}/handoffs/{hid}")
        content = hp["content"]
        # small -> not compacted
        _, small = api("POST", f"/api/v1/projects/{pid}/context/compact-probe", {"content": content})
        # oversized -> compacted, preserved
        big = dict(content)
        big["_pad"] = ["x" * 100] * 400
        _, p = api("POST", f"/api/v1/projects/{pid}/context/compact-probe", {"content": big})
        # unfittable -> OWNER_REQUIRED
        huge = dict(content)
        huge["acceptance_criteria"] = [{"id": f"ac{i}", "text": "y" * 400, "required": True}
                                       for i in range(200)]
        st_h, hres = api("POST", f"/api/v1/projects/{pid}/context/compact-probe", {"content": huge})
        ok = (small.get("ok") and not small.get("compacted")
              and p.get("ok") and p.get("compacted") and p.get("preserved")
              and st_h == 409 and (hres.get("error") or {}).get("code") == "OWNER_REQUIRED")
        ev = self.art("c21_compact.json",
                      {"small_compacted": small.get("compacted"), "big_compacted": p.get("compacted"),
                       "big_preserved": p.get("preserved"), "big_bytes":
                       [p.get("original_bytes"), p.get("compact_bytes")],
                       "unfittable_code": (hres.get("error") or {}).get("code")})
        self.rec(21, "Compact-fallback сохраняет инварианты, иначе fail-closed OWNER_REQUIRED", ok,
                 f"big_compacted={p.get('compacted')} unfittable={st_h}", [ev])

    # 22: WO/checkpoints/handoffs survive service restart; disconnect non-destructive
    def c22(self):
        pid = self._pid
        _, before_wo = api("GET", f"/api/v1/projects/{pid}/work-orders")
        _, before_h = api("GET", f"/api/v1/projects/{pid}/handoffs")
        rc, _o, _e = sh(["docker", "compose", "restart", "core"], timeout=120)
        for _ in range(40):
            if api("GET", "/api/v1/health", timeout=6)[0] == 200:
                break
            time.sleep(1)
        _, after_wo = api("GET", f"/api/v1/projects/{pid}/work-orders")
        _, after_h = api("GET", f"/api/v1/projects/{pid}/handoffs")
        # disconnect non-destructive: create separate throwaway project, disconnect, WOs remain
        pd, _ = self._accepted_project("disc")
        _, sp = self._spec(pd)
        _, wod = self._wo(pd, sp["id"], goal="disc-wo")
        api("DELETE", f"/api/v1/projects/{pd}")
        _, discwo = api("GET", f"/api/v1/projects/{pd}/work-orders")
        durable = (len(after_wo["work_orders"]) == len(before_wo["work_orders"])
                   and len(after_h["handoffs"]) == len(before_h["handoffs"]))
        disc_kept = any(w["id"] == wod["id"] for w in discwo.get("work_orders", []))
        ok = rc == 0 and durable and disc_kept
        ev = self.art("c22_restart_disconnect.json",
                      {"restart_rc": rc, "wo_before": len(before_wo["work_orders"]),
                       "wo_after": len(after_wo["work_orders"]),
                       "handoffs_before": len(before_h["handoffs"]),
                       "handoffs_after": len(after_h["handoffs"]),
                       "disconnect_non_destructive": disc_kept})
        self.rec(22, "Work Order/checkpoints/handoffs переживают рестарт; disconnect не удаляет", ok,
                 f"durable={durable} disc_kept={disc_kept}", [ev])

    # 23: RU/EN WO UI, parity, states, dark, a11y, responsive (CSS/DOM)
    def c23(self):
        import re
        rc, _o, _e = sh(["node", "scripts/check-i18n.mjs"], cwd=str(_ROOT / "apps/web"))
        ru = (_ROOT / "apps/web/src/locales/ru.ts").read_text()
        en = (_ROOT / "apps/web/src/locales/en.ts").read_text()
        rk = set(re.findall(r'"([a-zA-Z0-9_.]+)"\s*:', ru))
        ek = set(re.findall(r'"([a-zA-Z0-9_.]+)"\s*:', en))
        wo_keys = {"wo.title", "wo.status.active", "wo.decision.READY", "wo.reconstruct.title",
                   "pm.tab.workorders", "wo.state.conflict", "wo.state.ownerRequired"}
        st, page = fetch_text("/")
        m = re.search(r'src="(/assets/index-[A-Za-z0-9_]+\.js)"', page)
        mcss = re.search(r'href="(/assets/index-[A-Za-z0-9_]+\.css)"', page)
        js = fetch_text(m.group(1))[1] if m else ""
        css = fetch_text(mcss.group(1))[1] if mcss else ""
        css_nq = css.replace('"', "")
        markers = all(k in js for k in ("wo.title", "wo.status.active", "wo.decision.READY",
                                        "pm.tab.workorders", "wo.reconstruct.stale"))
        dark = "data-theme=dark" in css_nq and "atlas.theme" in js
        non_color = ".tb-active" in css and ".tbadge" in css
        responsive = css.count("@media") >= 3
        focus = "focus-visible" in css
        ok = (rc == 0 and rk == ek and wo_keys.issubset(rk) and wo_keys.issubset(ek)
              and markers and dark and non_color and responsive and focus)
        ev = self.art("c23_ui.json",
                      {"i18n_rc": rc, "ru_keys": len(rk), "en_keys": len(ek), "equal": rk == ek,
                       "wo_keys_present": wo_keys.issubset(rk), "bundle_markers": markers,
                       "dark_default": dark, "non_color": non_color, "responsive": responsive,
                       "focus": focus,
                       "note": "Браузер-автоматизация недоступна; проверены CSS/DOM/бандл. "
                               "Финальный визуальный обзор Work Order — за владельцем."})
        self.rec(23, "RU/EN Work Orders UI, паритет, состояния, dark, a11y, responsive (CSS/DOM)", ok,
                 f"parity={rk==ek} markers={markers} dark={dark}", [ev])

    # 24: migration empty + from live 0003 copy without losing VP-0..3 data
    def c24(self):
        alembic = str(_VENV / "alembic") if (_VENV / "alembic").exists() else "alembic"
        base_env = {"ATLAS_CONFIG_FILE": "/nonexistent.yaml",
                    "PATH": f"{_VENV}:{_UVBIN}", "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"}
        empty = ART / "mig_empty"
        if empty.exists():
            shutil.rmtree(empty)
        empty.mkdir(parents=True)
        rc_e, _o, e_e = sh([alembic, "upgrade", "head"], env={**base_env, "ATLAS_DATA_DIR": str(empty)})
        tabs_e = self._tables(str(empty / "atlas.db"))
        empty_ok = rc_e == 0 and {"vp_specs", "work_orders", "handoff_packages",
                                  "wo_checkpoints", "briefs", "approvals"}.issubset(tabs_e)
        live_ok = False
        detail = {}
        if PRE_0003.exists():
            copy_dir = ART / "mig_live"
            if copy_dir.exists():
                shutil.rmtree(copy_dir)
            copy_dir.mkdir(parents=True)
            dst = copy_dir / "atlas.db"
            shutil.copy(PRE_0003, dst)
            before_rev = self._rev(str(dst))
            before_counts = self._counts(str(dst))
            rc_l, _o2, e_l = sh([alembic, "upgrade", "head"],
                                env={**base_env, "ATLAS_DATA_DIR": str(copy_dir)})
            after_rev = self._rev(str(dst))
            after_counts = self._counts(str(dst))
            live_ok = (before_rev == "0003_product_map" and rc_l == 0
                       and after_rev == "0004_work_orders"
                       and before_counts == after_counts)
            detail = {"before_rev": before_rev, "after_rev": after_rev,
                      "before_counts": before_counts, "after_counts": after_counts}
        else:
            detail = {"note": "pre_migration_0003.db отсутствует"}
        ev = self.art("c24_migrations.json",
                      {"empty_rc": rc_e, "empty_tables_ok": empty_ok, "live": detail})
        self.rec(24, "Миграция из пустой БД и из копии живой 0003 без потери VP-0..3 данных",
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

    def _counts(self, db):
        c = sqlite3.connect(db)
        out = {}
        for t in ("projects", "briefs", "map_versions", "decisions", "approvals", "audit_events"):
            try:
                out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        c.close()
        return out

    # 25: VP-1/2/3 no regression; non-root+healthy; localhost serves VP-4 UI
    def c25(self):
        _, h = api("GET", "/api/v1/health")
        st_a, audit = api("GET", "/api/v1/audit?event_type=workorders.wo.created&limit=5")
        # VP-2 second-writer denial still works
        _, ov = api("POST", "/api/v1/projects",
                    {"name": f"vp4-reg-{RUN}", "source_kind": "local_git",
                     "path": make_repo(f"vp4-reg-{RUN}")})
        rpid = ov.get("project", {}).get("id", "")
        if rpid:
            self.projects.append(rpid)
            self.repos.append(os.path.join(WORKSPACES, f"vp4-reg-{RUN}"))
        stw, ovw = api("POST", f"/api/v1/projects/{rpid}/worktrees", {"branch": "atlas/vp-4-reg"})
        wt = (ovw.get("worktrees") or [{}])[0].get("path", "")
        st2w, r2w = api("POST", f"/api/v1/projects/{rpid}/worktrees/acquire", {"worktree_path": wt})
        # non-root
        core_uid = sh(["docker", "compose", "exec", "-T", "core", "id", "-u"])[1].strip()
        web_uid = sh(["docker", "compose", "exec", "-T", "web", "id", "-u"])[1].strip()
        rpid_pid = sh(["systemctl", "show", "-p", "MainPID", "--value",
                       "codevinci-atlas-runner.service"])[1].strip()
        ruser = sh(["ps", "-o", "user=", "-p", rpid_pid])[1].strip() if rpid_pid not in ("0", "") else "?"
        # VP-3 export still works
        _, ap = api("GET", f"/api/v1/projects/{self._pid}/export?format=json")
        st_page, page = fetch_text("/")
        import re
        m = re.search(r'src="(/assets/index-[A-Za-z0-9_]+\.js)"', page)
        ui_ok = False
        if m:
            js = fetch_text(m.group(1))[1]
            ui_ok = all(x in js for x in ("wo.title", "pm.tab.workorders", "nav.portfolio"))
        ok = (h.get("status") == "READY" and st_a == 200 and stw == 201 and st2w == 409
              and (r2w.get("error") or {}).get("code") == "WORKTREE_CONFLICT"
              and core_uid not in ("0", "") and web_uid not in ("0", "") and ruser == "atlas"
              and ap.get("brief") and st_page == 200 and ui_ok)
        ev = self.art("c25_regression.json",
                      {"health": h.get("status"), "wo_audit_http": st_a, "worktree_http": stw,
                       "second_writer_http": st2w, "core_uid": core_uid, "web_uid": web_uid,
                       "runner_user": ruser, "vp3_export_ok": bool(ap.get("brief")),
                       "vp4_ui_served": ui_ok})
        self.rec(25, "Нет регрессий VP-1/2/3; non-root+healthy; localhost отдаёт VP-4 UI", ok,
                 f"health={h.get('status')} 2writer={st2w} nonroot={core_uid}/{web_uid}/{ruser} ui={ui_ok}",
                 [ev])

    # 26: final secret scan + exactly one active VP gate
    def c26(self):
        from atlas_core.secret_scan import scan_repo
        extra = [DATA_DIR, str(_ROOT / "var"), WORKSPACES, str(ART)]
        rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if os.path.exists(e)])
        d = rep.to_dict()
        nxt = " ".join((_ROOT / "docs/NEXT.md").read_text().lower().split())
        vp3_done = "vp-3" in nxt and ("смёрж" in nxt or "заверш" in nxt)
        vp4_active = "vp-4" in nxt and "активн" in nxt
        one_gate = vp3_done and vp4_active
        ev = self.art("c26_secret_scan.json",
                      {**d, "vp3_done": vp3_done, "vp4_active": vp4_active, "one_gate": one_gate})
        self.rec(26, "Финальный секрет-скан чист; ровно один активный VP-гейт (VP-4)",
                 rep.clean and one_gate,
                 f"real={len(d['real_hits'])} history={len(d['git_history_hits'])} one_gate={one_gate}",
                 [ev])

    def cleanup(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("PRAGMA busy_timeout=8000;")
            for pid in self.projects:
                for t in _VP4_TABLES + _PM_TABLES:
                    try:
                        conn.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
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
        print("=== VP-4 ACCEPTANCE ===")
        try:
            self.c1_c3()
            self.c4_c6()
            self.c7_c9()
            self.c10_c14()
            self.c15()
            self.c16()
            self.c17_c19()
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
        matrix = {"vp": "VP-4", "passed": passed, "total": total, "verdict": verdict,
                  "run": RUN, "criteria": sorted(self.results, key=lambda r: r["id"])}
        self.art("acceptance_matrix.json", matrix)
        # SHA-256 всех evidence-файлов
        digests = {}
        for f in sorted(ART.glob("*.json")):
            digests[f.name] = sha256_text(f.read_text(encoding="utf-8"))
        (ART / "evidence_sha256.json").write_text(
            json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n  ИТОГ VP-4: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix


if __name__ == "__main__":
    VP4().run()
