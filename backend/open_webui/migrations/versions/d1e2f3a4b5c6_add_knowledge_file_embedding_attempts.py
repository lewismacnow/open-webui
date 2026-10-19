"""add knowledge_file.embedding attempts counter

Revision ID: d1e2f3a4b5c6
Revises: kb05mergeheads
Create Date: 2026-10-19 12:00:00.000000

Adds ``attempts`` to ``knowledge_file``: the number of embedding attempts a
file has consumed. A failed embed is re-queued (status back to 'pending',
at the END of the queue) until the counter reaches the configured maximum
(3 by default), after which the file is removed from the knowledge base
instead of being retried forever.

Legacy rows keep ``attempts`` NULL, which the resolver treats as 0 - so
files that failed before this migration are eligible for a fresh attempt.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'kb05mergeheads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('knowledge_file')}
    if 'attempts' not in columns:
        op.add_column(
            'knowledge_file',
            sa.Column('attempts', sa.Integer(), nullable=True, server_default=None),
        )
    # Backfill existing rows so the counter is never NULL for new failures.
    op.execute('UPDATE knowledge_file SET attempts = 0 WHERE attempts IS NULL')


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('knowledge_file')}
    if 'attempts' in columns:
        op.drop_column('knowledge_file', 'attempts')
