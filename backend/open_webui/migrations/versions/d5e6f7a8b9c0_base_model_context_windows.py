"""base model context windows: 256K default, seeded per base model

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-07 19:30:00.000000

Context compaction needs to know how much context a model actually has.
A base model now has a maximum context window; a wrapper model inherits its
base model's window and may only LOWER it.

Two config keys back this:

- ``models.base_model_context_window_default`` (256000) - the window every
  base model gets unless it declares its own. Connection models with no row
  in the ``model`` table always use this.
- ``models.base_model_context_windows`` - per-base-model overrides, keyed by
  base model id.

This migration seeds both. For every base model (a row in the ``model``
table with no ``base_model_id``) the existing Advanced Params
``context_window`` is kept if it was set; otherwise it is set to the 256K
default. Wrapper models are not touched - they inherit.

"""

import json
import time
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_WINDOW = 256000
DEFAULT_KEY = 'models.base_model_context_window_default'
WINDOWS_KEY = 'models.base_model_context_windows'


def _config_table():
    return sa.table(
        'config',
        sa.column('key', sa.Text),
        sa.column('value', sa.JSON),
        sa.column('updated_at', sa.BigInteger),
    )


def _read(conn, key):
    config = _config_table()
    row = conn.execute(sa.select(config.c.value).where(config.c.key == key)).fetchone()
    if row is None:
        return None
    value = row[0]
    return json.loads(value) if isinstance(value, str) else value


def _write(conn, key, value, now: int) -> None:
    """Upsert a config row.

    The keys are new, so the rows do not exist yet - a plain UPDATE would
    match nothing and silently write nothing.
    """
    config = _config_table()
    exists = conn.execute(sa.select(config.c.key).where(config.c.key == key)).first()
    if exists is None:
        conn.execute(config.insert().values(key=key, value=value, updated_at=now))
    else:
        conn.execute(config.update().where(config.c.key == key).values(value=value, updated_at=now))


def upgrade() -> None:
    conn = op.get_bind()
    now = int(time.time())

    if _read(conn, DEFAULT_KEY) is None:
        _write(conn, DEFAULT_KEY, DEFAULT_WINDOW, now)

    # Seed a window for every base model that has a row. A base model is a
    # row without a base_model_id - it shares its id with the underlying
    # connection model and overrides it.
    windows = _read(conn, WINDOWS_KEY)
    if not isinstance(windows, dict):
        windows = {}

    try:
        rows = conn.execute(sa.text('SELECT id, params FROM model WHERE base_model_id IS NULL')).fetchall()
    except Exception:
        rows = []

    for model_id, raw_params in rows:
        params = {}
        if isinstance(raw_params, str):
            try:
                decoded = json.loads(raw_params)
            except Exception:
                decoded = {}
            params = decoded if isinstance(decoded, dict) else {}
        elif isinstance(raw_params, dict):
            params = raw_params
        declared = (params or {}).get('context_window')
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
            windows.setdefault(model_id, declared)
        else:
            windows.setdefault(model_id, DEFAULT_WINDOW)

    _write(conn, WINDOWS_KEY, windows, now)


def downgrade() -> None:
    conn = op.get_bind()
    now = int(time.time())
    _write(conn, WINDOWS_KEY, {}, now)
