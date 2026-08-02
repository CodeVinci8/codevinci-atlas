# VP-3 — Product Map (исполнимый спек)

**Статус:** ЗАВЕРШЁН — 26/26 PASS, СМЁРЖЕН в `main`.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §36 (при конфликте — §1).
**Ветка:** `atlas/vp-3-product-map` · **PR:** #4 (squash) · **merge:** `07ed6f4`.

## Result и Definition of Done

Подключённый синтетический проект принимает структурный **intake**, из него
рождаются versioned **Draft Brief** и **Draft Map** с явными truth-status;
владелец **поштучно** принимает/отклоняет предложенные решения, **утверждает**
точную версию Brief и scope-envelope; система показывает durable **Project Map**
и правдивую **Portfolio Map**, сравнивает версии (**diff**), ведёт **parking
lot** и **экспортирует** принятое состояние в **Markdown** и **JSON**.

DoD: 26 приёмочных пунктов (см. ниже) проходят против реально развёрнутого
стека; миграция применяется из пустой БД (`0001→0002→0003`) и из копии живой
`0002` без потери данных VP-2; секрет-скан чист; регрессий VP-1/VP-2 нет; стек
здоров на `http://127.0.0.1:3210`.

## Inputs (bounded intake, §36, Phase E)

Владелец предоставляет только данные (§30.2 — данные не расширяют права и не
исполняются): idea/problem, target user, desired result, constraints, risks,
links (санированные метаданные ссылок), baseline-references (ID/hash VP-2
baseline), permissions/scope-notes, parking-lot suggestions. Repo/issues/web/
вывод модели — данные. VP-3 **не** ходит по внешним ссылкам и **не** делает
model/provider-вызовов; хранит только санированный текст и метаданные ссылок.

## Outputs

Draft/approved **Brief version** (immutable), versioned **Map snapshot**
(nodes+edges), **decisions** с историей, **scope envelope**, **parking lot**,
**approval record**, **diff** (Brief и Map), детерминированный **экспорт**
MD/JSON, **Portfolio Map** проекция, **Audit**-события.

## Truth-status (§15.4, Phase E)

Каждый факт несёт ровно один статус: `VERIFIED`, `OWNER_PROVIDED`, `INFERRED`,
`HYPOTHESIS`, `STALE`, `UNKNOWN`.

- `VERIFIED` требует **реальной resolvable evidence-ссылки и content-hash**.
  Отсутствующая/поддельная/устаревшая/несовпадающая ссылка `VERIFIED` создать
  не может (`EVIDENCE_INVALID`).
- Approval владельца **не** превращает гипотезу в `VERIFIED` автоматически.
- `OWNER_PROVIDED` фиксирует провенанс владельца без претензии на независимую
  проверку. Inference и hypothesis остаются видимо различимы в API/экспорте/UI.
- Переходы truth-status аудируются.
- Допустимый тип evidence в VP-3 — **VP-2 git baseline** (`kind=git_baseline`,
  `ref=<baseline_id>`, `hash=<baseline.content_hash>`); проверяется резолвом
  baseline проекта и сверкой hash. Полная система Evidence — VP-6, не здесь.

## Версии и concurrency (Phase F)

- Brief и Map — **immutable версии** с монотонным `version` (per-project),
  `parent_id`, `content_hash` (`sha256:` над canonical-JSON). Правка = новая
  версия, связанная с родителем; approved-версия никогда не мутируется.
- **Оптимистичная блокировка**: мутации принимают `expected_version`; расхождение
  → `VERSION_CONFLICT` (409) без тихой перезаписи.
- **Идемпотентность**: мутации принимают `Idempotency-Key`; повтор возвращает
  прежний результат и не создаёт дублей версий/approval (таблица
  `idempotency_keys`).
- **Один активный VP** — durable-инвариант на границе транзакции: таблица
  `vp_activations` с `UNIQUE(project_id, active_slot)`; вторая активация
  детерминированно падает `ACTIVE_VP_CONFLICT` (включая bounded-concurrency).

## Owner approval boundary (Phase F)

Approval **обязан падать**, если: клиент пишет против устаревшей версии
(`VERSION_CONFLICT`); есть неразрешённые required-решения (`DECISION_UNRESOLVED`);
envelope отсутствует/внутренне противоречив (`ENVELOPE_INVALID`); `VERIFIED`-факт
имеет невалидное evidence (`EVIDENCE_INVALID`); ссылки на узлы Map невалидны
(`MAP_INVALID`); проект недоступен по политике (`PROJECT_NOT_AVAILABLE`).

