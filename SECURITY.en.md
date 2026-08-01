# SECURITY — security model

Master Spec §30 (Russian). 🇷🇺 Русская версия: [`SECURITY.md`](SECURITY.md).

## Assets and threats

Assets: profile credentials, GitHub auth, repositories, dirty work, private
inputs, grants, audit. Threats: prompt/command injection, traversal, symlink
escape, secret leak, confused deputy, stale review, unauthorized merge, two
writers, lifecycle scripts, exposed panel.

## Controls (implemented in VP-0)

- **Profile isolation via separate Unix identities.** Each profile has its own
  identity (`atlas-cx01/02`, `atlas-cl01/02`); its root
  (`CODEX_HOME`/`CLAUDE_CONFIG_DIR`) is `0700` owned by that identity. The
  Runner drops privileges into the profile identity before launching the CLI.
  Proven at the real execution boundary: profile A's process cannot read B's
  credentials, and the `atlas` service user reads none. (The weak
  `nobody`-vs-root test was replaced.) The `isolated_env` Core guard also never
  passes another profile's root.
- **No credential copying** between roots (single auth owner rule).
- **Secret redaction** for anything written to logs/evidence/events/artifacts.
  Writing a secret to the DB is blocked (`SecretLeakError`).
- **Secret-marker scanner** over DB/Git/logs/artifacts (gitleaks equivalent).
- **Runner:** argv array only (no shell string), directory/executable
  allowlist, request token, socket `0660`, separate process group, secrets in
  request rejected, refuses root by default.
- **One writer** per worktree; heartbeat loss requires reconciliation.
- **Honest capacity** UNKNOWN — no fabricated numbers.

## Cookie gate (§30.3)

The cookie path is experimental and off by default. VP-0 implements only the
adapter **boundary** — no cookie extraction or import is performed.

## Reporting

Until a public disclosure policy is chosen, report privately to the repository
owner. Do not attach real secrets — use a redacted example.
