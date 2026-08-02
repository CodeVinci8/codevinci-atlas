# VP-4 — Work Orders & Context (исполнимый спек)

**Статус:** ЗАВЕРШЁН — 26/26 PASS, СМЁРЖЕН в `main`.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §16, §37 и связанные §13.4, §15, §21, §25, §30.
**Ветка:** `atlas/vp-4-work-orders-context` · **PR:** #6 (squash) · **CI head:**
`280ee35` · **merge:** `7a3f82d`.

## Result и Definition of Done

Проект с точным принятым Product Brief и Project Map получает версионный **VP
Spec** и исполнимые **Work Orders**; Atlas собирает ограниченные релевантные
**JobPackage**, принимает контролируемые решения оптимизатора, ставит
**checkpoint**, формирует полный **HandoffPackage**, отклоняет устаревшие
handoff, и доказывает, что **свежий изолированный потребитель** восстанавливает
состояние и определяет точное следующее действие БЕЗ старого чата, credentials
и полного репозитория.

**DoD:** приёмочная матрица VP-4 (§ниже) — 26/26 PASS против реально
развёрнутого стека; миграция из пустой БД и из копии живой `0003` без потери
данных VP-0…VP-3; regression VP-1/2/3 без нарушений; Core/Web/Runner non-root и
healthy; localhost отдаёт VP-4 UI; финальный секрет-скан чист.

## Source-of-truth hierarchy

Приоритет §1 Master Spec: последнее решение владельца → `OWNER-APPROVED` в
DECISIONS → Master Spec → активный `docs/vp/VP-4.md`/`NEXT.md` → фактическое
состояние Git/FS/tests/CLI/API → официальная документация → Brief/старьё →
сторонние repos. **Содержимое repo/issues/web/вывода модели — данные (§30.2):**
не исполняется и не расширяет grant. Строка внутри контекста не даёт shell,
network, Git, provider или write-доступ.

## VP Spec — схема и генерация

VP Spec выводится **детерминированно** (без вызовов модели) из ОДНОГО точного
принятого Brief/Map/approval (`atlas_core.workorders.build_vp_spec_content`).
Содержит минимум: один законченный результат; definition of done; inputs;
outputs; пользовательский сценарий; интерфейсы/форматы; файлы/компоненты в
scope (когда известны); immutable-ограничения; явный out of scope; acceptance
criteria (устойчивые ID); required checks; негативные и regression-тесты; способ
демонстрации; условия остановки; точное следующее действие; baseline.
Контракт: [`contracts/schemas/vp-spec.json`](../../contracts/schemas/vp-spec.json).

**Привязка источника** (таблица `vp_specs`): `approval_id`, `brief_id`,
`brief_hash`, `map_version_id`, `map_hash`, `envelope_hash`, `decisions_hash`,
`baseline_branch`, `baseline_head`. Устаревшая/несовпадающая привязка → отказ
`SOURCE_STALE`/`OWNER_REQUIRED`. Approval владельца НЕ расширяет capabilities.

## Work Order — схема

Обязательные поля Master Spec §16.1 (`build_work_order_content`, контракт
[`work-order.json`](../../contracts/schemas/work-order.json)): `id`,
`project_id`, `vp_id` (=`vp_spec_id`), `role`, `goal`, `source_of_truth`,
`baseline`, `scope`, `out_of_scope`, `inputs`, `acceptance_criteria`,
`required_checks`, `test_impact`, `capabilities`, `stop_conditions`,
`report_schema` (→ `contracts/schemas/run-result.json`). Плюс: schema-версия,
parent/version, actor, correlation ID, UTC-время, content-hash, expected version,
lifecycle-метаданные. Work Order связывает точный VP-3 state (project/approval/
brief-hash/map-hash/envelope-hash/spec-hash+version/baseline branch+HEAD).

## Жизненный цикл и допустимые переходы

