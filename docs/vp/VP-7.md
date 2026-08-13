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

**call 7/7 immutable, call 8/8 — новый вызов.** Правки после call-7 меняют PR head,
поэтому следующий независимый Reviewer — **call 8/8** (не повторный 7/7). call-7
evidence сохранён в `var/artifacts/vp7/final_review/call-7/` (REVISE@`4517ebd`);
call-8 пишет в `call-8/` и не перезаписывает call-7. call-7 никогда не
переименовывается в PASS.

8. **Официальная numeric-ёмкость Claude (status-line, §3).** Числа из
   документированного status-line `rate_limits` (`five_hour/seven_day` ×
   `used_percentage`/`resets_at`) через эфемерный `--settings` statusLine → spool
   0600; предпочтительно, fallback — `rate_limit_event`. Диагностика v2.1.220:
   numeric путь закрыт onboarding-login изолированного профиля (login запрещён +
   блокируется safety-классификатором; forge `hasCompletedOnboarding` запрещён) →
   для профиля с незавершённым onboarding честный fallback; collector numeric-
   capable (реальные % при завершённом onboarding). remaining = `100 − used%`.
9. **Один активный Claude (§2).** `claude-pro-02` (истёкшая подписка) durable
   disabled: не в активном пуле/UI/ёмкости; unix-user/home/creds не удалены;
   история сохранена. `CLAUDE_POOL` хардкод → registry-driven открытие
   (`claude_pool_aliases`): attach нового профиля в VP-8 без правок кода. «2/2»
   устранено; фиктивного combined-% нет.
10. **Production Run-start роутинг (§5).** `runtime.start_builder_run` +
    `POST /runs/{id}/start`: кандидаты из durable-реестра, registry-driven
    `select_builder`, одна аренда (один writer), bounded Builder под изолированным
    профилем, provider-session/router-decision durable, safe rate-limit handoff
    (РОВНО один switch на eligible, иначе OWNER_REQUIRED, без loop), restart без
    дублей. E2E входит через production-метод (не `select_builder` напрямую).
11. **Sub2API (§4).** REFERENCE/LGPL-3.0; отвергнут недокументированный OAuth
    usage-endpoint + TLS-fingerprint; общие паттерны переосмыслены независимо; 0
    строк скопированного кода (см. REUSE_REGISTER).

Согласно owner-правилу genuine REVISE — легитимный блокер call-7; находки
исправлены. VP-7 закрывается только при genuine PASS на исправленном head,
затем merge/backup/миграция/deploy/truth-sync.

### call 8/8 — genuine REVISE (head `fad8449`)

Независимый Reviewer (codex-plus-01, read-only, 21 файл) на полном diff `fad8449`
вернул genuine **REVISE** (accounting **8/8**), merge НЕ исполнялся:

1. **Критично** — Emergency Stop не закрывал production start boundary:
   `runtime.start_builder_run`/`POST /runs/{id}/start` не проверяли
   `emergency.blocks_new_jobs()` (ранее-QUEUED Run стартовал при активном Stop), и
   Stop во время шага не прерывал результат. **Fix:** проверка `blocks_new_jobs()`
   на входе диспетча (EMERGENCY_STOP deny) + кооперативное прерывание после шага
   (`is_active()` → INTERRUPTED, аренда снята). Тесты
   `test_emergency_blocks_dispatch_of_queued_run`, `test_emergency_during_step_interrupts`.
2. **Высокий** — `autonomy.evaluate()` не проверял `starts_at`: ACTIVE grant с
   будущим стартом разрешался до начала действия. **Fix:** `_not_yet_active` +
   reason-код `GRANT_NOT_YET_ACTIVE` (fail-closed: непарсируемый starts_at → deny).
   Тесты `test_future_starts_at_denies_not_yet_active`, `test_past_starts_at_still_permits`.

Обе находки исправлены с тестами; **merge не исполнялся, PASS не фабриковался**.
Полная регрессия **402 OK**; acceptance 34/34; живая БД остаётся `0006`.
Evidence: `final_review/call-8/`.

### call 9 — genuine REVISE (head `515acb0`, codex-plus-02)

