"""phase 3: metric snapshots

Revision ID: b7c1e4a9d2f0
Revises: 5373db8a3ee4
Create Date: 2026-10-10 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c1e4a9d2f0'
down_revision: str | None = '5373db8a3ee4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('metric_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('analysis_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('baseline_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('baseline_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('model_version', sa.String(), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
    nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_metric_snapshots_repo_analysis_at', 'metric_snapshots',
    ['repository_id', 'analysis_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_metric_snapshots_repo_analysis_at', table_name='metric_snapshots')
    op.drop_table('metric_snapshots')
