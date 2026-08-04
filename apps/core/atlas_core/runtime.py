"""Производственный запуск Run: registry-driven диспетч Builder-шага (VP-7, §17).

Ранее ``POST /api/v1/runs`` лишь вставлял строку Run (QUEUED), а маршрутизация
(``route_profile``/``claude_pool``) жила только в VP-5 acceptance/тестах с
caller-supplied кандидатами. Этот модуль — минимальный КОРРЕКТНЫЙ production-путь:

* кандидаты Builder берутся из DURABLE-реестра (``agent_profiles`` + последняя
  ёмкость), а не из хардкодного списка;
* Builder выбирается через registry-driven ``claude_pool.select_builder``;
* одна run-lease (один writer на профиль, UNIQUE(profile_id, released_at));
* реальный (или инъектированный на границе) Builder-шаг под изолированным профилем;
* provider-session и router-decision персистятся (без raw transcript/credentials);
* точный rate-limit → ``claude_pool.handle_rate_limit`` + РОВНО один switch на
  другой eligible профиль; иначе OWNER_REQUIRED (без retry-loop/фейкового handoff);
* durable lease/session → рестарт не дублирует writer/side-effect.

Границы (§30): не читаем credentials; в session-запись идут только safe-поля.
Реальные provider-вызовы — bounded; adapter — точка инъекции для детерминированных
E2E-тестов (тест входит через ``start_builder_run``, а не через ``select_builder``)."""

from __future__ import annotations

from . import audit
from .claude_pool import handle_rate_limit, select_builder
from .contracts import JobPackage, Provider, Role
from .errors import AtlasError, ErrorCode
from .router import Candidate

_BUILDER_ROLE = "builder"


def build_builder_candidates(profiles: list[dict]) -> list[Candidate]:
    """Registry-driven кандидаты Builder из durable-профилей (claude-пул).
    capacity_status/remaining_min/fresh берутся из последнего наблюдения ёмкости."""
    from .claude_pool import _is_fresh, _is_pool_member, _remaining_min
    cands: list[Candidate] = []
    for p in profiles:
        if not _is_pool_member(p):
            continue
        cap = p.get("capacity") or {}
        cands.append(Candidate(
            alias=p["alias"], provider="claude", state=p.get("state", "UNCONFIGURED"),
            capacity_status=cap.get("status", "UNKNOWN"), fresh=_is_fresh(cap),
            remaining_min=_remaining_min(cap), schedulable=bool(p.get("schedulable", True))))
    return cands


def _builder_job() -> JobPackage:
    """Bounded Builder JobPackage (без repo/chat/credentials — contract-safe)."""
    return JobPackage(
        goal="VP-7 bounded builder step (registry-routed)", role=Role.BUILDER,
        provider=Provider.CLAUDE, acceptance_criteria=["bounded response"],
        constraints=["read-only tools off", "no repo write"],
        inputs={"timeout_s": 120})


def _ver(run_svc, run_id: str) -> int:
    return run_svc.get_run(run_id)["version"]


