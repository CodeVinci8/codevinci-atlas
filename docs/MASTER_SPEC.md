# CodeVinci Atlas — Master Specification v1

**Целевая версия продукта:** 1.0.0  
**Статус документа:** каноническое ТЗ после утверждённых решений Product Brief v0.3  
**Дата фиксации:** 1 августа 2026  
**Каноническое расположение:** `docs/MASTER_SPEC.md`  
**Репозиторий:** `CodeVinci8/codevinci-atlas`  
**Путь на сервере:** `/opt/CodeVinciAtlas`  
**Источник направления:** `CODEVINCI_ATLAS_PRODUCT_BRIEF_v0.3.md`

> Документ определяет Atlas 1.0 и десять этапов `VP-0…VP-9`. Само наличие ТЗ не разрешает создание внешних ресурсов, push, merge, deploy, удаление, импорт credentials или платные вызовы. Для них нужен явный grant владельца.

---

## 0. Решение

CodeVinci Atlas создаётся как самостоятельный публичный open-source продукт CodeVinci и self-hosted инструмент одного владельца.

Он автоматизирует текущий процесс:

```text
идея и ограничения владельца
→ Codex Planner формирует исполнимый этап
→ Claude или Codex Builder реализует его
→ Atlas собирает diff, проверки и evidence
→ независимый Codex Reviewer проверяет результат
→ один ограниченный цикл исправления
→ разрешённый PR/merge
→ сохранение состояния и следующий этап
```

Память проекта хранится не в одном бесконечном чате, а в versioned Brief, Spec, Map, Git, Work Orders, decisions, checkpoints и evidence. Сессии Codex и Claude — сменяемые исполнительские ресурсы.

### 0.1 Формат работы

`BUILD` через `VP-0…VP-9`, WIP = 1 на уровне продукта и один writer на конкретный worktree.

Каждый VP:

- даёт законченный проверяемый результат;
- имеет точный scope и out of scope;
- закрывается одним большим самостоятельным промтом;
- допускает несколько физических CLI-сессий только через checkpoint;
- завершается работающим artifact/evidence;
- не меняет незаметно scope следующего VP.

### 0.2 Главные риски

Продуктовый: Atlas может ускорить плохое решение. Поэтому автоматизация не заменяет зафиксированную цель, независимый review, фактический запуск и owner-defined границы.

Технический: нужно безопасно изолировать профили Codex/Claude, выбирать их без копирования credentials, продолжать после смены профиля/сессии, правильно классифицировать rate limit и гарантировать одного writer. Это доказывает VP-0.

---

## 1. Приоритет источников

При противоречии:

1. Последнее прямое решение владельца.
2. `OWNER-APPROVED` в `docs/DECISIONS.md`.
3. `docs/MASTER_SPEC.md`.
4. Активный `docs/vp/VP-N.md` и `docs/NEXT.md`.
5. Фактически проверенное состояние Git, filesystem, tests, CLI и API.
6. Актуальная официальная документация поставщика.
7. Brief и старые документы.
8. Сторонние repos/screenshots только как reference.

Меняющиеся команды, модели, flags, API и лицензии проверяются перед соответствующим VP. Изменение официального контракта фиксируется решением, а не скрытым отклонением.

---

## 2. Подтверждённое исходное состояние

### 2.1 От владельца

- Используются несколько подписочных профилей Codex и Claude.
- Codex — Planner/Reviewer, Claude — основной Builder.
- Результаты и продолжения сейчас переносятся между чатами вручную.
- Лимиты и обрывы требуют ожидания или ручного восстановления.
- Полная регрессия после микроправок уже расходовала лишнее время и capacity.
- Оба CLI будут установлены на одном Linux-сервере.
- `gh` может быть авторизован для commit, push, PR и merge.
- Merge PR в `main` настраивается и разрешается после `PASS`/green checks.
- UI 1.0 русский с переключением на английский.
- Приёмка: synthetic repo, затем File Atelier.
- Commits, branches, PR и operational reports — на русском.
- Стабильная документация — RU/EN.

### 2.2 Из официальных CLI-контрактов

Codex:

- `CODEX_HOME` задаёт отдельный root для config/auth/logs/sessions;
- `codex exec --json` даёт программные events;
- `codex exec resume <SESSION_ID>` продолжает session;
- `codex login --device-auth` подходит headless-серверу;
- credentials находятся в `auth.json` под `CODEX_HOME` или credential store.

Claude:

- `CLAUDE_CONFIG_DIR` задаёт отдельный root на Linux;
- `claude auth status` даёт проверяемый status;
- `claude -p --output-format json|stream-json` поддерживает automation;
- `claude -p --resume <SESSION_ID>` продолжает session;
- `/clear` создаёт пустой context, `/compact` заменяет history summary;
- внутренний transcript JSONL не является стабильным API.

### 2.3 GitHub

- Подключён профиль `CodeVinci8`.
- `CodeVinci8/codevinci-atlas` на дату фиксации не существует.
- Подтверждены кандидаты Reuse Register из раздела 24.

### 2.4 Допущения до VP-0

- 64-bit Linux, systemd, Docker Engine и Compose plugin.
- Установка через `root`; runtime от отдельного `atlas`.
- Core, Web, Runner и SQLite на одном host.
- Web по умолчанию loopback + SSH tunnel.
- Код Atlas отсутствует; repo начинается с нуля.
- Core ↔ Runner: Unix domain socket.

Несовпадение останавливает зависимое решение до read-only отчёта.

---

## 3. Термины

| Термин | Значение |
|---|---|
| Portfolio Map | Все проекты, стадии, блокеры и следующий шаг. |
| Project Map | Версионная карта одного проекта. |
| VP | Вертикальный этап с одним проверяемым результатом. |
| Work Order | Крупное ограниченное задание внутри VP. |
| JobPackage | Машиночитаемый пакет входов для запуска. |
| HandoffPackage | Компактный пакет продолжения новой сессии. |
| ReviewPackage | Spec, diff, checks и evidence для Reviewer. |
| QualityReport | Аудит качества с findings и verdict. |
| DeliveryPackage | Финальная упаковка результата. |
| Profile | Alias изолированного auth/config root. |
| Adapter | Перевод общего контракта в команды CLI. |
| Runner | Native host-процесс запуска CLI и команд. |
| Core | State machine, API, policy, routing, orchestration. |
| Lease | Временное эксклюзивное право запуска/записи. |
| Checkpoint | Безопасная точка продолжения/fork. |
| Evidence | Проверяемый факт, а не отчёт агента. |
| Capability | Конкретное разрешённое действие. |
| Scope envelope | Границы автономных решений проекта. |

