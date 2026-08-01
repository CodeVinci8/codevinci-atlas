# TEST_POLICY — риск-ориентированное тестирование

Master Spec §18, §32. Принцип: полная регрессия после микроправки запрещена
без risk-триггера (§18.6).

## Классы влияния (§18.5)

| Класс | Пример | Гейт |
|---|---|---|
| DOC_ONLY | документация | markdown/ссылки/рендер |
| LOCAL | модуль | targeted unit/lint |
| INTEGRATION | API/DB/adapter | unit + integration |
| SHARED | schema/router/policy | зависимые suites |
| HIGH_RISK | auth/grant/migration/release | полный релевантный + security |

## Частота (§18.6)

- локальное изменение — targeted;
- checkpoint — затронутый integration;
- pre-merge — diff-based CI один раз;
- VP PASS — приёмка VP;
- 1.0 — release gate.

## Приёмка VP-0

- **Юниты** (`tests/test_*.py`, 86 тестов): errors, redaction, изоляция (в т.ч.
  реальная граница через per-profile идентичности), leases, handoff,
  fake-adapters + argv/auth реальных, runner UDS, Core restart, **runner
  recovery-to-success E2E**, ratelimit switch, capacity, secret-guard.
- **Приёмочный прогон** (`scripts/run_acceptance.py`): 11 критериев со статусами
  `PASS`/`PASS_MECHANISM`/`GATE_REAL`; реальная изоляция; recovery-to-success;
  полный секрет-скан; evidence в `var/artifacts/vp0/`.
- **Полный секрет-скан** (`scripts/secret_scan.py`): дерево + история Git +
  БД/логи/artifacts/конфиг; аллоуслист только для синтетических фикстур.
- **Manual-real** (`scripts/manual_real_probe.py`): реальные A→B probe после
  owner-логина. Не входит в обычную CI (§32.4).

## Обоснование выбора тестов (VP-0)

- Изоляция и один writer — HIGH_RISK → полный релевантный набор + OS-DAC.
- Redaction/secret-scan — security-гейт → отдельные тесты + сканы durable-состояния.
- Runner/адаптеры — INTEGRATION → UDS + fake-contracts.
- Полная regression на весь набор запускается один раз на границе VP-0.
  После правок документации она не повторяется (DOC_ONLY).

## Evidence Cache (§18.7)

Ключ: SHA + команда/версия + хеши входов + окружение + scope. Повторное
использование только точное и с видимой причиной. (Реализация — VP-6.)
