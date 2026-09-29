"""add allowed_extensions column to knowledge_github_source

Revision ID: g3h4i5j6k7l8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-29

Per-source extension allow-list override. NULL means use the admin
default (chat.api_tools.../rag.github.allowed_extensions) and the
hardcoded fallback in retrieval/github_sync.py.
"""

import sqlalchemy as sa
from alembic import op

revision = 'g3h4i5j6k7l8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('knowledge_github_source') as batch_op:
        batch_op.add_column(sa.Column('allowed_extensions', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('knowledge_github_source') as batch_op:
        batch_op.drop_column('allowed_extensions')
