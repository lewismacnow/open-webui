"""Failover routing for OpenAI-compatible workspace models.

A workspace model may list an ordered set of providers in
`Model.meta.failover_providers`. At request time this module resolves
that list into concrete (URL, key, model name) candidates the chat
handler can try in order. Providers marked unhealthy in the app-state
cache are deprioritised, but never removed outright — if every provider
looks unhealthy we still try them rather than hard-failing.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import logging
import time
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException, Request

from open_webui.models.config import Config
from open_webui.utils import provider_inflight

log = logging.getLogger(__name__)


class RetryableProviderError(Exception):
    """Signal from a single-provider attempt that the outer loop should try the next candidate.

    Raised when the request fails in a way that looks transient (connection
    error, 5xx, 429). The fields carry enough context for health-cache
    updates (retry_after) and, if every candidate fails, for the final
    HTTPException surfaced to the user.
    """

    def __init__(
        self,
        status_code: Optional[int] = None,
        detail: Optional[str] = None,
        retry_after: Optional[int] = None,
        provider_url: Optional[str] = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after
        self.provider_url = provider_url
        super().__init__(f'Retryable provider error at {provider_url}: {status_code} {detail}')


@dataclass
class ProviderCandidate:
    url: str
    url_idx: int
    key: str
    model_name: str
    api_config: dict
    prefix_id: Optional[str] = None
    # Max concurrent in-flight requests before the resolver sinks this
    # candidate to the at-capacity tier (sourced from the chain entry's
    # max_concurrent). None = no configured limit.
    max_concurrent: Optional[int] = None
    # Position in the original failover list (0 = primary). Surfaced to the
    # frontend so the UI can say "answered by backup #2".
    position: int = 0


def _health_status(health_cache: Optional[dict], url: str) -> str:
    """Return 'healthy' | 'unhealthy' | 'unknown' for a given provider URL."""
    if not health_cache:
        return 'unknown'
    entry = health_cache.get(url)
    if not entry:
        return 'unknown'
    # An unhealthy_until timestamp overrides a stale healthy status.
    unhealthy_until = entry.get('unhealthy_until')
    if unhealthy_until and unhealthy_until > time.time():
        return 'unhealthy'
    return entry.get('status', 'unknown')


async def resolve_failover_candidates(
    request: Request,
    model_info,
    payload: dict,
    skip_urls: Optional[list[str]] = None,
    health_cache: Optional[dict] = None,
) -> list[ProviderCandidate]:
    """Build the ordered candidate list for a chat completion.

    Resolution precedence (most specific wins):

    1. **Workspace-model-level chain**: ``model_info.meta.failover_providers``
       — when a workspace model has its own ordered list configured, that
       overrides everything else.
    2. **Base-model-level chain**: the ``models.failover_map`` DB config key
       — a global admin map keyed by model id. The requested model is the
       implicit primary; entries from the map become backups #1..N. This
       lets one config protect every workspace model (and direct chats)
       that share a base model.
    3. **Legacy**: single provider derived from the OPENAI_MODELS cache.

    Filters applied to the resolved chain:
    - ``skip_urls`` (set by the retry-with-different-provider button).
    - Health cache — unhealthy providers sink to the end of the list but
      remain present, so an all-unhealthy chain still degrades rather
      than hard-failing.

    Note: per-provider capability tags (tools/vision) were removed — the
    wrapper model's own capability settings (inherited from the base model)
    govern routing; providers no longer carry redundant capability asserts.
    """
    skip_set = set(skip_urls or [])

    # Config is now DB-backed (upstream removed app.state.config); read the
    # OpenAI connection lists directly from Config (mirrors get_openai_runtime_config).
    _rt = await Config.get_many('openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    base_urls = _rt.get('openai.api_base_urls') or []
    keys = _rt.get('openai.api_keys') or []
    configs = _rt.get('openai.api_configs') or {}
    models_state = request.app.state.OPENAI_MODELS or {}

    def _build_candidate(
        model_id: str, position: int, max_concurrent: Optional[int] = None
    ) -> Optional[ProviderCandidate]:
        """Resolve a `$models`-style id into a concrete (url, key, ...) candidate.

        Returns None if the model isn't in the OPENAI_MODELS cache (stale or
        unknown), or if its connection has been skipped.
        """
        model_entry = models_state.get(model_id)
        if not model_entry:
            return None
        idx = model_entry.get('urlIdx')
        if idx is None or idx >= len(base_urls):
            return None
        url = base_urls[idx]
        if url in skip_set:
            return None
        key = keys[idx] if idx < len(keys) else ''
        api_config = configs.get(str(idx), configs.get(url, {}))
        return ProviderCandidate(
            url=url,
            url_idx=idx,
            key=key,
            # Pass the prefixed id through; _try_provider_candidate strips
            # the prefix_id before it goes on the wire, matching legacy
            # single-provider behavior.
            model_name=model_id,
            api_config=api_config,
            prefix_id=api_config.get('prefix_id'),
            max_concurrent=max_concurrent if isinstance(max_concurrent, int) and max_concurrent > 0 else None,
            position=position,
        )

    # Chain source resolution:
    # 1. `failover_source == 'global'` on the wrapper's meta → the admin-
    #    configured chain for this wrapper model id (DB key
    #    `models.wrapper_provider_chains`). The workspace failover_providers
    #    list is ignored for resolution (kept so users can flip back).
    # 2. Otherwise → workspace `failover_providers` (custom), falling through
    #    to the legacy global base-model map below when unset.
    #
    # NOTE: payload['model'] has already been rewritten to base_model_id by
    # the caller when this is a wrapper — global chains are keyed by the
    # WRAPPER id, so key off model_info.id, never the payload.
    failover = None
    if model_info and model_info.meta:
        if getattr(model_info.meta, 'failover_source', None) == 'global':
            chains_data = await Config.get('models.wrapper_provider_chains') or []
            # Backwards compat: an earlier fork version stored this as
            # ``{wrapper_id: [entries]}`` keyed by wrapper id. If we see a
            # dict, fall back to the first non-empty value so legacy
            # configs keep working until the admin re-saves through the
            # new (flat) admin page.
            if isinstance(chains_data, dict):
                log.warning(
                    'resolve_failover_candidates: legacy per-wrapper dict '
                    'format for models.wrapper_provider_chains; falling back '
                    'to the first non-empty chain.'
                )
                chains_data = next(
                    (v for v in chains_data.values() if v),
                    [],
                )
            if not isinstance(chains_data, list):
                chains_data = []
            # PersistentConfig deserialises to plain dicts — normalise to the
            # attribute-access shape the loop below expects (carrying
            # max_concurrent through for the capacity tier).
            failover = [
                entry
                if not isinstance(entry, dict)
                else SimpleNamespace(model_id=entry.get('model_id'), max_concurrent=entry.get('max_concurrent'))
                for entry in chains_data
            ]
        elif getattr(model_info.meta, 'failover_providers', None):
            failover = model_info.meta.failover_providers

    candidates: list[ProviderCandidate] = []

    if failover:
        # Workspace-level (custom) or admin global chain wins entirely.
        # Entries are FailoverProvider (Pydantic) or SimpleNamespace (global
        # chain dicts) — both carry max_concurrent via getattr.
        for position, entry in enumerate(failover):
            candidate = _build_candidate(entry.model_id, position, getattr(entry, 'max_concurrent', None))
            if candidate is None:
                log.warning(
                    'Workspace failover provider model_id=%s not resolvable against current OPENAI_MODELS / config; skipping.',
                    entry.model_id,
                )
                continue
            candidates.append(candidate)
    else:
        # No workspace chain. Always start with the requested model as the
        # implicit primary, then expand from the global base-model map if
        # an entry exists for that id.
        requested_id = payload.get('model')
        primary = _build_candidate(requested_id, 0)
        if primary is not None:
            candidates.append(primary)

        global_map = (await Config.get('models.failover_map')) or {}
        # PersistentConfig deserialises to plain dicts/lists, not Pydantic
        # FailoverProvider instances — handle dicts defensively.
        chain = global_map.get(requested_id) or []
        for offset, raw_entry in enumerate(chain):
            entry = raw_entry if isinstance(raw_entry, dict) else getattr(raw_entry, '__dict__', {})
            target_id = entry.get('model_id')
            if not target_id:
                continue
            candidate = _build_candidate(target_id, offset + 1, entry.get('max_concurrent'))
            if candidate is None:
                log.warning(
                    'Base-model failover entry model_id=%s (parent=%s) not resolvable; skipping.',
                    target_id,
                    requested_id,
                )
                continue
            candidates.append(candidate)

    # Capacity tier: batch-fetch in-flight counts for candidates carrying a
    # max_concurrent limit (async fetch — the sort key itself must stay sync;
    # unlimited candidates skip the round trip entirely). Providers at or over
    # their limit sink below healthy/unknown but ABOVE unhealthy: capacity is
    # a transient, self-clearing condition, so a busy-but-healthy provider
    # still beats a broken one, and configured order is preserved among
    # equals (stable sort) — the user's "2 on primary, then secondary, …"
    # admission pattern.
    limited = [c for c in candidates if c.max_concurrent is not None]
    inflight = await provider_inflight.counts(request.app.state, [c.url for c in limited]) if limited else {}

    # Sink unhealthy providers to the end, but keep configured order among
    # equals so the primary still beats backup if both are healthy.
    def health_rank(c: ProviderCandidate) -> int:
        limit = c.max_concurrent
        if limit is not None and inflight.get(c.url, 0) >= limit:
            return 2  # at capacity
        status = _health_status(health_cache, c.url)
        if status == 'healthy':
            return 0
        if status == 'unknown':
            return 1
        return 3  # unhealthy

    # Stable sort preserves configured order within each health tier.
    candidates.sort(key=health_rank)
    return candidates


def is_retryable_error(status_code: Optional[int], exc: Optional[BaseException]) -> bool:
    """Does this failure mean we should try the next failover provider?

    Treat as retryable when the current provider clearly *can't fulfil*
    this request — i.e. the model is unavailable at this URL (400/401/403/404
    typically mean "model not found", bad auth, access denied, or endpoint
    missing) or is too busy to serve it (429, 5xx).

    Carve-outs for *client-side* failures that should NOT loop across
    providers — the next provider would respond identically:

    - 413 Payload Too Large: request body is too big for any provider.
    - 422 Unprocessable Entity: validation error in the request payload.
    """
    if exc is not None:
        # Network-layer: aiohttp.ClientError, asyncio.TimeoutError, OSError, etc.
        return True
    if status_code is None:
        return True
    if status_code in (400, 401, 403, 404, 429):
        # 400/401/403/404 → "this provider can't serve this model"; try the
        # next backup (e.g. admin disabled a connection, or the connection's
        # model list is stale). 429 → rate limit; try the next backup.
        return True
    if 500 <= status_code < 600:
        return True
    # 413 / 422 (and any other 4xx) — request-shape problems; retrying
    # would just hit the same error on every provider.
    return False


def parse_retry_after(header_value: Optional[str]) -> Optional[int]:
    """Parse a Retry-After HTTP header into seconds-from-now.

    Accepts either an integer delta ("60") or an HTTP date
    ("Wed, 21 Oct 2015 07:28:00 GMT"). Returns None if unparseable.
    """
    if not header_value:
        return None
    value = header_value.strip()
    try:
        return max(0, int(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        delta = dt.timestamp() - time.time()
        return max(0, int(delta))
    except (TypeError, ValueError):
        return None


# ── Failover capacity queue ──────────────────────────────────────────────
#
# When EVERY provider in a resolved candidate list is limited
# (``max_concurrent`` set) and every one is already at that limit, the
# resolver still hands back candidates (capacity is a transient tier — a
# busy-but-healthy provider outranks a broken one). Without a queue the
# retry loop fires straight into a saturated provider and the user eats an
# upstream 429. Instead we park the request in a short in-process FIFO and
# poll the in-flight counters until a slot frees or the deadline passes.
#
# The queue is a module-level deque — one FIFO per worker process, matching
# the consistency bar of the in-memory ``provider_inflight`` counters (a
# Redis-backed inflight store makes the *predicate* multi-node correct; the
# queue's own length accounting stays per-process, which is acceptable for
# v1). Deque append/remove are atomic under the GIL and we never await
# between the length check and the append, so no asyncio.Lock is needed.

DEFAULT_QUEUE_FULL_MESSAGE = 'LLM Load is at maximum capacity right now, retry in 30 seconds'

# Minimum seconds between in-flight re-checks while queued — prevents a
# hot-spin when an admin saves a sub-half-second poll interval.
MIN_POLL_INTERVAL_SECONDS = 0.5

# Floor for the total time a queued request may wait, regardless of
# ``max_queue_length × poll_interval_seconds``.
MIN_QUEUE_DEADLINE_SECONDS = 30.0


@dataclass
class _QueueEntry:
    """One waiting request in the failover capacity FIFO."""

    enqueued_at: float  # time.monotonic() when the request entered the queue


# Module-level FIFO of waiting requests (see the process-local note above).
_failover_queue: deque = deque()


def capacity_queue_targets(candidates: list[ProviderCandidate]) -> Optional[list[ProviderCandidate]]:
    """Structural half of the capacity-queue predicate (no I/O).

    Returns the limited candidates when queueing is structurally possible —
    at least one limited candidate exists AND no unlimited candidate is in
    the list (an unlimited candidate always has room; the resolver already
    ranks it above the at-capacity tier, so there is always somewhere to
    go). Returns None when the request should be admitted immediately
    regardless of in-flight counts.
    """
    limited = [c for c in candidates if c.max_concurrent is not None]
    if not limited:
        return None
    if len(limited) < len(candidates):
        # At least one unlimited candidate is present.
        return None
    return limited


async def _has_free_capacity(app_state, limited: list[ProviderCandidate]) -> bool:
    """True when any limited candidate sits below its max_concurrent."""
    inflight = await provider_inflight.counts(app_state, [c.url for c in limited])
    # The None re-check is for the type checker — `limited` is pre-filtered
    # to candidates with a concrete max_concurrent.
    return any(c.max_concurrent is not None and inflight.get(c.url, 0) < c.max_concurrent for c in limited)


async def acquire_capacity_or_queue(
    request: Request,
    candidates: list[ProviderCandidate],
    *,
    model_info=None,
    payload: Optional[dict] = None,
    skip_urls: Optional[list[str]] = None,
    health_cache: Optional[dict] = None,
    max_queue_length: int = 10,
    poll_interval_seconds: float = 2.0,
    full_message: str = DEFAULT_QUEUE_FULL_MESSAGE,
) -> list[ProviderCandidate]:
    """Gate a chat completion on provider capacity, queueing when saturated.

    Thin wrapper around ``resolve_failover_candidates`` output: if every
    candidate is limited and at its ``max_concurrent``, the request waits in
    the in-process FIFO (polling ``provider_inflight.counts`` every
    ``poll_interval_seconds``) until a slot frees, the client disconnects,
    or the deadline (``max_queue_length × poll_interval_seconds``, floored
    at 30s) elapses.

    - Returns the candidates unchanged when capacity is (or becomes)
      available; on the queue-success path the list is re-resolved ONCE for
      a fresh ordering (health/capacity tiers may have shifted during the
      wait) instead of trusting the stale pre-queue ordering.
    - Raises ``HTTPException(429, full_message)`` when the queue is full
      (``max_queue_length`` waiters already present — with 0 slots this
      rejects immediately), the deadline passes, or the client disconnects.
    - ``asyncio.CancelledError`` propagates untouched — cancellation means
      the server is shutting down or the task was revoked, not "busy".

    The resolver keyword arguments (``model_info`` / ``payload`` /
    ``skip_urls`` / ``health_cache``) are only used for the single
    re-resolve on queue success, and mirror the original call site's args.
    """
    limited = capacity_queue_targets(candidates)
    if limited is None:
        # No limited candidates, or an unlimited candidate is present.
        return candidates

    if await _has_free_capacity(request.app.state, limited):
        return candidates

    # Queue path: every limited candidate is at or over its limit.
    if len(_failover_queue) >= max_queue_length:
        # Queue full. max_queue_length == 0 lands here too, making the
        # queue "disabled": all-at-capacity requests are rejected at once.
        raise HTTPException(status_code=429, detail=full_message)

    poll_interval_seconds = max(float(poll_interval_seconds), MIN_POLL_INTERVAL_SECONDS)

    entry = _QueueEntry(enqueued_at=time.monotonic())
    _failover_queue.append(entry)
    try:
        deadline = time.monotonic() + max(MIN_QUEUE_DEADLINE_SECONDS, max_queue_length * poll_interval_seconds)
        while True:
            await asyncio.sleep(poll_interval_seconds)
            if await request.is_disconnected():
                raise HTTPException(status_code=429, detail=full_message)
            if await _has_free_capacity(request.app.state, limited):
                # A slot freed — re-resolve once for the fresh ordering
                # (not on every poll; saves DB churn while waiting).
                fresh = await resolve_failover_candidates(
                    request=request,
                    model_info=model_info,
                    payload=payload if payload is not None else {},
                    skip_urls=skip_urls,
                    health_cache=health_cache,
                )
                return fresh or candidates
            if time.monotonic() >= deadline:
                raise HTTPException(status_code=429, detail=full_message)
    finally:
        # try/finally (not except) so the entry is removed on EVERY exit
        # path — success, 429, disconnect, CancelledError (which must
        # propagate rather than be swallowed into a 429), any exception.
        try:
            _failover_queue.remove(entry)
        except ValueError:
            pass  # already removed; defensive only
