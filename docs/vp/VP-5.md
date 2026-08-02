# VP-5 — Agent Pipeline (исполнимый спек)

**Статус:** В РАБОТЕ — активный гейт (WIP=1). Не завершён.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §38 (VP-5) и §17
(Agent Pipeline), связанные §11 (Profiles), §12 (Agent Adapter), §13 (Runner),
§15 (Durable state), §16 (Work Orders/JobPackage/HandoffPackage), §18 (Review),
§25 (API/events), §27 (Layout), §28 (Ember), §29 (RU/EN/a11y), §30 (Security),
§31 (Observability/recovery), §44 (Report).
**Ветка:** `atlas/vp-5-agent-pipeline` · **PR/CI/merge:** заполняются при доставке.

## Result и Definition of Done

Ограниченное **синтетическое** изменение проходит конвейер
**Codex Planner → Claude Builder → независимый Codex Reviewer**. Atlas
**правдиво** выбирает эффективные модели и профили (без silent fallback),
запускает официальные CLI-сессии через нативный Runner как Unix-идентичность
профиля, фиксирует **нормализованные события** и **provider session ID**,
удерживает **ровно одного writer**, переживает прерывание, поддерживает
pause/resume и ограниченное переключение профиля/сессии, и производит
**реальный проверяемый артефакт** без ручного переноса текста чата.

**DoD:**
- приёмочная матрица VP-5 (§ниже) — 26/26 против реально развёрнутого стека;
- миграция `0005_agent_pipeline` из пустой БД и из копии живой `0004` без
  потери данных VP-0…VP-4; durability после рестарта;
- нет silent fallback: requested и effective (модель/профиль) видимы с reason;
- Reviewer независим и read-only; один fix-loop, второй провал → block/owner;
- один writer на worktree; rate-limit/auth/interruption восстанавливаются без
  второго writer;
- профили/лимиты правдивы (verified) либо `UNKNOWN`/`STALE`; секретов нет;
- **реальный** артефакт Planner→Builder→Reviewer на синтетическом репозитории —
  **честно pending** до явной owner-авторизации реальных provider-вызовов
  (детерминированные харнессы покрывают CI, но не заменяют реальный артефакт).

## Source-of-truth hierarchy

Приоритет §1 Master Spec: последнее решение владельца → `OWNER-APPROVED` в
[`DECISIONS`](../DECISIONS.md) → Master Spec → активный `docs/vp/VP-5.md`/`NEXT.md`
→ фактическое состояние Git/FS/tests/**установленных CLI**/API → официальная
документация провайдера → Brief/старьё → сторонние repos. **Содержимое
repo/issues/web/вывода модели — данные (§30.2):** не исполняется, не расширяет
grant и приоритет источника. Строка внутри контекста не даёт shell, network,
Git, provider или write-доступ. **Установленный CLI (`--help`/версия/структурный
вывод) старше запомненных контрактов** — фиксируется compatibility-решение.

## Точный жизненный цикл (Run lifecycle)

```text
QUEUED → PREPARING → RUNNING → COLLECTING → SUCCEEDED
  branches (типизированы, durable, идемпотентны, optimistic-concurrency):
    RATE_LIMITED   — bounded switch/backoff, без второго writer
    AUTH_REQUIRED  — не ретраить бесконечно; owner-действие
    PAUSED         — по owner; resume продолжает тот же Run
    INTERRUPTED    — прерывание/crash; recovery → одна безопасная continuation
    FAILED         — bounded failure с классификацией
    CANCELLED      — по owner
    OWNER_REQUIRED — stop-условие (§17.5)
```

Каждый переход: `run_id`, from→to, `reason_code`, `evidence_time`, актор,
`idempotency_key`, `expected_version` (optimistic lock). Конкурирующая мутация с
устаревшей версией отклоняется `409 CONFLICT` без перезаписи.

**Автоматический старт (§«Automatic start flow»):**
1. Валидировать принятый Work Order, grant, baseline, доступность проекта.
2. Создать **идемпотентный** Run (`Idempotency-Key` → один Run).
3. Router-решение: role/model/profile (durable, reason-coded).
4. Захватить lease профиля и worktree (safe boundary).
5. Собрать bounded JobPackage из VP-4 (`context_engine`/`workorders`).
6. Запустить официальный CLI как Unix-идентичность профиля через Runner
   (argv/stdin, без shell-интерполяции).
7. Сохранить provider session ID и нормализованные события.
8. Обновить состояние Run и точное next action.
9. На пороге контекста/прерывании/rate-limit — checkpoint и verified
   HandoffPackage (VP-4).
