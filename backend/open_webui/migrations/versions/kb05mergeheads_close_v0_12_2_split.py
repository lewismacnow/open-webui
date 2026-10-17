"""merge v0.12.2 upstream head with fork v0.11.3+ head

Topology-only merge revision. After syncing upstream v0.11.3 -> v0.12.2
on the -X ours merge, the migration graph forks at the original
kb04mergeheads join point into:
  - upstream's v0.11.4 -> v0.12.2 chain ... -> 0bd252b206fb
  - fork's kb04mergeheads -> 5643b1c2d4f5 -> 7c8d9e0f1a2b -> c8d9e0f1a2b3
    -> e2f3a4b5c6d7 -> g3h4i5j6k7l8 -> h4i5j6k7l8m9 -> b8c9d0e1f2a3
    -> c9d0e1f2a3b4 (metadata/github-sync/service-key features)
Both branches touch disjoint schema: upstream's v0.11.4..0.12.2 chain
adds ad-hoc tables/indexes (apps, audio/realtime, mfa, model/tool/skill/
function history, etc.) unrelated to knowledge_file/knowledge_github_source/
metadata_*; the fork branch only adds columns to those tables and new
service/api_token_usage tables. Merge order is safe. This join produces
a single head so alembic upgrade head resolves cleanly on fresh and
existing databases alike.

Revision ID: kb05mergeheads
Revises: 0bd252b206fb, c9d0e1f2a3b4
Create Date: 2026-10-15 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = 'kb05mergeheads'
down_revision: Union[str, None] = ('0bd252b206fb', 'c9d0e1f2a3b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Topology-only merge: no schema changes. The two parent branches
    # already applied their disjoint migrations; this node just closes
    # the graph so alembic has a single head.
    pass


def downgrade() -> None:
    # Downgrade is a no-op; dropping this topology merge does not
    # revert the schema changes of either parent branch.
    pass
