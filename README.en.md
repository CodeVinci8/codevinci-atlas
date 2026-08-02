# CodeVinci Atlas

**Self-hosted control center for Codex and Claude: projects, profiles, review,
evidence, and safe automation.**

Atlas is a standalone public open-source CodeVinci product and a single-owner
self-hosted tool. It turns the "idea → Planner → Builder → Reviewer → allowed
PR" process into a reproducible pipeline with versioned memory instead of an
endless chat.

> 🇷🇺 Русская версия: [`README.md`](README.md)
> 📘 Canonical spec: [`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md) (Russian)

## Status

Active staged development across **VP-0…VP-9**. Completed and merged into `main`:

- **VP-0 — Profile Pool & Live Handoff Proof: COMPLETE (11/11 PASS)** — real
  A→B for Codex and Claude, isolation of 4 profiles, one writer, honest
  `UNKNOWN` capacity.
- **VP-1 — Foundation: COMPLETE (17/17 PASS)** — Compose Core/Web + systemd
  Runner, health/migrations/audit, CLI `doctor/status/backup`, RU/EN web shell, CI.
- **VP-2 — Project Workspace: COMPLETE (20/20 PASS)** — project connect (local
  Git / GitHub / archive / empty), read-only git baseline, safe worktrees and
  writer leases, Project Overview (Ember RU/EN).
- **VP-3 — Product Map: COMPLETE (26/26 PASS)** ([`docs/vp/VP-3.md`](docs/vp/VP-3.md)) —
  structured intake, truth status, Brief versions and decisions, Project/Portfolio
  Map, version diff, scope envelope, parking lot, and export of the accepted state
  to Markdown/JSON. Every fact carries an explicit truth status; `VERIFIED`
  requires resolvable evidence.

- **VP-4 — Work Orders & Context: COMPLETE (26/26 PASS)**
  ([`docs/vp/VP-4.md`](docs/vp/VP-4.md), [`docs/en/WORK_ORDERS.md`](docs/en/WORK_ORDERS.md)) —
  deterministic VP Spec from an accepted Brief/Map, executable Work Orders with
  versioned lifecycle and one-writer leases, controlled optimizer decisions
  (READY/MERGE/SPLIT/OWNER_REQUIRED/SWITCH_PROFILE), bounded immutable JobPackage,
  Context Governor with durable checkpoints and handoff, and a **fresh isolated
  consumer** that reconstructs state from the HandoffPackage alone. Merged via
  PR #6 (squash `7a3f82d`, CI head `280ee35`); live DB on `0004_work_orders`.

**VP-5 — Agent Pipeline (Master Spec §38, §17): ACTIVE, implemented locally**
([`docs/vp/VP-5.md`](docs/vp/VP-5.md)) — the Codex Planner → Claude Builder →
independent Codex Reviewer pipeline: durable Runs (lifecycle, idempotency,
optimistic concurrency), router with no silent fallback, one writer (worktree +
profile lease), three distinct session semantics (EXACT_RESUME / FORK_SESSION /
FRESH_WITH_HANDOFF), bounded rate-limit/auth/interruption recovery, Profiles MVP,
full-width Pulse, RU/EN. Deterministic acceptance `run_vp5_acceptance.py` —
**26/26** (real `0005` migration, real services, fake adapters). **Real provider
E2E is pending** owner authorization; no PR/merge yet.

## Quick start

Core/Web run in Docker Compose; the Runner is a native systemd service. Single
runtime layout — `/var/lib/codevinci-atlas`. Web listens on loopback only,
`http://127.0.0.1:3210`.

```bash
# runtime identities and directories (root, idempotent), then profiles
sudo bash scripts/atlas-runtime-setup.sh
PYTHONPATH=apps/core python3 scripts/profile-init.py

# systemd Runner (native host, UDS)
sudo cp infra/systemd/codevinci-atlas-runner.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now codevinci-atlas-runner

# Compose Core/Web (atlas UID/GID in .env; entrypoint applies migrations)
docker compose up -d --build

# stack and DB health
curl -s http://127.0.0.1:3210/api/v1/health

# staged acceptances (root, stack up)
python3 scripts/run_vp1_acceptance.py      # 17/17
python3 scripts/run_vp2_acceptance.py      # 20/20
python3 scripts/run_vp3_acceptance.py      # 26/26 (VP-3)
python3 scripts/run_vp4_acceptance.py      # 26/26 (VP-4)
```

Unit/integration tests for Core/Runner (no stack):

```bash
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
```

Real subscription profile probes are behind an owner gate:
`scripts/login-gate.sh`.

## Architecture (short)

Core/Web run in Docker Compose; a native host Runner (systemd, user `atlas`)
executes the real `codex`/`claude`/`git`/`gh`. Core ↔ Runner is a Unix domain
socket with a request token. Credentials are never mounted into Web. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## License

Not chosen yet: LICENSE is fixed after the VP-0 reuse audit (candidates
MIT/Apache-2.0) and requires separate owner approval (Master Spec §49).

## Privacy and security

Atlas is not a secret vault for CLI credentials and does not bypass provider
rules. Credentials live only in isolated profile roots. Diagnostics and logs
never expose email, token, cookie, or raw path.
