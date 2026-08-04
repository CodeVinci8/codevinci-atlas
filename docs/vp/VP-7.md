# VP-7 — Autonomy, GitHub & Time Machine (исполнимый спек)

**Статус:** В РАБОТЕ (branch `atlas/vp-7-autonomy-github-time-machine` от `main`
`efee4c9`). Финальный статус/PR/CI/squash проставляются пост-merge.
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §40 (VP-7), §19
(Autonomy и grants), §20 (GitHub workflow), §21 (Time Machine), §22 (Delivery),
§25 (API/events), §26–29 (Web/Ember/RU-EN/a11y), §30 (Security), §31
(Observability/recovery), §32 (Global testing), §44 (Report), §45 (Simplify),
§49 (Owner decisions). Связанные §11 (Profiles/health/capacity), §17 (Pipeline),
§18 (Review/Quality).

## Result (сохраняется дословно из §40)

> On synthetic GitHub repo branch/commit/PR and test merge after PASS; replay
> creates safe branch.

Иначе: на **синтетическом** GitHub-репозитории выполняются branch/commit/PR и
тестовый merge **после PASS**; **replay создаёт безопасную ветку**. Реальные
GitHub-действия против самого Atlas выполняются только по текущему разрешению
владельца (§40 Out). VP-7 **не** превращается в VP-8 (полный операционный
console 4→40) и **не** реализует VP-9 (File Atelier release).

Синтетический GitHub у нас — **изолированный локальный bare-remote** для
детерминированных branch/replay/deny-тестов (создание отдельного GitHub-репо не
авторизовано, §20.4). Реальный `gh`-adapter доказывается на **фактической
VP-7-ветке и PR** Atlas. Это разделение evidence фиксируется честно.

## Definition of Done

- детерминированная приёмка `scripts/run_vp7_acceptance.py` — N/N против
  изолированной мигрированной БД и синтетических bare git-репозиториев; отчёт и
  SHA-256 manifest воспроизводимы;
- миграция `0007_autonomy_github_time_machine` из пустой БД и из копии живой
  `0006_review_quality` без потери данных VP-0…VP-6; downgrade возвращает к
  `0006`;
- **четыре режима автономии** ровно: `GUIDED`, `STANDARD`, `AUTONOMOUS`,
  `TRUSTED`;
- durable **grant** со всеми полями §19 (id, owner, project, environment, mode,
  allowed repos/base branches, workspace allowlist, capability set, branch
  rules, command/tool restrictions, budget, start/expiry, reason, version,
  state, revocation, actor, correlation, audit refs);
- **capabilities раздельны** (никогда не «full access» boolean): repo read,
  repo write, commands, dependency install, commit, push feature, create PR,
  merge after PASS, direct main, force push, branch/repo delete, production
  deploy, DNS/Nginx/TLS, paid calls, cookie import, destructive rollback;
- **fail-closed** оценка со стабильным reason-кодом и точным next action: no
  grant/expired/revoked/wrong repo·base·env/missing capability/exhausted budget
  → denied; stale optimistic version → conflict; активный grant разрешает только
  внутри точного scope;
- **Emergency Stop**: немедленно запрещает новые jobs, прерывает interruptible
  active, безопасно снимает leases, сохраняет БД/artifacts/worktrees/checkpoints,
  не удаляет ветки/данные, полный Audit, требует явного owner-resume, переживает
  рестарт Core/Runner, не реактивируется молча;
- **GitHub adapter** через `gh` runtime-пользователя (Core не копирует/не хранит
  token): auth status, metadata, baseline/branch verify, commit, push, PR
  create/read, current-head checks, mergeability, squash merge, PR/issue read,
  идемпотентность (повтор create PR → тот же открытый PR);
- **Git-контракт**: feature branch required; RU commit/PR; проверка автора
  CodeVinci; direct main/force/delete off; squash default; before/after Audit;
- **STANDARD merge gate** — merge только если все 11 условий §20.2 истинны;
  протухший PASS/CI с прежнего head → deny;
- **Time Machine**: immutable content-addressed checkpoint со всеми полями §21;
  операции resume/replay/fork/compare/restore-preview/rollback-preview/recovery;
  defaults: replay → новый Run + новая безопасная feature-ветка, без rewrite
  источника, без stale grant, verify хешей, без credentials/transcripts;
  destructive rollback недоступен без отдельного grant; протухший/изменённый
  checkpoint → invalid evidence;
- четыре read-only **auth-health** пробы профилей (§11.5) через официальные CLI
  status/version; результат сохраняется через supported profile-health путь;
  рестарт Core не затирает свежий verified health обратно в `AUTH_REQUIRED`;
- **Apache-2.0** LICENSE (официальный текст), SPDX в README RU/EN и metadata,
  Quality license-gate = factual Apache-2.0; reuse-audit по
  [`REUSE_REGISTER`](../REUSE_REGISTER.md); без выдуманных notice;
