"""VP-5 Agent Pipeline — детерминированная оркестрация Planner → Builder →
независимый Reviewer (Master Spec §17, §38).

Функциональный slice поверх ``0005_agent_pipeline``: idempotent Run, типизированный
lifecycle, router без silent fallback, координация worktree-writer-аренды (VP-2
``wsleases``) и profile-аренды (``run_leases``) с гарантией одного writer,
provider-session-ссылки без transcript/credentials, bounded-ретраи с
классификацией, pause/resume, interruption-recovery и fresh-session handoff.

Синтетическая «работа» проверяема: список целых обрабатывается по элементам,
Builder пишет реальный артефакт ``RESULT.json`` в worktree, а независимый
Reviewer **перечисляет и пересчитывает** результат из плана, а не доверяет отчёту
Builder — поэтому ложный success Builder не может стать PASS. По умолчанию
используются fake-адаптеры (§32.2) — без реальных подписочных вызовов.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import audit
from .adapters.fake import FakeClaudeAdapter, FakeCodexAdapter, FaultInjection
from .contracts import JobPackage, Provider, Role
from .errors import AtlasError, ErrorCode
from .router import Candidate, route_profile
from .run_leases import RunLeaseService
from .runs import RunService
from .wsleases import WorktreeLeaseService

# Роль → провайдер по умолчанию (§17.1).
_ROLE_PROVIDER = {"planner": "codex", "builder": "claude", "reviewer": "codex"}

_RETRYABLE_SWITCH = {ErrorCode.RATE_LIMITED}
_NO_LOOP = {ErrorCode.AUTH_REQUIRED, ErrorCode.AUTH_EXPIRED, ErrorCode.POLICY_DENIED}


def _adapter(provider: str, faults: FaultInjection | None = None, *, authenticated: bool = True):
    if provider == "claude":
        return FakeClaudeAdapter(faults, authenticated=authenticated)
    return FakeCodexAdapter(faults, authenticated=authenticated)


def _job(role: str, provider: str, work_items: list[int], *, worker_label: str,
         resume_from: int = 0, partial_sum: int = 0, processed: dict | None = None,
         acceptance: list[str] | None = None) -> JobPackage:
    return JobPackage(
        goal="Синтетический VP-5: обработать список и записать RESULT.json",
        role=Role(role), provider=Provider(provider),
        acceptance_criteria=acceptance or ["sum корректна", "все элементы обработаны"],
        inputs={"work_items": list(work_items), "worker_label": worker_label,
                "resume_from": resume_from, "partial_sum": partial_sum,
                "processed": processed or {}})


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class PipelineResult(dict):
    """Телеметрия прогона (для тестов и evidence)."""


class PipelineService:
    """Оркестратор одного синтетического Run. Все внешние зависимости —
    инъекция параметрами, поэтому прогон детерминирован и тестируем."""

    def __init__(self, db_path: str, *, run_svc: RunService | None = None,
                 max_fix_loops: int = 1, ttl_s: float = 30.0):
        self.db_path = db_path
        self.runs = run_svc or RunService()
        self.max_fix_loops = max_fix_loops
        self.ttl_s = ttl_s

    # --- helpers -----------------------------------------------------------
    def _route(self, run_id: str, role: str, candidates: list[Candidate], *,
               requested_profile: str = "", requested_model: str = "",
               effective_model: str = "", seq: int = 0, project_id: str = "") -> dict:
        decision = route_profile(Role(role), candidates, requested_profile=requested_profile,
                                 requested_model=requested_model, effective_model=effective_model)
        self.runs.record_router_decision(run_id, decision)
        step_id = self.runs.create_role_step(
            run_id, role, seq, provider=_ROLE_PROVIDER[role],
            requested_model=requested_model, effective_model=decision.effective_model,
            requested_profile=requested_profile, effective_profile=decision.effective_profile,
            reason_code=decision.reason_code, project_id=project_id)
        return {"decision": decision, "step_id": step_id}

    def _writer_count(self, worktree: str) -> int:
        wl = WorktreeLeaseService(self.db_path)
        try:
            return wl.active_count(worktree)
        finally:
            wl.close()

    def _write_artifact(self, worktree_path: str, structured: dict, *, corrupt: bool) -> tuple[str, str]:
        out = dict(structured)
        if corrupt:
            out["sum"] = int(out.get("sum", 0)) + 1  # ложный результат Builder
        p = Path(worktree_path) / "RESULT.json"
        p.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        return str(p), _sha256_file(p)

    # --- Builder (единственный writer) с bounded switch/recovery ------------
    def _run_builder(self, run_id: str, *, project_id: str, worktree_path: str,
                     work_items: list[int], candidates: list[Candidate], builder_profile: str,
                     builder_root: str, faults: FaultInjection | None, corrupt: bool,
                     telem: dict) -> dict:
        wl = WorktreeLeaseService(self.db_path, ttl_s=self.ttl_s)
        pl = RunLeaseService(self.db_path, ttl_s=self.ttl_s)
        profile = builder_profile
        attempt = 0
        switches_done = 0
        recoveries_done = 0
        resume_from = 0
        partial_sum = 0
        processed: dict = {}
        session_id = ""  # пусто → fresh session; иначе resume той же сессии
        try:
            while True:
                attempt += 1
                # Захват writer-аренды worktree + profile-аренды. Инвариант: перед
                # каждым acquire предыдущие аренды освобождены (release-before-acquire).
                wlease = wl.acquire(project_id=project_id, worktree=worktree_path,
                                    role="builder", holder=profile)
                please = pl.acquire(profile_id=profile, run_id=run_id, role="builder",
                                    worktree=worktree_path, holder=profile)
                telem["max_concurrent_writers"] = max(
                    telem.get("max_concurrent_writers", 0), wl.active_count(worktree_path))
                adapter = _adapter("claude", faults if attempt == 1 else None)
                job = _job("builder", "claude", work_items, worker_label=profile,
                           resume_from=resume_from, partial_sum=partial_sum, processed=processed)
                try:
                    if session_id:
                        res = adapter.resume(session_id, job, profile_alias=profile, root_path=builder_root)
                    else:
                        res = adapter.start(job, profile_alias=profile, root_path=builder_root)
                except AtlasError as exc:
                    code = exc.classified.code
                    part = getattr(exc, "partial_state", {}) or {}
                    # Release-before-acquire: освобождаем ОБЕ аренды до любого switch/recovery.
                    pl.release(please.id)
                    wl.release(wlease.id)
                    self.runs.record_retry(run_id, role="builder", attempt=attempt,
                                           error_class=code.value)
                    resume_from = int(part.get("processed_index", resume_from))
                    partial_sum = int(part.get("partial_sum", partial_sum))
                    processed = part.get("processed", processed)
                    if code in _NO_LOOP:
                        # Auth/policy не ретраятся бесконечно → owner-действие (0 ретраев).
                        target = ("AUTH_REQUIRED" if code in (ErrorCode.AUTH_REQUIRED,
                                  ErrorCode.AUTH_EXPIRED) else "OWNER_REQUIRED")
                        self.runs.transition(run_id, target, expected_version=self._ver(run_id),
                                             reason=code.value, blocker=f"builder:{code.value}")
                        telem["builder_stopped"] = code.value
                        return {"ok": False, "reason": code.value}
                    if code in _RETRYABLE_SWITCH:
                        if switches_done >= 1:
                            # Ровно один безопасный switch; второй rate-limit → owner-required.
                            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                                 reason="повторный rate limit после switch",
                                                 blocker="RATE_LIMITED")
                            telem["builder_stopped"] = "RATE_LIMITED_BOUNDED"
                            return {"ok": False, "reason": "RATE_LIMITED_BOUNDED"}
                        self.runs.transition(run_id, "RATE_LIMITED",
                                             expected_version=self._ver(run_id), reason="builder rate limit")
                        nxt = self._switch_candidate(candidates, exclude=profile)
                        if nxt is None:
                            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                                 reason="нет второго профиля для switch",
                                                 blocker="NO_ELIGIBLE_PROFILE")
                            telem["builder_stopped"] = "NO_ELIGIBLE_PROFILE"
                            return {"ok": False, "reason": "NO_ELIGIBLE_PROFILE"}
                        switches_done += 1
                        telem.setdefault("switches", []).append({"from": profile, "to": nxt})
                        # Смена профиля → fresh session с handoff (не resume чужой сессии).
                        self.runs.record_handoff_link(run_id, "handoff_" + run_id, "handoff")
                        self.runs.append_event(run_id, "session.fresh_with_handoff",
                                               {"from_profile": profile, "to_profile": nxt})
                        profile = nxt
                        session_id = ""
                        self.runs.transition(run_id, "PREPARING", expected_version=self._ver(run_id),
                                             reason="switch profile")
                        self.runs.transition(run_id, "RUNNING", expected_version=self._ver(run_id),
                                             reason="fresh session after switch")
                        continue
                    if code == ErrorCode.USER_INTERRUPTED and recoveries_done < 1:
                        # Interruption → ровно одна безопасная continuation из checkpoint (тот же профиль,
                        # resume той же сессии).
                        recoveries_done += 1
                        session_id = part.get("session_id", session_id)
                        self.runs.record_handoff_link(run_id, "ckpt_" + run_id, "checkpoint")
                        self.runs.record_pause(run_id, "interruption", reason="builder interrupted",
                                               safe_continuation_ref="ckpt_" + run_id)
                        self.runs.transition(run_id, "INTERRUPTED",
                                             expected_version=self._ver(run_id), reason="builder interrupted")
                        self.runs.transition(run_id, "PREPARING", expected_version=self._ver(run_id),
                                             reason="recovery")
                        self.runs.transition(run_id, "RUNNING", expected_version=self._ver(run_id),
                                             reason="one safe continuation")
                        continue
                    # Прочее (или превышение bound) → FAILED (без бесконечного ретрая).
                    self.runs.transition(run_id, "FAILED", expected_version=self._ver(run_id),
                                         reason=code.value, failure_class=code.value)
                    telem["builder_stopped"] = code.value
                    return {"ok": False, "reason": code.value}
                # Успех Builder: записываем provider-session и артефакт.
                session_id = res.result.session_id or session_id
                self.runs.record_provider_session(run_id, provider="claude", session_id=session_id,
                                                  role="builder", profile_id=profile)
                art_path, art_sha = self._write_artifact(worktree_path, res.result.structured_output,
                                                         corrupt=corrupt)
                self.runs.append_event(run_id, "builder.artifact", {"path": "RESULT.json", "sha": art_sha})
                pl.release(please.id)
                wl.release(wlease.id)
                telem["builder_profile"] = profile
                telem["builder_session"] = session_id
                telem["artifact_path"] = art_path
                telem["artifact_sha"] = art_sha
                return {"ok": True, "profile": profile, "session_id": session_id,
                        "artifact_path": art_path, "artifact_sha": art_sha}
        finally:
            wl.close()
            pl.close()

    def _switch_candidate(self, candidates: list[Candidate], *, exclude: str) -> str | None:
        # Router выбирает другой eligible builder-профиль (exclude → COOLDOWN).
        adj = []
        for c in candidates:
            if c.alias == exclude:
                adj.append(Candidate(alias=c.alias, provider=c.provider, state="COOLDOWN"))
            else:
                adj.append(c)
        d = route_profile(Role.BUILDER, adj)
        return d.effective_profile if d.ok else None

    def _ver(self, run_id: str) -> int:
        return self.runs.get_run(run_id)["version"]

    # --- независимый Reviewer (read-only, пересчёт) ------------------------
    def _run_reviewer(self, run_id: str, *, project_id: str, worktree_path: str,
                      work_items: list[int], candidates: list[Candidate], reviewer_root: str,
                      builder_session: str, builder_profile: str, seq: int, telem: dict) -> dict:
        routed = self._route(run_id, "reviewer", candidates, seq=seq, project_id=project_id)
        decision = routed["decision"]
        if not decision.ok:
            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                 reason="нет reviewer-профиля", blocker=decision.reason_code)
            return {"verdict": "OWNER_REQUIRED", "reason": decision.reason_code}
        reviewer_profile = decision.effective_profile
        # Независимость: другой профиль и другая сессия, без writer-аренды. Reviewer
        # НЕ вызывает Builder-адаптер и НЕ доверяет его отчёту — пересчитывает сам.
        session_id = "sess_rev_" + run_id
        independent = (reviewer_profile != builder_profile
                       and session_id != builder_session
                       and self._writer_count(worktree_path) == 0)
        self.runs.record_provider_session(run_id, provider="codex", session_id=session_id,
                                          role="reviewer", profile_id=reviewer_profile)
        self.runs.update_role_step(routed["step_id"], expected_version=1, status="RUNNING",
                                   session_ref=session_id, builder_session_ref=builder_session)
        telem["reviewer_profile"] = reviewer_profile
        telem["reviewer_session"] = session_id
        telem["reviewer_independent"] = independent
        if not independent:
            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                 reason="reviewer не независим", blocker="REVIEWER_NOT_INDEPENDENT")
            return {"verdict": "BLOCKED", "reason": "REVIEWER_NOT_INDEPENDENT"}
        # Пересчёт из плана — НЕ доверяем отчёту Builder.
        expected_sum = sum(work_items)
        art = Path(worktree_path) / "RESULT.json"
        findings = []
        if not art.exists():
            findings.append({"criterion": "artifact", "detail": "RESULT.json отсутствует"})
        else:
            data = json.loads(art.read_text(encoding="utf-8"))
            if int(data.get("sum", -1)) != expected_sum:
                findings.append({"criterion": "sum", "detail": "sum не совпадает с пересчётом"})
            processed = data.get("processed_by", {})
            idxs = {i for lst in processed.values() for i in lst}
            if idxs != set(range(len(work_items))):
                findings.append({"criterion": "coverage", "detail": "обработаны не все элементы"})
            if not data.get("complete"):
                findings.append({"criterion": "complete", "detail": "результат не complete"})
        verdict = "PASS" if not findings else "REVISE"
        self.runs.update_role_step(routed["step_id"], expected_version=2, status="SUCCEEDED",
                                   verdict=verdict, reason_code="RECOMPUTED")
        self.runs.append_event(run_id, "reviewer.verdict", {"verdict": verdict, "findings": findings})
        return {"verdict": verdict, "findings": findings, "reviewer_profile": reviewer_profile}

    # --- полный прогон -----------------------------------------------------
    def run_synthetic(self, run_id: str, *, project_id: str, worktree_path: str,
                      work_items: list[int], candidates: dict[str, list[Candidate]],
                      profile_roots: dict[str, str], requested: dict | None = None,
                      builder_faults: FaultInjection | None = None,
                      builder_corrupt: str | None = None) -> PipelineResult:
        """builder_corrupt: None|'first'|'always' — моделирует ложный результат Builder."""
        requested = requested or {}
        telem: dict = {"run_id": run_id, "max_concurrent_writers": 0, "switches": [], "fix_loops": 0}
        # QUEUED → PREPARING
        self.runs.transition(run_id, "PREPARING", expected_version=self._ver(run_id), reason="prepare")

        # 1. Planner (codex) — б bounded план (work_items уже приняты из Work Order).
        p_req = requested.get("planner", {})
        p_routed = self._route(run_id, "planner", candidates["planner"],
                               requested_profile=p_req.get("profile", ""),
                               requested_model=p_req.get("model", ""), seq=1, project_id=project_id)
        if not p_routed["decision"].ok:
            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                 reason="planner недоступен", blocker=p_routed["decision"].reason_code)
            telem["final_state"] = "OWNER_REQUIRED"
            telem["reason"] = p_routed["decision"].reason_code
            return PipelineResult(telem)
        planner_profile = p_routed["decision"].effective_profile
        self.runs.transition(run_id, "RUNNING", expected_version=self._ver(run_id), reason="planner")
        p_adapter = _adapter("codex")
        p_res = p_adapter.start(_job("planner", "codex", work_items, worker_label=planner_profile),
                                profile_alias=planner_profile, root_path=profile_roots.get(planner_profile, ""))
        self.runs.record_provider_session(run_id, provider="codex", session_id=p_res.result.session_id,
                                          role="planner", profile_id=planner_profile)
        self.runs.update_role_step(p_routed["step_id"], expected_version=1, status="SUCCEEDED",
                                   session_ref=p_res.result.session_id)
        telem["planner_profile"] = planner_profile

        # 2. Builder (claude) — единственный writer, artifact.
        b_req = requested.get("builder", {})
        b_routed = self._route(run_id, "builder", candidates["builder"],
                               requested_profile=b_req.get("profile", ""),
                               requested_model=b_req.get("model", ""), seq=2, project_id=project_id)
        if not b_routed["decision"].ok:
            self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                 reason="builder недоступен (no silent fallback)",
                                 blocker=b_routed["decision"].reason_code)
            telem["final_state"] = "OWNER_REQUIRED"
            telem["reason"] = b_routed["decision"].reason_code
            return PipelineResult(telem)
        builder_profile = b_routed["decision"].effective_profile
        corrupt_now = builder_corrupt in ("first", "always")
        b = self._run_builder(run_id, project_id=project_id, worktree_path=worktree_path,
                              work_items=work_items, candidates=candidates["builder"],
                              builder_profile=builder_profile,
                              builder_root=profile_roots.get(builder_profile, ""),
                              faults=builder_faults, corrupt=corrupt_now, telem=telem)
        self.runs.update_role_step(b_routed["step_id"], expected_version=1,
                                   status="SUCCEEDED" if b["ok"] else "FAILED")
        if not b["ok"]:
            telem["final_state"] = self.runs.get_run(run_id)["state"]
            telem["reason"] = b["reason"]
            return PipelineResult(telem)
        builder_profile = b["profile"]

        # RUNNING → COLLECTING (writer-аренда уже освобождена перед review).
        self.runs.transition(run_id, "COLLECTING", expected_version=self._ver(run_id), reason="collect")

        # 3. Reviewer (codex, независимый) + один bounded fix-loop.
        seq = 3
        while True:
            rev = self._run_reviewer(run_id, project_id=project_id, worktree_path=worktree_path,
                                     work_items=work_items, candidates=candidates["reviewer"],
                                     reviewer_root=profile_roots.get("", ""),
                                     builder_session=b["session_id"], builder_profile=builder_profile,
                                     seq=seq, telem=telem)
            telem["verdict"] = rev["verdict"]
            if rev["verdict"] == "PASS":
                self.runs.transition(run_id, "SUCCEEDED", expected_version=self._ver(run_id),
                                     reason="reviewer PASS",
                                     next_action="owner: финальный обзор артефакта")
                telem["final_state"] = "SUCCEEDED"
                audit.record("runs.pipeline.succeeded",
                             f"run={run_id} artifact={telem.get('artifact_sha')}")
                return PipelineResult(telem)
            if rev["verdict"] in ("BLOCKED", "OWNER_REQUIRED"):
                telem["final_state"] = self.runs.get_run(run_id)["state"]
                return PipelineResult(telem)
            # REVISE: один bounded fix-loop.
            if telem["fix_loops"] >= self.max_fix_loops:
                self.runs.transition(run_id, "OWNER_REQUIRED", expected_version=self._ver(run_id),
                                     reason="второй провал review", blocker="SECOND_FIX_BLOCKED")
                telem["final_state"] = "OWNER_REQUIRED"
                telem["reason"] = "SECOND_FIX_BLOCKED"
                return PipelineResult(telem)
            telem["fix_loops"] += 1
            self.runs.transition(run_id, "RUNNING", expected_version=self._ver(run_id), reason="fix-loop")
            fix_corrupt = builder_corrupt == "always"
            b = self._run_builder(run_id, project_id=project_id, worktree_path=worktree_path,
                                  work_items=work_items, candidates=candidates["builder"],
                                  builder_profile=builder_profile,
                                  builder_root=profile_roots.get(builder_profile, ""),
                                  faults=None, corrupt=fix_corrupt, telem=telem)
            if not b["ok"]:
                telem["final_state"] = self.runs.get_run(run_id)["state"]
                return PipelineResult(telem)
            self.runs.transition(run_id, "COLLECTING", expected_version=self._ver(run_id),
                                 reason="re-collect after fix")
            seq += 1
