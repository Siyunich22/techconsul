"""Фаза 7: правки экспертов (автор версии), комментарии, LLM-ревью

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-10 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("section_results", "risk_results"):
        op.add_column(table, sa.Column("edited_by", sa.Uuid(), nullable=True))
        op.create_foreign_key(f"{table}_edited_by_fkey", table, "users", ["edited_by"], ["id"], ondelete="SET NULL")
    op.add_column(
        "assessment_runs",
        sa.Column(
            "conclusions_history", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
    )
    op.add_column("assessment_runs", sa.Column("conclusions_edited_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "assessment_runs_conclusions_edited_by_fkey",
        "assessment_runs",
        "users",
        ["conclusions_edited_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("assessment_runs", sa.Column("llm_review_status", sa.String(length=20), nullable=True))
    op.add_column(
        "assessment_runs", sa.Column("llm_review_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("assessment_runs", sa.Column("llm_review_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "review_comments",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ref", sa.String(length=20), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["assessment_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_comments_org_id"), "review_comments", ["org_id"], unique=False)
    op.create_index("ix_review_comments_run_ref", "review_comments", ["run_id", "ref"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_review_comments_run_ref", table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_org_id"), table_name="review_comments")
    op.drop_table("review_comments")
    for col in ("llm_review_at", "llm_review_json", "llm_review_status"):
        op.drop_column("assessment_runs", col)
    op.drop_constraint("assessment_runs_conclusions_edited_by_fkey", "assessment_runs", type_="foreignkey")
    op.drop_column("assessment_runs", "conclusions_edited_by")
    op.drop_column("assessment_runs", "conclusions_history")
    for table in ("risk_results", "section_results"):
        op.drop_constraint(f"{table}_edited_by_fkey", table, type_="foreignkey")
        op.drop_column(table, "edited_by")
