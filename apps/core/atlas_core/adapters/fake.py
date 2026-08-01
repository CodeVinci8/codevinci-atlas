"""Fake-адаптеры для воспроизводимой приёмки (Master Spec §32.2–§32.3).

Fake-адаптер детерминированно моделирует реальный жизненный цикл: новый
структурный запуск, продолжение по handoff, а также инъекцию сбоев (rate
limit, auth, network, timeout, invalid output, interruption, policy denied).

Модель задачи намеренно проверяема: job несёт список ``work_items``; адаптер
обрабатывает их по одному, накапливая ``partial_sum`` и список обработанных
индексов. Профиль A обрабатывает часть и отдаёт handoff; профиль B
продолжает ровно с того места и завершает. Итоговый ``structured_output``
показывает, какие элементы обработал каждый профиль — это доказывает, что
передача состояния реальна, а не имитируется отчётом агента.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from ..capacity import Capacity, unknown_capacity
from ..contracts import JobPackage, Provider, RunResult, RunState, SessionCapability
from ..errors import AtlasError, ErrorCode, classify
from ..ids import new_id, utcnow_iso
from .base import AdapterResult


@dataclass
class FaultInjection:
    """Управляемые сбои для тестов восстановления."""

    auth_required: bool = False
    auth_expired: bool = False
    policy_denied: bool = False
    network_after: int | None = None
    timeout_after: int | None = None
    rate_limit_after: int | None = None
    interrupt_after: int | None = None
    invalid_output: bool = False


def _output_hash(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


class _FakeBase:
    provider: str = "fake"

    def __init__(self, faults: FaultInjection | None = None, *, authenticated: bool = True):
        self.faults = faults or FaultInjection()
        self.authenticated = authenticated

    # --- capability / auth --------------------------------------------------
    def discover_capabilities(self) -> list[SessionCapability]:
        return [
            SessionCapability.NEW_SESSION,
            SessionCapability.RESUME_BY_ID,
            SessionCapability.FRESH_WITH_HANDOFF,
        ]

    def auth_status(self, root_path: str) -> dict:
        if self.faults.auth_required or not self.authenticated:
            return {"authenticated": False, "state": "AUTH_REQUIRED", "detail": "нет активной сессии"}
        if self.faults.auth_expired:
            return {"authenticated": False, "state": "AUTH_EXPIRED", "detail": "сессия истекла"}
        return {"authenticated": True, "state": "READY", "detail": "ok (redacted)"}

    def capacity(self, root_path: str) -> Capacity:
        # Честный ответ: стабильного источника остатка нет.
        return unknown_capacity()

    # --- argv (для паритета с реальными адаптерами) -------------------------
    def build_start_argv(self, job: JobPackage) -> list[str]:
        return ["<fake>", self.provider, "start", job.job_id]

    def build_resume_argv(self, session_id: str, job: JobPackage) -> list[str]:
        return ["<fake>", self.provider, "resume", session_id]

    # --- исполнение ---------------------------------------------------------
    def _run(
        self,
        job: JobPackage,
        *,
        profile_alias: str,
        session_id: str,
        start_index: int,
        partial_sum: int,
        processed_prev: dict,
        on_progress: Callable[[dict], None] | None,
    ) -> AdapterResult:
        # Ранние сбои до обработки элементов.
        if self.faults.auth_required or not self.authenticated:
            self._raise(ErrorCode.AUTH_REQUIRED, "not authenticated: login required", {"start_index": start_index, "partial_sum": partial_sum})
        if self.faults.auth_expired:
            self._raise(ErrorCode.AUTH_EXPIRED, "token expired, please reauth", {"start_index": start_index, "partial_sum": partial_sum})
        if self.faults.policy_denied:
            self._raise(ErrorCode.POLICY_DENIED, "action outside grant: policy denied", {"start_index": start_index, "partial_sum": partial_sum})
        if self.faults.invalid_output:
            self._raise(ErrorCode.OUTPUT_INVALID, "invalid json: malformed structured output", {"start_index": start_index, "partial_sum": partial_sum})

        items: list[int] = list(job.inputs.get("work_items", []))
        # Сохраняем ВСЕ метки предыдущих исполнителей (иначе вклад A теряется при передаче B).
        processed = {k: list(v) for k, v in processed_prev.items()}
        who = job.inputs.get("worker_label", profile_alias)
        acc = partial_sum
        idx = start_index
        n = len(items)
        for step, idx in enumerate(range(start_index, n), start=1):
            # Сбои по достижении шага (моделируют реальный обрыв в середине).
            self._maybe_fault(step, idx, acc, processed, session_id, on_progress)
            acc += items[idx]
            processed.setdefault(who, [])
            processed[who].append(idx)
            state = {"processed_index": idx + 1, "partial_sum": acc, "processed": processed, "session_id": session_id}
            if on_progress:
                on_progress(state)

        structured = {
            "sum": acc,
            "processed_by": processed,
            "complete": True,
            "items_count": n,
        }
        result = RunResult(
            run_id=new_id("run"),
            state=RunState.SUCCEEDED,
            provider=Provider(self.provider) if self.provider in ("codex", "claude") else Provider.CODEX,
            profile_alias=profile_alias,
            session_id=session_id,
            exit_code=0,
            structured_output=structured,
            output_hash=_output_hash(structured),
            finished_at=utcnow_iso(),
            events=[{"type": "run.completed", "occurred_at": utcnow_iso()}],
        )
        return AdapterResult(result=result, handoff_state={"processed_index": n, "partial_sum": acc, "processed": processed})

    def _maybe_fault(self, step, idx, acc, processed, session_id, on_progress):
        f = self.faults
        state = {"processed_index": idx, "partial_sum": acc, "processed": processed, "session_id": session_id}
        if f.rate_limit_after is not None and step > f.rate_limit_after:
            if on_progress:
                on_progress(state)
            self._raise(ErrorCode.RATE_LIMITED, "429 usage limit reached; reset_at later", state)
        if f.network_after is not None and step > f.network_after:
            if on_progress:
                on_progress(state)
            self._raise(ErrorCode.NETWORK_ERROR, "connection reset by peer", state)
        if f.timeout_after is not None and step > f.timeout_after:
            if on_progress:
                on_progress(state)
            self._raise(ErrorCode.TIMEOUT, "deadline exceeded: timed out", state)
        if f.interrupt_after is not None and step > f.interrupt_after:
            if on_progress:
                on_progress(state)
            self._raise(ErrorCode.USER_INTERRUPTED, "interrupted: sigterm", state)

    def _raise(self, code: ErrorCode, raw: str, partial: dict) -> None:
        classified = classify(raw)
        # Форсируем ожидаемый код (raw специально совпадает с паттерном).
        if classified.code != code:
            from ..errors import ClassifiedError, next_action_for
            from ..redaction import redact
            classified = ClassifiedError(code=code, evidence=redact(raw), retryable=classified.retryable,
                                         next_action=next_action_for(code), raw_len=len(raw))
        err = AtlasError(classified)
        err.partial_state = partial  # type: ignore[attr-defined]
        raise err

    def start(self, job: JobPackage, *, profile_alias: str, root_path: str,
              on_progress: Callable[[dict], None] | None = None) -> AdapterResult:
        session_id = new_id("sess")
        # Продолжение из handoff: восстанавливаем прогресс.
        start_index = int(job.inputs.get("resume_from", 0))
        partial_sum = int(job.inputs.get("partial_sum", 0))
        processed_prev = job.inputs.get("processed", {})
        return self._run(job, profile_alias=profile_alias, session_id=session_id,
                         start_index=start_index, partial_sum=partial_sum,
                         processed_prev=processed_prev, on_progress=on_progress)

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str,
               on_progress: Callable[[dict], None] | None = None) -> AdapterResult:
        start_index = int(job.inputs.get("resume_from", 0))
        partial_sum = int(job.inputs.get("partial_sum", 0))
        processed_prev = job.inputs.get("processed", {})
        return self._run(job, profile_alias=profile_alias, session_id=session_id,
                         start_index=start_index, partial_sum=partial_sum,
                         processed_prev=processed_prev, on_progress=on_progress)


class FakeCodexAdapter(_FakeBase):
    provider = "codex"


class FakeClaudeAdapter(_FakeBase):
    provider = "claude"
