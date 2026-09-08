"""add service_api_key

Revision ID: 7c8d9e0f1a2b
Revises: 5643b1c2d4f5
Create Date: 2026-09-08 10:00:00.000000

Group-bound service API keys (see models/service_api_key.py). Purely
additive: new ``service_api_key`` table + a nullable, indexed
``service_key_id`` column on ``api_token_usage`` for usage attribution.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7c8d9e0f1a2b'
down_revision: Union[str, None] = '5643b1c2d4f5'  # the chain's single head
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SERVICE_KEY_INDEXES = (
    'ix_service_api_key_group_id',
    'ix_service_api_key_key_prefix',
    'ix_service_api_key_revoked_at',
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'service_api_key' not in tables:
        op.create_table(
            'service_api_key',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True, unique=True),
            sa.Column('group_id', sa.Text(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('key_prefix', sa.Text(), nullable=False, unique=True),
            sa.Column('key_hash', sa.Text(), nullable=False),
            sa.Column('created_by', sa.Text(), nullable=False),
            sa.Column('expires_at', sa.BigInteger(), nullable=True),
            sa.Column('ip_whitelist', sa.JSON(), nullable=True),
            sa.Column('last_used_at', sa.BigInteger(), nullable=True),
            sa.Column('revoked_at', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(['group_id'], ['group.id'], ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'], ondelete='RESTRICT'),
        )

    if 'service_api_key' in tables:
        indexes = {idx['name'] for idx in inspector.get_indexes('service_api_key')}
        for name in _SERVICE_KEY_INDEXES:
            if name not in indexes:
                op.create_index(name, 'service_api_key', [name.removeprefix('ix_service_api_key_')])

    # Usage attribution for service-key-authenticated API calls.
    if 'api_token_usage' in tables:
        columns = {col['name'] for col in inspector.get_columns('api_token_usage')}
        if 'service_key_id' not in columns:
            op.add_column('api_token_usage', sa.Column('service_key_id', sa.Text(), nullable=True))
        indexes = {idx['name'] for idx in inspector.get_indexes('api_token_usage')}
        if 'ix_api_token_usage_service_key_id' not in indexes:
            op.create_index('ix_api_token_usage_service_key_id', 'api_token_usage', ['service_key_id'])
        if 'ix_api_token_usage_servicekey_created' not in indexes:
            op.create_index(
                'ix_api_token_usage_servicekey_created', 'api_token_usage', ['service_key_id', 'created_at']
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'api_token_usage' in tables:
        indexes = {idx['name'] for idx in inspector.get_indexes('api_token_usage')}
        if 'ix_api_token_usage_servicekey_created' in indexes:
            op.drop_index('ix_api_token_usage_servicekey_created', table_name='api_token_usage')
        if 'ix_api_token_usage_service_key_id' in indexes:
            op.drop_index('ix_api_token_usage_service_key_id', table_name='api_token_usage')
        columns = {col['name'] for col in inspector.get_columns('api_token_usage')}
        if 'service_key_id' in columns:
            op.drop_column('api_token_usage', 'service_key_id')

    if 'service_api_key' in tables:
        op.drop_table('service_api_key')
