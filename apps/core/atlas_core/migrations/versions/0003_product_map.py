"""VP-3 Product Map: intake, briefs, map versions/nodes/edges, decisions,
parking lot, approvals, one-active-VP, idempotency.

Revision ID: 0003_product_map
Revises: 0002_project_workspace
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_product_map"
down_revision = "0002_project_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_intakes",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_product_intakes_project_id", "product_intakes", ["project_id"])
    op.create_index("ix_product_intakes_correlation_id", "product_intakes", ["correlation_id"])
    op.create_index("ix_product_intakes_created_at", "product_intakes", ["created_at"])

    op.create_table(
        "briefs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("envelope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("envelope_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_brief_project_version"),
    )
    op.create_index("ix_briefs_project_id", "briefs", ["project_id"])
    op.create_index("ix_briefs_status", "briefs", ["status"])
    op.create_index("ix_briefs_created_at", "briefs", ["created_at"])

    op.create_table(
        "map_versions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_mapversion_project_version"),
    )
    op.create_index("ix_map_versions_project_id", "map_versions", ["project_id"])
    op.create_index("ix_map_versions_status", "map_versions", ["status"])
    op.create_index("ix_map_versions_created_at", "map_versions", ["created_at"])

    op.create_table(
        "map_nodes",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("map_version_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("node_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("truth_status", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
        sa.Column("evidence_ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("evidence_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("map_version_id", "node_key", name="uq_mapnode_version_key"),
    )
    op.create_index("ix_map_nodes_map_version_id", "map_nodes", ["map_version_id"])
    op.create_index("ix_map_nodes_project_id", "map_nodes", ["project_id"])

    op.create_table(
        "map_edges",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("map_version_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("src_key", sa.String(length=80), nullable=False),
        sa.Column("dst_key", sa.String(length=80), nullable=False),
        sa.Column("edge_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_map_edges_map_version_id", "map_edges", ["map_version_id"])
    op.create_index("ix_map_edges_project_id", "map_edges", ["project_id"])

    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("decision_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("truth_status", sa.String(length=20), nullable=False, server_default="HYPOTHESIS"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "decision_key", name="uq_decision_project_key"),
    )
    op.create_index("ix_decisions_project_id", "decisions", ["project_id"])
    op.create_index("ix_decisions_status", "decisions", ["status"])
    op.create_index("ix_decisions_created_at", "decisions", ["created_at"])

    op.create_table(
        "decision_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("decision_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("to_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_decision_events_decision_id", "decision_events", ["decision_id"])
    op.create_index("ix_decision_events_project_id", "decision_events", ["project_id"])
    op.create_index("ix_decision_events_created_at", "decision_events", ["created_at"])

    op.create_table(
        "parking_items",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("return_condition", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="parked"),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_parking_items_project_id", "parking_items", ["project_id"])
    op.create_index("ix_parking_items_status", "parking_items", ["status"])
    op.create_index("ix_parking_items_created_at", "parking_items", ["created_at"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("brief_id", sa.String(length=40), nullable=False),
        sa.Column("brief_hash", sa.String(length=80), nullable=False),
        sa.Column("map_version_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("envelope_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("decisions_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_approvals_project_id", "approvals", ["project_id"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])

    op.create_table(
        "vp_activations",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("vp_key", sa.String(length=80), nullable=False),
        sa.Column("active_slot", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "active_slot", name="uq_active_vp"),
    )
    op.create_index("ix_vp_activations_project_id", "vp_activations", ["project_id"])
    op.create_index("ix_vp_activations_activated_at", "vp_activations", ["activated_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("scope", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_idempotency_keys_project_id", "idempotency_keys", ["project_id"])
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    for idx, tbl in (
        ("ix_idempotency_keys_created_at", "idempotency_keys"),
        ("ix_idempotency_keys_project_id", "idempotency_keys"),
    ):
        op.drop_index(idx, table_name=tbl)
    op.drop_table("idempotency_keys")
    op.drop_index("ix_vp_activations_activated_at", table_name="vp_activations")
    op.drop_index("ix_vp_activations_project_id", table_name="vp_activations")
    op.drop_table("vp_activations")
    op.drop_index("ix_approvals_created_at", table_name="approvals")
    op.drop_index("ix_approvals_project_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_parking_items_created_at", table_name="parking_items")
    op.drop_index("ix_parking_items_status", table_name="parking_items")
    op.drop_index("ix_parking_items_project_id", table_name="parking_items")
    op.drop_table("parking_items")
    op.drop_index("ix_decision_events_created_at", table_name="decision_events")
    op.drop_index("ix_decision_events_project_id", table_name="decision_events")
    op.drop_index("ix_decision_events_decision_id", table_name="decision_events")
    op.drop_table("decision_events")
    op.drop_index("ix_decisions_created_at", table_name="decisions")
    op.drop_index("ix_decisions_status", table_name="decisions")
    op.drop_index("ix_decisions_project_id", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_map_edges_project_id", table_name="map_edges")
    op.drop_index("ix_map_edges_map_version_id", table_name="map_edges")
    op.drop_table("map_edges")
    op.drop_index("ix_map_nodes_project_id", table_name="map_nodes")
    op.drop_index("ix_map_nodes_map_version_id", table_name="map_nodes")
    op.drop_table("map_nodes")
    op.drop_index("ix_map_versions_created_at", table_name="map_versions")
    op.drop_index("ix_map_versions_status", table_name="map_versions")
    op.drop_index("ix_map_versions_project_id", table_name="map_versions")
    op.drop_table("map_versions")
    op.drop_index("ix_briefs_created_at", table_name="briefs")
    op.drop_index("ix_briefs_status", table_name="briefs")
    op.drop_index("ix_briefs_project_id", table_name="briefs")
    op.drop_table("briefs")
    op.drop_index("ix_product_intakes_created_at", table_name="product_intakes")
    op.drop_index("ix_product_intakes_correlation_id", table_name="product_intakes")
    op.drop_index("ix_product_intakes_project_id", table_name="product_intakes")
    op.drop_table("product_intakes")
