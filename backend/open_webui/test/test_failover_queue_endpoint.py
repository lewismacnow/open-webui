"""Tests for the /api/v1/configs/failover-queue endpoints.

Calls the real FastAPI handler functions directly (no TestClient needed —
the admin dependency is expressed via ``Depends`` at registration time and
never runs on a direct call), with ``Config.upsert`` / ``Config.get_many``
monkeypatched so nothing touches the database.

Importing ``open_webui.routers.configs`` pulls the full router dependency
tree; if those optional dependencies are not installed the whole module is
skipped rather than erroring.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

configs_router = pytest.importorskip('open_webui.routers.configs', reason='router dependency tree unavailable')

from open_webui.models.config import Config
from open_webui.utils.failover import DEFAULT_QUEUE_FULL_MESSAGE, MIN_POLL_INTERVAL_SECONDS

FailoverQueueConfigForm = configs_router.FailoverQueueConfigForm
get_failover_queue_config = configs_router.get_failover_queue_config
set_failover_queue_config = configs_router.set_failover_queue_config


@pytest.fixture
def captured_upsert(monkeypatch):
    """Capture Config.upsert writes instead of persisting them."""
    saved: dict = {}

    async def fake_upsert(updates: dict) -> None:
        saved.update(updates)

    monkeypatch.setattr(Config, 'upsert', fake_upsert)
    return saved


# ── POST validation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_clamps_poll_interval_to_half_second(captured_upsert):
    """poll_interval_seconds=0.4 submitted via POST is clamped up to 0.5 —
    both in the persisted value and the response — to prevent a hot-spin."""
    assert MIN_POLL_INTERVAL_SECONDS == 0.5

    form = FailoverQueueConfigForm(max_queue_length=5, poll_interval_seconds=0.4, full_message='busy')
    result = await set_failover_queue_config(request=None, form_data=form, user=SimpleNamespace())

    assert result.poll_interval_seconds == 0.5
    assert captured_upsert['chat.failover_queue.poll_interval_seconds'] == 0.5
    assert captured_upsert['chat.failover_queue.max_queue_length'] == 5
    assert captured_upsert['chat.failover_queue.full_message'] == 'busy'


@pytest.mark.asyncio
async def test_post_allows_zero_queue_length(captured_upsert):
    """0 is a valid (documented) "reject all" setting — allowed on save."""
    form = FailoverQueueConfigForm(max_queue_length=0, poll_interval_seconds=1.0, full_message='none')
    result = await set_failover_queue_config(request=None, form_data=form, user=SimpleNamespace())
    assert result.max_queue_length == 0
    assert captured_upsert['chat.failover_queue.max_queue_length'] == 0


@pytest.mark.asyncio
async def test_post_rejects_negative_queue_length(captured_upsert):
    form = FailoverQueueConfigForm(max_queue_length=-1, poll_interval_seconds=1.0, full_message='x')
    with pytest.raises(HTTPException) as excinfo:
        await set_failover_queue_config(request=None, form_data=form, user=SimpleNamespace())
    assert excinfo.value.status_code == 400
    assert captured_upsert == {}  # nothing persisted on validation failure


@pytest.mark.asyncio
async def test_post_rejects_empty_full_message(captured_upsert):
    form = FailoverQueueConfigForm(max_queue_length=5, poll_interval_seconds=1.0, full_message='  ')
    with pytest.raises(HTTPException) as excinfo:
        await set_failover_queue_config(request=None, form_data=form, user=SimpleNamespace())
    assert excinfo.value.status_code == 400
    assert captured_upsert == {}


# ── GET defaults ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_defaults_when_nothing_persisted(monkeypatch):
    async def fake_get_many(*keys):
        return {}  # nothing stored

    monkeypatch.setattr(Config, 'get_many', fake_get_many)
    result = await get_failover_queue_config(user=SimpleNamespace())
    assert result.max_queue_length == 10
    assert result.poll_interval_seconds == 2.0
    assert result.full_message == DEFAULT_QUEUE_FULL_MESSAGE
    assert result.full_message == 'LLM Load is at maximum capacity right now, retry in 30 seconds'


@pytest.mark.asyncio
async def test_get_returns_persisted_values(monkeypatch):
    async def fake_get_many(*keys):
        return {
            'chat.failover_queue.max_queue_length': 3,
            'chat.failover_queue.poll_interval_seconds': 1.5,
            'chat.failover_queue.full_message': 'custom full',
        }

    monkeypatch.setattr(Config, 'get_many', fake_get_many)
    result = await get_failover_queue_config(user=SimpleNamespace())
    assert result.max_queue_length == 3
    assert result.poll_interval_seconds == 1.5
    assert result.full_message == 'custom full'


def test_routes_registered():
    """The endpoints are wired into the router at the paths the frontend
    API client expects."""
    paths = {route.path for route in configs_router.router.routes}
    assert '/failover-queue' in paths
