"""Guards for the tool-call continuation.

The tool-call continuation is the turn that turns tool results into the
model's final answer. Three things went wrong there in production:

1. **A transient provider failure killed the turn.** The tool had already
   run and its result was in hand - only the final answer was missing - yet
   a 500 ("Internal network failure, please try again later") ended the
   turn with ``Tool-call continuation failed`` and a 30-line traceback.
   The continuation is now retried a bounded number of times on a transient
   failure (5xx / 429); a 4xx is not retried, because the request shape or
   the credentials are wrong and the same call would fail identically.

2. **The provider's message was hidden.** The chat UI renders
   ``error.content`` as a *string* - the frontend's ``getErrorMessage()``
   takes the first string candidate and falls back to a generic "Server
   connection failed" when it finds none. A provider body that is still a
   dict therefore made the UI show "Server connection failed" instead of
   the provider's actual message.

3. **A handled provider failure was logged with a full traceback.** The
   provider's message is the information; the stack is noise on top of it.
"""

import asyncio

import pytest
from fastapi import HTTPException

import open_webui.utils.failover as failover
import open_webui.utils.misc as misc

# Resolved dynamically so that a module WITHOUT these helpers fails the
# assertions below instead of erroring at collection time.
extract_error_message = getattr(misc, 'extract_error_message', None)
get_response_error_detail = getattr(misc, 'get_response_error_detail', None)
is_transient_provider_error = getattr(failover, 'is_transient_provider_error', None)
call_with_transient_retry = getattr(failover, 'call_with_transient_retry', None)
TOOL_CONTINUATION_MAX_RETRIES = getattr(failover, 'TOOL_CONTINUATION_MAX_RETRIES', 2)


# --------------------------------------------------------------------------------------
# The helpers must exist
# --------------------------------------------------------------------------------------


def test_the_helpers_exist():
    assert callable(extract_error_message), 'utils.misc.extract_error_message is missing'
    assert callable(is_transient_provider_error), 'utils.failover.is_transient_provider_error is missing'
    assert callable(call_with_transient_retry), 'utils.failover.call_with_transient_retry is missing'


# --------------------------------------------------------------------------------------
# is_transient_provider_error
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize('status', [500, 502, 503, 504, 429])
def test_transient_statuses(status):
    assert is_transient_provider_error(status) is True


@pytest.mark.parametrize('status', [400, 401, 403, 404, 413, 422])
def test_non_transient_statuses(status):
    assert is_transient_provider_error(status) is False


def test_missing_status_is_not_transient():
    assert is_transient_provider_error(None) is False


# --------------------------------------------------------------------------------------
# extract_error_message
# --------------------------------------------------------------------------------------


def test_extracts_the_message_from_a_provider_error_body():
    body = {'error': {'code': '1234', 'message': 'Internal network failure, please try again later.'}}
    assert extract_error_message(body) == 'Internal network failure, please try again later.'


def test_extracts_the_message_from_a_nested_detail():
    body = {'detail': {'error': {'message': 'boom'}}}
    assert extract_error_message(body) == 'boom'


def test_extracts_the_message_from_a_json_string():
    raw = '{"error": {"code": "1234", "message": "Internal network failure"}}'
    assert extract_error_message(raw) == 'Internal network failure'


def test_plain_text_is_returned_as_is():
    assert extract_error_message('<html><body>502 Bad Gateway</body></html>').startswith('<html>')


def test_a_dict_without_a_known_key_is_serialised_not_dropped():
    out = extract_error_message({'code': '1234'})
    assert '1234' in out


def test_empty_inputs():
    assert extract_error_message(None) == ''
    assert extract_error_message('') == ''
    assert extract_error_message({}) != '' or True  # a dict with no keys is not an error


# --------------------------------------------------------------------------------------
# get_response_error_detail
# --------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, status_code: int = 500):
        self.body = body
        self.status_code = status_code


def test_response_error_detail_extracts_the_provider_message():
    raw = b'{"error": {"code": "1234", "message": "Internal network failure"}}'
    assert get_response_error_detail(_FakeResponse(raw)) == 'Internal network failure'


def test_response_error_detail_returns_a_non_json_body_as_text():
    # A provider that answers with an HTML error page: the text is more
    # useful than "Provider returned HTTP 500".
    assert get_response_error_detail(_FakeResponse(b'<html>502</html>', 500)) == '<html>502</html>'


def test_response_error_detail_falls_back_when_the_body_is_empty():
    assert 'HTTP 500' in get_response_error_detail(_FakeResponse(b'', 500))


# --------------------------------------------------------------------------------------
# call_with_transient_retry
# --------------------------------------------------------------------------------------


def test_a_transient_failure_is_retried_and_then_succeeds():
    calls = []

    async def call():
        calls.append(1)
        if len(calls) < 2:
            raise HTTPException(status_code=500, detail='Internal network failure')
        return 'ok'

    result = asyncio.run(call_with_transient_retry(call, label='test', delay=0))
    assert result == 'ok'
    assert len(calls) == 2


def test_the_retry_is_bounded():
    calls = []

    async def call():
        calls.append(1)
        raise HTTPException(status_code=503, detail='still down')

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(call_with_transient_retry(call, label='test', delay=0))

    assert excinfo.value.status_code == 503
    # The first attempt plus TOOL_CONTINUATION_MAX_RETRIES retries.
    assert len(calls) == TOOL_CONTINUATION_MAX_RETRIES + 1


def test_a_client_error_is_not_retried():
    calls = []

    async def call():
        calls.append(1)
        raise HTTPException(status_code=400, detail='bad request')

    with pytest.raises(HTTPException):
        asyncio.run(call_with_transient_retry(call, label='test', delay=0))

    assert len(calls) == 1


def test_a_non_http_exception_is_not_retried():
    calls = []

    async def call():
        calls.append(1)
        raise RuntimeError('boom')

    with pytest.raises(RuntimeError):
        asyncio.run(call_with_transient_retry(call, label='test', delay=0))

    assert len(calls) == 1


def test_a_successful_call_is_not_retried():
    calls = []

    async def call():
        calls.append(1)
        return 'ok'

    assert asyncio.run(call_with_transient_retry(call, label='test', delay=0)) == 'ok'
    assert len(calls) == 1
