# ARCHITECTURE (EN, VP-0 slice)

Russian canonical: [`../ARCHITECTURE.md`](../ARCHITECTURE.md). Full target
architecture: Master Spec §7.

## Target topology

```text
Browser via SSH tunnel
        │
Web container (Nginx + React)          ← VP-1/VP-8
        │ /api
Core container (FastAPI + SQLite)      ← VP-1 (contracts + SQLite store in VP-0)
        │ Unix socket + request token  ← VP-0 prototype implemented
Host Runner (systemd, user atlas)      ← VP-0 prototype (allow_root in VP-0 env)
        ├── Codex CLI / isolated CODEX_HOME
        ├── Claude CLI / isolated CLAUDE_CONFIG_DIR
        ├── Git / GitHub CLI
        └── allowlisted worktrees
```

## Implemented in VP-0

- `apps/core/atlas_core`: contracts, error taxonomy + classifier, redaction +
  secret scanner, profile isolation + non-secret registry, honest capacity,
  SQLite store with secret-write guard, one-writer leases with reconciliation,
  adapters (protocol + fake + real spikes), checkpoint/handoff with
  verification, orchestrator (A→B switch, single-writer invariant, Core restart
  recovery), diagnostics and web status (identity-free).
- `apps/runner/atlas_runner`: UDS protocol with request token, asyncio server
  (allowlist, argv-only, streamed redacted events, heartbeat, timeout,
  interrupt, recovery journal), client, journal.

## Docker vs native Runner

Core/Web are containerized for repeatable install. The Runner stays native
because it runs the real `codex`/`claude`/`git`/`gh`, sees auth roots,
worktrees, and process groups. Credentials are never mounted into Web (§7.1).

## Secret-free data flows

Profile credentials live only in isolated CLI roots. Core/Runner pass a process
only its own root variable (`CODEX_HOME`/`CLAUDE_CONFIG_DIR`), never another's.
Anything going to logs/evidence/events/artifacts passes through `redact()`;
writing secrets to the DB is blocked (`SecretLeakError`).
