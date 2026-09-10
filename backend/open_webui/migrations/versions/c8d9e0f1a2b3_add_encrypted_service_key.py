"""add encrypted_key to service_api_key

Revision ID: c8d9e0f1a2b3
Revises: 7c8d9e0f1a2b
Create Date: 2026-09-10 12:00:00.000000

Adds a nullable ``encrypted_key`` column to ``service_api_key`` for
at-rest storage of the plaintext. Ciphertext is Fernet, keyed off
``WEBUI_SECRET_KEY`` (same derivation as ``utils/valves.py``).

The plaintext is only set by the mint path; pre-existing rows minted
before this migration will have ``encrypted_key IS NULL`` and the
reveal endpoint returns 404 for them with a "legacy record" message.

Purely additive: nullable column + idempotent check via inspector.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = '7c8d9e0f1a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if 'service_api_key' not in tables:
        return
    columns = {c['name'] for c in inspector.get_columns('service_api_key')}
    if 'encrypted_key' not in columns:
        op.add_column(
            'service_api_key',
            sa.Column('encrypted_key', sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if 'service_api_key' not in tables:
        return
    columns = {c['name'] for c in inspector.get_columns('service_api_key')}
    if 'encrypted_key' in columns:
        op.drop_column('service_api_key', 'encrypted_key')
