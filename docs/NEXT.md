# NEXT — активный VP и следующий шаг

## Завершено

- **VP-0 — Profile Pool & Live Handoff Proof: ЗАВЕРШЁН, 11/11 PASS**
  ([`vp/VP-0.md`](vp/VP-0.md)). Смёржен в `main` (PR #1, squash `ec72350`).
- **VP-1 — Foundation: ЗАВЕРШЁН, 17/17 PASS** ([`vp/VP-1.md`](vp/VP-1.md)).
  Смёржен в `main` (PR #2, squash `22951b6`). Compose Core/Web + systemd Runner
  + health/migrations/audit + CLI `doctor/backup/status` + RU/EN Web-shell + CI.
- **VP-2 — Project Workspace: ЗАВЕРШЁН, 20/20 PASS** ([`vp/VP-2.md`](vp/VP-2.md)).
  Смёржен в `main` (PR #3, squash `a14472a`). Источники (local Git / GitHub /
  архив / пустой), read-only git baseline, безопасные worktree и writer-аренды,
  Project Overview (RU/EN Ember).

## Активный VP

**VP-3 — Product Map (Master Spec §36)** ([`vp/VP-3.md`](vp/VP-3.md)).
Структурный intake → versioned Draft Brief и Draft Map с truth-status,
поштучный accept/reject решений, approval точной версии Brief и scope-envelope,
Project Map и правдивая Portfolio Map, diff версий, parking lot, экспорт в
Markdown/JSON. Доставляется через PR из `atlas/vp-3-product-map` (в момент
разработки — ещё не смёржен).

## NEXT_ACTION

Owner ревьюит VP-3 на `http://127.0.0.1:3210` (через SSH-туннель): структурный
intake синтетического проекта, Draft Brief с truth-badges, поштучный
accept/reject решений, approval версии Brief, Project/Portfolio Map, diff
версий, parking lot, экспорт MD/JSON. После приёмки и merge VP-3 — VP-4.

## Границы

Один активный VP-гейт (WIP=1). **VP-4 (Work Orders & Context) молча не
начинать** — это отдельное решение владельца после закрытия VP-3. LICENSE —
отдельное решение владельца.
