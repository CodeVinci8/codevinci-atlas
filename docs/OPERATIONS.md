# OPERATIONS — эксплуатация (VP-1)

🇬🇧 English: [`en/OPERATIONS.md`](en/OPERATIONS.md).

## Сервисы

- **Runner** — host systemd: `codevinci-atlas-runner.service` (`User=atlas`,
  non-root). Управление: `systemctl {status,restart,stop} codevinci-atlas-runner`.
  Сокет/токен: `/run/codevinci-atlas/{runner.sock,runner.token}` (группа
  `atlas-bridge`, `RuntimeDirectoryPreserve=yes`).
- **Core/Web** — Docker Compose: `docker compose {up -d,ps,logs,restart,down}`.
  Core non-root (uid = host `atlas`), Web `nginx-unprivileged` (uid 101), только
  `127.0.0.1:3210`. Обычный boot Core **не** мигрирует живую БД: entrypoint лишь
  проверяет совместимость схемы (`atlas_core.schema_check`) и fail-closed при
  несовпадении. Живая миграция — отдельная одноразовая команда (см.
  «Миграции: deploy-safety модель (VP-7)»).
- **Health:** `curl -s http://127.0.0.1:3210/api/v1/health` — правдивые
  `READY/DEGRADED/OFFLINE`. Runner offline → overall `DEGRADED`, runner
  `OFFLINE` (виден и в API, и в Web).
- **Диагностика/backup:** `uv run atlas doctor|status|backup`.
- **Рестарт-безопасность:** Core/Web и Runner перезапускаются независимо; после
  рестарта Runner контейнер Core снова видит сокет (стабильный инод благодаря
  `RuntimeDirectoryPreserve=yes`).

## Runtime и идентичности

- Единый layout — `/var/lib/codevinci-atlas`.
- Создать идентичности, bridge-группу и каталоги:
  `sudo bash scripts/atlas-runtime-setup.sh` (сервисный `atlas`, `atlas-bridge`,
  per-profile `atlas-cx01/02`, `atlas-cl01/02`).
- Root профиля `0700` во владении своей идентичности; Runner дропает привилегии
  в идентичность профиля через `subprocess(user=/group=)` + `CAP_SETUID/SETGID`
  (runuser от non-root неприменим — см. DECISIONS VP1-D2).

## Профили

- Создать изолированные root: `sudo PYTHONPATH=apps/core python3 scripts/profile-init.py`.
- Логин (владелец): `scripts/login-gate.sh` — по одному профилю, разные аккаунты.
- Состояния профиля (§11.3):
  `UNCONFIGURED → AUTH_REQUIRED → READY → LEASED → READY`, ветви
  `COOLDOWN`/`ERROR`/`DRAINING`/`DISABLED`/`RETIRED`.
- Health без идентичностей: `scripts/atlas-doctor`.

## Один writer и восстановление

- На worktree — одна аренда (`atlas_core.leases`). Второй acquire →
  `WORKTREE_CONFLICT`.
- После потери heartbeat новый writer запрещён до `reconcile()` (проверка
  живости процесса-писателя и чистоты Git). Автоугон запрещён.
- Рестарт Core: активные runs → `INTERRUPTED`, продолжение из checkpoint
  (`Core.recover_after_core_restart`).
- Обрыв Runner (жёсткий краш): незавершённые job журнала → `INTERRUPTED` при
  старте; reconciliation после гибели писателя; **продолжение до одного успеха**
  без второго writer (`atlas_runner.recovery_demo.prove_recovery_to_success`).

## Смена профиля при лимите (§5.3)

`error → classify → checkpoint → release lease → compatible profile →
fresh session + handoff → continue`. Аренда A освобождается **до** получения
аренды B — второго writer не возникает. Настоящий лимит не провоцируется.

## Work Orders & Context (VP-4)

- Живая миграция `0003_product_map → 0004_work_orders` выполняется одноразовой
  guarded-командой (см. «Миграции: deploy-safety модель (VP-7)»); порядок
  обновления — **backup → migrate → health → switch**. Данные VP-0…VP-3 сохраняются.
- **Реконструкция исполняется внутри Core-образа** изолированным процессом
  `scripts/vp4_fresh_consumer.py` по контракту `contracts/schemas/run-result.json`.
  Образ обязан их содержать — `infra/docker/core.Dockerfile` копирует
  `scripts/vp4_fresh_consumer.py` и `contracts/`. Регрессия упаковки:
  `bash scripts/check_core_image.sh <image>` (CI-job `core-image`); после
  пересборки образа проверить, что consumer запускается внутри контейнера.
- Ротация контекста и смена профиля сохраняют **одного writer**: lease
  освобождается на границе (см. выше). Автоугона нет — нужен reconcile.
- Приёмка: `python3 scripts/run_vp4_acceptance.py` (26/26, стек поднят).

## Миграции: deploy-safety модель (VP-7)

Обычный запуск/перезапуск Core **никогда** не меняет живую схему сам. Так
исключён неявный upgrade живой bind-mount БД при каждой перезагрузке контейнера
и молчаливый старт на несовместимой схеме.

- **Обычный boot:** entrypoint зовёт `python -m atlas_core.schema_check` — read-only
  сравнение ревизии БД с head. Совпадает → старт; не совпадает → fail-closed с
  явной ошибкой (Core не поднимается на старой/несовместимой схеме).
