"""metadata_scan: test_run, test_preview, reembedded columns

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('metadata_scan') as batch_op:
        batch_op.add_column(sa.Column('test_run', sa.BigInteger(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('test_preview', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('reembedded', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('metadata_scan') as batch_op:
        batch_op.drop_column('reembedded')
        batch_op.drop_column('test_preview')
        batch_op.drop_column('test_run')