---

## 4. Назначение

Atlas должен:

1. принять идею, repo и материалы;
2. зафиксировать Brief и ближайший VP;
3. сформировать Work Order;
4. выбрать role/model/effort/profile;
5. запустить официальный CLI;
6. сохранить events, diff, checks и artifacts;
7. передать результат независимому Reviewer;
8. выполнить максимум один fix-loop в STANDARD;
9. продолжить без ручного переноса чатов;
10. выполнить только разрешённые GitHub-действия;
11. показать фактическое состояние и NEXT_ACTION;
12. подготовить запускаемый результат.

Atlas не является:

- обещанием безошибочного AI;
- обходом лимитов/правил провайдера;
- продавцом API;
- multi-tenant SaaS;
- billing/marketplace;
- secret vault CLI credentials;
- универсальным CI/CD;
- причиной полной регрессии после каждой правки.

---

## 5. Пользовательские сценарии

### 5.1 Новый проект

`Create → intake → Draft Map → Brief → owner decision → VP Spec → Planner → Builder → Reviewer → result`

Факты, owner-provided данные и гипотезы различаются явно.

### 5.2 Существующий repository

`Connect → read-only audit → baseline → worktree → change → targeted checks → PR/merge`

Dirty worktree не очищается. Пользовательская работа сохраняется.

### 5.3 Лимит профиля

`error → classify → checkpoint → release lease → compatible profile → fresh session + handoff → continue`

Не провоцировать настоящий limit и не маскировать auth/network failure.

### 5.4 Большой context

`threshold → checkpoint → optional compact → fresh session → verified handoff`

Основа — fresh session, не автоматическое нажатие slash commands.

### 5.5 Quality Audit

`Run Quality Audit → target → collect → QualityReport → accept/fix/waive`

Waiver требует причины.

### 5.6 Recovery

`checkpoint → impact preview → recovery branch/run → verify → continue`

Git history по умолчанию не переписывается.

---

## 6. Scope Atlas 1.0

### 6.1 Входит

- one-owner self-host;
- Compose Core/Web;
- native host Runner;
- SQLite + versioned artifacts;
- RU/EN Web Console;
- Portfolio/Project Map;
- Brief, VP Spec, Work Orders, Context Governor;
- Planner/Builder/Reviewer + one fix-loop;
- role model/effort/profile selection;
- Codex/Claude pools;
- official login и existing auth roots;
- experimental cookie adapter contract;
- health/cooldown/drain/disable/retire;
- truthful capacity;
- checkpoints/handoffs/recovery;
- risk-based tests/Evidence Cache;
- Quality Firewall/manual audit;
- GUIDED/STANDARD/AUTONOMOUS/TRUSTED;
- commit/push/PR/permitted merge;
- Time Machine/Delivery Mode;
- diagnostics/backup/restore;
- synthetic + File Atelier acceptance.

### 6.2 Не входит

- teams/multi-tenant/billing;
- общий API relay;
- fingerprint/proxy/IP isolation;
- массовый импорт аккаунтов;
- Kubernetes/Redis/Kafka/PostgreSQL;
- два writer в одном worktree;
- произвольный production deploy;
- force push/delete/destructive rollback default;
- десятки providers;
- встроенный code editor;
- обещание точного 5h/7d остатка;
- публикация AI-текста без review.

### 6.3 Later

- remote Runner nodes;
- competing implementations;
- GitLab adapter;
- public demo;
- webhook automations;
- SaaS/control plane.

---

## 7. Архитектура установки

### 7.1 Docker и native Runner простыми словами

Core и Web контейнеризируются для повторяемой установки/обновления.

Runner остаётся обычным Linux-процессом, потому что он запускает реальные `codex`, `claude`, `git`, `gh`, видит auth roots, worktrees, process groups и interactive login. Credentials не монтируются в Web.

### 7.2 Топология

```text
Browser via SSH tunnel
        │
Web container (Nginx + React)
        │ /api
Core container (FastAPI + SQLite)
        │ Unix socket + request token
Host Runner (systemd, user atlas)
        ├── Codex CLI / isolated CODEX_HOME
        ├── Claude CLI / isolated CLAUDE_CONFIG_DIR
        ├── Git/GitHub CLI
        └── allowlisted worktrees
```

### 7.3 Пути

```text
/opt/CodeVinciAtlas/                    # checkout
/etc/codevinci-atlas/                   # config, no Git
/var/lib/codevinci-atlas/
  atlas.db
  artifacts/
  backups/
  profiles/codex/<profile-id>/
  profiles/claude/<profile-id>/
  runner/
  worktrees/
/var/log/codevinci-atlas/
/run/codevinci-atlas/runner.sock
```

Permissions:

- `atlas:atlas`;
- profile roots `0700`;
- credentials не шире `0600`;
- socket `0660`;
- runtime не от root.

### 7.4 Network

- Web default `127.0.0.1:3210`;
- доступ по SSH tunnel;
- Core не public;
- Runner без TCP listener;
- domain/Nginx/TLS — отдельный owner-approved increment.

### 7.5 Lifecycle

- Compose управляет Core/Web.
- systemd управляет Runner.
- Core restart не должен убивать child Runner.
- Runner restart помечает jobs `INTERRUPTED`; recovery из checkpoint.
- Upgrade: backup → migrate → health → switch.

---

## 8. Стек

### 8.1 Core/Runner

- Python 3.12+;
- FastAPI, Pydantic v2;
- SQLAlchemy 2.x, Alembic;
- SQLite WAL;
- `asyncio` subprocess;
- `pexpect` только при реальной необходимости onboarding;
- `uv`;
- pytest/pytest-asyncio.

