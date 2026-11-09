"""Tests for the failover chain RESOLVER (``utils/failover.py``).

The production symptom these cover: a wrapper model configured with a
custom failover chain resolved to a SINGLE candidate, so when the primary
provider 500'd there was nowhere to fail over to ("no more candidates")
and the operator only got a generic "check the chain configuration"
warning that named neither the entry that vanished nor the reason.

Covered here:

1. A chain entry whose model id is not in the OPENAI_MODELS snapshot is
   dropped AND the reason is reported.
2. A chain entry whose snapshot ``urlIdx`` is out of range (a connection
   was removed after the snapshot was taken) is dropped AND the reason is
   reported.
3. A chain entry whose resolved connection was explicitly skipped by the
   request is dropped AND the reason is reported.
4. A STALE ``urlIdx`` (the admin removed a connection before it, so the
   index now points at a different connection) must NOT route the request
   to the wrong provider — the entry follows its URL to the connection
   that actually serves it.
5. A wrapper whose ``failover_source`` says 'custom' but whose chain is
   EMPTY is reported as such instead of silently degrading to the single
   base provider.
6. The summary warning names the dropped entries and their reasons.
7. Chain semantics are unchanged: configured order wins among equals and
   the health ranking still applies.
"""

import logging
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from open_webui.models.config import Config
from open_webui.utils.failover import resolve_failover_candidates

ZAI = 'https://api.z.ai/api/coding/paas/v4'
MINIMAX = 'https://api.minimax.chat/v1'
OTHER = 'https://api.other.test/v1'


@pytest.fixture
def runtime(monkeypatch):
    """Install a fake Config so the resolver reads a known connection set."""

    def install(base_urls, keys=None, configs=None, failover_map=None, wrapper_chains=None):
        keys = keys if keys is not None else [''] * len(base_urls)
        configs = configs or {}

        async def fake_get_many(*_keys):
            return {
                'openai.api_base_urls': list(base_urls),
                'openai.api_keys': list(keys),
                'openai.api_configs': configs,
            }

        async def fake_get(key, default=None):
            if key == 'models.failover_map':
                return failover_map or {}
            if key == 'models.wrapper_provider_chains':
                return wrapper_chains if wrapper_chains is not None else []
            return default

        monkeypatch.setattr(Config, 'get_many', staticmethod(fake_get_many))
        monkeypatch.setattr(Config, 'get', staticmethod(fake_get))

    return install


def make_request(models_state, snapshot_urls=None):
    """A request whose app.state carries the OPENAI_MODELS snapshot.

    ``snapshot_urls`` is the connection list the snapshot was built from —
    the resolver needs it to tell a stale index from a current one.
    """
    app_state = SimpleNamespace(
        OPENAI_MODELS=models_state,
        OPENAI_MODELS_BASE_URLS=snapshot_urls,
    )
    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/',
            'headers': [],
            'query_string': b'',
            'app': SimpleNamespace(state=app_state),
        }
    )


def make_model_info(model_id='minimax.MiniMax-M3.1-Flash', source='custom', providers=None, base_model_id=None):
    """A workspace-model stand-in carrying the fork's chain metadata."""
    meta = SimpleNamespace(
        failover_source=source,
        failover_providers=[SimpleNamespace(model_id=p, max_concurrent=None) for p in (providers or [])],
    )
    return SimpleNamespace(id=model_id, base_model_id=base_model_id, meta=meta)


@pytest.mark.asyncio
async def test_unknown_model_id_is_dropped_with_a_reason(runtime, caplog):
    """A chain entry the connections do not offer is named AND explained."""
    runtime([ZAI], keys=['k-zai'])
    request = make_request({'other.model': {'urlIdx': 0}}, snapshot_urls=[ZAI])
    model_info = make_model_info(providers=['zai.glm-5.3-flash'])

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
        )

    assert candidates == []
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'zai.glm-5.3-flash' in messages
    assert 'not in the current OPENAI_MODELS snapshot' in messages


@pytest.mark.asyncio
async def test_out_of_range_url_idx_is_dropped_with_a_reason(runtime, caplog):
    """A snapshot index past the end of the connection list is explained."""
    runtime([ZAI], keys=['k-zai'])
    # urlIdx 3 but the snapshot only ever had one connection.
    request = make_request({'zai.glm-5.3-flash': {'urlIdx': 3}}, snapshot_urls=[ZAI])
    model_info = make_model_info(providers=['zai.glm-5.3-flash'])

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
        )

    assert candidates == []
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'out of range' in messages


@pytest.mark.asyncio
async def test_skipped_connection_is_dropped_with_a_reason(runtime, caplog):
    """The retry-with-another-provider skip list is explained, not silent."""
    runtime([ZAI], keys=['k-zai'])
    request = make_request({'zai.glm-5.3-flash': {'urlIdx': 0}}, snapshot_urls=[ZAI])
    model_info = make_model_info(providers=['zai.glm-5.3-flash'])

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
            skip_urls=[ZAI],
        )

    assert candidates == []
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'skipped by the request' in messages