10. Освобождать/захватывать lease только на документированной safe boundary.
11. Resume точной сессии при совместимости; иначе fresh-сессия с handoff-ack.
12. Продолжать до success/bounded failure/owner-required.

## Durable schemas (миграция `0005_agent_pipeline`)

Миграция `0005_agent_pipeline` создаёт ровно **16 durable-таблиц** (перечислены
ниже; `idempotency_keys` переиспользуется из VP-3, не пересоздаётся).
Минимально необходимое durable-состояние (append-only Audit — §30.2):

- `model_registry` — provider, model_id, alias, display, efforts, context/
  structured capabilities, availability, source (`official_structured|wrapper|
  observed|manual|unknown`), confidence, `discovered_at`.
- `discovery_snapshots` — снимок discover_capabilities per profile+time.
- `role_presets` / `effective_selections` — requested vs effective (role,
  model, profile), `reason_code`, `evidence_time`.
- `agent_profiles` — safe metadata (alias, provider, auth root **allowlist-ref**,
  Unix-идентичность-метка), **без** email/token/cookie/raw path.
- `profile_health` — executable/version, auth_status, permissions, last
  result/error (redacted), `observed_at`.
- `profile_availability` — состояние (`READY|LEASED|COOLDOWN|AUTH_REQUIRED|
  ERROR|DRAINING|DISABLED|RETIRED`), cooldown_until, drain, `updated_at`.
- `capacity_observations` — §11.6 (status/5h/7d/reset/source/observed_at/
  confidence); отсутствие стабильного источника → `UNKNOWN` (не фикция).
- `runs` — project_id, wo_id, vp, correlation_id, state, timestamps, версия.
- `run_role_steps` — role (planner/builder/reviewer), requested/effective
  model+profile, session_ref, статус, verdict (для reviewer).
- `run_events` — нормализованные события (type, occurred_at, payload,
  schema_version); переживают рестарт Core.
- `provider_sessions` — provider, session_id, profile, role, `started_at`;
  **без transcript/credentials**.
- `router_decisions` — inputs, chosen, `reason_code`, кандидаты, `decided_at`.
- `run_leases` — run/profile/worktree/role/expires_at/heartbeat (§13.4).
- `run_retries` — attempt, класс ошибки (§12.4), backoff, bounded счётчик.
- `run_pauses` / `interruptions` — тип, время, safe-continuation-ref.
- `handoff_links` — связь Run ↔ HandoffPackage (VP-4) / recovery.
- `idempotency_keys` — переиспользуем существующую таблицу VP-3.

**Никогда не хранить:** credentials, cookies, email, raw auth root, env dump,
полный provider payload, полный transcript. Оптимистичная блокировка — колонка
`version` на мутируемых сущностях (`runs`, `run_role_steps`, `profile_*`).

Миграция обязана пройти: пустая БД → head; копия живой `0004_work_orders` →
head (сохранение VP-0…VP-4); durability после рестарта; апгрейд живой БД —
только после verified Atlas-бэкапа.

## Role independence (независимость ролей)

- Default: `Codex Planner → Claude Builder → Codex Reviewer` (§17.1).
- Reviewer **не** Builder-сессия и **не** Builder-роль; read-only до отдельно
  авторизованного фикса; оценивает точный ReviewPackage и текущий diff.
- Ложный success Builder **не** становится PASS: verdict основан на evidence.
- Один fix-loop: после первого REVISE допускается одна bounded-итерация; второй
  проваленный review → `BLOCKED`/`OWNER_REQUIRED`.

## Routing priority (§17.3, без silent fallback)

1. owner override; 2. role compatibility; 3. profile READY; 4. safe affinity;
5. verified capacity; 6. cooldown/error; 7. least recently used;
8. deterministic tie-break. Persist: requested + effective + `reason_code` +
`evidence_time`. Никакого молчаливого переключения модели/профиля.

## Provider adapter contracts (проверено на установленных CLI)

Реализация — repository-native адаптеры за границей Runner
(`atlas_core/adapters/`), протокол §12 (`discover_capabilities`, `auth_status`,
`start`, `resume`, `stream`, `interrupt`, `collect_result`, `capacity`).
Контракты зафиксированы spike’ом (`var/artifacts/vp5/spike/compat_decision.json`).