- **Живая миграция — одноразовая авторизованная команда** только после
  проверенного backup (порядок §8: **backup → migrate → health → switch**):

  ```bash
  # 0006 → 0007: миграция выполняется в разовом контейнере; разрешение на живую
  # миграцию (ATLAS_ALLOW_LIVE_MIGRATION) поднимается ТОЛЬКО этой командой.
  docker compose run --rm -e ATLAS_RUN_MIGRATIONS=1 core true
  # затем обычный старт — уже без каких-либо migration-override:
  docker compose up -d core
  ```

- **Compose не хранит** `ATLAS_ALLOW_LIVE_MIGRATION` постоянно: иначе каждая
  перезагрузка была бы неявно авторизованной живой миграцией и обесценила бы
  backup-first guard. Разрешение живёт только внутри одноразовой команды выше.
- **Guard границы alembic** (`migrations/env.py` → `assert_live_migration_allowed`)
  fail-closed блокирует любой `alembic upgrade` против живого data_dir/DB без
  `ATLAS_ALLOW_LIVE_MIGRATION=1`; изолированные/CI/dev-цели (temp `ATLAS_DATA_DIR`)
  — свободно.

## Резервное копирование (`atlas backup`)

```bash
uv run atlas backup --json --out /var/lib/codevinci-atlas/backups
```

- Онлайн-снимок SQLite (`Connection.backup`, не копирование файла).
- Манифест artifacts с SHA-256 каждого файла + `PRAGMA integrity_check`.
- Архив `atlas-backup-<ts>.tar.gz` с `atlas.db`, `manifest.json`, безопасным
  `config.yaml` (только если в нём нет секретов) и content-addressed artifacts.
- Backup **исключает** profile auth-root, runner-токены, логи; архив
  сканируется на secret-markers (`secret_scan_clean`).
- Политика хранения (§23.3): 7 daily + 4 weekly.

## Восстановление (ручная процедура, проверяемо dry-run)

Отдельной команды `atlas restore` нет — процедура ручная и проверяемая:

```bash
# 1) Проверить хеш архива против манифеста из backup
sha256sum atlas-backup-<ts>.tar.gz

# 2) DRY-RUN: распаковать во временный каталог и проверить целостность БД
tmp=$(mktemp -d); tar -xzf atlas-backup-<ts>.tar.gz -C "$tmp"
sqlite3 "$tmp/atlas.db" 'PRAGMA integrity_check;'   # ожидается: ok
cat "$tmp/manifest.json"                            # хеши db/artifacts

# 3) Применение (owner-действие): остановить Core, заменить БД, мигрировать
docker compose stop core
install -o atlas -g atlas -m 0640 "$tmp/atlas.db" /var/lib/codevinci-atlas/atlas.db
docker compose run --rm -e ATLAS_RUN_MIGRATIONS=1 core true   # одноразовая guarded-миграция
docker compose up -d core     # обычный boot: schema_check, без неявной миграции
curl -s http://127.0.0.1:3210/api/v1/health
```

Порядок обновления стека (§зеркалит backup): **backup → migrate → health → switch**.

## Доступ: localhost и SSH-туннель (§7.4)

Web слушает только `127.0.0.1:3210` — публичного порта нет. С локальной машины:

```bash
ssh -L 3210:127.0.0.1:3210 <user>@<server>
# затем открыть http://127.0.0.1:3210
```

Проверка на сервере: `curl -s http://127.0.0.1:3210/api/v1/health`.

## Разработка и тесты

```bash
# юнит-приёмка (stdlib unittest)
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
# lint (как в CI)
uv run ruff check apps tests scripts
# web: typecheck (tsc strict), паритет локалей, production-сборка
cd apps/web && pnpm typecheck && pnpm check:i18n && pnpm build
# полная приёмка VP-1 (17/17, требует поднятый стек)
python3 scripts/run_vp1_acceptance.py
# полный секрет-скан
PYTHONPATH=apps/core python3 scripts/secret_scan.py
```

## Troubleshooting

- **`/api/v1/health` = DEGRADED, runner OFFLINE** — проверить
  `systemctl status codevinci-atlas-runner`; после `restart` Core снова видит
  сокет автоматически.
- **runner `UNAUTHORIZED`** — токен не читается или неверен; проверить права
  `/run/codevinci-atlas/runner.token` (`0640`, группа `atlas-bridge`) и членство
  Core в `atlas-bridge`.
- **Core не видит сокет после рестарта Runner** — убедиться, что в unit есть
  `RuntimeDirectoryPreserve=yes` (иначе меняется инод bind-mount, VP1-D3).
- **`docker compose up` — permission denied на путях** — `.env` должен задавать
  `ATLAS_UID/ATLAS_GID/ATLAS_BRIDGE_GID` реального host-пользователя `atlas`.
- **миграции не применились** — посмотреть логи entrypoint Core
  (`docker compose logs core`); проверить `uv run atlas doctor` (поле `migrations`).

## Безопасность в эксплуатации

- Диагностика/логи/evidence не содержат email, token, cookie, raw path.
- Проверка durable-состояния: сканер secret-markers (в приёмке `c16`, backup `c9/c10`).
- Runner: только argv-массив, allowlist каталогов/исполняемых файлов,
  request-token, socket `0660`.

## Emergency Stop (целевое, VP-7)

Запрещает новые jobs, прерывает активные, отзывает аренды, не удаляет данные,
требует явного resume.
