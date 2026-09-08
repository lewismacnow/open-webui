"""Tests for the Redis-shared failover capacity queue (``utils/failover.py`` v2).

Covers the oracle's 8-item review list plus the v1 guards that must survive
the rewrite:

1. Depth drift after a cancel/disconnect storm → ZCARD returns to 0.
2. Crash window: a stale heartbeat self-heals AFTER the window, not before.
3. Heartbeat refresh per poll tick actually happens.
4. Concurrent admission at depth == limit → exactly one admitted (Lua atomicity).
5. Concurrent claim on freed capacity → exactly ``max_concurrent`` dispatches.
6. Redis dies mid-wait → no hang, cleanup errors swallowed, fallback engages.
7. Global depth: 4 concurrent clients hit the shared limit N, not 4N.
8. v1 guards: task exemption (structural), 429 surfacing, deadline floor,
   0.5s poll clamp, max_queue_length=0 reject-all, CancelledError re-raise.

Redis is faked with ``fakeredis.aioredis.FakeRedis`` (verified to support
the admission Lua via EVAL). The in-flight counters run for real against
the fake — so admission, semaphore claims, and stale eviction all exercise
the same code paths production uses, just against an in-memory Redis. The
resolver call is monkeypatched (its inputs need DB/model state).

No pub/sub, streams, or LISTEN/NOTIFY anywhere — polling only.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

import open_webui.utils.failover as failover_module
from open_webui.utils import provider_inflight
from open_webui.utils.failover import (
    HEARTBEAT_STALE_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    MIN_QUEUE_DEADLINE_SECONDS,
    ProviderCandidate,
    _waiters_key,
    acquire_capacity_or_queue,
    capacity_queue_targets,
)

POLL = 0.5  # tests run at the minimum clamp so timing stays tight


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
    """A real Request shaped just enough for the queue: ``app.state`` is a
    plain namespace whose ``redis`` attribute the test controls (that is
    exactly what ``provider_inflight._redis`` reads), plus a controllable
    disconnect flag."""

    def __init__(self, redis_client=None, disconnected: bool = False):
        self.app_state = SimpleNamespace(redis=redis_client)
        super().__init__(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/',
                'headers': [],
                'query_string': b'',
                'app': SimpleNamespace(state=self.app_state),
            }
        )
        self._fake_disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._fake_disconnected


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    """Isolate module-level state (memory counters, fallback deque) between
    tests; verify the fallback drains just like the ZSET."""
    provider_inflight._memory_counts.clear()
    failover_module._fallback_waiters.clear()
    yield
    assert all(not w for w in failover_module._fallback_waiters.values()), 'fallback waiter leaked'
    provider_inflight._memory_counts.clear()
    failover_module._fallback_waiters.clear()


def patch_resolver(monkeypatch, fresh_list):
    """Monkeypatch the single re-resolve with a fresh-list factory that
    counts invocations."""
    calls = []

    async def fake_resolve(**kwargs):
        calls.append(kwargs)
        return list(fresh_list)  # fresh copies, like the real resolver

    monkeypatch.setattr(failover_module, 'resolve_failover_candidates', fake_resolve)
    return calls


async def saturate(app_state, url: str, limit: int) -> None:
    """Drive a provider to its max_concurrent via the real counters."""
    for _ in range(limit):
        await provider_inflight.increment(app_state, url)


def start_waiter(
    monkeypatch,
    candidates,
    fresh,
    *,
    redis_client,
    max_queue_length=10,
    poll_interval_seconds=POLL,
    full_message='all full',
    disconnected=False,
    payload=None,
):
    """Create (not await) one acquire_capacity_or_queue task."""
    calls = patch_resolver(monkeypatch, fresh)
    request = FakeRequest(redis_client=redis_client, disconnected=disconnected)
    task = asyncio.create_task(
        acquire_capacity_or_queue(
            request,
            candidates,
            payload=payload if payload is not None else {},
            max_queue_length=max_queue_length,
            poll_interval_seconds=poll_interval_seconds,
            full_message=full_message,
        )
    )
    return task, request, calls


# ── 8. v1 guards: structural predicate + constants ───────────────────────


def test_capacity_queue_targets_predicate():
    """Structural half of the predicate, directly (unchanged from v1)."""
    assert capacity_queue_targets([cand('http://a', None)]) is None
    assert capacity_queue_targets([cand('http://a', 1), cand('http://b', None)]) is None
    only_limited = [cand('http://a', 1), cand('http://b', 2)]
    assert capacity_queue_targets(only_limited) == only_limited


def test_v1_constants_unchanged():
    assert MIN_POLL_INTERVAL_SECONDS == 0.5
    assert MIN_QUEUE_DEADLINE_SECONDS == 30.0
    assert HEARTBEAT_STALE_SECONDS == 30.0


def test_task_exemption_guard_still_wraps_the_hook():
    """Structural check: the openai.py hook call must still sit behind the
    background-task exemption (``metadata.get('task')``) — importing the
    router module itself needs optional heavy deps, so assert on source."""
    source = Path(failover_module.__file__).resolve().parents[1].joinpath('routers', 'openai.py').read_text()
    guard = source.index("if not (metadata and metadata.get('task')):")
    hook = source.index('candidates = await acquire_capacity_or_queue(')
    assert guard < hook, 'task exemption no longer guards the queue hook'


# ── Fast paths (no queueing) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlimited_candidate_skips_queue(redis_client):
    """A max_concurrent=None candidate always has room — no queue I/O."""
    candidates = [cand('http://a', 1), cand('http://b', None)]
    await saturate(FakeRequest(redis_client).app.state, 'http://a', 1)

    result = await acquire_capacity_or_queue(FakeRequest(redis_client=redis_client), candidates, payload={})
    assert result == candidates
    key = _waiters_key([candidates[0]])
    assert await redis_client.zcard(key) == 0
    assert not failover_module._fallback_waiters


@pytest.mark.asyncio
async def test_free_capacity_at_entry_skips_queue(monkeypatch, redis_client):
    """All limited but one below its limit ⇒ unchanged candidates, and no
    pre_claimed marker (dispatch does its own accounting, as in v1)."""
    candidates = [cand('http://a', 1), cand('http://b', 2)]
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, 'http://a', 1)  # a full, b has room

    calls = patch_resolver(monkeypatch, candidates)
    result = await acquire_capacity_or_queue(FakeRequest(redis_client=redis_client), candidates, payload={})
    assert result == candidates
    assert not any(getattr(c, 'pre_claimed', False) for c in result)
    assert calls == []  # no re-resolve when nothing queued


# ── 3. Queue entry, heartbeat refresh, and the happy claim path ──────────


@pytest.mark.asyncio
async def test_enters_queue_and_heartbeats_each_tick(monkeypatch, redis_client):
    """All-limited saturated ⇒ the waiter sits in the ZSET; every poll tick
    refreshes its score so stale eviction never reclaims a live waiter; a
    freed slot is claimed, the fresh list is pinned pre_claimed, and the
    waiter leaves the ZSET cleanly."""
    url, limit = 'http://a', 1
    candidates = [cand(url, limit)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, limit)

    task, _req, calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
    await asyncio.sleep(0.15)  # admitted, one tick not yet elapsed
    assert await redis_client.zcard(key) == 1
    score_1 = (await redis_client.zrange(key, 0, -1, withscores=True))[0][1]
    assert await redis_client.ttl(key) > 0

    await asyncio.sleep(0.7)  # > one 0.5s tick
    assert await redis_client.zcard(key) == 1  # still waiting (saturated)
    score_2 = (await redis_client.zrange(key, 0, -1, withscores=True))[0][1]
    assert score_2 > score_1, 'heartbeat score was not refreshed per tick'

    # Free the slot; next tick claims it and dispatches.
    await provider_inflight.decrement(state, url)
    result = await asyncio.wait_for(task, timeout=3.0)
    assert result[0].url == url
    assert result[0].pre_claimed is True  # queue-claimed: no double count
    assert len(calls) == 1  # single re-resolve, not per tick
    counts = await provider_inflight.counts(state, [url])
    assert counts[url] == limit  # our claim is held until dispatch releases
    assert await redis_client.zcard(key) == 0


# ── 4. Concurrent admission at depth == limit ─────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_admission_admits_exactly_one(monkeypatch, redis_client):
    """max_queue_length=1, two concurrent callers: the admission Lua is the
    single source of truth — exactly one is admitted, the other gets an
    immediate 429."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    async def attempt():
        try:
            await acquire_capacity_or_queue(
                FakeRequest(redis_client=redis_client),
                candidates,
                payload={},
                max_queue_length=1,
                poll_interval_seconds=POLL,
                full_message='full',
            )
            return 'admitted'
        except HTTPException:
            return 'rejected'

    tasks = [asyncio.create_task(attempt()), asyncio.create_task(attempt())]
    await asyncio.sleep(0.25)
    outcomes = [t.result() for t in tasks if t.done()]
    pending = [t for t in tasks if not t.done()]
    assert outcomes == ['rejected']  # the loser 429'd immediately
    assert len(pending) == 1  # the winner is waiting in the ZSET
    assert await redis_client.zcard(key) == 1

    for t in pending:
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    assert await redis_client.zcard(key) == 0


