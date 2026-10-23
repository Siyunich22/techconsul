"""Фаза 4: паспорт проекта

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

passport_status = postgresql.ENUM('extracting', 'draft', 'confirmed', 'failed', name='passport_status', create_type=False)


def upgrade() -> None:
    passport_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        'passports',
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('status', passport_status, nullable=False),
        sa.Column('data_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('ai_data_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('discrepancies_json', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('sources_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('warnings_json', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('progress_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('run_id', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('confirmed_by', sa.Uuid(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_passports_confirmed_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_passports_org_id_organizations'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_passports_project_id_projects'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['started_by'], ['users.id'], name=op.f('fk_passports_started_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_passports_updated_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_passports')),
        sa.UniqueConstraint('project_id', name=op.f('uq_passports_project_id')),
    )
    op.create_index(op.f('ix_passports_org_id'), 'passports', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_passports_org_id'), table_name='passports')
    op.drop_table('passports')
    passport_status.drop(op.get_bind(), checkfirst=True)
