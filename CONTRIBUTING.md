# CONTRIBUTING — участие в разработке

🇬🇧 English: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md).

## Правила

- Читайте [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) — это источник истины.
  Работайте только над активным VP из [`docs/NEXT.md`](docs/NEXT.md).
- Коммиты, PR и оперативные документы — на русском. Стабильные документы —
  пары RU/EN, обновляются в одном коммите при изменении публичного контракта.
- Git-идентичность: имя `CodeVinci`, email — из `git config`; без `--author`,
  без AI-атрибуции и `Co-Authored-By`.
- Subject коммита — императив, ≤72 символа, один логический результат. Тело
  `Проверки:` — только с реально выполненными командами.
- Один writer на worktree; ветка `atlas/vp-<n>-<slug>`. Без force push и
  destructive Git. Пользовательскую работу сохранять.
- Секреты не попадают в код/логи/БД/artifacts. Не коммитить `.env`, `auth.json`,
  токены, cookie.

## Проверки перед PR

```bash
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py
```

Политика тестов — риск-ориентированная ([`docs/TEST_POLICY.md`](docs/TEST_POLICY.md)).
Полная регрессия после микроправки — только при risk-триггере.
