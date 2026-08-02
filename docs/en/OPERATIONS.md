# OPERATIONS (VP-1)

🇷🇺 Russian (canonical): [`../OPERATIONS.md`](../OPERATIONS.md).

## Services

- **Runner** — host systemd: `codevinci-atlas-runner.service` (`User=atlas`,
  non-root). Manage: `systemctl {status,restart,stop} codevinci-atlas-runner`.
  Socket/token: `/run/codevinci-atlas/{runner.sock,runner.token}` (group
  `atlas-bridge`, `RuntimeDirectoryPreserve=yes`).
- **Core/Web** — Docker Compose: `docker compose {up -d,ps,logs,restart,down}`.
  Core non-root (uid = host `atlas`), Web `nginx-unprivileged` (uid 101), only
  `127.0.0.1:3210`. Migrations are applied by the entrypoint (`alembic upgrade head`).
- **Health:** `curl -s http://127.0.0.1:3210/api/v1/health` — truthful
  `READY/DEGRADED/OFFLINE`. Runner offline → overall `DEGRADED`, runner
  `OFFLINE` (visible both in the API and the Web).
- **Restart safety:** Core/Web and Runner restart independently; after a Runner
  restart the Core container sees the socket again (stable inode via
  `RuntimeDirectoryPreserve=yes`).

## Runtime and identities

- Single layout — `/var/lib/codevinci-atlas`.
- Create identities, bridge group and directories:
  `sudo bash scripts/atlas-runtime-setup.sh` (service `atlas`, `atlas-bridge`,
  per-profile `atlas-cx01/02`, `atlas-cl01/02`).
- Profile roots `0700` owned by their own identity; the Runner drops privileges
  into the profile identity via `subprocess(user=/group=)` + `CAP_SETUID/SETGID`
  (`runuser` is unusable from non-root — see DECISIONS VP1-D2).

## One writer and recovery

- One lease per worktree (`atlas_core.leases`). A second acquire →
  `WORKTREE_CONFLICT`.
- After a lost heartbeat a new writer is denied until `reconcile()` (checks
  writer-process liveness and Git cleanliness). Auto-takeover is forbidden.
- Core restart: active runs → `INTERRUPTED`, continued from checkpoint.
- Runner hard crash: unfinished journal jobs → `INTERRUPTED` on start;
  reconciliation after the writer dies; **continuation to one success** without a
  second writer.

## Backup (`atlas backup`)

```bash
uv run atlas backup --json --out /var/lib/codevinci-atlas/backups
```

- Online SQLite snapshot (`Connection.backup`, not a file copy).
- Artifacts manifest with per-file SHA-256 + `PRAGMA integrity_check`.
- Archive `atlas-backup-<ts>.tar.gz` with `atlas.db`, `manifest.json`, a safe
  `config.yaml` (only if it contains no secrets), and content-addressed artifacts.
- The backup **excludes** profile auth-roots, runner tokens, logs; the archive is
  scanned for secret markers (`secret_scan_clean`).
- Retention (§23.3): 7 daily + 4 weekly.

## Restore (manual, dry-run verifiable)

There is no `atlas restore` command; the procedure is manual and verifiable:

```bash
# 1) verify archive hash against the backup manifest
sha256sum atlas-backup-<ts>.tar.gz

# 2) DRY-RUN: extract to a temp dir and check DB integrity
tmp=$(mktemp -d); tar -xzf atlas-backup-<ts>.tar.gz -C "$tmp"
sqlite3 "$tmp/atlas.db" 'PRAGMA integrity_check;'   # expected: ok
cat "$tmp/manifest.json"                            # db/artifacts hashes

# 3) apply (owner action): stop Core, swap DB, migrate
docker compose stop core
install -o atlas -g atlas -m 0640 "$tmp/atlas.db" /var/lib/codevinci-atlas/atlas.db
docker compose up -d core     # entrypoint runs alembic upgrade head
curl -s http://127.0.0.1:3210/api/v1/health
```

Stack upgrade order: **backup → migrate → health → switch**.

## Access: localhost and SSH tunnel (§7.4)

The Web listens only on `127.0.0.1:3210` — no public port. From your machine:

```bash
ssh -L 3210:127.0.0.1:3210 <user>@<server>
# then open http://127.0.0.1:3210
```

## Development and tests

```bash
# unit acceptance (stdlib unittest)
PYTHONPATH=apps/core:apps/runner:tests python3 -m unittest discover -s tests -p 'test_*.py'
# lint (as in CI)
uv run ruff check apps tests scripts
# web: typecheck (tsc strict), locale parity, production build
cd apps/web && pnpm typecheck && pnpm check:i18n && pnpm build
# full VP-1 acceptance (17/17, requires a running stack)
python3 scripts/run_vp1_acceptance.py
# full secret scan
PYTHONPATH=apps/core python3 scripts/secret_scan.py
```

## Troubleshooting

- **`/api/v1/health` = DEGRADED, runner OFFLINE** — check
  `systemctl status codevinci-atlas-runner`; after `restart` the Core sees the
  socket again automatically.
- **runner `UNAUTHORIZED`** — the token is unreadable or wrong; check
  `/run/codevinci-atlas/runner.token` permissions (`0640`, group `atlas-bridge`)
  and Core membership in `atlas-bridge`.
- **Core cannot see the socket after a Runner restart** — ensure the unit has
  `RuntimeDirectoryPreserve=yes` (otherwise the bind-mount inode changes, VP1-D3).
- **`docker compose up` — permission denied on paths** — `.env` must set
  `ATLAS_UID/ATLAS_GID/ATLAS_BRIDGE_GID` of the real host `atlas` user.
- **migrations not applied** — inspect the Core entrypoint logs
  (`docker compose logs core`); check `uv run atlas doctor` (`migrations` field).

## Security in operation

- Diagnostics/logs/evidence contain no email, token, cookie, raw path.
- Durable-state check: secret-marker scanner (acceptance `c16`, backup `c9/c10`).
- Runner: argv arrays only, allowlisted dirs/executables, request token, socket `0660`.