Лимит на число Reviewer-вызовов снят владельцем (последовательно до PASS).
Находки: Emergency Stop TOCTOU (окно между снимком jobs и durable-commit);
`github_deliveries` не писалась реальным merge. **Fix:** in-process `_ENGAGING`
до durable-commit + re-check после регистрации job; `merge_pull_request` пишет
authoritative delivery. Исправлено `774ed7b` с тестами. Evidence:
`final_review/call-9/`.

### call 10 — genuine REVISE (head `896cc9b`, codex-plus-01)

Находки: grant consume TOCTOU (списание не привязано к оценённой version);
недостаточная строгость `checks()`/`mergeability()`; delivery не durable до
squash; Emergency не проверялся у самой merge-boundary. **Fix:** атомарная
ре-валидация в `consume_budget`; строгие checks (только success, neutral не в
счёт) + `mergeStateStatus==CLEAN`; authoritative delivery до squash; Emergency
re-check у boundary. Исправлено `2f54e39`/`896cc9b` с тестами. Evidence:
`final_review/call-10/`.

### call 11 — genuine REVISE (head `2d13d04`, codex-plus-02) + аудит перед call-12

Четыре находки Time Machine: (1) replay создавал ветку из `base_sha`, теряя
`base..head` — не воспроизводил состояние checkpoint; (2) реальный replay не был
подключён к production API (только preview); (3) `compare()` не верифицировал
checkpoints (tampered принимался); (4) детерминированное имя ветки/dedup Run —
повторный replay конфликтовал. **Fix:** ветка из `head_sha` + verify состояния;
`replay_production` через **доверенный checkout из durable Worktree/Project**
(HTTP-endpoint не принимает произвольный путь каллера); `compare` верифицирует
оба checkpoint (fail-closed); уникальный энтропийный токен на каждый replay +
preview показывает **паттерн**, не точное имя. HTTP-интеграционный тест доказывает
10 свойств (новый Run, реальная ветка = `head_sha`, файл checkpoint, повтор →
новые ветка/Run, source не переписан, tamper → отказ, Emergency → без ветки/Run).

Перед call-12 закрыты 3 остаточных риска аудита: **A** — списание бюджета строго
на оценённой `Decision.version` (снимок), без перечитывания более новой; **B** —
барьер Emergency Stop **у каждой** необратимой forge-границы (commit/push/
create_pr/merge), а не только в начале `_consume`; **C** — явная политика
обязательных CI-контекстов Atlas (`classify_check_runs`): head зелёный только при
наличии всех required-jobs со `success` (закрывает отсутствующую/постороннюю/
pending/skipped/дублированную job). Регрессия **429 OK**; acceptance 34/34; Chrome
50/50 (39 PNG + sha256-manifest); секрет-скан ЧИСТО; живая БД остаётся `0006`.
call-7…11 immutable.

### call 12 — genuine REVISE (head `a15bd87`, codex-plus-01)

Четыре находки: (1, критично) production replay для local_git не проверял
фактический workspace-scope grant (`_project_repo`→None ⇒ `repo=None`, проверялись
лишь непустые allowlists); (2, высокий) `GET /checkpoints/compare` не маппил
`InvalidCheckpointError` в `INVALID_EVIDENCE` (tampered → 500); (3, высокий) Web
Time Machine не подключён к production replay (нет метода в `api.ts`/действия в UI);
(4, средний) `replay()` при пустом `head_sha` брал `base_sha` (baseline) вместо
fail-closed `CHECKPOINT_NO_HEAD`. **Fix:** для local_git replay энфорсит
`workspace`-scope (grant обязан перечислять мутируемый checkout); compare-endpoint
возвращает `INVALID_EVIDENCE` (409); `api.replay` + replay-действие/результат в
`timemachine.tsx` (RU/EN); `replay()` fail-closed `CHECKPOINT_NO_HEAD` при пустом
head_sha ДО создания ветки/Run. Регрессия **432 OK**; acceptance 34/34; Web i18n
736/736 + tsc + build; Chrome 50/50 (39 PNG); секрет-скан ЧИСТО; живая БД остаётся
`0006`. call-7…12 immutable.

