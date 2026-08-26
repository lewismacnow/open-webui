"""merge kb03mergeheads with upstream v0.11.1 head

Topology-only merge revision. After syncing upstream v0.11.1, the migration
graph forked at f0bd01a18a3d (the v0.11.0 head that kb03mergeheads already
absorbed) into two heads:
  - fork's kb03mergeheads (which joins kb02mergeheads + f0bd01a18a3d,
    representing fork's durable embedding pipeline + v0.11.0 sync)
  - upstream's v0.11.1 chain f0bd01a18a3d -> 1ce6ade7d93b (group_member
    index) -> 6d09d1bf1f23 (repair double-encoded oauth) ->
    d4c1a8e37b62 (chat_timer_at + chat indexes)

This join produces a single head so `alembic upgrade head` resolves cleanly.
It performs no schema changes of its own: the two branches touch disjoint
schema (fork's kb01-03 chain only adds `status`/`error` columns to
`knowledge_file` and earlier per-message/context-summary columns;
upstream's v0.11.1 chain adds a covering index on group_member, repairs
double-encoded oauth strings, and adds chat_timer_at + chat query indexes),
so merge order is safe.

Revision ID: kb04mergeheads
Revises: kb03mergeheads, d4c1a8e37b62
Create Date: 2026-08-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'kb04mergeheads'
down_revision: Union[str, None] = ('kb03mergeheads', 'd4c1a8e37b62')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
