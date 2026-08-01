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

Active staged development across **VP-0…VP-9**. Current stage — **VP-0:
Profile Pool & Live Handoff Proof**. **VP-0 is not complete yet:** 8/11 criteria
are proven for real; the real A→B probes (criteria 3–5) are behind an owner
login gate for real subscription profiles (`GATE_REAL`).

Proven for real:

- isolation of **2 Codex + 2 Claude** profiles via separate Unix identities
  (profile A's process cannot read B's credentials; the `atlas` service user
  reads none);
- **one writer** per worktree; Runner interruption → reconciliation →
  continuation to a single success without a second writer;
- recovery after Core restart;
- no secrets in tree/Git history/DB/logs/artifacts;
- honest **UNKNOWN** capacity.

The mechanism (fake) is proven for rate-limit profile switching and A→B
handoff; real confirmation of criteria 3–5 runs after the owner login.

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
