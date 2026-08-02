# HOT — быстрый контекст

- **Проект:** CodeVinci Atlas — self-hosted центр управления Codex и Claude.
- **VP-0: ЗАВЕРШЁН — 11/11** (реальные A→B, merged). **VP-1: ЗАВЕРШЁН — 17/17**
  (Compose Core/Web + systemd Runner + health/migrations/audit/CLI/RU-EN/CI, merged).
- **VP-2 — Project Workspace: ЗАВЕРШЁН — 20/20** (источники, read-only git
  baseline, безопасные worktree+аренды, Project Overview Ember RU/EN, merged
  PR #3 squash `a14472a`).
- **VP-3 — Product Map: ЗАВЕРШЁН — 26/26** (intake, truth-status, версии Brief,
  решения accept/reject, nodes/edges, approval + envelope, parking lot,
  Project/Portfolio Map, diff, экспорт MD/JSON; merged PR #4 squash `07ed6f4`;
  живая БД на `0003_product_map`).
- **Активного VP нет.** Следующий гейт — VP-4 (Work Orders & Context), **не
  начат** (отдельное решение владельца).
- **Стек запущен:** `http://127.0.0.1:3210` (SSH-туннель). Core/Web healthy,
  Runner READY.
- **Профили:** 4 реальных, авторизованы; per-profile идентичности
  (`atlas-cx01/02`, `atlas-cl01/02`) и исполняемые файлы `<root>/.local/bin/*`.
- **Среда:** Ubuntu 26.04, root-only. **Codex CLI 0.146.0**, Claude Code 2.1.220.
  uv/pnpm/pytest не установлены → на stdlib.
- **Runtime-layout:** единый **`/var/lib/codevinci-atlas`** (repo-local `./var`
  больше не используется; тесты — временный `ATLAS_DATA_DIR`).
- **Изоляция:** per-profile Unix-идентичности `atlas-cx01/02`, `atlas-cl01/02`;
  root `0700` во владении своей идентичности; Runner дропает привилегии.
  Сервисный `atlas` не читает credentials.
- **Репозиторий:** `CodeVinci8/codevinci-atlas`, public. VP-0 (PR #1), VP-1
  (PR #2), VP-2 (PR #3) и VP-3 (PR #4) **смёржены** в `main`.
- **Git-идентичность:** имя `CodeVinci`, email в `git config` (задан).
- **Главные правила:** один writer, credentials не копируются, секреты не в
  durable-состоянии, capacity честно UNKNOWN.
- **Запуск:** `sudo bash scripts/atlas-runtime-setup.sh` →
  `PYTHONPATH=apps/core python3 scripts/profile-init.py` →
  `PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py`.
- **Owner-гейт:** `scripts/login-gate.sh` → `scripts/manual_real_probe.py`.
