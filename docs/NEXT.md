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

## Активный VP

**VP-4 — Work Orders & Context (Master Spec §16, §37): РЕАЛИЗОВАН, 26/26 PASS**
([`vp/VP-4.md`](vp/VP-4.md)). Приёмка `scripts/run_vp4_acceptance.py` — 26/26
против развёрнутого стека; живая БД мигрирована на `0004_work_orders` (backup
снят до миграции). PR `atlas/vp-4-work-orders-context` открыт, ожидается CI на
точном head-SHA и squash-merge. Точные PR #, CI head и merge-SHA фиксируются
post-merge sync-коммитом. Следующий гейт после merge — **VP-5 (Run Engine &
Role Routing)**, **не начат** (отдельное решение владельца).

## NEXT_ACTION

Owner проводит финальный визуальный обзор **VP-4 Work Orders console** на
`http://127.0.0.1:3210` (через SSH-туннель `ssh -N -L 3210:127.0.0.1:3210
<host>`): вкладка «Work Orders» — VP Spec из принятого Brief/Map, список Work
Orders и переходы состояний, решения оптимизатора (READY/MERGE/SPLIT/
OWNER_REQUIRED/SWITCH_PROFILE), checkpoint/handoff, свежая изолированная
реконструкция и next action; RU/EN-паритет, тёмная тема по умолчанию, a11y.

## Границы

Один активный VP-гейт (WIP=1). **VP-5 молча не начинать** — отдельное решение
владельца. LICENSE — отдельное решение владельца.
