"""add api_token_usage

Revision ID: 5643b1c2d4f5
Revises: 56359461a091
Create Date: 2026-08-27 10:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5643b1c2d4f5'
down_revision: Union[str, None] = '56359461a091'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'api_token_usage' not in tables:
        op.create_table(
            'api_token_usage',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True, unique=True),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('api_key_id', sa.Text(), nullable=True),
            sa.Column('model_id', sa.Text(), nullable=False),
            sa.Column('endpoint', sa.Text(), nullable=False),
            sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('status_code', sa.Integer(), nullable=False, server_default='200'),
            sa.Column('trace_id', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )

    indexes = (
        {idx['name'] for idx in inspector.get_indexes('api_token_usage')} if 'api_token_usage' in tables else set()
    )

    if 'ix_api_token_usage_user_id' not in indexes:
        op.create_index('ix_api_token_usage_user_id', 'api_token_usage', ['user_id'])
    if 'ix_api_token_usage_api_key_id' not in indexes:
        op.create_index('ix_api_token_usage_api_key_id', 'api_token_usage', ['api_key_id'])
    if 'ix_api_token_usage_model_id' not in indexes:
        op.create_index('ix_api_token_usage_model_id', 'api_token_usage', ['model_id'])
    if 'ix_api_token_usage_user_created' not in indexes:
        op.create_index('ix_api_token_usage_user_created', 'api_token_usage', ['user_id', 'created_at'])
    if 'ix_api_token_usage_apikey_created' not in indexes:
        op.create_index('ix_api_token_usage_apikey_created', 'api_token_usage', ['api_key_id', 'created_at'])
    if 'ix_api_token_usage_model_created' not in indexes:
        op.create_index('ix_api_token_usage_model_created', 'api_token_usage', ['model_id', 'created_at'])


def downgrade() -> None:
    # Best-effort — leave the rows in place if the table isn't there.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'api_token_usage' in inspector.get_table_names():
        op.drop_table('api_token_usage')
