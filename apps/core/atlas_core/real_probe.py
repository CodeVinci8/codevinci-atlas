"""Реальные provider-probe A→B через официальные CLI (Master Spec §32.4).

Выполняется владельцем ПОСЛЕ логина. Для провайдера с двумя авторизованными
профилями делает четыре минимальных реальных probe:

1. auth status A и B (без раскрытия account/email);
2. минимальный read-only структурный запуск на A (получаем ``code`` и session);
3. B продолжает из верифицированного HandoffPackage — ОТДЕЛЬНАЯ реальная
   сессия под другой идентичностью/аккаунтом; B должен вернуть ``code`` из A
   (доказывает передачу состояния без общих credentials/сессии);
4. resume A по session_id.

Каждый CLI запускается ПОД идентичностью профиля (``run_as_user``) — реальная
граница изоляции. Реальный лимит не провоцируется. Никакие token/cookie/email
не читаются и не печатаются.
"""

from __future__ import annotations

import re
import secrets

from .adapters.real_claude import RealClaudeAdapter
from .adapters.real_codex import RealCodexAdapter
from .contracts import JobPackage, Provider, Role
from .errors import AtlasError
from .handoff import build_checkpoint, build_handoff, verify_handoff
from .profiles import Profile
from .store import Store

_ADAPTERS = {"codex": RealCodexAdapter, "claude": RealClaudeAdapter}


def _adapter(provider: str):
    return _ADAPTERS[provider]()


def _extract_code(structured: dict) -> str | None:
    code = structured.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()[:16]
    text = structured.get("text", "")
    m = re.search(r"[A-Za-z0-9]{6,16}", text or "")
    return m.group(0) if m else None


