"""VP-7 Autonomy, GitHub & Time Machine (Master Spec §19, §20, §21, §23):
durable capability grants (раздельные capabilities, optimistic version),
Emergency Stop (append-only lifecycle, переживает рестарт), immutable
content-addressed checkpoints Time Machine, GitHub delivery/merge-gate записи;
+ колонка ``source`` в ``profile_health`` для provenance auth-health.

Никаких token/cookie/email/raw auth path/transcript в durable-состоянии.
content_hash — sha256 над canonical-JSON. Таблицы создаёт только Alembic.

Revision ID: 0007_autonomy_github_time_machine
Revises: 0006_review_quality
Create Date: 2026-08-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_autonomy_github_time_machine"
down_revision = "0006_review_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- grants (durable capability grants, optimistic version) -------------
    op.create_table(
        "grants",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("owner_ref", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("environment", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="GUIDED"),
        sa.Column("allowed_repos_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("allowed_bases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("workspace_allowlist_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("branch_rules_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("command_restrictions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("budget_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("revoke_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("audit_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_grants_project_id", "grants", ["project_id"])
    op.create_index("ix_grants_mode", "grants", ["mode"])
    op.create_index("ix_grants_state", "grants", ["state"])
    op.create_index("ix_grants_content_hash", "grants", ["content_hash"])
    op.create_index("ix_grants_created_at", "grants", ["created_at"])

    # --- emergency_stops (append-only lifecycle) ---------------------------
    op.create_table(
        "emergency_stops",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("action", sa.String(length=12), nullable=False, server_default="ENGAGED"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("interrupted_runs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("released_leases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_emergency_stops_active", "emergency_stops", ["active"])
    op.create_index("ix_emergency_stops_created_at", "emergency_stops", ["created_at"])

    # --- checkpoints (immutable content-addressed) -------------------------
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("work_order_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("db_revision", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("base_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("head_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("worktree_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("patch_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("artifact_hashes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("profile_alias", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("effort", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("session_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("grant_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("grant_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("test_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("handoff_ref", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("cause", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_checkpoints_project_id", "checkpoints", ["project_id"])
    op.create_index("ix_checkpoints_vp_key", "checkpoints", ["vp_key"])
    op.create_index("ix_checkpoints_run_id", "checkpoints", ["run_id"])
    op.create_index("ix_checkpoints_content_hash", "checkpoints", ["content_hash"])
    op.create_index("ix_checkpoints_created_at", "checkpoints", ["created_at"])

    # --- github_deliveries (merge-gate/delivery, token НЕ хранится) --------
    op.create_table(
        "github_deliveries",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("repo", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("base", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("head_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("pr_state", sa.String(length=20), nullable=False, server_default="NONE"),
        sa.Column("checks_state", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
        sa.Column("checks_head_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("mergeable", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("merge_state", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("review_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("quality_report_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("gate_decision", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("gate_reason", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("grant_id", sa.String(length=40), nullable=False, server_default=""),
        # Fix3: полный ключ идемпотентности (repo+base+branch+head) + UNIQUE-индекс.
        sa.Column("idempotency_key", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_github_deliveries_project_id", "github_deliveries", ["project_id"])
    op.create_index("ix_github_deliveries_repo", "github_deliveries", ["repo"])
    op.create_index("ux_github_deliveries_idempotency_key", "github_deliveries",
                    ["idempotency_key"], unique=True)
    op.create_index("ix_github_deliveries_created_at", "github_deliveries", ["created_at"])

    # --- profile_health.source (provenance auth-health, VP-7) --------------
    op.add_column("profile_health",
                  sa.Column("source", sa.String(length=40), nullable=False, server_default=""))

    # --- capacity_observations: подтверждённый план + полные окна + error_code (VP-7) ---
    op.add_column("capacity_observations",
                  sa.Column("plan", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("capacity_observations",
                  sa.Column("five_h_reset_at", sa.DateTime(), nullable=True))
    op.add_column("capacity_observations",
                  sa.Column("seven_d_reset_at", sa.DateTime(), nullable=True))
    op.add_column("capacity_observations",
                  sa.Column("error_code", sa.String(length=60), nullable=False, server_default=""))
    op.add_column("capacity_observations",
                  sa.Column("windows_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("capacity_observations") as b:
        for col in ("windows_json", "error_code", "seven_d_reset_at", "five_h_reset_at", "plan"):
            b.drop_column(col)
    with op.batch_alter_table("profile_health") as b:
        b.drop_column("source")
    for idx in ("ix_github_deliveries_created_at", "ux_github_deliveries_idempotency_key",
                "ix_github_deliveries_repo", "ix_github_deliveries_project_id"):
        op.drop_index(idx, table_name="github_deliveries")
    op.drop_table("github_deliveries")
    for idx in ("ix_checkpoints_created_at", "ix_checkpoints_content_hash",
                "ix_checkpoints_run_id", "ix_checkpoints_vp_key", "ix_checkpoints_project_id"):
        op.drop_index(idx, table_name="checkpoints")
    op.drop_table("checkpoints")
    for idx in ("ix_emergency_stops_created_at", "ix_emergency_stops_active"):
        op.drop_index(idx, table_name="emergency_stops")
    op.drop_table("emergency_stops")
    for idx in ("ix_grants_created_at", "ix_grants_content_hash", "ix_grants_state",
                "ix_grants_mode", "ix_grants_project_id"):
        op.drop_index(idx, table_name="grants")
    op.drop_table("grants")
