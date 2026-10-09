"""metadata_proposal: keywords + extra (category/doc_type/audience) columns

Revision ID: b8c9d0e1f2a3
Revises: h4i5j6k7l8m9
Create Date: 2026-09-29

Expanded enrichment attribute set: keywords (list, feeds BM25 retrieval),
category (hierarchical path), doc_type, audience (stored together in
proposed_extra / previous_extra JSON).
"""

import sqlalchemy as sa
from alembic import op

revision = 'b8c9d0e1f2a3'
down_revision = 'h4i5j6k7l8m9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('metadata_proposal') as batch_op:
        batch_op.add_column(sa.Column('proposed_keywords', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('proposed_extra', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('previous_keywords', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('previous_extra', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('metadata_proposal') as batch_op:
        batch_op.drop_column('previous_extra')
        batch_op.drop_column('previous_keywords')
        batch_op.drop_column('proposed_extra')
        batch_op.drop_column('proposed_keywords')
