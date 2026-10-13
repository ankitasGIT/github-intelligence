"""phase 4: findings, recommendations, health and trend on snapshots

Revision ID: d4a8f2c61e57
Revises: b7c1e4a9d2f0
Create Date: 2026-10-12 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a8f2c61e57'
down_revision: str | None = 'b7c1e4a9d2f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('metric_snapshots', sa.Column('detection_version', sa.String(), nullable=True))
    op.add_column('metric_snapshots', sa.Column('health_version', sa.String(), nullable=True))
    op.add_column('metric_snapshots',
    sa.Column('health', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('metric_snapshots',
    sa.Column('trend', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('milestones',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table('findings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('rule', sa.String(), nullable=False),
    sa.Column('rule_version', sa.String(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('confidence_detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('summary', sa.String(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('affected_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metric_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
    nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
    sa.ForeignKeyConstraint(['snapshot_id'], ['metric_snapshots.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('snapshot_id', 'key', name='uq_findings_snapshot_key')
    )
    op.create_index(op.f('ix_findings_repository_id'), 'findings', ['repository_id'],
    unique=False)

    op.create_table('recommendations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('finding_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('detail', sa.String(), nullable=False),
    sa.Column('item_numbers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
    nullable=False),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['metric_snapshots.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendations_finding_id'), 'recommendations', ['finding_id'],
    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recommendations_finding_id'), table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index(op.f('ix_findings_repository_id'), table_name='findings')
    op.drop_table('findings')
    op.drop_column('milestones', 'created_at')
    op.drop_column('metric_snapshots', 'trend')
    op.drop_column('metric_snapshots', 'health')
    op.drop_column('metric_snapshots', 'health_version')
    op.drop_column('metric_snapshots', 'detection_version')
