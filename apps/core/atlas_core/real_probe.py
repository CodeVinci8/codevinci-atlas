"""Реальные provider-probe A→B через официальные CLI (Master Spec §32.4).

Выполняется владельцем ПОСЛЕ логина. Для провайдера с двумя авторизованными
профилями делает независимо проверяемый A→B:

1. auth status A и B (без раскрытия account/email);
2. профиль A выполняет ПЕРВУЮ половину ограниченной задачи и возвращает
   структурный ``partial`` + ``nonce`` (read-only, sandbox);
3. Atlas сохраняет checkpoint и HandoffPackage, верифицирует их против
   persisted-состояния БД (хеши);
4. профиль B (ДРУГАЯ идентичность и аккаунт, отдельная новая CLI-сессия)
   получает только ограниченный JobPackage + HandoffPackage и ВЫЧИСЛЯЕТ
   ``final = partial + addend_b``, эхом возвращая ``nonce``;
5. проверка: ``final`` арифметически верен (independently checkable), ``nonce``
   совпал, сессия B ≠ сессии A (нет общей нативной сессии/credentials).

Каждый CLI запускается под идентичностью профиля с его исполняемым файлом.
Реальный лимит не провоцируется. Никакие token/cookie/email/account не
читаются и не печатаются. В evidence — только aliases, redacted session-хеши,
хеши checkpoint/handoff, ограниченные числовые результаты, команды и redacted
исходы.
"""

from __future__ import annotations

import hashlib
import json
import random
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