def start_builder_run(run_id: str, *, run_svc=None, lease_svc=None, adapter=None,
                      profiles: list[dict] | None = None, profile_roots: dict | None = None,
                      actor: str = "core") -> dict:
    """Производственный диспетч Builder-шага Run (registry-driven). Возвращает
    сводку решения. Вход как из API, так и из E2E-теста (через этот метод, не через
    select_builder напрямую). ``adapter`` инъектируется на границе провайдера.

    Bounded: исходный профиль + РОВНО один switch на другой eligible при rate-limit;
    иначе OWNER_REQUIRED (без retry-loop). Один writer (durable lease). Restart-safe."""
    from .agent_registry import ProfileService
    from .run_leases import RunLeaseService
    from .runs import RunService
    from .settings import load_settings

    run_svc = run_svc or RunService()
    lease_svc = lease_svc or RunLeaseService(load_settings().db_path)
    profiles = profiles if profiles is not None else ProfileService().list_profiles()
    roots = profile_roots or {}
    if adapter is None:
        from .adapters.real_claude import RealClaudeAdapter
        adapter = RealClaudeAdapter()

    if run_svc.get_run(run_id)["state"] != "QUEUED":
        return {"ok": False, "reason": "RUN_NOT_QUEUED", "state": run_svc.get_run(run_id)["state"]}

    excluded: set[str] = set()
    switches = 0
    reached_running = False
    while True:
        # 1. Registry-driven выбор Builder, исключая уже исчерпанные (switch).
        decision = select_builder(profiles, exclude=excluded, actor=actor)
        run_svc.record_router_decision(run_id, decision)
        if not decision.ok:
            frm = run_svc.get_run(run_id)["state"]
            # из QUEUED сначала PREPARING (нельзя QUEUED→OWNER_REQUIRED напрямую).
            if frm == "QUEUED":
                run_svc.transition(run_id, "PREPARING", expected_version=_ver(run_svc, run_id),
                                   reason="dispatch")
            run_svc.transition(run_id, "OWNER_REQUIRED", expected_version=_ver(run_svc, run_id),
                               reason="нет eligible Claude Builder", blocker=decision.reason_code)
            audit.record("runs.dispatch.owner_required",
                         f"run={run_id} reason={decision.reason_code}", actor=actor)
            return {"ok": False, "reason": decision.reason_code, "effective": ""}
        profile = decision.effective_profile
        pid = next((p["id"] for p in profiles if p["alias"] == profile), profile)

        # 2. Аренда (один writer). Конфликт → OWNER_REQUIRED (не второй writer).
        try:
            lease = lease_svc.acquire(profile_id=pid, run_id=run_id, role=_BUILDER_ROLE,
                                      worktree=f"wt-{run_id}", holder=profile)
        except AtlasError as exc:
            frm = run_svc.get_run(run_id)["state"]
            if frm == "QUEUED":
                run_svc.transition(run_id, "PREPARING", expected_version=_ver(run_svc, run_id),
                                   reason="dispatch")
            run_svc.transition(run_id, "OWNER_REQUIRED", expected_version=_ver(run_svc, run_id),
                               reason="профиль уже арендован", blocker="LEASE_CONFLICT")
            return {"ok": False, "reason": "LEASE_CONFLICT", "detail": str(exc)}

        # 3. PREPARING → RUNNING, затем bounded Builder-шаг.
        cur = run_svc.get_run(run_id)["state"]
        if cur in ("QUEUED", "RATE_LIMITED"):
            run_svc.transition(run_id, "PREPARING", expected_version=_ver(run_svc, run_id),
                               reason="dispatch" if cur == "QUEUED" else f"switch → {profile}")
        run_svc.transition(run_id, "RUNNING", expected_version=_ver(run_svc, run_id),
                           reason=f"builder={profile}")
        reached_running = True
        root = roots.get(profile, "")
        try:
            res = adapter.start(_builder_job(), profile_alias=profile, root_path=root)
        except AtlasError as exc:
            code = getattr(getattr(exc, "classified", None), "code", None)
            lease_svc.release(lease.id)  # release-before-switch (не второй writer)
            if code == ErrorCode.RATE_LIMITED:
                run_svc.transition(run_id, "RATE_LIMITED", expected_version=_ver(run_svc, run_id),
                                   reason="builder rate limit")
                hr = handle_rate_limit(profiles, alias=profile, rate_limit_type="five_hour",
                                       actor=actor)
                excluded.add(profile)
                profiles = ProfileService().list_profiles()  # обновить: profile теперь EXHAUSTED
                if switches >= 1 or not hr["ok"]:
                    run_svc.transition(run_id, "OWNER_REQUIRED",
                                       expected_version=_ver(run_svc, run_id),
                                       reason="повторный rate limit / нет второго Claude",
                                       blocker=("RATE_LIMITED_BOUNDED" if switches >= 1
                                                else "NO_ELIGIBLE_PROFILE"))
                    return {"ok": False, "reason": "OWNER_ACTION_REQUIRED", "exhausted": profile,
                            "next_effective": hr.get("next_effective", "")}
                switches += 1
                continue  # РОВНО один switch на другой eligible
            target = ("AUTH_REQUIRED" if code in (ErrorCode.AUTH_REQUIRED, ErrorCode.AUTH_EXPIRED)
                      else "OWNER_REQUIRED")
            run_svc.transition(run_id, target, expected_version=_ver(run_svc, run_id),
                               reason=f"builder:{code.value if code else 'error'}",
                               blocker=f"builder:{code.value if code else 'ERROR'}")
            return {"ok": False, "reason": code.value if code else "ERROR", "effective": profile}

        # 4. Успех: session (safe) + COLLECTING; освобождение аренды.
        sid = res.result.session_id or ""
        run_svc.record_provider_session(run_id, provider="claude", session_id=sid,
                                        role=_BUILDER_ROLE, profile_id=pid)
        run_svc.transition(run_id, "COLLECTING", expected_version=_ver(run_svc, run_id),
                           reason="builder ответил")
        lease_svc.release(lease.id)
        audit.record("runs.dispatch.builder_ok",
                     f"run={run_id} profile={profile} switches={switches}", actor=actor)
        _ = reached_running
        return {"ok": True, "effective": profile, "reason": decision.reason_code,
                "session_present": bool(sid), "switches": switches}
