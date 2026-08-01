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

Активная разработка по этапам **VP-0…VP-9**. **VP-0: Profile Pool & Live
Handoff Proof — ЗАВЕРШЁН (11/11 PASS)**, включая реальные A→B для Codex и
Claude. Текущий этап — **VP-1: Foundation**.

Доказано реально в VP-0:

- изоляция **2 Codex + 2 Claude** профилей через отдельные Unix-идентичности и
  исполняемые файлы (процесс профиля A не читает credentials B; `atlas` — ни один);
- **реальный A→B**: профиль A даёт структурный результат, Atlas сохраняет и
  верифицирует HandoffPackage против БД, профиль B (другой аккаунт, отдельная
  сессия) продолжает и завершает — независимо проверяемо, для обоих провайдеров;
- **один writer**; обрыв Runner → reconciliation → продолжение до одного успеха;
- восстановление после рестарта Core; смена профиля при **rate limit** без
  второго writer;
- отсутствие секретов в дереве/истории Git/БД/логах/artifacts;
- честная ёмкость **UNKNOWN**.

## Быстрый старт (доказательство VP-0)

Нужен Python 3.12+ (проверено на 3.14); uv/pnpm/Docker для VP-0 не нужны.
Единый runtime-layout — `/var/lib/codevinci-atlas`.

```bash
# runtime-идентичности и каталоги (root, идемпотентно), затем профили
sudo bash scripts/atlas-runtime-setup.sh
PYTHONPATH=apps/core python3 scripts/profile-init.py

# Юнит-приёмка
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'

# Полная приёмка VP-0 (PASS / PASS_MECHANISM / GATE_REAL) + evidence
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py

# Диагностика профилей без идентичностей + web-status; полный секрет-скан
PYTHONPATH=apps/core:apps/runner python3 scripts/atlas-doctor --web-status var/status.html
PYTHONPATH=apps/core python3 scripts/secret_scan.py
```

Реальные подписочные profile-probe — за owner-гейтом: `scripts/login-gate.sh`.

## Архитектура (кратко)

Core/Web в Docker Compose; нативный host Runner (systemd, пользователь
`atlas`) запускает реальные `codex`/`claude`/`git`/`gh`. Core ↔ Runner — Unix
domain socket с request-token. Credentials не монтируются в Web. Подробнее —
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Документация

- [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) — каноническое ТЗ.
- [`docs/vp/VP-0.md`](docs/vp/VP-0.md) — исполнимый спек VP-0.
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
