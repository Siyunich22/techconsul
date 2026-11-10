"""Фаза 8: версии отчёта (DOCX/PDF)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.Enum("draft", "released", name="report_status"), nullable=False),
        sa.Column(
            "render_status",
            sa.Enum("queued", "rendering", "ready", "failed", name="render_status"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("docx_key", sa.String(length=1024), nullable=True),
        sa.Column("pdf_key", sa.String(length=1024), nullable=True),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["assessment_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "number"),
    )
    op.create_index(op.f("ix_report_versions_org_id"), "report_versions", ["org_id"], unique=False)
    op.create_index(op.f("ix_report_versions_project_id"), "report_versions", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_report_versions_project_id"), table_name="report_versions")
    op.drop_index(op.f("ix_report_versions_org_id"), table_name="report_versions")
    op.drop_table("report_versions")
    sa.Enum(name="render_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="report_status").drop(op.get_bind(), checkfirst=True)
