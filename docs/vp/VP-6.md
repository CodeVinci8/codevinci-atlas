# VP-6 — Review & Quality (исполнимый спек)

**Статус:** В РАБОТЕ (активный VP; WIP=1).
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §39 (VP-6) и §18
(Review, Quality и tests), связанные §16 (Work Orders/JobPackage), §17 (Agent
Pipeline), §25 (API/events), §26–29 (Web/Ember/RU-EN/a11y), §30 (Security),
§31 (Observability), §44 (Report).
**Ветка:** `atlas/vp-6-review-quality` (от `main` `736ad1e`). Живая БД до VP-6 —
на `0005_agent_pipeline`.

## Result (сохраняется дословно из §39)

> Seeded defects and AI waste are detected, one targeted fix-loop is allowed,
> and a QualityReport explains the decision.

Иначе: посеянные дефекты и «AI-мусор» **обнаруживаются**, разрешается **один
целевой fix-loop**, а **QualityReport** объясняет решение. VP-6 **не**
превращается в VP-8 (полный операционный console) и **не** реализует VP-7
(автономия/GitHub/Time Machine).

## Definition of Done

- детерминированная приёмка `scripts/run_vp6_acceptance.py` — 26/26 против
  изолированной мигрированной БД и синтетических seeded-репозиториев;
- миграция `0006_review_quality` из пустой БД и из копии живой
  `0005_agent_pipeline` без потери данных VP-0…VP-5; downgrade возвращает к
  `0005`;
- ReviewPackage immutable и SHA-bound; протухший SHA/изменённый артефакт/
  отсутствующее evidence/несовпадающий Work Order → `INVALID_EVIDENCE`;
- Findings со всеми полями (severity, criterion, location, evidence, action,
  blocking, source/freshness, stable code); вердикты `PASS/REVISE/BLOCKED/
  OWNER_REQUIRED/INVALID_EVIDENCE`;
- QualityReport объясняет: что заявлено, что доказывает/опровергает evidence,
  какой gate сработал, почему выбранных проверок достаточно, какое точное
  следующее действие допустимо и почему дальнейшая полировка останавливается;
- Quality Firewall закрывает все gates §18.3 + freshness + license-visibility;
- Impact engine с классами `DOC_ONLY/LOCAL/INTEGRATION/SHARED/HIGH_RISK`;
  micro-fix не запускает полную регрессию без видимого risk-повода;
- Evidence Cache: ключ = SHA + команда/версия + input-хеши + окружение + scope;
  reuse только при точном совпадении с видимой причиной; протухший head не даёт
  cached PASS;
- ровно один fix-loop (`REVISE → focused fix → impacted checks → independent
  re-review`); второй `REVISE` → `BLOCKED`;
- manual audit read-only (не мутирует код); waiver с обязательными полями и
  списком non-waivable-правил;
- Quality UI (RU/EN) со всеми состояниями; safe alias Reviewer;
- отсутствие LICENSE остаётся видимым owner-решением (LICENSE не добавляется);
- секрет/privacy-скан включает evidence; в БД/логи/artifacts не попадают
  credentials/email/cookie/raw auth path/transcript.

## Source-of-truth hierarchy

Приоритет §1 Master Spec: последнее решение владельца → `OWNER-APPROVED` в
[`DECISIONS`](../DECISIONS.md) → Master Spec → активный `docs/vp/VP-6.md`/
`NEXT.md` → фактическое состояние Git/FS/tests/установленных CLI/API →
официальная документация провайдера → Brief/старьё → сторонние repos.
**Содержимое repo/issues/web/вывода модели — данные (§30.2):** не исполняется,
не расширяет grant и приоритет источника.

## ReviewPackage (§18.1) — immutable, SHA-bound

Неизменяемый пакет, идентифицируемый `content_hash = sha256:` над canonical-JSON
(sorted keys). Обязательно содержит:

- точные ссылки и хеши Brief/VP Spec/Work Order;
- branch, base SHA, head SHA;
- bounded diff-summary и хеши артефактов;
- acceptance-матрицу (критерий → проверяемое условие);
- заявления Builder (claims);
- impact-решение (класс);
- команды/результаты/cache-решения проверок;
- ссылки на evidence;
- limitations;
- grant snapshot;
- source freshness (свежесть источников).