@pytest.mark.asyncio
async def test_stale_url_idx_follows_the_url_not_the_index(runtime):
    """A stale index must not route a chain entry to the wrong provider.

    The snapshot was taken when the connection list was
    ``[MINIMAX, ZAI]``; the admin then removed MINIMAX, so the snapshot's
    ``urlIdx`` (1) now points at a different connection. The entry has to
    keep going to the connection that actually serves the model.
    """
    runtime([ZAI, OTHER], keys=['k-zai', 'k-other'])
    request = make_request(
        {'zai.glm-5.3-flash': {'urlIdx': 1}},
        snapshot_urls=[MINIMAX, ZAI],
    )
    model_info = make_model_info(providers=['zai.glm-5.3-flash'])

    candidates = await resolve_failover_candidates(
        request=request,
        model_info=model_info,
        payload={'model': 'zai.glm-5.3-flash'},
    )

    assert len(candidates) == 1
    # ZAI, not OTHER — and the key belongs to the ZAI connection.
    assert candidates[0].url == ZAI
    assert candidates[0].url_idx == 0
    assert candidates[0].key == 'k-zai'


@pytest.mark.asyncio
async def test_removed_connection_is_dropped_with_a_reason(runtime, caplog):
    """The connection the snapshot was taken from is gone — say so."""
    runtime([OTHER], keys=['k-other'])
    request = make_request(
        {'zai.glm-5.3-flash': {'urlIdx': 0}},
        snapshot_urls=[ZAI],
    )
    model_info = make_model_info(providers=['zai.glm-5.3-flash'])

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
        )

    assert candidates == []
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'was removed from the connection list' in messages


@pytest.mark.asyncio
async def test_empty_custom_chain_is_reported(runtime, caplog):
    """``failover_source='custom'`` with NO chain entries is a misconfiguration.

    The resolver used to fall back to the legacy single-provider path
    silently, so the operator saw "resolved to 1 candidate(s)" with no
    explanation at all.
    """
    runtime([ZAI], keys=['k-zai'])
    request = make_request({'zai.glm-5.3-flash': {'urlIdx': 0}}, snapshot_urls=[ZAI])
    model_info = make_model_info(providers=[], base_model_id='zai.glm-5.3-flash')

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
        )

    # Legacy fallback still yields the single base provider (no behaviour
    # change) — but the operator is now told the chain is empty.
    assert len(candidates) == 1
    assert candidates[0].model_name == 'zai.glm-5.3-flash'
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'failover chain is EMPTY' in messages
    assert 'Failover will NOT be possible' in messages


@pytest.mark.asyncio
async def test_summary_warning_names_the_dropped_entries(runtime, caplog):
    """The summary warning must say WHICH entry was dropped and WHY."""
    runtime([ZAI], keys=['k-zai'])
    request = make_request(
        {'zai.glm-5.3-flash': {'urlIdx': 0}},
        snapshot_urls=[ZAI],
    )
    model_info = make_model_info(providers=['zai.glm-5.3-flash', 'minimax.MiniMax-M3.1-Flash'])

    with caplog.at_level(logging.WARNING, logger='open_webui.utils.failover'):
        candidates = await resolve_failover_candidates(
            request=request,
            model_info=model_info,
            payload={'model': 'zai.glm-5.3-flash'},
        )

    assert len(candidates) == 1
    messages = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'dropped chain entries' in messages
    assert 'minimax.MiniMax-M3.1-Flash' in messages
    assert 'not in the current OPENAI_MODELS snapshot' in messages


@pytest.mark.asyncio
async def test_chain_semantics_unchanged(runtime):
    """Configured order wins among equals; health ranking still applies."""
    runtime([ZAI, MINIMAX], keys=['k-zai', 'k-minimax'])
    request = make_request(
        {
            'zai.glm-5.3-flash': {'urlIdx': 0},
            'minimax.MiniMax-M3.1-Flash': {'urlIdx': 1},
        },
        snapshot_urls=[ZAI, MINIMAX],
    )
    model_info = make_model_info(
        providers=['minimax.MiniMax-M3.1-Flash', 'zai.glm-5.3-flash'],
    )

    candidates = await resolve_failover_candidates(
        request=request,
        model_info=model_info,
        payload={'model': 'minimax.MiniMax-M3.1-Flash'},
        health_cache={},
    )

    # Configured order preserved among equally-healthy providers.
    assert [c.model_name for c in candidates] == [
        'minimax.MiniMax-M3.1-Flash',
        'zai.glm-5.3-flash',
    ]

    # An unhealthy provider sinks to the end but is still present.
    candidates = await resolve_failover_candidates(
        request=request,
        model_info=model_info,
        payload={'model': 'minimax.MiniMax-M3.1-Flash'},
        health_cache={MINIMAX: {'status': 'unhealthy', 'last_check_at': 9999999999}},
    )
    assert [c.model_name for c in candidates] == [
        'zai.glm-5.3-flash',
        'minimax.MiniMax-M3.1-Flash',
    ]
