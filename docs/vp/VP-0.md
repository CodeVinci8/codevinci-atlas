# VP-0 — Profile Pool & Live Handoff Proof (исполнимый спек)

**Статус:** **ЗАВЕРШЁН — 11/11 PASS** (включая реальные A→B для Codex и Claude).
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §33, §47
**Ветка:** `atlas/vp-0-profile-handoff-proof`

> Извлечено из Master Spec без изменения scope. VP-0 доказывает главный риск:
> изоляция профилей, продолжение после смены профиля/сессии, классификация
> rate limit, один writer.

## Цель / Result

CLI-диагностика (`scripts/atlas-doctor`) + минимальный web-status показывают
profiles/health и реальные handoff **без идентичностей**.

## Модель выполнения и изоляции

- Единый runtime-layout: **`/var/lib/codevinci-atlas`** (тесты используют
  временный `ATLAS_DATA_DIR`).
- У каждого профиля — **отдельная Unix-идентичность** (`atlas-cx01/02`,
  `atlas-cl01/02`) и **отдельный исполняемый файл CLI**
  (`<root>/.local/bin/{codex,claude}`); root профиля `0700` во владении этой
  идентичности.
- CLI запускается под идентичностью профиля (`runuser -u … env -i …`), поэтому
  процесс профиля A **физически не видит** credentials B, а сервисный `atlas`
  не видит ни один профиль. Core не читает содержимое credentials.
- `scripts/atlas-runtime-setup.sh` создаёт идентичности и каталоги.

## Реальные CLI-контракты (сверено вживую)

- Codex 0.146.0: `codex exec --json --skip-git-repo-check -s read-only -C <cwd>`;
  события `thread.started`(→`thread_id`), `item.completed`(→`item.text`),
  `turn.completed`; `codex login status`; `codex login --device-auth`.
- Claude 2.1.220: `claude -p --output-format stream-json --verbose`; события
  `system`/`assistant`/`result`(→`result`,`session_id`); `claude auth status --json`.

## Acceptance criteria — ЗАВЕРШЕНО (11/11 PASS)

Статусы: `PASS` (реально доказано). Механические слои помечены `PASS_MECHANISM`
и НЕ считаются финальным PASS сами по себе — там, где требуется реальность
(крит. 3–5), стоит реальный `PASS`.

| # | Критерий | Статус | Доказательство / артефакт |
|---|---|---|---|
| 1 | 2 Codex + 2 Claude root изолированы (свои идентичности) | **PASS** | `c1_permissions.json`, `c1_identities.json` |
| 2 | Cross-read заблокирован (реальные идентичности) | **PASS** | `c2_isolation_matrix.json` (кросс/сервис DENIED) |
| 3 | Минимальный run A структурный (реально) | **PASS** | `c3-6_real.json` (partial верен) |
| 4 | B продолжает из верифиц. HandoffPackage (реально) | **PASS** | `c3-6_real.json` (final=partial+addend) |
| 5 | Доказано для обоих провайдеров (реально) | **PASS** | `c3-6_real.json` (codex + claude ok) |
| 6 | Simulated rate limit без второго writer | **PASS** | `c3-6_mechanism.json` (max_writers=1) |
| 7 | Core restart сохраняет состояние | **PASS** | `c7_restart.json` |
| 8 | Runner interruption → reconcile → продолжение до успеха | **PASS** | `c8_runner_recovery.json` |
| 9 | Нет credentials в дереве/истории/БД/логах/artifacts | **PASS** | `c9_secret_scan.json` |
| 10 | UNKNOWN capacity честно | **PASS** | `c10_capacity.json` |
| 11 | Воспроизводимый отчёт/evidence | **PASS** | `acceptance_matrix.json` |

**Итог: 11/11 PASS. VP-0 ЗАВЕРШЁН.**

## Реальный A→B (как доказано)

Задача независимо проверяема: A вычисляет `partial = n1 + n2` и возвращает
структурный JSON + `nonce`; Atlas сохраняет checkpoint+HandoffPackage и
верифицирует их против persisted-БД (хеши); B (ДРУГАЯ идентичность и аккаунт,
ОТДЕЛЬНАЯ новая CLI-сессия) получает только ограниченный JobPackage+Handoff и
вычисляет `final = partial + addend`, эхом возвращая `nonce`. Проверка: `final`
арифметически верен, `nonce` совпал, сессия B ≠ сессии A (общей нативной
сессии/credentials нет). Независимо для Codex и Claude. Запуск —
`scripts/manual_real_probe.py`; реальный лимит не провоцируется.

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
