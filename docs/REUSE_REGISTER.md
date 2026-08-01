# Reuse Register — решения по внешним проектам

**Статус:** обновлено в рамках VP-0.
**Основание решений (evidence):**
- правило владения credentials — один auth owner на профиль (Master Spec §11.1);
- VP-0 реализовал изоляцию/lease/handoff/Runner **нативно**, без копирования
  чужого кода (см. `apps/`, `tests/`, `scripts/run_acceptance.py`);
- лицензии и польза зафиксированы в Master Spec §24;
- любое **ADOPT** кода требует pinned commit/tag, license/NOTICE, security-обзора,
  диаграммы владения credentials, границы адаптера и пути удаления (§24).

## Таксономия

- **ADOPT** — код включается напрямую (с pinned commit и всеми условиями §24).
- **WRAP** — используется как отдельный sidecar/зависимость за адаптером.
- **REFERENCE** — берём идеи/поведение, код не копируем.
- **REJECT** — не используем.
- **SPIKE** — требуется предметная проверка перед WRAP; решение отложено до VP.

## Решения VP-0

| Проект | Лицензия | Решение VP-0 | Обоснование (evidence) | Граница |
|---|---|---|---|---|
| [Sub2API](https://github.com/Wei-Shaw/sub2api) | LGPL-3.0 + no-commercial notice | **REFERENCE** | LGPL + пометка о некоммерческом использовании несовместимы с копированием в open-source ядро; полезны идеи account-states/pool-UI/scheduler | Только UI/поведение; код ядра не копируется. Пул/состояния реализованы нативно (`atlas_core.profiles`). |
| [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | MIT | **SPIKE → возможный WRAP (позже)** | OAuth Codex/Claude, multi-account; но нарушает правило «один auth owner на профиль», если станет вторым владельцем credentials | Только как опциональный sidecar с собственным auth-root; никогда не общий credential owner. Для VP-0 не требуется. |
| [codex-multi-auth](https://github.com/ndycode/codex-multi-auth) | MIT | **SPIKE (позже)** | Профили/health/rotation Codex; требует проверки актуального CLI-контракта и владения | Проверить перед VP-5. Изоляция уже доказана нативно (`test_profiles_isolation`). |
| [CCS](https://github.com/kaitranntt/ccs) | MIT | **REFERENCE** | Изолированные профили/switching/dashboard — концептуально близко | Не копировать рантайм целиком; конвенция root у нас своя (`CODEX_HOME`/`CLAUDE_CONFIG_DIR`). |
| [claude-code-router](https://github.com/musistudio/claude-code-router) | MIT | **REFERENCE** | Идеи routing/failover/observability | Не обязательный шлюз; router у нас нативный (§17.3). |
| [ccusage](https://github.com/ccusage/ccusage) | MIT | **SPIKE/WRAP (позже)** | Локальные отчёты usage Claude/Codex | Usage ≠ точный остаток лимита. Пока capacity=UNKNOWN честно (§11.6). |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | MIT | **REFERENCE** | Spec-driven workflow | Идеи, не полный генератор. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | MIT | **REFERENCE** | Паттерны runtime/events/self-host | Слишком тяжёл как зависимость; UDS-Runner реализован минимально нативно. |

## Итог VP-0

Кода из внешних проектов **не адоптировано**. Изоляция профилей, writer-lease,
checkpoint/handoff, UDS-Runner и классификация ошибок реализованы нативно и
доказаны приёмкой. Все кандидаты остаются REFERENCE или SPIKE до отдельного
adoption-гейта с полными условиями §24.

## Что нужно до любого ADOPT (чек-лист §24)

- [ ] pinned commit/tag;
- [ ] совместимость лицензии + `NOTICE`;
- [ ] security-обзор;
- [ ] диаграмма владения credentials (нет второго owner);
- [ ] граница адаптера и путь удаления;
- [ ] запись решения в `docs/DECISIONS.md`.
