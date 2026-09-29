"""set existing github sources to max_files=0 (no cap, rate-limit paced)

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8
Create Date: 2026-09-29

The max_files default changed from 1000 to 0 (no cap; pacing via GitHub
rate-limit headers). Rows created before that change still carry the old
1000 default and would keep clipping. Since the source-create form never
exposed an explicit max_files value, every 1000 in the table is the old
default - safe to rewrite to 0.
"""

import sqlalchemy as sa
from alembic import op

revision = 'h4i5j6k7l8m9'
down_revision = 'g3h4i5j6k7l8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('UPDATE knowledge_github_source SET max_files = 0 WHERE max_files = 1000')


def downgrade() -> None:
    op.execute('UPDATE knowledge_github_source SET max_files = 1000 WHERE max_files = 0')