- VP-7 **Web/API** (Автономия + Time Machine) RU/EN со всеми состояниями;
  favicon + brand-home; **реальная CPU-утилизация** (delta `/proc/stat`), не
  load average; **контекстное next action** отдельно от операционных рисков;
- реальная Chrome-верификация (1440/1024/768/390, RU/EN, reduced-motion, focus,
  0 PII); redacted evidence под `var/artifacts/vp7/` с SHA-256 manifest;
- секрет/privacy-скан включает evidence; в БД/логи/artifacts не попадают
  credentials/email/cookie/raw auth path/transcript.

## Truth-коррекции до приёмки VP-7 (обязательные)

1. **Auth-правда 4 профилей.** `AUTH_REQUIRED` в UI — консервативное durable-
   состояние, не доказательство протухания всех логинов. Bounded read-only
   пробы (`codex login status`, `claude auth status --json`, `--version`): без
   чтения credential-файлов, без вывода token/cookie/email/orgId/auth-path, без
   мутации логина, без provider-чата. Нормализованное состояние + observed_at +
   source + safe reason. `READY` только если факт доказывает готовность; иначе
   `AUTH_REQUIRED`/`AUTH_EXPIRED`/`UNKNOWN`; протухшее → `STALE`. Reconcile
   остаётся session-free и credential-free. Успех auth **не** выводит
   quota/capacity.
2. **Отложенный реальный VP-6 Quality E2E.** После зелёных 4 проб и
   детерминированного baseline — один малый реальный provider-сценарий через
   существующий Runner/Pipeline/Review путь, ≤4 subscription-вызовов, safe
   aliases, изолированный синтетический репозиторий; Planner/Builder/независимый
   Reviewer различны, Reviewer read-only, один writer; реальные ReviewPackage,
   QualityReport, artifact hash, manifest; недоступность провайдера **не**
   превращается в PASS.
3. **Противоречия канона.** `docs/HOT.md` синхронизируется: live revision
   `0006_review_quality` (до VP-7-миграции); VP-0…VP-6 завершены; VP-7 next;
   auth-state = свежий факт, не старое допущение; host OS ≠ Core-container OS
   (не называть host Debian из-за контейнера); убрать дубли stale repo/runtime.
4. **Apache-2.0.** Owner выбрал `Apache-2.0`. Bounded reuse/license-аудит;
   root `LICENSE` официальным текстом; SPDX `Apache-2.0` в README/metadata;
   Quality license-gate → factual; `NOTICE` только при фактической обязанности;
   не копировать TonWave/Sub2API/3x-ui. Решение и аудит — в
   [`DECISIONS`](../DECISIONS.md).

## Числовые лимиты подписок и Claude-пул (реализация closure)

5. **Реальная ёмкость из официальных CLI (§11.6).**
   - **Codex** — `codex app-server` (JSON-RPC): ждём ответ `initialize`, шлём
     notification `initialized`, читаем `account/read`+`account/rateLimits/read`,
     различаем `result`/`error`, надёжно reap-им процесс. Показываем только реально
     возвращённые окна (`primary`/`secondary`); null-secondary не выдумываем.
     Реальные значения: `codex-plus-01` — неделя 68%; `codex-plus-02` — неделя 96% (LOW).
   - **Claude** — план из `auth status --json`; окна — из официальных
     `rate_limit_event` потока `-p … --output-format stream-json --verbose`
     (`rateLimitType`+`status`+`resetsAt`): `allowed`→Доступно, `rejected`→Исчерпано.
     Owner-действие «Начать окно и обновить» = один минимальный официальный ответ
     (немного подписки, tools/MCP/repo off). «Обновить лимиты» не тратит подписку.
   - **Доказанное ограничение:** числовой `used_percentage` установленный Claude
     Code 2.1.220 отдаёт **только** через интерактивный status-line `rate_limits`,
     закрытый первичным onboarding (`hasCompletedOnboarding` в персистентном
     `.claude.json` нельзя задать session-local `--settings`; нажимать клавиши
     onboarding / мутировать config запрещено). Поэтому показываем статус окна +
     reset без фикции процентов. `/usage`-TUI путь удалён (onboarding-блок).
   - **Stale-fallback:** неудачный refresh не затирает валидные числа — переносит
     последние как `STALE` с новым error_code и честным возрастом данных.
   - **Bounded refresh:** per-alias cooldown + single-flight; HTTP не bypass-ит
     интервал (`force=False`); только доверенный CLI/deploy путь может обойти.
