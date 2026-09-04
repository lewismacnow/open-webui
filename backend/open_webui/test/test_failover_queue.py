"""Tests for the failover capacity queue (``utils/failover.py``).

Covers the admission predicate, the queue/dispatch lifecycle of
``acquire_capacity_or_queue``, the poll-interval floor, the
``max_queue_length=0`` reject-all mode, and CancelledError propagation.

The in-flight counters and the re-resolve call are monkeypatched, so these
tests are fully hermetic (no DB, no HTTP).
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import open_webui.utils.failover as failover_module
from open_webui.utils.failover import (
    MIN_POLL_INTERVAL_SECONDS,
    ProviderCandidate,
    acquire_capacity_or_queue,
    capacity_queue_targets,
)


def cand(url: str, max_concurrent) -> ProviderCandidate:
    """Minimal candidate for capacity logic — routing fields are unused."""
    return ProviderCandidate(
        url=url,
        url_idx=0,
        key='',
        model_name='test-model',
        api_config={},
        max_concurrent=max_concurrent,
    )


class FakeRequest(Request):
    """Just enough of a real Request for the queue helper: an app.state to
    read inflight counters through, and a controllable disconnect flag."""

    def __init__(self, disconnected: bool = False):
        app = SimpleNamespace(state=SimpleNamespace(redis=None))
        super().__init__(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/',
                'headers': [],
                'query_string': b'',
                'app': app,
            }
        )
        self._fake_disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._fake_disconnected


def patch_counts(monkeypatch, initial: dict[str, int]) -> dict:
    """Replace ``provider_inflight.counts`` with a mutable fake.

    Returns a state dict whose ``counts`` the test can flip mid-wait (to
    simulate a slot freeing) and which records call timestamps and a call
    count for interval assertions.
    """
    state = {'counts': dict(initial), 'stamps': [], 'calls': 0}

    async def fake_counts(app_state, urls):
        state['calls'] += 1
        state['stamps'].append(time.monotonic())
        return {u: state['counts'].get(u, 0) for u in urls}

    monkeypatch.setattr(failover_module.provider_inflight, 'counts', fake_counts)
    return state


@pytest.fixture(autouse=True)
def clean_queue():
    """Isolate the module-level FIFO between tests and verify it drains."""
    failover_module._failover_queue.clear()
    yield
    assert len(failover_module._failover_queue) == 0, 'queue entry leaked after test'


# ── 1. Unlimited candidate ⇒ never queue ─────────────────────────────────


@pytest.mark.asyncio
async def test_unlimited_candidate_skips_queue(monkeypatch):
    """A candidate with max_concurrent=None always has room — the helper
    must return the candidates unchanged without touching the counters."""
    candidates = [cand('http://a', 1), cand('http://b', None)]
    state = patch_counts(monkeypatch, {'http://a': 1})  # 'a' at its limit

    result = await acquire_capacity_or_queue(FakeRequest(), candidates, payload={})

    assert result == candidates
    assert state['calls'] == 0  # predicate short-circuits before any I/O
    assert len(failover_module._failover_queue) == 0


@pytest.mark.asyncio
async def test_all_unlimited_candidates_skip_queue(monkeypatch):
    """No limited candidate at all ⇒ nothing to gate on."""
    candidates = [cand('http://a', None), cand('http://b', None)]
    state = patch_counts(monkeypatch, {})

    result = await acquire_capacity_or_queue(FakeRequest(), candidates, payload={})

    assert result == candidates
    assert state['calls'] == 0


def test_capacity_queue_targets_predicate():
    """Structural half of the predicate, directly."""
    assert capacity_queue_targets([cand('http://a', None)]) is None
    assert capacity_queue_targets([cand('http://a', 1), cand('http://b', None)]) is None
    only_limited = [cand('http://a', 1), cand('http://b', 2)]
    assert capacity_queue_targets(only_limited) == only_limited


# ── 2. All limited at capacity ⇒ enter the queue ──────────────────────────


@pytest.mark.asyncio
async def test_all_limited_at_capacity_enters_queue(monkeypatch):
    """Every limited candidate at/over its limit ⇒ the request parks in the
    FIFO instead of proceeding."""
    candidates = [cand('http://a', 1), cand('http://b', 2)]
    state = patch_counts(monkeypatch, {'http://a': 1, 'http://b': 2})

    async def fake_resolve(**kwargs):
        return candidates

    monkeypatch.setattr(failover_module, 'resolve_failover_candidates', fake_resolve)

    async def observe_then_release():
        await asyncio.sleep(0.1)
        # The request is sitting in the queue, not through the handler yet.
        assert len(failover_module._failover_queue) == 1
        await asyncio.sleep(0.55)  # at least one poll cycle at 0.5s
        state['counts']['http://a'] = 0  # release so the test ends quickly

    bg = asyncio.create_task(observe_then_release())
    started = time.monotonic()
    await acquire_capacity_or_queue(
        FakeRequest(), candidates, payload={}, max_queue_length=10, poll_interval_seconds=0.5
    )
    await bg
    # It actually waited (queued), and the entry was removed on exit.
    assert time.monotonic() - started >= 0.5
    assert len(failover_module._failover_queue) == 0


# ── 3. Slot frees during the wait ⇒ exit with the fresh candidate list ────


@pytest.mark.asyncio
async def test_freed_slot_returns_fresh_candidates(monkeypatch):
    """When inflight drops below max_concurrent mid-wait, the helper exits
    the queue and returns a ONCE-re-resolved (fresh) candidate list."""
    stale = [cand('http://a', 1), cand('http://b', 2)]
    fresh = [cand('http://b', 2), cand('http://a', 1)]  # ordering flipped
    state = patch_counts(monkeypatch, {'http://a': 1, 'http://b': 2})
    resolves = []

    async def fake_resolve(**kwargs):
        resolves.append(kwargs)
        return fresh

    monkeypatch.setattr(failover_module, 'resolve_failover_candidates', fake_resolve)

    async def free_slot_later():
        await asyncio.sleep(0.7)  # > one 0.5s poll
        state['counts']['http://b'] = 1  # 1 < 2 → capacity available

    bg = asyncio.create_task(free_slot_later())
    result = await acquire_capacity_or_queue(
        FakeRequest(), stale, payload={'model': 'x'}, max_queue_length=10, poll_interval_seconds=0.5
    )
    await bg

    assert result == fresh
    assert len(resolves) == 1  # re-resolved exactly once, not per poll
    assert resolves[0]['payload'] == {'model': 'x'}  # resolver kwargs pass through
    assert len(failover_module._failover_queue) == 0


@pytest.mark.asyncio
async def test_capacity_available_upfront_skips_queue(monkeypatch):
    """All limited but one below its limit ⇒ proceed without queueing."""
    candidates = [cand('http://a', 1), cand('http://b', 2)]
    patch_counts(monkeypatch, {'http://a': 1, 'http://b': 1})  # b has room

    async def fail_resolve(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError('resolver must not be re-called when no queueing happened')

    monkeypatch.setattr(failover_module, 'resolve_failover_candidates', fail_resolve)

    result = await acquire_capacity_or_queue(
        FakeRequest(), candidates, payload={}, max_queue_length=10, poll_interval_seconds=0.5
    )
    assert result == candidates
    assert len(failover_module._failover_queue) == 0


# ── 4. Poll interval is clamped up to 0.5s (no hot-spin) ──────────────────


@pytest.mark.asyncio
async def test_poll_interval_clamped_to_half_second(monkeypatch):
    """A sub-floor interval (0.4s) is clamped up to 0.5s: the first
    re-check after the admission check must be ≥ ~0.5s later, matching the
    clamp the POST /configs/failover-queue handler applies on save."""
    assert MIN_POLL_INTERVAL_SECONDS == 0.5

    candidates = [cand('http://a', 1)]
    state = patch_counts(monkeypatch, {'http://a': 1})

    async def fake_resolve(**kwargs):
        return candidates

    monkeypatch.setattr(failover_module, 'resolve_failover_candidates', fake_resolve)

    async def free_later():
        await asyncio.sleep(0.95)
        state['counts']['http://a'] = 0

    bg = asyncio.create_task(free_later())
    await acquire_capacity_or_queue(
        FakeRequest(), candidates, payload={}, max_queue_length=10, poll_interval_seconds=0.4
    )
    await bg

    stamps = state['stamps']
    assert len(stamps) >= 2  # admission check + at least one re-check
    gap = stamps[1] - stamps[0]
    # 0.4 would hot-spin; the clamp holds the first re-check back to ~0.5s
    # (small tolerance for event-loop scheduling jitter).
    assert gap >= 0.45, f'poll gap {gap:.3f}s is under the 0.5s floor'


# ── 5. max_queue_length=0 ⇒ immediate 429 when saturated ──────────────────


@pytest.mark.asyncio
async def test_zero_queue_length_rejects_immediately(monkeypatch):
    """0 queue slots disables the queue: all-at-capacity ⇒ instant 429 with
    the configured message, no 30s deadline wait."""
    candidates = [cand('http://a', 1), cand('http://b', 2)]
    patch_counts(monkeypatch, {'http://a': 1, 'http://b': 2})

    started = time.monotonic()
    with pytest.raises(HTTPException) as excinfo:
        await acquire_capacity_or_queue(
            FakeRequest(),
            candidates,
            payload={},
            max_queue_length=0,
            poll_interval_seconds=0.5,
            full_message='all full',
        )
    assert time.monotonic() - started < 0.3  # immediate
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == 'all full'
    assert len(failover_module._failover_queue) == 0


@pytest.mark.asyncio
async def test_queue_full_rejects(monkeypatch):
    """With the FIFO already at max_queue_length, the next waiter gets 429."""
    candidates = [cand('http://a', 1)]
    patch_counts(monkeypatch, {'http://a': 1})

    failover_module._failover_queue.append(failover_module._QueueEntry(enqueued_at=time.monotonic()))
    with pytest.raises(HTTPException) as excinfo:
        await acquire_capacity_or_queue(
            FakeRequest(),
            candidates,
            payload={},
            max_queue_length=1,
            poll_interval_seconds=0.5,
            full_message='queue full',
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == 'queue full'
    # The pre-existing (foreign) waiter is untouched — the helper never
    # enqueued, so it must not have evicted anyone either.
    assert len(failover_module._failover_queue) == 1
    failover_module._failover_queue.clear()


# ── 6. CancelledError propagates (not swallowed into a 429) ───────────────


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised(monkeypatch):
    """Cancelling the waiting coroutine must surface CancelledError itself
    — never an HTTPException — and still remove the queue entry."""
    candidates = [cand('http://a', 1)]
    patch_counts(monkeypatch, {'http://a': 1})

    task = asyncio.create_task(
        acquire_capacity_or_queue(FakeRequest(), candidates, payload={}, max_queue_length=10, poll_interval_seconds=0.5)
    )
    await asyncio.sleep(0.1)
    assert len(failover_module._failover_queue) == 1  # it is mid-wait

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The finally block removed the entry despite cancellation.
    assert len(failover_module._failover_queue) == 0


# ── Bonus: client disconnect while queued ⇒ 429 ───────────────────────────


@pytest.mark.asyncio
async def test_disconnect_while_queued_returns_429(monkeypatch):
    candidates = [cand('http://a', 1)]
    patch_counts(monkeypatch, {'http://a': 1})

    started = time.monotonic()
    with pytest.raises(HTTPException) as excinfo:
        await acquire_capacity_or_queue(
            FakeRequest(disconnected=True),
            candidates,
            payload={},
            max_queue_length=10,
            poll_interval_seconds=0.5,
            full_message='gone',
        )
    assert time.monotonic() - started < 2.0  # caught on the first poll
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == 'gone'
    assert len(failover_module._failover_queue) == 0
