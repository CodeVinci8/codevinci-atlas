# CodeVinci Atlas

**Self-hosted центр управления Codex и Claude: проекты, профили, review,
evidence и безопасная автоматизация.**

Atlas — самостоятельный публичный open-source продукт CodeVinci и
self-hosted инструмент одного владельца. Он превращает процесс «идея →
Planner → Builder → Reviewer → разрешённый PR» в воспроизводимый конвейер с
versioned-памятью вместо бесконечного чата.

> 🇬🇧 English: [`README.en.md`](README.en.md)
> 📘 Каноническое ТЗ: [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md)

## Статус

Активная разработка по этапам **VP-0…VP-9**. Завершено и смёржено в `main`:

- **VP-0 — Profile Pool & Live Handoff Proof: ЗАВЕРШЁН (11/11 PASS)** — реальные
  A→B для Codex и Claude, изоляция 4 профилей, один writer, честная ёмкость `UNKNOWN`.
- **VP-1 — Foundation: ЗАВЕРШЁН (17/17 PASS)** — Compose Core/Web + systemd
  Runner, health/миграции/audit, CLI `doctor/status/backup`, RU/EN Web-shell, CI.
- **VP-2 — Project Workspace: ЗАВЕРШЁН (20/20 PASS)** — подключение проектов
  (local Git / GitHub / архив / пустой), read-only git baseline, безопасные
  worktree и writer-аренды, Project Overview (Ember RU/EN).
- **VP-3 — Product Map: ЗАВЕРШЁН (26/26 PASS)** ([`docs/vp/VP-3.md`](docs/vp/VP-3.md)) —
  структурный intake, truth-status, версии Brief и решения, Project/Portfolio Map,
  diff версий, scope-envelope, parking lot и экспорт accepted-состояния в
  Markdown/JSON. Каждый факт несёт явный truth-status; `VERIFIED` требует
  проверяемого evidence.
- **VP-4 — Work Orders & Context: ЗАВЕРШЁН (26/26 PASS)**
  ([`docs/vp/VP-4.md`](docs/vp/VP-4.md), [`docs/WORK_ORDERS.md`](docs/WORK_ORDERS.md)) —
  детерминированный VP Spec из принятого Brief/Map, исполняемые Work Orders с
  версионным жизненным циклом и writer-арендами (один writer), контролируемые
  решения оптимизатора (READY/MERGE/SPLIT/OWNER_REQUIRED/SWITCH_PROFILE),
  bounded immutable JobPackage, Context Governor с durable-checkpoint и handoff,
  а также **свежий изолированный потребитель**, который восстанавливает
  состояние только из HandoffPackage. Смёржен через PR #6 (squash `7a3f82d`,
  CI head `280ee35`); живая БД — на `0004_work_orders`.

**VP-5 — Agent Pipeline (Master Spec §38, §17): АКТИВЕН, локально реализован**
([`docs/vp/VP-5.md`](docs/vp/VP-5.md)) — конвейер Codex Planner → Claude Builder →
независимый Codex Reviewer: durable Runs (жизненный цикл, идемпотентность,
optimistic-concurrency), router без silent fallback, один writer (worktree +
profile lease), три раздельные session-семантики (EXACT_RESUME/FORK_SESSION/
FRESH_WITH_HANDOFF), bounded rate-limit/auth/interruption-recovery, Profiles MVP,
full-width Pulse, RU/EN. Детерминированная приёмка `run_vp5_acceptance.py` —
**26/26** (реальная миграция `0005`, реальные сервисы, fake-адаптеры). **Реальный
provider-E2E — pending** до owner-авторизации; PR/merge ещё нет.

## Быстрый старт

Стек Core/Web — в Docker Compose; Runner — нативный systemd-сервис. Единый
runtime-layout — `/var/lib/codevinci-atlas`. Web слушает только
`http://127.0.0.1:3210` (loopback).

```bash
# runtime-идентичности и каталоги (root, идемпотентно), затем профили
sudo bash scripts/atlas-runtime-setup.sh
PYTHONPATH=apps/core python3 scripts/profile-init.py

# systemd Runner (нативный host, UDS)
sudo cp infra/systemd/codevinci-atlas-runner.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now codevinci-atlas-runner

# Compose Core/Web (UID/GID atlas в .env; миграции применяет entrypoint)
docker compose up -d --build

# состояние стека и БД
curl -s http://127.0.0.1:3210/api/v1/health

# приёмки по этапам (root, стек поднят)
python3 scripts/run_vp1_acceptance.py      # 17/17
python3 scripts/run_vp2_acceptance.py      # 20/20
python3 scripts/run_vp3_acceptance.py      # 26/26 (VP-3)
python3 scripts/run_vp4_acceptance.py      # 26/26 (VP-4)
```

Юнит/интеграционные тесты Core/Runner (без стека):

```bash
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
```

Реальные подписочные profile-probe — за owner-гейтом: `scripts/login-gate.sh`.

## Архитектура (кратко)

Core/Web в Docker Compose; нативный host Runner (systemd, пользователь
`atlas`) запускает реальные `codex`/`claude`/`git`/`gh`. Core ↔ Runner — Unix
domain socket с request-token. Credentials не монтируются в Web. Подробнее —
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Документация

- [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) — каноническое ТЗ.
- Исполнимые спеки: [`docs/vp/VP-0.md`](docs/vp/VP-0.md),
  [`docs/vp/VP-1.md`](docs/vp/VP-1.md), [`docs/vp/VP-2.md`](docs/vp/VP-2.md),
  [`docs/vp/VP-3.md`](docs/vp/VP-3.md), [`docs/vp/VP-4.md`](docs/vp/VP-4.md).
- [`docs/PRODUCT_MAP.md`](docs/PRODUCT_MAP.md) — модель Product Map и API VP-3.
- [`docs/WORK_ORDERS.md`](docs/WORK_ORDERS.md) — Work Orders & Context Engine
  и API VP-4.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/ADAPTERS.md`](docs/ADAPTERS.md),
  [`docs/INSTALL.md`](docs/INSTALL.md), [`docs/OPERATIONS.md`](docs/OPERATIONS.md),
  [`docs/TEST_POLICY.md`](docs/TEST_POLICY.md).
- [`docs/REUSE_REGISTER.md`](docs/REUSE_REGISTER.md), [`docs/DECISIONS.md`](docs/DECISIONS.md).
- Безопасность: [`SECURITY.md`](SECURITY.md).

## Лицензия

Пока не выбрана: LICENSE фиксируется после reuse-аудита VP-0 (кандидаты
MIT/Apache-2.0) и требует отдельного подтверждения владельца (Master Spec §49).

## Приватность и безопасность

Atlas не является secret-vault для CLI credentials и не обходит правила
провайдеров. Учётные данные живут только в изолированных root профилей.
Диагностика и логи не раскрывают email, token, cookie и raw path.
