# PROJECT WORKSPACE — connecting projects (VP-2)

🇷🇺 Russian (canonical): [`../PROJECT_WORKSPACE.md`](../PROJECT_WORKSPACE.md).
Source of truth — [`../MASTER_SPEC.md`](../MASTER_SPEC.md) §35; spec — [`../vp/VP-2.md`](../vp/VP-2.md).

## What it does

Connects a synthetic/real repository to Atlas, collects a **read-only** Git
baseline, shows dirty state, instructions, source and commands, and safely
creates an isolated worktree under a single writer lease.

## Project sources (§35)

- **Local Git path** — canonical path inside an allowed workspace root
  (`/var/lib/codevinci-atlas/workspaces`); baseline is collected read-only.
- **GitHub repository** — sanitized metadata (`owner/repo`,
  `https://github.com/owner/repo`) is stored **without credentials**; a
  credential-bearing URL is rejected.
- **Archive (read-only intake)** — tar/zip is extracted only into the intake
  root (`…/intake`); hostile archives are blocked, extracted content is read-only.
- **Empty project** — no source; a source can be connected later.

Disconnecting a project does **not** delete the repository, dirty work, remote
repository, archive source, or owner files — it only marks the project detached
in Atlas state.

## Git baseline (read-only, non-destructive)

Captured: canonical path, branch, HEAD, sanitized remotes, porcelain dirty state,
tracked/untracked counts, nested instructions with precedence, package managers,
baseline commands, redacted secret-scan status, timestamp, and content hash.

Guarantees: **no** `git reset --hard`/`git clean`/silent stash/checkout over
dirty work; the original dirty state is preserved byte-for-byte and displayed.
`GIT_TERMINAL_PROMPT=0` and `GIT_OPTIONAL_LOCKS=0` — collection never blocks and
never takes the index lock. **Baseline commands are not executed** — they are
shown to the owner.

## Worktree and single writer

- Branch is strictly `atlas/vp-<n>-<slug>`; the worktree path is canonical inside
  the allowlist (`…/worktrees/<project>/<vp-slug>`), without overwriting an
  existing one.
- `git worktree add -b` creates a new branch and directory without touching the
  original working tree/dirty state.
- One Builder writer per worktree (`worktree_leases`, unique active lease); a
  second writer → `WORKTREE_CONFLICT`. Planner/Reviewer stay read-only.
- An orphaned lease is released only via `reconcile()` after checking writer
  process liveness and Git cleanliness; auto-takeover is forbidden. Worktree
  removal is explicit only.

## Security (§30.1)

- Canonicalization (`realpath`) + root allowlist; blocking of traversal (`..`),
  absolute paths, Windows separators, and symlink escape.
- Archives are hostile input: symlinks/hardlinks, devices/special files, entry
  count/size overflow, and duplicates are rejected; no file is created outside
  the intake root.
- Repository/archive/instruction/model-output content is **data**; it never
  expands capabilities or stores credentials (§30.2). Secrets never reach the
  DB/logs/artifacts (redaction + secret scan).

## API

```text
GET    /api/v1/projects                         list projects
POST   /api/v1/projects                         connect (source_kind: local_git|github|archive|empty)
GET    /api/v1/projects/{id}                     Project Overview
POST   /api/v1/projects/{id}/baseline/refresh   re-collect baseline (read-only)
POST   /api/v1/projects/{id}/worktrees          create worktree {branch}
POST   /api/v1/projects/{id}/worktrees/acquire  writer attempt (denial demo)
DELETE /api/v1/projects/{id}                     disconnect (no delete)
```

## Web (Ember)

A **Projects** section in the console (`http://127.0.0.1:3210`): list, connect a
source, Project Overview (source, git baseline, dirty warning, instructions,
package managers, commands, worktree/lease, exact next action). States
loading/empty/offline/stale/error. RU by default, EN via switch.

## Acceptance

```bash
python3 scripts/run_vp2_acceptance.py   # 20/20 against the live stack and fixtures
```
