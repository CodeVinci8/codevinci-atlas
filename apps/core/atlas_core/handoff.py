"""Checkpoint и HandoffPackage: построение и верификация (Master Spec §16.4, §21).

Инвариант: новый агент сверяет handoff с фактическим Git/DB. Фактическое
состояние побеждает; расхождение фиксируется в audit, а не молча
принимается на веру.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Checkpoint, HandoffPackage


@dataclass
class VerificationReport:
    ok: bool
    mismatches: list[str] = field(default_factory=list)
    effective_head: str | None = None
    note: str = ""


def build_checkpoint(*, project_id: str, vp_id: str, branch: str, head: str | None,
                     status_porcelain: str, cause: str, profile_alias: str | None,
                     model: str | None = None, effort: str | None = None,
                     session_id: str | None = None, work_order_id: str | None = None,
                     tests: list[dict] | None = None) -> Checkpoint:
    return Checkpoint(
        project_id=project_id, vp_id=vp_id, work_order_id=work_order_id, branch=branch,
        head=head, status_porcelain=status_porcelain, profile_alias=profile_alias,
        model=model, effort=effort, session_id=session_id, cause=cause, tests=tests or [],
    )


def build_handoff(*, project_id: str, vp_id: str, goal: str, immutable_constraints: list[str],
                  baseline_head: str | None, current_head: str | None, changed_files: list[str],
                  commands: list[dict], failures: list[dict], acceptance_matrix: list[dict],
                  decisions: list[str], exact_next_action: str, prohibited_actions: list[str],
                  from_profile_alias: str | None, session_ids: list[str] | None = None,
                  artifact_refs: list[str] | None = None, progress: dict | None = None) -> HandoffPackage:
    hp = HandoffPackage(
        project_id=project_id, vp_id=vp_id, goal=goal,
        immutable_constraints=immutable_constraints, baseline_head=baseline_head,
        current_head=current_head, changed_files=changed_files, commands=commands,
        failures=failures, acceptance_matrix=acceptance_matrix, decisions=decisions,
        exact_next_action=exact_next_action, prohibited_actions=prohibited_actions,
        from_profile_alias=from_profile_alias, session_ids=session_ids or [],
        artifact_refs=artifact_refs or [],
    )
    # Прогресс продолжения хранится в отдельном поле, чтобы новая сессия
    # (в т.ч. после рестарта) восстановила состояние из persisted handoff.
    if progress is not None:
        hp.progress = progress
    return hp


def verify_handoff(handoff: HandoffPackage, *, actual_head: str | None,
                   actual_changed_files: list[str] | None = None,
                   max_age_s: float | None = None) -> VerificationReport:
    """Сверить handoff с фактическим состоянием. Фактическое побеждает."""

    mismatches: list[str] = []
    if handoff.current_head != actual_head:
        mismatches.append(
            f"HEAD в handoff ({handoff.current_head}) != фактического ({actual_head})")
    if actual_changed_files is not None and set(handoff.changed_files) != set(actual_changed_files):
        mismatches.append("список изменённых файлов расходится с фактом")
    if max_age_s is not None:
        from datetime import datetime, timezone
        created = datetime.strptime(handoff.created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age > max_age_s:
            mismatches.append(f"handoff устарел ({age:.0f}s > {max_age_s:.0f}s)")
    return VerificationReport(
        ok=not mismatches,
        mismatches=mismatches,
        effective_head=actual_head,  # факт всегда побеждает
        note="Фактическое состояние принято за истину; расхождения записаны в audit." if mismatches else "ok",
    )
