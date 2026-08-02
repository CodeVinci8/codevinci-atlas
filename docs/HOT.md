# HOT — быстрый контекст

- **Проект:** CodeVinci Atlas — self-hosted центр управления Codex и Claude.
- **VP-0: ЗАВЕРШЁН — 11/11** (реальные A→B). **VP-1: ЗАВЕРШЁН — 17/17**
  (Compose Core/Web + systemd Runner + health/migrations/audit/CLI/RU-EN/CI).
- **Стек запущен:** `http://127.0.0.1:3210` (SSH-туннель). Core/Web healthy,
  Runner READY. Следующий — VP-2 (не начат).
- **Профили:** 4 реальных, авторизованы; per-profile идентичности
  (`atlas-cx01/02`, `atlas-cl01/02`) и исполняемые файлы `<root>/.local/bin/*`.
- **Среда:** Ubuntu 26.04, root-only. **Codex CLI 0.146.0**, Claude Code 2.1.220.
  uv/pnpm/pytest не установлены → на stdlib.
- **Runtime-layout:** единый **`/var/lib/codevinci-atlas`** (repo-local `./var`
  больше не используется; тесты — временный `ATLAS_DATA_DIR`).
- **Изоляция:** per-profile Unix-идентичности `atlas-cx01/02`, `atlas-cl01/02`;
  root `0700` во владении своей идентичности; Runner дропает привилегии.
  Сервисный `atlas` не читает credentials.
- **Репозиторий:** `CodeVinci8/codevinci-atlas`, public. VP-0 **смёржен** в
  `main` (PR #1); VP-1 доставляется через PR из `atlas/vp-1-foundation` (ещё не смёржен).
- **Git-идентичность:** имя `CodeVinci`, email в `git config` (задан).
- **Главные правила:** один writer, credentials не копируются, секреты не в
  durable-состоянии, capacity честно UNKNOWN.
- **Запуск:** `sudo bash scripts/atlas-runtime-setup.sh` →
  `PYTHONPATH=apps/core python3 scripts/profile-init.py` →
  `PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py`.
- **Owner-гейт:** `scripts/login-gate.sh` → `scripts/manual_real_probe.py`.
