"""Оркестрация запусков, смена профиля и восстановление (Master Spec §16.5, §17).

Ключевые доказательства VP-0, которые обеспечивает этот модуль:

* A→B: запуск на профиле A, checkpoint+handoff, продолжение на профиле B с
  ровно того места (структурный вывод показывает вклад каждого профиля);
* simulated rate limit переключает профиль, НИКОГДА не создавая второго
  writer (аренда A освобождается до получения аренды B);
* Core restart: активные runs помечаются INTERRUPTED, продолжение из
  checkpoint;
* Runner interruption: обрыв job восстанавливается из checkpoint.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .contracts import JobPackage, RunState
from .errors import AtlasError, ErrorCode
from .handoff import build_checkpoint, build_handoff, verify_handoff
from .ids import new_id
from .leases import LeaseStore
from .profiles import Profile, ProfileRegistry, ProfileState
from .store import Store

# Ошибки, при которых осмысленно сменить профиль (а не повторять тем же).
SWITCHABLE = {ErrorCode.RATE_LIMITED}


@dataclass
class SwitchTelemetry:
    max_concurrent_writers: int = 0
    switches: list[dict] = field(default_factory=list)
    lease_ids: list[str] = field(default_factory=list)
    single_writer_ok: bool = True
    handoff_ids: list[str] = field(default_factory=list)
    checkpoint_ids: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    profile: Profile
    adapter: object  # AgentAdapter (fake или real)


class Core:
    def __init__(self, store: Store, lease_store: LeaseStore, registry: ProfileRegistry | None = None):
        self.store = store
        self.leases = lease_store
        self.registry = registry

    def run_with_switch(self, job: JobPackage, *, project_id: str, worktree: str, vp_id: str,
                        candidates: list[Candidate]) -> tuple[object, SwitchTelemetry]:
        """Выполнить job, переключая профиль при simulated rate limit.

        Инвариант единственного writer проверяется на каждом шаге.
        """

        tele = SwitchTelemetry()
        current_job = job
        last_error: AtlasError | None = None

        for i, cand in enumerate(candidates):
            alias = cand.profile.alias
            # --- получить аренду; гарантированно один writer -----------------
            if self.leases.active_count(project_id, worktree) != 0:
                tele.single_writer_ok = False
                raise AtlasError_from(ErrorCode.WORKTREE_CONFLICT,
                                      "перед acquire уже есть активная аренда")
            lease = self.leases.acquire(project_id=project_id, worktree=worktree,
                                        run_id=None, role=job.role.value, holder=alias)
            tele.lease_ids.append(lease.id)
            active = self.leases.active_count(project_id, worktree)
            tele.max_concurrent_writers = max(tele.max_concurrent_writers, active)
            if active != 1:
                tele.single_writer_ok = False

            run_id = new_id("run")
            self.store.upsert_run(run_id=run_id, state=RunState.RUNNING.value, project_id=project_id,
                                  vp_id=vp_id, role=job.role.value, provider=cand.profile.provider,
                                  profile_alias=alias)

            progress: dict = {}

            def on_progress(state: dict, _p=progress):
                _p.clear()
                _p.update(state)

            try:
                result = cand.adapter.start(current_job, profile_alias=alias,
                                            root_path=cand.profile.root_path, on_progress=on_progress)
            except AtlasError as exc:
                last_error = exc
                code = exc.classified.code
                partial = getattr(exc, "partial_state", None) or progress
                # checkpoint + handoff ДО освобождения аренды
                ckpt = build_checkpoint(project_id=project_id, vp_id=vp_id, branch=worktree,
                                        head=partial.get("session_id"), status_porcelain="",
                                        cause=code.value, profile_alias=alias,
                                        session_id=partial.get("session_id"),
                                        tests=[])
                cid = self.store.save_checkpoint(ckpt.to_dict())
                tele.checkpoint_ids.append(cid)
                handoff = build_handoff(
                    project_id=project_id, vp_id=vp_id, goal=current_job.goal,
                    immutable_constraints=current_job.constraints,
                    baseline_head=None, current_head=partial.get("session_id"),
                    changed_files=[], commands=[{"cmd": "adapter.start", "outcome": code.value}],
                    failures=[exc.classified.to_dict()],
                    acceptance_matrix=[{"criterion": "continue after " + code.value, "status": "PENDING"}],
                    decisions=[f"switch from {alias} due to {code.value}"],
                    exact_next_action="fresh session на совместимом профиле с этим handoff",
                    prohibited_actions=current_job.prohibited_actions,
                    from_profile_alias=alias, progress=partial)
                hid = self.store.save_handoff(handoff.to_dict())
                tele.handoff_ids.append(hid)
                self.store.upsert_run(run_id=run_id, state=self._state_for(code).value,
                                      project_id=project_id, profile_alias=alias, error_code=code.value)
                self.store.audit(f"run.{code.value.lower()}",
                                 f"профиль {alias}: {code.value}; checkpoint {cid}, handoff {hid}")

                # освобождаем аренду ДО попытки взять следующую (никогда два writer)
                self.leases.release(lease.id)
                if self.leases.active_count(project_id, worktree) != 0:
                    tele.single_writer_ok = False

                tele.switches.append({"from": alias, "code": code.value, "handoff": hid, "checkpoint": cid})

                if code in SWITCHABLE and i + 1 < len(candidates):
                    # верификация handoff перед продолжением: факт побеждает
                    vr = verify_handoff(handoff, actual_head=partial.get("session_id"))
                    if not vr.ok:
                        self.store.audit("handoff.mismatch", "; ".join(vr.mismatches))
                    current_job = self._continuation_job(current_job, partial, candidates[i + 1].profile.alias,
                                                          handoff_ref=hid)
                    continue
                raise
            else:
                # успех: освобождаем аренду, фиксируем результат
                self.leases.release(lease.id)
                if self.leases.active_count(project_id, worktree) != 0:
                    tele.single_writer_ok = False
                self.store.upsert_run(run_id=run_id, state=RunState.SUCCEEDED.value,
                                      project_id=project_id, profile_alias=alias,
                                      session_id=result.result.session_id)
                self.store.add_evidence(run_id, "structured_output",
                                        str(result.result.structured_output),
                                        content_hash=result.result.output_hash or "")
                self.store.audit("run.succeeded", f"профиль {alias}: run {run_id} SUCCEEDED")
                return result, tele

        assert last_error is not None
        raise last_error

    def _state_for(self, code: ErrorCode) -> RunState:
        if code == ErrorCode.RATE_LIMITED:
            return RunState.RATE_LIMITED
        if code == ErrorCode.USER_INTERRUPTED:
            return RunState.INTERRUPTED
        return RunState.FAILED

    def _continuation_job(self, job: JobPackage, progress: dict, next_alias: str, *, handoff_ref: str) -> JobPackage:
        new_inputs = copy.deepcopy(job.inputs)
        new_inputs["resume_from"] = progress.get("processed_index", 0)
        new_inputs["partial_sum"] = progress.get("partial_sum", 0)
        new_inputs["processed"] = progress.get("processed", {})
        new_inputs["worker_label"] = next_alias
        cont = JobPackage(
            goal=job.goal, role=job.role, provider=job.provider,
            source_of_truth=job.source_of_truth, acceptance_criteria=job.acceptance_criteria,
            constraints=job.constraints, prohibited_actions=job.prohibited_actions,
            output_schema_ref=job.output_schema_ref, inputs=new_inputs, handoff_ref=handoff_ref,
        )
        return cont

    # --- восстановление после рестарта Core --------------------------------
    def recover_after_core_restart(self, project_id: str) -> dict:
        """Пометить активные runs INTERRUPTED и вернуть последний checkpoint (§7.5)."""

        interrupted = self.store.mark_interrupted_active()
        for rid in interrupted:
            self.store.audit("core.restart.interrupted", f"run {rid} помечен INTERRUPTED при рестарте")
        ckpt = self.store.latest_checkpoint(project_id)
        return {"interrupted_runs": interrupted, "checkpoint": ckpt}


def AtlasError_from(code: ErrorCode, msg: str) -> AtlasError:
    from .errors import ClassifiedError, next_action_for
    from .redaction import redact
    return AtlasError(ClassifiedError(code=code, evidence=redact(msg), retryable=False,
                                      next_action=next_action_for(code), raw_len=len(msg)))
