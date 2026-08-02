# Work Orders & Context Engine (VP-4)

Source of truth: [`docs/MASTER_SPEC.md`](../MASTER_SPEC.md) §16, §37 (on conflict —
§1; the spec is Russian). Executable spec: [`docs/vp/VP-4.md`](../vp/VP-4.md).
Decisions: [`docs/DECISIONS.md`](../DECISIONS.md) (VP-4 block). RU version:
[`docs/WORK_ORDERS.md`](../WORK_ORDERS.md).

VP-4 turns an accepted Product Brief and Project Map (VP-3) into executable work
contracts and context: **VP Spec → Work Orders → JobPackage →
checkpoint/handoff → fresh isolated reconstruction**. Real autonomous role
routing and the Run engine are out of scope — that is VP-5.

## Data model

Migration `0004_work_orders` (after `0003_product_map`) adds durable tables:

- `vp_specs` — versioned VP Spec derived deterministically from a single accepted
  Brief/Map/approval; `content_hash` over canonical JSON.
- `work_orders` — executable unit; binds exact Spec/Brief/Map hashes and the
  baseline (§16.1); optimistic-lock field (`version`), writer lease
  (`lease_id`, `writer_holder`).
- `work_order_events` — **append-only** history of transitions and decisions.
- `optimizer_decisions` — optimizer decisions and their evidence.
- `job_packages` — immutable, bounded JobPackage with provenance.
- `wo_checkpoints` — durable, hash-verifiable restore points.
- `handoff_packages`, `handoff_acks` — full HandoffPackage and acknowledgements.
- `rotation_records` — context-rotation records (one writer is preserved).

## Work Order lifecycle

States and allowed transitions (`VALID_TRANSITIONS`) are fixed. A valid
transition persists atomically; an invalid one is rejected **without partial
mutation** (`INVALID_TRANSITION`). History is append-only. Optimistic locking by
version: a mismatch → `VERSION_CONFLICT` (no overwrite). A retry with the same
`Idempotency-Key` creates no duplicates.

**One writer.** A worktree holds exactly one lease
(`UNIQUE(worktree, released_at IS NULL)`); there is **no auto-hijack** —
reconcile is required. The lease is held in `active/checkpointed/handoff_ready`;
released on a terminal/blocked state and at the rotation boundary. A second
concurrent write → `WRITER_CONFLICT`.

## Optimizer

Outputs: `READY` (one bounded executable WO), `MERGE_TASKS` (only compatible and
preserving every criterion — otherwise `CRITERIA_LOST`), `SPLIT_AT_CHECKPOINT`
(only on a durable checkpoint, full criterion mapping onto children),
`SWITCH_PROFILE` (profile switch without role routing), `OWNER_REQUIRED`
(fail-closed). The optimizer **never changes** scope or acceptance criteria.

## JobPackage and Context Governor

The JobPackage is deterministic, immutable, and carries provenance; it contains
**no** repo/full chat/logs/credentials/env; capacity is honestly `UNKNOWN`;
capabilities come only from an allowlist. Context **does not expand** authority
(§30.2). The Context Governor deterministically detects rotation triggers (no
fabricated capacity), takes a durable checkpoint, builds the HandoffPackage, and
performs rotation while preserving one writer.

## Handoff and fresh reconstruction

The HandoffPackage carries all required fields and a deterministic hash; it
rejects `HASH_MISMATCH`/`HANDOFF_STALE`/`SCOPE_DRIFT`/`SOURCE_STALE`/
`PROJECT_NOT_AVAILABLE`/`CAPABILITY_DENIED` (tamper/stale/wrong-project/
wrong-version/wrong-HEAD/over-capability). A **fresh isolated consumer**
(`scripts/vp4_fresh_consumer.py`) runs as a separate process in a clean
environment — no `atlas_core`, DB, credentials, full repo, or prior chat — and
reconstructs state and the exact next action from the HandoffPackage alone; the
result is valid against
[`contracts/schemas/run-result.json`](../../contracts/schemas/run-result.json).
The compact fallback is a local deterministic harness: it preserves all
invariants or fails closed with `OWNER_REQUIRED`. **There are no real provider
calls.**

The Core image contains and executes the consumer and contracts
(`infra/docker/core.Dockerfile` copies `scripts/vp4_fresh_consumer.py` and
`contracts/`); packaging regression — `scripts/check_core_image.sh` and
`tests/test_vp4_packaging.py`.

## API (`/api/v1/projects/{id}/...`)

- VP Spec: `POST/GET vp-specs`, `GET vp-specs/{spec_id}`.
- Work Orders: `POST/GET work-orders`, `GET work-orders/{wo_id}`,
  `POST work-orders/{wo_id}/transition`.
- Optimizer: `POST optimizer/evaluate`, `optimizer/merge/preview|confirm`,
  `optimizer/split/preview|confirm`, `GET optimizer/decisions`.
- JobPackage: `POST work-orders/{wo_id}/job-package`, `GET job-packages[/{id}]`,
  `POST context/compact-probe`.
- Checkpoints: `POST work-orders/{wo_id}/checkpoints`, `GET checkpoints[/{id}]`,
  `GET checkpoints/{id}/verify`.
- Handoff: `POST work-orders/{wo_id}/handoffs`, `GET handoffs[/{id}]`,
  `GET handoffs/{id}/verify`, `POST handoffs/{id}/acknowledge|reject|reconstruct`,
  `GET handoffs/{id}/acks`.
- Governor/rotation: `POST governor/detect`, `POST work-orders/{wo_id}/rotate`,
  `POST rotations/{id}/continue`, `GET rotations[/{id}]`.

Mutations accept `Idempotency-Key` and `expected_version`; errors are stable
codes (see VP4-D3). The audit log is append-only and redacted (no secrets).

## Schema contracts

[`contracts/schemas/`](../../contracts/schemas/): `vp-spec.json`,
`work-order.json`, `job-package.json`, `handoff-package.json`,
`run-result.json`. Validated by `scripts/validate_schemas.py`. Our validator is a
**documented subset** of JSON Schema (`type/enum/pattern/minimum/maximum/`
`required/properties/additionalProperties(bool)/items`), not full draft 2020-12.

## Web

The **Work Orders** console (`http://127.0.0.1:3210`, "Work Orders" tab): VP Spec,
Work Orders and transitions, optimizer decisions, checkpoint/handoff, fresh
reconstruction and next action. Dark theme by default, RU/EN parity, a11y,
responsive.

## Acceptance and tests

- Full acceptance: `PYTHONPATH=apps/core:apps/runner python3 scripts/run_vp4_acceptance.py`
  (26/26 against the deployed stack; SHA-256 evidence in `var/artifacts/vp4/`).
- Unit/integration: `tests/test_vp4_workorders.py`, `tests/test_vp4_packaging.py`.
- Schemas: `scripts/validate_schemas.py`. Image: `scripts/check_core_image.sh`.