### call 13 — genuine REVISE (head `14a2da9`, codex-plus-02)

Три находки: (1, критично) `GitHubAdapter.commit`/`push_feature` не энфорсили
workspace-scope — grant нужного project_id разрешал write из ПРОИЗВОЛЬНОГО
caller-supplied worktree; (2, критично) `GhForge.squash_merge` не передавал
`--match-head-commit` — commit между чтением head и squash был бы слит без PASS/CI
для нового SHA; (3, высокий) окно Emergency Stop между первым барьером и squash
из-за durable `record_delivery`. **Fix:** commit/push передают `workspace=worktree`
в evaluate (fail-closed `WORKSPACE_NOT_ALLOWED`); squash-merge с
`--match-head-commit <expected_head>` (атомарная current-head гарантия); повторный
барьер Emergency НЕПОСРЕДСТВЕННО перед squash. Регрессия **435 OK**; acceptance
34/34; Web i18n 736/736 + tsc + build; Chrome 50/50; секрет-скан ЧИСТО;
живая БД остаётся `0006`. call-7…13 immutable.

### call 14 — genuine PASS (head `9684e8b`, codex-plus-01), НО execution-gate деним

Независимый Reviewer (codex-plus-01, read-only, session present, 19 файлов) на полном
diff `9684e8b` вернул genuine **PASS** — reviewer PASS, quality PASS, **0 находок**,
CI GREEN (4 обязательные job), mergeability CLEAN. Однако authoritative STANDARD merge
gate (`authorize_merge_execution`) вернул `REVIEW_PACKAGE_INVALID / MISSING_EVIDENCE` →
**merge НЕ исполнен** (`merge_executed=false`), PASS не фабриковался. Evidence —
`var/artifacts/vp7/final_review/call-14/` (immutable). call 14 — подлинный PASS на
старом head; **НЕ переименовывается в REVISE**.

### Evidence-gate defect (реальный, §2) — исправлено → call 15

`authorize_merge_execution`/`evaluate_merge_authoritative` вызывали
`validate_review_package` с **пустыми фактами** (`evidence_present=[]`, `artifacts={}`):
любой evidence-backed RP всегда падал в `MISSING_EVIDENCE`, а `artifact_hashes` вообще
не сверялись на execution-boundary (tamper незаметен). Пройти можно было лишь
evidence-**пустым** RP — что скрыло бы evidence-backed финальный review. **Fix
(fail-closed):**

* durable head-bound **evidence-store** — таблица `merge_evidence` (миграция 0007),
  `reviewpkg.register_merge_evidence` (sha256 из реальных файлов), `resolve_review_facts`
  (факты из store + **пересчёта реальных файлов**, НЕ из caller-claims);
* authoritative-путь: RP обязан быть evidence-backed (пустой → deny);
  неразрешимое/отсутствующее → `MISSING_EVIDENCE`; изменённый файл → `ARTIFACT_ALTERED`;
  evidence с другого head невидимо; один список без durable-регистрации не авторизует;
* тот же evidence-backed RP/QR используется реальным `merge_pull_request`;
* harness `run_vp7_final_review.py` строит RP из **реальных** входов: точный 40-симв.
  base SHA (сверка live main == PR base == reviewed base), свежий детерминированный
  acceptance (реальные command/exit/count/timestamp), зарегистрированные реальные
  evidence-файлы; permissive `gh_checks_state` заменён на production required-context
  политику (`GhForge.checks`).

Регрессионные тесты: `TestEvidenceGate` (present→proceed, missing→MISSING_EVIDENCE,
tampered→ARTIFACT_ALTERED, чужой head→deny, список-без-store→deny, evidence-empty→deny,
stale head→deny) + `test_merge_requires_resolvable_evidence` (production merge-путь).

Это **tracked-правка** → call 14 больше НЕ авторизует исправленный head. Валидация:
регрессия **443 OK** (435 + 8), acceptance 34/34, Web i18n 736/736 + tsc + build,
Chrome 50/50 (34 скриншота), миграции empty→0007 и seeded 0006→0007→downgrade→up
(данные VP-2..6 сохранены, live БД остаётся `0006`), секрет-скан ЧИСТО, git diff
--check чист. NEXT: **call 15** (codex-plus-02, независим) по новому зелёному head;
при genuine PASS — merge/backup/миграция/deploy/truth-sync.

