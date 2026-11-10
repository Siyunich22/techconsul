"""Фаза 6: оценка рисков и выводы (раздел 5)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ITEM_STATUS = postgresql.ENUM(name="item_status", create_type=False)
RESULT_AUTHOR = postgresql.ENUM(name="result_author", create_type=False)
REVIEW_STATUS = postgresql.ENUM(name="review_status", create_type=False)


def upgrade() -> None:
    op.add_column("assessment_runs", sa.Column("risks_status", sa.String(length=20), nullable=True))
    op.add_column("assessment_runs", sa.Column("risks_error", sa.Text(), nullable=True))
    op.add_column("assessment_runs", sa.Column("risks_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessment_runs", sa.Column("risks_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "assessment_runs",
        sa.Column("conclusions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "assessment_runs",
        sa.Column("conclusions_review", REVIEW_STATUS, nullable=False, server_default="ai_draft"),
    )
    op.create_table(
        "risk_results",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("group", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", ITEM_STATUS, nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("author", RESULT_AUTHOR, nullable=False),
        sa.Column("review_status", REVIEW_STATUS, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "validator_warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["assessment_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "code", "version"),
    )
    op.create_index(op.f("ix_risk_results_org_id"), "risk_results", ["org_id"], unique=False)
    op.create_index(op.f("ix_risk_results_run_id"), "risk_results", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_risk_results_run_id"), table_name="risk_results")
    op.drop_index(op.f("ix_risk_results_org_id"), table_name="risk_results")
    op.drop_table("risk_results")
    for col in (
        "conclusions_review",
        "conclusions_json",
        "risks_finished_at",
        "risks_started_at",
        "risks_error",
        "risks_status",
    ):
        op.drop_column("assessment_runs", col)
