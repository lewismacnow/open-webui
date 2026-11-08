"""merge upstream v0.13.2 head with fork head

Topology-only merge revision. After syncing upstream v0.12.2 -> v0.13.2
the migration graph forks at the fork's v0.13.2 head into:
  - upstream's v0.13.x chain ... -> 9f6c2a8d1b43 (channel message /
    reaction lookup indexes)
  - fork's v0.13.2 chain ... -> e7f8a9b0c1d2 (base model repair,
    context compaction defaults, context window seeding)

Both branches add disjoint schema: upstream's chain adds indexes on the
channel message tables, the fork branch only seeds config rows and
repairs model rows - no schema changes at all. Merge order is safe.
This join produces a single head so `alembic upgrade head` resolves
cleanly on fresh and existing databases alike.

Revision ID: kb06mergeheads
Revises: 9f6c2a8d1b43, e7f8a9b0c1d2
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = 'kb06mergeheads'
down_revision: Union[str, None] = ('9f6c2a8d1b43', 'e7f8a9b0c1d2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Topology-only merge: no schema changes. The two parent branches
    # already applied their disjoint migrations; this node just closes
    # the graph so alembic has a single head.
    pass


def downgrade() -> None:
    pass
