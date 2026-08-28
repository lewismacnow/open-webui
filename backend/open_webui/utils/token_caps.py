"""Per-target token usage caps (user / group / model / api_key).

Mirrors the rate_limit.py pattern: Redis with in-memory fallback.
Key shape: ``{REDIS_KEY_PREFIX}:tokcap:{type}:{id}:{window}:{bucket}``.

The admin UI in ``admin/Settings/TokenCaps.svelte`` (a future PR)
configures per-target caps in 1M-token increments. Internally the
config stores raw tokens; the admin form takes "millions of tokens"
and multiplies by 1_000_000 on save. This module only sees raw tokens.

Precedence: "whichever limit is hit" — when a request would exceed ANY
applicable cap (user, the user's groups, the model, the api key), the
first-hit cap is reported and the request is rejected with HTTP 429.
Per the user's spec, the api_key cap is **summed with the owning
user's cap** (same billing pool), not a separate cap.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from open_webui.env import REDIS_KEY_PREFIX

log = logging.getLogger(__name__)


# Window name → seconds per window. UTC-aligned: hourly rolls at the
# top of every hour, daily at UTC midnight, etc. week starts Monday.
WINDOW_SECONDS: dict[str, int] = {
    'hourly': 60 * 60,
    'daily': 24 * 60 * 60,
    'weekly': 7 * 24 * 60 * 60,
    'monthly': 30 * 24 * 60 * 60,  # approximate — the bucket key uses start-of-month epoch
}


def _window_start_epoch(window: str, now_epoch: float) -> int:
    """Return the epoch of the start of the current bucket for ``window``."""
    if window == 'hourly':
        return int(now_epoch // 3600 * 3600)
    if window == 'daily':
        return int(now_epoch // 86400 * 86400)
    if window == 'weekly':
        # Monday-anchored week. 1970-01-01 was a Thursday so adjust to find
        # the most recent Monday at or before ``now_epoch``.
        day = 86400
        days_since_monday = (int(now_epoch // day) + 3) % 7
        return int((int(now_epoch // day) - days_since_monday) * day)
    if window == 'monthly':
        # First day of the current month (UTC). Manual calculation to
        # avoid pulling in the `calendar` module.
        import datetime

        dt = datetime.datetime.fromtimestamp(int(now_epoch), tz=datetime.timezone.utc)
        return int(datetime.datetime(dt.year, dt.month, 1, tzinfo=datetime.timezone.utc).timestamp())
    return int(now_epoch)


@dataclass
class TokenCap:
    """A single cap configuration row. The token counts are raw tokens
    (the admin UI multiplies its "millions of tokens" input by 1e6)."""

    target_type: str  # 'user' | 'group' | 'model' | 'api_key'
    target_id: str
    hourly: int = 0
    daily: int = 0
    weekly: int = 0
    monthly: int = 0


@dataclass
class CapHit:
    """Returned by ``check`` when a request would exceed a cap. The
    handler maps this to an HTTP 429 with a descriptive body."""

    cap: TokenCap
    window: str
    current: int
    would_be: int
    limit: int


class _TokenCaps:
    """Redis-backed (with in-memory fallback) token-usage cap tracker.

    Storage layout:
        Key:   ``{REDIS_KEY_PREFIX}:tokcap:{type}:{id}:{window}:{bucket_start}``
        Value: integer token count for the bucket
    """

    _memory: dict[str, int] = {}

    def _bucket_key(self, target_type: str, target_id: str, window: str, bucket: int) -> str:
        return f'{REDIS_KEY_PREFIX}:tokcap:{target_type}:{target_id}:{window}:{bucket}'

    def _redis_available(self, app_state: Any) -> bool:
        if app_state is None:
            return False
        return getattr(app_state, 'redis', None) is not None

    # ---------- redis / memory helpers ----------
    def _get_count(self, app_state: Any, target_type: str, target_id: str, window: str, bucket: int) -> int:
        key = self._bucket_key(target_type, target_id, window, bucket)
        if self._redis_available(app_state):
            try:
                v = app_state.redis.get(key)
                return int(v) if v else 0
            except Exception:
                pass
        return self._memory.get(key, 0)

    def _incr(
        self,
        app_state: Any,
        target_type: str,
        target_id: str,
        window: str,
        bucket: int,
        amount: int,
    ) -> int:
        key = self._bucket_key(target_type, target_id, window, bucket)
        if self._redis_available(app_state):
            try:
                new = app_state.redis.incrby(key, amount)
                # First-increment in a bucket — set TTL slightly beyond the
                # window so stale buckets self-clean.
                ttl = WINDOW_SECONDS.get(window, 86400) + 60
                app_state.redis.expire(key, ttl)
                return int(new)
            except Exception:
                pass
        # Memory fallback
        cur = self._memory.get(key, 0)
        new = cur + amount
        self._memory[key] = max(0, new)
        return new

    # ---------- public API ----------
    def collect_applicable_targets(
        self,
        *,
        user_id: Optional[str],
        group_ids: Iterable[str],
        model_id: Optional[str],
        api_key_user_id: Optional[str] = None,
    ) -> list[tuple[str, str]]:
        """Build the list of (type, id) targets the cap tracker should
        check. The api_key's owner is the same user as the caller when
        the call was authenticated by an API key (so the api_key and
        user are the SAME pool — the spec says summed)."""
        out: list[tuple[str, str]] = []
        if user_id:
            out.append(('user', user_id))
        for gid in group_ids or ():
            out.append(('group', gid))
        if model_id:
            out.append(('model', model_id))
        return out

    def check(
        self,
        app_state: Any,
        caps: dict[tuple[str, str], TokenCap],
        targets: list[tuple[str, str]],
        projected_tokens: int,
    ) -> Optional[CapHit]:
        """Pre-flight: if any cap would be exceeded by ``projected_tokens``,
        return the first-hit ``CapHit``. Otherwise ``None``.

        ``caps`` is a flat ``{(type, id): TokenCap}`` map for the targets
        the caller cares about — the caller pulls it from the DB config
        so this module stays DB-agnostic.
        """
        if not targets or projected_tokens <= 0:
            return None
        now = time.time()
        # Deterministic order so the same request always reports the
        # same first-hit cap (helps with debugging).
        ordered = sorted(targets, key=lambda t: (t[0], t[1]))
        for target_type, target_id in ordered:
            cap = caps.get((target_type, target_id))
            if cap is None:
                continue
            for window in ('hourly', 'daily', 'weekly', 'monthly'):
                limit = getattr(cap, window)
                if not limit or limit <= 0:
                    continue
                bucket = _window_start_epoch(window, now)
                current = self._get_count(app_state, target_type, target_id, window, bucket)
                would_be = current + projected_tokens
                if would_be > limit:
                    return CapHit(
                        cap=cap,
                        window=window,
                        current=current,
                        would_be=would_be,
                        limit=limit,
                    )
        return None

    def record(
        self,
        app_state: Any,
        targets: list[tuple[str, str]],
        tokens: int,
    ) -> None:
        """Post-flight: increment every target's window counters by ``tokens``."""
        if tokens <= 0 or not targets:
            return
        now = time.time()
        for target_type, target_id in targets:
            for window in ('hourly', 'daily', 'weekly', 'monthly'):
                bucket = _window_start_epoch(window, now)
                self._incr(app_state, target_type, target_id, window, bucket, tokens)


TokenCaps = _TokenCaps()
