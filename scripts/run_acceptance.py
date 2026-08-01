#!/usr/bin/env python3
"""VP-0 acceptance suite — честное доказательство (Master Spec §33).

Статусы критериев (важно — механика НЕ засчитывается как финальный PASS):
  PASS            — доказано реально и воспроизводимо здесь;
  PASS_MECHANISM  — доказан только механизм (fake/симуляция); реальная часть — ниже;
  GATE_REAL       — механизм готов, но реальное подтверждение требует owner-логина
                    реальных подписочных профилей;
  FAIL            — не выполнено.

Итог VP-0 НЕ печатает «11/11 PASS», пока реальные A→B (крит. 3–5) не пройдены.

Запуск: PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for pkg in ("apps/core", "apps/runner"):
    sys.path.insert(0, str(_ROOT / pkg))

from atlas_core import config, isolation  # noqa: E402
from atlas_core.adapters.fake import FakeClaudeAdapter, FakeCodexAdapter, FaultInjection  # noqa: E402
from atlas_core.adapters.real_claude import RealClaudeAdapter  # noqa: E402
from atlas_core.adapters.real_codex import RealCodexAdapter  # noqa: E402
from atlas_core.contracts import JobPackage, Provider, Role, RunState  # noqa: E402
from atlas_core.diagnostics import snapshot  # noqa: E402
from atlas_core.handoff import build_checkpoint  # noqa: E402
from atlas_core.leases import LeaseStore  # noqa: E402
from atlas_core.orchestrator import Candidate, Core  # noqa: E402
from atlas_core.profiles import ProfileRegistry, check_root_permissions  # noqa: E402
from atlas_core.real_probe import probe_provider  # noqa: E402
from atlas_core.secret_scan import scan_repo  # noqa: E402
from atlas_core.store import Store  # noqa: E402
from atlas_runner.recovery_demo import prove_recovery_to_success  # noqa: E402

PASS, MECH, GATE, FAIL = "PASS", "PASS_MECHANISM", "GATE_REAL", "FAIL"


class Acceptance:
    def __init__(self):
        # Механические стораджи — во временном каталоге; изоляция и реальные
        # probe используют РЕАЛЬНЫЙ layout (/var/lib/codevinci-atlas).
        os.environ.pop("ATLAS_DATA_DIR", None)
        self.tmp = tempfile.mkdtemp(prefix="atlas-vp0-accept-")
        self.artifacts = Path(self.tmp) / "artifacts" / "vp0"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.results: list[dict] = []
        self.real_registry = ProfileRegistry()  # реальный layout

    def _art(self, name, content) -> str:
        path = self.artifacts / name
        if isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        else:
            path.write_text(str(content), encoding="utf-8")
        return str(path)

    def record(self, cid, name, status, note, evidence, mechanism=None):
        self.results.append({"id": cid, "criterion": name, "status": status, "mechanism": mechanism,
                             "note": note, "evidence": [os.path.basename(e) for e in evidence]})

    # --- механические стораджи ---------------------------------------------
    def _mstore(self, name="mech.db"):
        return Store(os.path.join(self.tmp, name))

    # --- критерии -----------------------------------------------------------
    def c1_isolated_roots(self):
        profiles = self.real_registry.list()
        perms = {p.alias: check_root_permissions(p) for p in profiles}
        ok = (len(profiles) >= 4 and
              all(perms[a]["is_0700"] for a in perms) and
              all(perms[a].get("owner_is_runtime_user") for a in perms))
        ev = [self._art("c1_permissions.json", perms),
              self._art("c1_identities.json", {p.alias: {"provider": p.provider,
                        "runtime_user": p.runtime_user, "owner": perms[p.alias]["owner"]} for p in profiles})]
        self.record(1, "2 Codex + 2 Claude roots isolated & aliased (own identities)",
                    PASS if ok else FAIL,
                    "4 root'а 0700, каждый во владении СВОЕЙ Unix-идентичности; aliases в non-secret реестре", ev)
        return profiles

    def c2_cross_read(self, profiles):
        rep = isolation.prove_isolation(profiles)
        ev = [self._art("c2_isolation_matrix.json", rep)]
        if not rep.get("available"):
            self.record(2, "Cross-read blocked (real execution identities)", GATE,
                        "нужен root+runuser для OS-доказательства; Core-guard проверен юнит-тестами", ev)
            return
        ok = rep.get("ok")
        cross = sum(1 for m in rep["matrix"] if m["kind"] == "cross_profile")
        svc = sum(1 for m in rep["matrix"] if m["kind"] == "service_user")
        self.record(2, "Cross-read blocked (real execution identities)",
                    PASS if ok else FAIL,
                    f"Идентичность профиля A не читает credentials B ({cross} кросс-чтений DENIED); "
                    f"сервисный atlas не читает ни один ({svc} DENIED)", ev)

    def _fake_switch(self, provider, cls, aA, aB):
        store = self._mstore(f"switch_{provider}.db")
        core = Core(store, LeaseStore(store))
        # Механизм: обычные Profile с временными путями (реальный layout не трогаем).
        from atlas_core.profiles import Profile, ProfileState
        pA = Profile(aA, provider, os.path.join(self.tmp, aA)); pA.state = ProfileState.READY
        pB = Profile(aB, provider, os.path.join(self.tmp, aB)); pB.state = ProfileState.READY
        job = JobPackage(goal="sum", role=Role.BUILDER, provider=Provider(provider),
                         inputs={"work_items": [1, 2, 3, 4, 5, 6], "worker_label": aA})
        result, tele = core.run_with_switch(
            job, project_id="codevinci-atlas", worktree=f"wt-{provider}", vp_id="VP-0",
            candidates=[Candidate(pA, cls(FaultInjection(rate_limit_after=3))), Candidate(pB, cls())])
        store.close()
        return result, tele

    def _try_real_probes(self):
        """Попытаться реальные A→B, если профили авторизованы. Иначе GATE."""
        adapters = {"codex": RealCodexAdapter(), "claude": RealClaudeAdapter()}
        ready = {"codex": [], "claude": []}
        for p in self.real_registry.list():
            st = adapters[p.provider].auth_status(p.root_path, executable=p.executable_path,
                                                  run_as_user=p.runtime_user)
            if st.get("authenticated"):
                ready[p.provider].append(p)
        results = {}
        if all(len(ready[pr]) >= 2 for pr in ("codex", "claude")):
            store = self._mstore("realprobe.db")
            for pr in ("codex", "claude"):
                results[pr] = probe_provider(pr, ready[pr][0], ready[pr][1], store)
            store.close()
        return ready, results

    def c3_4_5_6(self):
        rc, tc = self._fake_switch("codex", FakeCodexAdapter, "codex-plus-01", "codex-plus-02")
        rl, tl = self._fake_switch("claude", FakeClaudeAdapter, "claude-pro-01", "claude-pro-02")
        mech_proof = {
            "codex": {"sum": rc.result.structured_output["sum"], "max_writers": tc.max_concurrent_writers,
                      "single_writer_ok": tc.single_writer_ok, "switch": [s["code"] for s in tc.switches]},
            "claude": {"sum": rl.result.structured_output["sum"], "max_writers": tl.max_concurrent_writers,
                       "single_writer_ok": tl.single_writer_ok, "switch": [s["code"] for s in tl.switches]},
        }
        mech_ok = (mech_proof["codex"]["sum"] == 21 and mech_proof["claude"]["sum"] == 21 and
                   mech_proof["codex"]["max_writers"] == 1 and mech_proof["claude"]["max_writers"] == 1)

        ready, real = self._try_real_probes()
        real_ok = bool(real) and all(r.get("ok") for r in real.values()) and len(real) == 2
        ev = [self._art("c3-6_mechanism.json", mech_proof),
              self._art("c3-6_real.json", {"authed": {k: [p.alias for p in v] for k, v in ready.items()},
                                          "results": real})]

        def st():  # статус для крит. 3–5
            if real_ok:
                return PASS
            return GATE if mech_ok else FAIL

        self.record(3, "Minimal run A structured", st(),
                    "Реальный минимальный структурный run A" if real_ok else
                    "Механизм доказан (fake). Реальный run A — за owner-гейтом.", ev,
                    mechanism=MECH if mech_ok else FAIL)
        self.record(4, "B continues from verified HandoffPackage", st(),
                    "Реальный B продолжил из HandoffPackage" if real_ok else
                    "Механизм доказан (fake A→B). Реальный — за owner-гейтом.", ev,
                    mechanism=MECH if mech_ok else FAIL)
        self.record(5, "Proven for BOTH providers (real)", st(),
                    "Реально для Codex и Claude" if real_ok else
                    "Механизм для обоих провайдеров. Реальное — за owner-гейтом.", ev,
                    mechanism=MECH if mech_ok else FAIL)
        # крит. 6 — «simulated» по определению: механизм и есть результат
        self.record(6, "Simulated rate limit switches w/o second writer", PASS if mech_ok else FAIL,
                    "RATE_LIMITED → release lease A → acquire B; max_concurrent_writers=1 (по определению симуляция)", ev)

    def c7_restart(self):
        db = os.path.join(self.tmp, "restart.db")
        s1 = Store(db)
        s1.upsert_run(run_id="run_active", state=RunState.RUNNING.value, project_id="codevinci-atlas", vp_id="VP-0")
        s1.save_checkpoint(build_checkpoint(project_id="codevinci-atlas", vp_id="VP-0", branch="atlas/vp-0",
                           head="sha1", status_porcelain="", cause="crash",
                           profile_alias="codex-plus-01", session_id="sess1").to_dict())
        s1.close()
        s2 = Store(db); core2 = Core(s2, LeaseStore(s2))
        rec = core2.recover_after_core_restart("codevinci-atlas")
        run = s2.get_run("run_active")
        ok = "run_active" in rec["interrupted_runs"] and run["state"] == "INTERRUPTED" and rec["checkpoint"]
        s2.close()
        ev = [self._art("c7_restart.json", {"interrupted": rec["interrupted_runs"],
                                            "run_state_after": run["state"],
                                            "checkpoint_session": rec["checkpoint"]["session_id"]})]
        self.record(7, "Core restart preserves state", PASS if ok else FAIL,
                    "После рестарта активный run → INTERRUPTED, checkpoint доступен для продолжения", ev)

    def c8_runner_recovery(self):
        rep = asyncio.run(prove_recovery_to_success(os.path.join(self.tmp, "recovery")))
        ev = [self._art("c8_runner_recovery.json", rep)]
        self.record(8, "Runner interruption reconciled & continued to success",
                    PASS if rep["ok"] else FAIL,
                    f"Обрыв на {rep['processed_at_interrupt']} → recovery → продолжение до ОДНОГО SUCCESS; "
                    f"все элементы обработаны один раз; max_writers={rep['max_concurrent_writers']}", ev)

    def c9_secret_scan(self):
        extra = [str(config.PROD_DATA_DIR), str(_ROOT / "var"), self.tmp]
        rep = scan_repo(str(_ROOT), extra_roots=[e for e in extra if Path(e).exists()])
        ev = [self._art("c9_secret_scan.json", rep.to_dict())]
        self.record(9, "No credentials in tree/history/DB/logs/artifacts", PASS if rep.clean else FAIL,
                    f"Реальных находок: {len(rep.real_hits)}; аллоуслист (синт. фикстуры): "
                    f"{len(rep.allowlisted_hits)}; коммитов: {rep.git_commits} ({rep.note})", ev)

    def c10_capacity(self):
        from atlas_core.capacity import unknown_capacity
        cap = unknown_capacity().to_dict()
        ok = cap["status"] == "UNKNOWN" and cap["5h_remaining"] is None and cap["7d_remaining"] is None
        ev = [self._art("c10_capacity.json", cap)]
        self.record(10, "UNKNOWN capacity honest", PASS if ok else FAIL,
                    "Остаток лимита не выдумывается: status=UNKNOWN, remaining=null", ev)

    def c11_repeatable(self, unittest_ok):
        ev = [self._art("c11_snapshot.json", snapshot())]
        self.record(11, "Repeatable report/evidence", PASS if unittest_ok else FAIL,
                    "Прогон детерминирован; юнит-приёмка зелёная; артефакты сохранены", ev)

    def run(self):
        profiles = self.c1_isolated_roots()
        self.c2_cross_read(profiles)
        self.c3_4_5_6()
        self.c7_restart()
        self.c8_runner_recovery()
        self.c9_secret_scan()
        self.c10_capacity()
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                           cwd=str(_ROOT), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner:{_ROOT}/tests"})
        unittest_ok = r.returncode == 0
        self._art("unittest_output.txt", r.stderr[-6000:])
        self.c11_repeatable(unittest_ok)

        real_pass = sum(1 for c in self.results if c["status"] == PASS)
        gates = [c["id"] for c in self.results if c["status"] == GATE]
        fails = [c["id"] for c in self.results if c["status"] == FAIL]
        vp0_complete = not gates and not fails and unittest_ok
        if vp0_complete:
            verdict = "COMPLETE — 11/11 PASS (включая реальные A→B Codex и Claude)"
        elif gates:
            verdict = f"INCOMPLETE — GATE_REAL на критериях {gates} (нужен owner-логин)"
        elif fails:
            verdict = f"INCOMPLETE — FAIL на критериях {fails}"
        else:
            verdict = "INCOMPLETE — юнит-приёмка не зелёная"
        matrix = {"vp": "VP-0", "generated_at": snapshot()["host"]["collected_at"],
                  "criteria": self.results, "unittest_passed": unittest_ok,
                  "real_pass_count": real_pass, "gate_real_criteria": gates, "failed_criteria": fails,
                  "vp0_complete": vp0_complete, "verdict": verdict}
        self._art("acceptance_matrix.json", matrix)
        self._write_report(matrix)
        self._publish_to_repo()
        return matrix

    def _write_report(self, matrix):
        lines = ["# VP-0 Acceptance Report", "",
                 f"Сгенерировано: {matrix['generated_at']}",
                 f"Итог VP-0: **{matrix['verdict']}**",
                 f"Юнит-приёмка: {'PASS' if matrix['unittest_passed'] else 'FAIL'}",
                 f"Реальных PASS: {matrix['real_pass_count']}/11; GATE_REAL: {matrix['gate_real_criteria']}", "",
                 "| # | Критерий | Статус | Механизм | Заметка |", "|---|---|---|---|---|"]
        for c in matrix["criteria"]:
            lines.append(f"| {c['id']} | {c['criterion']} | **{c['status']}** | {c['mechanism'] or '—'} | {c['note']} |")
        lines += ["", f"Артефакты: `{self.artifacts}` (опубликованы в `var/artifacts/vp0/`)", ""]
        self._art("acceptance_report.md", "\n".join(lines))

    def _publish_to_repo(self):
        import shutil
        dest = _ROOT / "var" / "artifacts" / "vp0"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.artifacts, dest)
        self.published = dest


def main():
    a = Acceptance()
    m = a.run()
    print("\n=== VP-0 ACCEPTANCE MATRIX ===")
    for c in m["criteria"]:
        mech = f" (mechanism={c['mechanism']})" if c["mechanism"] else ""
        print(f"  [{c['status']:>14}] #{c['id']:>2} {c['criterion']}{mech}")
    print(f"\n  unittest: {'PASS' if m['unittest_passed'] else 'FAIL'}")
    print(f"  Реальных PASS: {m['real_pass_count']}/11   GATE_REAL: {m['gate_real_criteria']}   FAIL: {m['failed_criteria']}")
    print(f"\n  ИТОГ VP-0: {m['verdict']}")
    print(f"  Артефакты: {getattr(a, 'published', a.artifacts)}")


if __name__ == "__main__":
    main()