Approval **связывает**: Brief version ID + content-hash, Map version ID, envelope
hash, состояния принятых решений (decisions-hash), actor и timestamp.

## Durable-модель (Phase D, Alembic `0003_product_map`)

Таблицы (только Alembic; ORM-автосоздание в проде запрещено). Общие поля где
применимо: сортируемый `id`, `project_id`, UTC `created_at`/`updated_at`, `actor`,
`correlation_id`, `version` (optimistic), soft-archive (`archived`/`status`),
`content_hash`, bounded+redacted содержимое.

- `product_intakes` — bounded owner-intake (санированный JSON payload + refs).
- `briefs` — immutable версии Brief: `version`, `parent_id`, `status`
  (`draft|approved|superseded|rejected`), `content_json`, `content_hash`,
  `envelope_json`, `envelope_hash`.
- `map_versions` — снапшоты Map: `version`, `parent_id`, `status`, `content_hash`.
- `map_nodes` — `map_version_id`, `node_key`, `node_type`
  (`goal|user_problem|brief_decision|vp|blocker|evidence_ref|next_action|parking_item`),
  `title`, `detail`, `truth_status`, `evidence_ref`, `evidence_hash`, `data_json`.
- `map_edges` — `map_version_id`, `src_key`, `dst_key`, `edge_type`
  (`dependency|blocks|proves|includes|next`).
- `decisions` — `decision_key`, `title`, `detail`, `status`
  (`proposed|accepted|rejected`), `required`, `truth_status`, `note`, `version`.
- `decision_events` — append-only история переходов решения (`from`/`to`/`note`).
- `parking_items` — `title`, `reason`, `return_condition`, `status`
  (`parked|promoted|archived`), `version`.
- `approvals` — `brief_id`, `brief_hash`, `map_version_id`, `envelope_hash`,
  `decisions_hash`, `actor`, `created_at`.
- `vp_activations` — `vp_key`, `active_slot` (`ACTIVE`|`id`), `activated_at`,
  `deactivated_at`; `UNIQUE(project_id, active_slot)`.
- `idempotency_keys` — `key` (PK), `scope`, `entity_id`, `created_at`.

Upgrade доказуемо работает из пустой БД (`0001→0002→0003`) и из копии живой
`0002` без потери данных VP-2 (downgrade `0003→0002` предусмотрен).

## API (Phase I, namespace `/api/v1/projects/{id}`)

Мутации: `Idempotency-Key` (опционально), `expected_version` где есть версия;
ошибки — стабильный код + локализуемое сообщение + correlation ID.

- `POST …/intake` — принять intake, создать Draft Brief v1 + Draft Map v1 +
  proposed decisions. `GET …/intake` — последний intake.
- `GET …/briefs` · `GET …/briefs/{brief_id}` — версии Brief.
- `POST …/briefs/{brief_id}/revise` — новая версия из родителя (field-changes +
  `expected_version`).
- `GET …/briefs/diff?from=&to=` — детерминированный field-level diff.
- `POST …/briefs/{brief_id}/approve` — approve точной версии (+ envelope, map).
- `GET …/decisions` · `POST …/decisions/{decision_id}/accept` ·
  `POST …/decisions/{decision_id}/reject` — поштучно, история сохраняется.
- `GET …/parking-lot` · `POST …/parking-lot` — parking items.
- `GET …/map` · `GET …/map/versions` · `GET …/map/diff?from=&to=` — Project Map.
- `POST …/map/vps/activate` — активировать один VP (durable-инвариант).
- `GET …/export?format=json|md&version=` — детерминированный экспорт.
- `GET /api/v1/portfolio` — Portfolio Map (проекция; отсутствующее — `UNKNOWN`).

Каждая мутация пишет append-only Audit (actor/project/correlation/version/hash +
redacted summary; без полного приватного ввода и credentials).

## UI scope (Phase J, Ember RU/EN)

