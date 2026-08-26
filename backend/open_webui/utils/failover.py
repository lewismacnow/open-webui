"""Failover routing for OpenAI-compatible workspace models.

A workspace model may list an ordered set of providers in
`Model.meta.failover_providers`. At request time this module resolves
that list into concrete (URL, key, model name) candidates the chat
handler can try in order. Providers marked unhealthy in the app-state
cache are deprioritised, but never removed outright — if every provider
looks unhealthy we still try them rather than hard-failing.
"""

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import logging
import time
from typing import Optional

from fastapi import Request

from open_webui.models.config import Config

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

    def _build_candidate(model_id: str, position: int) -> Optional[ProviderCandidate]:
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
            position=position,
        )

    failover = None
    if model_info and model_info.meta and getattr(model_info.meta, 'failover_providers', None):
        failover = model_info.meta.failover_providers

    candidates: list[ProviderCandidate] = []

    if failover:
        # Workspace-level chain wins entirely.
        for position, entry in enumerate(failover):
            candidate = _build_candidate(entry.model_id, position)
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
            candidate = _build_candidate(target_id, offset + 1)
            if candidate is None:
                log.warning(
                    'Base-model failover entry model_id=%s (parent=%s) not resolvable; skipping.',
                    target_id,
                    requested_id,
                )
                continue
            candidates.append(candidate)

    # Sink unhealthy providers to the end, but keep configured order among
    # equals so the primary still beats backup if both are healthy.
    def health_rank(c: ProviderCandidate) -> int:
        status = _health_status(health_cache, c.url)
        if status == 'healthy':
            return 0
        if status == 'unknown':
            return 1
        return 2

    # Stable sort preserves configured order within each health tier.
    candidates.sort(key=health_rank)
    return candidates


def is_retryable_error(status_code: Optional[int], exc: Optional[BaseException]) -> bool:
    """Does this failure mean we should try the next failover provider?"""
    if exc is not None:
        # Network-layer: aiohttp.ClientError, asyncio.TimeoutError, OSError, etc.
        return True
    if status_code is None:
        return True
    if status_code == 429:
        return True
    if 500 <= status_code < 600:
        return True
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
