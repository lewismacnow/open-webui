"""context compaction: on by default, 80% of the model's context window

Revision ID: c4d5e6f7a8b9
Revises: f3a4b5c6d7e8
Create Date: 2026-11-07 18:00:00.000000

Context compaction shipped disabled. The defaults are now:

- enabled
- triggered at 80% of the model's declared context window
- the most recent 40% of messages are kept, the older 60% are summarised

The config table is written at startup by Config.seed_defaults(), which only
INSERTS keys that do not exist yet - so changing the code default does not
change an existing installation. This migration updates the persisted rows.

The token cap is cleared where it equals the token threshold. The previous
admin save path forced ``token_cap = threshold``, and a cap equal to the
threshold clamps the window-derived threshold straight back down to the
global guess - which would defeat the percentage entirely.

An admin can still turn compaction off, or set a different ratio, from
Admin > Settings > Interface at any time.

"""

import time
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONFIG = sa.table(
    'config',
    sa.column('key', sa.Text),
    sa.column('value', sa.JSON),
    sa.column('updated_at', sa.BigInteger),
)

ENABLE_KEY = 'chat.context_compaction.enable'
RATIO_KEY = 'chat.context_compaction.context_window_ratio'
THRESHOLD_KEY = 'chat.context_compaction.token_threshold'
CAP_KEY = 'chat.context_compaction.token_cap'


def _current(conn, key: str):
    row = conn.execute(sa.select(CONFIG.c.value).where(CONFIG.c.key == key)).fetchone()
    return None if row is None else row[0]


def _write(conn, key: str, value, now: int) -> None:
    conn.execute(CONFIG.update().where(CONFIG.c.key == key).values(value=value, updated_at=now))


def upgrade() -> None:
    conn = op.get_bind()
    now = int(time.time())

    # 1. Compaction on by default.
    if _current(conn, ENABLE_KEY) is False:
        _write(conn, ENABLE_KEY, True, now)

    # 2. The ratio default moved from 0.7 to 0.8. Only touch the old default
    #    value so an admin who picked a different ratio keeps it.
    if _current(conn, RATIO_KEY) == 0.7:
        _write(conn, RATIO_KEY, 0.8, now)

    # 3. Drop the forced cap so the window-derived threshold is not clamped
    #    back down to the global token threshold.
    threshold = _current(conn, THRESHOLD_KEY)
    cap = _current(conn, CAP_KEY)
    if cap is not None and threshold is not None and cap == threshold:
        _write(conn, CAP_KEY, None, now)


def downgrade() -> None:
    conn = op.get_bind()
    now = int(time.time())

    if _current(conn, ENABLE_KEY) is True:
        _write(conn, ENABLE_KEY, False, now)
    if _current(conn, RATIO_KEY) == 0.8:
        _write(conn, RATIO_KEY, 0.7, now)
