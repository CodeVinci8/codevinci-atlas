# INSTALL — установка и запуск (VP-0)

Полная Compose/systemd-установка — VP-1. Для VP-0 достаточно Python 3.12+
(проверено на 3.14). uv/pnpm/Docker для доказательства VP-0 не нужны.

## Требования VP-0

- Linux, Python 3.12+.
- Для реальных probe: Codex CLI и Claude Code, авторизованные владельцем.

## Единый runtime-layout

VP-0 использует один канонический путь **`/var/lib/codevinci-atlas`** (не
repo-local). Тесты изолируются временным `ATLAS_DATA_DIR`.

## Подготовка runtime и профилей

```bash
cd /opt/CodeVinciAtlas

# 1) идентичности и каталоги (root, идемпотентно)
sudo bash scripts/atlas-runtime-setup.sh
#   создаёт atlas, atlas-cx01/02, atlas-cl01/02 и /var/lib/codevinci-atlas

# 2) изолированные root профилей (0700, во владении своих идентичностей)
PYTHONPATH=apps/core python3 scripts/profile-init.py
```

## Запуск доказательства VP-0

```bash
# юнит-приёмка
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'

# полная приёмка VP-0 + evidence (статусы PASS / PASS_MECHANISM / GATE_REAL)
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py
#   → var/artifacts/vp0/acceptance_matrix.json, acceptance_report.md

# полный секрет-скан
PYTHONPATH=apps/core python3 scripts/secret_scan.py

# диагностика + минимальный web-status (без идентичностей)
PYTHONPATH=apps/core:apps/runner python3 scripts/atlas-doctor --web-status var/status.html
```

## Owner-логин и реальные probe

```bash
# точные команды логина (выполняет владелец; по одному профилю; разные аккаунты)
scripts/login-gate.sh

# после логина — продолжить ТОТ ЖЕ VP-0 реальными probe
PYTHONPATH=apps/core:apps/runner python3 scripts/manual_real_probe.py
```

## Прод-пути (целевые, VP-1)

```text
/opt/CodeVinciAtlas/        # checkout
/etc/codevinci-atlas/       # config, вне Git
/var/lib/codevinci-atlas/   # atlas.db, artifacts, backups, profiles, worktrees
/var/log/codevinci-atlas/
/run/codevinci-atlas/runner.sock
```

Права: `atlas:atlas`; profile roots `0700`; credentials ≤ `0600`; socket `0660`;
рантайм не от root (в VP-0-среде Runner запускается с явным `allow_root`, см.
`docs/DECISIONS.md` VP0-D4).

## Установка (целевая, VP-1)

Docker Compose управляет Core/Web; systemd (`User=atlas`) — Runner. Порядок
обновления: backup → migrate → health → switch.
