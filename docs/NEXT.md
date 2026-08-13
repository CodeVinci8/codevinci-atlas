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

**VP-7 — Autonomy, GitHub & Time Machine (Master Spec §40): В РАБОТЕ**
([`vp/VP-7.md`](vp/VP-7.md)). Ветка `atlas/vp-7-autonomy-github-time-machine`
(от `main` `efee4c9`). Владелец явно авторизовал закрытие VP-7 в сессии.
Скоуп: 4 режима (GUIDED/STANDARD/AUTONOMOUS/TRUSTED), durable capability-гранты
(раздельные capabilities, fail-closed), Emergency Stop, GitHub-адаптер (`gh`) +
STANDARD merge gate (current-head, stale PASS/CI → deny), Time Machine
(checkpoints/replay/compare), Apache-2.0 LICENSE + reuse-аудит, read-only
auth-health, официальная numeric-ёмкость (Claude status-line + Codex app-server),
single-Claude пул (registry-driven, claude-pro-02 disabled), production Run-start
роутинг, бренд/favicon/реальная CPU-утилизация. Миграция — `0007`.
Живая БД — `0006_review_quality` (мигрирует на `0007` при deploy).

## NEXT_ACTION

Владелец снял лимит на число Reviewer-вызовов: последовательные независимые Codex
Reviewer до genuine current-head PASS. **Immutable история:** call 7…13 — genuine
**REVISE** (heads `4517ebd`…`14a2da9`, все находки исправлены с тестами); call **14**
(head `9684e8b`, codex-plus-01) — genuine **PASS** (0 находок), НО authoritative merge
gate вернул `MISSING_EVIDENCE` → merge не исполнен (реальный evidence-gate дефект);
call **15** (head `d788bd1`, codex-plus-02) — genuine **REVISE** (evidence-store
самодостаточность + replay Emergency-окно); call **16** (head `541c534`, codex-plus-02)
— genuine **REVISE** (replay check-then-insert TOCTOU + рассинхрон durable-truth);
call **17** (head `be94175`, codex-plus-02) — genuine **REVISE** (межпроцессный
Emergency TOCTOU: durable-барьер до снимка active-runs); call **18** (head `cff0eed`,
codex-plus-01) — genuine **REVISE** (2 HIGH: `verify_checkpoint` не пересчитывал реальные
артефакты; `create_checkpoint` без redaction); call **19** (head `57b1abb`, codex-plus-02)
— genuine **REVISE** (1 HIGH обход verified-hashes через malformed ref без хеша + 2 MEDIUM:
cause>80 → TAMPERED; actor/correlation_id/created_at не в content_hash); call **20** (head
`c73f689`, codex-plus-02) — genuine **PASS** (0 находок), НО harness упал `TypeError` в
`authorize_merge_execution` до merge (`GhForge.get_pr` без обязательного `body`); PR остался
OPEN (fail-safe). Находки call 15…19 исправлены; get_pr исправлен (+тест), DRY authorize против
живого PR = MERGE_PERMITTED. Вместе с call-18 внесены tracked-правки §2/§3 (immutable
evidence-store; обязательная evidence-политика; сохраняемая closure-БД + resume без Reviewer) и
**устранён дефект безопасности миграций**: `env.py` теперь вызывает `migration_guard` (случайная
миграция живой БД `0006→0007` при валидации немедленно возвращена downgrade → `0006`; VP-0..6
целы). call 14 и call 20 (PASS) НЕ переименовываются в REVISE, но не авторизуют новый head.

**Следующий шаг:** независимый Reviewer **call 21** по исправленному зелёному head
(после commit/push и зелёного CI на точном PR head). При genuine PASS — авторитетный
evidence-backed STANDARD-merge PR #13 (`authorize_merge_execution`=MERGE_PERMITTED,
`VP7_EXECUTE_MERGE=1`, `--match-head-commit`), backup, миграция `0006→0007` (guarded,
`ATLAS_ALLOW_LIVE_MIGRATION=1`), рестарт стека, live Chrome-smoke, truth-sync. При REVISE
— не мержить, исправить, повторить.

## VP-8 (записано, НЕ реализовано)

- Удобная авторизация/реавторизация Codex и Claude прямо в Profiles.
- Добавление/замена профилей без правок кода (registry-driven пул уже готов).
- Поддержанные официальные login/attach-флоу; привязка второго Claude-аккаунта.
- Изолированный owner-only cookie-import адаптер (если будет одобрен отдельно);
  по умолчанию выключен; без browser-extraction и обхода MFA.
- Хранение секретов зашифровано вне Core DB/логов.
- Полный операционный console Profiles/Pulse (40 профилей, login/refresh/quotas/
  usage-history).

## Границы

Активный гейт — **VP-7** (WIP=1). **VP-8/VP-9 не начинать.** File Atelier
release-proof — VP-9. Cookie-import — `UNSUPPORTED` в VP-7. LICENSE —
**Apache-2.0** (owner-решение, §49; см. DECISIONS).