### call 15 — genuine REVISE (head `d788bd1`, codex-plus-02)

Независимый Reviewer (codex-plus-02, read-only, session present, 21 файл) на полном
diff `d788bd1` вернул genuine **REVISE** (2 находки HIGH), merge НЕ исполнялся:

1. **evidence-store не самодостаточен** (`reviewpkg.py`): `resolve_review_facts`
   пересчитывал файл, но игнорировал сохранённый `MergeEvidence.sha256`, а
   `validate_review_package` сверял только с `ReviewPackage.artifact_hashes` — если
   `evidence_ref` зарегистрирован, но НЕ включён в `artifact_hashes`, подмена его файла
   осталась бы валидной. **Fix:** `resolve_review_facts` помечает ссылку `present`
   только при совпадении с зарегистрированным `sha256`; новая
   `verify_evidence_for_refs(head, refs)` — fail-closed сверка КАЖДОЙ объявленной
   ссылки с store+реальным файлом (отсутствует→`MISSING_EVIDENCE`, изменён→
   `ARTIFACT_ALTERED`), вызывается в `_authoritative_rp_facts` независимо от
   `artifact_hashes`. Тест `test_tampered_ref_without_artifact_hash_denies`.
2. **replay Emergency-окно** (`timemachine.py`): Emergency Stop проверялся только в
   начале; `engage()` после проверки, но до создания Run, позволил бы материализовать
   ветку и создать новый QUEUED Run при активном Stop. **Fix:** повторный
   `blocks_new_jobs()` барьер НЕПОСРЕДСТВЕННО перед созданием Run; orphan replay-ветка
   откатывается (branch без Run — не job), source не трогается. Тест
   `test_replay_emergency_race_before_run_denies` (гонка через counter-patch).

Обе находки исправлены с регрессионными тестами; **merge не исполнялся, PASS не
фабриковался**. Валидация: регрессия **445 OK**; acceptance 34/34; Web i18n 736/736 +
tsc + build; Chrome 50/50; секрет-скан ЧИСТО; live БД остаётся `0006`. call-7…15
immutable. NEXT: **call 16** по исправленному зелёному head.

### call 16 — genuine REVISE (head `541c534`, codex-plus-02)

Независимый Reviewer (codex-plus-02, read-only, session present, 17 файлов) на полном
diff `541c534` вернул genuine **REVISE** (2 находки), merge НЕ исполнялся:

1. **(критично)** replay всё ещё имел TOCTOU с Emergency Stop: повторная проверка
   `blocks_new_jobs()` перед `_create_replay_run()` НЕ атомарна с durable-INSERT Run —
   если `engage()` снял снимок active-runs ДО появления нового Run, тот остался бы
   QUEUED при активном Stop. **Fix:** после INSERT — повторная проверка
   `blocks_new_jobs()` и rollback (Run→CANCELLED через `_cancel_replay_run`, ветка
   откатывается `_rollback_replay_branch`); при ошибке INSERT ветка тоже откатывается.
   `_ENGAGING` (ставится engage() ДО снимка) + durable active держат
   `blocks_new_jobs()` непрерывно, поэтому Stop, перекрывший INSERT, замечается с двух
   сторон. Тест `test_replay_emergency_race_after_run_creation_rolls_back`.
2. Рассинхрон durable-truth: `docs/NEXT.md` объявлял `NEXT_ACTION` как call 9/9, тогда
   как фактически пройдены call 9…16. **Fix:** `NEXT.md` приведён к фактическому
   состоянию (история call 7…16, next call по исправленному head).

Обе находки исправлены; **merge не исполнялся, PASS не фабриковался**. Валидация:
регрессия **446 OK**; acceptance 34/34; Web i18n 736/736 + tsc + build; Chrome 50/50;
секрет-скан ЧИСТО; live БД остаётся `0006`. call-7…16 immutable. NEXT: **call 17** по
исправленному зелёному head.

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
