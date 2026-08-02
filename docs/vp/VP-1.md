# VP-1 — Foundation (исполнимый спек)

**Статус:** ЗАВЕРШЁН — 17/17 PASS, СМЁРЖЕН в `main`.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §34.
**Ветка:** `atlas/vp-1-foundation` · **PR:** #2 (squash) · **merge:** `22951b6`.

## Result

Compose-управляемые Core и Web + systemd-управляемый нативный host Runner +
health, миграции, artifacts, Audit.

## Scope (реализовано)

- Модульный монолит: `apps/core/atlas_core` (FastAPI, SQLAlchemy 2.x,
  Alembic, health/audit/settings), `apps/runner/atlas_runner` (UDS + health).
- Локи зависимостей: `uv.lock` (Python), `apps/web/pnpm-lock.yaml` (Node).
- БД SQLite (WAL) + миграции Alembic (`0001_initial`); таблицы создаются
  ТОЛЬКО Alembic (в проде — entrypoint `alembic upgrade head`).
- Artifacts: content-addressed (SHA-256) в backup-манифесте.
- Append-only Audit (`audit_events`), redaction на входе.
- Аутентифицированный UDS Core↔Runner: request-token, least-privilege
  bridge-группа `atlas-bridge` (Core читает токен/сокет; профильные
  идентичности — нет).
- Health: правдивые `READY/DEGRADED/OFFLINE/UNAUTHORIZED`.
- systemd unit Runner (`User=atlas`, non-root; `CAP_SETUID/SETGID` для
  дропа привилегий в VP-5).
- Слоистая конфигурация (defaults → `/etc/.../config.yaml` → env);
  секреты в конфиге отклоняются.
- CLI `atlas doctor|status|backup` (осмысленные exit codes, без секретов).
- Минимальный RU/EN Web-shell (health + Runner-offline + Audit), i18n с
  проверкой идентичности ключей.
- CI (`.github/workflows/ci.yml`): ruff, миграции из пустой БД, юниты/UDS,
  typecheck, i18n, web-сборка, секрет-скан, `git diff --check`.
- Документация RU/EN.

## Acceptance boundary (§34)

Прогон `scripts/run_vp1_acceptance.py` доказывает 17 пунктов (16 из §34 +
структурная изоляция всех профилей). Итог — в
`var/artifacts/vp1/acceptance_matrix.json`.

| # | Проверка | Доказательство |
|---|---|---|
| 1 | Чистая воспроизводимая установка (frozen) | `c1_frozen_installs.json` |
| 2 | Миграция пустой БД | `c2_empty_migration.json` |
| 3 | Безопасный рестарт Core/Web | `c3_corebweb_restart.json` |
| 4 | Безопасный рестарт Runner | `c4_runner_restart.json` |
| 5 | Runner offline виден через API и Web | `c5_runner_offline.json` |
| 6 | Аутентифицированный UDS; неверный токен отклонён | `c6_uds_auth.json` |
| 7 | Audit: запись и запрос | `c7_audit.json` |
| 8 | RU/EN переключение (идентичные ключи) | `c8_i18n.json` |
| 9 | Целостность backup и хеши | `c9_10_backup.json` |
| 10 | Backup без provider-credentials | `c9_10_backup.json` |
| 11 | Core/Web/Runner НЕ от root | `c11_nonroot.json` |
| 12 | Ровно один активный VP-гейт | `c12_vp_gate.json` |
| 13 | Frozen-установки Python и Node | `c13_frozen.json` |
| 14 | Production-сборка Web | `c14_web_build.json` |
| 15 | 127.0.0.1:3210 доступен | `c15_localhost.json` |
| 16 | Полный секрет-скан чист | `c16_secret_scan.json` |
| 17 | Структурная изоляция всех профилей | `c_isolation.json` |

## Out (не входит в VP-1)

Project Workspace, Agent Pipeline, профильная маршрутизация, полный Pulse,
плотный Profiles (это VP-2/VP-5/VP-8). Провайдерские run'ы — не требуются.

## Как запустить

```bash
# runtime (root, идемпотентно) + профили
sudo bash scripts/atlas-runtime-setup.sh
# systemd Runner
sudo cp infra/systemd/codevinci-atlas-runner.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now codevinci-atlas-runner
# Compose Core/Web (UID/GID atlas в .env)
docker compose up -d --build
# приёмка
python3 scripts/run_vp1_acceptance.py
```
