# DECISIONS — журнал решений

Русский — source of truth. `OWNER-APPROVED` имеет приоритет над Master Spec
(§1). Пункты «требуют подтверждения» не выполняются без явного grant владельца
в текущей сессии.

## OWNER-APPROVED — авторизация текущей сессии (Phase 0/A/B)

Владелец в текущей сессии подтвердил и авторизовал: логин 4 реальных профилей
(2 Codex + 2 Claude, разные аккаунты, per-profile идентичности и исполняемые
файлы); установку зависимостей; управление Atlas Unix-пользователями и
runtime-каталогами; создание/изменение Atlas Compose/systemd; запуск реальных
ограниченных probe; создание веток и коммитов; push feature-веток; создание PR;
исправление CI; merge принятых PR в `main`. Границы: без force push, без
destructive Git, без правки посторонних сервисов/Nginx/DNS/данных, без
исчерпания лимитов, без выбора LICENSE, без копирования исходников Sub2API.

### VP0-BOOTSTRAP (однократное исключение)

На момент старта: локальный `main` — unborn, удалённый
`CodeVinci8/codevinci-atlas` — пуст (`isEmpty:true`, без default branch).
Пустой репозиторий не имеет base ref для PR. Поэтому сделан **один** безопасный
bootstrap-коммит в `main` (только repo-owned non-secret исходники и
документация; секрет-скан ЧИСТО; `var/`, БД, сокеты, auth-root, `.env`
исключены `.gitignore`) и **однократно** запушен. Это checkpoint инфраструктуры,
а НЕ принятый релиз VP-0. Вся дальнейшая работа VP-0 идёт в feature-ветке и
принимается через PR/merge. Повторный bootstrap запрещён.

## OWNER-APPROVED (из Master Spec §49)

- Compose для Core/Web + нативный host Runner.
- Удобный официальный логин / использование existing auth roots.
- Cookie-путь — экспериментальное дополнение (off by default).
- STANDARD по умолчанию + настраиваемый merge PR.
- Повышенная автономия на уровне проекта.
- RU UI с переключением на EN.
- Стабильные документы RU/EN; коммиты и отчёты — RU.
- Приёмка: synthetic, затем File Atelier.
- Внешние проекты — через Reuse Register.
- Десять этапов VP-0…VP-9.

## Технические решения VP-0

- **VP0-D1.** VP-0 реализован на стандартной библиотеке Python (unittest,
  sqlite3, asyncio, socket) без uv/pnpm/Docker. *Почему:* uv/pnpm/pytest не
  установлены; доказательство должно реально запускаться. Тяжёлый стек — VP-1.
- **VP0-D2 (обновлено).** Единый runtime-layout — **`/var/lib/codevinci-atlas`**.
  Прежнее расхождение с repo-local `./var` **устранено**: путь один для
  profile-init, логина, диагностики, адаптеров, Runner и реальных probe.
  Изоляция тестов — только через временный `ATLAS_DATA_DIR`, а не второй layout.
- **VP0-D3 (обновлено, супеседит nobody-доказательство).** Изоляция профилей —
  через **отдельные Unix-идентичности** на профиль (`atlas-cx01/02`,
  `atlas-cl01/02`), root `0700` во владении своей идентичности. Runner дропает
  привилегии (`user=`/`group=`, prod — `CAP_SETUID/SETGID` у systemd-сервиса).
  Доказано: идентичность A не читает credentials B, сервисный `atlas` не читает
  ни один. `runuser -u nobody` против root-owned 0700 **признан недостаточным**
  и заменён на проверку реальной границы исполнения.
- **VP0-D4.** В среде VP-0 (root-only) Runner идёт с `allow_root=True` и сам
  выполняет дроп в идентичность профиля. Прод: systemd `User=atlas` +
  `AmbientCapabilities=CAP_SETUID CAP_SETGID` (или `systemd-run --uid`). Отказ
  от root по умолчанию реализован в коде.
- **VP0-D5.** Секрет-сканер реализован нативно (эквивалент gitleaks): рабочее
  дерево + история Git (честно: 0 коммитов ⇒ история не заявляется проверенной)
  + БД/логи/artifacts/конфиг. Узкий аллоуслист — только синтетические фикстуры
  `tests/` и определения в `redaction.py`/`secret_scan.py`. Настоящий gitleaks — VP-1.
