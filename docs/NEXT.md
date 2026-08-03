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
  Reviewer; детерминированная приёмка **26/26**; реальный E2E `calc.py`
  (3/6 вызовов, PASS). Живая БД была на **`0005_agent_pipeline`**.
- **VP-6 — Review & Quality: ЗАВЕРШЁН, 26/26 PASS + реальная Chrome-верификация**
  ([`vp/VP-6.md`](vp/VP-6.md)). Смёржен в `main` (**PR #11**, squash `63cdc35`,
  CI head `f6c3d0e`, все 4 job зелёные). SHA-bound ReviewPackage (инвалидация
  фактом → `INVALID_EVIDENCE`), Quality Firewall (11 gates + freshness + license-
  visibility), вердикты `PASS/REVISE/BLOCKED/OWNER_REQUIRED/INVALID_EVIDENCE`,
  QualityReport с объяснением, Impact engine (`DOC_ONLY/LOCAL/INTEGRATION/SHARED/
  HIGH_RISK`), Evidence Cache (точный ключ, инвалидация протухшего head), manual
  audit (read-only), waiver (non-waivable), fix-loop (второй REVISE → BLOCKED),
  экран **Качество** (RU/EN). Reconcile профилей: **4 профиля видны** в живой UI.
  Bounded Ember-refinement Pulse/Profiles/Audit/время. Детерминированная приёмка
  `run_vp6_acceptance.py` — **26/26**; полная Python-регрессия **268 OK**; Web
  tsc/build/i18n (569 ключей) green; реальная Chrome-верификация (Chromium
  151.0.7922.34) — 43 скриншота, 0 PII. Живая БД мигрирована на
  **`0006_review_quality`** (backup снят до миграции; данные сохранены).

## Активный VP

**Нет активного VP.** VP-0…VP-6 завершены и смёржены. Следующий гейт — **VP-7
(Autonomy, GitHub & Time Machine, Master Spec §40)**, но он **не начат** и
стартует только по отдельному решению владельца.

## NEXT_ACTION

Owner проводит финальный визуальный обзор **VP-6 Качество/Профили/Пульс** на
`http://127.0.0.1:3210` (SSH-туннель): вкладка «Качество» — blocking findings
первыми, QualityReport (gate/evidence/next action), SHA-bound ReviewPackage,
claim vs evidence, impact/cache, manual audit, waiver, focused fix Work Order;
«Профили» — **4 карточки** Codex/Claude, честная ёмкость UNKNOWN; «Пульс» —
иерархия (состояние → риски → ресурсы → диагностика), критическое хранилище
и подпись «Нагрузка за 1/5/15 мин»; RU/EN.

## Границы

Активного VP-гейта нет (WIP=0). **VP-7…VP-9 не начинать** — отдельное решение
владельца. Полный операционный console Profiles/Pulse (40 профилей) — VP-8.
Cookie-import — `UNSUPPORTED`. LICENSE — отдельное решение владельца.
