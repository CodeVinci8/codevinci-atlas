"""Рендер экспорта Product Map: JSON и человекочитаемый Markdown (§36 Phase H).

Экспорт детерминирован для одной и той же принятой версии (кроме ``_generated``).
Не содержит credentials/env-дампов/raw auth-путей/безграничного содержимого
репо/небезопасного HTML: содержимое уже redacted на входе, а рендер экранирует
опасные последовательности и ещё раз прогоняет redaction.
"""

from __future__ import annotations

import json

from .redaction import redact

_TRUTH_RU = {
    "VERIFIED": "проверено", "OWNER_PROVIDED": "от владельца", "INFERRED": "выведено",
    "HYPOTHESIS": "гипотеза", "STALE": "устарело", "UNKNOWN": "неизвестно",
}


def render_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _md_escape(text: str) -> str:
    # Экранируем HTML-угловые скобки и обрезаем; контент — данные, не разметка.
    return redact(str(text)).replace("<", "&lt;").replace(">", "&gt;")


def _line(label: str, value) -> str:
    v = _md_escape(value) if value not in (None, "", []) else "—"
    return f"- **{label}:** {v}\n"


def _list_block(title: str, items: list) -> str:
    out = f"\n### {title}\n\n"
    if not items:
        return out + "—\n"
    for it in items:
        out += f"- {_md_escape(it)}\n"
    return out


def render_markdown(payload: dict) -> str:
    p = payload.get("project", {})
    b = payload.get("brief", {})
    c = b.get("content", {})
    env = b.get("envelope", {})
    gen = payload.get("_generated", {})

    out = f"# Product Map — {_md_escape(p.get('name', ''))}\n\n"
    out += _line("Проект", p.get("id"))
    out += _line("Состояние проекта", p.get("status"))
    out += _line("Версия Brief", f"v{b.get('version')} ({b.get('status')})")
    out += _line("Brief content-hash", b.get("content_hash"))
    out += _line("Envelope hash", b.get("envelope_hash"))
    out += _line("Активный VP", payload.get("active_vp"))
    out += _line("Схема экспорта", payload.get("schema_version"))
    out += _line("Payload-hash", gen.get("payload_hash"))
    out += _line("Сгенерировано (метаданные)", gen.get("generated_at"))

    out += "\n## Brief\n\n"
    out += _line("Продукт (одна строка)", c.get("product_statement"))
    out += _line("Пользователь и проблема", c.get("user_and_problem"))
    out += _line("Текущая альтернатива", c.get("current_alternative"))
    out += _line("Обещанный результат", c.get("promised_result"))
    out += _line("Основной сценарий", c.get("main_scenario"))
    out += _line("Метрика/наблюдение успеха", c.get("success_metric"))
    out += _line("Минимальная валидация", c.get("minimum_validation"))
    out += _line("Критерий остановки", c.get("stop_criterion"))

    out += "\n### Подтверждённые факты (с truth-status)\n\n"
    facts = c.get("confirmed_facts", [])
    if not facts:
        out += "—\n"
    for f in facts:
        ts = _TRUTH_RU.get(f.get("truth_status"), f.get("truth_status", ""))
        ev = f.get("evidence_ref") or "—"
        out += f"- [{ts}] {_md_escape(f.get('text', ''))} (evidence: {_md_escape(ev)})\n"

    out += "\n### Гипотезы\n\n"
    hyps = c.get("hypotheses", [])
    if not hyps:
        out += "—\n"
    for h in hyps:
        ts = _TRUTH_RU.get(h.get("truth_status"), h.get("truth_status", ""))
        out += f"- [{ts}] {_md_escape(h.get('text', ''))}\n"

    out += _list_block("MVP scope", c.get("mvp_scope", []))
    out += _list_block("Вне scope", c.get("out_of_scope", []))
    out += _list_block("Риски", c.get("risks", []))

    out += "\n## Scope envelope\n\n"
    out += _list_block("В scope", env.get("in_scope", []))
    out += _list_block("Вне scope", env.get("out_of_scope", []))
    out += _list_block("Ограничения", env.get("constraints", []))
    out += _line("Примечание к границе", env.get("boundary_note"))

    out += "\n## Решения\n\n"
    decisions = payload.get("decisions", [])
    if not decisions:
        out += "—\n"
    for d in decisions:
        req = "required" if d.get("required") else "optional"
        ts = _TRUTH_RU.get(d.get("truth_status"), d.get("truth_status", ""))
        out += (f"- **{_md_escape(d.get('title', ''))}** — {d.get('status')} "
                f"({req}, {ts})"
                + (f" — {_md_escape(d.get('note'))}" if d.get("note") else "") + "\n")

    out += "\n## Project Map\n\n"
    mp = payload.get("map", {})
    meta = mp.get("meta")
    if meta:
        out += _line("Версия карты", f"v{meta.get('version')} ({meta.get('status')})")
        out += _line("Map content-hash", meta.get("content_hash"))
    out += "\n### Узлы\n\n"
    for n in mp.get("nodes", []):
        ts = _TRUTH_RU.get(n.get("truth_status"), n.get("truth_status", ""))
        out += f"- `{_md_escape(n.get('node_type'))}` **{_md_escape(n.get('title', ''))}** [{ts}]\n"
    out += "\n### Рёбра\n\n"
    for e in mp.get("edges", []):
        out += f"- {_md_escape(e.get('src_key'))} —{_md_escape(e.get('edge_type'))}→ {_md_escape(e.get('dst_key'))}\n"

    out += "\n## Parking lot\n\n"
    parking = payload.get("parking_lot", [])
    if not parking:
        out += "—\n"
    for x in parking:
        cond = x.get("return_condition") or "—"
        out += (f"- **{_md_escape(x.get('title', ''))}** ({x.get('status')}) — "
                f"причина: {_md_escape(x.get('reason') or '—')}; возврат: {_md_escape(cond)}\n")

    approval = payload.get("approval")
    out += "\n## Approval\n\n"
    if approval:
        out += _line("Approval ID", approval.get("id"))
        out += _line("Brief hash", approval.get("brief_hash"))
        out += _line("Map version", approval.get("map_version_id"))
        out += _line("Envelope hash", approval.get("envelope_hash"))
        out += _line("Decisions hash", approval.get("decisions_hash"))
        out += _line("Утверждено", approval.get("created_at"))
    else:
        out += "Версия ещё не утверждена владельцем.\n"

    return out


__all__ = ["render_json", "render_markdown"]
