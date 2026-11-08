"""Guards for the context-compaction token estimator.

The v0.13.2 upstream sync rewrote ``utils/context_compaction.py`` on top of
upstream's own file and the fork's additions were re-applied — but the fork's
**tiktoken-based estimator** was missed, and the merge shipped the bare
``len(text) // 4`` heuristic instead.

That heuristic is not a rough approximation of tiktoken, it is wrong in the
direction that matters:

    content                     len//4      tiktoken    error
    English prose                 225         201      -11%
    JSON tool payload              95         369      -74%
    non-ASCII text                40         160      -75%

So a tool-heavy chat was counted at roughly a quarter of its real size and
**compaction fired several turns later than configured** — on exactly the
content the fork's compaction work exists to bound. The module's own
docstring still claimed "falls back to a tiktoken estimate" while the code
did no such thing.

``tiktoken==0.13.0`` is a hard dependency in both ``requirements.txt`` and
``requirements-slim.txt``, so the estimator has no excuse not to be used.

These tests pin the estimator's behaviour so the next sync cannot silently
drop it again, and they cover the heuristic fallback for deployments where
tiktoken cannot be loaded.
"""

import json

import pytest

import open_webui.utils.context_compaction as cc
from open_webui.utils.context_compaction import (
    _estimate_context_tokens,
    _estimate_messages_tokens,
    _estimate_tokens,
    _exceeds_token_threshold,
)


@pytest.fixture(autouse=True)
def _reset_encoder_cache():
    """Keep the module-level encoder cache from leaking between tests."""
    saved = (getattr(cc, '_TIKTOKEN_ENC', None), getattr(cc, '_TIKTOKEN_AVAILABLE', None))
    yield
    cc._TIKTOKEN_ENC, cc._TIKTOKEN_AVAILABLE = saved


def _encoder():
    """The tiktoken encoder, or None when the module has no tiktoken path at all."""
    getter = getattr(cc, '_get_tiktoken_encoder', None)
    return getter() if getter else None


def test_the_module_has_a_tiktoken_path():
    """The estimator must have a tiktoken path at all.

    Asserted separately (rather than by importing the helper directly) so that
    a module without it fails this test instead of erroring at collection.
    """
    assert hasattr(cc, '_get_tiktoken_encoder'), (
        'context_compaction has no tiktoken estimator - the estimator reverted to the len//4 heuristic'
    )


def test_tiktoken_encoder_is_available():
    """tiktoken is a hard dependency, so the encoder must load."""
    encoder = _encoder()
    assert encoder is not None, 'tiktoken encoder failed to load; the estimator is on the heuristic path'


def test_estimate_tokens_matches_tiktoken():
    text = 'The quick brown fox jumps over the lazy dog. ' * 20
    encoder = _encoder()
    expected = len(encoder.encode(text, disallowed_special=()))
    assert _estimate_tokens(text) == expected


def test_estimate_tokens_is_not_the_char_heuristic():
    """The estimator must not have silently reverted to len//4."""
    text = 'The quick brown fox jumps over the lazy dog. ' * 20
    assert _estimate_tokens(text) != max(1, len(text) // 4)


def test_tool_payloads_are_counted_accurately():
    """The case the heuristic got wrong by ~4x.

    A tool result is a JSON blob. ``len//4`` counted it at roughly a
    quarter of its real token count, so a tool-heavy chat looked far
    smaller than it was and compaction fired too late.
    """
    payload = json.dumps({'tool': 'search', 'args': list(range(200))})
    estimated = _estimate_tokens(payload)
    heuristic = max(1, len(payload) // 4)
    encoder = _encoder()
    expected = len(encoder.encode(payload, disallowed_special=()))

    assert estimated == expected
    # The heuristic is not merely different, it is dramatically low.
    assert heuristic < expected * 0.5, f'heuristic {heuristic} is not the underestimate this guards against'


def test_estimate_tokens_handles_non_strings():
    assert _estimate_tokens(None) == 0
    assert _estimate_tokens('') == 0
    assert _estimate_tokens({'a': 1}) > 0
    assert _estimate_tokens([1, 2, 3]) > 0


def test_estimate_tokens_falls_back_when_tiktoken_is_unavailable(monkeypatch):
    """A deployment without tiktoken must still get a sane number."""
    monkeypatch.setattr(cc, '_TIKTOKEN_ENC', None)
    monkeypatch.setattr(cc, '_TIKTOKEN_AVAILABLE', False)

    text = 'hello world' * 10
    assert _estimate_tokens(text) == max(1, len(text) // 4)


def test_estimate_tokens_survives_a_broken_encoder(monkeypatch):
    """A failing encode() must fall through to the heuristic, not raise."""

    class Broken:
        def encode(self, *_args, **_kwargs):
            raise RuntimeError('boom')

    monkeypatch.setattr(cc, '_TIKTOKEN_ENC', Broken())
    monkeypatch.setattr(cc, '_TIKTOKEN_AVAILABLE', True)

    text = 'hello world' * 10
    assert _estimate_tokens(text) == max(1, len(text) // 4)


def test_threshold_check_uses_the_real_token_count():
    """The compaction trigger must fire on the real size, not the heuristic."""
    payload = json.dumps({'tool': 'search', 'args': list(range(400))})
    message = {'role': 'tool', 'content': payload}
    real = cc._estimate_messages_tokens([message])

    # A threshold below the real count must trip it; a threshold far above
    # must not. The heuristic would have under-counted `real` by ~4x and
    # failed to trip the first case.
    assert _exceeds_token_threshold([message], '', None, real - 1) is True
    assert _exceeds_token_threshold([message], '', None, real + 10_000) is False
    assert _exceeds_token_threshold([message], '', None, 0) is False


def test_estimate_context_tokens_reports_its_source():
    messages = [{'role': 'user', 'content': 'hello'}]
    tokens, source = _estimate_context_tokens(messages, '', None)
    assert tokens > 0
    assert source in ('estimated', 'usage', 'summary')