# ── 7. Global depth across concurrent clients ────────────────────────────


@pytest.mark.asyncio
async def test_global_depth_four_clients_share_limit(monkeypatch, redis_client):
    """4 concurrent clients (simulating workers), max_queue_length=3: the
    depth is GLOBAL — 3 admitted, the 4th gets an immediate 429 (v1's
    per-process deque would have admitted all 4)."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    tasks = [
        asyncio.create_task(
            acquire_capacity_or_queue(
                FakeRequest(redis_client=redis_client),
                candidates,
                payload={},
                max_queue_length=3,
                poll_interval_seconds=POLL,
            )
        )
        for _ in range(4)
    ]
    await asyncio.sleep(0.25)
    done = [t for t in tasks if t.done()]
    pending = [t for t in tasks if not t.done()]
    assert len(pending) == 3  # shared depth capped at 3
    assert len(done) == 1
    with pytest.raises(HTTPException) as excinfo:
        done[0].result()
    assert excinfo.value.status_code == 429
    assert await redis_client.zcard(key) == 3  # N, not 4N

    for t in pending:
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    assert await redis_client.zcard(key) == 0


# ── 2. Crash window: stale heartbeat self-heals AFTER the window ─────────


@pytest.mark.asyncio
async def test_stale_waiter_not_evicted_early(monkeypatch, redis_client):
    """A LIVE waiter (fresh heartbeat) pins the depth — a second caller at
    the limit is rejected; no premature eviction."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    live, _req, _calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
    await asyncio.sleep(0.1)
    assert await redis_client.zcard(key) == 1

    with pytest.raises(HTTPException) as excinfo:
        await acquire_capacity_or_queue(
            FakeRequest(redis_client=redis_client),
            candidates,
            payload={},
            max_queue_length=1,
            poll_interval_seconds=POLL,
        )
    assert excinfo.value.status_code == 429
    assert await redis_client.zcard(key) == 1  # live waiter still there

    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live


