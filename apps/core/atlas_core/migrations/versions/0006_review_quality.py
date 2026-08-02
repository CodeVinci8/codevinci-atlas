"""VP-6 Review & Quality (Master Spec §18, §39): immutable SHA-bound ReviewPackage,
findings, QualityReport, impact assessments, Evidence Cache, manual audit, waivers,
fix-loop; append-only profile-registry reconciliation record (VP6-D2).

Никаких credentials/cookies/email/raw auth path/transcript в durable-состоянии.
Content-hash — sha256 над canonical-JSON. Таблицы создаёт только Alembic.

Revision ID: 0006_review_quality
Revises: 0005_agent_pipeline
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_review_quality"
down_revision = "0005_agent_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- review_packages (immutable, SHA-bound) ----------------------------
    op.create_table(
        "review_packages",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("work_order_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("vp_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("wo_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("base_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("head_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("spec_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("brief_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("map_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("diff_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("artifact_hashes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("acceptance_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("claims_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("impact_class", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("checks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("limitations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("grant_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("freshness_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="valid"),
        sa.Column("invalid_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("invalid_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="core"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_packages_project_id", "review_packages", ["project_id"])
    op.create_index("ix_review_packages_run_id", "review_packages", ["run_id"])
    op.create_index("ix_review_packages_vp_key", "review_packages", ["vp_key"])
    op.create_index("ix_review_packages_content_hash", "review_packages", ["content_hash"])
    op.create_index("ix_review_packages_status", "review_packages", ["status"])
    op.create_index("ix_review_packages_created_at", "review_packages", ["created_at"])

    # --- quality_findings --------------------------------------------------
    op.create_table(
        "quality_findings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("gate", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("code", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("severity", sa.String(length=12), nullable=False, server_default="minor"),
        sa.Column("criterion", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("freshness", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("waived", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_quality_findings_review_package_id", "quality_findings", ["review_package_id"])
    op.create_index("ix_quality_findings_project_id", "quality_findings", ["project_id"])
    op.create_index("ix_quality_findings_gate", "quality_findings", ["gate"])
    op.create_index("ix_quality_findings_created_at", "quality_findings", ["created_at"])

    # --- quality_reports ---------------------------------------------------
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("claims_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("gate_fired", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("sufficiency_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("stop_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("blocking_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("findings_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="reviewer"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_quality_reports_review_package_id", "quality_reports", ["review_package_id"])
    op.create_index("ix_quality_reports_project_id", "quality_reports", ["project_id"])
    op.create_index("ix_quality_reports_verdict", "quality_reports", ["verdict"])
    op.create_index("ix_quality_reports_created_at", "quality_reports", ["created_at"])

    # --- impact_assessments ------------------------------------------------
    op.create_table(
        "impact_assessments",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("impact_class", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("check_groups_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("risk_trigger", sa.Text(), nullable=False, server_default=""),
        sa.Column("full_regression", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_impact_assessments_review_package_id", "impact_assessments", ["review_package_id"])
    op.create_index("ix_impact_assessments_project_id", "impact_assessments", ["project_id"])
    op.create_index("ix_impact_assessments_impact_class", "impact_assessments", ["impact_class"])
    op.create_index("ix_impact_assessments_created_at", "impact_assessments", ["created_at"])

    # --- evidence_cache ----------------------------------------------------
    op.create_table(
        "evidence_cache",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("cache_key", sa.String(length=120), nullable=False),
        sa.Column("sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("command", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("command_version", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("input_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("environment", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("scope", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reuse_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_reused_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("cache_key", name="uq_evidence_cache_key"),
    )
    op.create_index("ix_evidence_cache_cache_key", "evidence_cache", ["cache_key"])
    op.create_index("ix_evidence_cache_created_at", "evidence_cache", ["created_at"])

    # --- manual_audits -----------------------------------------------------
    op.create_table(
        "manual_audits",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("target", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("scope", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("findings_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_manual_audits_review_package_id", "manual_audits", ["review_package_id"])
    op.create_index("ix_manual_audits_project_id", "manual_audits", ["project_id"])
    op.create_index("ix_manual_audits_created_at", "manual_audits", ["created_at"])

    # --- waivers -----------------------------------------------------------
    op.create_table(
        "waivers",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("finding_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="owner"),
        sa.Column("expiry", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("review_condition", sa.Text(), nullable=False, server_default=""),
        sa.Column("audit_ref", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("waivable", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("rejected_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_waivers_review_package_id", "waivers", ["review_package_id"])
    op.create_index("ix_waivers_finding_id", "waivers", ["finding_id"])
    op.create_index("ix_waivers_project_id", "waivers", ["project_id"])
    op.create_index("ix_waivers_waivable", "waivers", ["waivable"])
    op.create_index("ix_waivers_created_at", "waivers", ["created_at"])

    # --- fix_loops ---------------------------------------------------------
    op.create_table(
        "fix_loops",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("review_package_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("verdict", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("fix_work_order_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fix_loops_review_package_id", "fix_loops", ["review_package_id"])
    op.create_index("ix_fix_loops_run_id", "fix_loops", ["run_id"])
    op.create_index("ix_fix_loops_project_id", "fix_loops", ["project_id"])
    op.create_index("ix_fix_loops_created_at", "fix_loops", ["created_at"])

    # --- profile_registry_reconciles (append-only, VP6-D2) -----------------
    op.create_table(
        "profile_registry_reconciles",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("by_provider_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("actor", sa.String(length=80), nullable=False, server_default="deploy"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_profile_registry_reconciles_created_at",
                    "profile_registry_reconciles", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_profile_registry_reconciles_created_at",
                  table_name="profile_registry_reconciles")
    op.drop_table("profile_registry_reconciles")
    for idx in ("ix_fix_loops_created_at", "ix_fix_loops_project_id",
                "ix_fix_loops_run_id", "ix_fix_loops_review_package_id"):
        op.drop_index(idx, table_name="fix_loops")
    op.drop_table("fix_loops")
    for idx in ("ix_waivers_created_at", "ix_waivers_waivable", "ix_waivers_project_id",
                "ix_waivers_finding_id", "ix_waivers_review_package_id"):
        op.drop_index(idx, table_name="waivers")
    op.drop_table("waivers")
    for idx in ("ix_manual_audits_created_at", "ix_manual_audits_project_id",
                "ix_manual_audits_review_package_id"):
        op.drop_index(idx, table_name="manual_audits")
    op.drop_table("manual_audits")
    for idx in ("ix_evidence_cache_created_at", "ix_evidence_cache_cache_key"):
        op.drop_index(idx, table_name="evidence_cache")
    op.drop_table("evidence_cache")
    for idx in ("ix_impact_assessments_created_at", "ix_impact_assessments_impact_class",
                "ix_impact_assessments_project_id", "ix_impact_assessments_review_package_id"):
        op.drop_index(idx, table_name="impact_assessments")
    op.drop_table("impact_assessments")
    for idx in ("ix_quality_reports_created_at", "ix_quality_reports_verdict",
                "ix_quality_reports_project_id", "ix_quality_reports_review_package_id"):
        op.drop_index(idx, table_name="quality_reports")
    op.drop_table("quality_reports")
    for idx in ("ix_quality_findings_created_at", "ix_quality_findings_gate",
                "ix_quality_findings_project_id", "ix_quality_findings_review_package_id"):
        op.drop_index(idx, table_name="quality_findings")
    op.drop_table("quality_findings")
    for idx in ("ix_review_packages_created_at", "ix_review_packages_status",
                "ix_review_packages_content_hash", "ix_review_packages_vp_key",
                "ix_review_packages_run_id", "ix_review_packages_project_id"):
        op.drop_index(idx, table_name="review_packages")
    op.drop_table("review_packages")
