"""Accurate token counter + recording hook for API token usage.

The pre-flight counter uses ``tiktoken`` to produce an accurate estimate
of how many tokens the request will consume. We try the model's
named encoding first (``encoding_for_model``); fall back to
``cl100k_base`` (the same BPE family used by ``gpt-3.5-turbo`` /
``gpt-4``); fall back to a ``len/4`` chars heuristic as the very last
resort (e.g. exotic local models where tiktoken has no encoding).

The user asked for an accurate counter, so tiktoken is the default.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

try:
    import tiktoken  # type: ignore

    _TIKTOKEN_AVAILABLE = True
except Exception:  # pragma: no cover - tiktoken is in requirements.txt
    tiktoken = None  # type: ignore
    _TIKTOKEN_AVAILABLE = False


log = logging.getLogger(__name__)


# Cached encodings — re-resolving per call is a 10-50µs hit and the
# tiktoken registry scan is global state. The cache key is the encoding
# name (e.g. ``"cl100k_base"``) so we share across models that use the
# same family.
_ENCODING_CACHE: dict[str, Any] = {}


def _get_encoding(model_id: str | None) -> Optional[Any]:
    """Return the tiktoken encoding for ``model_id``, or None on failure.

    Strategy:
    1. Try ``encoding_for_model(model_id)`` — covers gpt-4, gpt-4o,
       gpt-3.5-turbo, o1-preview, text-embedding-ada-002, etc.
    2. Fall back to ``cl100k_base`` — works well enough for any
       OpenAI-family tokeniser the named-encoding registry doesn't know.
    3. Return None so the caller can use the chars/4 heuristic.
    """
    if not _TIKTOKEN_AVAILABLE or tiktoken is None:
        return None
    # Strip any model-name suffix (e.g. "gpt-4o-2024-08-06") — tiktoken
    # expects the family root.
    family = (model_id or '').split('-')[0] if model_id else ''
    if family in _ENCODING_CACHE:
        return _ENCODING_CACHE[family]
    try:
        enc = tiktoken.encoding_for_model(model_id or 'gpt-4o')
    except Exception:
        try:
            enc = tiktoken.get_encoding('cl100k_base')
        except Exception:
            log.debug('tiktoken: no encoding available for %r', model_id)
            return None
    _ENCODING_CACHE[family or '_default'] = enc
    return enc


def _extract_text(content: Any) -> str:
    """Pull the text out of a chat-completions content union.

    Handles string, list of {text, image_url} parts, and nested shapes.
    Returns '' for non-text content (e.g. pure image blocks) — those
    contribute to the bill but a char/4 estimate from an empty string
    is fine; the upstream returns the authoritative number on the way
    out anyway.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text' and item.get('text'):
                    parts.append(str(item['text']))
                # image_url / tool_calls etc. — we can't tokenize binary
                # content so we conservatively assume 0 here; the upstream's
                # post-flight usage figure is authoritative.
            elif isinstance(item, str):
                parts.append(item)
        return ''.join(parts)
    if content is None:
        return ''
    return str(content)


def count_request_tokens(messages: Iterable[dict] | None, model_id: str | None) -> int:
    """Pre-flight estimate of the prompt token count.

    Uses tiktoken for an accurate count when an encoding is available,
    otherwise falls back to ``len/4`` chars heuristic. Includes the
    per-message ``<|im_start|>`` / ``<|im_end|>`` overhead the upstream
    uses for chat-format requests (a small but real 3-4 token cost per
    message).
    """
    if not messages:
        return 0
    enc = _get_encoding(model_id)
    if enc is None:
        # Heuristic — chars/4. Conservative; the upstream's post-flight
        # usage is authoritative for the actual billing number.
        return max(1, sum(len(_extract_text(m.get('content'))) for m in messages) // 4)

    total = 0
    for m in messages:
        # Per-message chat-format overhead. tiktoken's own ``encoding_for_model``
        # doesn't include this automatically; the upstream's tokenizer
        # does. For pre-flight it's a useful approximation.
        total += 3  # <|im_start|> role <|im_sep|>
        total += len(enc.encode(_extract_text(m.get('content')) or ''))
        # Role marker contribution (already covered by the +3 above for
        # the role name; we don't need to encode the literal role text).
    total += 3  # <|im_end|> reply primer
    return total


def extract_response_tokens(usage_obj: Any) -> tuple[int, int]:
    """Read prompt / completion totals from an OpenAI-style ``usage`` dict.

    Handles both the standard (``prompt_tokens`` / ``completion_tokens``)
    and Anthropic-style (``input_tokens`` / ``output_tokens``) shapes.
    Returns (prompt, completion). If the dict is missing or unparseable,
    returns (0, 0) — the caller should fall back to its pre-flight
    estimate.
    """
    if not isinstance(usage_obj, dict):
        return (0, 0)
    prompt = usage_obj.get('prompt_tokens') or usage_obj.get('input_tokens') or 0
    completion = usage_obj.get('completion_tokens') or usage_obj.get('output_tokens') or 0
    try:
        return (max(0, int(prompt)), max(0, int(completion)))
    except (TypeError, ValueError):
        return (0, 0)
