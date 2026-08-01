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

Active staged development across **VP-0…VP-9**. **VP-0: Profile Pool & Live
Handoff Proof — COMPLETE (11/11 PASS)**, including real A→B for both Codex and
Claude. Current stage — **VP-1: Foundation**.

Proven for real in VP-0:

- isolation of **2 Codex + 2 Claude** profiles via separate Unix identities and
  executables (profile A's process cannot read B's credentials; `atlas` reads none);
- **real A→B**: profile A yields a structured result, Atlas persists and verifies
  the HandoffPackage against the DB, profile B (different account, separate
  session) continues and finishes — independently checkable, for both providers;
- **one writer**; Runner interruption → reconciliation → continuation to one success;
- recovery after Core restart; rate-limit profile switch without a second writer;
- no secrets in tree/Git history/DB/logs/artifacts;
- honest **UNKNOWN** capacity.

## Quick start (VP-0 proof)

Only Python 3.12+ is required (tested on 3.14); uv/pnpm/Docker are not needed
for VP-0.

```bash
# Unit acceptance (83 tests)
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'

# Full VP-0 acceptance + evidence + scans
PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py

# Identity-free profile diagnostics + web status
PYTHONPATH=apps/core:apps/runner python3 scripts/atlas-doctor --web-status var/status.html
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