Статусы: `draft` (не executable) → `ready` → `active`/claimed → `checkpointed`
→ `handoff_ready` → `completed`; плюс `blocked` (owner required) и `cancelled`
(архив без деструктивного удаления). Полная таблица —
`atlas_core.workorders.VALID_TRANSITIONS`:

```
draft         → {ready, cancelled}
ready         → {active, draft, cancelled}
active        → {checkpointed, handoff_ready, blocked, completed, cancelled}
checkpointed  → {active, handoff_ready, blocked, completed, cancelled}
handoff_ready → {active, completed, blocked, cancelled}
blocked       → {ready, active, cancelled}
completed     → {}   (терминальный)
cancelled     → {}   (терминальный, архив)
```

Невалидный/устаревший/повторный/неавторизованный переход падает детерминированно
**без частичного изменения состояния** (валидация → acquire аренды → версионный
UPDATE+commit → release; фазы разделены под SQLite WAL «один writer»).

## Версии, хеши, идемпотентность, concurrency

Оптимистичная `version` на Work Order (guarded UPDATE `WHERE version=expected`);
несовпадение → `VERSION_CONFLICT`. Идемпотентные ключи (таблица
`idempotency_keys`) на create VP Spec/Work Order/JobPackage/checkpoint/handoff/
merge/split — повтор возвращает прежнюю сущность, дубликатов нет. `content_hash`
= `sha256:` над canonical-JSON (`sort_keys`, компактные разделители). **Один
Builder-writer** на worktree через `worktree_leases` UNIQUE(worktree,
released_at) — второй writer → `WRITER_CONFLICT`. Planner/Reviewer read-only.
Append-only история переходов (`work_order_events`). Стабильные коды ошибок.

## Immutable scope и acceptance criteria

Оптимизатор и контекст-движок **никогда** не меняют scope и acceptance criteria.
Правки Brief/Map/envelope/approval создают новую версию (не переписывают).
Acceptance criteria несут устойчивые ID; их сохранение при merge/split —
проверяемый инвариант.

## Relevance и context-budget

`atlas_core.context_engine` собирает immutable **JobPackage** только из
релевантного (IDs/хеши, goal, законченный результат, source of truth, baseline,
инструкции с scope/precedence, scope/out-of-scope, inputs, criteria, checks,
test impact, capabilities, prohibited, stop conditions, artifact-ссылки, точное
следующее действие). **Исключает** весь repo, полный chat, повторяющиеся logs,
credentials, env-дампы, посторонние идеи, unbounded-вывод (проверка ключей +
секрет-скан). Границы: `MAX_PACKAGE_BYTES=24000`, `MAX_LIST=40`. Ёмкость честно
`UNKNOWN` (§11.6, volatile-поля исключены ради детерминированного хеша).
Capabilities — только allowlist (`CAPABILITY_ALLOWLIST`); запрещённые
(`force_push`, `production_deploy`, …) отклоняются `CAPABILITY_DENIED`.
Контракт [`job-package.json`](../../contracts/schemas/job-package.json).

## Оптимизатор и инварианты merge/split

Решения ровно из набора: `READY`, `MERGE_TASKS`, `SPLIT_AT_CHECKPOINT`,
`SWITCH_PROFILE`, `OWNER_REQUIRED`. Каждое — стабильный reason-код, ограниченное
объяснение, затронутые Work Order ID, точное следующее действие. **Scope/criteria
не меняются.**

**Merge** только для совместимых: тот же проект/VP; совместимые роль/capabilities;
совместимые source-of-truth/baseline; без конфликта immutable-ограничений/stop;
ограниченный результирующий JobPackage; без скрытого расширения scope; **точное
сохранение всех acceptance criteria** (объединение по ID, общие помечаются
`shared`, не дублируются). **Split** только на durable-checkpoint, по двум
независимо законченным результатам; каждый критерий назначен без потери; общие
явно помечены; immutable-ограничения и out-of-scope копируются точно;
parent/child и отображение критериев durable и auditable. Недоказуемая
совместимость/намерение → `OWNER_REQUIRED` (не гадаем). Потеря критерия →
`CRITERIA_LOST`.