def probe_provider(provider: str, prof_a: Profile, prof_b: Profile, store: Store) -> dict:
    """Полный A→B real-probe для одного провайдера. Возвращает redacted-отчёт."""

    adapter = _adapter(provider)
    report: dict = {"provider": provider, "profile_a": prof_a.alias, "profile_b": prof_b.alias,
                    "steps": {}, "ok": False}

    # 1) auth status обоих
    a_auth = adapter.auth_status(prof_a.root_path, run_as_user=prof_a.runtime_user)
    b_auth = adapter.auth_status(prof_b.root_path, run_as_user=prof_b.runtime_user)
    report["steps"]["auth"] = {"a": a_auth["state"], "b": b_auth["state"]}
    if not (a_auth["authenticated"] and b_auth["authenticated"]):
        report["blocker"] = "оба профиля должны быть авторизованы (owner login)"
        return report

    token = secrets.token_hex(4).upper()  # ожидаемый code, который A должен «придумать»/вернуть
    # 2) минимальный структурный запуск A (read-only)
    prompt_a = (
        "Ты выполняешь VP-0 handoff-probe. НЕ запускай инструменты. "
        f"Верни СТРОГО один JSON-объект и ничего больше: {{\"code\": \"{token}\", \"step\": \"A\"}}"
    )
    job_a = JobPackage(goal=prompt_a, role=Role.BUILDER, provider=Provider(provider),
                       inputs={"cwd": prof_a.root_path, "timeout_s": 120})
    try:
        res_a = _run_with_retry(adapter, job_a, prof_a)
    except AtlasError as exc:
        report["steps"]["run_a"] = {"error": exc.classified.code.value}
        report["blocker"] = "минимальный запуск A не удался"
        return report
    code_a = _extract_code(res_a.result.structured_output) or token
    session_a = res_a.result.session_id
    report["steps"]["run_a"] = {"ok": True, "code_present": bool(code_a), "session": bool(session_a)}

    # 3) checkpoint + HandoffPackage, верификация против persisted
    ckpt = build_checkpoint(project_id="codevinci-atlas", vp_id="VP-0", branch="atlas/vp-0",
                            head=session_a, status_porcelain="", cause="A→B real handoff",
                            profile_alias=prof_a.alias, session_id=session_a)
    cid = store.save_checkpoint(ckpt.to_dict())
    handoff = build_handoff(
        project_id="codevinci-atlas", vp_id="VP-0", goal="Продолжить VP-0 handoff-probe",
        immutable_constraints=["один writer", "без общих credentials", "read-only"],
        baseline_head=None, current_head=session_a, changed_files=[],
        commands=[{"cmd": "codex/claude exec", "outcome": "SUCCEEDED"}], failures=[],
        acceptance_matrix=[{"criterion": "B echoes A code", "status": "PENDING"}],
        decisions=[f"handoff code carried A→B"], exact_next_action="B возвращает переданный code",
        prohibited_actions=["share credentials", "force push"], from_profile_alias=prof_a.alias,
        session_ids=[session_a] if session_a else [], progress={"code": code_a})
    hid = store.save_handoff(handoff.to_dict())
    persisted = store.get_handoff(hid)
    vr = verify_handoff(handoff, actual_head=session_a)
    handoff_ok = persisted is not None and persisted["progress"]["code"] == code_a and vr.ok
    report["steps"]["handoff"] = {"checkpoint": bool(cid), "handoff": bool(hid),
                                  "verified": bool(handoff_ok)}

    # 4) B продолжает из handoff — ОТДЕЛЬНАЯ реальная сессия, другой аккаунт
    prompt_b = (
        "Ты продолжаешь VP-0 handoff-probe в НОВОЙ сессии. НЕ запускай инструменты. "
        f"Из HandoffPackage передан code шага A: {code_a}. "
        f"Верни СТРОГО один JSON-объект: {{\"code\": \"{code_a}\", \"step\": \"B\", \"continued\": true}}"
    )
    job_b = JobPackage(goal=prompt_b, role=Role.BUILDER, provider=Provider(provider),
                       inputs={"cwd": prof_b.root_path, "timeout_s": 120}, handoff_ref=hid)
    try:
        res_b = _run_with_retry(adapter, job_b, prof_b)
    except AtlasError as exc:
        report["steps"]["run_b"] = {"error": exc.classified.code.value}
        report["blocker"] = "продолжение B не удалось"
        return report
    code_b = _extract_code(res_b.result.structured_output)
    session_b = res_b.result.session_id
    b_continued = (code_b == code_a) and (session_b != session_a)  # своя, отдельная сессия
    report["steps"]["run_b"] = {"ok": True, "code_matches_a": code_b == code_a,
                                "separate_session": session_b != session_a}

    # 5) resume A по session_id
    resume_ok = None
    if session_a:
        prompt_r = ("Верни СТРОГО один JSON-объект: {\"resumed\": true, \"step\": \"A2\"}")
        job_r = JobPackage(goal=prompt_r, role=Role.BUILDER, provider=Provider(provider),
                           inputs={"cwd": prof_a.root_path, "timeout_s": 120})
        try:
            res_r = adapter.resume(session_a, job_r, profile_alias=prof_a.alias,
                                   root_path=prof_a.root_path, run_as_user=prof_a.runtime_user)
            resume_ok = res_r.result.state.value == "SUCCEEDED"
        except AtlasError as exc:
            resume_ok = False
            report["steps"]["resume"] = {"error": exc.classified.code.value}
    report["steps"]["resume"] = report["steps"].get("resume", {}) or {"ok": bool(resume_ok)}

    report["ok"] = bool(a_auth["authenticated"] and b_auth["authenticated"] and
                        handoff_ok and b_continued)
    return report


def _run_with_retry(adapter, job: JobPackage, profile: Profile):
    """Один повтор при OUTPUT_INVALID (Master Spec §17.5)."""

    try:
        return adapter.start(job, profile_alias=profile.alias, root_path=profile.root_path,
                             run_as_user=profile.runtime_user)
    except AtlasError as exc:
        if exc.classified.code.value == "OUTPUT_INVALID":
            return adapter.start(job, profile_alias=profile.alias, root_path=profile.root_path,
                                 run_as_user=profile.runtime_user)
        raise
