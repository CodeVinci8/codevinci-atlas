# HOT — быстрый контекст

- **Проект:** CodeVinci Atlas — self-hosted центр управления Codex и Claude.
- **VP-0: ЗАВЕРШЁН — 11/11** (реальные A→B, merged). **VP-1: ЗАВЕРШЁН — 17/17**
  (Compose Core/Web + systemd Runner + health/migrations/audit/CLI/RU-EN/CI, merged).
- **VP-2 — Project Workspace: ЗАВЕРШЁН — 20/20** (источники, read-only git
  baseline, безопасные worktree+аренды, Project Overview Ember RU/EN, merged
  PR #3 squash `a14472a`).
- **VP-3 — Product Map: ЗАВЕРШЁН — 26/26** (intake, truth-status, версии Brief,
  решения accept/reject, nodes/edges, approval + envelope, parking lot,
  Project/Portfolio Map, diff, экспорт MD/JSON; merged PR #4 squash `07ed6f4`;
  живая БД на `0003_product_map`).
- **VP-4 — Work Orders & Context: ЗАВЕРШЁН — 26/26** (VP Spec из принятого
  Brief/Map, Work Orders + переходы, оптимизатор READY/MERGE/SPLIT/OWNER_REQUIRED/
  SWITCH_PROFILE, bounded JobPackage, checkpoint/handoff, свежая изолированная
  реконструкция внутри Core-образа, ротация с одним writer, compact-fallback,
  Work Orders UI RU/EN; merged PR #6 squash `7a3f82d`, CI head `280ee35`; живая
  БД на `0004_work_orders`).
- **VP-5 — Agent Pipeline: ЗАВЕРШЁН — 26/26 + реальный E2E** (merged PR #9,
  squash `afefa61`, CI head `86c504e`; живая БД на `0005_agent_pipeline`).
  Миграция 0005 (16 таблиц), router без silent fallback, RunService (lifecycle/
  idempotency/optimistic), one-writer (worktree+profile lease), PipelineService
  (Planner→Builder→независимый Reviewer, fix-loop, rate-limit/auth/interruption),
  адаптеры (3 session-семантики: EXACT_RESUME/FORK_SESSION/FRESH_WITH_HANDOFF),
  API /runs·/profiles·/models·/system/summary, Web (Runs/Profiles/Pulse/RU-EN
  segmented). Детерминированная приёмка **26/26**; полная Python-регрессия
  **247 OK**; Web tsc+build+i18n 452/452, bundle-verify 28/28. Реальный
  provider-E2E: Codex Planner (codex-plus-01) → Claude Builder (claude-pro-01,
  артефакт calc.py) → независимый Codex Reviewer (codex-plus-02) → PASS, 3/6
  вызовов.
- **VP-6 — Review & Quality: ЗАВЕРШЁН — 26/26 + реальная Chrome-верификация**
  (merged PR #11, squash `63cdc35`, CI head `f6c3d0e`; живая БД на
  `0006_review_quality`). Миграция 0006 (9 таблиц), SHA-bound ReviewPackage
  (инвалидация фактом → INVALID_EVIDENCE), Quality Firewall (11 gates), Impact
  engine, Evidence Cache, manual audit/waiver, fix-loop (второй REVISE → BLOCKED),
  экран Качество RU/EN, bounded Ember-refinement Pulse/Profiles/Audit/время.
  Reconcile профилей: **4 профиля видны** в живой UI (root-cause: реестр не
  синхронизировался в БД). Полная регрессия **268 OK**; Chrome — 43 скриншота,
  0 PII (Chromium 151.0.7922.34).
- **VP-7 — Autonomy, GitHub & Time Machine (§40): В РАБОТЕ** на ветке
  `atlas/vp-7-autonomy-github-time-machine` (от `main` `efee4c9`; PR #13 OPEN).
  Автономия (4 режима, capability-гранты, Emergency Stop), GitHub-адаптер (`gh`) +
  STANDARD merge gate (current-head), Time Machine (checkpoints/replay/compare),
  Apache-2.0 LICENSE, read-only auth-health, официальная numeric-ёмкость Claude
  (status-line rate_limits) + Codex (app-server), single-Claude пул (registry-
  driven), production Run-start роутинг. Новая миграция — `0007`.
- **Reviewer история (immutable):** call **7/7** (head `4517ebd`) → genuine
  **REVISE** (2 находки merge_gate.py) → исправлено `6aa2d20`. call **8/8** (head
  `fad8449`) → genuine **REVISE** (Emergency Stop не закрывал production start
  boundary; grant не проверял starts_at) → исправлено (emergency-check в
  start_builder_run + кооперативный interrupt; `_not_yet_active`/GRANT_NOT_YET_ACTIVE)
  с тестами. call **9** (head `515acb0`, codex-plus-02, т.к. codex-plus-01 исчерпан
  0%) → genuine **REVISE** (Emergency TOCTOU; github_deliveries не писалась реальным
  merge) → исправлено (engage _ENGAGING-флаг + re-check после регистрации job;
  merge_pull_request пишет authoritative delivery) с тестами. call **10** (head
  `896cc9b`, codex-plus-01) → genuine **REVISE** (grant consume TOCTOU; строгость
  checks/mergeability; durability delivery до squash; Emergency у merge-boundary) →
  исправлено `2f54e39`/`896cc9b` с тестами. call **11** (head `2d13d04`,
  codex-plus-02) → genuine **REVISE** (4 находки Time Machine: replay не
  воспроизводил head-состояние; replay не подключён к production API; compare не
  верифицировал checkpoints; повторный replay мог конфликтовать по ветке/Run) →
  исправлено с тестами; дополнительно закрыты 3 остаточных риска перед call-12
  (grant version-snapshot, Emergency-барьер у каждой forge-границы, явная политика
  обязательных CI-контекстов) + production HTTP replay через доверенный checkout из
  durable-состояния. Лимит на число Reviewer-вызовов снят владельцем
  (последовательно до PASS). call **12** (head `a15bd87`, codex-plus-01) → genuine
  **REVISE** (4 находки: local_git replay без workspace-scope; compare endpoint не
  маппил InvalidCheckpointError→INVALID_EVIDENCE; Web не подключён к production
  replay; replay при пустом head_sha не fail-closed) → исправлено с тестами. call **13**
  (head `14a2da9`, codex-plus-02) → genuine **REVISE** (3 находки: commit/push не
  энфорсили workspace-scope checkout; squash-merge без `--match-head-commit` — TOCTOU
  current-head; Emergency-окно между первым барьером и squash из-за durable
  record_delivery) → исправлено с тестами. call **14** (head `9684e8b`, codex-plus-01,
  независим, session present) → genuine **PASS** (reviewer PASS, quality PASS, 0 находок,
  CI GREEN 4 required job, mergeability CLEAN), НО authoritative STANDARD merge gate
  вернул `REVIEW_PACKAGE_INVALID / MISSING_EVIDENCE` → merge НЕ исполнен. call-7…14
  immutable; call 14 НЕ переименовывается в REVISE — это подлинный PASS на старом head,
  чей execution-gate деним по MISSING_EVIDENCE.
- **Evidence-gate defect (call-14 → исправлено, §2):** `authorize_merge_execution`
  вызывал `validate_review_package` с ПУСТЫМИ фактами, из-за чего evidence-backed RP
  всегда падал в MISSING_EVIDENCE, а `artifact_hashes` не сверялись (tamper незаметен).
  Fix: durable head-bound **evidence-store** (`merge_evidence`, миграция 0007) +
  `reviewpkg.resolve_review_facts` (факты из store + пересчёта реальных файлов, не из
  caller-claims); authoritative-путь требует evidence-backed RP (пустой → deny),
  missing→MISSING_EVIDENCE, tampered→ARTIFACT_ALTERED, чужой head→deny. Harness
  `run_vp7_final_review.py` строит RP из РЕАЛЬНЫХ входов (точный base SHA, свежий
  acceptance, зарегистрированные evidence-файлы) и использует production CI-политику.
  Это **tracked-правка** → call 14 больше не авторизует исправленный head; следующий
  подлинный вызов — **call 15** по новому зелёному head.
- **call 15** (head `d788bd1`, codex-plus-02, независим) → genuine **REVISE** (2 HIGH):
  (1) evidence-store не самодостаточен — `resolve_review_facts` игнорировал
  `MergeEvidence.sha256`, ref-only файл вне `artifact_hashes` можно подменить →
  добавлена `verify_evidence_for_refs` (fail-closed сверка КАЖДОЙ ссылки со store,
  independent of artifact_hashes); (2) replay Emergency-окно между первичной проверкой
  и созданием Run → повторный `blocks_new_jobs()` барьер + откат orphan-ветки. Обе
  исправлены с тестами; merge не исполнялся. call-7…15 immutable; NEXT — **call 16**.
- **call 16** (head `541c534`, codex-plus-02, независим) → genuine **REVISE** (2):
  (1, критично) replay TOCTOU — повторная проверка `blocks_new_jobs()` перед
  `_create_replay_run()` не атомарна с INSERT Run → добавлена проверка ПОСЛЕ INSERT +
  rollback (Run→CANCELLED, откат ветки); (2) `NEXT.md` рассинхронизирован (call 9/9) →
  приведён к факту. Исправлены с тестами; merge не исполнялся. call-7…16 immutable;
  NEXT — **call 17**.
- **Аккаунты Claude (текущая правда):** активен ТОЛЬКО **`claude-pro-01`**;
  `claude-pro-02` — истёкшая вторая подписка, **disabled** (не в активном пуле/UI/
  ёмкости; unix-user/home/creds не удалены; история сохранена). Второй Claude
  привяжут в **VP-8**. Пул — registry-driven (не хардкод), «2/2» устранено.
- **Стек запущен:** `http://127.0.0.1:3210` (SSH-туннель). Core/Web healthy,
  Runner READY. **Живая БД — `0006_review_quality`** (миграция на `0007` — при
  deploy VP-7, backup снимается до миграции).
- **Профили:** 4 зарегистрированы (`codex-plus-01/02`, `claude-pro-01/02`), из них
  активны 3 (claude-pro-02 disabled); per-profile идентичности (`atlas-cx01/02`,
  `atlas-cl01/02`) и исполняемые файлы
  `<root>/.local/bin/*`. **`AUTH_REQUIRED` в UI — консервативное durable-состояние,
  не доказательство протухания логинов.** Готовность подтверждается только
  bounded read-only auth-health (`codex login status`/`claude auth status`),
  результат — свежий факт с observed_at/source. Успех auth не выводит capacity
  (остаётся UNKNOWN, §11.6).
- **Среда — host отделён от Core-контейнера:** **host** — Ubuntu 26.04 LTS,
  kernel `7.0.0-28-generic` (по host `/etc/os-release`); **Core-контейнер** —
  Debian GNU/Linux 13 (trixie). Не называть host Debian из-за контейнера.
  **Codex CLI 0.146.0**, Claude Code 2.1.220. На host: uv, pnpm, `.venv`
  (Python 3.14) присутствуют; регрессия гоняется через `.venv`.
- **Runtime-layout:** единый **`/var/lib/codevinci-atlas`** (repo-local `./var`
  больше не используется; тесты — временный `ATLAS_DATA_DIR`).
- **Изоляция:** per-profile Unix-идентичности `atlas-cx01/02`, `atlas-cl01/02`;
  root `0700` во владении своей идентичности; Runner дропает привилегии.
  Сервисный `atlas` не читает credentials.
- **Репозиторий:** `CodeVinci8/codevinci-atlas`, public. **VP-0…VP-6 смёржены**
  в `main` (VP-6: PR #11 squash `63cdc35`, doc-sync PR #12 squash `efee4c9` =
  текущий `main`). Живая БД — на **`0006_review_quality`**. VP-7 — в работе.
- **Лицензия:** **Apache-2.0** (owner-решение, §49; см. DECISIONS). LICENSE в
  корне; SPDX в README/metadata. Reuse-аудит: копий стороннего кода нет
  (все REFERENCE/SPIKE), NOTICE не требуется.
- **Git-идентичность:** имя `CodeVinci`, email в `git config` (задан).
- **Главные правила:** один writer, credentials не копируются, секреты не в
  durable-состоянии, capacity честно UNKNOWN.
- **Запуск:** `sudo bash scripts/atlas-runtime-setup.sh` →
  `PYTHONPATH=apps/core python3 scripts/profile-init.py` →
  `PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py`.
- **Owner-гейт:** `scripts/login-gate.sh` → `scripts/manual_real_probe.py`.
