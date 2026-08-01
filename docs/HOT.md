# HOT — быстрый контекст

- **Проект:** CodeVinci Atlas — self-hosted центр управления Codex и Claude.
- **Активный VP:** VP-0. **Статус: НЕ завершён** — 8/11 PASS, крит. 3–5
  (реальные A→B) = GATE_REAL за owner-логином.
- **Среда:** Ubuntu 26.04, root-only. **Codex CLI 0.146.0** (установлен),
  Claude Code 2.1.220. uv/pnpm/pytest не установлены → VP-0 на stdlib.
- **Runtime-layout:** единый **`/var/lib/codevinci-atlas`** (repo-local `./var`
  больше не используется; тесты — временный `ATLAS_DATA_DIR`).
- **Изоляция:** per-profile Unix-идентичности `atlas-cx01/02`, `atlas-cl01/02`;
  root `0700` во владении своей идентичности; Runner дропает привилегии.
  Сервисный `atlas` не читает credentials.
- **Репозиторий:** `CodeVinci8/codevinci-atlas` существует, пуст, public.
  Первый push — гейт владельца. Коммитов пока нет.
- **Git-идентичность:** имя `CodeVinci`, email в `git config` (задан).
- **Главные правила:** один writer, credentials не копируются, секреты не в
  durable-состоянии, capacity честно UNKNOWN.
- **Запуск:** `sudo bash scripts/atlas-runtime-setup.sh` →
  `PYTHONPATH=apps/core python3 scripts/profile-init.py` →
  `PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py`.
- **Owner-гейт:** `scripts/login-gate.sh` → `scripts/manual_real_probe.py`.
