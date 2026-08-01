# Нормализованные события Runner → Core

Master Spec §25.1, §31. События — newline-delimited JSON. Секреты redacted.

## Типы (VP-0)

| type | payload | назначение |
|---|---|---|
| `accepted` | `request_id` | запрос принят валидатором |
| `run.started` | `request_id`, `pid` | процесс запущен в отдельной группе |
| `run.output` | `request_id`, `line` (redacted) | строка вывода |
| `run.heartbeat` | `request_id`, `at` | признак живости |
| `run.finished` | `exit_code`, `state`, `output_hash`, `truncated` | завершение |
| `interrupt.ack` | `request_id`, `found` | подтверждение прерывания |
| `error` | `code`, `evidence` (redacted) | ошибка/отказ политики |

`state` ∈ `SUCCEEDED | FAILED | TIMEOUT | INTERRUPTED`. `output_hash` —
`sha256:<hex>` по redacted-выводу.

Пример события Core-уровня (§25.1) — см. `RunEvent` в
`apps/core/atlas_core/contracts.py`.