Portfolio Map; project Map view (структурный, не free-form граф-редактор);
структурный intake; Draft Brief; truth-status badges (текст+источник, не только
цвет); поштучный accept/reject; approval Brief; scope envelope; parking lot;
история версий; field-level diff; экспорт MD/JSON; точный next action; состояния
loading/empty/stale/conflict/offline/error. Dark по умолчанию; Ember-оранжевый —
основное действие/активная навигация; семантические цвета только для правдивых
состояний; одно основное действие на экран; клавиатура/focus/skip/landmarks;
reduced-motion; responsive 390/768/1024/1440 без горизонтального переполнения.

## Экспорт (Phase H)

JSON (документированная `schema_version`) и человекочитаемый Markdown для точной
версии: Brief version, Map version, decisions, truth-статусы, envelope, parking
lot, состояние проекта, hashes и timestamps. Детерминирован для одной и той же
принятой версии (кроме явно помеченной generated-метаданных). Без credentials,
env-дампов, raw auth-путей, безграничного содержимого репо и небезопасного HTML.

## Acceptance boundary (§36) — 26 пунктов

Прогон `scripts/run_vp3_acceptance.py` против реального стека и синтетических
фикстур; итог — `var/artifacts/vp3/acceptance_matrix.json`. COMPLETE только при
26/26 PASS.

| # | Проверка |
|---|----------|
| 1 | Подключённый синтетический проект принимает bounded intake |
| 2 | Intake создаёт Draft Brief и Draft Map с явными truth-status |
| 3 | `OWNER_PROVIDED/INFERRED/HYPOTHESIS/STALE/UNKNOWN` персистятся и различимы |
| 4 | `VERIFIED` без валидного evidence отклоняется |
| 5 | Валидный evidence ID/hash поддерживает `VERIFIED` |
| 6 | Первая версия Brief immutable |
| 7 | Правка создаёт вторую версию, связанную с родителем |
| 8 | Устаревшая expected-версия отклонена без тихой перезаписи |
| 9 | Diff версий сообщает точные added/removed/changed поля/узлы/рёбра |
| 10 | Решения принимаются/отклоняются поштучно с сохранением истории |
| 11 | Approval заблокирован при неразрешённых required-решениях |
| 12 | Approval связывает точные Brief/Map/envelope/decisions-hash |
| 13 | Невалидные/висячие рёбра Map отклоняются |
| 14 | Parking-lot вне активного scope и переживает версионирование |
| 15 | Ровно один активный VP; вторая активация отклонена (bounded-concurrency) |
| 16 | Brief/Map/decisions/approval переживают рестарт Core |
| 17 | Portfolio Map корректна на ≥3 состояниях и не выдумывает данные |
| 18 | Экспорт MD и JSON представляют ту же принятую версию, без секрет-маркеров |
| 19 | RU/EN контролы, паритет каталогов и принятые UI-состояния |
| 20 | Dark default, keyboard/focus/non-color status, responsive DOM/CSS |
| 21 | Миграция из пустой БД и из копии живой `0002` без потери данных VP-2 |
| 22 | Нет регрессий VP-1 (health/audit/UDS/backup) и VP-2 (projects/baseline/worktree/lease/security) |
| 23 | Core/Web/Runner non-root и healthy |
| 24 | Ровно один активный VP-гейт репозитория при разработке |
| 25 | `127.0.0.1:3210` отдаёт принятый VP-3 UI через Web-прокси |
| 26 | Финальный секрет-скан чист (дерево/история/БД/логи/artifacts/exports/фикстуры) |

## Tests (Phase K/L)

Targeted-юниты: версии/concurrency, truth/evidence, node/edge-валидация,
one-active-VP, экспорт-детерминизм. Негативные: `VERIFIED` без evidence,
stale-write, dangling edge, second-VP, unresolved-decision approval. Регрессия:
приёмки VP-1/VP-2 без относящихся регрессий. Fixtures — уникальные ID/prefix;
чистка только своих записей и allowlisted-каталогов. Полная регрессия не
запускается после микроправки без risk-триггера (§18.5).

## Out (не входит в VP-3)

Work Orders, VP Specs, Optimizer/Context Governor, Planner/Builder/Reviewer
исполнение, полная система Evidence (VP-6), Time Machine, GitHub merge
automation, полная консоль VP-8, тема/настройки VP-8, реальные Codex/Claude
probe, адаптеры/credentials/handoff, free-form граф-редактор. VP-node в Map —
только planning-состояние; promotion parking→scope делает лишь явная новая версия.
