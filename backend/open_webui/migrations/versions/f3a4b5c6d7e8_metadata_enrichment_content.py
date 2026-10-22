"""metadata enrichment: content proposals + counters

Revision ID: f3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-10-19 14:00:00.000000

Extends the metadata-enrichment tables for the automatic (no-approval)
enrichment flow:

- ``metadata_proposal.proposed_content``       - the model's improved
  document body (markdown), applied to the file and re-embedded.
- ``metadata_proposal.previous_content_excerpt`` - a bounded excerpt of the
  body before the change, for the test-run preview (before/after).
- ``metadata_proposal.content_changed``        - whether the body was
  actually rewritten.
- ``metadata_scan.content_updated``            - files whose body was
  rewritten.
- ``metadata_scan.titles_updated``             - files whose title was
  improved.
- ``metadata_scan.ignored``                    - files auto-ignored as
  low value.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c['name'] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has('metadata_proposal', 'proposed_content'):
        op.add_column('metadata_proposal', sa.Column('proposed_content', sa.Text(), nullable=True))
    if not _has('metadata_proposal', 'previous_content_excerpt'):
        op.add_column('metadata_proposal', sa.Column('previous_content_excerpt', sa.Text(), nullable=True))
    if not _has('metadata_proposal', 'content_changed'):
        op.add_column(
            'metadata_proposal',
            sa.Column('content_changed', sa.BigInteger(), nullable=True, server_default='0'),
        )
    for column in ('content_updated', 'titles_updated', 'ignored'):
        if not _has('metadata_scan', column):
            op.add_column(
                'metadata_scan',
                sa.Column(column, sa.BigInteger(), nullable=True, server_default='0'),
            )


def downgrade() -> None:
    for column in ('ignored', 'titles_updated', 'content_updated'):
        if _has('metadata_scan', column):
            op.drop_column('metadata_scan', column)
    for column in ('content_changed', 'previous_content_excerpt', 'proposed_content'):
        if _has('metadata_proposal', column):
            op.drop_column('metadata_proposal', column)
