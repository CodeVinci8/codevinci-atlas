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
  compact fail-closed, Work Orders UI RU/EN. Живая БД — на `0004_work_orders`.

## Активный VP

**VP-5 — Agent Pipeline (Master Spec §38, §17): АКТИВЕН, локально реализован.**
Ветка `atlas/vp-5-agent-pipeline` (не запушена). Детерминированная приёмка
`scripts/run_vp5_acceptance.py` — **26/26 PASS** против реальной миграции
`0005_agent_pipeline`, реального ORM/БД и ASGI-стека (TestClient), с
fake-адаптерами (§32.2). Полная Python-регрессия — 243 OK. Evidence + SHA-256 —
`var/artifacts/vp5/`.

**Реальный provider-E2E** (Planner→Builder→Reviewer на подписке) — **честно
pending** до отдельной owner-авторизации. Пуша/PR/merge VP-5 **ещё нет**.

## NEXT_ACTION

Owner подтверждает external-action checkpoint VP-5: (1) первый push ветки
`atlas/vp-5-agent-pipeline`; (2) русский PR; (3) после зелёного CI на точном
head-SHA — squash-merge; и отдельно (4) авторизует ограниченный реальный
provider-E2E (точные профили/модели/команды/лимит вызовов — в чекпойнте).

## Границы

Один активный VP-гейт (WIP=1) — VP-5. **VP-6…VP-9 не начинать.** Полный
операционный console Profiles/Pulse (40 профилей) — VP-8. Cookie-import —
`UNSUPPORTED`. LICENSE — отдельное решение владельца.