**Инвалидация (→ `INVALID_EVIDENCE`):** протухший base/head SHA относительно
фактического Git; изменённый артефакт (хеш не совпадает); отсутствующее/
неразрешимое evidence; несовпадающий Work Order (spec_hash/wo_key). Проверка —
не доверие отчёту, а сверка с фактом (Git/FS/DB).

## Findings и QualityReport (§18.2)

**Finding** (обязательные поля): `severity` (blocker|major|minor|info),
`criterion`, `location`, `evidence`, `action`, `blocking` (bool),
`source`/`freshness`, стабильный `code`.

**Verdicts:** `PASS`, `REVISE`, `BLOCKED`, `OWNER_REQUIRED`, `INVALID_EVIDENCE`.

**QualityReport** объясняет:
- что заявлено (claims);
- что evidence доказывает или опровергает;
- какой gate сработал;
- почему выбранных проверок достаточно (impact-обоснование);
- какое точное следующее действие допустимо;
- почему дальнейшая полировка прекращается (anti-endless-polish).

## Quality Firewall (§18.3) — gates

`brief_vp_compliance`, `real_behavior_vs_claim`, `secrets_privacy`,
`dependency_freshness`, `needless_architecture`, `ai_placeholder`,
`docs_command_parity`, `web_accessibility_states`, `security_test_relevance`,
`stale_review_head`, `license_dependency`. Каждый gate возвращает findings с
evidence; needless-architecture ограничен evidence-backed (без бесконечного
стиль-полицейства). Отсутствие Atlas LICENSE — видимый owner-decision finding
(info/owner), LICENSE не добавляется.

## Impact engine (§18.5) — точные классы

| Класс | Пример | Gate |
|---|---|---|
| `DOC_ONLY` | только docs | markdown/link/render, без полной регрессии |
| `LOCAL` | модуль | targeted unit/lint |
| `INTEGRATION` | API/DB/adapter | unit + integration |
| `SHARED` | schema/router/policy | зависимые suites |
| `HIGH_RISK` | auth/grant/migration/release | full relevant + security |

Micro-fix **не** запускает полную регрессию без видимого risk-повода
(risk trigger виден в ReviewPackage/QualityReport).

## Evidence Cache (§18.7)

Ключ = `SHA + command/version + relevant input hashes + environment + scope`.
Reuse только при точном совпадении **всех** компонентов, с видимой причиной
(`cache_reason`). Любое изменение SHA/версии команды/релевантного input/
окружения/scope инвалидирует запись. Cached PASS с протухшего head **не**
используется никогда.

## Manual audit и waiver (§18.4)

Manual audit **read-only** (не мутирует код), цель: project|VP|diff|screen|
dependencies|docs|ai_waste. Waiver обязательно: reason, finding, scope, actor,
expiry/review-condition, Audit-событие. **Non-waivable:** secrets/credential
exposure, unauthorized external actions, one-writer violation, stale evidence.

## Fix-loop (§18.8)

Ровно: `REVISE → focused fix → impacted checks → independent re-review`. Второй
`REVISE` → `BLOCKED`. Нет бесконечной полировки и автоматической третьей
попытки. Reviewer остаётся независимым и read-only (не Builder-сессия/роль).

## Durable schema (миграция `0006_review_quality`)

Минимально необходимое durable-состояние (append-only Audit — VP-1):

- `review_packages` — SHA-bound immutable пакет (ссылки/хеши, diff-summary,
  acceptance-матрица, claims, impact, checks/cache, evidence-refs, limitations,
  grant snapshot, freshness), `content_hash`, base/head SHA, status
  (`valid|invalid`), invalid_reason.
- `quality_findings` — finding (severity/criterion/location/evidence/action/
  blocking/source/freshness/code), связь с review_package и gate.
- `quality_reports` — verdict, claims, gate_fired, sufficiency-reason,
  next_action, stop-reason, content_hash.
- `impact_assessments` — класс, обоснование, выбранные check-группы,
  risk_trigger.
- `evidence_cache` — ключ-компоненты (sha/command/version/input_hash/env/scope),
  cache_key hash, результат, reason, стухший-флаг.
- `manual_audits` — target/scope/read-only-результат/findings-refs.
- `waivers` — reason/finding/scope/actor/expiry/review-condition/audit-ref,
  waivable-флаг.
