"""Per-provider in-flight request counters for capacity-aware failover.

Tracks how many chat-completion requests are currently being served by each
provider URL, so the failover resolver can sink providers that have reached
their configured ``max_concurrent`` limit (see ``models.wrapper_provider_chains``
and ``FailoverProvider.max_concurrent``).

Storage strategy mirrors ``utils/rate_limit.py``:
- Redis-backed when ``app.state.redis`` is available (multi-node correct):
  one counter key per provider URL, INCR on request start, DECR on finish,
  with a safety TTL so workers that crash mid-request self-clear.
- In-memory dict fallback for deployments without Redis (single-process).

Counters are advisory only — best-effort accounting that degrades to
"no capacity signal" on any storage failure, never blocking a request.
"""

import hashlib
import logging
from typing import Any

from open_webui.env import REDIS_KEY_PREFIX

log = logging.getLogger(__name__)

# Safety TTL for Redis counters: if a worker dies mid-request without
# decrementing, the counter self-clears after this window so a provider
# cannot stay artificially "at capacity" forever.
INFLIGHT_TTL_SECONDS = 300

# In-memory fallback (no Redis configured, or Redis errored).
_memory_counts: dict[str, int] = {}


def _key(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f'{REDIS_KEY_PREFIX}:provider:inflight:{digest}'


def _redis(app_state) -> Any | None:
    """Extract the async Redis client from app.state, tolerating test stubs."""
    return getattr(app_state, 'redis', None)


async def increment(app_state, url: str) -> None:
    """Record a request starting against this provider URL."""
    redis_client = _redis(app_state)
    if redis_client is not None:
        try:
            k = _key(url)
            await redis_client.incr(k)
            # Refresh the safety window on every increment: an actively used
            # provider never expires, an abandoned counter clears after TTL.
            await redis_client.expire(k, INFLIGHT_TTL_SECONDS)
            return
        except Exception:
            log.debug('provider_inflight.increment: Redis failed, using memory fallback', exc_info=True)
    _memory_counts[url] = _memory_counts.get(url, 0) + 1


async def decrement(app_state, url: str) -> None:
    """Record a request finishing against this provider URL. Clamps at zero."""
    redis_client = _redis(app_state)
    if redis_client is not None:
        try:
            k = _key(url)
            count = await redis_client.decr(k)
            if count < 0:
                # Two decrements for one increment (e.g. finally + stream
                # wrapper edge) or a crashed peer already expired the key.
                # Clamp so a negative counter never blocks a provider.
                await redis_client.set(k, 0, ex=INFLIGHT_TTL_SECONDS)
            return
        except Exception:
            log.debug('provider_inflight.decrement: Redis failed, using memory fallback', exc_info=True)
    _memory_counts[url] = max(0, _memory_counts.get(url, 0) - 1)


async def counts(app_state, urls: list[str]) -> dict[str, int]:
    """Batch-fetch in-flight counts for the given provider URLs.

    Used by the failover resolver just before sorting candidates; only URLs
    with a configured ``max_concurrent`` need to be fetched.
    """
    url_list = list(dict.fromkeys(urls))
    if not url_list:
        return {}
    redis_client = _redis(app_state)
    if redis_client is not None:
        try:
            raw = await redis_client.mget([_key(u) for u in url_list])
            return {u: int(c) if c else 0 for u, c in zip(url_list, raw)}
        except Exception:
            log.debug('provider_inflight.counts: Redis failed, using memory fallback', exc_info=True)
    return {u: _memory_counts.get(u, 0) for u in url_list}