### 8.2 Web

- React 19, TypeScript strict, Vite;
- React Router, TanStack Query;
- CSS variables + CSS Modules либо ограниченный Tailwind v4 по решению VP-1;
- SVG/CSS charts;
- Vitest/Testing Library/Playwright.

### 8.3 Infra/quality

- Docker Compose, Nginx, systemd;
- Git, GitHub CLI;
- ruff, mypy, eslint, prettier;
- gitleaks или проверенный эквивалент.

Новая runtime dependency требует сценария, лицензии, maintenance check и записи решения.

---

## 9. Repository layout

```text
/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── README.en.md
├── LICENSE
├── NOTICE
├── SECURITY.md
├── SECURITY.en.md
├── CONTRIBUTING.md
├── CONTRIBUTING.en.md
├── .env.example
├── compose.yaml
├── pyproject.toml
├── uv.lock
├── package.json
├── pnpm-lock.yaml
├── apps/
│   ├── core/atlas_core/
│   ├── runner/atlas_runner/
│   └── web/src/
├── contracts/
│   ├── openapi.json
│   ├── events/
│   └── schemas/
├── docs/
│   ├── MASTER_SPEC.md
│   ├── BRIEF.md
│   ├── ARCHITECTURE.md
│   ├── INSTALL.md
│   ├── OPERATIONS.md
│   ├── ADAPTERS.md
│   ├── TEST_POLICY.md
│   ├── DECISIONS.md
│   ├── NEXT.md
│   ├── HOT.md
│   ├── REUSE_REGISTER.md
│   ├── en/
│   └── vp/
├── infra/docker/
├── infra/systemd/
├── scripts/
├── tests/
│   ├── contract/
│   ├── e2e/
│   ├── fixtures/
│   └── synthetic-repo/
└── var/                         # ignored local dev only
```

Stable docs имеют RU/EN пары. `HOT`, `NEXT`, `DECISIONS`, VP reports, commits и оперативные записи — русский source of truth. Изменение публичного контракта обновляет RU/EN в одном commit.

---

## 10. Configuration

### 10.1 Layers

1. built-in safe defaults;
2. `/etc/codevinci-atlas/config.yaml`;
3. project policy;
4. active grant;
5. one-run owner override.

Нижний слой не расширяет owner restriction.

### 10.2 `.env.example`

```dotenv
ATLAS_ENV=development
ATLAS_DATA_DIR=/var/lib/codevinci-atlas
ATLAS_RUNNER_SOCKET=/run/codevinci-atlas/runner.sock
ATLAS_WEB_ORIGIN=http://127.0.0.1:3210
ATLAS_LOG_LEVEL=INFO
```

Tokens, cookies, API keys и provider credentials запрещены.

### 10.3 Project policy

```yaml
project_id: file-atelier
autonomy: STANDARD
default_branch: main
allowed_repositories:
  - CodeVinci8/file-atelier
allowed_workspaces:
  - /opt/FileAtelier
roles:
  planner: codevinci-max
  builder: codevinci-max
  reviewer: codevinci-max
test_policy: balanced
capabilities:
  repository_read: true
  repository_write: true
  commands: true
  install_dependencies: true
  commit: true
  push_feature_branch: true
  create_pull_request: true
  merge_to_main_after_pass: true
  direct_push_main: false
  force_push: false
  delete_branch: false
  delete_repository: false
  production_deploy: false
  paid_api_calls: false
```

---

## 11. Profiles и credentials

### 11.1 Правило владения

> Credentials каждого профиля принадлежат ровно одному auth owner.

Native CLI → отдельный root CLI. Sidecar → sidecar. Core не копирует tokens между ними.

### 11.2 Privacy

UI показывает aliases `codex-plus-01`, `claude-pro-01`. Email, account ID, token, cookies и raw path скрыты.

### 11.3 Profile states

```text
UNCONFIGURED → AUTH_REQUIRED → READY → LEASED → READY
                          ↘ COOLDOWN
                          ↘ ERROR
READY → DRAINING → DISABLED → RETIRED
```

### 11.4 Onboarding

1. **Official login, recommended**
   - Codex: отдельный `CODEX_HOME`, `codex login --device-auth` на headless.
   - Claude: отдельный `CLAUDE_CONFIG_DIR`, `claude auth login`/`/login`.
2. **Existing auth root**
   - проверить owner/permissions/status;
   - path в admin allowlist;
   - ничего не копировать.
3. **Cookie import, experimental**
   - отдельный adapter;
   - off by default, not release blocker;
   - только явно предоставленный bundle;
   - не извлекать из браузера без отдельного подтверждения;
   - не обходить MFA/CAPTCHA/provider restrictions;
   - raw cookies не проходят через Core DB/logs/artifacts;
   - temporary file уничтожается;
   - при несовместимости вернуть `UNSUPPORTED`.

Поддержка cookie path для каждого provider заявляется только после текущего compatibility/security spike и проверки правил сервиса.

### 11.5 Health

- executable/version;
- auth status;
- root permissions;
- optional minimal read-only probe;
- last result/error;
- redacted output.

### 11.6 Capacity

```text
status: AVAILABLE | LOW | EXHAUSTED | UNKNOWN
5h_remaining: nullable
7d_remaining: nullable
reset_at: nullable
source: official_structured | wrapper | observed | manual | unknown
observed_at
confidence
```

Если стабильного interface нет — `UNKNOWN`, а не вычисленная фикция.

---

## 12. Agent Adapter

```python
class AgentAdapter(Protocol):
    async def discover_capabilities(profile): ...
    async def auth_status(profile): ...
    async def start(job: JobPackage): ...
    async def resume(session_id, job: JobPackage): ...
    async def stream(handle): ...
    async def interrupt(handle): ...
    async def collect_result(handle): ...
    async def capacity(profile): ...
```

### 12.1 Codex

- new: `codex exec --json`;
- strict verdict: `--output-schema`;
- resume: `codex exec resume <SESSION_ID>`;
- profile: `CODEX_HOME=<root>`;
- sandbox/approval explicit from grant;
- version recorded.

Interactive `/clear` не automation API. Fresh run = новый `codex exec`.

### 12.2 Claude

