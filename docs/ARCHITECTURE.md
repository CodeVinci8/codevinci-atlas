# ARCHITECTURE — архитектура Atlas (срез VP-0)

Полная целевая архитектура — Master Spec §7. Здесь — то, что реально
реализовано в VP-0, и как оно ложится в целевую топологию.

## Топология (целевая)

```text
Browser via SSH tunnel
        │
Web container (Nginx + React)          ← VP-1/VP-8
        │ /api
Core container (FastAPI + SQLite)      ← VP-1 (в VP-0 — контракты + SQLite-стор)
        │ Unix socket + request token  ← VP-0 прототип реализован
Host Runner (systemd, user atlas)      ← VP-0 прототип (allow_root в среде VP-0)
        ├── Codex CLI / изолированный CODEX_HOME
        ├── Claude CLI / изолированный CLAUDE_CONFIG_DIR
        ├── Git / GitHub CLI
        └── allowlisted worktrees
```

## Реализовано в VP-0

- **`apps/core/atlas_core`**
  - `contracts.py` — JobPackage, RunRequest, RunEvent, RunResult, Checkpoint,
    HandoffPackage (dataclasses, JSON-сериализуемые).
  - `errors.py` — таксономия §12.4 + классификатор (redacted evidence,
    retryable, next_action).
  - `redaction.py` — редактирование секретов + сканер secret-markers.
  - `profiles.py` — конвенция изолированных root, non-secret реестр,
    `isolated_env` (изоляция), проверка прав.
  - `capacity.py` — честная модель, `UNKNOWN` по умолчанию.
  - `store.py` — SQLite (WAL) durable-состояние + guard против записи секретов.
  - `leases.py` — один writer, heartbeat, reconciliation без автоугона.
  - `adapters/` — протокол + fake (Codex/Claude) + реальные спайки.
  - `handoff.py` — checkpoint/handoff, верификация (факт побеждает).
  - `orchestrator.py` — A→B со сменой профиля при rate limit; single-writer
    инвариант; восстановление после рестарта Core.
  - `diagnostics.py`, `web_status.py` — CLI/web без идентичностей.
  - `orm.py` + `migrations/` — durable-состояние через Alembic (прод-путь):
    `0001` audit, `0002` Project Workspace, `0003` Product Map,
    `0004` Work Orders. Прод-таблицы создаёт только Alembic (не
    ORM-автосоздание).
  - `workspace.py`/`gitbaseline.py`/`worktrees.py`/`wsleases.py` (VP-2) —
    источники проекта, read-only baseline, безопасные worktree и writer-аренды.
  - `productmap.py`/`productmap_export.py`/`api_productmap.py` (VP-3) —
    versioned Brief/Map, truth-status + evidence, поштучные решения, approval,
    один активный VP, parking lot, Portfolio-проекция, детерминированный
    экспорт MD/JSON. Owner-текст bounded+redacted; секреты в БД не попадают.
    См. [`PRODUCT_MAP.md`](PRODUCT_MAP.md).
  - `workorders.py`/`optimizer.py`/`context_engine.py`/`governor.py`/
    `vp4handoff.py`/`reconstruct.py`/`api_workorders.py` (VP-4) — VP Spec,
    Work Orders + переходы (один writer, оптимистичная блокировка,
    идемпотентность), оптимизатор (READY/MERGE/SPLIT/OWNER_REQUIRED/
    SWITCH_PROFILE), bounded JobPackage, Context Governor + checkpoint/handoff,
    свежая изолированная реконструкция (`scripts/vp4_fresh_consumer.py`,
    контракт `run-result.json`). См. [`WORK_ORDERS.md`](WORK_ORDERS.md).
- **`apps/runner/atlas_runner`**
  - `protocol.py` — framing + request-token.
  - `server.py` — asyncio UDS: allowlist, argv-only, stream+redaction,
    heartbeat, timeout, interrupt, recovery journal.
  - `client.py`, `journal.py`.

## Границы Docker и нативного Runner

Core/Web контейнеризируются для повторяемой установки. Runner остаётся
нативным процессом, потому что запускает реальные `codex`/`claude`/`git`/`gh`,
видит auth roots, worktrees и process groups. Credentials не монтируются в Web
(§7.1).

## Изоляция через per-profile идентичности

Единый runtime-layout — `/var/lib/codevinci-atlas`. У каждого профиля своя
Unix-идентичность (`atlas-cx01/02`, `atlas-cl01/02`); root профиля `0700` во
владении этой идентичности; базовые каталоги `0751` (traverse, не listable).
Сервисный пользователь `atlas` (Core/Runner) НЕ владеет профильными root и не
читает их. Runner перед запуском CLI дропает привилегии в идентичность профиля
(`user=`/`group=`; прод — `CAP_SETUID/SETGID` у systemd-сервиса или
`systemd-run --uid`). Итог: процесс профиля A физически не видит credentials B
(доказано `atlas_core.isolation.prove_isolation`). Создание идентичностей —
`scripts/atlas-runtime-setup.sh`.

## Потоки данных без секретов

Профильные credentials живут только в изолированных root CLI, доступных лишь
идентичности профиля. Core/Runner передают процессу лишь его root-переменную
(`CODEX_HOME`/`CLAUDE_CONFIG_DIR`), никогда чужую, и не читают содержимое
credentials. Всё, что идёт в лог/evidence/event/artifact, проходит `redact()`;
запись секретов в БД блокируется (`SecretLeakError`). Полный секрет-скан —
`scripts/secret_scan.py`.
