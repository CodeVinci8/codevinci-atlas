# ADAPTERS — контракты адаптеров агентов

Master Spec §12. Адаптер переводит общий контракт Atlas в команды CLI.

## Протокол (`atlas_core.adapters.base.AgentAdapter`)

`discover_capabilities`, `auth_status`, `build_start_argv`,
`build_resume_argv`, `start`, `resume`, `capacity`.

## Codex (`RealCodexAdapter`)

- Новый запуск: `codex exec --json <prompt>`.
- Строгий verdict: `--output-schema <file>`.
- Продолжение: `codex exec resume <SESSION_ID> --json`.
- Изолированный root: `CODEX_HOME=<root>`.
- Headless-логин: `codex login --device-auth`.
- Интерактивный `/clear` — не automation API; fresh run = новый `codex exec`.

## Claude (`RealClaudeAdapter`)

- Новый запуск: `claude -p --output-format stream-json`.
- Продолжение: `claude -p --resume <SESSION_ID>`.
- Изолированный root: `CLAUDE_CONFIG_DIR=<root>`.
- Модель: `--model`; статус: `claude auth status`.
- Обычный Builder не использует `--bare` (нужны project `CLAUDE.md`/settings).
  `--bare` — для изолированной диагностики.

## Session capabilities (§12.3)

`NEW_SESSION` (required), `RESUME_BY_ID` (required),
`FRESH_WITH_HANDOFF` (required), `COMPACT` (optional),
`CLEAR_INTERACTIVE` (operator-only), `FORK_NATIVE` (optional).

## Fake-адаптеры

`FakeCodexAdapter`/`FakeClaudeAdapter` детерминированно моделируют жизненный
цикл и инъекцию сбоев (`FaultInjection`: auth, policy, network, timeout,
rate_limit, interrupt, invalid_output). Задача проверяема: список `work_items`
обрабатывается по одному; `structured_output.processed_by` показывает вклад
каждого профиля — это доказывает реальность handoff.

## Ошибки (§12.4)

Каждая ошибка: код таксономии, redacted-evidence, `retryable`, `next_action`.
Классификатор — `atlas_core.errors.classify`.

## Capacity

`capacity()` возвращает `UNKNOWN`, пока нет стабильного официального источника
остатка лимита (§11.6). Никаких вычисленных фикций.

## Cookie-адаптер (только граница, §11.4)

Экспериментальный, off by default, не release blocker. Raw cookies не проходят
через Core DB/logs/artifacts; temp-файл уничтожается; при несовместимости —
`UNSUPPORTED`. Извлечение/импорт cookie **не входит** в VP-0 — определена
только граница адаптера и требования безопасности.