**Codex (codex-cli 0.146.0):**
- non-interactive: `codex exec --json` (JSONL событий), `--output-schema <FILE>`
  (строгий verdict), `-o/--output-last-message <FILE>`, `-m/--model`,
  `-p/--profile`, `-s/--sandbox {read-only|workspace-write|danger-full-access}`,
  `-C/--cd`, `--add-dir`, `--skip-git-repo-check`, `--ephemeral`;
  `CODEX_HOME=<root>`; prompt через argv/stdin (`-`), без shell-интерполяции.
- resume: `codex exec resume <SESSION_ID>` (UUID/thread, `--last`).
- auth state: `codex login status` (текст в 0.146.0); login: `codex login
  --device-auth` (device-code) / `codex login` (browser).
- structured account/rateLimits: `codex app-server` (experimental, UDS
  `--listen unix://`, `generate-json-schema`) — **отдельный spike** до
  привязки; до него лимиты Codex через CLI = `UNKNOWN`.

**Claude (Claude Code 2.1.220):**
- non-interactive: `claude -p --output-format stream-json --verbose`
  (нормализованные события), `--include-partial-messages`, `--model`,
  `--effort`, `--add-dir`; `CLAUDE_CONFIG_DIR=<root>`; **не** `--bare` для
  Builder (нужны project `CLAUDE.md`/settings).
- resume: `claude -p --resume <SESSION_ID>`; фиксированный id: `--session-id
  <uuid>`; fresh-from-handoff при небезопасном resume: `--fork-session`.
- auth state: `claude auth status --json` (JSON по умолчанию) →
  нормализуем `loggedIn/authMethod/apiProvider/subscriptionType`; **отбрасываем
  PII** `email/orgId/orgName`. Plan-label = `subscriptionType` (verified).
- лимиты подписки: официальный statusLine-вход stdin
  (`rate_limits.five_hour.*`, `rate_limits.seven_day.*`) — только для
  поддерживаемых подписчиков **после** ответа; **отдельный gated statusline
  spike**; вне границы Runner — `UNKNOWN`, TUI не скрейпить.

**Общее:** не читать `auth.json`/session-JSONL как стабильный API; transcript —
только через публичный CLI-интерфейс; version фиксируется.

## Session rules (три РАЗЛИЧНЫЕ семантики)

- `NEW_SESSION` (required): fresh `codex exec` / `claude -p`.
- `EXACT_RESUME` / `RESUME_BY_ID` (required): продолжить **ту же** совместимую
  сессию по её ID (`codex exec resume <id>` / `claude -p --resume <id>`). Только
  в пределах того же профиля (сессия живёт в его `CODEX_HOME`/`CLAUDE_CONFIG_DIR`).
- `FRESH_WITH_HANDOFF` (required): **genuinely fresh** сессия БЕЗ прежней истории
  — контекст только из принятого HandoffPackage (VP-4) + ack. Никакого
  `--resume`/`--fork-session`. Единственный безопасный вариант при **смене
  профиля** (origin-сессия недоступна из чужого auth-root).
- `FORK_SESSION` / `FORK_NATIVE` (optional): новый session-id, **копирующий**
  историю оригинала (`claude --resume <id> --fork-session`). Несёт прежний
  provider-контекст → допустим ТОЛЬКО в пределах того же профиля и при явном
  намерении. **Не** путать с fresh-from-handoff.
- `COMPACT` — optional; `CLEAR_INTERACTIVE` — operator-only.

## Lease и one-writer (§13.4)

Lease связан с project, worktree, run, role, `expires_at`, heartbeat. После
потери heartbeat новый writer запрещён до Git/process reconciliation (не
автоугон). Переключение профиля по rate-limit освобождает/захватывает lease
только на safe boundary и **не** создаёт второго writer.

## Rate-limit / auth / error классификация (§12.4)