- **VP0-D6 (обновлено).** Реальные подписочные probe (2 Codex + 2 Claude,
  реальный A→B) — за единственным owner-гейтом логина. Механизм доказан; крит.
  3–5 помечены **GATE_REAL**, а не PASS.
- **VP0-D7.** Честность приёмки: механический/fake-результат не засчитывается
  как финальный PASS. Введены статусы `PASS`, `PASS_MECHANISM`, `GATE_REAL`,
  `FAIL`. Runner-recovery усилен до продолжения-до-успеха.
- **VP0-D8 (per-profile executables).** Владелец установил CLI отдельно на
  каждый профиль (`<root>/.local/bin/{codex,claude}`). Профиль хранит
  `executable_path`; адаптеры используют именно его. Это усиливает изоляцию:
  у каждой идентичности свой бинарь и своё окружение (`env -i`).
- **VP0-D9 (VP-0 ЗАВЕРШЁН — 11/11).** Реальные A→B доказаны для Codex и Claude:
  A вычисляет `partial`, Atlas сохраняет и верифицирует checkpoint+Handoff
  против persisted-БД, B (другая идентичность/аккаунт, отдельная сессия)
  вычисляет `final=partial+addend` и эхом отдаёт `nonce`; арифметика независимо
  проверяема, сессии различны. Секрет-скан различает санкционированные
  auth-store (исключены) и durable-состояние Atlas (должно быть чисто); git
  history сканируется точными токен-паттернами; настроенная git-идентичность
  владельца не считается утечкой. Реальный лимит не исчерпывался.

## Технические решения VP-1

- **VP1-D1 (стек).** VP-1 вводит полноценный стек: FastAPI + SQLAlchemy 2.x +
  Alembic (SQLite WAL) для Core; Vite + React 19 + TS strict для Web; `uv` и
  `pnpm` с локами. Таблицы создаются ТОЛЬКО Alembic (в проде — entrypoint
  `alembic upgrade head`), не автосозданием. VP-0-модули (изоляция, leases,
  адаптеры, redaction) переиспользованы без изменения контрактов.
- **VP1-D2 (дроп привилегий: runuser неприменим от non-root).** Проверено на
  реальном пути: `runuser` от non-root возвращает «may not be used by non-root
  users»; Python `subprocess(user=)` от non-root без caps → PermissionError.
  Поэтому production Runner (systemd `User=atlas`) дропает привилегии в
  идентичность профиля через **Python `subprocess(user=/group=)` + systemd
  `AmbientCapabilities=CAP_SETUID CAP_SETGID`**, а НЕ через `runuser`. `runuser`
  остаётся только для dev/CI-инструментов, работающих от root. Health/UDS
  привилегий не требуют. Граница безопасности: capabilities ограничены
  `CapabilityBoundingSet`; профильные идентичности не входят в bridge-группу.
- **VP1-D3 (least-privilege bridge + стабильный runtime-каталог).** Core-контейнер
  (non-root) аутентифицируется к host Runner через UDS: сокет `0660` и токен
  `0640`, оба во владении группы **`atlas-bridge`**; Core добавлен в эту группу
  (`group_add`), профильные идентичности — нет (не читают runner-токен, не
  подключаются к сокету — доказано). Каталог `/run/codevinci-atlas` монтируется
  в Core read-only. `RuntimeDirectoryPreserve=yes` обязателен: иначе рестарт
  Runner пересоздаёт каталог с новым инодом и bind-mount в контейнере рвётся.
- **VP1-D4 (non-root контейнеры).** Core — образ с непривилегированным
  пользователем (UID/GID = host `atlas` через build-args, чтобы совпасть с
  владельцем смонтированных путей); Web — `nginxinc/nginx-unprivileged` (uid
  101, порт 8080). Web слушает только `127.0.0.1:3210`; Core не публикуется.
- **VP1-D5 (воспроизводимая Web-сборка).** pnpm 11 блокирует build-скрипты;
  esbuild (нужен Vite) разрешён неинтерактивно через `allowBuilds: {esbuild: true}`
  в `pnpm-workspace.yaml`. `pnpm install --frozen-lockfile` и `pnpm build` идут
  из чистого состояния без правки `node_modules`.

