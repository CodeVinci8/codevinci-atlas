# PROJECT WORKSPACE — подключение проектов (VP-2)

🇬🇧 English: [`en/PROJECT_WORKSPACE.md`](en/PROJECT_WORKSPACE.md).
Источник истины — [`MASTER_SPEC.md`](MASTER_SPEC.md) §35; спек — [`vp/VP-2.md`](vp/VP-2.md).

## Что делает

Подключает синтетический/реальный репозиторий к Atlas, собирает **read-only**
git baseline, показывает dirty-state, инструкции, источник и команды, и
безопасно создаёт изолированный worktree под одной writer-арендой.

## Источники проекта (§35)

- **Локальный Git-путь** — канонический путь внутри разрешённого workspace-корня
  (`/var/lib/codevinci-atlas/workspaces`); baseline собирается только чтением.
- **GitHub-репозиторий** — сохраняются санированные метаданные (`owner/repo`,
  `https://github.com/owner/repo`) **без credentials**; credential-URL отклоняется.
- **Архив (read-only intake)** — tar/zip распаковывается только в intake-корень
  (`…/intake`); враждебные архивы блокируются, распакованное read-only.
- **Пустой проект** — без источника; источник можно подключить позже.

Отключение проекта (`disconnect`) **не удаляет** репозиторий, dirty-работу,
удалённый репозиторий, архив-источник или файлы владельца — только помечает
проект отключённым в состоянии Atlas.

## Git baseline (read-only, non-destructive)

Собирается: канонический путь, ветка, HEAD, санированные remotes, porcelain
dirty-state, счётчики tracked/untracked, вложенные инструкции с precedence,
пакетные менеджеры, baseline-команды, redacted-статус секрет-скана, метка
времени и content-hash.

Гарантии: **никаких** `git reset --hard`/`git clean`/тихого stash/checkout
поверх dirty; исходное dirty-состояние сохраняется байт-в-байт и отображается.
`GIT_TERMINAL_PROMPT=0` и `GIT_OPTIONAL_LOCKS=0` — сбор не блокируется и не берёт
index.lock. **Baseline-команды не исполняются** — они показываются владельцу.

## Worktree и один writer

- Ветка — строго `atlas/vp-<n>-<slug>`; путь worktree — канонический в allowlist
  (`…/worktrees/<project>/<vp-slug>`), без перезаписи существующего.
- `git worktree add -b` создаёт новую ветку и каталог, не трогая оригинал/dirty.
- Один Builder-writer на worktree (`worktree_leases`, UNIQUE активной аренды);
  второй writer → `WORKTREE_CONFLICT`. Planner/Reviewer — read-only.
- Осиротевшая аренда освобождается только через `reconcile()` после проверки
  живости процесса и чистоты Git; автоугон запрещён. Удаление worktree — только явное.

## Безопасность (§30.1)

- Канонизация (`realpath`) + allowlist корней; блокировка traversal (`..`),
  абсолютных путей, Windows-разделителей и symlink-escape.
- Архивы — враждебный вход: отклоняются symlink/hardlink, устройства/спец-файлы,
  превышение числа элементов/размера, дубликаты; ни один файл не создаётся вне
  intake-корня.
- Содержимое репозитория/архива/инструкций/вывода модели — **данные**; они не
  расширяют права и не хранят credentials (§30.2). В БД/логи/artifacts секреты
  не попадают (redaction + секрет-скан).

## API

```text
GET    /api/v1/projects                         список проектов
POST   /api/v1/projects                         подключить (source_kind: local_git|github|archive|empty)
GET    /api/v1/projects/{id}                     Project Overview
POST   /api/v1/projects/{id}/baseline/refresh   пересобрать baseline (read-only)
POST   /api/v1/projects/{id}/worktrees          создать worktree {branch}
POST   /api/v1/projects/{id}/worktrees/acquire  попытка writer (демонстрация отказа)
DELETE /api/v1/projects/{id}                     отключить (без удаления)
```

## Web (Ember)

Раздел **Проекты** в консоли (`http://127.0.0.1:3210`): список, подключение
источника, Project Overview (источник, git baseline, dirty-предупреждение,
инструкции, пакетные менеджеры, команды, worktree/аренда, точный next action).
Состояния loading/empty/offline/stale/error. RU по умолчанию, EN переключением.

## Приёмка

```bash
python3 scripts/run_vp2_acceptance.py   # 20/20 против реального стека и фикстур
```
