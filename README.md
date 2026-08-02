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

Активного VP нет. Следующий этап — **VP-4: Work Orders & Context**
(Master Spec §37) — **не начат** и стартует по отдельному решению владельца.

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
  [`docs/vp/VP-3.md`](docs/vp/VP-3.md).
- [`docs/PRODUCT_MAP.md`](docs/PRODUCT_MAP.md) — модель Product Map и API VP-3.
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
