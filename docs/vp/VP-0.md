# VP-0 — Profile Pool & Live Handoff Proof (исполнимый спек)

**Статус:** механизм доказан; **VP-0 НЕ завершён** — реальные A→B (крит. 3–5)
за owner-гейтом логина.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §33, §47
**Ветка (предлагается):** `atlas/vp-0-profile-handoff-proof`

> Извлечено из Master Spec без изменения scope. VP-0 доказывает главный риск:
> изоляция профилей, продолжение после смены профиля/сессии, классификация
> rate limit, один writer.

## Цель / Result

CLI-диагностика (`scripts/atlas-doctor`) + минимальный web-status показывают
profiles/health и реальные handoff **без идентичностей**.

## Модель выполнения и изоляции (уточнено)

- Единый runtime-layout: **`/var/lib/codevinci-atlas`** (расхождение с
  repo-local `./var` устранено; тесты используют временный `ATLAS_DATA_DIR`).
- У каждого профиля — **отдельная Unix-идентичность** (`atlas-cx01/02`,
  `atlas-cl01/02`); root профиля `0700` во владении этой идентичности.
- Runner дропает привилегии в идентичность профиля перед запуском CLI, поэтому
  процесс профиля A **физически не видит** credentials B, а сервисный `atlas`
  не видит ни один профиль. Core не читает содержимое credentials.
- `scripts/atlas-runtime-setup.sh` создаёт идентичности и каталоги.

## Acceptance criteria (честный учёт)

Статусы: `PASS` (реально доказано), `PASS_MECHANISM` (только механизм),
`GATE_REAL` (реальное подтверждение за owner-логином).

| # | Критерий | Статус | Доказательство / артефакт |
|---|---|---|---|
| 1 | 2 Codex + 2 Claude root изолированы (свои идентичности) | **PASS** | `c1_permissions.json`, `c1_identities.json` |
| 2 | Cross-read заблокирован (реальные идентичности) | **PASS** | `c2_isolation_matrix.json` (кросс/сервис DENIED) |
| 3 | Минимальный run A структурный | **GATE_REAL** (mech PASS_MECHANISM) | `c3-6_*.json` |
| 4 | B продолжает из HandoffPackage | **GATE_REAL** (mech PASS_MECHANISM) | `c3-6_*.json` |
| 5 | Доказано для обоих провайдеров | **GATE_REAL** (mech PASS_MECHANISM) | `c3-6_*.json` |
| 6 | Simulated rate limit без второго writer | **PASS** | `c3-6_mechanism.json` (max_writers=1) |
| 7 | Core restart сохраняет состояние | **PASS** | `c7_restart.json` |
| 8 | Runner interruption → reconcile → продолжение до успеха | **PASS** | `c8_runner_recovery.json` |
| 9 | Нет credentials в дереве/истории/БД/логах/artifacts | **PASS** | `c9_secret_scan.json` |
| 10 | UNKNOWN capacity честно | **PASS** | `c10_capacity.json` |
| 11 | Воспроизводимый отчёт/evidence | **PASS** | `acceptance_matrix.json` |

**Итог: 8/11 PASS (реально), крит. 3–5 — GATE_REAL. VP-0 не завершён.**

## Реальная приёмка (за owner-гейтом)

Владелец выполняет `scripts/login-gate.sh` (логин 2 Codex + 2 Claude под их
идентичностями, по одному профилю за раз, разные аккаунты). Затем
`scripts/manual_real_probe.py` и `scripts/run_acceptance.py` продолжают ТОТ ЖЕ
VP-0: минимальный run A, B продолжает из верифицированного HandoffPackage
(отдельная реальная сессия/аккаунт), resume, — независимо для Codex и Claude.
Реальный лимит намеренно не исчерпывается.

## Stop

Если изоляция/handoff проваливаются — VP-1 не начинается (Master Spec §46).

## Как запустить

```bash
# runtime-идентичности и каталоги (root, идемпотентно)
sudo bash scripts/atlas-runtime-setup.sh
PYTHONPATH=apps/core python3 scripts/profile-init.py

# полная приёмка (механизм + реальные проверки, что доступны без логина)
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py

# юнит-приёмка
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'

# полный секрет-скан
PYTHONPATH=apps/core python3 scripts/secret_scan.py

# owner-гейт логина, затем реальные probe
scripts/login-gate.sh
PYTHONPATH=apps/core:apps/runner python3 scripts/manual_real_probe.py
```
