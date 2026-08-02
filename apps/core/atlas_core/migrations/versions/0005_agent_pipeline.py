"""VP-5 Agent Pipeline (Master Spec §38, §17): model/provider registry and
discovery snapshots, safe profile registry metadata + health/availability/
capacity observations, runs and typed role steps, normalized run events,
provider session references (no transcript/credentials), reason-coded router
decisions, run/profile leases (one active lease per profile), bounded retries
with error classification, pause/interruption records, handoff/recovery links.

Никаких credentials/cookies/email/raw auth root/env dump/full payload/transcript
в durable-состоянии. Append-only Audit — существующая таблица VP-1.

Revision ID: 0005_agent_pipeline
Revises: 0004_work_orders
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_agent_pipeline"
down_revision = "0004_work_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- model/provider registry + discovery snapshots ---------------------
    op.create_table(
        "model_registry",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False),  # codex|claude
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("display", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("efforts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("context_capability", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("structured_capability", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("availability", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("provider", "model_id", name="uq_model_provider_id"),
    )
    op.create_index("ix_model_registry_provider", "model_registry", ["provider"])
    op.create_index("ix_model_registry_availability", "model_registry", ["availability"])
    op.create_index("ix_model_registry_discovered_at", "model_registry", ["discovered_at"])

    op.create_table(
        "discovery_snapshots",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("profile_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("models_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_discovery_snapshots_provider", "discovery_snapshots", ["provider"])
    op.create_index("ix_discovery_snapshots_profile_id", "discovery_snapshots", ["profile_id"])
    op.create_index("ix_discovery_snapshots_observed_at", "discovery_snapshots", ["observed_at"])

    # --- role presets + effective selections -------------------------------
    op.create_table(
        "role_presets",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("preset_key", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),  # planner|builder|reviewer
        sa.Column("provider", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("model_pref_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("effort_pref", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("preset_key", "role", name="uq_preset_role"),
    )
    op.create_index("ix_role_presets_preset_key", "role_presets", ["preset_key"])

    # --- safe profile registry metadata (NO credentials/email/raw path) -----
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("alias", sa.String(length=80), nullable=False),  # codex-plus-01
        sa.Column("provider", sa.String(length=20), nullable=False),  # codex|claude
        sa.Column("unix_label", sa.String(length=40), nullable=False, server_default=""),  # safe service label
        sa.Column("auth_root_ref", sa.String(length=120), nullable=False, server_default=""),  # allowlist ref, NOT raw path
        sa.Column("schedulable", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("alias", name="uq_agent_profile_alias"),
    )
    op.create_index("ix_agent_profiles_provider", "agent_profiles", ["provider"])

    op.create_table(
        "profile_states",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("profile_id", sa.String(length=40), nullable=False),
        # UNCONFIGURED|AUTH_REQUIRED|READY|LEASED|COOLDOWN|ERROR|DRAINING|DISABLED|RETIRED
        sa.Column("state", sa.String(length=20), nullable=False, server_default="AUTH_REQUIRED"),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("drain", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("current_role", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("next_action", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("profile_id", name="uq_profile_state_profile"),
    )
    op.create_index("ix_profile_states_state", "profile_states", ["state"])

    op.create_table(
        "profile_health",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("profile_id", sa.String(length=40), nullable=False),
        sa.Column("executable", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("cli_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("auth_status", sa.String(length=30), nullable=False, server_default="UNKNOWN"),
        sa.Column("plan_label", sa.String(length=40), nullable=False, server_default=""),  # verified only
        sa.Column("permissions_ok", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(length=80), nullable=False, server_default=""),  # redacted short code
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_profile_health_profile_id", "profile_health", ["profile_id"])
    op.create_index("ix_profile_health_observed_at", "profile_health", ["observed_at"])

    op.create_table(
        "capacity_observations",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("profile_id", sa.String(length=40), nullable=False),
        # AVAILABLE|LOW|EXHAUSTED|UNKNOWN
        sa.Column("status", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
        sa.Column("five_h_used_pct", sa.Integer(), nullable=True),
        sa.Column("seven_d_used_pct", sa.Integer(), nullable=True),
        sa.Column("reset_at", sa.DateTime(), nullable=True),
        # official_structured|wrapper|observed|manual|unknown
        sa.Column("source", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_capacity_observations_profile_id", "capacity_observations", ["profile_id"])
    op.create_index("ix_capacity_observations_observed_at", "capacity_observations", ["observed_at"])

    # --- runs + typed role steps -------------------------------------------
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("work_order_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        # QUEUED|PREPARING|RUNNING|COLLECTING|SUCCEEDED|RATE_LIMITED|AUTH_REQUIRED|
        # PAUSED|INTERRUPTED|FAILED|CANCELLED|OWNER_REQUIRED
        sa.Column("state", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("preset", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("owner_override_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("dedup_key", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("next_action", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("blocker", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("failure_class", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_state", "runs", ["state"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])
    # Idempotent run create: unique dedup_key when provided (partial index).
    op.create_index("uq_runs_dedup", "runs", ["dedup_key"], unique=True,
                    sqlite_where=sa.text("dedup_key != ''"))

    op.create_table(
        "run_role_steps",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=20), nullable=False),  # planner|builder|reviewer
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("requested_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("effective_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("requested_profile", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("effective_profile", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("session_ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        # reviewer verdict: PASS|REVISE|BLOCKED|OWNER_REQUIRED|INVALID_EVIDENCE
        sa.Column("verdict", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("builder_session_ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("run_id", "role", "seq", name="uq_role_step_run_role_seq"),
    )
    op.create_index("ix_run_role_steps_run_id", "run_role_steps", ["run_id"])
    op.create_index("ix_run_role_steps_role", "run_role_steps", ["role"])
    op.create_index("ix_run_role_steps_status", "run_role_steps", ["status"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_event_type", "run_events", ["event_type"])
    op.create_index("ix_run_events_occurred_at", "run_events", ["occurred_at"])

    # --- provider session references (NO transcript/credentials) -----------
    op.create_table(
        "provider_sessions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("profile_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("session_id", sa.String(length=120), nullable=False),  # provider handle (UUID/thread), not a secret
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_provider_sessions_run_id", "provider_sessions", ["run_id"])
    op.create_index("ix_provider_sessions_provider", "provider_sessions", ["provider"])
    op.create_index("ix_provider_sessions_session_id", "provider_sessions", ["session_id"])

    # --- reason-coded router decisions -------------------------------------
    op.create_table(
        "router_decisions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("requested_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("requested_profile", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("effective_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("effective_profile", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("candidates_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_router_decisions_run_id", "router_decisions", ["run_id"])
    op.create_index("ix_router_decisions_role", "router_decisions", ["role"])
    op.create_index("ix_router_decisions_decided_at", "router_decisions", ["decided_at"])

    # --- run/profile leases: one active lease per profile ------------------
    # Active lease sentinel released_at='' + UNIQUE(profile_id, released_at)
    # → a profile can hold at most one active lease (mirrors worktree_leases).
    op.create_table(
        "run_leases",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("profile_id", sa.String(length=40), nullable=False),
        sa.Column("worktree", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("holder", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("acquired_at", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("heartbeat_at", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("released_at", sa.String(length=30), nullable=False, server_default=""),
        sa.UniqueConstraint("profile_id", "released_at", name="uq_run_lease_profile_active"),
    )
    op.create_index("ix_run_leases_run_id", "run_leases", ["run_id"])
    op.create_index("ix_run_leases_profile_id", "run_leases", ["profile_id"])

    # --- bounded retries with error classification -------------------------
    op.create_table(
        "run_retries",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(length=30), nullable=False, server_default="UNKNOWN"),
        sa.Column("backoff_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_run_retries_run_id", "run_retries", ["run_id"])

    # --- pause/interruption + handoff/recovery links -----------------------
    op.create_table(
        "run_pauses",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),  # pause|resume|interruption|recovery
        sa.Column("reason", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("safe_continuation_ref", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_run_pauses_run_id", "run_pauses", ["run_id"])
    op.create_index("ix_run_pauses_kind", "run_pauses", ["kind"])

    op.create_table(
        "handoff_links",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("handoff_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default=""),  # checkpoint|handoff|recovery
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_handoff_links_run_id", "handoff_links", ["run_id"])
    op.create_index("ix_handoff_links_handoff_package_id", "handoff_links", ["handoff_package_id"])


def downgrade() -> None:
    for idx, tbl in (
        ("ix_handoff_links_handoff_package_id", "handoff_links"),
        ("ix_handoff_links_run_id", "handoff_links"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("handoff_links")
    for idx, tbl in (
        ("ix_run_pauses_kind", "run_pauses"),
        ("ix_run_pauses_run_id", "run_pauses"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("run_pauses")
    op.drop_index("ix_run_retries_run_id", table_name="run_retries")
    op.drop_table("run_retries")
    for idx, tbl in (
        ("ix_run_leases_profile_id", "run_leases"),
        ("ix_run_leases_run_id", "run_leases"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("run_leases")
    for idx, tbl in (
        ("ix_router_decisions_decided_at", "router_decisions"),
        ("ix_router_decisions_role", "router_decisions"),
        ("ix_router_decisions_run_id", "router_decisions"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("router_decisions")
    for idx, tbl in (
        ("ix_provider_sessions_session_id", "provider_sessions"),
        ("ix_provider_sessions_provider", "provider_sessions"),
        ("ix_provider_sessions_run_id", "provider_sessions"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("provider_sessions")
    for idx, tbl in (
        ("ix_run_events_occurred_at", "run_events"),
        ("ix_run_events_event_type", "run_events"),
        ("ix_run_events_run_id", "run_events"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("run_events")
    for idx, tbl in (
        ("ix_run_role_steps_status", "run_role_steps"),
        ("ix_run_role_steps_role", "run_role_steps"),
        ("ix_run_role_steps_run_id", "run_role_steps"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("run_role_steps")
    for idx, tbl in (
        ("uq_runs_dedup", "runs"),
        ("ix_runs_created_at", "runs"),
        ("ix_runs_state", "runs"),
        ("ix_runs_project_id", "runs"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("runs")
    for idx, tbl in (
        ("ix_capacity_observations_observed_at", "capacity_observations"),
        ("ix_capacity_observations_profile_id", "capacity_observations"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("capacity_observations")
    for idx, tbl in (
        ("ix_profile_health_observed_at", "profile_health"),
        ("ix_profile_health_profile_id", "profile_health"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("profile_health")
    op.drop_index("ix_profile_states_state", table_name="profile_states")
    op.drop_table("profile_states")
    op.drop_index("ix_agent_profiles_provider", table_name="agent_profiles")
    op.drop_table("agent_profiles")
    op.drop_index("ix_role_presets_preset_key", table_name="role_presets")
    op.drop_table("role_presets")
    for idx, tbl in (
        ("ix_discovery_snapshots_observed_at", "discovery_snapshots"),
        ("ix_discovery_snapshots_profile_id", "discovery_snapshots"),
        ("ix_discovery_snapshots_provider", "discovery_snapshots"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("discovery_snapshots")
    for idx, tbl in (
        ("ix_model_registry_discovered_at", "model_registry"),
        ("ix_model_registry_availability", "model_registry"),
        ("ix_model_registry_provider", "model_registry"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("model_registry")