## VP-1 — ЗАВЕРШЁН (17/17), СМЁРЖЕН

Приёмка `scripts/run_vp1_acceptance.py`: 17/17 PASS против реально развёрнутого
стека (Compose Core/Web + systemd Runner). Evidence — `var/artifacts/vp1/`.
Смёржен в `main` через PR #2 (squash), merge-commit `22951b6`
`CodeVinci8/codevinci-atlas`.

## Технические решения VP-2

- **VP2-D1 (персистентность только через Alembic).** Таблицы VP-2 —
  `projects/git_baselines/worktrees/worktree_leases` — добавлены миграцией
  `0002_project_workspace` и ORM-моделями (SQLAlchemy 2.x). VP-0 `Store`
  (runs/leases/…) остаётся тестовым; прод-БД `atlas.db` — только Alembic (VP1-D1).
- **VP2-D2 (allowlist + канонические пути).** Все пути проектов/worktree/intake
  проходят `WorkspaceGuard`: `realpath` (резолв symlink) + вхождение в явные
  корни `<data_dir>/{workspaces,intake,worktrees}`. Traversal, абсолютные пути,
  Windows-разделители и symlink-escape отклоняются. Содержимое репо/архива —
  данные, оно не расширяет allowlist (§30.2).
- **VP2-D3 (read-only baseline, non-destructive).** Baseline собирается только
  чтением (`git status/rev-parse/remote/ls-files`), `GIT_TERMINAL_PROMPT=0`,
  `GIT_OPTIONAL_LOCKS=0`. Remotes санируются (userinfo вырезается, redact) —
  credential-bearing URL не хранятся. Никаких `reset --hard`/`clean`/тихого
  stash/checkout; dirty сохраняется байт-в-байт (доказано приёмкой #4/#7).
- **VP2-D4 (worktree add — не изменяет оригинал).** `git worktree add -b` создаёт
  новую ветку и связанный каталог, не трогая рабочее дерево/dirty оригинала.
  Ветка обязана быть `atlas/vp-<n>-<slug>`; путь — канонический в allowlist без
  перезаписи. Core-контейнеру добавлен `git`; синтетические репо во владении
  `atlas` (без dubious-ownership).
- **VP2-D5 (один writer на worktree).** `worktree_leases` с UNIQUE(worktree,
  released_at='') — атомарный acquire; второй writer → `WORKTREE_CONFLICT`.
  Reconcile освобождает осиротевшую аренду ТОЛЬКО после проверки живости
  процесса и чистоты Git (автоугон запрещён; та же семантика, что VP-0 leases).
- **VP2-D6 (враждебный intake архивов).** Ручная поэлементная распаковка tar/zip
  внутри intake-корня: блокируются абсолютные/`..`/Windows-пути, symlink/
  hardlink, device/спец-файлы, превышение числа/размера, дубликаты. Ни один файл
  вне intake (доказано #11). Intake read-only (0444/0555).
- **VP2-D7 (Ember Web без копирования).** Project Workspace UI — собственный
  Ember developer-cockpit; TonWave/Sub2API использованы как визуальные референсы
  (иерархия/плотность), без копирования кода/стилей/строк/лейаутов — см.
  `docs/REUSE_REGISTER.md`.

## VP-2 — ЗАВЕРШЁН (20/20), СМЁРЖЕН

Приёмка `scripts/run_vp2_acceptance.py`: 20/20 PASS против реально развёрнутого
стека (Compose Core/Web + systemd Runner) и синтетических git-фикстур. Evidence
— `var/artifacts/vp2/`. Смёржен в `main` через PR #3 (squash), merge-commit
`a14472a` `CodeVinci8/codevinci-atlas`. Живая БД — на миграции
`0002_project_workspace`.

## Технические решения VP-3

- **VP3-D1 (durable-модель только через Alembic).** Таблицы VP-3 —
  `product_intakes`, `briefs`, `map_versions`, `map_nodes`, `map_edges`,
  `decisions`, `decision_events`, `parking_items`, `approvals`,
  `vp_activations`, `idempotency_keys` — добавлены миграцией
  `0003_product_map` и ORM-моделями (SQLAlchemy 2.x). Прод-путь — только
  Alembic (VP1-D1). Апгрейд доказан из пустой БД (`0001→0002→0003`) и из копии
  живой `0002` без потери данных VP-2 (приёмка #21).
- **VP3-D2 (immutable-версии + content-hash).** Brief/Map — immutable-версии с
  монотонным `version`, `parent_id` и `content_hash` = `sha256:` над
  canonical-JSON (sorted keys). Правка = новая версия; approved-версия не
  мутируется. Diff — детерминированный field/node/edge-level.
- **VP3-D3 (truth-status и evidence).** `VERIFIED` требует resolvable evidence
  (единственный тип VP-3 — VP-2 git baseline: `git_baseline:<id>`/`:latest`) с
  совпадением `content_hash`; forged/stale/mismatch → `EVIDENCE_INVALID`.
  Approval **не** превращает гипотезу в VERIFIED. `OWNER_PROVIDED`/`INFERRED`/
  `HYPOTHESIS`/`STALE`/`UNKNOWN` видимо различимы в API/экспорте/UI. Полная
  система Evidence — VP-6.
- **VP3-D4 (Approval-record — источник истины об утверждении).** Утверждённая
  версия определяется неизменяемой записью `approvals`, а не изменчивым
  статусом Brief: создание нового черновика поверх принятой версии **не**
  «разутверждает» её (history не удаляется). Approval связывает Brief+hash,
  Map version, envelope-hash, decisions-hash, actor, timestamp.
- **VP3-D5 (concurrency + идемпотентность).** Оптимистичная блокировка через
  `expected_version` (расхождение → `VERSION_CONFLICT`); `Idempotency-Key` →
  повтор не создаёт дублей. Один активный VP — durable-инвариант
  `UNIQUE(project_id, active_slot)`; вторая (в т.ч. конкурентная) активация →
  `ACTIVE_VP_CONFLICT` (доказано bounded-concurrency, #15). Стабильные коды:
  `VERSION_CONFLICT/EVIDENCE_INVALID/DECISION_UNRESOLVED/ENVELOPE_INVALID/`
  `MAP_INVALID/PROJECT_NOT_AVAILABLE/ACTIVE_VP_CONFLICT`.
- **VP3-D6 (данные — не команды; без внешних вызовов).** Intake/ссылки/факты —
  данные (§30.2): текст redacted+bounded, ссылки хранятся как санированные
  метаданные, VP-3 **не** ходит по внешним URL и **не** делает model/provider-
  вызовов. Секрет во вводе отклоняется/редактируется — в БД не попадает.
- **VP3-D7 (Portfolio — правдивая проекция).** Portfolio Map не выдумывает
  прогресс/капасити/последний запуск; отсутствующее — `UNKNOWN`. Экспорт
  MD/JSON детерминирован для одной принятой версии (кроме `_generated`), без
  секретов/env-дампов/raw auth-путей/небезопасного HTML.
- **VP3-D8 (Web: тёмный default сохранён).** Без явного сохранённого выбора
  ставится `data-theme="dark"` — светлое OS-предпочтение не заменяет
  утверждённую тёмную тему Atlas. Полная тема/настройки — VP-8. UI —
  собственный Ember (Portfolio/Map/Brief/решения/parking/diff/экспорт), без
  копирования TonWave/Sub2API.

## VP-3 — ЗАВЕРШЁН (26/26), СМЁРЖЕН

Приёмка `scripts/run_vp3_acceptance.py`: 26/26 PASS против реально
развёрнутого стека (Compose Core/Web + systemd Runner) и синтетических
фикстур. Evidence — `var/artifacts/vp3/`. Живая БД мигрирована на
`0003_product_map`; backup снят до миграции. Смёржен в `main` через PR #4
(squash), merge-commit `07ed6f4` `CodeVinci8/codevinci-atlas`.

## VP-4 — Work Orders & Context (решения)

- **VP4-D1 (VP Spec — детерминированный вывод).** VP Spec выводится без вызовов
  модели из ОДНОГО точного принятого Brief/Map/approval
  (`workorders.build_vp_spec_content`), версионный, с `content_hash`
  (canonical-JSON, sorted keys). Work Order связывает точные хеши Spec/Brief/Map
  и baseline (§16.1); правка — новая версия, approved не мутируется.
- **VP4-D2 (Work Order lifecycle — атомарность).** Состояния и `VALID_TRANSITIONS`
  фиксированы; валидный переход персистится, невалидный отклоняется **атомарно**
  (`INVALID_TRANSITION`, без частичной мутации); история переходов append-only
  (`work_order_events`).
- **VP4-D3 (concurrency + идемпотентность).** Оптимистичная блокировка через
  версию (расхождение → `VERSION_CONFLICT`, без перезаписи); `Idempotency-Key` →
  повтор не создаёт дублей. Стабильные коды:
  `VERSION_CONFLICT/INVALID_TRANSITION/WRITER_CONFLICT/OWNER_REQUIRED/`
  `PROJECT_NOT_AVAILABLE/SCOPE_DRIFT/SOURCE_STALE/HANDOFF_STALE/`
  `HASH_MISMATCH/CAPABILITY_DENIED/CRITERIA_LOST`.
- **VP4-D4 (один writer — durable-аренда).** На worktree ровно одна аренда
  (`UNIQUE(worktree, released_at IS NULL)`); **автоугона нет** (нужен reconcile).
  Lease держится только в `active/checkpointed/handoff_ready`; освобождается на
  терминале/блокировке и на границе ротации. Вторая параллельная запись →
  `WRITER_CONFLICT`.
- **VP4-D5 (оптимизатор — контролируемые решения).** Выходы —
  `READY/MERGE_TASKS/SPLIT_AT_CHECKPOINT/SWITCH_PROFILE/OWNER_REQUIRED`. Merge —
  только совместимые и с **сохранением каждого критерия** (criterion
  conservation, иначе `CRITERIA_LOST`); split — только на durable checkpoint с
  полным отображением критериев на детей. Оптимизатор **не меняет** scope и
  acceptance criteria и **не** делает реальной маршрутизации ролей (это VP-5).
- **VP4-D6 (bounded JobPackage; данные — не команды).** JobPackage
  детерминирован, immutable, с provenance; **без** repo/полного чата/логов/
  credentials/env; capacity честно `UNKNOWN`; capabilities — только из allowlist.
  Контекст **не расширяет** права/авторизацию (§30.2): строка внутри пакета не
  даёт shell/network/Git/provider/write.
- **VP4-D7 (Context Governor + ротация).** Пороги/триггеры детерминированы, без
  выдуманной ёмкости (`UNKNOWN`); checkpoint durable и hash-verifiable, переживает
  рестарт Core; ротация по безопасной последовательности сохраняет одного writer
  (lease освобождается на границе), продолжение восстанавливает работу.
- **VP4-D8 (Handoff + свежая реконструкция).** HandoffPackage содержит все
  обязательные поля, детерминированный hash, без запрещённого; отклоняет
  tamper/stale/wrong-project/wrong-version/wrong-HEAD/over-capability. **Свежий
  изолированный потребитель** (`scripts/vp4_fresh_consumer.py`, без atlas_core,
  БД, credentials, полного repo и старого чата) восстанавливает состояние и
  точное следующее действие из handoff-only и валиден по
  `contracts/schemas/run-result.json`. Compact-fallback — локальный
  детерминированный harness: сохраняет инварианты или fail-closed
  `OWNER_REQUIRED`. **Реальных provider-вызовов нет.**
- **VP4-D9 (упаковка образа + валидатор схем).** Core-образ содержит и исполняет
  изолированный consumer и контракт `run-result.json`
  (`infra/docker/core.Dockerfile` копирует `scripts/vp4_fresh_consumer.py` и
  `contracts/`); регрессия — `scripts/check_core_image.sh` и
  `tests/test_vp4_packaging.py` (CI-job `core-image`). Наш валидатор схем —
  **документированное подмножество** JSON Schema (`type/enum/pattern/min/max/`
  `required/properties/additionalProperties(bool)/items`), не полный draft
  2020-12; схемы VP-4 держатся внутри этого подмножества.
- **VP4-D10 (Web: Work Orders console).** Собственная консоль Work Orders
  (VP Spec, Work Orders + переходы, решения оптимизатора, checkpoint/handoff,
  реконструкция), тёмная тема по умолчанию сохранена, RU/EN-паритет, a11y,
  responsive. Полная оркестрация/run-стрим — VP-5/VP-8, в VP-4 UI не тянется.

## VP-4 — ЗАВЕРШЁН (26/26), СМЁРЖЕН

Приёмка `scripts/run_vp4_acceptance.py`: **26/26 PASS** против реально
развёрнутого стека (Compose Core/Web + systemd Runner) и синтетических фикстур
(удаляются по точным ID; append-only Audit сохраняется). Evidence с SHA-256 —
`var/artifacts/vp4/`. Живая БД мигрирована на `0004_work_orders`; backup снят до
миграции. Reconstruction исполняется внутри Core-образа. Смёржен в `main` через
**PR #6** (squash), CI зелёный на точном head-SHA `280ee35`, merge-commit
`7a3f82d` `CodeVinci8/codevinci-atlas`.

## VP-5 — Agent Pipeline (ЗАВЕРШЁН, 26/26 + реальный E2E, СМЁРЖЕН: PR #9, squash `afefa61`, CI head `86c504e`; живая БД `0005_agent_pipeline`)

- **VP5-D1 (session-семантики).** Три РАЗДЕЛЬНЫЕ семантики (§12.3), не смешиваем:
  `EXACT_RESUME` (`--resume <id>`, тот же профиль), `FORK_SESSION`
  (`--resume <id> --fork-session` — копирует историю оригинала, тот же профиль,
  явное намерение) и `FRESH_WITH_HANDOFF` (genuinely fresh, без `--resume`,
  контекст только из HandoffPackage — единственный безопасный вариант при смене
  профиля). Сверено с claude 2.1.220 `--help`.
- **VP5-D2 (router).** Приоритет §17.3 детерминирован, reason-coded; запрошенный
  недоступный профиль/модель → пустой effective + `*_UNAVAILABLE` (уход в
  OWNER_REQUIRED), **никакой** молчаливой замены.
- **VP5-D3 (один writer).** `run_leases` с `UNIQUE(profile_id, released_at='')`
  → не более одной активной аренды на профиль; worktree-writer — прежний
  `worktree_leases`. Смена профиля: release-before-acquire, второго writer нет.
  Доказано конкурентными негативными тестами (не только схемным ограничением).
- **VP5-D4 (durable-состояние).** Миграция `0005_agent_pipeline` = 16 таблиц;
  секреты/email/cookie/raw path/transcript/полный payload не хранятся. Auth
  нормализуется без чтения credential-файлов; PII (email/orgId/orgName)
  отбрасывается. Ёмкость: verified-only, иначе `UNKNOWN`/`STALE`.
- **VP5-D5 (Pulse system-summary).** Sanitized: без IP/hostname/nodename/Unix-имён/
  auth-путей/env; machine_id — необратимый хеш; недоступное → partial `None`,
  не фикция. Web=`UNKNOWN` (Core не наблюдает Web напрямую).
- **VP5-D6 (граница scope).** Cookie-import → `UNSUPPORTED` (до отдельного
  security-spike). Полный операционный console (40 профилей, saved views, bulk,
  история) — VP-8. Реальный provider-E2E — под owner-гейтом.

Приёмка `scripts/run_vp5_acceptance.py`: **26/26** (детерминированно, реальная
миграция `0005` + реальные сервисы + ASGI TestClient + fake-адаптеры §32.2).
Полная Python-регрессия 247 OK. Evidence + SHA-256 — `var/artifacts/vp5/`.

- **VP5-D7 (реальный provider-E2E — ВЫПОЛНЕН).** `scripts/run_vp5_real_e2e.py`:
  реальный Codex Planner (`codex-plus-01`) → Claude Builder (`claude-pro-01`) →
  независимый Codex Reviewer (`codex-plus-02`) через нативные адаптеры Atlas
  (runuser + изолированный env под идентичностью профиля) на СИНТЕТИЧЕСКОМ
  git-репо. **3/6** подписочных вызовов. Реальный артефакт `calc.py`
  (`def add(a,b): return a+b`, sha256 в evidence) произведён реальным Claude,
  отревьюен реальным независимым Codex → **PASS**. Один writer
  (`max_concurrent_writers=1`), Reviewer другой профиль+сессия, `fix_loops=0`,
  без transcript/credentials (secret-scan чист). Evidence —
  `var/artifacts/vp5/real_e2e/`. Наблюдение: `claude auth status --json` может
  сообщать `loggedIn=true` при истёкшем OAuth-токене (первый прогон дал
  транзиентный `401 OAuth expired`, корректно классифицирован как AUTH_EXPIRED,
  повторный прогон прошёл). Реальный и детерминированный уровни — раздельные
  evidence.

## VP-6 — Review & Quality (решения)

- **VP6-D0 (owner-авторизация текущей сессии).** Владелец в текущей сессии
  авторизовал ограниченную последовательность закрытия VP-6: локальную
  реализацию, тесты, браузерную верификацию, ограниченные обязательные
  provider-вызовы (≤4), push feature-ветки, русский PR, починку CI, squash-merge,
  обновление приватного loopback-стека с verified backup/миграцией и пост-merge
  синхронизацию docs. **Не** входит: выбор LICENSE/NOTICE, cookies, мутация
  login, публичный deploy, домен/TLS, force push, destructive cleanup, удаление
  owner-данных, реализация VP-7/8/9, посторонние изменения VPS/репозиториев.
- **VP6-D1 (браузер: Playwright chromium из официального источника).** На хосте
  (Ubuntu, suite `resolute`) браузер отсутствовал: ни `google-chrome*`, ни
  `chromium*` на PATH; `apt-cache policy chromium` пуст (в Ubuntu `chromium` —
  транзитный snap, ненадёжный headless на сервере). Единственные chrome-бинарники
  в системе — внутри образов чужих контейнеров (`/var/lib/docker/.../ms-playwright`)
  и **не** трогаются. Репозиторий — pnpm/Vite/React/Node, поэтому сильнейший
  repo-совместимый harness — **Playwright** с его официальной загрузкой chromium
  (`npx playwright install chromium`, официальный источник Playwright, **не**
  curl-pipe). Установлен `chromium-1234` (Chromium **151.0.7922.34**),
  подтверждён реальный headless-запуск против `127.0.0.1:3210`. Диск после
  установки — 85% (warning-полоса, не critical).
- **VP6-D2 (root-cause пустых Profiles).** `/api/v1/profiles` пуст, потому что
  `scripts/profile-init.py` писал только non-secret **файловый** реестр
  (`ProfileRegistry` JSON, 4 профиля присутствуют), а API читает durable-таблицу
  `agent_profiles` (жив.БД: 0 строк). `ProfileService.upsert_profile` (путь в БД)
  вызывался **только** `run_vp5_acceptance.py`. Нет шага, синхронизирующего
  реестр → БД при setup/startup/deploy. Исправление — идемпотентная
  reconciliation (`atlas_core/profile_reconcile.py` + CLI `atlas profiles
  reconcile`), читающая allowlisted реестр и upsert-ящая safe-метаданные; **не**
  стартует provider-сессии, **не** читает credential-файлы, **не** сканирует
  произвольные Unix-home. Capacity остаётся `UNKNOWN`/`STALE` до verified
  observation; остаток не выводится из факта успешной auth.
- **VP6-D3 (bounded Ember/UX correction — не VP-8).** По прямому указанию
  владельца до приёмки VP-6 выполняется ограниченная коррекция долга UI: Pulse-
  иерархия (above-fold state/VP/Run/handoff/blocker/next; диагностика
  раскрываемая), подпись load average `Нагрузка за 1 / 5 / 15 мин` (не «CPU %»),
  memory/disk бары с текстовыми порогами, перенос backend `Web: UNKNOWN` в
  диагностику при правдивом `Интерфейс открыт`, locale-aware время через
  `Intl.DateTimeFormat` с сохранением UTC в `<time datetime>`, human-labels и
  фильтры Audit, компактные empty states, сдержанный Ember-refinement (glow
  только вокруг активного, `prefers-reduced-motion`). Полный операционный console
  (4→40 профилей, saved views, bulk) остаётся **VP-8**.
- **VP6-D4 (ReviewPackage — SHA-bound, инвалидация фактом).** ReviewPackage
  immutable, `content_hash` = sha256 над canonical-JSON. Валидность проверяется
  **сверкой с фактом** (Git/FS/DB), а не доверием отчёту: протухший base/head SHA,
  изменённый артефакт (хеш), отсутствующее/неразрешимое evidence или несовпадающий
  Work Order → `INVALID_EVIDENCE`. Cached PASS с протухшего head не используется.
- **VP6-D5 (Impact engine — точные классы, без слепой полной регрессии).**
  Классы `DOC_ONLY/LOCAL/INTEGRATION/SHARED/HIGH_RISK` детерминированы по diff;
  micro-fix не запускает полную регрессию без видимого risk-повода. Финальная
  полная Python-регрессия оправдана HIGH_RISK-диффом (миграция `0006` + policy).
- **VP6-D6 (Evidence Cache — точный ключ).** Ключ = SHA + команда/версия +
  input-хеши + окружение + scope; reuse только при точном совпадении всех
  компонентов с видимой причиной; любое изменение инвалидирует запись.
- **VP6-D7 (waiver — non-waivable).** Waiver требует reason/finding/scope/actor/
  expiry/review-condition/Audit; **не** может обойти secrets/credential exposure,
  unauthorized external actions, one-writer violation, stale evidence.
- **VP6-D8 (LICENSE остаётся видимым owner-решением).** Firewall-gate
  `license_dependency` эмитит видимый owner/info finding об отсутствии Atlas
  LICENSE. LICENSE **не** добавляется (VP-6 не выбирает лицензию).

## VP-6 — ЗАВЕРШЁН (26/26 + реальная Chrome), СМЁРЖЕН: PR #11, squash `63cdc35`, CI head `f6c3d0e`; живая БД `0006_review_quality`

Приёмка `scripts/run_vp6_acceptance.py`: **26/26** (детерминированно, изолированная
мигрированная БД + посеянные дефекты в синтетических репо; живая БД не затронута).
Полная Python-регрессия **268 OK** (impact HIGH_RISK: миграция 0006 + SHARED
orm.py оправдывают полную регрессию). Web tsc/build/i18n (569 ключей) green.
Реальная **Chrome-верификация** (Playwright + Chromium **151.0.7922.34**): 43
скриншота 1440/1024/768/390 × RU/EN + reduced-motion + Quality detail +
критическое хранилище; язык RU/EN persist, focus-outline, нет горизонтального
overflow, тач-таргеты ≥44px, **0 PII** в DOM/сети, данные переживают refresh.
Evidence (gitignored, §9) — `var/artifacts/vp6/` + SHA-256 `MANIFEST_sha256.json`.

Приватный loopback-стек обновлён: verified backup снят до миграции
(`atlas-backup-*.tar.gz`, integrity+secret-scan OK); `docker compose build/up`;
Core-entrypoint `alembic upgrade head` → **`0006_review_quality`**; данные
сохранены (`audit_events` 656→663, потерь нет); Core non-root (UID 995), db во
владении `atlas`; Core/Web/Runner **READY**. Reconcile на старте Core: **4
профиля** (codex-plus-01/02, claude-pro-01/02) видны в живой UI на
`http://127.0.0.1:3210` (live Chrome smoke — 8 скриншотов, 0 PII). Реальный
provider Quality-E2E — отдельно под owner-гейтом (здесь не выполнялся; реальное и
детерминированное evidence раздельны). LICENSE **не** добавлена (видимое
owner-решение, firewall-gate эмитит info-finding); cookie-import — `UNSUPPORTED`.

## Требуют отдельного подтверждения владельца

- Выбор LICENSE (кандидаты MIT/Apache-2.0; выбирается после reuse-аудита, §20.4).
- Старт VP-7 (Autonomy, GitHub & Time Machine, §40) — отдельное решение владельца.
- Official login профилей (Codex CLI 0.146.0 уже установлен владельцем).
- File Atelier increment, публичный release/tag, домен/TLS, активация cookie-импорта.

(VP-5 push/PR #9/squash-merge `afefa61` и реальный provider-E2E — **выполнены**
по one-time owner-авторизации; см. VP5-D7.)