@pytest.mark.asyncio
async def test_stale_waiter_evicted_on_next_admission(monkeypatch, redis_client):
    """A waiter whose heartbeat is older than HEARTBEAT_STALE_SECONDS
    (crashed worker / lost cleanup) is evicted by the next admission — but
    ONLY after the window has actually elapsed."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    # Plant a ghost waiter whose heartbeat aged out 1s past the window.
    ghost = 'ghost-worker:deadbeef'
    await redis_client.zadd(key, {ghost: time.monotonic() - (HEARTBEAT_STALE_SECONDS + 1.0)})
    assert await redis_client.zcard(key) == 1

    task, _req, _calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
    await asyncio.sleep(0.15)
    # Admission evicted the ghost and admitted the newcomer.
    assert await redis_client.zscore(key, ghost) is None
    assert await redis_client.zcard(key) == 1

    await provider_inflight.decrement(state, url)  # free the slot
    result = await asyncio.wait_for(task, timeout=3.0)
    assert result[0].pre_claimed is True
    assert await redis_client.zcard(key) == 0


# ── 5. Concurrent claim on freed capacity ────────────────────────────────


@pytest.mark.asyncio
async def test_freed_slots_claimed_by_exactly_max_concurrent(monkeypatch, redis_client):
    """5 waiters, max_concurrent=2, both slots freed: exactly 2 claims win
    (final counter == max_concurrent), the other 3 keep waiting; each
    winner's fresh list is pinned pre_claimed and re-resolved once."""
    url, limit = 'http://a', 2
    candidates = [cand(url, limit)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, limit)

    tasks = []
    all_calls = []
    for _ in range(5):
        task, _req, calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
        tasks.append(task)
        all_calls.append(calls)

    await asyncio.sleep(0.15)
    assert await redis_client.zcard(key) == 5  # all waiting

    await provider_inflight.decrement(state, url)
    await provider_inflight.decrement(state, url)  # both slots freed

    # Wait (bounded) until exactly `limit` waiters have dispatched.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if sum(1 for t in tasks if t.done() and not t.cancelled()) == limit:
            break
        await asyncio.sleep(0.05)

    finished = [t for t in tasks if t.done() and not t.cancelled()]
    assert len(finished) == limit, f'{len(finished)} dispatches, expected exactly {limit}'
    for t in finished:
        result = t.result()
        assert result[0].url == url
        assert result[0].pre_claimed is True
    counts = await provider_inflight.counts(state, [url])
    assert counts[url] == limit  # exactly max_concurrent claims held
    assert sum(len(c) for c in all_calls) == limit  # one re-resolve per winner
    assert await redis_client.zcard(key) == 5 - limit  # losers still queued

    for t in tasks:
        if not t.done():
            t.cancel()
    # Let every task settle so its finally-cleanup has actually run.
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, HTTPException):
            pass
    assert await redis_client.zcard(key) == 0
    assert (await provider_inflight.counts(state, [url]))[url] == limit  # claims intact


