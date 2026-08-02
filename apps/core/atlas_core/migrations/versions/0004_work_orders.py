"""VP-4 Work Orders & Context: versioned VP specs, work orders + lifecycle,
transition history, optimizer decisions, immutable job packages, checkpoints,
immutable handoff packages, ack/reject, rotation records.

Revision ID: 0004_work_orders
Revises: 0003_product_map
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_work_orders"
down_revision = "0003_product_map"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vp_specs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("vp_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("approval_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("brief_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("brief_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("map_version_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("map_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("envelope_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("decisions_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("baseline_branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("baseline_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "vp_key", "version", name="uq_vpspec_project_vp_version"),
    )
    op.create_index("ix_vp_specs_project_id", "vp_specs", ["project_id"])
    op.create_index("ix_vp_specs_vp_key", "vp_specs", ["vp_key"])
    op.create_index("ix_vp_specs_status", "vp_specs", ["status"])
    op.create_index("ix_vp_specs_created_at", "vp_specs", ["created_at"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("vp_spec_id", sa.String(length=40), nullable=False),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("wo_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="builder"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="spec"),
        sa.Column("approval_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("spec_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("spec_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("brief_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("map_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("envelope_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("baseline_branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("baseline_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("lease_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("writer_holder", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_work_orders_project_id", "work_orders", ["project_id"])
    op.create_index("ix_work_orders_vp_spec_id", "work_orders", ["vp_spec_id"])
    op.create_index("ix_work_orders_status", "work_orders", ["status"])
    op.create_index("ix_work_orders_created_at", "work_orders", ["created_at"])

    op.create_table(
        "work_order_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("work_order_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("to_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_work_order_events_work_order_id", "work_order_events", ["work_order_id"])
    op.create_index("ix_work_order_events_project_id", "work_order_events", ["project_id"])
    op.create_index("ix_work_order_events_created_at", "work_order_events", ["created_at"])

    op.create_table(
        "optimizer_decisions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("vp_spec_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("affected_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("exact_next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("inputs_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_optimizer_decisions_project_id", "optimizer_decisions", ["project_id"])
    op.create_index("ix_optimizer_decisions_decision", "optimizer_decisions", ["decision"])
    op.create_index("ix_optimizer_decisions_created_at", "optimizer_decisions", ["created_at"])

    op.create_table(
        "job_packages",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("work_order_id", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("counts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("compact", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_job_packages_project_id", "job_packages", ["project_id"])
    op.create_index("ix_job_packages_work_order_id", "job_packages", ["work_order_id"])
    op.create_index("ix_job_packages_created_at", "job_packages", ["created_at"])

    op.create_table(
        "wo_checkpoints",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("work_order_id", sa.String(length=40), nullable=False),
        sa.Column("job_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("baseline_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("current_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("changed_files_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("commands_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("failures_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("completed_criteria_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("remaining_criteria_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("decisions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("impacted_checks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("artifact_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("lease_state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("writer_holder", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("exact_next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("cause", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wo_checkpoints_project_id", "wo_checkpoints", ["project_id"])
    op.create_index("ix_wo_checkpoints_work_order_id", "wo_checkpoints", ["work_order_id"])
    op.create_index("ix_wo_checkpoints_created_at", "wo_checkpoints", ["created_at"])

    op.create_table(
        "handoff_packages",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("vp_spec_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("work_order_id", sa.String(length=40), nullable=False),
        sa.Column("job_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("baseline_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("current_head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("spec_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("brief_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("map_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("approval_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="issued"),
        sa.Column("compact", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_handoff_packages_project_id", "handoff_packages", ["project_id"])
    op.create_index("ix_handoff_packages_work_order_id", "handoff_packages", ["work_order_id"])
    op.create_index("ix_handoff_packages_status", "handoff_packages", ["status"])
    op.create_index("ix_handoff_packages_created_at", "handoff_packages", ["created_at"])

    op.create_table(
        "handoff_acks",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("handoff_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=12), nullable=False, server_default="ACK"),
        sa.Column("reason_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("ack_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("baseline_ack", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="consumer"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_handoff_acks_handoff_id", "handoff_acks", ["handoff_id"])
    op.create_index("ix_handoff_acks_project_id", "handoff_acks", ["project_id"])
    op.create_index("ix_handoff_acks_created_at", "handoff_acks", ["created_at"])

    op.create_table(
        "rotation_records",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("work_order_id", sa.String(length=40), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("handoff_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("next_profile_request", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("lease_released", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("one_writer_ok", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("steps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="started"),
        sa.Column("exact_next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rotation_records_project_id", "rotation_records", ["project_id"])
    op.create_index("ix_rotation_records_work_order_id", "rotation_records", ["work_order_id"])
    op.create_index("ix_rotation_records_created_at", "rotation_records", ["created_at"])


def downgrade() -> None:
    for idx, tbl in (
        ("ix_rotation_records_created_at", "rotation_records"),
        ("ix_rotation_records_work_order_id", "rotation_records"),
        ("ix_rotation_records_project_id", "rotation_records"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("rotation_records")
    for idx, tbl in (
        ("ix_handoff_acks_created_at", "handoff_acks"),
        ("ix_handoff_acks_project_id", "handoff_acks"),
        ("ix_handoff_acks_handoff_id", "handoff_acks"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("handoff_acks")
    for idx, tbl in (
        ("ix_handoff_packages_created_at", "handoff_packages"),
        ("ix_handoff_packages_status", "handoff_packages"),
        ("ix_handoff_packages_work_order_id", "handoff_packages"),
        ("ix_handoff_packages_project_id", "handoff_packages"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("handoff_packages")
    for idx, tbl in (
        ("ix_wo_checkpoints_created_at", "wo_checkpoints"),
        ("ix_wo_checkpoints_work_order_id", "wo_checkpoints"),
        ("ix_wo_checkpoints_project_id", "wo_checkpoints"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("wo_checkpoints")
    for idx, tbl in (
        ("ix_job_packages_created_at", "job_packages"),
        ("ix_job_packages_work_order_id", "job_packages"),
        ("ix_job_packages_project_id", "job_packages"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("job_packages")
    for idx, tbl in (
        ("ix_optimizer_decisions_created_at", "optimizer_decisions"),
        ("ix_optimizer_decisions_decision", "optimizer_decisions"),
        ("ix_optimizer_decisions_project_id", "optimizer_decisions"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("optimizer_decisions")
    for idx, tbl in (
        ("ix_work_order_events_created_at", "work_order_events"),
        ("ix_work_order_events_project_id", "work_order_events"),
        ("ix_work_order_events_work_order_id", "work_order_events"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("work_order_events")
    for idx, tbl in (
        ("ix_work_orders_created_at", "work_orders"),
        ("ix_work_orders_status", "work_orders"),
        ("ix_work_orders_vp_spec_id", "work_orders"),
        ("ix_work_orders_project_id", "work_orders"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("work_orders")
    for idx, tbl in (
        ("ix_vp_specs_created_at", "vp_specs"),
        ("ix_vp_specs_status", "vp_specs"),
        ("ix_vp_specs_vp_key", "vp_specs"),
        ("ix_vp_specs_project_id", "vp_specs"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("vp_specs")