- new: `claude -p --output-format stream-json`;
- resume: `claude -p --resume <SESSION_ID>`;
- profile: `CLAUDE_CONFIG_DIR=<root>`;
- model: `--model`;
- effort: `--effort`;
- tools/permissions explicit;
- version recorded.

Обычный Builder не использует `--bare`, потому что project `CLAUDE.md`/settings нужны. Bare допустим для изолированных diagnostics.

### 12.3 Session capabilities

- `NEW_SESSION` required;
- `RESUME_BY_ID` required;
- `FRESH_WITH_HANDOFF` required;
- `COMPACT` optional;
- `CLEAR_INTERACTIVE` operator-only;
- `FORK_NATIVE` optional;
- transcript только через public CLI interface.

### 12.4 Errors

```text
AUTH_REQUIRED
AUTH_EXPIRED
RATE_LIMITED
CAPACITY_UNKNOWN
NETWORK_ERROR
PROVIDER_UNAVAILABLE
MODEL_UNAVAILABLE
PERMISSION_DENIED
TOOL_FAILED
PROCESS_CRASHED
TIMEOUT
OUTPUT_INVALID
WORKTREE_CONFLICT
POLICY_DENIED
USER_INTERRUPTED
UNKNOWN
```

Каждый error: redacted evidence, retryable, next action.

---

## 13. Runner

### 13.1 Обязанности

- validate RunRequest;
- resolve profile alias без раскрытия Core;
- check workspace/executable allowlist;
- create process group;
- stream normalized events;
- bound/redact output;
- heartbeat;
- interrupt/timeout;
- minimal recovery journal;
- return exit code/hashes/artifacts.

### 13.2 Запреты

- raw token/cookie в обычном request;
- arbitrary shell string;
- root;
- path outside allowlist;
- branch change без Work Order;
- destructive action без capability;
- environment dump;
- лишнее наследование credentials.

RunRequest использует argv array, cwd, allowed env keys. Shell — только explicit project command.

### 13.3 Limits

- timeout;
- output/artifact bytes;
- child count;
- network mode;
- allowed dirs;
- cancellation grace.

### 13.4 Lease

Lease связан с project, worktree, run, role, expires_at, heartbeat. После heartbeat loss новый writer запрещён до Git/process reconciliation.

---

## 14. Project Workspace и Git

### 14.1 Inputs

- local Git path;
- GitHub repo;
- archive read-only intake;
- empty project.

### 14.2 Baseline before write

- path/branch/HEAD/remotes;
- porcelain status;
- tracked/untracked summary;
- nested instructions;
- package managers;
- baseline commands;
- secret scan status без secrets.

Dirty worktree не очищается.

### 14.3 Worktrees

- branch `atlas/vp-<n>-<slug>`;
- one Builder writer;
- Planner/Reviewer read-only;
- альтернативная реализация — новый worktree;
- removal только отдельным recoverable action.

### 14.4 Commit contract

- один логический результат;
- subject по-русски, imperative, ≤72;
- no fake `Co-Authored-By`;
- no `--author`;
- effective name/email checked;
- body `Проверки:` только с выполненными командами.

Примеры:

- `Добавить изоляцию профилей CLI`
- `Сохранить состояние рабочих запусков`
- `Реализовать карту проектов Atlas`
- `Добавить проверку качества перед слиянием`

---

## 15. Durable state

### 15.1 Хранится

Brief/spec versions, maps, decisions, work orders, criteria, runs, sessions metadata, Git baselines, evidence, reviews, grants, checkpoints, audit, deliveries.

### 15.2 Project state

```text
DRAFT → READY → ACTIVE → PAUSED → ACTIVE
                  ↘ BLOCKED
                  ↘ DELIVERED
                  ↘ ARCHIVED
```

### 15.3 VP state

```text
DRAFT → OWNER_APPROVAL → READY → PLANNING → BUILDING
BUILDING → REVIEWING → PASSED
                    ↘ REVISING → REVIEWING
                    ↘ FAILED/BLOCKED
```

Один automatic `REVISING` в STANDARD.

### 15.4 Truth status

`VERIFIED`, `OWNER_PROVIDED`, `INFERRED`, `HYPOTHESIS`, `STALE`, `UNKNOWN`.

Только evidence переводит гипотезу в VERIFIED.

---

## 16. Work Orders и Context Engine

### 16.1 Work Order

```yaml
id: wo-vp4-001
project_id: codevinci-atlas
vp_id: VP-4
role: builder
goal: "Один законченный результат"
source_of_truth:
  - docs/MASTER_SPEC.md
  - docs/vp/VP-4.md
baseline:
  branch: atlas/vp-4-context
  head: <sha>
scope:
  files: []
  components: []
out_of_scope: []
inputs: []
acceptance_criteria: []
required_checks: []
test_impact: []
capabilities: []
stop_conditions: []
report_schema: contracts/schemas/run-result.json
```

### 16.2 Optimizer

`READY`, `MERGE_TASKS`, `SPLIT_AT_CHECKPOINT`, `SWITCH_PROFILE`, `OWNER_REQUIRED`.

Он не меняет scope/criteria. Split только по двум законченным результатам.

### 16.3 JobPackage

Не включает весь repo, полный chat, повторяющиеся logs, credentials и future ideas без relevance.

### 16.4 HandoffPackage

- IDs и цель;
- immutable constraints;
- baseline/current HEAD;
- changed files;
- commands/outcomes;
- failures;
- acceptance matrix;
- decisions;
- exact next action;
- prohibited actions;
- artifact references.

Новый агент сверяет handoff с Git/DB. Фактическое state побеждает, mismatch audited.

### 16.5 Rotation triggers

VP boundary, profile switch, rate limit, context threshold, repetition, crash, failed review, owner command.

Алгоритм:

1. stop new actions;
2. collect diff/process;
3. impacted checks;
4. checkpoint;
5. handoff;
6. release lease;
7. select profile/model;
8. fresh session;
9. baseline acknowledgement;
10. continue.

---

## 17. Agent Pipeline

### 17.1 Default

`Codex Planner → Claude Builder → Codex Reviewer`.

