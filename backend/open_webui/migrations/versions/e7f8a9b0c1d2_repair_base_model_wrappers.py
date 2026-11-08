"""repair base models that were mistakenly turned into wrappers

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-11-08 10:00:00.000000

The Model Editor used to demand a base model for EVERY model it saved,
including base models - and it did not offer the model itself as an
option. A base model therefore ended up pointing at some OTHER model,
which turned it into a wrapper: the registry stopped treating it as an
override of the connection model, its requests were rerouted to the
model it pointed at, and it showed up in the workspace model list
demanding a base model of its own.

This migration clears the unambiguously broken case - a row whose
``base_model_id`` is its own id.

The general case (a base model pointing at a different model) can only be
detected where the list of connection models is known, which is the
runtime model registry - not a migration, because connection models are
discovered from the providers and are not stored in the database. So
``utils/models.get_all_models`` now detects it, repairs the stored row
and logs it. That repair runs on the first model list build after the
deploy.

"""

import sqlalchemy as sa
from alembic import op
from typing import Sequence, Union

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text('UPDATE model SET base_model_id = NULL WHERE base_model_id IS NOT NULL AND base_model_id = id')
    )
    if result.rowcount:
        print(f'Cleared {result.rowcount} self-referential base_model_id value(s).')


def downgrade() -> None:
    # Nothing to restore - the old value was always wrong.
    pass