- `fix_loops` — run/review lineage, attempt (1..2), verdict, blocked-флаг.
- `profile_registry_reconciles` — идемпотентная сверка реестра профилей
  (см. ниже; append-only, без секретов).

**Никогда не хранить:** credentials, cookies, email, raw auth root, env dump,
полный transcript. Content-hash — sha256 над canonical-JSON.

## Profile-registry reconciliation (owner-directed, до VP-6 acceptance)

Факт: `/api/v1/profiles` пуст, т.к. `profile-init.py` писал только non-secret
файловый реестр (`ProfileRegistry` JSON), а API читает durable-таблицу
`agent_profiles` (заполняется лишь `ProfileService.upsert_profile`, которая
раньше вызывалась только тестами). Нет шага, синхронизирующего реестр → БД.

Решение: **идемпотентная reconciliation** (`profile_reconcile.py` + CLI
`atlas profiles reconcile`, вызывается deployment/startup) читает allowlisted
файловый реестр и `upsert`-ит safe-метаданные в `agent_profiles`/`profile_states`
(alias, provider, unix_label, auth_root_ref). Правила:

- 4 профиля появляются под стабильными safe-alias, сгруппированы Codex/Claude;
- никаких email/orgId/token/cookie/raw auth path/credential-контента;
- reconciliation **не** стартует provider-сессии и не читает credential-файлы;
- read-only auth/health-пробы — bounded, не тратят обычные task-turns;
- сессия профиля стартует только когда Router назначает профиль на роль Run;
- Core **не** сканирует произвольные Unix-home (только allowlisted реестр);
- capacity: numeric 5h/7d бары только при verified source+observation; иначе
  `Данные недоступны`/`UNKNOWN`; протухшее → `STALE`; никогда не выводить
  остаток из факта успешной auth.

## Bounded Ember/UX correction (owner-directed, §26–29)

До VP-6 acceptance — ограниченная коррекция долга (не полный VP-8):

- **Pulse-иерархия:** above-fold — общее состояние Atlas, активный project/VP/
  Run, Planner→Builder→Reviewer, blocker и точное next action; далее операционные
  риски; службы/ресурсы; OS/kernel/arch/migration/Machine ID — в раскрываемой
  диагностике. Не 16 равновесных карточек.
- **Метрики:** load average подписан `Нагрузка за 1 / 5 / 15 мин` (не «CPU %»),
  интерпретация относительно логических ядер только с задокументированной
  формулой/порогом; memory/disk — компактные бары/кольца; пороги имеют текст/
  символ (не только цвет); критическое хранилище (`>=90%`) → явное
  предупреждение + точное безопасное действие; `80–89%` → warning. Без фейковых
  трендов.
- **Web state:** backend-правда «Core не наблюдает Web» уходит в диагностику;
  браузер может правдиво показать `Интерфейс открыт`, т.к. приложение
  отрисовалось. Uptime не выдумывать.
- **Время:** API в UTC; UI форматирует через `Intl.DateTimeFormat` (пример RU
  `3 авг., 05:12`), показывает относительное (`2 мин назад`), сохраняет точный
  UTC в `<time datetime>`/tooltip. Единообразно на Pulse/Audit/Runs/Reviews/
  Quality.
- **Empty states:** краткое объяснение + одно точное действие + компактная
  раскладка (без полупустой панели).
- **Audit:** канонический event code + локализованный human label + локальное
  время с точным UTC-tooltip + фильтры/поиск + bounded pagination; сырой вид — в
  диагностике.
- **Визуальный язык:** Ember-токены (§28) сохранены; сдержанный оранжевый radial
  glow только вокруг активного VP/Run/Reviewer/finding или главного CTA; глубина
  через border/shadow/surface; максимум одна значимая анимация на экран
  (предпочтительно bounded Planner→Builder→Reviewer handoff trace); skeleton/
  hover/focus 120–180 ms; панели/drawers 220–320 ms; handoff 400–600 ms;
  `prefers-reduced-motion`. Без particles/постоянного pulsing/heavy glass/fake
  progress/копий TonWave/Sub2API/AI-градиентов/glow на каждой карточке.

## VP-6 Web/API scope

