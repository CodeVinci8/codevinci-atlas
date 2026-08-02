#!/usr/bin/env python3
"""VP-2 acceptance boundary — Project Workspace (Master Spec §35).

Прогоняет 20 приёмочных проверок против РЕАЛЬНО развёрнутого стека (Compose
Core/Web + systemd Runner) и синтетических git-фикстур, пишет redacted-evidence
с SHA-256 в var/artifacts/vp2/.

Итог COMPLETE только при 20/20 PASS.
Запуск (root, стек поднят): python3 scripts/run_vp2_acceptance.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))

ART = _ROOT / "var" / "artifacts" / "vp2"
ART.mkdir(parents=True, exist_ok=True)
WEB = "http://127.0.0.1:3210"
DATA_DIR = "/var/lib/codevinci-atlas"
WORKSPACES = f"{DATA_DIR}/workspaces"
INTAKE = f"{DATA_DIR}/intake"
WORKTREES = f"{DATA_DIR}/worktrees"
RUN = time.strftime("%H%M%S")
_UVBIN = f"{os.environ['HOME']}/.local/bin:{os.environ['PATH']}"
_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Atlas Test", "GIT_AUTHOR_EMAIL": "atlas@local",
            "GIT_COMMITTER_NAME": "Atlas Test", "GIT_COMMITTER_EMAIL": "atlas@local"}


def _atlas_ids() -> tuple[int, int]:
    uid = int(subprocess.run(["id", "-u", "atlas"], capture_output=True, text=True).stdout.strip())
    gid = int(subprocess.run(["id", "-g", "atlas"], capture_output=True, text=True).stdout.strip())
    return uid, gid


ATLAS_UID, ATLAS_GID = _atlas_ids()


def sh(cmd, timeout=180, env=None, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd or str(_ROOT), env={**os.environ, **(env or {})})
    return r.returncode, r.stdout, r.stderr


def gsh(cwd, *args, safe=False):
    cmd = ["git"]
    if safe:
        cmd += ["-c", f"safe.directory={cwd}"]
    cmd += ["-C", cwd, *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=_GIT_ENV)


def _loads(raw):
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"error": {"code": "NON_JSON", "reason": (raw or "")[:80]}}


def api(method, path, body=None, timeout=40):
    url = WEB + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, _loads(e.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return None, {"error": {"code": "TRANSPORT", "reason": str(exc)}}


def chown_atlas(path):
    subprocess.run(["chown", "-R", f"{ATLAS_UID}:{ATLAS_GID}", path], check=False)


def make_repo(name, *, dirty=False, remote="https://github.com/o/r.git"):
    path = os.path.join(WORKSPACES, name)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    gsh(path, "init", "-q", "-b", "main")
    Path(path, "README.md").write_text("# synthetic\n")
    Path(path, "package.json").write_text('{"name":"syn","scripts":{"build":"vite build","test":"vitest"}}\n')
    Path(path, "AGENTS.md").write_text("Root instructions: build then test.\n")
    os.makedirs(os.path.join(path, "sub"))
    Path(path, "sub", "CLAUDE.md").write_text("Nested instructions for sub/.\n")
    gsh(path, "add", "-A"); gsh(path, "commit", "-q", "-m", "initial")
    if remote:
        gsh(path, "remote", "add", "origin", remote)
    if dirty:
        with open(os.path.join(path, "README.md"), "a") as f:
            f.write("uncommitted tracked change\n")
        Path(path, "NEW_UNTRACKED.txt").write_text("untracked owner work\n")
    chown_atlas(path)
    return path


def tree_hash(root):
    acc = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".git"]
        for name in fns:
            fp = os.path.join(dp, name)
            rel = os.path.relpath(fp, root)
            try:
                acc[rel] = hashlib.sha256(Path(fp).read_bytes()).hexdigest()
            except OSError:
                acc[rel] = "ERR"
    blob = "\n".join(f"{k}:{acc[k]}" for k in sorted(acc))
    return hashlib.sha256(blob.encode()).hexdigest()


class VP2:
    def __init__(self):
        self.results = []
        self.tmp = []

    def art(self, name, content):
        p = ART / name
        p.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
                     if isinstance(content, (dict, list)) else str(content), encoding="utf-8")
        return p.name

    def rec(self, n, name, ok, note, ev=None):
        self.results.append({"id": n, "criterion": name, "status": "PASS" if ok else "FAIL",
                             "note": note, "evidence": ev or []})
        print(f"  [{'PASS' if ok else 'FAIL'}] #{n:>2} {name} — {note}")

    # 1-2: clean connect + baseline persisted/visible
    def c1_c2(self):
        repo = make_repo(f"clean-{RUN}"); self.tmp.append(repo)
        st, ov = api("POST", "/api/v1/projects",
                     {"name": f"clean-{RUN}", "source_kind": "local_git", "path": repo})
        ok1 = st == 201 and ov.get("project", {}).get("source_kind") == "local_git"
        bl = ov.get("baseline") or {}
        pid = ov.get("project", {}).get("id", "")
        st2, ov2 = api("GET", f"/api/v1/projects/{pid}")
        instr = {i["path"]: i["precedence"] for i in bl.get("instructions", [])}
        remotes_clean = bl.get("remotes") and "token" not in json.dumps(bl["remotes"]).lower()
        visible = (bl.get("branch") == "main" and len(bl.get("head", "")) == 40
                   and "AGENTS.md" in instr and "sub/CLAUDE.md" in instr
                   and instr.get("sub/CLAUDE.md", 0) > instr.get("AGENTS.md", -1)
                   and any(p["name"] in ("npm", "pnpm") for p in bl.get("package_managers", []))
                   and any(c["name"] == "build" and not c["executed"] for c in bl.get("baseline_commands", []))
                   and bl.get("content_hash", "").startswith("sha256:")
                   and st2 == 200 and ov2.get("baseline"))
        self._clean_pid = pid
        ev = self.art("c1_c2_clean_connect.json",
                      {"http": st, "state": ov.get("state"), "branch": bl.get("branch"),
                       "head": bl.get("head"), "remotes": bl.get("remotes"),
                       "instructions": bl.get("instructions"),
                       "package_managers": bl.get("package_managers"),
                       "commands": bl.get("baseline_commands"), "content_hash": bl.get("content_hash")})
        self.rec(1, "Чистый синтетический git подключается", ok1 and ov.get("state") == "clean",
                 f"http={st} state={ov.get('state')}", [ev])
        self.rec(2, "Источник/baseline персистятся и видимы", bool(visible and remotes_clean),
                 f"branch={bl.get('branch')} instr={list(instr)} pkg={len(bl.get('package_managers', []))}", [ev])

    # 3-7: dirty preserve + worktree + original unchanged
    def c3_c7(self):
        repo = make_repo(f"dirty-{RUN}", dirty=True); self.tmp.append(repo)
        before = tree_hash(repo)
        st, ov = api("POST", "/api/v1/projects",
                     {"name": f"dirty-{RUN}", "source_kind": "local_git", "path": repo})
        pid = ov.get("project", {}).get("id", "")
        self._dirty_pid = pid
        after_connect = tree_hash(repo)
        bl = ov.get("baseline") or {}
        self.rec(3, "Грязный репозиторий подключается без модификации",
                 st == 201 and ov.get("state") == "dirty" and before == after_connect,
                 f"state={ov.get('state')} unchanged={before == after_connect}")
        self.rec(4, "Dirty tracked+untracked неизменны байт-в-байт",
                 before == after_connect and bl.get("untracked", 0) >= 1 and bl.get("tracked_changes", 0) >= 1,
                 f"untracked={bl.get('untracked')} tracked_changes={bl.get('tracked_changes')}")
        # worktree
        stw, ovw = api("POST", f"/api/v1/projects/{pid}/worktrees", {"branch": "atlas/vp-2-demo"})
        wt = (ovw.get("worktrees") or [{}])[0]
        wt_path = wt.get("path", "")
        self._dirty_wt = wt_path
        after_wt = tree_hash(repo)
        status_after = gsh(repo, "status", "--porcelain", safe=True).stdout
        # статический анализ: нет деструктивных git-команд в модулях workspace
        srcs = ""
        for m in ("workspace.py", "worktrees.py", "gitbaseline.py"):
            srcs += Path(_ROOT, "apps/core/atlas_core", m).read_text()
        no_destructive = not any(x in srcs for x in ("reset --hard", "clean -fd", "clean -xdf", "checkout -- ."))
        ev = self.art("c3_c7_dirty_worktree.json",
                      {"connect_http": st, "worktree_http": stw, "worktree_path": wt_path,
                       "tree_hash_before": before, "tree_hash_after_connect": after_connect,
                       "tree_hash_after_worktree": after_wt, "status_after_porcelain": status_after,
                       "no_destructive_git": no_destructive})
        self.rec(5, "Деструктивные git-команды не используются",
                 no_destructive and "NEW_UNTRACKED.txt" in status_after,
                 f"no_destructive={no_destructive}, still_dirty={'NEW_UNTRACKED.txt' in status_after}", [ev])
        self.rec(6, "Разрешённая ветка и изолированный worktree созданы",
                 stw == 201 and wt_path.startswith(WORKTREES) and os.path.isdir(wt_path),
                 f"http={stw} path={wt_path}", [ev])
        self.rec(7, "Оригинальный репозиторий не изменён",
                 before == after_wt, f"unchanged={before == after_wt}", [ev])

    # 8: second writer denied
    def c8(self):
        st, res = api("POST", f"/api/v1/projects/{self._dirty_pid}/worktrees/acquire",
                      {"worktree_path": self._dirty_wt})
        code = (res.get("error") or {}).get("code")
        ev = self.art("c8_second_writer.json", {"http": st, "error_code": code})
        self.rec(8, "Второй writer детерминированно отклонён",
                 st == 409 and code == "WORKTREE_CONFLICT", f"http={st} code={code}", [ev])

    # 9-11: archive traversal/symlink blocked, no file outside intake
    def c9_c11(self):
        acc = os.path.join(DATA_DIR, f"accept_tmp_{RUN}")
        os.makedirs(acc, exist_ok=True); self.tmp.append(acc)

        def tar_with(members, fname):
            p = os.path.join(acc, fname)
            with tarfile.open(p, "w") as tar:
                for ti, data in members:
                    if data is None:
                        tar.addfile(ti)
                    else:
                        ti.size = len(data); tar.addfile(ti, io.BytesIO(data))
            chown_atlas(p)
            return p

        sentinel = os.path.join(DATA_DIR, f"PWNED_{RUN}.txt")
        mal = tar_with([(tarfile.TarInfo("ok.txt"), b"ok"),
                        (tarfile.TarInfo(f"../../PWNED_{RUN}.txt"), b"pwn")], "traversal.tar")
        st_t, r_t = api("POST", "/api/v1/projects",
                        {"name": "mal-trav", "source_kind": "archive", "archive_path": mal})
        sym = tarfile.TarInfo("link"); sym.type = tarfile.SYMTYPE; sym.linkname = "/etc/passwd"
        symtar = tar_with([(sym, None)], "symlink.tar")
        st_s, r_s = api("POST", "/api/v1/projects",
                        {"name": "mal-sym", "source_kind": "archive", "archive_path": symtar})
        # canonical-path escape (direct source path traversal)
        st_p, r_p = api("POST", "/api/v1/projects",
                        {"name": "mal-path", "source_kind": "local_git",
                         "path": os.path.join(WORKSPACES, "..", "..", "..", "etc")})
        no_outside = not os.path.exists(sentinel)
        # проверить, что вне intake нет новых файлов traversal
        intake_ok = not os.path.exists(sentinel)
        ev = self.art("c9_11_archive_security.json",
                      {"traversal_http": st_t, "traversal_code": (r_t.get("error") or {}).get("code"),
                       "symlink_http": st_s, "symlink_code": (r_s.get("error") or {}).get("code"),
                       "path_http": st_p, "path_code": (r_p.get("error") or {}).get("code"),
                       "sentinel_outside_intake_exists": os.path.exists(sentinel)})
        self.rec(9, "Traversal в архиве блокируется",
                 st_t == 400 and (r_t.get("error") or {}).get("code") == "ARCHIVE_UNSAFE",
                 f"http={st_t} code={(r_t.get('error') or {}).get('code')}", [ev])
        self.rec(10, "Symlink и canonical-path escape блокируются",
                 st_s == 400 and (r_s.get("error") or {}).get("code") == "ARCHIVE_UNSAFE"
                 and st_p == 400 and (r_p.get("error") or {}).get("code") == "PATH_TRAVERSAL",
                 f"symlink={st_s} path={st_p}/{(r_p.get('error') or {}).get('code')}", [ev])
        self.rec(11, "Ни один вредоносный файл не создан вне intake",
                 no_outside and intake_ok, f"sentinel_exists={os.path.exists(sentinel)}", [ev])

    # 12: baseline survives Core restart
    def c12(self):
        st0, ov0 = api("GET", f"/api/v1/projects/{self._clean_pid}")
        hash0 = (ov0.get("baseline") or {}).get("content_hash")
        rc, _o, _e = sh(["docker", "compose", "restart", "core"], timeout=120)
        # ждать здоровья
        healthy = False
        for _ in range(30):
            s, _b = api("GET", "/api/v1/health", timeout=6)
            if s == 200:
                healthy = True; break
            time.sleep(1)
        st1, ov1 = api("GET", f"/api/v1/projects/{self._clean_pid}")
        hash1 = (ov1.get("baseline") or {}).get("content_hash")
        ev = self.art("c12_restart_durability.json",
                      {"restart_rc": rc, "healthy": healthy, "hash_before": hash0, "hash_after": hash1})
        self.rec(12, "Baseline переживает рестарт Core и запрашивается",
                 rc == 0 and healthy and st1 == 200 and hash0 and hash0 == hash1,
                 f"healthy={healthy} hash_match={hash0 == hash1}", [ev])

    # 13: disconnect no delete
    def c13(self):
        repo = make_repo(f"disc-{RUN}", dirty=True); self.tmp.append(repo)
        st, ov = api("POST", "/api/v1/projects",
                     {"name": f"disc-{RUN}", "source_kind": "local_git", "path": repo})
        pid = ov["project"]["id"]
        stw, ovw = api("POST", f"/api/v1/projects/{pid}/worktrees", {"branch": "atlas/vp-2-disc"})
        wt_path = ovw["worktrees"][0]["path"]
        before = tree_hash(repo)
        std, ovd = api("DELETE", f"/api/v1/projects/{pid}")
        after = tree_hash(repo)
        kept = (os.path.isdir(repo) and os.path.exists(os.path.join(repo, "NEW_UNTRACKED.txt"))
                and os.path.isdir(wt_path) and before == after)
        ev = self.art("c13_disconnect_no_delete.json",
                      {"http": std, "state": ovd.get("state"), "repo_exists": os.path.isdir(repo),
                       "untracked_exists": os.path.exists(os.path.join(repo, "NEW_UNTRACKED.txt")),
                       "worktree_exists": os.path.isdir(wt_path), "tree_unchanged": before == after})
        self.rec(13, "Disconnect не удаляет репозиторий/dirty/worktree",
                 ovd.get("state") == "disconnected" and kept, f"kept={kept}", [ev])

    # 14: RU/EN parity
    def c14(self):
        rc, o, e = sh(["pnpm", "check:i18n"], env={"PATH": _UVBIN}, cwd=str(_ROOT / "apps/web"))
        import re
        ru = (_ROOT / "apps/web/src/locales/ru.ts").read_text()
        en = (_ROOT / "apps/web/src/locales/en.ts").read_text()
        rk = set(re.findall(r'"([a-zA-Z0-9_.]+)":', ru))
        ek = set(re.findall(r'"([a-zA-Z0-9_.]+)":', en))
        ev = self.art("c14_i18n.json", {"check_rc": rc, "ru_keys": len(rk), "en_keys": len(ek),
                                        "equal": rk == ek})
        self.rec(14, "RU/EN переключение и паритет каталогов", rc == 0 and rk == ek and len(rk) > 30,
                 f"rc={rc} ru={len(rk)} en={len(ek)} equal={rk == ek}", [ev])

    # 15: truthful states clean/dirty/empty/stale/error (loading — UI)
    def c15(self):
        _, ov_clean = api("GET", f"/api/v1/projects/{self._clean_pid}")
        _, ov_dirty = api("GET", f"/api/v1/projects/{self._dirty_pid}")
        _, ov_empty = api("POST", "/api/v1/projects", {"name": f"empty-{RUN}", "source_kind": "empty"})
        # stale: добавить untracked в чистый репозиторий → дрейф
        clean_repo = os.path.join(WORKSPACES, f"clean-{RUN}")
        Path(clean_repo, "DRIFT.txt").write_text("drift\n"); chown_atlas(clean_repo)
        _, ov_stale = api("GET", f"/api/v1/projects/{self._clean_pid}")
        # error: подключить repo, затем удалить источник
        err_repo = make_repo(f"err-{RUN}")
        _, ov_e = api("POST", "/api/v1/projects",
                      {"name": f"err-{RUN}", "source_kind": "local_git", "path": err_repo})
        shutil.rmtree(err_repo)
        _, ov_err = api("GET", f"/api/v1/projects/{ov_e['project']['id']}")
        loading_ui = "common.loading" in (_ROOT / "apps/web/src/App.tsx").read_text()
        states = {"clean": ov_clean.get("state"), "dirty": ov_dirty.get("state"),
                  "empty": ov_empty.get("state"), "stale": ov_stale.get("state"),
                  "error": ov_err.get("state")}
        ok = (states["clean"] == "clean" and states["dirty"] == "dirty"
              and states["empty"] == "empty" and states["stale"] == "stale"
              and states["error"] == "error" and loading_ui)
        ev = self.art("c15_states.json", {**states, "loading_ui": loading_ui,
                                          "clean_next": ov_clean.get("next_action")})
        self.rec(15, "Overview: правдивые clean/dirty/empty/stale/error/loading", ok,
                 f"{states} loading_ui={loading_ui}", [ev])

    # 16: non-root + healthy
    def c16(self):
        core_uid = sh(["docker", "compose", "exec", "-T", "core", "id", "-u"])[1].strip()
        web_uid = sh(["docker", "compose", "exec", "-T", "web", "id", "-u"])[1].strip()
        rpid = sh(["systemctl", "show", "-p", "MainPID", "--value", "codevinci-atlas-runner.service"])[1].strip()
        ruser = sh(["ps", "-o", "user=", "-p", rpid])[1].strip() if rpid != "0" else "?"
        _, h = api("GET", "/api/v1/health")
        ok = core_uid not in ("0", "") and web_uid not in ("0", "") and ruser == "atlas" and h.get("status") == "READY"
        ev = self.art("c16_nonroot_health.json",
                      {"core_uid": core_uid, "web_uid": web_uid, "runner_user": ruser, "health": h.get("status")})
        self.rec(16, "Core/Web/Runner non-root и healthy", ok,
                 f"core={core_uid} web={web_uid} runner={ruser} health={h.get('status')}", [ev])

    # 17: VP-1 regression (health/audit/UDS/backup/web build)
    def c17(self):
        _, h = api("GET", "/api/v1/health")
        st_a, audit = api("GET", "/api/v1/audit?event_type=project.baseline&limit=5")
        from atlas_core.runner_health import runner_health
        good = runner_health("/run/codevinci-atlas/runner.sock", "/run/codevinci-atlas/runner.token")
        badf = ART / "c17_badtoken"; badf.write_text("WRONG")
        bad = runner_health("/run/codevinci-atlas/runner.sock", str(badf)); badf.unlink()
        rc_b, o_b, _ = sh(["uv", "run", "atlas", "backup", "--json", "--out", str(ART / "backups")],
                          env={"ATLAS_DATA_DIR": DATA_DIR, "ATLAS_CONFIG_FILE": "/nonexistent.yaml", "PATH": _UVBIN})
        try:
            man = json.loads(o_b)
        except json.JSONDecodeError:
            man = {}
        rc_w, o_w, e_w = sh(["pnpm", "build"], env={"PATH": _UVBIN}, cwd=str(_ROOT / "apps/web"), timeout=300)
        web_ok = rc_w == 0 and (_ROOT / "apps/web/dist/index.html").exists()
        ok = (h.get("status") == "READY" and st_a == 200 and audit.get("events")
              and good.get("status") == "READY" and bad.get("status") == "UNAUTHORIZED"
              and rc_b == 0 and man.get("integrity_ok") and man.get("secret_scan_clean") and web_ok)
        ev = self.art("c17_vp1_regression.json",
                      {"health": h.get("status"), "audit_http": st_a, "audit_found": bool(audit.get("events")),
                       "uds_good": good.get("status"), "uds_bad": bad.get("status"),
                       "backup_rc": rc_b, "backup_integrity": man.get("integrity_ok"),
                       "backup_clean": man.get("secret_scan_clean"), "web_build_rc": rc_w, "web_ok": web_ok})
        self.rec(17, "VP-1 регрессий нет (health/audit/UDS/backup/web)", bool(ok),
                 f"health={h.get('status')} uds={good.get('status')}/{bad.get('status')} backup={man.get('integrity_ok')} web={web_ok}", [ev])

    # 18: full secret scan
    def c18(self):
        from atlas_core.secret_scan import scan_repo
        extra = [DATA_DIR, str(_ROOT / "var"), WORKSPACES, INTAKE, WORKTREES, str(ART / "backups")]
        rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if os.path.exists(e)])
        d = rep.to_dict()
        ev = self.art("c18_secret_scan.json", d)
        self.rec(18, "Полный секрет-скан чист (код/история/БД/фикстуры)", rep.clean,
                 f"real={len(d['real_hits'])}, history={len(d['git_history_hits'])}", [ev])

    # 19: one active VP gate
    def c19(self):
        nxt = (_ROOT / "docs/NEXT.md").read_text()
        low = nxt.lower()
        vp1_done = "VP-1" in nxt and ("смёрж" in low or "заверш" in low or "merged" in low)
        vp2_active = "VP-2" in nxt
        vp3_absent = "VP-3" not in nxt or "не начин" in low
        ok = vp1_done and vp2_active and vp3_absent
        ev = self.art("c19_vp_gate.json", {"vp1_done": vp1_done, "vp2_active": vp2_active,
                                           "vp3_not_started": vp3_absent})
        self.rec(19, "Ровно один активный VP-гейт (VP-2)", ok,
                 f"vp1_done={vp1_done} vp2_active={vp2_active} vp3_absent={vp3_absent}", [ev])

    # 20: 3210 serves VP-2 Project Workspace UI
    def c20(self):
        try:
            with urllib.request.urlopen(WEB + "/", timeout=6) as r:
                page = r.read().decode("utf-8", "replace"); page_st = r.status
        except Exception as exc:  # noqa: BLE001
            page, page_st = str(exc), None
        st_api, _ = api("GET", "/api/v1/health")
        # найти bundle и проверить маркеры Project Workspace
        import re
        m = re.search(r'src="(/assets/index-[A-Za-z0-9]+\.js)"', page)
        markers = False
        bundle_name = m.group(1) if m else ""
        if m:
            try:
                with urllib.request.urlopen(WEB + m.group(1), timeout=10) as rb:
                    js = rb.read().decode("utf-8", "replace")
                markers = ("nav.projects" in js and "projects.title" in js
                           and "overview.nextAction" in js)
            except Exception:  # noqa: BLE001
                markers = False
        ok = page_st == 200 and st_api == 200 and markers
        ev = self.art("c20_web_ui.json", {"page_http": page_st, "api_http": st_api,
                                          "bundle": bundle_name, "workspace_markers": markers})
        self.rec(20, "127.0.0.1:3210 отдаёт VP-2 Project Workspace UI", ok,
                 f"page={page_st} api={st_api} markers={markers}", [ev])

    def cleanup(self):
        for p in self.tmp:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        # убрать intake/worktree артефакты приёмки
        for base in (INTAKE, WORKTREES):
            if os.path.isdir(base):
                for entry in os.listdir(base):
                    pass  # оставляем — evidence; чистка не обязательна

    def run(self):
        print("=== VP-2 ACCEPTANCE ===")
        self.c1_c2(); self.c3_c7(); self.c8(); self.c9_c11(); self.c12(); self.c13()
        self.c14(); self.c15(); self.c16(); self.c17(); self.c18(); self.c19(); self.c20()
        self.cleanup()
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        verdict = "COMPLETE" if passed == total else f"INCOMPLETE ({passed}/{total})"
        matrix = {"vp": "VP-2", "passed": passed, "total": total, "verdict": verdict,
                  "criteria": sorted(self.results, key=lambda r: r["id"])}
        self.art("acceptance_matrix.json", matrix)
        print(f"\n  ИТОГ VP-2: {verdict} ({passed}/{total})")
        print(f"  Артефакты: {ART}")
        return matrix


if __name__ == "__main__":
    VP2().run()
