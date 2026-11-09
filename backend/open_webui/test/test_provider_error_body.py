"""Provider error bodies that arrive with the wrong (or missing) Content-Type.

A provider (z.ai) answers a failed chat completion with HTTP 500 and a JSON
body, but with a blank Content-Type header. aiohttp's ``resp.json()`` refuses
to decode anything that is not ``application/json`` and raises
``ContentTypeError``; the router used to log that as an application error and
then hand the raw text on as an opaque string, so the provider's structured
error never reached the retry machinery.

Every test below runs against a real aiohttp server, so the Content-Type
handling under test is aiohttp's own and not a re-implementation of it.
"""

import logging
from types import SimpleNamespace

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import open_webui.routers.openai as openai_router
from open_webui.routers.openai import _try_provider_candidate
from open_webui.utils.failover import ProviderCandidate, RetryableProviderError
from open_webui.utils.json_codec import JSONCodec

JSON_ERROR = {
    'error': {
        'code': '1234',
        'message': 'Internal network failure, error id: 202611092125437697dd6aee624691, please try again later.',
    }
}
JSON_ERROR_BODY = JSONCodec.dumps(JSON_ERROR)
HTML_ERROR_BODY = '<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>'


class _ProviderServer:
    """Answers every request with the status / Content-Type / body a test sets.

    ``content_type`` is the literal header value; ``''`` sends a blank
    Content-Type (what z.ai does) and ``None`` removes the header entirely.
    """

    def __init__(self):
        self.status = 500
        self.content_type = ''
        self.body = b''
        self._runner = None
        self.base_url = ''

    async def start(self):
        app = web.Application()
        app.router.add_post('/chat/completions', self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '127.0.0.1', 0)
        await site.start()
        self.base_url = f'http://127.0.0.1:{self._runner.addresses[0][1]}'
        return self

    async def stop(self):
        await self._runner.cleanup()

    async def _handle(self, request):
        response = web.Response(status=self.status, body=self.body)
        if self.content_type is None:
            response.headers.pop('Content-Type', None)
        else:
            response.headers['Content-Type'] = self.content_type
        return response

    async def post(self, session):
        """POST to the server and hand the live response to the caller."""
        return await session.post(f'{self.base_url}/chat/completions')


@pytest_asyncio.fixture
async def provider():
    """A local HTTP server that behaves like the (misbehaving) provider."""
    server = _ProviderServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _request():
    """The bare minimum of a Request that the provider call touches."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={})),
        state=SimpleNamespace(),
        cookies={},
    )


def _candidate(url: str) -> ProviderCandidate:
    return ProviderCandidate(
        url=url,
        url_idx=0,
        key='sk-test',
        model_name='test-model',
        api_config={},
    )


def _payload() -> dict:
    return {
        'model': 'test-model',
        'messages': [{'role': 'user', 'content': 'hello'}],
        'stream': False,
    }


def _user():
    return SimpleNamespace(id='u1', name='Tester', email='tester@example.com', role='user')


def _session_getter(session):
    async def _get_session():
        return session

    return _get_session


async def _run_candidate(provider, monkeypatch, session):
    """Run one provider attempt against the local server."""
    monkeypatch.setattr(openai_router, 'get_session', _session_getter(session))
    return await _try_provider_candidate(
        request=_request(),
        candidate=_candidate(provider.base_url),
        payload=_payload(),
        metadata=None,
        user=_user(),
    )


@pytest.mark.asyncio
async def test_json_body_with_json_content_type_is_parsed(provider):
    """The unchanged path: a well-behaved provider is decoded as JSON."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 200
    provider.content_type = 'application/json'
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            decoded = await _decode_provider_body(response)

    assert decoded == JSON_ERROR


@pytest.mark.asyncio
async def test_json_body_with_blank_content_type_is_parsed(provider):
    """The z.ai bug: HTTP 500, JSON body, blank Content-Type — still parsed."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 500
    provider.content_type = ''
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            # aiohttp itself refuses to decode this ...
            with pytest.raises(aiohttp.ContentTypeError):
                await response.json()
            # ... but the body is JSON, so the body wins over the header.
            decoded = await _decode_provider_body(response)

    assert decoded == JSON_ERROR


@pytest.mark.asyncio
async def test_json_body_without_content_type_is_parsed(provider):
    """A completely absent Content-Type is handled the same way."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 500
    provider.content_type = None
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            decoded = await _decode_provider_body(response)

    assert decoded == JSON_ERROR


