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
  `atlas/vp-7-autonomy-github-time-machine` (от `main` `efee4c9`). Автономия (4
  режима, capability-гранты, Emergency Stop), GitHub-адаптер (`gh`) + STANDARD
  merge gate (current-head), Time Machine (checkpoints/replay/compare), Apache-2.0
  LICENSE, read-only auth-health 4 профилей. Новая миграция — `0007`.
- **Стек запущен:** `http://127.0.0.1:3210` (SSH-туннель). Core/Web healthy,
  Runner READY. **Живая БД — `0006_review_quality`** (миграция на `0007` — при
  deploy VP-7, backup снимается до миграции).
- **Профили:** 4 зарегистрированы (`codex-plus-01/02`, `claude-pro-01/02`);
  per-profile идентичности (`atlas-cx01/02`, `atlas-cl01/02`) и исполняемые файлы
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
