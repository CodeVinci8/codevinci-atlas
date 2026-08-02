# Reuse Register — решения по внешним проектам

**Статус:** обновлено в рамках VP-0 (+ фокус-аудит Sub2API перед VP-1 Web-shell).
**Основание решений (evidence):**
- правило владения credentials — один auth owner на профиль (Master Spec §11.1);
- VP-0 реализовал изоляцию/lease/handoff/Runner **нативно**, без копирования
  чужого кода (см. `apps/`, `tests/`, `scripts/run_acceptance.py`);
- лицензии и польза зафиксированы в Master Spec §24;
- любое **ADOPT** кода требует pinned commit/tag, license/NOTICE, security-обзора,
  диаграммы владения credentials, границы адаптера и пути удаления (§24).

## Таксономия

- **ADOPT** — код включается напрямую (с pinned commit и всеми условиями §24).
- **WRAP** — используется как отдельный sidecar/зависимость за адаптером.
- **REFERENCE** — берём идеи/поведение, код не копируем.
- **REJECT** — не используем.
- **SPIKE** — требуется предметная проверка перед WRAP; решение отложено до VP.

## Решения VP-0

| Проект | Лицензия | Решение VP-0 | Обоснование (evidence) | Граница |
|---|---|---|---|---|
| [Sub2API](https://github.com/Wei-Shaw/sub2api) | LGPL-3.0 + no-commercial notice | **REFERENCE** | LGPL + пометка о некоммерческом использовании несовместимы с копированием в open-source ядро; полезны идеи account-states/pool-UI/scheduler | Только UI/поведение; код ядра не копируется. Пул/состояния реализованы нативно (`atlas_core.profiles`). |
| [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | MIT | **SPIKE → возможный WRAP (позже)** | OAuth Codex/Claude, multi-account; но нарушает правило «один auth owner на профиль», если станет вторым владельцем credentials | Только как опциональный sidecar с собственным auth-root; никогда не общий credential owner. Для VP-0 не требуется. |
| [codex-multi-auth](https://github.com/ndycode/codex-multi-auth) | MIT | **SPIKE (позже)** | Профили/health/rotation Codex; требует проверки актуального CLI-контракта и владения | Проверить перед VP-5. Изоляция уже доказана нативно (`test_profiles_isolation`). |
| [CCS](https://github.com/kaitranntt/ccs) | MIT | **REFERENCE** | Изолированные профили/switching/dashboard — концептуально близко | Не копировать рантайм целиком; конвенция root у нас своя (`CODEX_HOME`/`CLAUDE_CONFIG_DIR`). |
| [claude-code-router](https://github.com/musistudio/claude-code-router) | MIT | **REFERENCE** | Идеи routing/failover/observability | Не обязательный шлюз; router у нас нативный (§17.3). |
| [ccusage](https://github.com/ccusage/ccusage) | MIT | **SPIKE/WRAP (позже)** | Локальные отчёты usage Claude/Codex | Usage ≠ точный остаток лимита. Пока capacity=UNKNOWN честно (§11.6). |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | MIT | **REFERENCE** | Spec-driven workflow | Идеи, не полный генератор. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | MIT | **REFERENCE** | Паттерны runtime/events/self-host | Слишком тяжёл как зависимость; UDS-Runner реализован минимально нативно. |

## Фокус-аудит Sub2API (read-only, перед VP-1 Web-shell)

**Инспектированный commit:** `Wei-Shaw/sub2api@b74024c7868ee88a0bf921306cbc22a2f922872a`
(default branch `main`, LGPL-3.0). Аудит **read-only**, код не копировался.
**Инспектированные файлы:** `frontend/src/views/admin/AccountsView.vue` (92KB),
`frontend/src/views/admin/GroupsView.vue`, `backend/ent/schema/account.go` (9KB),
`backend/internal/service/openai_account_scheduler.go` (95KB),
`backend/internal/service/ops_health_score.go` (4KB),
`frontend/src/views/admin/ops/OpsDashboard.vue` (27KB), `README.md`, `LICENSE`.

### Что наблюдалось (концепты, не код)

1. **Операционная плотность.** Таблицы аккаунтов с фильтрами и сортировкой;
   индексы схемы по `status`, `priority`, `last_used_at`, `schedulable`,
   `rate_limited_at`, `rate_limit_reset_at` — подтверждают сортируемые/фильтруемые
   колонки и компактные статус/ёмкость/cooldown-представления.
2. **Поведение пула.** Явное разделение общего состояния (`status`:
   active/error/disabled) и планируемости (`schedulable`); метаданные приоритета
   и конкуренции (`priority`, `concurrency`, `load_factor`, `rate_multiplier`);
   `last_used_at`; rate-limit сброс (`rate_limited_at`/`rate_limit_reset_at`);
   временная недоступность с причиной (`temp_unschedulable_until`/`_reason`,
   `overload_until`); членство в группах (edge `groups`).
3. **Планировщик.** Eligibility до scoring; сигналы health/capacity/cooldown/
   recent-use; детерминированный tie-break; наблюдаемые error-rate/latency.
4. **Операционный обзор.** `ops_health_score.go`: health **вычисляется бэкендом
   из реальных наблюдений** — слоистый score (Business 70% + Infra 30%; error-rate
   + TTFT), с явным idle/gray-состоянием при отсутствии трафика (не «плохо») —
   ложится на Atlas `UNKNOWN/STALE/OFFLINE/loading/empty/error`.

### Решение: **REFERENCE** (только идеи/поведение)

| Аспект | Atlas-ссылка | Явная граница (НЕ копировать) |
|---|---|---|
| Плотные таблицы/фильтры | идеи для Profiles-таблицы (VP-8) | без Vue-компонентов, стилей, строк, разметки экранов |
| Разделение state/schedulable | health/cooldown/drain модель (§11.3) | без копирования схемы `account.go` |
| Сигналы планировщика | Router §17.3 (нативный) | без переноса взвешенного планировщика Sub2API |
| Health из наблюдений | truthful health/`UNKNOWN` (§11.5/§31) | без копирования `ops_health_score.go` |

### Жёсткие запреты (подтверждены аудитом)

- **Никакого кода/компонентов/стилей/строк/ассетов/схем/разметки экранов Sub2API.**
- Sub2API хранит `credentials` **в строке аккаунта** (JSON-поле) — это
  централизованная БД credentials, которую Atlas **НЕ принимает**: правило одного
  auth owner на профиль (§11.1). Credentials Atlas живут только в изолированных
  auth-root профилей.
- Без API-gateway/relay, billing/payment, proxy, user-management, коммерческой
  инфраструктуры Sub2API.
- Без переноса native-сессий между разными владельцами credentials.
- LGPL-3.0 + явная пометка «No Commercial Authorization» в README →
  консервативная граница «no-copy» сохраняется даже при неопределённой лицензии
  Atlas.

### Границы VP-1 (scope не расширять)

Профильный пул/маршрутизация — это Agent Pipeline VP (§38). Полный плотный
Profiles/Pulse — Full Web Console VP (§41). VP-1 реализует ТОЛЬКО foundation,
минимальный RU/EN Web-shell, truthful health и видимость Runner-offline (§34).

## Визуальные референсы VP-2 (Web Console, REFERENCE)

Владелец утвердил два **визуальных** референса для CodeVinci Ember-консоли.
Решение — **REFERENCE**: берётся только визуальный язык/поведение, **код,
компоненты, стили, строки, лейауты, логотипы, иконки, ассеты и брендинг не
копируются**; ни один экран не воспроизводится один-в-один.

| Референс | Что взято (идея) | Явная граница (НЕ копировать) |
|---|---|---|
| **TonWave** | визуальная иерархия, сильная типографика, тёмные премиальные поверхности, Ember-оранжевый акцент, крупные заголовки страниц и явное primary-действие | без копирования компонентов/стилей/разметки/ассетов/брендинга; свой лэйаут и токены |
| **Sub2API** | операционная плотность, компактные статусы, фильтры/группировка, паттерны карточек/таблиц, точная видимость health и dirty-state | сохраняется консервативная no-copy граница (см. фокус-аудит выше); без Vue-кода/схем/строк/экранов |

**Реализация Atlas (собственная).** Ember developer-cockpit: тёмные поверхности,
тёплый off-white текст, Ember-оранжевый как главный акцент действия и активной
навигации; зелёный/янтарный/красный/синий — только для правдивых семантических
состояний; статус — не только цветом; sidebar 232–256 px; сильный заголовок +
тихое описание; компактные операционные карточки и таблицы во всю ширину;
monospace только для путей/веток/SHA/команд/ID; одно primary-действие на экран.
**Запрещено:** фейковые проценты/активность/ёмкость/декоративный прогресс,
AI-градиенты, избыточное стекло/частицы/постоянная анимация. Источник истины —
Master Spec (§28), а не сами дашборды. Файлы: `apps/web/src/{App.tsx,styles.css,
i18n.ts,locales/*}` — написаны нативно, без заимствований кода референсов.

## Итог VP-0

Кода из внешних проектов **не адоптировано**. Изоляция профилей, writer-lease,
checkpoint/handoff, UDS-Runner и классификация ошибок реализованы нативно и
доказаны приёмкой. Все кандидаты остаются REFERENCE или SPIKE до отдельного
adoption-гейта с полными условиями §24.

## Что нужно до любого ADOPT (чек-лист §24)

- [ ] pinned commit/tag;
- [ ] совместимость лицензии + `NOTICE`;
- [ ] security-обзор;
- [ ] диаграмма владения credentials (нет второго owner);
- [ ] граница адаптера и путь удаления;
- [ ] запись решения в `docs/DECISIONS.md`.
