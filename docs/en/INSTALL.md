# INSTALL — setup and run

🇷🇺 Russian (canonical): [`../INSTALL.md`](../INSTALL.md).

## VP-1 — Compose Core/Web + systemd Runner

Requirements: Linux + systemd, Docker Engine + Compose plugin, Python 3.12+,
`uv` and `pnpm` (for the CLI/build outside containers).

```bash
cd /opt/CodeVinciAtlas

# 1) runtime identities, bridge group, directories (root, idempotent)
sudo bash scripts/atlas-runtime-setup.sh
sudo PYTHONPATH=apps/core python3 scripts/profile-init.py

# 2) systemd Runner (native host process, User=atlas, non-root)
sudo cp infra/systemd/codevinci-atlas-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codevinci-atlas-runner.service
systemctl is-active codevinci-atlas-runner.service   # active

# 3) .env: UID/GID of the host `atlas` user and the bridge group
printf 'ATLAS_UID=%s\nATLAS_GID=%s\nATLAS_BRIDGE_GID=%s\n' \
  "$(id -u atlas)" "$(getent group atlas | cut -d: -f3)" \
  "$(getent group atlas-bridge | cut -d: -f3)" > .env

# 4) Compose Core/Web (migrations are applied by the entrypoint)
docker compose up -d --build
docker compose ps          # core/web healthy

# 5) VP-1 acceptance (17/17)
python3 scripts/run_vp1_acceptance.py
```

### Access via SSH tunnel (no public port, §7.4)

The Web listens only on `127.0.0.1:3210`. From your local machine:

```bash
ssh -L 3210:127.0.0.1:3210 <user>@<server>
# then open http://127.0.0.1:3210 in the browser
```

On-server check: `curl -s http://127.0.0.1:3210/api/v1/health`.

### CLI

```bash
uv run atlas doctor    # deps/migrations/Runner/permissions/profiles (no secrets)
uv run atlas status    # short Core/Runner/DB state
uv run atlas backup    # online SQLite backup + SHA-256 manifest + secret scan
```

There is no `atlas restore` command by design; the restore procedure is manual
and verifiable — see [`OPERATIONS.md`](OPERATIONS.md).

## Canonical runtime layout

A single canonical path **`/var/lib/codevinci-atlas`** holds `atlas.db`,
`artifacts`, `backups`, `profiles`, `worktrees`. Tests isolate with a temporary
`ATLAS_DATA_DIR`.

```text
/opt/CodeVinciAtlas/        # checkout
/etc/codevinci-atlas/       # config, outside Git
/var/lib/codevinci-atlas/   # atlas.db, artifacts, backups, profiles, worktrees
/var/log/codevinci-atlas/
/run/codevinci-atlas/runner.sock
```

Permissions: `atlas:atlas`; profile roots `0700`; credentials ≤ `0600`;
socket `0660`; runtime never runs as root.