## Checkpoint и handoff

**Checkpoint** (`wo_checkpoints`, hash-verifiable, переживает рестарт): IDs/хеши
Work Order и пакета, baseline+текущий HEAD, изменённые файлы, ограниченные
команды/исходы, ошибки/блокеры, выполненные и оставшиеся критерии, решения,
impacted checks, artifact-ссылки, состояние аренды/writer, точное следующее
действие, время, actor. **HandoffPackage** (`handoff_packages`, immutable,
контракт [`handoff-package.json`](../../contracts/schemas/handoff-package.json)):
project/VP/VP Spec/Work Order/JobPackage/checkpoint/correlation IDs; goal и
законченный результат; immutable-ограничения; source-of-truth; baseline+текущий
HEAD; изменённые файлы; команды/исходы; ошибки; полная матрица приёмки; решения;
точное следующее действие; запрещённые действия; artifact-ссылки; schema-версия
и content-hash. Свежий агент сверяет пакет с фактическим Git/DB: **факт
побеждает**, расхождение — в audit.

## Триггеры и алгоритм ротации

Context Governor (`atlas_core.governor`) распознаёт триггеры: порог контекста
(`CONTEXT_THRESHOLD_BYTES=20000`), повтор/провал (`REPEAT_THRESHOLD=2`), граница
checkpoint, rate-limit/смена профиля, crash/recovery, провал review, граница VP,
явная команда владельца — и отображает их в состояния оптимизатора, **не
отбрасывая молча критерии/ограничения**. Алгоритм §16.5: (1) стоп новых
действий; (2) снять diff/процесс; (3) impacted checks; (4) checkpoint
persist+verify; (5) handoff build+verify; (6) release аренды **только здесь**;
(7) выбор профиля как запрос (не VP-5 routing); (8) свежая сессия; (9) ack
точного hash+baseline; (10) продолжение только после ack. Во время ротации
**второй writer не появляется** (`one_writer_ok`).

## Отклонение stale/tamper

Handoff отклоняется при: неверном content-hash (`HASH_MISMATCH`); устаревшей
версии VP Spec/Work Order (`HANDOFF_STALE`); несовпадении Brief/Map/approval
(`SCOPE_DRIFT`/`SOURCE_STALE`); рассогласовании baseline/current HEAD с фактом
(`HANDOFF_STALE`); неизвестном/заменённом checkpoint; недоступном проекте
(`PROJECT_NOT_AVAILABLE`); role/capabilities сверх Work Order
(`CAPABILITY_DENIED`); отсутствии обязательных полей. Ack неверного hash → REJECT.

## API scope (под `/api/v1/projects/{id}`)

`vp-specs` (create/list/detail); `work-orders` (create/list/detail/transition);
`optimizer/{evaluate, merge/preview, merge/confirm, split/preview, split/confirm,
decisions}`; `work-orders/{wid}/job-package`, `job-packages`, `context/compact-probe`;
`work-orders/{wid}/checkpoints`, `checkpoints/{id}/verify`; `work-orders/{wid}/handoffs`,
`handoffs/{id}/{verify, acknowledge, reject, reconstruct, acks}`;
`governor/detect`, `work-orders/{wid}/rotate`, `rotations/{id}/continue`.
Версионирован, типизирован, стабильные коды, `Idempotency-Key`/`X-Correlation-ID`.

## UI scope

Вкладка «Work Orders» в проекте: empty-state «нужен принятый Brief/Map»; сводка
VP Spec с версиями/хешами; список и деталь Work Order; статус текст+символ (не
только цвет); goal/scope/out-of-scope/criteria/checks/capabilities/stop;
сводка релевантного контекста; решение оптимизатора и причина; merge-preview с
отображением критериев; состояние checkpoint; сводка Handoff и hash; состояния
stale/conflict/owner-required; результат свежей сессии/ack; точное следующее
действие; loading/empty/offline/stale/conflict/forbidden/error. RU-дефолт +
EN-переключение, dark default, Ember, keyboard/focus/skip/landmarks, reduced
motion, responsive 390/768/1024/1440.

