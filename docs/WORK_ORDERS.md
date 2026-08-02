# Work Orders & Context Engine (VP-4)

Источник истины: [`docs/MASTER_SPEC.md`](MASTER_SPEC.md) §16, §37 (при конфликте —
§1). Исполнимый спек: [`docs/vp/VP-4.md`](vp/VP-4.md). Решения:
[`docs/DECISIONS.md`](DECISIONS.md) (блок VP-4). EN-версия:
[`docs/en/WORK_ORDERS.md`](en/WORK_ORDERS.md).

VP-4 превращает принятый Product Brief и Project Map (VP-3) в исполнимые
контракты работы и контекст: **VP Spec → Work Orders → JobPackage →
checkpoint/handoff → свежая изолированная реконструкция**. Реальной автономной
маршрутизации ролей и Run-движка здесь нет — это VP-5.

## Модель данных

Миграция `0004_work_orders` (после `0003_product_map`) добавляет durable-таблицы:

- `vp_specs` — версионный VP Spec, выведенный детерминированно из одного
  принятого Brief/Map/approval; `content_hash` по canonical-JSON.
- `work_orders` — исполнимая единица; связывает точные хеши Spec/Brief/Map и
  baseline (§16.1); поля оптимистичной блокировки (`version`), аренда writer
  (`lease_id`, `writer_holder`).
- `work_order_events` — **append-only** история переходов и решений.
- `optimizer_decisions` — решения оптимизатора и их evidence.
- `job_packages` — immutable bounded JobPackage с provenance.
- `wo_checkpoints` — durable, hash-verifiable точки восстановления.
- `handoff_packages`, `handoff_acks` — полный HandoffPackage и подтверждения.
- `rotation_records` — записи ротации контекста (один writer сохраняется).

## Жизненный цикл Work Order

Состояния и допустимые переходы (`VALID_TRANSITIONS`) фиксированы. Валидный
переход персистится атомарно; невалидный отклоняется **без частичной мутации**
(`INVALID_TRANSITION`). История — append-only. Оптимистичная блокировка по
версии: расхождение → `VERSION_CONFLICT` (без перезаписи). Повтор с тем же
`Idempotency-Key` не создаёт дублей.

**Один writer.** На worktree ровно одна аренда
(`UNIQUE(worktree, released_at IS NULL)`); **автоугона нет** — нужен reconcile.
Lease держится в `active/checkpointed/handoff_ready`; освобождается на
терминале/блокировке и на границе ротации. Вторая параллельная запись →
`WRITER_CONFLICT`.

## Оптимизатор

Выходы: `READY` (один ограниченный исполнимый WO), `MERGE_TASKS` (только
совместимые и с сохранением каждого критерия — иначе `CRITERIA_LOST`),
`SPLIT_AT_CHECKPOINT` (только на durable checkpoint, полное отображение
критериев на детей), `SWITCH_PROFILE` (смена профиля без маршрутизации ролей),
`OWNER_REQUIRED` (fail-closed). Оптимизатор **не меняет** scope и acceptance
criteria.

## JobPackage и Context Governor

JobPackage детерминирован, immutable, с provenance; **без** repo/полного чата/
логов/credentials/env; capacity честно `UNKNOWN`; capabilities — только из
allowlist. Контекст **не расширяет** права (§30.2). Context Governor
детерминированно определяет триггеры ротации (без выдуманной ёмкости), ставит
durable checkpoint, формирует HandoffPackage и ведёт ротацию, сохраняя одного
writer.

## Handoff и свежая реконструкция

HandoffPackage содержит все обязательные поля и детерминированный hash; отклоняет
`HASH_MISMATCH`/`HANDOFF_STALE`/`SCOPE_DRIFT`/`SOURCE_STALE`/
`PROJECT_NOT_AVAILABLE`/`CAPABILITY_DENIED` (tamper/stale/wrong-project/
wrong-version/wrong-HEAD/over-capability). **Свежий изолированный потребитель**
(`scripts/vp4_fresh_consumer.py`) запускается отдельным процессом в чистом
окружении — без `atlas_core`, БД, credentials, полного repo и старого чата — и
восстанавливает состояние и точное следующее действие только из HandoffPackage;
результат валиден по [`contracts/schemas/run-result.json`](../contracts/schemas/run-result.json).
Compact-fallback — локальный детерминированный harness: сохраняет все инварианты
или fail-closed `OWNER_REQUIRED`. **Реальных provider-вызовов нет.**

Core-образ содержит и исполняет consumer и контракты (`infra/docker/core.Dockerfile`
копирует `scripts/vp4_fresh_consumer.py` и `contracts/`); регрессия упаковки —
`scripts/check_core_image.sh` и `tests/test_vp4_packaging.py`.

## API (`/api/v1/projects/{id}/...`)

- VP Spec: `POST/GET vp-specs`, `GET vp-specs/{spec_id}`.
- Work Orders: `POST/GET work-orders`, `GET work-orders/{wo_id}`,
  `POST work-orders/{wo_id}/transition`.
- Оптимизатор: `POST optimizer/evaluate`, `optimizer/merge/preview|confirm`,
  `optimizer/split/preview|confirm`, `GET optimizer/decisions`.
- JobPackage: `POST work-orders/{wo_id}/job-package`, `GET job-packages[/{id}]`,
  `POST context/compact-probe`.
- Checkpoints: `POST work-orders/{wo_id}/checkpoints`, `GET checkpoints[/{id}]`,
  `GET checkpoints/{id}/verify`.
- Handoff: `POST work-orders/{wo_id}/handoffs`, `GET handoffs[/{id}]`,
  `GET handoffs/{id}/verify`, `POST handoffs/{id}/acknowledge|reject|reconstruct`,
  `GET handoffs/{id}/acks`.
- Governor/ротация: `POST governor/detect`, `POST work-orders/{wo_id}/rotate`,
  `POST rotations/{id}/continue`, `GET rotations[/{id}]`.

Мутации принимают `Idempotency-Key` и `expected_version`; ошибки — стабильные
коды (см. VP4-D3). Аудит — append-only и redacted (секреты не попадают).

## Контракты схем

[`contracts/schemas/`](../contracts/schemas/): `vp-spec.json`, `work-order.json`,
`job-package.json`, `handoff-package.json`, `run-result.json`. Валидируются
`scripts/validate_schemas.py`. Наш валидатор — **документированное подмножество**
JSON Schema (`type/enum/pattern/minimum/maximum/required/properties/`
`additionalProperties(bool)/items`), не полный draft 2020-12.

## Web

Консоль **Work Orders** (`http://127.0.0.1:3210`, вкладка «Work Orders»):
VP Spec, Work Orders и переходы, решения оптимизатора, checkpoint/handoff,
свежая реконструкция и next action. Тёмная тема по умолчанию, RU/EN-паритет,
a11y, responsive.

## Приёмка и тесты

- Полная приёмка: `PYTHONPATH=apps/core:apps/runner python3 scripts/run_vp4_acceptance.py`
  (26/26 против развёрнутого стека; evidence с SHA-256 — `var/artifacts/vp4/`).
- Юнит/интеграция: `tests/test_vp4_workorders.py`, `tests/test_vp4_packaging.py`.
- Схемы: `scripts/validate_schemas.py`. Образ: `scripts/check_core_image.sh`.