Коды: `AUTH_REQUIRED`, `AUTH_EXPIRED`, `RATE_LIMITED`, `CAPACITY_UNKNOWN`,
`NETWORK_ERROR`, `PROVIDER_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `PERMISSION_DENIED`,
`TOOL_FAILED`, `PROCESS_CRASHED`, `TIMEOUT`, `OUTPUT_INVALID`,
`WORKTREE_CONFLICT`, `POLICY_DENIED`, `USER_INTERRUPTED`, `UNKNOWN`. Каждая
ошибка: redacted evidence, retryable-флаг, next action.

## Bounded retry и stop (§17.5)

Ретраи ограничены (`run_retries`, счётчик + backoff). `AUTH_REQUIRED` не
ретраится бесконечно → owner-действие. Stop: outside grant, scope drift, dirty
conflict, suspected leak, второй проваленный fix, unresolved license, дважды
invalid output, нет профиля → `OWNER_REQUIRED`.

## Pause / resume / recovery (§31)

Pause — durable; resume продолжает **тот же** Run и совместимую сессию.
Interruption/crash → recovery journal Runner + Core reconciliation → **одна**
безопасная continuation (не два writer). Fresh-session handoff восстанавливает
точное состояние и next action без старого чата.

## API scope (`/api/v1`, §25.1)

- `GET /runs`, `POST /runs` (idempotent), `GET /runs/{id}`,
  `POST /runs/{id}/pause|resume|cancel`, `GET /runs/{id}/events`.
- `GET /profiles` (+ health/availability/capacity, redacted),
  `POST /profiles/{id}/onboarding` (метод official|attach|cookie-import; cookie
  import → `UNSUPPORTED`).
- `GET /models` (registry + source/availability/observed_at).
- `GET /api/v1/system/summary` (Pulse baseline, §ниже).
- Realtime: SSE `GET /api/v1/events?after=<event_id>`. Мутации принимают
  `Idempotency-Key`. Error: stable code, localized message, correlation ID,
  retryable. Ответы не рендерят raw provider payload/произвольный HTML.

## UI scope

**Runs console (минимально полный VP-5):** список + деталь; project/VP/WO/
correlation IDs; последовательность ролей; requested/effective модель+профиль;
router reason; timeline жизненного цикла; нормализованные live-события; текущий
writer/lease; checkpoint/handoff; rate-limit/auth/interruption; pause/resume/
cancel (с подтверждением, где нужно); verdict и findings Reviewer; точное next
action; bounded logs/artifacts; состояния loading/empty/offline/stale/conflict/
forbidden/error.

**Profiles MVP (входит в VP-5, §Profiles boundary ниже).**

**Pulse baseline (входит, §Pulse boundary ниже).**

**RU/EN switch + Ember refinement (§Language/Visual ниже).**

## Profiles MVP — граница VP-5

Навигация `Profiles`/`Профили` (не credential-vault «Accounts»). Поддержка 4
реальных профилей и будущих безопасных добавлений:
- группы провайдеров Codex/Claude; сводка по `READY/LEASED/COOLDOWN/
  AUTH_REQUIRED/ERROR`; карточки + табличный режим; поиск/фильтр по alias/
  provider/state/schedulability;
- **только alias** (без email); verified plan-label только если официальный
  адаптер вернул; 5h/weekly бары только с verified-значениями; reset time и
  observation timestamp; `UNKNOWN`/`STALE`/unsupported; текущий Run/role/lease;
  последняя успешная auth/health; cooldown/drain/disable; точное next action;
  details drawer.
- Onboarding: (1) official login (recommended); (2) attach allowlisted auth root
  без копирования; (3) cookie import — experimental, backend `UNSUPPORTED` до
  отдельного approved spike (без Sub2API-кода/формата, без браузер-extraction,
  без MFA/CAPTCHA-обхода, raw cookie не проходит через Core DB/Web/Audit/logs/
  artifacts). Реальный login не завершать без owner-подтверждения.

Полный операционный console «40 профилей», saved views, bulk-операции,
исторический анализ — **VP-8** (§41).

## Pulse baseline — граница VP-5

Правдивый `GET /api/v1/system/summary` + full-width responsive layout. Показывать
при безопасной доступности: CPU (логические ядра, load); память used/total; disk
used/total для Atlas-хранилища; OS name/version; kernel/arch; sanitized
machine/runtime identity; host uptime; state+uptime Core/Web/Runner; версия
Atlas; текущая DB-миграция; возраст последнего verified-бэкапа; active/queued/
paused Runs; счётчики writer/profile lease; last refresh.

**Не раскрывать:** public/private IP; raw hostname (если идентифицирует
владельца); Unix-имена кроме безопасных сервис-меток; auth-root paths; container
env; credentials; неподдерживаемые проценты. Layout: 1440→4 KPI-колонки,
1024/768→2, 390→1; без пустого пол-панели и горизонтального overflow; состояния
skeleton/offline/stale/partial/error. Исторические графики и полный VP-8 Pulse —
вне scope.

## Language switch и визуальная отделка

Сегментированный контрол `RU`/`EN` (текст обязателен, иконка опциональна),
скользящий индикатор, persistent-выбор, клавиатура+screen-reader, видимый focus,
корректный `lang`, mobile-размещение, RU по умолчанию, полный typed-catalog
паритет (missing key → CI fail). CodeVinci Ember (§28): тёмный дефолт, тёплый
off-white текст, Ember-orange для primary/active-nav, subtle warm radial glow,
semantic green/amber/red/blue **только** для правдивых состояний, статус не
только цветом. Motion 120–180 ms hover/focus/segment; skeleton только при
реальной загрузке; `prefers-reduced-motion`. Запрещено копирование
TonWave/Sub2API CSS/layout/strings/icons/assets, постоянный motion, particles,
heavy glass, AI-градиенты, fake progress, decorative counters. Изменение
затронутых поверхностей фиксируется в [`REUSE_REGISTER`](../REUSE_REGISTER.md).

## Стабильные ошибки

`RUN_CONFLICT` (409 optimistic), `RUN_NOT_FOUND`, `PROFILE_UNAVAILABLE`,
`NO_ELIGIBLE_PROFILE`, `MODEL_UNAVAILABLE`, `SILENT_FALLBACK_FORBIDDEN`,
`WORKTREE_CONFLICT`, `REVIEWER_NOT_INDEPENDENT`, `SECOND_FIX_BLOCKED`,
`AUTH_REQUIRED`, `RATE_LIMITED`, `HANDOFF_MISMATCH`, `COOKIE_UNSUPPORTED`,
`OWNER_REQUIRED`. Локализованное сообщение + correlation ID + retryable.

## Audit и redaction

Append-only Audit на каждый значимый переход/решение (§30.2). Redaction-сканер
на границе БД/логов/artifacts (§30). В события/Audit не попадают секреты, email,
cookie, raw path, полный payload, transcript.

## Приёмочная матрица (26)

1. Миграция из пустой БД и из `0004` без потерь.
2. Метаданные профиля durable, без credentials/email/raw path.
3. Auth-состояние адаптера нормализовано без чтения credential-файлов.
4. Реестр моделей: source, availability, observation time.
5. Requested/effective модель и профиль видимы.
6. Silent fallback невозможен.
7. Router-решения детерминированы и reason-coded.
8. Verified-лимиты: корректные окна/reset/provenance.
9. Отсутствующие/устаревшие лимиты → `UNKNOWN`/`STALE`, не фикция.
10. Создание Run идемпотентно.
11. Переходы Run валидны, durable, атомарны.
12. Конкурирующие конфликтные мутации отклонены.
13. Planner производит bounded executable-пакет.
14. Builder захватывает единственный writer-lease.
15. Reviewer независим и read-only.
16. Нормализованные события переживают рестарт Core.
17. Provider session ID сохранён без transcript/credentials.
18. Pause/resume продолжает верный Run.
19. Fresh-session handoff восстанавливает точное состояние.
20. Rate-limit-переключение ограничено и не создаёт второго writer.
21. Auth-провал не ретраится бесконечно.
22. Interruption/crash → одна безопасная continuation.
23. Один fix-loop; второй провал блокирует.
24. Profiles/Pulse/Runs/RU-EN — на максимально сильном реальном уровне.
25. Нет регрессий VP-1…VP-4; сервисы healthy/non-root.
26. Финальный секрет/privacy-скан чист; синтетический конвейер даёт реальный
    артефакт (реальный provider-E2E — под owner-гейтом).

### Результат приёмки

`scripts/run_vp5_acceptance.py` (или repo-consistent эквивалент). COMPLETE
только при 26/26 против реального стека. Детерминированные харнессы покрывают
CI-часть; критерий «реальный артефакт» остаётся честно pending до owner-
авторизации реальных вызовов Codex/Claude.

## Тесты

Python lint; полная Python-регрессия; contract/schema; migration empty→head и
`0004`→head; routing/lifecycle/idempotency/concurrency; one-writer/profile-
switch; normalized-event/recovery; adapter-compatibility; Web typecheck; RU/EN
parity; production Web build; responsive/keyboard/reduced-motion; секрет/privacy-
скан; `git diff --check`; полная приёмка VP-5.

## Формат evidence

`var/artifacts/vp5/`, bounded, redacted, SHA-256 manifest. Никаких credentials
или owner runtime-данных в Git.

## Out of scope (граница VP-5)

- Полный VP-8 web-console (все nav/screens, Inspector Drawer, Profiles 4→40,
  saved views, bulk, исторический анализ) — §41.
- VP-6 Review & Quality Firewall/Evidence Cache сверх одного fix-loop — §39.
- VP-7 Autonomy/GitHub/Time Machine — §40.
- billing, API resale, proxies, multi-tenant, user management.
- Реальный provider-E2E без owner-подтверждения; cookie-import adapter без
  отдельного approved security-spike; копирование Sub2API/TonWave.
