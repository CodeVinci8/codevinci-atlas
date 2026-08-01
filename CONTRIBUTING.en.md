# CONTRIBUTING

🇷🇺 Русская версия: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Rules

- Read [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) — it is the source of
  truth. Work only on the active VP from [`docs/NEXT.md`](docs/NEXT.md).
- Commits, PRs, and operational docs are in Russian. Stable docs are RU/EN
  pairs, updated in the same commit when the public contract changes.
- Git identity: name `CodeVinci`, email from `git config`; no `--author`, no
  AI attribution or `Co-Authored-By`.
- Commit subject: imperative, ≤72 chars, one logical result. A `Проверки:`
  body lists only commands actually run.
- One writer per worktree; branch `atlas/vp-<n>-<slug>`. No force push or
  destructive Git. Preserve user work.
- No secrets in code/logs/DB/artifacts. Never commit `.env`, `auth.json`,
  tokens, or cookies.

## Checks before a PR

```bash
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py
```

Risk-based test policy: see [`docs/TEST_POLICY.md`](docs/TEST_POLICY.md). No
full regression after a micro-fix unless a risk trigger applies.