Overrides доступны; Builder session не Reviewer.

### 17.2 Model registry

Хранит provider ID/alias, display, discovered_at, efforts, context/structured capabilities, availability, source/confidence.

Preset `CodeVinci Max Quality` выбирает доступный Sol very-high/max для Codex и Opus high/xhigh для Claude. Никакой silent fallback: effective selection и причина видимы.

### 17.3 Router priority

1. owner override;
2. role compatibility;
3. profile READY;
4. safe affinity;
5. capacity;
6. cooldown/error;
7. least recently used;
8. deterministic tie.

### 17.4 Run lifecycle

```text
QUEUED → PREPARING → RUNNING → COLLECTING → SUCCEEDED
                    ↘ RATE_LIMITED
                    ↘ FAILED
                    ↘ INTERRUPTED
                    ↘ CANCELLED
```

### 17.5 Stop

Outside grant, scope drift, dirty conflict, suspected leak, second failed fix, unresolved license, invalid output twice, no profile.

---

## 18. Review, Quality и tests

### 18.1 ReviewPackage

Brief/VP/Work Order, SHAs, diff, acceptance matrix, impact decision, results/cache, artifacts, limitations, Builder report, grant snapshot.

### 18.2 Verdicts

`PASS`, `REVISE`, `BLOCKED`, `OWNER_REQUIRED`, `INVALID_EVIDENCE`.

Finding: severity, criterion, location, evidence, action, blocking.

### 18.3 Quality Firewall

- Brief/VP compliance;
- real run;
- secrets/privacy;
- dependencies/freshness;
- needless architecture;
- AI placeholders;
- docs-command parity;
- web accessibility/states;
- security/test relevance.

### 18.4 Manual audit

Target: project, VP, diff, screen, dependencies, docs, AI waste. Audit itself не меняет code.

### 18.5 Impact classes

| Class | Example | Gate |
|---|---|---|
| DOC_ONLY | docs | markdown/link/render |
| LOCAL | module | targeted unit/lint |
| INTEGRATION | API/DB/adapter | unit + integration |
| SHARED | schema/router/policy | dependent suites |
| HIGH_RISK | auth/grant/migration/release | full relevant + security |

### 18.6 Frequency

- local change: targeted;
- checkpoint: affected integration;
- pre-merge: diff-based CI once;
- VP PASS: VP acceptance;
- 1.0: release gate.

Full regression после micro-fix запрещена без risk trigger.

### 18.7 Evidence Cache

Key: SHA + command/version + relevant input hashes + environment + scope. Reuse only exact, with visible reason.

### 18.8 Fix-loop

REVISE → focused fix → impacted checks → re-review. Второй REVISE → BLOCKED.

---

## 19. Autonomy и grants

| Mode | Contract |
|---|---|
| GUIDED | Owner gate перед VP/чувствительным действием. |
| STANDARD | Owner утверждает VP; Atlas закрывает его, one fix, allowed merge. |
| AUTONOMOUS | Следующие VP внутри envelope/budget. |
| TRUSTED | Широкий временный project/environment grant. |

Grant: owner ref, allowlists, environment, capabilities, branch rules, budget, time, revocation, reason, audit.

Emergency Stop запрещает новые jobs, interrupts active, revokes leases, не удаляет data, требует explicit resume.

Отдельные capabilities: direct main, force push, deletion, production, DNS/Nginx, paid calls, cookie import, destructive rollback.

---

## 20. GitHub workflow

### 20.1 Adapter

1.0 использует `gh` от runtime user. Token остаётся в auth store `gh`.

Capabilities: auth status, metadata, push, PR, checks, merge, read PR/issue context, idempotency.

### 20.2 STANDARD merge gate

Merge в `main` без повторного вопроса только если:

1. repo/base в allowlist;
2. grant разрешает;
3. baseline известен;
4. diff в VP;
5. Reviewer PASS;
6. no blocking finding;
7. checks green for current SHA;
8. mergeable;
9. no owner gate;
10. audit before/after.

### 20.3 Policy

Feature branch required; direct main/force/delete off; squash default; PR RU; release notes RU/EN.

### 20.4 Репозиторий Atlas

- `CodeVinci8/codevinci-atlas`;
- public;
- создать пустым без README/license/.gitignore;
- About: `Self-hosted центр управления Codex и Claude: проекты, профили, review, evidence и безопасная автоматизация.`
- Topics: `codevinci`, `ai-agents`, `developer-tools`, `self-hosted`, `codex`, `claude-code`, `automation`, `agent-orchestration`, `fastapi`, `react`, `docker`, `sqlite`.

License выбирается после VP-0 reuse audit. Кандидаты MIT/Apache-2.0. Код Sub2API не копируется.

---

## 21. Time Machine

Checkpoint: DB version, VP/WO, branch/HEAD/status, patch hash/artifact, profile/model/effort (no secrets), session IDs, grant, tests/evidence, handoff, cause.

Operations: resume, replay other profile, fork, compare, restore state, rollback preview.

Defaults: new run/branch; no rewrite; verified hashes; no credentials; destructive rollback separate.

---

## 22. Delivery Mode

На финальной границе:

- verified commands;
- README RU/EN;
- `.env.example`;
- changelog/release notes RU/EN;
- release evidence;
- limitations;
- install/update/rollback;
- approved screenshots;
- draft GitHub Release;
- Orbit card;
- case draft без invented facts.

Preparation ≠ publication.

---

## 23. Data/storage

### 23.1 Tables

`projects`, `project_sources`, `brief_versions`, `spec_versions`, `map_nodes`, `map_edges`, `decisions`, `work_orders`, `runs`, `agent_sessions`, `profiles`, `capacity_snapshots`, `leases`, `reviews`, `findings`, `evidence`, `test_results`, `grants`, `checkpoints`, `deliveries`, `audit_events`.

Common: sortable ID, UTC timestamps, optimistic version, actor, correlation ID, soft archive.

### 23.2 Artifacts

Content-addressed SHA-256. DB содержит metadata/relative path.

No credentials, unbounded transcripts, env dump, unredacted image, external symlink.

### 23.3 Retention

