"""Shared fixtures/environment for the failover capacity-queue tests.

Everything environment-sensitive MUST happen before the first
``open_webui.*`` import:

- ``env.py`` hard-exits without ``WEBUI_SECRET_KEY`` when auth is enabled
  (the default), so a test secret is set here.
- ``internal.db`` / ``config.py`` resolve ``DATA_DIR`` at import time (and
  ``config.py`` runs Alembic migrations against it), so tests point it at a
  throwaway directory instead of the repo's ``backend/data``.
- ``backend/`` is placed on ``sys.path`` so ``import open_webui`` works
  whether pytest is invoked from the repo root or from ``backend/``.

Python 3.13+ removed the ``audioop`` module that ``pydub`` (pulled in by the
routers import chain) still imports; a stub keeps router modules importable
on modern interpreters. On the supported range (<3.13) the real module is
used untouched.
"""

import os
import sys
import tempfile
from pathlib import Path

# ``backend/`` — test/ → open_webui/ → backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key-never-for-production')
if 'DATA_DIR' not in os.environ:
    os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='open-webui-fq-test-')

try:
    import audioop  # noqa: F401
except ImportError:  # Python >= 3.13
    import types

    _audioop = types.ModuleType('audioop')

    def _unimplemented(*_args, **_kwargs):
        raise NotImplementedError('audioop stub for tests')

    for _name in (
        'avg',
        'avgpp',
        'bias',
        'cross',
        'dbtomul',
        'findfactor',
        'findfit',
        'findmax',
        'getsample',
        'lin2lin',
        'max',
        'maxpp',
        'minmax',
        'mul',
        'ratecv',
        'reverse',
        'rms',
        'tomono',
        'tostereo',
        'adpcm2lin',
        'lin2adpcm',
        'lin2alaw',
        'alaw2lin',
        'lin2ulaw',
        'ulaw2lin',
        'error',
    ):
        setattr(_audioop, _name, _unimplemented if _name != 'error' else Exception)
    sys.modules.setdefault('audioop', _audioop)
    sys.modules.setdefault('pyaudioop', _audioop)
