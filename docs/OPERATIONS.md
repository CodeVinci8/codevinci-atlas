# OPERATIONS — эксплуатация (VP-0)

## Runtime и идентичности

- Единый layout — `/var/lib/codevinci-atlas`.
- Создать идентичности и каталоги: `sudo bash scripts/atlas-runtime-setup.sh`
  (сервисный `atlas` + per-profile `atlas-cx01/02`, `atlas-cl01/02`).
- Root профиля `0700` во владении своей идентичности; Runner дропает привилегии
  в идентичность профиля перед запуском CLI.

## Профили

- Создать изолированные root: `scripts/profile-init.py`.
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

## Безопасность в эксплуатации

- Диагностика/логи/evidence не содержат email, token, cookie, raw path.
- Проверка durable-состояния: сканер secret-markers (в приёмке `c9`).
- Runner: только argv-массив, allowlist каталогов/исполняемых файлов,
  request-token, socket `0660`.

## Резервное копирование (целевое, VP-1)

SQLite online-backup, безопасный config, манифест artifacts с хешами,
restore dry-run, процедура RU/EN. Backups: 7 daily + 4 weekly (§23.3).

## Emergency Stop (целевое, VP-7)

Запрещает новые jobs, прерывает активные, отзывает аренды, не удаляет данные,
требует явного resume.
