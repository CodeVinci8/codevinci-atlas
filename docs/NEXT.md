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

**Нет активного VP.** VP-0…VP-3 завершены и смёржены. Следующий гейт — **VP-4
(Work Orders & Context, Master Spec §37)**, но он **не начат** и стартует только
по отдельному решению владельца.

## NEXT_ACTION

Owner проводит финальный визуальный обзор VP-3 на `http://127.0.0.1:3210`
(через SSH-туннель `ssh -N -L 3210:127.0.0.1:3210 <host>`): вкладка «Карта
продукта» и раздел «Портфель» — intake, Draft Brief с truth-badges, поштучные
accept/reject, approval версии Brief, Project/Portfolio Map, diff версий,
parking lot, экспорт MD/JSON. После обзора — решение о старте VP-4.

## Границы

Один активный VP-гейт (WIP=1). **VP-4 молча не начинать** — отдельное решение
владельца. LICENSE — отдельное решение владельца.