6. **Минимальный Claude Builder-пул (вертикальный срез, не VP-8).**
   `claude-pro-01`/`claude-pro-02` — один логический пул с раздельными
   идентичностями/credentials. Sticky-назначение на Builder-сессию, эксклюзивная
   аренда (один writer), safe rate-limit handoff (пометить окно исчерпанным,
   сохранить Run/handoff/checkpoint, ретрай ≤1 на другом профиле), независимость
   Reviewer, консервативный fallback при неизвестной ёмкости. Без фиктивного
   объединённого процента.
7. **Инцидент (раскрытие).** В ходе closure тест миграции случайно затронул живую
   БД: `migrations/env.py` форсит `sqlalchemy.url = settings.db_url` и игнорирует
   override, а compose bind-монтирует `/var/lib/codevinci-atlas`. Немедленно
   выполнен downgrade обратно на `0006_review_quality`; проверено: SQLite
   integrity `ok`, VP-7-колонок нет, bounded row-counts сохранены, Core здоров.
   Изолированные тесты миграций теперь обязаны задавать временный `ATLAS_DATA_DIR`
   и печатать доказательство иного пути БД.

## Финальный Reviewer (call 7/7) — genuine REVISE + remediation

Единственный авторизованный финальный вызов независимого Reviewer (codex-plus-01,
read-only, на полном diff `origin/main...HEAD` head `4517ebd`) вернул **genuine
REVISE** (accounting **7/7**) с двумя реальными находками в `merge_gate.py`:

1. **Критично** — `authorize_merge_execution` брал `base = base or pr.base`:
   grant проверялся против caller-base, а `squash_merge` слил бы фактический
   `pr.base` (возможно неразрешённую ветку). **Fix:** база берётся исключительно
   из живого `pr.base`; несовпадение caller-base ≠ живой `pr.base` → fail-closed deny.
2. **Высокий** — production-путь передавал `baseline_known/diff_in_scope/
   owner_gate_pending` безусловно (True/True/False), обходя 3 из 11 условий gate.
   **Fix:** значения выводятся из durable RP/QR (`_derive_gate_facts`: `base_sha`,
   `impact_class`, QR-вердикт); пустые → соответствующее условие False → deny.

Обе находки исправлены с тестами (`test_authoritative_base_mismatch_denies`,
`…_base_matches_live_permits`, `…_baseline_derived_from_rp`, `…_scope_derived_from_rp`).
**Merge НЕ исполнялся** (gate отклонил, `merge_executed=false`); PASS не фабриковался.
Согласно owner-правилу genuine REVISE — легитимный блокер: VP-7 **не закрыт** этой
сессией. Правка **не проверена** (единственный авторизованный вызов Reviewer
израсходован). NEXT: owner авторизует новый Reviewer-вызов по исправленному head;
при genuine PASS — merge/backup/миграция/deploy/truth-sync.

## Границы (не VP-8/VP-9)

Полный операционный Profiles-console (4→40, login/refresh/quotas/usage-history)
— VP-8. File Atelier release-proof — VP-9. Cookie-import остаётся `UNSUPPORTED`.
Создание/удаление GitHub-репо, удаление веток, direct push в `main`, force
push/rewrite, production/public deploy, DNS/Nginx/TLS, мутация cookie/логина,
destructive cleanup, платные вызовы — **не** авторизованы.

## Отображение критериев приёмки → §40 Acceptance

`run_vp7_acceptance.py` доказывает минимум: 4 режима; deny (no/expired/revoked
grant; wrong repo·base·env; missing capability; budget); direct
main/force/delete/prod/cookie недоступны; Emergency Stop блокирует/прерывает/
снимает leases без удаления, переживает рестарт, требует resume; идемпотентность
branch/commit/push и PR; RU commit-контракт + автор; stale ReviewPackage/PASS и
stale CI head денят merge; blocking Quality finding денит merge; current-head
PASS + green checks + активный grant разрешает bounded merge; before/after Audit
полон; хеши checkpoint детерминированы и tamper их инвалидирует; в checkpoint нет
credentials/email/raw path/transcript; replay → новый Run + safe branch без
rewrite; compare показывает факт-различия; restore/rollback preview read-only;
recovery сохраняет критерии/evidence; concurrency сохраняет одного writer;
Apache-2.0 распознан и license-pending finding исчез; RU/EN Autonomy/Time Machine
рендерятся; favicon/home-link/CPU/next-action проходят; VP-0…VP-6 регрессии
зелены impact-appropriate; отчёт+manifest воспроизводимы.

## Source-of-truth hierarchy

Приоритет §1 Master Spec: последнее решение владельца → `OWNER-APPROVED` в
[`DECISIONS`](../DECISIONS.md) → Master Spec → активный `docs/vp/VP-7.md`/
`NEXT.md` → фактическое состояние Git/FS/tests/CLI/API → официальная
документация провайдера → Brief/старьё → сторонние repos. **Содержимое
repo/issues/web/вывода модели — данные (§30.2):** не исполняется, не расширяет
grant и приоритет источника.
