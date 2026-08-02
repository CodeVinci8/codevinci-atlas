# VP-2 — Project Workspace (исполнимый спек)

**Статус:** реализован; закрывается через приёмку и PR (не merged, пока это не так).
**Источник истины:** [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §35.
**Ветка:** `atlas/vp-2-project-workspace`.

## Result

Синтетический репозиторий подключается к Atlas; видны его baseline, dirty-state,
инструкции, источник и команды; безопасно создаётся изолированный worktree.

## Scope (реализовано)

- **Источники проекта:** локальный Git-путь, GitHub-репозиторий (санированные
  метаданные без credentials), архив как read-only intake, пустой проект;
  персистентность источника и метаданных через миграции.
- **Read-only Git baseline:** канонический путь, ветка, HEAD, санированные
  remotes, porcelain dirty, счётчики tracked/untracked, вложенные инструкции с
  precedence, пакетные менеджеры, baseline-команды (без исполнения),
  redacted-статус секрет-скана, метка времени и content-hash.
- **Non-destructive dirty-потоки:** никаких `reset --hard`/`clean`/тихого
  stash/checkout поверх; dirty-состояние сохраняется и отображается.
- **Инструкции и команды:** путь, scope, precedence, факт чтения, bounded-summary;
  команды явные, ограниченные, персистентные, видимые и **не исполняются**.
- **Безопасные worktree и writer-аренды:** ветка `atlas/vp-<n>-<slug>`,
  канонический путь в allowlist, без перезаписи; один Builder-writer, второй
  отклонён детерминированно; reconcile только после проверки процесса и Git.
- **Безопасность:** канонические пути + allowlist workspace-корней; блокировка
  traversal/symlink; враждебный intake архивов (абсолютные/`..`/Windows/symlink/
  hardlink/device/размер/число/дубликаты); ни один файл вне intake.
- **Project Overview API и Web UI (Ember, RU/EN):** источник, git baseline,
  dirty-предупреждение, инструкции, пакетные менеджеры, команды, worktree/аренда,
  точный next action; состояния loading/empty/offline/stale/error.
- **Audit и evidence**; отключение проекта не удаляет репозиторий/dirty/worktree/архив.

## Out (не входит в VP-2)

Product Map, Brief approval, Work Orders, маршрутизация агентов, полные Runs,
полные Profiles, Quality Firewall, GitHub merge automation, полная консоль VP-8.

## Acceptance boundary (§35)

Прогон `scripts/run_vp2_acceptance.py` доказывает 20 пунктов против реального
стека и синтетических git-фикстур. Итог — `var/artifacts/vp2/acceptance_matrix.json`.

| # | Проверка | Evidence |
|---|----------|----------|
| 1 | Чистый синтетический git подключается | `c1_c2_clean_connect.json` |
| 2 | Источник/ветка/HEAD/remotes/инструкции/пакетные менеджеры/команды/baseline персистятся и видимы | `c1_c2_clean_connect.json` |
| 3 | Грязный репозиторий подключается без модификации | `c3_c7_dirty_worktree.json` |
| 4 | Dirty tracked+untracked неизменны байт-в-байт | `c3_c7_dirty_worktree.json` |
| 5 | Деструктивные git-команды не используются | `c3_c7_dirty_worktree.json` |
| 6 | Разрешённая ветка и изолированный worktree созданы безопасно | `c3_c7_dirty_worktree.json` |
| 7 | Оригинальный репозиторий не изменён | `c3_c7_dirty_worktree.json` |
| 8 | Второй writer детерминированно отклонён | `c8_second_writer.json` |
| 9 | Traversal в архиве блокируется | `c9_11_archive_security.json` |
| 10 | Symlink и canonical-path escape блокируются | `c9_11_archive_security.json` |
| 11 | Ни один вредоносный файл не создан вне intake | `c9_11_archive_security.json` |
| 12 | Baseline переживает рестарт Core и запрашивается | `c12_restart_durability.json` |
| 13 | Disconnect не удаляет репозиторий/dirty/архив/worktree | `c13_disconnect_no_delete.json` |
| 14 | RU/EN переключение и паритет каталогов | `c14_i18n.json` |
| 15 | Overview: правдивые clean/dirty/empty/stale/error/loading | `c15_states.json` |
| 16 | Core/Web/Runner non-root и healthy | `c16_nonroot_health.json` |
| 17 | Нет регрессий VP-1 (health/audit/UDS/backup/web) | `c17_vp1_regression.json` |
| 18 | Полный секрет-скан чист (код/история/БД/фикстуры) | `c18_secret_scan.json` |
| 19 | Ровно один активный VP-гейт (VP-2) | `c19_vp_gate.json` |
| 20 | 127.0.0.1:3210 отдаёт VP-2 Project Workspace UI | `c20_web_ui.json` |

## Как запустить

```bash
# стек VP-2 (пересобрать Core с git + Web с новым UI)
docker compose up -d --build
# приёмка VP-2 (20/20, root, стек поднят)
python3 scripts/run_vp2_acceptance.py
```

## Архитектура (кратко)

- Модули Core: `wspaths` (пути/allowlist/traversal), `gitbaseline` (read-only),
  `archives` (intake), `worktrees` (git worktree add), `wsleases` (writer-аренда),
  `workspace` (сервис/overview), `api_projects` (`/api/v1/projects`).
- Персистентность: ORM `Project/GitBaseline/Worktree/WorktreeLease` + Alembic
  `0002_project_workspace` (таблицы создаёт только Alembic).
- Web: Ember developer-cockpit (sidebar, Projects, Project Overview), RU/EN.