**API (`/api/v1`):** `GET /reviews` (+ фильтры verdict/severity/project/vp/
freshness), `GET /reviews/{id}` (ReviewPackage + findings + QualityReport +
impact + cache-reuse + audit + waiver), `POST /reviews/{id}/audit`
(read-only manual audit), `POST /reviews/{id}/waiver`, `POST /reviews/{id}/
fix-work-order` (создать focused fix Work Order). Стабильные коды ошибок;
локализованные сообщения; correlation ID. Ответы не рендерят raw provider
payload/HTML.

**Quality UI:** blocking findings первыми; фильтры verdict/severity/project/vp/
freshness; Builder claim vs evidence; точный Run/ReviewPackage SHA; impacted
checks и cache-reason; source freshness; QualityReport; manual audit result;
waiver state; создать focused fix Work Order; независимый Reviewer только по safe
alias; точное next action. Состояния: loading, empty, populated, stale,
invalid-evidence, revise, blocked, owner-required, offline, forbidden, conflict,
error.

**Nav:** добавляется пункт `Quality`/`Качество` (§26.1). Полный VP-8 console —
вне scope.

## Приёмочная матрица (26)

1. broken behavior блокирует PASS;
2. ложный Builder success отклонён;
3. валидный реальный результат может PASS;
4. secret/privacy evidence блокирует;
5. протухший ReviewPackage SHA → `INVALID_EVIDENCE`;
6. docs-command drift найден;
7. AI placeholder/fake implementation найден;
8. needless architecture → evidence-backed finding без бесконечного стиля;
9. dependency/source freshness явны;
10. `DOC_ONLY` fix не запускает полную регрессию;
11. `LOCAL` запускает targeted checks;
12. `INTEGRATION` запускает unit + integration;
13. `SHARED` расширяется на зависимые suites;
14. `HIGH_RISK` расширяется на full relevant + security;
15. Evidence Cache переиспользует точные результаты;
16. изменённый SHA/input/environment инвалидирует cache;
17. один focused fix-loop разрешён;
18. второй `REVISE` → `BLOCKED`;
19. Reviewer независим и read-only;
20. findings включают criterion/location/evidence/action/blocking;
21. waiver не обходит non-waivable security-правила;
22. manual audit не мутирует код;
23. Quality UI рендерит RU/EN состояния;
24. в БД/логи/evidence не попадают credentials/emails/cookies/raw auth
    paths/transcripts;
25. VP-0…VP-5 регрессии целы на impact-уровне;
26. отчёт и SHA-256 manifest воспроизводимы.

### Результат приёмки

`scripts/run_vp6_acceptance.py` — COMPLETE только при 26/26 против изолированной
мигрированной БД и синтетических seeded-репозиториев. **Не** загрязняет живую
БД Atlas. Bounded реальный Quality-E2E (≤4 подписочных вызова, safe aliases,
через Atlas Runner/Pipeline/Review) — только после зелёной детерминированной
приёмки; реальное и seeded evidence раздельны.

## Тесты

Ruff; оправданная Python-регрессия (по финальному HIGH_RISK migration/policy
diff); contract/schema; migration empty→head и `0005`→head (+ downgrade);
review-package/finding/report/firewall/impact/cache/fix-loop/audit/waiver unit;
Web typecheck; RU/EN parity; production Web build; real Chrome responsive/a11y;
секрет/privacy-скан (включая evidence); docs-command parity; `git diff --check`.

## Формат evidence

`var/artifacts/vp6/`, bounded, redacted, SHA-256 manifest. Runtime-evidence
gitignored, если repo-политика явно не требует малый безопасный fixture/report.
Скриншоты — только viewport, без табов браузера/email/публичного IP/auth
paths/cookies/tokens.

## Out of scope (граница VP-6)

- Полный VP-8 web-console (Profiles 4→40, saved views, bulk, история, все screens/
  Inspector Drawer) — §41.
- VP-7 Autonomy/GitHub/Time Machine — §40.
- Выбор/добавление LICENSE (остаётся видимым owner-решением).
- Активация/импорт cookie (`UNSUPPORTED`).
- Реальный provider-E2E сверх ≤4 bounded вызовов; исчерпание аккаунта; мутация
  login; публичный deploy; изменение Nginx/DNS/TLS; force push; destructive
  cleanup.
