#!/usr/bin/env python3
"""VP-5 РЕАЛЬНЫЙ provider-E2E — Codex Planner → Claude Builder → независимый
Codex Reviewer через настоящий Atlas Agent Pipeline (Master Spec §17, §38).

Запускает НАСТОЯЩИЕ подписочные CLI через нативные адаптеры Atlas (runuser +
изолированный env под идентичностью профиля) на маленьком СИНТЕТИЧЕСКОМ git-репо.
Оркестрация — durable RunService + аренды (один writer). Жёсткий потолок: не
более 6 подписочных вызовов. В durable/evidence НЕ попадают transcript,
credentials, email, cookie, raw auth path, полный provider payload.

Доказывает: 1 durable Run; bounded Planner-пакет; Builder — единственный writer;
реальный артефакт в репо; нормализованные события + provider session ref без
секретов; checkpoint/handoff при необходимости; release-before-acquire;
независимый read-only Reviewer оценивает реальный diff; ложный success ≠ PASS;
не более одного fix-loop.

Запуск (root; профили READY):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp5_real_e2e.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))

ART = _ROOT / "var" / "artifacts" / "vp5" / "real_e2e"
ART.mkdir(parents=True, exist_ok=True)
REGISTRY = "/var/lib/codevinci-atlas/profiles/registry.json"
MAX_CALLS = 6
TS = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_text(t: str) -> str:
    return "sha256:" + hashlib.sha256(t.encode()).hexdigest()


class Budget:
    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0
        self.log = []

    def spend(self, who: str):
        self.used += 1
        self.log.append(who)
        if self.used > self.cap:
            raise RuntimeError(f"превышен потолок подписочных вызовов ({self.cap})")
        print(f"  [call {self.used}/{self.cap}] {who}")


class RealE2E:
    def __init__(self):
        from atlas_core.adapters.real_claude import RealClaudeAdapter
        from atlas_core.adapters.real_codex import RealCodexAdapter
        self.reg = json.load(open(REGISTRY))["profiles"]
        self.cx = RealCodexAdapter()
        self.cl = RealClaudeAdapter()
        self.budget = Budget(MAX_CALLS)
        self.evidence = {"ts": TS, "max_calls": MAX_CALLS, "steps": []}
        # изолированный синтетический репозиторий (без пользовательских/прод-данных)
        self.repo = Path(f"/tmp/atlas-vp5-e2e-{TS}")

    # --- профиль helpers ---------------------------------------------------
    def _p(self, alias):
        p = self.reg[alias]
        return p["root_path"], p["runtime_user"], p["executable_path"], p["provider"]

    def _adapter(self, provider):
        return self.cx if provider == "codex" else self.cl

    def _make_repo(self):
        if self.repo.exists():
            subprocess.run(["rm", "-rf", str(self.repo)])
        self.repo.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Atlas E2E", "GIT_AUTHOR_EMAIL": "e2e@local",
               "GIT_COMMITTER_NAME": "Atlas E2E", "GIT_COMMITTER_EMAIL": "e2e@local"}
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"], env=env)
        (self.repo / "README.md").write_text("# synthetic e2e repo\nЗадача: добавить чистую функцию add.\n")
        (self.repo / "calc.py").write_text("# TODO: реализовать add(a, b)\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], env=env)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "scaffold"], env=env)
        # читаемо профильными идентичностями (синтетика, без секретов)
        subprocess.run(["chmod", "-R", "a+rX", str(self.repo)])
        self._genv = env
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def _job(self, role, provider, goal, *, cwd=None):
        from atlas_core.contracts import JobPackage, Provider, Role
        return JobPackage(goal=goal, role=Role(role), provider=Provider(provider),
                          inputs={"cwd": cwd or str(self.repo), "timeout_s": 240})

    # --- прогоны роли (реальные вызовы) -----------------------------------
    def planner(self, run, runs, alias):
        root, user, exe, prov = self._p(alias)
        goal = ("Ты Planner. Составь МИНИМАЛЬНЫЙ план реализации чистой функции add(a, b) "
                "в файле calc.py на Python (возвращает a+b). Ответь СТРОГО одним JSON-объектом "
                "без пояснений: {\"steps\": [строки], \"files\": [\"calc.py\"], "
                "\"acceptance\": [строки]}.")
        self.budget.spend(f"codex Planner ({alias})")
        res = self.cx.start(self._job("planner", "codex", goal), profile_alias=alias,
                            root_path=root, executable=exe, run_as_user=user)
        out = res.result.structured_output or {}
        runs.record_provider_session(run, provider="codex", session_id=res.result.session_id or "",
                                     role="planner", profile_id=alias)
        runs.append_event(run, "planner.package", {"files": out.get("files"), "steps_count": len(out.get("steps", []))})
        bounded = bool(out.get("files")) and bool(out.get("steps") or out.get("acceptance"))
        self.evidence["steps"].append({"role": "planner", "profile": alias,
                                       "session": res.result.session_id, "bounded_package": bounded,
                                       "files": out.get("files")})
        return out, bounded

    def _builder_once(self, run, runs, alias, worktree):
        """Одна попытка Builder на профиле alias. Один writer (worktree+profile
        аренда), release-before-acquire в finally. Возвращает результат или auth-ошибку."""
        from atlas_core.errors import AtlasError, ErrorCode
        from atlas_core.run_leases import RunLeaseService
        from atlas_core.wsleases import WorktreeLeaseService
        root, user, exe, prov = self._p(alias)
        wls = WorktreeLeaseService(self.db_path)
        pls = RunLeaseService(self.db_path)
        max_writers = 0
        wlease = please = None
        try:
            wlease = wls.acquire(project_id="proj_e2e", worktree=worktree, role="builder", holder=alias)
            please = pls.acquire(profile_id=alias, run_id=run, role="builder", worktree=worktree, holder=alias)
            max_writers = wls.active_count(worktree)
            goal = ("Ты Builder. Реализуй чистую функцию add(a, b) в calc.py (Python), "
                    "возвращающую a+b. Ответь СТРОГО одним JSON-объектом без пояснений: "
                    "{\"path\": \"calc.py\", \"content\": \"<полное содержимое файла>\"}.")
            self.budget.spend(f"claude Builder ({alias})")
            try:
                res = self.cl.start(self._job("builder", "claude", goal), profile_alias=alias,
                                    root_path=root, executable=exe, run_as_user=user)
            except AtlasError as exc:
                code = exc.classified.code
                runs.record_retry(run, role="builder", attempt=1, error_class=code.value)
                runs.append_event(run, "builder.error", {"profile": alias, "error_class": code.value})
                self.evidence["steps"].append({"role": "builder", "profile": alias,
                                               "error_class": code.value, "max_concurrent_writers": max_writers})
                return {"wrote": False, "auth_error": code in (ErrorCode.AUTH_REQUIRED, ErrorCode.AUTH_EXPIRED),
                        "error_class": code.value, "max_writers": max_writers, "session": ""}
            out = res.result.structured_output or {}
            runs.record_provider_session(run, provider="claude", session_id=res.result.session_id or "",
                                         role="builder", profile_id=alias)
            path = out.get("path", "calc.py")
            content = out.get("content", "")
            wrote = False
            if content and "def add" in content:
                (self.repo / path).write_text(content)
                subprocess.run(["git", "-C", str(self.repo), "add", "-A"], env=self._genv)
                subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                                f"builder: {alias} реализовал add"], env=self._genv)
                subprocess.run(["chmod", "-R", "a+rX", str(self.repo)])
                wrote = True
            art_sha = sha256_file(self.repo / path) if wrote else ""
            runs.append_event(run, "builder.artifact", {"path": path, "sha": art_sha, "wrote": wrote})
            self.evidence["steps"].append({"role": "builder", "profile": alias,
                                           "session": res.result.session_id, "artifact_sha": art_sha,
                                           "wrote": wrote, "max_concurrent_writers": max_writers})
            return {"wrote": wrote, "sha": art_sha, "path": path, "max_writers": max_writers,
                    "session": res.result.session_id, "auth_error": False}
        finally:
            if please is not None:
                try:
                    pls.release(please.id)
                except Exception:  # noqa: BLE001
                    pass
            if wlease is not None:
                try:
                    wls.release(wlease.id)
                except Exception:  # noqa: BLE001
                    pass
            wls.close(); pls.close()

    def builder(self, run, runs, aliases, worktree):
        """Пробуем builder-профили по очереди; auth-ошибка → следующий профиль
        (release-before-acquire гарантируется). Если все с auth-ошибкой — blocker."""
        last = None
        for alias in aliases:
            if self.budget.used >= MAX_CALLS:
                break
            last = self._builder_once(run, runs, alias, worktree)
            last["profile"] = alias
            if last["wrote"] or not last.get("auth_error"):
                return last
        return last or {"wrote": False, "auth_error": True, "profile": aliases[0], "max_writers": 0, "session": ""}

    def reviewer(self, run, runs, alias, builder_profile, builder_session):
        root, user, exe, prov = self._p(alias)
        # независимость: другой профиль, другая сессия, БЕЗ writer-аренды
        independent = alias != builder_profile
        goal = ("Ты независимый Reviewer (read-only). Прочитай calc.py в текущем каталоге. "
                "Определи, объявлена ли чистая функция add(a, b), возвращающая a+b, без побочных "
                "эффектов. Ответь СТРОГО одним JSON-объектом без пояснений: "
                "{\"verdict\": \"PASS\"|\"REVISE\", \"findings\": [строки]}.")
        self.budget.spend(f"codex Reviewer ({alias})")
        res = self.cx.start(self._job("reviewer", "codex", goal), profile_alias=alias,
                            root_path=root, executable=exe, run_as_user=user)
        out = res.result.structured_output or {}
        runs.record_provider_session(run, provider="codex", session_id=res.result.session_id or "",
                                     role="reviewer", profile_id=alias)
        verdict = str(out.get("verdict", "")).upper()
        if verdict not in ("PASS", "REVISE"):
            verdict = "REVISE"  # невалидный вывод трактуем консервативно
        runs.append_event(run, "reviewer.verdict", {"verdict": verdict,
                          "findings_count": len(out.get("findings", [])), "independent": independent})
        self.evidence["steps"].append({"role": "reviewer", "profile": alias, "session": res.result.session_id,
                                       "verdict": verdict, "independent": independent,
                                       "different_session_than_builder": res.result.session_id != builder_session})
        return verdict, independent

    def run(self):
        print(f"=== VP-5 REAL PROVIDER E2E (потолок {MAX_CALLS} вызовов) ===")
        # проверка READY (read-only, без затрат)
        for alias in ("codex-plus-01", "claude-pro-01", "codex-plus-02"):
            root, user, exe, prov = self._p(alias)
            st = self._adapter(prov).auth_status(root, executable=exe, run_as_user=user)
            print(f"  precheck {alias}: authed={st.get('authenticated')} state={st.get('state')}")
            if not st.get("authenticated"):
                print(f"  BLOCKER: профиль {alias} не READY — требуется owner login.")
                return {"ok": False, "blocker": f"{alias} not authenticated"}

        os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
        # Изолированная БД, мигрированная реальным alembic до 0005 (не трогаем живую
        # 0004 до Phase 5). Профили/адаптеры/провайдеры — настоящие.
        db_dir = self.repo.parent / f"e2e-db-{TS}"
        db_dir.mkdir(parents=True, exist_ok=True)
        os.environ["ATLAS_DATA_DIR"] = str(db_dir)
        venv = _ROOT / ".venv" / "bin"
        alembic = str(venv / "alembic") if (venv / "alembic").exists() else "alembic"
        mig = subprocess.run([alembic, "upgrade", "head"], cwd=str(_ROOT), capture_output=True, text=True,
                             env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH','')}",
                                  "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})
        if mig.returncode != 0:
            print("  BLOCKER: миграция изолированной БД до 0005 не удалась")
            return {"ok": False, "blocker": "isolated migration failed"}
        from atlas_core.db import init_engine, session_scope
        from atlas_core.orm import Project
        from atlas_core.runs import RunService
        from atlas_core.settings import load_settings
        settings = load_settings()
        self.db_path = settings.db_path
        init_engine(settings.db_url, settings.db_path)
        runs = RunService()
        head = self._make_repo()
        worktree = str(self.repo)
        with session_scope() as s:
            if s.get(Project, "proj_e2e") is None:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                s.add(Project(id="proj_e2e", name="VP-5 real E2E", source_kind="local_git",
                              source_location=worktree, status="connected", created_at=now, updated_at=now))
                s.commit()

        # 1 durable Run (идемпотентный)
        run = runs.create_run("proj_e2e", work_order_id="wo-e2e", vp_key="VP-5",
                              dedup_key=f"e2e-{TS}")["id"]
        dup = runs.create_run("proj_e2e", work_order_id="wo-e2e", vp_key="VP-5", dedup_key=f"e2e-{TS}")["id"]
        one_run = run == dup
        runs.transition(run, "PREPARING", expected_version=runs.get_run(run)["version"], reason="e2e prepare")
        runs.transition(run, "RUNNING", expected_version=runs.get_run(run)["version"], reason="planner")

        # Planner (codex-plus-01)
        plan, bounded = self.planner(run, runs, "codex-plus-01")
        # Builder — единственный writer; пробуем оба Claude-профиля при auth-ошибке.
        b = self.builder(run, runs, ["claude-pro-01", "claude-pro-02"], worktree)
        if not b["wrote"] and b.get("auth_error"):
            # Genuine hard blocker: OAuth-токен Claude истёк на всех профилях (401
            # OAuth expired), несмотря на loggedIn=true. Требуется owner re-login —
            # ВНЕ авторизации. Честно фиксируем, PASS не фабрикуем.
            runs.transition(run, "AUTH_REQUIRED", expected_version=runs.get_run(run)["version"],
                            reason="claude OAuth expired на всех builder-профилях",
                            blocker="CLAUDE_AUTH_EXPIRED",
                            next_action="owner: re-login Claude профилей (claude auth login / setup-token)")
            self.evidence.update({
                "run_id": run, "one_durable_run": one_run, "bounded_planner_package": bounded,
                "builder_wrote_artifact": False, "blocker": "CLAUDE_AUTH_EXPIRED_ALL_PROFILES",
                "blocker_detail": "claude auth status --json сообщает loggedIn=true, но API возвращает "
                                  "401 OAuth access token has expired; требуется owner re-login",
                "provider_calls_used": self.budget.used, "call_log": self.budget.log,
                "codex_planner_worked": bounded, "events": [e["type"] for e in runs.events(run)],
            })
            (ART / f"real_e2e_{TS}.json").write_text(
                json.dumps(self.evidence, ensure_ascii=False, indent=2, sort_keys=True))
            digests = {p.name: sha256_file(p) for p in sorted(ART.glob("*"))}
            (ART / "manifest_sha256.json").write_text(
                json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True))
            print(f"\n  BLOCKER: Claude OAuth истёк на всех builder-профилях (401). "
                  f"Codex Planner отработал реально. calls={self.budget.used}/{MAX_CALLS}.")
            print(f"  Требуемое действие владельца: re-login Claude профилей. Evidence: {ART}")
            return {"ok": False, "blocker": "CLAUDE_AUTH_EXPIRED", "codex_planner_worked": bounded,
                    "calls": self.budget.used, "run_id": run}
        runs.transition(run, "COLLECTING", expected_version=runs.get_run(run)["version"], reason="collect")
        # независимый Reviewer (codex-plus-02 ≠ planner/builder profile)
        verdict, independent = self.reviewer(run, runs, "codex-plus-02", b["profile"], b["session"])

        fix_loops = 0
        if verdict != "PASS" and b["wrote"] and self.budget.used < MAX_CALLS - 1:
            # один bounded fix-loop (тот же builder-профиль)
            fix_loops = 1
            runs.transition(run, "RUNNING", expected_version=runs.get_run(run)["version"], reason="fix-loop")
            b = self.builder(run, runs, [b["profile"]], worktree)
            runs.transition(run, "COLLECTING", expected_version=runs.get_run(run)["version"], reason="re-collect")
            verdict, independent = self.reviewer(run, runs, "codex-plus-02", b.get("profile", ""), b["session"])

        final = "SUCCEEDED" if verdict == "PASS" else "OWNER_REQUIRED"
        runs.transition(run, final, expected_version=runs.get_run(run)["version"],
                        reason=f"reviewer {verdict}",
                        next_action="owner: финальный обзор реального артефакта" if final == "SUCCEEDED" else "owner review")

        # verify durable privacy: сессии без transcript/секретов
        sess = runs.provider_sessions(run)
        privacy_ok = all("transcript" not in json.dumps(x) for x in sess)

        # final diff (реальный артефакт)
        diff = subprocess.run(["git", "-C", str(self.repo), "diff", head, "HEAD", "--stat"],
                              capture_output=True, text=True).stdout.strip()

        self.evidence.update({
            "run_id": run, "one_durable_run": one_run, "bounded_planner_package": bounded,
            "builder_wrote_artifact": b["wrote"], "artifact_sha": b["sha"],
            "max_concurrent_writers": b["max_writers"], "reviewer_independent": independent,
            "verdict": verdict, "final_state": final, "fix_loops": fix_loops,
            "provider_calls_used": self.budget.used, "call_log": self.budget.log,
            "privacy_no_transcript": privacy_ok, "diff_stat": diff,
            "events": [e["type"] for e in runs.events(run)],
        })
        # артефакт-файл (реальное содержимое, произведённое реальной моделью)
        artifact_copy = ART / "artifact_calc.py"
        if (self.repo / b["path"]).exists():
            artifact_copy.write_text((self.repo / b["path"]).read_text())

        (ART / f"real_e2e_{TS}.json").write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2, sort_keys=True))
        # manifest
        digests = {p.name: sha256_file(p) for p in sorted(ART.glob("*"))}
        (ART / "manifest_sha256.json").write_text(json.dumps(digests, ensure_ascii=False, indent=2, sort_keys=True))

        ok = (one_run and bounded and b["wrote"] and b["max_writers"] == 1 and independent
              and privacy_ok and verdict == "PASS" and self.budget.used <= MAX_CALLS)
        print(f"\n  РЕЗУЛЬТАТ: verdict={verdict} final={final} calls={self.budget.used}/{MAX_CALLS} "
              f"artifact={b['wrote']} one_writer={b['max_writers']==1} independent={independent} ok={ok}")
        print(f"  Evidence: {ART}")
        return {"ok": ok, "verdict": verdict, "final": final, "calls": self.budget.used,
                "artifact_sha": b["sha"], "run_id": run}


if __name__ == "__main__":
    RealE2E().run()