def _sha(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8") \
        if not isinstance(obj, (str, bytes)) else (obj.encode() if isinstance(obj, str) else obj)
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _sess_hash(sid: str | None) -> str | None:
    """Redacted-хеш session id (не раскрывает сам id)."""
    if not sid:
        return None
    return "sha256:" + hashlib.sha256(sid.encode()).hexdigest()[:16]


def _num(structured: dict, key: str):
    v = structured.get(key)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        if m:
            return int(m.group(0))
    # иногда модель кладёт число в text
    m = re.search(r"-?\d+", structured.get("text", "") or "")
    return int(m.group(0)) if m else None


def _run(adapter, job: JobPackage, profile: Profile, *, retry_invalid: bool = True):
    """Запуск под идентичностью/исполняемым файлом профиля; 1 повтор при OUTPUT_INVALID."""
    try:
        return adapter.start(job, profile_alias=profile.alias, root_path=profile.root_path,
                             executable=profile.executable_path, run_as_user=profile.runtime_user)
    except AtlasError as exc:
        if retry_invalid and exc.classified.code.value == "OUTPUT_INVALID":
            return adapter.start(job, profile_alias=profile.alias, root_path=profile.root_path,
                                 executable=profile.executable_path, run_as_user=profile.runtime_user)
        raise


def probe_provider(provider: str, prof_a: Profile, prof_b: Profile, store: Store) -> dict:
    adapter = _adapter(provider)
    rep: dict = {"provider": provider, "profile_a": prof_a.alias, "profile_b": prof_b.alias,
                 "steps": {}, "ok": False}

    # 1) auth
    a_auth = adapter.auth_status(prof_a.root_path, executable=prof_a.executable_path,
                                 run_as_user=prof_a.runtime_user)
    b_auth = adapter.auth_status(prof_b.root_path, executable=prof_b.executable_path,
                                 run_as_user=prof_b.runtime_user)
    rep["steps"]["auth"] = {"a": a_auth["state"], "b": b_auth["state"]}
    if not (a_auth["authenticated"] and b_auth["authenticated"]):
        rep["blocker"] = "оба профиля должны быть авторизованы"
        return rep

    # ограниченная задача: final = n1 + n2 + n3 ; A даёт partial=n1+n2, B даёт final
    n1, n2, n3 = random.randint(2, 9), random.randint(2, 9), random.randint(2, 9)
    nonce = secrets.token_hex(3).upper()
    expected_partial = n1 + n2
    expected_final = expected_partial + n3

    prompt_a = (
        "Ты выполняешь шаг A ограниченной задачи. Не используй инструменты. "
        f"Вычисли partial = {n1} + {n2}. "
        f"Верни СТРОГО один JSON и ничего больше: {{\"partial\": <число>, \"nonce\": \"{nonce}\", \"step\": \"A\"}}"
    )
    job_a = JobPackage(goal=prompt_a, role=Role.BUILDER, provider=Provider(provider),
                       inputs={"cwd": prof_a.root_path, "timeout_s": 150})
    input_a_hash = _sha({"n1": n1, "n2": n2, "nonce": nonce})
    try:
        res_a = _run(adapter, job_a, prof_a)
    except AtlasError as exc:
        rep["steps"]["run_a"] = {"error": exc.classified.code.value}
        rep["blocker"] = "запуск A не удался"
        return rep
    partial = _num(res_a.result.structured_output, "partial")
    session_a = res_a.result.session_id
    a_correct = partial == expected_partial
    rep["steps"]["run_a"] = {"ok": True, "partial_correct": a_correct,
                             "session_hash": _sess_hash(session_a),
                             "output_hash": _sha(res_a.result.structured_output),
                             "input_hash": input_a_hash}
    if not a_correct or not session_a:
        rep["blocker"] = "A не дал корректный partial/сессию"
        return rep

    # 3) checkpoint + handoff, верификация против persisted
    ckpt = build_checkpoint(project_id="codevinci-atlas", vp_id="VP-0", branch="atlas/vp-0",
                            head=session_a, status_porcelain="", cause=f"{provider} A→B real",
                            profile_alias=prof_a.alias, session_id=session_a)
    cid = store.save_checkpoint(ckpt.to_dict())
    handoff = build_handoff(
        project_id="codevinci-atlas", vp_id="VP-0", goal="Завершить ограниченную задачу шагом B",
        immutable_constraints=["один writer", "без общих credentials", "read-only sandbox"],
        baseline_head=None, current_head=session_a, changed_files=[],
        commands=[{"cmd": f"{provider} exec/start A", "outcome": "SUCCEEDED"}], failures=[],
        acceptance_matrix=[{"criterion": "B computes final from A partial", "status": "PENDING"}],
        decisions=["partial и nonce переданы A→B без общей сессии"],
        exact_next_action="B вычисляет final = partial + addend",
        prohibited_actions=["share credentials", "resume A session with B creds", "force push"],
        from_profile_alias=prof_a.alias, session_ids=[session_a],
        progress={"partial": partial, "nonce": nonce, "addend_b": n3})
    hid = store.save_handoff(handoff.to_dict())
    persisted = store.get_handoff(hid)
    vr = verify_handoff(handoff, actual_head=session_a)
    handoff_ok = (persisted is not None and persisted["progress"]["partial"] == partial
                  and persisted["progress"]["nonce"] == nonce and vr.ok)
    rep["steps"]["handoff"] = {"checkpoint_hash": _sha(ckpt.to_dict()),
                               "handoff_hash": _sha(persisted),
                               "verified_against_persisted": bool(handoff_ok)}
    if not handoff_ok:
        rep["blocker"] = "handoff не верифицирован против persisted"
        return rep

    # 4) B продолжает — ОТДЕЛЬНАЯ новая сессия, другой аккаунт/идентичность
    prompt_b = (
        "Ты выполняешь шаг B, продолжая работу шага A в НОВОЙ сессии. Не используй инструменты. "
        f"Из HandoffPackage передано: partial = {partial}, addend = {n3}, nonce = \"{nonce}\". "
        f"Вычисли final = partial + addend. "
        f"Верни СТРОГО один JSON: {{\"final\": <число>, \"nonce\": \"{nonce}\", \"step\": \"B\"}}"
    )
    job_b = JobPackage(goal=prompt_b, role=Role.BUILDER, provider=Provider(provider),
                       inputs={"cwd": prof_b.root_path, "timeout_s": 150}, handoff_ref=hid)
    try:
        res_b = _run(adapter, job_b, prof_b)
    except AtlasError as exc:
        rep["steps"]["run_b"] = {"error": exc.classified.code.value}
        rep["blocker"] = "продолжение B не удалось"
        return rep
    final = _num(res_b.result.structured_output, "final")
    nonce_b = res_b.result.structured_output.get("nonce")
    session_b = res_b.result.session_id
    b_correct = final == expected_final
    separate_session = bool(session_b) and session_b != session_a
    nonce_match = nonce_b == nonce
    rep["steps"]["run_b"] = {"ok": True, "final_correct": b_correct, "nonce_match": nonce_match,
                             "separate_session": separate_session,
                             "session_hash": _sess_hash(session_b),
                             "output_hash": _sha(res_b.result.structured_output)}

    # независимая проверка арифметики (evidence чисел ограничен)
    rep["verification"] = {"expected_partial": expected_partial, "a_partial": partial,
                           "expected_final": expected_final, "b_final": final,
                           "b_used_a_contribution": b_correct and a_correct,
                           "no_shared_session": separate_session}
    rep["ok"] = bool(a_correct and handoff_ok and b_correct and nonce_match and separate_session)
    if not rep["ok"] and "blocker" not in rep:
        rep["blocker"] = "A→B не полностью подтверждён (см. verification)"
    return rep
