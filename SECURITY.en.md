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
- **Product Map (VP-3): data is not commands.** Intake/links/facts are owner
  data (§30.2): text is redacted and length-bounded, links are stored as
  sanitized metadata, VP-3 never fetches external URLs and makes no
  model/provider calls. Double guard: a secret in the input is redacted, and the
  canary marker is rejected (`INTAKE_INVALID`) — it never reaches DB/export.
  `VERIFIED` requires resolvable evidence + hash match; approval never turns a
  hypothesis into `VERIFIED`. MD/JSON export carries no credentials, environment
  dumps, raw auth paths, or unsafe HTML.
- **Work Orders & Context (VP-4): isolation and non-escalation.** JobPackage and
  HandoffPackage are bounded and immutable, with **no** repo/full chat/logs/
  credentials/env; capabilities come only from an allowlist and context does not
  expand authority (§30.2). The fresh consumer (`scripts/vp4_fresh_consumer.py`)
  runs as a separate process in a clean environment — no DB, credentials, full
  repo, or prior chat; the compact fallback fails closed with `OWNER_REQUIRED`;
  there are no real provider calls. Handoff rejects tamper/stale/wrong-project/
  wrong-version/wrong-HEAD/over-capability. The audit log is append-only and
  redacted.

## Cookie gate (§30.3)

The cookie path is experimental and off by default. VP-0 implements only the
adapter **boundary** — no cookie extraction or import is performed.

## Reporting

Until a public disclosure policy is chosen, report privately to the repository
owner. Do not attach real secrets — use a redacted example.
