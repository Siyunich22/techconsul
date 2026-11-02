"""Повторное использование результатов пунктов (отпечаток входных данных), выжимки документов

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("digest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("section_results", sa.Column("fingerprint", sa.String(length=64), nullable=True))
    op.add_column("section_results", sa.Column("reused_from_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "section_results_reused_from_id_fkey",
        "section_results",
        "section_results",
        ["reused_from_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("section_results_reused_from_id_fkey", "section_results", type_="foreignkey")
    op.drop_column("section_results", "reused_from_id")
    op.drop_column("section_results", "fingerprint")
    op.drop_column("documents", "digest_json")