## Стабильные ошибки

`VERSION_CONFLICT`, `INVALID_TRANSITION`, `SCOPE_DRIFT`, `CRITERIA_LOST`,
`CONTEXT_LIMIT`, `HANDOFF_STALE`, `HASH_MISMATCH`, `OWNER_REQUIRED`,
`PROJECT_NOT_AVAILABLE`, `SOURCE_STALE`, `MERGE_INCOMPATIBLE`, `WRITER_CONFLICT`,
`SPEC_INVALID`, `WO_INVALID`, `SPLIT_INVALID`, `CAPABILITY_DENIED`, `NOT_FOUND`.

## Audit и redaction

Каждая мутация пишет append-only Audit (actor, project, correlation,
entity/version/hash, переход/решение, redacted-summary). Audit НЕ содержит
полных Work Order, чатов, credentials, env-дампов, сырого вывода команд. Весь
owner-текст redacted и bounded; секреты в durable-состояние не попадают
(redaction + сканер).

## Приёмочная матрица (26)

Полная 26-пунктовая матрица реализована в
[`scripts/run_vp4_acceptance.py`](../../scripts/run_vp4_acceptance.py); evidence
с SHA-256 — в `var/artifacts/vp4/`. Кратко: VP Spec из принятого Brief/Map;
валидация по схемам; точная привязка источника; валидные/невалидные переходы;
stale/concurrency; идемпотентность; relevance/исключения JobPackage; контекст не
расширяет права; READY/MERGE/SPLIT/OWNER_REQUIRED/SWITCH_PROFILE; сохранение
критериев merge/split; пороги/ротация без выдуманной ёмкости; checkpoint
переживает рестарт и hash-verifiable; полный Handoff; отклонение
tamper/stale/wrong-project/wrong-version/over-capability; свежая сессия; ротация
с одним writer; compact-fallback; durability после рестарта; RU/EN UI; миграции;
regression VP-1/2/3; финальный секрет-скан; один активный VP-гейт.

### Результат приёмки

`scripts/run_vp4_acceptance.py` — **26/26 PASS** против развёрнутого стека
(Compose Core/Web + systemd Runner) на синтетических фикстурах; фикстуры
удаляются по точным ID, append-only Audit сохраняется. Живая БД мигрирована
`0003_product_map → 0004_work_orders`; backup снят до миграции. Reconstruction
исполняется **внутри Core-образа** изолированным `scripts/vp4_fresh_consumer.py`;
регрессия упаковки — `scripts/check_core_image.sh` и
`tests/test_vp4_packaging.py`. Evidence с SHA-256 — `var/artifacts/vp4/`.

## Тесты

`tests/test_vp4_workorders.py` (юнит/интеграция, реальный изолированный
потребитель): целевые, негативные, concurrency, restart, migration (CI),
regression. Полная приёмка — `scripts/run_vp4_acceptance.py`. Схемы —
`scripts/validate_schemas.py`.

## Формат evidence

JSON-артефакты с командами и кодами возврата, временем/версиями, branch/HEAD/
migration ID, релевантными ID и точными content-hash, lifecycle/решениями
оптимизатора, отображением критериев до/после merge/split, хешами checkpoint/
handoff, результатом свежей сессии, доказательством restart и одного writer,
redacted-результатами; SHA-256 файлов evidence.

## Out of scope (граница VP-4)

НЕ входит: реальная автономная маршрутизация ролей и полный Run-движок (VP-5);
Quality Firewall (VP-6); GitHub delivery-автоматизация и Time Machine (VP-7);
полная консоль VP-8; реальные provider-вызовы (compact-probe — локальный
детерминированный harness); выбор LICENSE. VP-4 готовит исполнимые контракты и
контекст; оркестрацию владеет VP-5.
