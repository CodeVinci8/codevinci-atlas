# NEXT — активный VP и следующий шаг

## Завершено

- **VP-0 — Profile Pool & Live Handoff Proof: ЗАВЕРШЁН, 11/11 PASS**
  ([`vp/VP-0.md`](vp/VP-0.md)). Смёржен в `main` (PR #1, squash `ec72350`).
- **VP-1 — Foundation: ЗАВЕРШЁН, 17/17 PASS** ([`vp/VP-1.md`](vp/VP-1.md)).
  Смёржен в `main` (PR #2, squash `22951b6`).
- **VP-2 — Project Workspace: ЗАВЕРШЁН, 20/20 PASS** ([`vp/VP-2.md`](vp/VP-2.md)).
  Смёржен в `main` (PR #3, squash `a14472a`).
- **VP-3 — Product Map: ЗАВЕРШЁН, 26/26 PASS** ([`vp/VP-3.md`](vp/VP-3.md)).
  Смёржен в `main` (PR #4, squash `07ed6f4`). Структурный intake, truth-status,
  версии Brief и решения, Project/Portfolio Map, diff, scope-envelope, parking
  lot, экспорт MD/JSON. Живая БД — на миграции `0003_product_map`.
- **VP-4 — Work Orders & Context: ЗАВЕРШЁН, 26/26 PASS** ([`vp/VP-4.md`](vp/VP-4.md)).
  Смёржен в `main` (PR #6, squash `7a3f82d`, CI head `280ee35`). VP Spec из
  принятого Brief/Map, Work Orders (один writer, concurrency, идемпотентность),
  оптимизатор с сохранением критериев, bounded JobPackage, checkpoint/handoff,
  свежая изолированная реконструкция внутри Core-образа, ротация одного writer,
  compact fail-closed, Work Orders UI RU/EN.
- **VP-5 — Agent Pipeline: ЗАВЕРШЁН, 26/26 PASS + реальный provider-E2E**
  ([`vp/VP-5.md`](vp/VP-5.md)). Смёржен в `main` (**PR #9**, squash `afefa61`,
  CI head `86c504e`). Конвейер Codex Planner → Claude Builder → независимый Codex
  Reviewer: durable Runs (lifecycle/идемпотентность/optimistic), router без
  silent fallback, один writer (worktree+profile lease), три раздельные
  session-семантики, bounded rate-limit/auth/interruption-recovery, Profiles MVP,
  full-width Pulse, RU/EN. Детерминированная приёмка `run_vp5_acceptance.py` —
  **26/26**; реальный E2E `run_vp5_real_e2e.py` — реальный артефакт `calc.py`
  (3/6 подписочных вызовов, PASS). Живая БД — на **`0005_agent_pipeline`**.

## Активный VP

**Нет активного VP.** VP-0…VP-5 завершены и смёржены. Следующий гейт — **VP-6
(Review & Quality, Master Spec §39)**, но он **не начат** и стартует только по
отдельному решению владельца.

## NEXT_ACTION

Owner проводит финальный обзор **VP-5 Runs/Profiles/Pulse** на
`http://127.0.0.1:3210` (SSH-туннель): вкладка «Запуски» — lifecycle-таймлайн,
requested/effective модель+профиль, router reason, роли, provider-сессии,
аренда, вердикт Reviewer, next action; «Профили» — карточки/таблица, честная
ёмкость UNKNOWN/STALE; «Пульс» — full-width система; RU/EN-переключатель.

## Границы

Активного VP-гейта нет (WIP=0). **VP-6…VP-9 не начинать** — отдельное решение
владельца. Полный операционный console Profiles/Pulse (40 профилей) — VP-8.
Cookie-import — `UNSUPPORTED`. LICENSE — отдельное решение владельца.