Audit/decisions durable; transcripts/logs default 30 days; PASS evidence durable; cookie temp immediate wipe; backups 7 daily + 4 weekly.

---

## 24. Reuse Register

| Project | Польза | License | Решение | Boundary |
|---|---|---|---|---|
| [Sub2API](https://github.com/Wei-Shaw/sub2api) | account states, pool UI, scheduler concepts | LGPL-3.0 + no-commercial notice в README | REFERENCE | UI/behavior only, no core copy. |
| [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | Codex/Claude OAuth, multi-account, API | MIT | SPIKE/POSSIBLE WRAP | Optional sidecar, never same credential owner. |
| [codex-multi-auth](https://github.com/ndycode/codex-multi-auth) | Codex profiles, health, rotation | MIT | SPIKE/POSSIBLE WRAP | Verify current CLI/ownership. |
| [CCS](https://github.com/kaitranntt/ccs) | isolated profiles, switching, dashboard | MIT | REFERENCE/POSSIBLE WRAP | Do not copy whole runtime. |
| [claude-code-router](https://github.com/musistudio/claude-code-router) | routing/failover/observability | MIT | REFERENCE | Not mandatory gateway. |
| [ccusage](https://github.com/ccusage/ccusage) | local Claude/Codex usage reports | MIT | SPIKE/WRAP | Usage ≠ exact remaining limit. |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | spec-driven workflow | MIT | REFERENCE | Ideas, not full generator. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | runtime/events/self-host patterns | MIT | REFERENCE | Too heavy as dependency. |

Code adoption requires pinned commit/tag, license/NOTICE, security, credential diagram, adapter boundary, removal path, recorded decision.

---

## 25. API/events

### 25.1 REST

```text
/api/v1/health
/api/v1/projects
/api/v1/projects/{id}/map
/api/v1/projects/{id}/briefs
/api/v1/projects/{id}/specs
/api/v1/projects/{id}/work-orders
/api/v1/runs
/api/v1/profiles
/api/v1/reviews
/api/v1/quality-audits
/api/v1/evidence
/api/v1/checkpoints
/api/v1/grants
/api/v1/deliveries
/api/v1/audit
/api/v1/settings
```

Realtime: SSE `GET /api/v1/events?after=<event_id>`. WebSocket без сценария не нужен.

Event:

```json
{
  "id": "evt_...",
  "type": "run.started",
  "occurred_at": "2026-08-01T00:00:00Z",
  "project_id": "...",
  "run_id": "...",
  "actor": {"type": "system", "id": "core"},
  "payload": {},
  "schema_version": 1
}
```

Mutations принимают `Idempotency-Key`. Error: stable code, localized message, correlation ID, retryable.

---

## 26. Web Console IA

### 26.1 Global nav

1. Pulse
2. Projects
3. Profiles
4. Runs
5. Quality
6. Deliveries
7. Audit
8. Settings

Top bar: project, VP/state, autonomy/grant, capacity summary, Runner, RU/EN, Pause, Emergency Stop.

Project tabs: Overview, Map, Brief & Spec, Work Orders, Runs, Evidence, Timeline, Project Settings.

Inspector Drawer справа показывает run/profile/node/finding/evidence с deep link и focus restore.

---

## 27. Layout

### 27.1 Desktop

Sidebar 232–256, top 56–64, drawer 360–420, 12-column. Tables use full width. One main CTA.

### 27.2 Pulse

Above fold:

- active project/VP;
- Planner→Builder→Reviewer;
- run state;
- model/effort/profile;
- confirmed criteria;
- blocker/NEXT_ACTION;
- pause/open run/review.

Below: pools, timeline, impacted checks, diff, truthful usage, owner gates.

### 27.3 Projects

Portfolio Map + table. Columns: project, stage, VP, last run, blocker, health, next. No fake percentage.

### 27.4 Profiles

≤8 cards; >8 table. Add/login/register/import/enable/disable/drain/health/cooldown/retire. Numeric bar only with confirmed source; otherwise `Данные недоступны`.

### 27.5 Runs

Events, commands, transcript reference, diff, checks, artifacts, model/profile, duration, handoff chain, interrupt/checkpoint.

### 27.6 Map

Goal, Brief decisions, VP sequence, Work Orders, blockers/evidence, parking lot. AI proposes; scope change follows envelope.

### 27.7 Quality

Blocking first, filters, Builder claim vs evidence, Run Audit, waiver, create fix Work Order.

### 27.8 Settings

General/locale, models, routing, tests, grants, adapters, retention, GitHub, security, diagnostics.

---

## 28. CodeVinci Ember

### 28.1 Character

Спокойный, технологичный, тёплый, точный; плотный для data и воздушный для decisions.

### 28.2 Initial tokens

```css
--bg-0: #0b0b0c;
--bg-1: #111113;
--surface-1: #171719;
--surface-2: #1d1d20;
--border-subtle: #2a2928;
--text-primary: #f2eee8;
--text-secondary: #aaa49c;
--ember-500: #f28a3d;
--ember-400: #ffad66;
--success: #54b982;
--warning: #e2a84b;
--danger: #df6262;
--info: #6e9ecf;
```

Final tokens pass contrast.

Glow only active VP/run/profile/review/status node. Status never color-only.

Motion: 120–180 micro, 220–320 panel, 400–600 handoff, one major animation, reduced-motion.

Запрещены AI gradient, endless rounded cards, fake logs/progress, excess glass, particles, constant motion, emails.

---

## 29. RU/EN, responsive, accessibility

### 29.1 Locale

RU default; EN switch; typed catalog keys; server stable codes; no automatic translation user content; missing key fails CI; locale formatting.

### 29.2 Responsive

Widths 320/390/768/1024/1280/1440/1920. Collapsible sidebar, mobile monitoring/emergency actions, graph edit may be desktop-only, touch 44px, no accidental overflow.

### 29.3 Accessibility

WCAG 2.2 AA: landmarks, skip, keyboard, focus, dialogs/drawers, focus restore, non-color status, reduced motion, contrast, tables, short live regions, chart text alternative, active language.

---

## 30. Security

### 30.1 Assets/threats

Credentials, GitHub auth, repos, dirty work, private inputs, grants, audit. Threats: prompt/command injection, traversal, symlink escape, leak, confused deputy, stale review, unauthorized merge, two writers, lifecycle scripts, exposed panel.

### 30.2 Controls

Dedicated user, UDS + nonce/token, allowlists, argv, minimal env, writer lease, redaction, secret scan, content hashes, SHA-bound review, append audit, loopback, backup, install capability, no root.

Repository docs/issues/web/model output считаются data и не расширяют grant/source priority.

### 30.3 Cookie gate

Provider compatibility, terms decision, protected temp, no log, one-time, explicit capability, post-scan, logout/revocation.

### 30.4 Acceptance

Secret/history scan, redaction, traversal/symlink, lease replay, forged PASS/checks, socket permissions, cross-profile denial, backup no credentials.

---

## 31. Observability/recovery

JSON logs: time, level, service, event/correlation/run, redacted message, error. No prompts by default.

Metrics: queue/active, duration, handoffs, limits, verdicts, cache hits, test counts, heartbeat, storage. No identity/secrets.

Audit: grants, profile state, runs, decisions, checkpoints, reviews/waivers, GitHub, restore, settings, emergency.

Backup: SQLite online API, safe config, artifact manifest/hashes, restore dry-run, RU/EN procedure.

---

## 32. Global testing

### 32.1 Fast

Targeted Python/TS lint/type/unit, schema, `git diff --check`.

### 32.2 Integration

Core↔Runner UDS, migrations, fake adapters, leases, worktree, GitHub fake, SSE reconnect, catalogs.

### 32.3 Synthetic fixtures

Success, events, invalid output, rate limit, auth, hang, partial change, crash, resume, secret marker.

### 32.4 Real CLI (`manual-real`)

Auth status, minimal read-only prompt, A→B, resume, fresh handoff, interrupt, no credential copy. Не обычная CI.

### 32.5 Browser

Pulse, onboarding, UNKNOWN capacity, Map keyboard, run, review, emergency, RU/EN, 390/768/1440, reduced motion, offline states.

### 32.6 Release

Clean checkout, Compose/systemd, migrations, synthetic E2E, File Atelier, security, backup/restore, docs commands, no secrets, no regression loop.

---

## 33. VP-0 — Profile Pool & Live Handoff Proof

### Цель

Доказать главный риск до платформы.

### Result

CLI diagnostic + minimal Web status показывают profiles/health и реальные handoff без identities.

### Scope

- bootstrap repo/docs;
- minimal Core/Runner contracts;
- profile root convention/registry;
- official login helpers/existing roots;
- fake + minimal real adapters;
- UDS prototype;
- writer lease;
- checkpoint/handoff prototype;
- real A→B для Codex и Claude;
- simulated rate limit;
- Core restart/Runner interruption;
- security scans;
- Reuse Register update.

### Out

Full dashboard/intake/GitHub merge/cookie implementation/Map/polish/File Atelier.

### Acceptance

1. 2 Codex + 2 Claude roots isolated and aliased.
2. Cross-read blocked.
3. Minimal run A structured.
4. B continues handoff.
5. Доказано для обоих providers.
6. Simulated rate limit switches without second writer.
7. Core restart preserves state.
8. Runner interruption recovers.
9. No credentials in DB/Git/logs/artifacts.
10. UNKNOWN capacity honest.
11. Repeatable report/evidence.

### Tests

State/error unit, fake contracts, lease race, permissions, restart/recovery, redaction, four minimal real probes.

### Stop

If isolation/handoff fails, no VP-1. Alternative adapter spike first.

---

## 34. VP-1 — Foundation

### Result

Compose Core/Web + systemd Runner + health/migrations/Audit.

### Scope

Monorepo, locks, modules, DB/migrations, artifacts, audit, authenticated UDS, health, systemd, config, CLI `doctor/backup/status`, RU/EN shell, CI, docs.

### Acceptance

Clean install, empty migration, safe restart, Runner offline visible, Audit query, language switch, backup integrity, no root, one VP gate.

---

## 35. VP-2 — Project Workspace

### Result

Synthetic repo connects, baseline/dirty/instructions visible, worktree created.

### Scope

Create/connect, path/GitHub/archive, sources, Git audit, instructions, baseline commands, worktrees, leases, dirty flows, Overview, traversal security.

### Acceptance

Clean/dirty, preserve user work, no destructive Git, allowed branch, second writer denied, archive escape blocked, baseline persisted, disconnect no delete.

---

## 36. VP-3 — Product Map

### Result

Draft Map, approved Brief, Project/Portfolio Map and version diff.

### Scope

Intake, truth status, Brief versions, decisions, nodes/edges, owner approval, envelope, parking lot, Map UI, Markdown/JSON export.

### Acceptance

No silent overwrite, VERIFIED needs evidence, individual accept/reject, one active VP, restart durability, correct portfolio, UI controls translated.

---

## 37. VP-4 — Work Orders & Context

### Result

VP Spec/JobPackage, controlled split and fresh handoff continuation.

### Scope

Spec schema, Work Order lifecycle, Optimizer, relevance, Context Governor, handoff, rotation, optional compact probe, Work Orders UI.

### Acceptance

Meaningful merge/split, no lost criteria, fresh session reconstructs state, stale handoff rejected, no credentials/full chat, exact next, compact fallback.

---

## 38. VP-5 — Agent Pipeline

### Result

Synthetic change through Codex Planner → Claude Builder → Codex Reviewer with profile/session switch.

### Scope

Roles, registry/presets, router, capacity/health, normalized events, outputs, leases, bounded retry, handoff/recovery, Runs UI, pause/resume.

### Acceptance

Effective choices visible, no silent fallback, Reviewer independent, one writer, rate limit bounded, auth not endless, resume/fresh tested, interruption recoverable, real artifact.

---

## 39. VP-6 — Review & Quality

### Result

Seeded defects/AI waste found; targeted fix-loop; QualityReport explains.

### Scope

ReviewPackage, findings, impact engine, Evidence Cache, gates, Firewall, manual audit, one fix, waiver, UI, source freshness.

### Acceptance

Broken behavior blocks, false Builder claim rejected, doc fix no full regression, shared/auth escalates, cache invalidates, second revise blocks, findings evidence, no endless polish.

---

## 40. VP-7 — Autonomy, GitHub & Time Machine

### Result

On synthetic GitHub repo branch/commit/PR and test merge after PASS; replay creates safe branch.

### Scope

Modes, grant UI/API, expiry/revoke, GitHub adapter, RU Git contract, SHA checks, merge gate, checkpoints/replay/compare, recovery, Audit, Emergency Stop.

### Out

Production deploy, force push, deletion, real Atlas merge without current approval.

### Acceptance

Expired grant denied, stale PASS denied, PR idempotent, RU commit, author checked, current SHA merge only, stop blocks jobs, replay new branch, destructive unavailable, audit complete.

---

## 41. VP-8 — Full Web Console

### Result

All daily operations in RU/EN panel; Pulse truthful; Profiles scales 4→40.

### Scope

All nav/screens, Inspector Drawer, Ember, i18n, accessibility, responsive, performance.

### Acceptance

State understood in 10 sec, no fake numbers, cards/table, keyboard, reduced motion, 390/768/1440 accepted, WCAG checks, offline states, no excess motion, budgets documented.

---

## 42. VP-9 — Release Proof / File Atelier

### Цель

Clean install + real known project.

### File Atelier increment

Owner chooses one small useful vertical improvement. No big rewrite. It must have user result, bounded diff, targeted checks, visible/CLI evidence, no production secrets.

### Scope

Clean install, backup/restore, synthetic E2E, File Atelier intake/Brief/VP, full pipeline, deliberate handoff, one revise, targeted checks/merge gate, DeliveryPackage, security, RU/EN, 1.0.0 report.

### Acceptance

1. Documented clean install.
2. Synthetic E2E repeatable.
3. Useful owner-approved File Atelier result.
4. No manual chat transfer.
5. Handoff loses no criteria.
6. Micro-fix no unjustified full regression.
7. Reviewer/Quality confirm.
8. Permitted PR/merge gate.
9. Delivery matches repo.
10. Restore works.
11. No credentials anywhere.
12. RU/EN accepted.

### Owner gates

File Atelier task, real push/merge, Atlas LICENSE, public release/tag, optional domain, real cookie activation.

---

## 43. Definition of Done 1.0

- VP-0…VP-9 pass;
- Compose + native non-root Runner;
- 2 Codex + 2 Claude profiles isolated;
- official login/existing roots;
- experimental cookie status honest;
- memory survives session/profile/process;
- pipeline makes real artifact;
- one writer race proof;
- targeted tests save repeats without risk loss;
- QualityReport catches seeded waste/failure;
- merge gate current SHA/grant;
- safe Time Machine;
- truthful Pulse/NEXT_ACTION;
- RU/EN UI/docs;
- File Atelier accepted;
- backup/restore/security/release pass;
- public repo no private/secrets.

---

## 44. Report after each VP

Claude reports in Russian:

1. Factual status.
2. User-visible result.
3. Changed files.
4. Decisions.
5. Checks.
6. Evidence/exit codes/artifacts.
7. Test impact reason.
8. Security/privacy.
9. Limitations.
10. Not done.
11. Git branch/HEAD/status/remote.
12. Proposed Russian commits.
13. Authorized external actions with URL/SHA.
14. One real owner gate.
15. NEXT_ACTION.

«Готово» forbidden without acceptance evidence.

---

## 45. Simplification order

1. decorative motion;
2. historical charts;
3. graph editing → structured form;
4. capacity UNKNOWN;
5. disable compact;
6. cookie adapter UNSUPPORTED;
7. Delivery files only;
8. GitHub only through gh;
9. SSH tunnel instead of domain.

Never simplify credential ownership, one writer, durable state, verified handoff, SHA-bound merge, redaction, test impact, backup, truth, RU/EN switch, File Atelier proof.

---

## 46. Stop criteria

Stop/rethink if:

- VP-0 fails isolation/handoff;
- two VP no new evidence;
- Core needs credentials;
- native CLI unsafe;
- two credential owners;
- micro-fix full regression returns;
- fake capacity/progress;
- File Atelier requires manual chat transfer;
- security only works as root.

---

## 47. First executor action

Claude starts only VP-0:

1. `cd /opt/CodeVinciAtlas`;
2. read `docs/MASTER_SPEC.md` fully;
3. read-only Git/GitHub/Git author audit;
4. versions OS/Docker/Compose/Python/Node/uv/pnpm/Codex/Claude;
5. auth roots only paths/permissions/status, never content;
6. extract exact `docs/vp/VP-0.md` without scope change;
7. implement locally until unavoidable official login gate;
8. show exact login actions only;
9. continue VP-0 after login;
10. no repo creation/commit/push/PR/merge without current owner approval.

---

## 48. Primary sources

OpenAI Codex:

- [Environment variables and CODEX_HOME](https://learn.chatgpt.com/docs/config-file/environment-variables.md)
- [Authentication/headless login](https://learn.chatgpt.com/docs/auth.md)
- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode.md)
- [CLI reference](https://learn.chatgpt.com/docs/developer-commands.md?surface=cli)

Anthropic Claude Code:

- [Authentication and CLAUDE_CONFIG_DIR](https://code.claude.com/docs/en/iam)
- [CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Programmatic mode](https://code.claude.com/docs/en/headless)
- [Sessions, clear and compact](https://code.claude.com/docs/en/sessions)
- [Model/effort](https://code.claude.com/docs/en/model-config)

Reuse sources are linked in section 24.

---

## 49. Owner decisions

Approved:

- Compose Core/Web + native Runner.
- Convenient official login/existing roots.
- Cookie path as experimental addition.
- STANDARD default + configurable PR merge.
- Higher autonomy per project.
- RU UI with EN switch.
- Stable docs RU/EN; commits/reports RU.
- Synthetic then File Atelier.
- External projects via Reuse Register.
- Ten stages VP-0…VP-9.

Need separate future confirmation:

- GitHub repository creation;
- LICENSE;
- first commit/push/merge;
- File Atelier increment;
- public release/tag;
- domain/TLS;
- real cookie import activation.

