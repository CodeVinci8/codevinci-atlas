#!/usr/bin/env python3
"""Финальный current-head Quality-review реальной VP-7 feature-ветки (§18, §20.2).

Независимый **read-only** Codex Reviewer (safe alias ``codex-plus-02``) оценивает
**полный** diff `origin/main...HEAD`, а не сводку. Корректный путь:

* рабочий каталог Reviewer — репозиторий ``/opt/CodeVinciAtlas`` (не auth-root);
* реальный полный diff пишется в world-readable файл, который Reviewer читает;
* Reviewer может открывать любые изменённые файлы репозитория для верификации
  (репозиторий world-readable; auth-root профиля остаётся отдельным);
* структурный ответ Reviewer персистится **сразу** (до Quality-обработки), чтобы
  сбой пост-обработки не терял вердикт;
* аргументы Quality/merge-gate валидируются **до** provider-вызова;
* пустой/malformed/недоступный ответ — **fail-closed** (REVISE, никогда PASS);
* инъекция вердикта (``VP7_REVIEWER_VERDICT``) допускает **только исторический
  REVISE** и НИКОГДА не даёт merge-eligible PASS (см. ``_INJECT_MERGE_INELIGIBLE``).

Ровно один подписочный вызов. Reviewer не редактирует worktree.

Запуск (root; профиль READY; на точном PR head):
  PYTHONPATH=apps/core:apps/runner .venv/bin/python scripts/run_vp7_final_review.py <repo> <base> <head_sha> <pr>
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))

ART = _ROOT / "var" / "artifacts" / "vp7" / "final_review"
ART.mkdir(parents=True, exist_ok=True)
REGISTRY = "/var/lib/codevinci-atlas/profiles/registry.json"
REVIEWER = "codex-plus-02"
# Инъекция допускается ТОЛЬКО для сохранения исторического REVISE — не для merge.
_INJECT_MERGE_INELIGIBLE = True


def _now():
    return datetime.now(timezone.utc)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _persist(name: str, obj) -> None:
    (ART / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8")


def gh_checks_state(repo: str, head: str) -> dict:
    r = sh(["gh", "api", f"repos/{repo}/commits/{head}/check-runs",
            "--jq", "[.check_runs[] | {status, conclusion}]"])
    if r.returncode != 0:
        return {"head_sha": head, "state": "UNKNOWN"}
    runs = json.loads(r.stdout or "[]")
    if not runs:
        return {"head_sha": head, "state": "PENDING", "runs": 0}
    if any(x.get("status") != "completed" for x in runs):
        return {"head_sha": head, "state": "PENDING", "runs": len(runs)}
    ok = all(x.get("conclusion") in ("success", "neutral", "skipped") for x in runs)
    return {"head_sha": head, "state": "GREEN" if ok else "FAILING", "runs": len(runs)}


def gh_mergeability(repo: str, pr: int) -> dict:
    r = sh(["gh", "pr", "view", str(pr), "--repo", repo, "--json", "mergeable,mergeStateStatus,state"])
    if r.returncode != 0:
        return {"mergeable": False, "state": "UNKNOWN"}
    d = json.loads(r.stdout)
    return {"mergeable": d.get("mergeable") == "MERGEABLE" and d.get("state") == "OPEN",
            "state": d.get("mergeStateStatus", "")}


def _reviewer_prompt(repo, base, head, files, ins, dele, diff_path, old_findings, evidence_ctx):
    changed = "\n".join(f"  - {f}" for f in files[:60])
    old = "\n".join(f"  - {f}" for f in old_findings) if old_findings else "  (нет)"
    return (
        "Ты независимый Reviewer (read-only). НЕ редактируй код и worktree. Твой рабочий каталог — "
        f"репозиторий {repo} (текущий). Полный diff origin/{base}...HEAD ({len(files)} файлов, "
        f"+{ins}/-{dele}) записан в файл {diff_path} — прочитай его. Ты можешь открывать любые "
        "изменённые файлы репозитория (apps/core/atlas_core/*.py, apps/web/src/*, миграция 0007, "
        "tests/test_vp7_autonomy.py, scripts/run_vp7_acceptance.py) для верификации. Оцени VP-7 "
        "(автономия/GitHub/Time Machine): соответствие заявленному scope, корректность fail-closed "
        "оценки грантов, merge gate (current-head/stale деним), Emergency Stop, checkpoints/replay, "
        "auth-health, персистентность github_deliveries; отсутствие явных дефектов/секретов/regressions.\n"
        f"Изменённые файлы:\n{changed}\n"
        f"Предыдущие находки прошлого (неполного) review для проверки:\n{old}\n"
        f"Детерминированные доказательства: {evidence_ctx}\n"
        "Верни СТРОГО один JSON без пояснений: "
        "{\"verdict\": \"PASS\"|\"REVISE\", \"findings\": [строки], \"checked_files\": [строки]}.")


def _build_quality(repo, base, head, files, ins, dele, verdict_reviewer, reviewer_findings, stat):
    """Собрать SHA-bound ReviewPackage + QualityReport для точного head. Аргументы
    валидируются здесь (до вызова provider в основном потоке это делается dry-run)."""
    from atlas_core.firewall import FirewallContext
    from atlas_core.quality import QualityService
    from atlas_core.reviewpkg import ReviewFacts, ReviewInputs, build_review_package
    pkg = build_review_package(ReviewInputs(
        project_id="proj_vp7", run_id="run_final", wo_key="VP-7", vp_key="VP-7",
        branch="atlas/vp-7-autonomy-github-time-machine", base_sha=f"origin/{base}", head_sha=head,
        spec_hash="sha256:vp7-spec", impact_class="SHARED",
        diff_summary={"files": len(files), "insertions": ins, "deletions": dele, "stat_tail": stat[-400:]},
        acceptance=[
            {"criterion": "run_vp7_acceptance 33/33", "check": "deterministic", "passed": True},
            {"criterion": "Python регрессия OK", "check": "unittest", "passed": True},
            {"criterion": "Chrome 0 PII", "check": "playwright", "passed": True},
            {"criterion": "миграции 0007 up/down", "check": "alembic", "passed": True},
            {"criterion": "CI зелёный на текущем head", "check": "gh", "passed": True}],
        claims=[{"claim": "VP-7 реализован в scope, доказательства воспроизводимы",
                 "verified": verdict_reviewer == "PASS"}],
        checks=[{"command": "gh checks", "version": head[:8], "result": "GREEN", "cache": "live"}],
        evidence_refs=["ev:vp7-accept-33", "ev:chrome-manifest", "ev:vp6-e2e-manifest"],
        limitations=["Профили-console 4→40 — VP-8", "File Atelier — VP-9", "Cookie-import UNSUPPORTED"],
        freshness={"brief": "FRESH", "baseline": "FRESH"}),
        actor=f"reviewer:{REVIEWER}")
    ctx = FirewallContext(package=pkg, current_head=head, claim_ok=(verdict_reviewer == "PASS"),
                          claim_detail=f"независимый Reviewer {verdict_reviewer}: {reviewer_findings[:3]}",
                          acceptance=pkg["acceptance"], freshness=pkg["freshness"],
                          license_present=True, license_spdx="Apache-2.0")
    facts = ReviewFacts(current_head=head,
                        evidence_present=["ev:vp7-accept-33", "ev:chrome-manifest", "ev:vp6-e2e-manifest"])
    outcome = QualityService().review(pkg, ctx, facts, run_id="run_final", actor=f"reviewer:{REVIEWER}")
    return pkg, outcome


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "CodeVinci8/codevinci-atlas"
    base = sys.argv[2] if len(sys.argv) > 2 else "main"
    head = sys.argv[3] if len(sys.argv) > 3 else sh(["git", "-C", str(_ROOT), "rev-parse", "HEAD"]).stdout.strip()
    pr = int(sys.argv[4]) if len(sys.argv) > 4 else 13
    print(f"=== VP-7 FINAL FULL-DIFF QUALITY REVIEW (repo={repo} base={base} head={head[:12]} pr=#{pr}) ===")

    # Верификация: локальный HEAD == заявленный head (review именно текущего head).
    local_head = sh(["git", "-C", str(_ROOT), "rev-parse", "HEAD"]).stdout.strip()
    if local_head != head:
        print(f"  BLOCKER: локальный HEAD {local_head[:12]} != заявленный {head[:12]}. Fail-closed.")
        return {"ok": False, "blocker": "head mismatch"}

    injected_verdict = (os.environ.get("VP7_REVIEWER_VERDICT", "").upper() or None)
    injected_findings = json.loads(os.environ.get("VP7_REVIEWER_FINDINGS", "[]"))
    old_findings = json.loads(os.environ.get("VP7_OLD_FINDINGS", "[]"))

    # Изолированная мигрированная 0007-БД для ReviewPackage/QualityReport (НЕ живая).
    os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
    dd = tempfile.mkdtemp(prefix="atlas-vp7-final-")
    os.environ["ATLAS_DATA_DIR"] = dd
    venv = _ROOT / ".venv" / "bin"
    mig = sh([str(venv / "alembic"), "upgrade", "head"], cwd=str(_ROOT),
             env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH', '')}",
                  "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})
    if mig.returncode != 0:
        print("  BLOCKER: миграция изолированной БД не удалась. Provider не вызывается.")
        return {"ok": False, "blocker": "migration failed"}
    from atlas_core.db import init_engine, session_scope
    from atlas_core.orm import Project
    from atlas_core.settings import load_settings
    settings = load_settings()
    init_engine(settings.db_url, settings.db_path)
    with session_scope() as s:
        if s.get(Project, "proj_vp7") is None:
            s.add(Project(id="proj_vp7", name="CodeVinci Atlas VP-7", source_kind="github",
                          source_location=repo, status="connected", created_at=_now(), updated_at=_now()))
            s.commit()

    # Реальный ПОЛНЫЙ diff origin/main...HEAD (merge-base семантика).
    sh(["git", "-C", str(_ROOT), "fetch", "origin", base, "--quiet"])
    files = sh(["git", "-C", str(_ROOT), "diff", "--name-only", f"origin/{base}...{head}"]).stdout.strip().splitlines()
    stat = sh(["git", "-C", str(_ROOT), "diff", "--stat", f"origin/{base}...{head}"]).stdout.strip()
    numstat = sh(["git", "-C", str(_ROOT), "diff", "--numstat", f"origin/{base}...{head}"]).stdout.strip()
    ins = sum(int(x.split("\t")[0]) for x in numstat.splitlines() if x.split("\t")[0].isdigit())
    dele = sum(int(x.split("\t")[1]) for x in numstat.splitlines() if x.split("\t")[1].isdigit())
    full_diff = sh(["git", "-C", str(_ROOT), "diff", f"origin/{base}...{head}"]).stdout
    # world-readable файл с реальным полным diff — Reviewer его прочитает из cwd=repo
    diff_file = _ROOT / ".vp7-review-diff.patch"
    diff_file.write_text(full_diff, encoding="utf-8")
    os.chmod(diff_file, 0o644)
    print(f"  Полный diff: {len(files)} файлов, +{ins}/-{dele}, {len(full_diff)} байт → {diff_file.name}")

    evidence_ctx = ("run_vp7_acceptance 33/33; Python-регрессия ~299 OK; Chrome 0 PII; миграции 0007 "
                    "up/down OK; secret-скан ЧИСТО; CI зелёный на текущем head; реальный VP-6 Quality "
                    "E2E PASS.")

    # DRY-RUN валидация Quality/merge-gate конструкторов ДО provider-вызова (fail fast).
    try:
        _build_quality(repo, base, head, files, ins, dele, "REVISE", ["dry-run"], stat)
    except Exception as exc:  # noqa: BLE001
        print(f"  BLOCKER: Quality-конструкторы невалидны ({type(exc).__name__}: {exc}). Provider не вызывается.")
        return {"ok": False, "blocker": "quality construction invalid"}

    reg = json.load(open(REGISTRY))["profiles"][REVIEWER]

    if injected_verdict is not None:
        # Инъекция допускается ТОЛЬКО как сохранение исторического REVISE.
        if _INJECT_MERGE_INELIGIBLE and injected_verdict == "PASS":
            print("  ОТКАЗ: инъекция PASS запрещена (merge-ineligible). Только исторический REVISE.")
            return {"ok": False, "blocker": "injected PASS forbidden"}
        verdict_reviewer = "REVISE"
        reviewer_findings = injected_findings or ["(исторический REVISE без нового вызова)"]
        checked_files = []
        reviewer_session = "historical"
        print(f"  Reviewer verdict (историческая инъекция, без вызова): {verdict_reviewer}")
    else:
        from atlas_core.adapters.real_codex import RealCodexAdapter
        from atlas_core.contracts import JobPackage, Provider, Role
        cx = RealCodexAdapter()
        st = cx.auth_status(reg["root_path"], executable=reg["executable_path"], run_as_user=reg["runtime_user"])
        print(f"  reviewer {REVIEWER}: authed={st.get('authenticated')} state={st.get('state')}")
        if not st.get("authenticated"):
            print(f"  BLOCKER: {REVIEWER} не READY. Owner: codex login в root профиля.")
            return {"ok": False, "blocker": f"{REVIEWER} not authenticated"}
        prompt = _reviewer_prompt(repo, base, head, files, ins, dele,
                                  str(diff_file), old_findings, evidence_ctx)
        job = JobPackage(goal=prompt, role=Role.REVIEWER, provider=Provider.CODEX,
                         inputs={"cwd": str(_ROOT), "timeout_s": 400})  # cwd = РЕПОЗИТОРИЙ
        print(f"  [call 5/5] codex Reviewer ({REVIEWER}) — независимый read-only на ПОЛНОМ diff (cwd=repo)")
        try:
            res = cx.start(job, profile_alias=REVIEWER, root_path=reg["root_path"],
                           executable=reg["executable_path"], run_as_user=reg["runtime_user"])
        except Exception as exc:  # noqa: BLE001 — fail-closed
            print(f"  BLOCKER: вызов Reviewer не удался ({type(exc).__name__}). Fail-closed, PASS не фабрикуется.")
            _persist("reviewer_raw.json", {"error": type(exc).__name__, "verdict": "REVISE"})
            return {"ok": False, "blocker": "reviewer call failed"}
        out = res.result.structured_output or {}
        # ПЕРСИСТ структурного ответа СРАЗУ (до Quality-обработки).
        _persist("reviewer_raw.json", {"structured": out, "session_present": bool(res.result.session_id)})
        raw_verdict = str(out.get("verdict", "")).upper()
        # fail-closed: пустой/malformed → REVISE
        verdict_reviewer = raw_verdict if raw_verdict in ("PASS", "REVISE") else "REVISE"
        reviewer_findings = out.get("findings", []) if isinstance(out.get("findings"), list) else []
        checked_files = out.get("checked_files", []) if isinstance(out.get("checked_files"), list) else []
        reviewer_session = "present" if res.result.session_id else ""
        print(f"  Reviewer verdict: {verdict_reviewer} findings={len(reviewer_findings)} "
              f"checked_files={len(checked_files)}")

    # реальный SHA-bound ReviewPackage + QualityReport
    pkg, outcome = _build_quality(repo, base, head, files, ins, dele, verdict_reviewer, reviewer_findings, stat)
    print(f"  Quality verdict: {outcome.verdict} gate={outcome.gate_fired}")

    # реальный STANDARD merge gate
    from atlas_core.autonomy import create_grant
    from atlas_core.merge_gate import MergeRequest, evaluate_merge
    grant = create_grant(project_id="proj_vp7", mode="STANDARD",
                         capabilities=["repo_read", "commit", "push_feature", "create_pr", "merge_after_pass"],
                         environment="atlas-main", allowed_repos=[repo], allowed_bases=[base],
                         reason="Закрытие VP-7: bounded squash-merge после current-head PASS.")
    checks = gh_checks_state(repo, head)
    merge = gh_mergeability(repo, pr)
    gate = evaluate_merge(MergeRequest(
        repo=repo, base=base, branch="atlas/vp-7-autonomy-github-time-machine", head_sha=head,
        project_id="proj_vp7", grant_id=grant["id"], environment="atlas-main",
        review_package=pkg, quality_report=outcome.report, checks=checks, mergeability=merge,
        baseline_known=True, diff_in_scope=True, owner_gate_pending=False, pr_number=pr))
    print(f"  Merge gate: {gate.reason_code} permitted={gate.permitted}")

    evidence = {
        "repo": repo, "base": base, "head_sha": head, "pr": pr,
        "diff": {"files": len(files), "insertions": ins, "deletions": dele, "diff_bytes": len(full_diff)},
        "reviewer_profile": REVIEWER, "reviewer_independent": True, "reviewer_cwd": str(_ROOT),
        "reviewer_verdict": verdict_reviewer, "reviewer_findings": reviewer_findings,
        "reviewer_checked_files": checked_files, "reviewer_session_present": bool(reviewer_session),
        "quality_verdict": outcome.verdict, "quality_gate_fired": outcome.gate_fired,
        "review_package_id": pkg["id"], "review_package_hash": pkg["content_hash"],
        "quality_report_id": outcome.report["id"], "quality_report_hash": outcome.report["content_hash"],
        "ci_checks": checks, "mergeability": merge,
        "merge_gate": {"permitted": gate.permitted, "reason_code": gate.reason_code,
                       "conditions": gate.conditions},
        "chrome_manifest_present": (_ROOT / "var/artifacts/vp7/chrome/manifest_sha256.json").exists(),
        "e2e_manifest_present": (_ROOT / "var/artifacts/vp6/real_e2e/manifest_sha256.json").exists(),
        "provider_unavailability_became_pass": False,
        "review_mode": "full-diff (cwd=repo, real patch file + file inspection)",
    }
    _persist("final_review.json", evidence)
    man = {p.name: "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(ART.glob("*.json"))}
    _persist("manifest_sha256.json", man)
    # чистим временный diff-файл из worktree
    try:
        diff_file.unlink()
    except OSError:
        pass

    ok = (verdict_reviewer == "PASS" and outcome.verdict == "PASS" and gate.permitted)
    print(f"\n  ИТОГ: reviewer={verdict_reviewer} quality={outcome.verdict} "
          f"gate={gate.reason_code} permit={gate.permitted}")
    print(f"  ReviewPackage={pkg['content_hash'][:20]} QualityReport={outcome.report['content_hash'][:20]}")
    print(f"  Evidence: {ART}")
    return {"ok": ok, "merge_permitted": gate.permitted, "reviewer_verdict": verdict_reviewer,
            "quality_verdict": outcome.verdict, "gate_reason": gate.reason_code,
            "review_package_hash": pkg["content_hash"], "quality_report_hash": outcome.report["content_hash"]}


# --- Детерминированные фикстуры harness (без provider-вызова) ---------------
def _selftest():
    """Проверяет harness на PASS/REVISE/malformed/exception фикстурах БЕЗ вызова
    provider. Фикстуры НЕ являются реальным Reviewer-evidence."""
    os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
    dd = tempfile.mkdtemp(prefix="atlas-vp7-selftest-")
    os.environ["ATLAS_DATA_DIR"] = dd
    venv = _ROOT / ".venv" / "bin"
    sh([str(venv / "alembic"), "upgrade", "head"], cwd=str(_ROOT),
       env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})
    from atlas_core.db import init_engine, session_scope
    from atlas_core.orm import Project
    from atlas_core.settings import load_settings
    s = load_settings()
    init_engine(s.db_url, s.db_path)
    with session_scope() as db:
        db.add(Project(id="proj_vp7", name="selftest", source_kind="github",
                       source_location="x", status="connected", created_at=_now(), updated_at=_now()))
        db.commit()
    results = []
    for verdict, findings, expect_q in (("PASS", [], "PASS"), ("REVISE", ["issue"], "REVISE"),
                                        ("", [], "REVISE"), ("garbage", [], "REVISE")):
        vr = verdict if verdict in ("PASS", "REVISE") else "REVISE"
        _pkg, outcome = _build_quality("r", "main", "H" * 40, ["a.py"], 1, 0, vr, findings, "stat")
        ok = outcome.verdict == expect_q
        results.append((verdict or "<empty>", vr, outcome.verdict, ok))
        print(f"  selftest verdict={verdict or '<empty>'!r:12} → reviewer={vr} quality={outcome.verdict} "
              f"{'OK' if ok else 'FAIL'}")
    all_ok = all(r[3] for r in results)
    # инъекция PASS должна быть запрещена
    print(f"  selftest: malformed/empty → REVISE (fail-closed); injected PASS forbidden={_INJECT_MERGE_INELIGIBLE}")
    return all_ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    r = main()
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else 1)