@pytest.mark.asyncio
async def test_json_body_with_text_plain_content_type_is_parsed(provider):
    """A wrong-but-present Content-Type is handled the same way."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 500
    provider.content_type = 'text/plain'
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            decoded = await _decode_provider_body(response)

    assert decoded == JSON_ERROR


@pytest.mark.asyncio
async def test_non_json_body_with_blank_content_type_stays_text(provider):
    """An HTML error page is not JSON — keep it as text instead of raising."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 502
    provider.content_type = ''
    provider.body = HTML_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            decoded = await _decode_provider_body(response)

    assert decoded == HTML_ERROR_BODY
    assert isinstance(decoded, str)


@pytest.mark.asyncio
async def test_empty_body_decodes_to_none(provider):
    """An empty body behaves like ``resp.json()`` on an empty body."""
    from open_webui.routers.openai import _decode_provider_body

    provider.status = 500
    provider.content_type = 'application/json'
    provider.body = b''

    async with aiohttp.ClientSession() as session:
        response = await provider.post(session)
        async with response:
            decoded = await _decode_provider_body(response)

    assert decoded is None


@pytest.mark.asyncio
async def test_provider_error_reaches_retry_machinery_parsed(provider, monkeypatch):
    """The provider's structured error must arrive at the retry machinery parsed."""
    provider.status = 500
    provider.content_type = ''
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableProviderError) as excinfo:
            await _run_candidate(provider, monkeypatch, session)

    detail = excinfo.value.detail
    assert isinstance(detail, dict), f'expected a parsed provider error, got {type(detail).__name__}: {detail!r}'
    assert detail == JSON_ERROR
    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_provider_error_with_text_plain_reaches_retry_machinery_parsed(provider, monkeypatch):
    """Same for a wrong-but-present Content-Type."""
    provider.status = 500
    provider.content_type = 'text/plain'
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableProviderError) as excinfo:
            await _run_candidate(provider, monkeypatch, session)

    assert excinfo.value.detail == JSON_ERROR


@pytest.mark.asyncio
async def test_provider_error_with_sse_content_type_reaches_retry_machinery_parsed(provider, monkeypatch):
    """The SSE error branch decodes the body the same way."""
    provider.status = 500
    provider.content_type = 'text/event-stream'
    provider.body = JSON_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableProviderError) as excinfo:
            await _run_candidate(provider, monkeypatch, session)

    assert excinfo.value.detail == JSON_ERROR


@pytest.mark.asyncio
async def test_non_json_provider_error_stays_text(provider, monkeypatch):
    """An HTML error page stays text in the failure detail."""
    provider.status = 500
    provider.content_type = ''
    provider.body = HTML_ERROR_BODY.encode()

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableProviderError) as excinfo:
            await _run_candidate(provider, monkeypatch, session)

    assert isinstance(excinfo.value.detail, str)
    assert excinfo.value.detail == HTML_ERROR_BODY


@pytest.mark.asyncio
async def test_non_retryable_provider_error_is_published_parsed(provider, monkeypatch):
    """The provider-failure event stream gets the parsed body, not a string."""
    provider.status = 422
    provider.content_type = ''
    provider.body = JSON_ERROR_BODY.encode()

    published = []

    async def _record(request, **kwargs):
        published.append(kwargs)

    monkeypatch.setattr(openai_router, 'publish_model_provider_request_failed', _record)

    async with aiohttp.ClientSession() as session:
        response = await _run_candidate(provider, monkeypatch, session)

    assert len(published) == 1
    assert published[0]['upstream_error'] == JSON_ERROR
    assert published[0]['status'] == 422
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mis_typed_json_body_is_not_logged_as_an_error(provider, monkeypatch, caplog):
    """A handled provider quirk must not be logged at ERROR level."""
    provider.status = 500
    provider.content_type = ''
    provider.body = JSON_ERROR_BODY.encode()

    with caplog.at_level(logging.DEBUG):
        async with aiohttp.ClientSession() as session:
            with pytest.raises(RetryableProviderError):
                await _run_candidate(provider, monkeypatch, session)

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == [], f'handled provider quirk logged at ERROR: {[r.getMessage() for r in errors]}'
    assert any('unexpected Content-Type' in record.getMessage() for record in caplog.records)


def test_provider_error_detail_keeps_small_json_bodies_structured():
    from open_webui.routers.openai import _provider_error_detail

    assert _provider_error_detail(JSON_ERROR) == JSON_ERROR


def test_provider_error_detail_truncates_large_bodies():
    from open_webui.routers.openai import _PROVIDER_DETAIL_MAX_CHARS, _provider_error_detail

    detail = _provider_error_detail({'error': {'message': 'x' * 5000}})
    assert isinstance(detail, str)
    assert len(detail) == _PROVIDER_DETAIL_MAX_CHARS


def test_provider_error_detail_truncates_text():
    from open_webui.routers.openai import _PROVIDER_DETAIL_MAX_CHARS, _provider_error_detail

    assert _provider_error_detail('y' * 1000) == 'y' * _PROVIDER_DETAIL_MAX_CHARS
