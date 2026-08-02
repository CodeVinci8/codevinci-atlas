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

## VP-1 — ЗАВЕРШЁН (17/17)

Приёмка `scripts/run_vp1_acceptance.py`: 17/17 PASS против реально развёрнутого
стека (Compose Core/Web + systemd Runner). Evidence — `var/artifacts/vp1/`.

## Требуют отдельного подтверждения владельца

- Создание/использование GitHub-репозитория за пределами read-only (репозиторий
  `CodeVinci8/codevinci-atlas` уже существует и пуст — первый push это гейт).
- Выбор LICENSE (кандидаты MIT/Apache-2.0; выбирается после reuse-аудита, §20.4).
- Первый commit/push/merge.
- Official login профилей (Codex CLI 0.146.0 уже установлен владельцем).
- File Atelier increment, публичный release/tag, домен/TLS, активация cookie-импорта.