# ── 1. Cancel/disconnect storm → depth returns to zero ───────────────────


@pytest.mark.asyncio
async def test_cancel_and_disconnect_storm_leaves_no_depth(monkeypatch, redis_client):
    """3 cancelled waiters + 2 disconnected waiters: every exit path runs
    the finally-cleanup, so ZCARD returns to 0 (no depth drift)."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    waiting = [start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)[0] for _ in range(3)]
    gone = [
        start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client, disconnected=True)[0]
        for _ in range(2)
    ]

    await asyncio.sleep(0.25)
    # The disconnected pair already exited 429 through their own cleanup.
    for t in gone:
        assert t.done()
        with pytest.raises(HTTPException) as excinfo:
            t.result()
        assert excinfo.value.status_code == 429
    assert await redis_client.zcard(key) == 3  # only the live trio remains

    for t in waiting:  # the cancel storm
        t.cancel()
    for t in waiting:
        with pytest.raises(asyncio.CancelledError):
            await t
    assert await redis_client.zcard(key) == 0  # depth fully drained
    assert not any(failover_module._fallback_waiters.values())


# ── 6. Redis dies mid-wait → fallback engages, no hang ───────────────────


@pytest.mark.asyncio
async def test_redis_dies_mid_wait_falls_back(monkeypatch, redis_client):
    """A waiter admitted via Redis loses Redis mid-wait: the claim falls
    back to the in-process counters, cleanup errors are swallowed (the
    stale ZSET member self-heals via stale eviction/TTL), and the request
    completes — no hang, no crash."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    health = {'up': True}

    def flaky_redis(app_state):
        return redis_client if health['up'] else None

    monkeypatch.setattr(provider_inflight, '_redis', flaky_redis)
    task, _req, _calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
    await asyncio.sleep(0.15)
    assert await redis_client.zcard(key) == 1  # admitted via Redis

    health['up'] = False  # Redis dies mid-wait
    provider_inflight._memory_counts.clear()  # fallback counters start clean

    started = time.monotonic()
    result = await asyncio.wait_for(task, timeout=3.0)  # would hang on a bug
    assert time.monotonic() - started < 2.5
    assert result[0].pre_claimed is True  # claimed via the memory semaphore
    # The orphaned ZSET member stays until stale eviction — by design; the
    # key's TTL plus the next admission's ZREMRANGEBYSCORE recover the depth.
    assert await redis_client.zcard(key) == 1
    assert not any(failover_module._fallback_waiters.values())


