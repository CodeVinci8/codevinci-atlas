"""VP-2 Project Workspace: projects, git_baselines, worktrees, worktree_leases.

Revision ID: 0002_project_workspace
Revises: 0001_initial
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_project_workspace"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source_location", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ref", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="connected"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_projects_source_kind", "projects", ["source_kind"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_created_at", "projects", ["created_at"])

    op.create_table(
        "git_baselines",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("head", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("remotes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("porcelain_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("porcelain_truncated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("tracked_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tracked_changes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("untracked", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("instructions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("package_managers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("baseline_commands_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("secret_scan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_git_baselines_project_id", "git_baselines", ["project_id"])
    op.create_index("ix_git_baselines_observed_at", "git_baselines", ["observed_at"])

    op.create_table(
        "worktrees",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_worktrees_project_id", "worktrees", ["project_id"])
    op.create_index("ix_worktrees_status", "worktrees", ["status"])
    op.create_index("ix_worktrees_created_at", "worktrees", ["created_at"])

    op.create_table(
        "worktree_leases",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("worktree", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="builder"),
        sa.Column("holder", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("acquired_at", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.String(length=30), nullable=False),
        sa.Column("heartbeat_at", sa.String(length=30), nullable=False),
        sa.Column("released_at", sa.String(length=30), nullable=False, server_default=""),
        sa.UniqueConstraint("worktree", "released_at", name="uq_worktree_active"),
    )
    op.create_index("ix_worktree_leases_project_id", "worktree_leases", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_worktree_leases_project_id", table_name="worktree_leases")
    op.drop_table("worktree_leases")
    op.drop_index("ix_worktrees_created_at", table_name="worktrees")
    op.drop_index("ix_worktrees_status", table_name="worktrees")
    op.drop_index("ix_worktrees_project_id", table_name="worktrees")
    op.drop_table("worktrees")
    op.drop_index("ix_git_baselines_observed_at", table_name="git_baselines")
    op.drop_index("ix_git_baselines_project_id", table_name="git_baselines")
    op.drop_table("git_baselines")
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_source_kind", table_name="projects")
    op.drop_table("projects")
