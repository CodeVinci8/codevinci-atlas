# SECURITY — модель безопасности

Master Spec §30. 🇬🇧 English: [`SECURITY.en.md`](SECURITY.en.md).

## Активы и угрозы

Активы: credentials профилей, GitHub-auth, репозитории, «грязная» работа,
приватные входы, grants, audit. Угрозы: prompt/command injection, traversal,
symlink-escape, утечка секретов, confused deputy, устаревший review,
несанкционированный merge, два writer, lifecycle-скрипты, открытая панель.

## Контроли (реализовано в VP-0)

- **Изоляция профилей — отдельные Unix-идентичности.** Каждый профиль имеет
  собственную идентичность (`atlas-cx01/02`, `atlas-cl01/02`); root
  (`CODEX_HOME`/`CLAUDE_CONFIG_DIR`) `0700` во владении этой идентичности.
  Runner дропает привилегии в идентичность профиля перед запуском CLI.
  Доказано на реальной границе исполнения: процесс профиля A НЕ читает
  credentials B, а сервисный `atlas` не читает ни один профиль. (Слабый тест
  `nobody`-vs-root заменён.) Core-guard `isolated_env` дополнительно не
  передаёт процессу чужой root.
- **Никакого копирования credentials** между root (правило одного auth owner).
- **Редактирование секретов** во всём, что идёт в лог/evidence/event/artifact
  (`redact`). Запись секрета в БД блокируется (`SecretLeakError`).
- **Сканер secret-markers** по БД/Git/логам/artifacts (эквивалент gitleaks).
- **Runner:** только argv-массив (не shell-строка), allowlist каталогов и
  исполняемых файлов, request-token, socket `0660`, отдельная process group,
  запрет секретов в запросе, отказ от root по умолчанию.
- **Один writer** на worktree; после потери heartbeat — reconciliation.
- **Честная ёмкость** UNKNOWN — без фиктивных значений.
- **Product Map (VP-3): данные — не команды.** Intake/ссылки/факты — owner-данные
  (§30.2): текст redacted и bounded по длине, ссылки хранятся как санированные
  метаданные, VP-3 не ходит по внешним URL и не делает model/provider-вызовов.
  Двойная защита: секрет во вводе редактируется, а канареечный маркер отклоняется
  (`INTAKE_INVALID`) — в БД/экспорт не попадает. `VERIFIED` требует resolvable
  evidence + совпадения hash; approval не создаёт `VERIFIED` из гипотезы. Экспорт
  MD/JSON без credentials/env-дампов/raw auth-путей/небезопасного HTML.

## Cookie-гейт (§30.3)

Cookie-путь экспериментален и off by default. Требует: проверки совместимости
провайдера, решения о terms, защищённого temp, отсутствия логирования,
одноразовости, явной capability, пост-скана, logout/revocation. В VP-0
реализована только **граница** адаптера — извлечение/импорт cookie не выполняется.

## Что не делает Atlas

Не является secret-vault CLI credentials, не обходит лимиты/правила провайдера,
не выполняет неразрешённые внешние действия (repo/commit/push/merge/deploy).

## Тест-фикстуры

Файлы `tests/test_redaction.py`, `tests/test_errors.py`,
`tests/test_runner_uds.py`, `tests/test_capacity_and_scan.py` содержат
**синтетические** примеры токенов/куки (напр. `ghp_ABCDEF…012345`) — они не
являются реальными секретами и нужны, чтобы проверить работу `redact()` и
классификатора. Приёмочный сканер (`c9`) проверяет **durable-состояние**
(БД/Git-objects/логи/artifacts), а не исходники тестов. В CI VP-1 gitleaks
получит allowlist на каталог `tests/` для этих фикстур.

## Сообщить о проблеме

До выбора публичной политики раскрытия сообщайте владельцу репозитория
приватно. Не прикладывайте реальные секреты — используйте redacted-пример.
