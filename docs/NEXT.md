# NEXT — активный VP и следующий шаг

## Завершено

- **VP-0 — Profile Pool & Live Handoff Proof: ЗАВЕРШЁН, 11/11 PASS**
  ([`vp/VP-0.md`](vp/VP-0.md)). Смёржен в `main` (PR #1).
- **VP-1 — Foundation: ЗАВЕРШЁН, 17/17 PASS** ([`vp/VP-1.md`](vp/VP-1.md)).
  Смёржен в `main` (PR #2, squash). Compose Core/Web + systemd Runner +
  health/migrations/audit + CLI `doctor/backup/status` + RU/EN Web-shell + CI.

## Активный VP

**VP-2 — Project Workspace (Master Spec §35)** ([`vp/VP-2.md`](vp/VP-2.md)).
Подключение проектов (local Git / GitHub / архив / пустой), read-only git
baseline, безопасные worktree и writer-аренды, Project Overview (RU/EN Ember).
Доставляется через PR из `atlas/vp-2-project-workspace` (в момент разработки —
ещё не смёржен).

## NEXT_ACTION

Owner ревьюит VP-2 на `http://127.0.0.1:3210` (через SSH-туннель): подключение
синтетического проекта, git baseline, dirty-предупреждение, инструкции,
worktree/аренда, точный next action. После приёмки и merge VP-2 — VP-3.

## Границы

Один активный VP-гейт (WIP=1). **VP-3 (Product Map) молча не начинать** —
это отдельное решение владельца после закрытия VP-2. LICENSE — отдельное
решение владельца.
