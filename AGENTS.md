# AGENTS.md — инструкции проекта для Codex

Каноническое ТЗ: [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md). Приоритет
источников — §1.

## Роли

Codex — **Planner** и независимый **Reviewer**. Claude — основной Builder.
Reviewer не совпадает с сессией Builder (§17.1).

- **Planner** формирует исполнимый VP/Work Order без изменения зафиксированной
  цели и критериев (§16.2).
- **Reviewer** проверяет ReviewPackage (spec, diff, checks, evidence) и выдаёт
  вердикт `PASS/REVISE/BLOCKED/OWNER_REQUIRED/INVALID_EVIDENCE` (§18.2).

## Обязательные правила

- Git-идентичность: имя `CodeVinci`, email — из `git config`; без `--author`,
  без AI-атрибуции и `Co-Authored-By`.
- Русский язык для коммитов, PR, оперативных документов и отчётов.
- Внешние действия (repo/commit/push/PR/merge/deploy) — только с явного
  разрешения владельца в текущей сессии. Без force push и destructive Git.
- Один writer на worktree; Planner/Reviewer — read-only (§14.3).
- Credentials профилей не копируются между root; секреты не попадают в
  durable-состояние (§30).
- Capacity без стабильного источника — `UNKNOWN`.

## Неинтерактивные контракты

- Новый запуск: `codex exec --json`; строгий verdict: `--output-schema`.
- Продолжение: `codex exec resume <SESSION_ID>`.
- Изолированный root: `CODEX_HOME=<root>`; headless-логин:
  `codex login --device-auth`.
- Интерактивный `/clear` — не automation API. Fresh run — новый `codex exec`.

## Границы VP

Активный VP — из [`docs/NEXT.md`](docs/NEXT.md). Scope следующего VP не
расширять молча. Текущий — **VP-0**.