@pytest.mark.asyncio
async def test_redis_unavailable_uses_inprocess_fallback(monkeypatch):
    """With no Redis at all (state.redis=None), admission/claim run fully
    in-process (v1 semantics) — depth via len(deque), counters via memory."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    no_redis_state = FakeRequest(redis_client=None).app_state
    await saturate(no_redis_state, url, 1)  # memory counter = 1 = limit

    task, _req, calls = start_waiter(monkeypatch, candidates, candidates, redis_client=None)
    await asyncio.sleep(0.1)
    fallback = failover_module._fallback_waiters.get(key)
    assert fallback is not None and len(fallback) == 1  # deque depth accounting

    _memory = provider_inflight._memory_counts
    _memory[url] = 0  # slot frees in the fallback counters
    result = await asyncio.wait_for(task, timeout=3.0)
    assert result[0].pre_claimed is True
    assert len(calls) == 1
    assert not failover_module._fallback_waiters.get(key, ())  # drained


# ── 8. v1 guards: reject-all, deadline floor, clamp, cancel ──────────────


@pytest.mark.asyncio
async def test_zero_queue_length_rejects_immediately(redis_client):
    """max_queue_length=0 disables the queue: all-at-capacity ⇒ instant
    429 with the configured message; nothing is written to the ZSET."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    started = time.monotonic()
    with pytest.raises(HTTPException) as excinfo:
        await acquire_capacity_or_queue(
            FakeRequest(redis_client=redis_client),
            candidates,
            payload={},
            max_queue_length=0,
            poll_interval_seconds=POLL,
            full_message='all full',
        )
    assert time.monotonic() - started < 0.3
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == 'all full'
    assert await redis_client.zcard(_waiters_key(candidates)) == 0


@pytest.mark.asyncio
async def test_deadline_floor_429s(monkeypatch, redis_client):
    """The wait deadline (floored at MIN_QUEUE_DEADLINE_SECONDS) elapses →
    429 with the configured message; the floor mechanism is what's tested
    (the constant itself is pinned to 30s in test_v1_constants_unchanged)."""
    monkeypatch.setattr(failover_module, 'MIN_QUEUE_DEADLINE_SECONDS', 0.6)
    url = 'http://a'
    candidates = [cand(url, 1)]
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    started = time.monotonic()
    with pytest.raises(HTTPException) as excinfo:
        await asyncio.wait_for(
            acquire_capacity_or_queue(
                FakeRequest(redis_client=redis_client),
                candidates,
                payload={},
                max_queue_length=1,  # 1 × 0.5 = 0.5 < 0.6 floor ⇒ floor binds
                poll_interval_seconds=POLL,
                full_message='timed out',
            ),
            timeout=5.0,
        )
    elapsed = time.monotonic() - started
    assert elapsed >= 0.6, f'429 arrived at {elapsed:.2f}s, before the deadline floor'
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == 'timed out'
    assert await redis_client.zcard(_waiters_key(candidates)) == 0


@pytest.mark.asyncio
async def test_poll_interval_clamped_to_half_second(monkeypatch, redis_client):
    """A sub-floor interval (0.4s) is clamped to 0.5s at runtime: the gap
    between claim attempts stays ≥ ~0.5s (no hot-spin)."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    stamps = []
    real_increment = provider_inflight.increment

    async def tracking_increment(app_state, u):
        stamps.append(time.monotonic())
        return await real_increment(app_state, u)

    monkeypatch.setattr(provider_inflight, 'increment', tracking_increment)
    task, _req, _calls = start_waiter(
        monkeypatch,
        candidates,
        candidates,
        redis_client=redis_client,
        poll_interval_seconds=0.4,  # under floor
    )

    async def free_later():
        await asyncio.sleep(0.7)
        await provider_inflight.decrement(state, url)

    bg = asyncio.create_task(free_later())
    await asyncio.wait_for(task, timeout=3.0)
    await bg

    assert len(stamps) >= 2  # entry claim attempt + at least one re-check
    gap = stamps[1] - stamps[0]
    assert gap >= 0.45, f'claim-attempt gap {gap:.3f}s is under the 0.5s floor'


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised(monkeypatch, redis_client):
    """Cancelling the waiting coroutine surfaces CancelledError itself —
    never an HTTPException — and the ZSET entry is still removed."""
    url = 'http://a'
    candidates = [cand(url, 1)]
    key = _waiters_key(candidates)
    state = FakeRequest(redis_client=redis_client).app_state
    await saturate(state, url, 1)

    task, _req, _calls = start_waiter(monkeypatch, candidates, candidates, redis_client=redis_client)
    await asyncio.sleep(0.1)
    assert await redis_client.zcard(key) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await redis_client.zcard(key) == 0
