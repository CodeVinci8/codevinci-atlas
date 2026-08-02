# Product Map (VP-3)

The Product Map model and API — versioned Brief/Map, truth status, decisions,
approval, parking lot, and export. Canonical spec — [`../MASTER_SPEC.md`](../MASTER_SPEC.md)
§36 (Russian); executable spec — [`../vp/VP-3.md`](../vp/VP-3.md). Русский:
[`../PRODUCT_MAP.md`](../PRODUCT_MAP.md).

## Idea

A connected project accepts a structured **intake** (owner data, not commands).
From it, versioned **Draft Brief** and **Draft Map** are produced with explicit
**truth statuses**. The owner accepts/rejects proposed decisions individually,
approves an exact Brief version and scope envelope. The system shows a durable
**Project Map** and a truthful **Portfolio Map**, compares versions (diff),
maintains a parking lot, and exports the accepted state to Markdown/JSON.

## Truth status

Each fact carries exactly one status: `VERIFIED`, `OWNER_PROVIDED`, `INFERRED`,
`HYPOTHESIS`, `STALE`, `UNKNOWN`.

- `VERIFIED` requires **resolvable evidence** and a matching `content_hash`. The
  only evidence type in VP-3 is a **VP-2 git baseline**
  (`evidence_ref="git_baseline:<id>"` or `":latest"`, `evidence_hash =
  baseline.content_hash`). A missing/forged/stale/mismatched reference cannot
  create `VERIFIED` (`EVIDENCE_INVALID`).
- Owner approval does **not** turn a hypothesis into `VERIFIED`.
- `OWNER_PROVIDED` records provenance without claiming independent verification;
  inference and hypothesis stay visibly distinct.
- Status and decision transitions are audited (append-only Audit).

## Versions, concurrency, approval

- Brief/Map are immutable versions (`version`, `parent_id`, `content_hash` =
  `sha256:` over canonical JSON). Editing creates a new version; an approved
  version is never mutated. Diff is a deterministic field/node/edge diff.
- Optimistic concurrency: mutations take `expected_version` (mismatch →
  `VERSION_CONFLICT`). Idempotency: `Idempotency-Key` (replay creates no
  duplicates). Exactly one active VP is a durable invariant
  (`ACTIVE_VP_CONFLICT`).
- The **approved version** is defined by the immutable `approvals` record, not by
  the Brief status: a new draft on top of an approved version does not
  un-approve it.
- Approval **fails** when: stale version (`VERSION_CONFLICT`), unresolved
  required decisions (`DECISION_UNRESOLVED`), empty/inconsistent envelope
  (`ENVELOPE_INVALID`), invalid evidence on a `VERIFIED` fact
  (`EVIDENCE_INVALID`), invalid map node references (`MAP_INVALID`), unavailable
  project (`PROJECT_NOT_AVAILABLE`). Approval binds Brief+hash, Map version,
  envelope hash, decisions hash, actor, timestamp.

## Project Map

Nodes: `goal`, `user_problem`, `brief_decision`, `vp`, `blocker`,
`evidence_ref`, `next_action`, `parking_item`. Edges: `dependency`, `blocks`,
`proves`, `includes`, `next`. Dangling references, cross-project edges, unknown
types, and cycles (dependency semantics are acyclic) are rejected →
`MAP_INVALID`. Parking items stay outside the active scope (reason + return
condition), survive versioning; only an explicit new version moves them in.

## API (`/api/v1`)

Mutations: `Idempotency-Key` (opt.), `X-Correlation-ID` (opt.), `expected_version`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/projects/{id}/intake` | intake → Draft Brief v1 + Draft Map v1 + decisions |
| GET | `/projects/{id}/product-state` | aggregated Product Map state |
| GET | `/projects/{id}/briefs` · `/briefs/{bid}` | Brief versions |
| POST | `/projects/{id}/briefs/{bid}/revise` | new version from parent |
| GET | `/projects/{id}/briefs/diff?from=&to=` | field-level diff |
| POST | `/projects/{id}/briefs/{bid}/approve` | approve exact version |
| GET | `/projects/{id}/decisions` · `/decisions/{did}` | decisions |
| POST | `/projects/{id}/decisions/{did}/accept`·`/reject` | individual |
| GET/POST | `/projects/{id}/parking-lot` | parking lot |
| GET | `/projects/{id}/map` · `/map/versions` · `/map/diff` | Project Map |
| POST | `/projects/{id}/map/versions` | new map version (validated) |
| POST | `/projects/{id}/map/vps/activate` | activate one VP |
| GET | `/projects/{id}/export?format=json\|md&version=` | export |
| GET | `/portfolio` | Portfolio Map (projection) |

Every mutation records an append-only Audit event (actor/project/correlation/
version/hash + redacted summary; no full private input or credentials).

## Export

JSON (documented `schema_version`) and human-readable Markdown for the exact
version: Brief, Map, decisions, truth statuses, envelope, parking lot, project
state, hashes, and timestamps. Deterministic for the same accepted version
(apart from the `_generated` block). No credentials, environment dumps, raw auth
paths, unbounded repository contents, or unsafe HTML.

## Boundary (VP-3)

Data is not commands: VP-3 does **not** fetch external links and makes **no**
model/provider calls. Out of scope: Work Orders, VP Specs, Planner/Builder/
Reviewer execution, the full Evidence system (VP-6), Time Machine, GitHub merge
automation, the full VP-8 console/theme, a free-form graph editor.
