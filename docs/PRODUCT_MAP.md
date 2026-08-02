# Product Map (VP-3)

Модель и API Product Map — versioned Brief/Map, truth-status, решения,
approval, parking lot и экспорт. Каноническое ТЗ — [`MASTER_SPEC.md`](MASTER_SPEC.md)
§36; исполнимый спек — [`vp/VP-3.md`](vp/VP-3.md). English: [`en/PRODUCT_MAP.md`](en/PRODUCT_MAP.md).

## Идея

Подключённый проект принимает структурный **intake** (данные владельца, не
команды). Из него рождаются versioned **Draft Brief** и **Draft Map** с явными
**truth-status**. Владелец поштучно принимает/отклоняет предложенные решения,
утверждает точную версию Brief и scope-envelope. Система показывает durable
**Project Map** и правдивую **Portfolio Map**, сравнивает версии (diff), ведёт
parking lot и экспортирует принятое состояние в Markdown/JSON.

## Truth-status

Каждый факт несёт ровно один статус: `VERIFIED`, `OWNER_PROVIDED`, `INFERRED`,
`HYPOTHESIS`, `STALE`, `UNKNOWN`.

- `VERIFIED` требует **resolvable evidence** и совпадения `content_hash`.
  Единственный тип evidence в VP-3 — **VP-2 git baseline**
  (`evidence_ref="git_baseline:<id>"` или `":latest"`, `evidence_hash =
  baseline.content_hash`). Отсутствующая/поддельная/устаревшая/несовпадающая
  ссылка `VERIFIED` создать не может (`EVIDENCE_INVALID`).
- Approval владельца **не** превращает гипотезу в `VERIFIED`.
- `OWNER_PROVIDED` фиксирует провенанс без претензии на независимую проверку;
  inference и hypothesis остаются видимо различимы.
- Переходы статусов и решений аудируются (append-only Audit).

## Версии, concurrency, approval

- Brief/Map — immutable-версии (`version`, `parent_id`, `content_hash` =
  `sha256:` над canonical-JSON). Правка = новая версия; approved-версия не
  мутируется. Diff — детерминированный field/node/edge-level.
- Оптимистичная блокировка: мутации принимают `expected_version` (расхождение →
  `VERSION_CONFLICT`). Идемпотентность: `Idempotency-Key` (повтор не создаёт
  дублей). Один активный VP — durable-инвариант (`ACTIVE_VP_CONFLICT`).
- **Утверждённая версия** определяется неизменяемой записью `approvals`, а не
  статусом Brief: новый черновик поверх принятой версии её не «разутверждает».
- Approval **падает**, если: устаревшая версия (`VERSION_CONFLICT`),
  неразрешённые required-решения (`DECISION_UNRESOLVED`), пустой/противоречивый
  envelope (`ENVELOPE_INVALID`), невалидное evidence у `VERIFIED`-факта
  (`EVIDENCE_INVALID`), невалидные ссылки узлов Map (`MAP_INVALID`), недоступный
  проект (`PROJECT_NOT_AVAILABLE`). Approval связывает Brief+hash, Map version,
  envelope-hash, decisions-hash, actor, timestamp.

## Project Map

Узлы: `goal`, `user_problem`, `brief_decision`, `vp`, `blocker`,
`evidence_ref`, `next_action`, `parking_item`. Рёбра: `dependency`, `blocks`,
`proves`, `includes`, `next`. Отклоняются висячие ссылки, cross-project рёбра,
неизвестные типы и циклы (семантика зависимостей ациклична) → `MAP_INVALID`.
Parking-элементы — вне активного scope (причина + условие возврата), переживают
версионирование; перевод в scope делает лишь явная новая версия.

## API (`/api/v1`)

Мутации: `Idempotency-Key` (опц.), `X-Correlation-ID` (опц.), `expected_version`.

| Метод | Путь | Назначение |
|-------|------|-----------|
| POST | `/projects/{id}/intake` | intake → Draft Brief v1 + Draft Map v1 + решения |
| GET | `/projects/{id}/product-state` | сводное состояние Product Map |
| GET | `/projects/{id}/briefs` · `/briefs/{bid}` | версии Brief |
| POST | `/projects/{id}/briefs/{bid}/revise` | новая версия из родителя |
| GET | `/projects/{id}/briefs/diff?from=&to=` | field-level diff |
| POST | `/projects/{id}/briefs/{bid}/approve` | approve точной версии |
| GET | `/projects/{id}/decisions` · `/decisions/{did}` | решения |
| POST | `/projects/{id}/decisions/{did}/accept`·`/reject` | поштучно |
| GET/POST | `/projects/{id}/parking-lot` | parking lot |
| GET | `/projects/{id}/map` · `/map/versions` · `/map/diff` | Project Map |
| POST | `/projects/{id}/map/versions` | новая версия карты (валидируется) |
| POST | `/projects/{id}/map/vps/activate` | активировать один VP |
| GET | `/projects/{id}/export?format=json\|md&version=` | экспорт |
| GET | `/portfolio` | Portfolio Map (проекция) |

Каждая мутация пишет append-only Audit (actor/project/correlation/version/hash +
redacted summary; без полного приватного ввода и credentials).

## Экспорт

JSON (документированная `schema_version`) и человекочитаемый Markdown точной
версии: Brief, Map, decisions, truth-статусы, envelope, parking lot, состояние
проекта, hashes и timestamps. Детерминирован для одной принятой версии (кроме
блока `_generated`). Без credentials, env-дампов, raw auth-путей, безграничного
содержимого репо и небезопасного HTML.

## Границы (VP-3)

Данные — не команды: VP-3 **не** ходит по внешним ссылкам и **не** делает
model/provider-вызовов. Не входит: Work Orders, VP Specs, Planner/Builder/
Reviewer исполнение, полная система Evidence (VP-6), Time Machine, GitHub merge
automation, полная консоль/тема VP-8, free-form граф-редактор.
