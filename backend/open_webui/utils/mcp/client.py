import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Optional

log = logging.getLogger(__name__)

import anyio
import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER,
    MCP_INITIALIZE_TIMEOUT,
)


def _install_mcp_cleanup_filter() -> None:
    """Suppress the two known-benign MCP SDK cleanup races from the
    asyncio loop's 'Task exception was never retrieved' warning. The SDK
    spawns internal anyio task groups that race on cancellation during
    aclose(); the resulting RuntimeError fires in fire-and-forget tasks
    that nobody awaits, so the per-call except handler in
    MCPClient.disconnect() / .connect() cannot catch them. Filter them at
    the loop level so the user never sees the noise.

    Both patterns are upstream MCP SDK bugs (anyio task-group cleanup
    from a sibling task, generator-still-iterating on aclose). They are
    non-fatal — the transport is torn down anyway — so suppression is
    safe.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No running loop (called at import time before app start).
        return

    # Marker stored in a side-dict on the loop instance to avoid the
    # type-checker complaint about setting arbitrary attributes on the
    # abstract event loop class.
    installed = getattr(loop, '__dict__', {}).get('_open_webui_mcp_cleanup_filter_installed', False)
    if installed:
        return

    original = loop.get_exception_handler()

    def _filter(loop, context):
        exc = context.get('exception')
        msg = str(exc) if exc else ''
        if 'aclose(): asynchronous generator is already running' in msg or 'Attempted to exit cancel scope' in msg:
            log.debug('Suppressed MCP SDK cleanup race: %s', msg)
            return
        return original(loop, context)

    loop.set_exception_handler(_filter)
    # Mark the loop (bypasses the abstract-class attribute type checker).
    try:
        loop.__dict__['_open_webui_mcp_cleanup_filter_installed'] = True
    except Exception:
        # Some loops disallow __dict__ mutation — give up silently.
        pass


# Install at import time. Safe to call multiple times — idempotent.
_install_mcp_cleanup_filter()


def _build_httpx_client(headers=None, timeout=None, auth=None, verify=True):
    """Create an httpx AsyncClient for MCP transport.

    Falls back to AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER when the caller
    (i.e. the MCP SDK) does not supply an explicit timeout.

    Note: verify must be passed at construction time because httpx
    configures the SSL context during __init__. Setting client.verify = False
    after construction does not affect the underlying transport's SSL context.
    """
    kwargs = {
        'follow_redirects': True,
        'verify': verify,
    }
    if timeout is not None:
        kwargs['timeout'] = timeout
    elif AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER is not None:
        kwargs['timeout'] = float(AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER)
    if headers is not None:
        kwargs['headers'] = headers
    if auth is not None:
        kwargs['auth'] = auth
    return httpx.AsyncClient(**kwargs)


def create_httpx_client(headers=None, timeout=None, auth=None):
    # AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL may be True, False, or an
    # ssl.SSLContext (when a custom CA bundle path is configured).
    # httpx's verify= accepts bool | str | ssl.SSLContext, so all three work.
    ssl_setting = AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL
    verify = ssl_setting if ssl_setting is not True else True
    return _build_httpx_client(headers=headers, timeout=timeout, auth=auth, verify=verify)


def create_insecure_httpx_client(headers=None, timeout=None, auth=None):
    return _build_httpx_client(headers=headers, timeout=timeout, auth=auth, verify=False)


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = None

    async def connect(self, url: str, headers: Optional[dict] = None):
        # The MCP SDK's `async with streamablehttp_client(...)` and
        # `async with anyio.create_task_group()` contexts can fire benign
        # cleanup race errors during __aexit__: 'aclose(): asynchronous
        # generator is already running' and 'Attempted to exit cancel
        # scope in a different task than it was entered in'. These surface
        # when a sibling task cancels the transport while we're in the
        # middle of an aclose. They're non-fatal (the underlying sockets
        # are torn down anyway) but they propagate up to the asyncio
        # loop as 'Task exception was never retrieved' warnings. Catch them
        # both here AND in disconnect() so the user never sees them.
        exit_stack = AsyncExitStack()
        try:
            try:
                self._streams_context = streamablehttp_client(
                    url,
                    headers=headers,
                    httpx_client_factory=create_httpx_client
                    if AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL
                    else create_insecure_httpx_client,
                )

                transport = await exit_stack.enter_async_context(self._streams_context)
                read_stream, write_stream, _ = transport

                self._session_context = ClientSession(read_stream, write_stream)  # pylint: disable=W0201

                self.session = await exit_stack.enter_async_context(self._session_context)
                with anyio.fail_after(MCP_INITIALIZE_TIMEOUT):
                    await self.session.initialize()
                self.exit_stack = exit_stack.pop_all()
            except BaseException:
                # Connection failure path — close the exit_stack before
                # the outer `async with` re-raises, and suppress the SDK's
                # cleanup race conditions here too (they're independent of
                # the body exception).
                try:
                    await exit_stack.aclose()
                except (RuntimeError, asyncio.CancelledError, Exception) as exc:
                    log.debug(
                        'MCPClient.connect() suppressed aclose() error on failure path: %s: %s',
                        type(exc).__name__,
                        exc,
                    )
                # Re-raise the original body exception so callers see the
                # real failure cause.
                raise
        except RuntimeError as exc:
            msg = str(exc)
            # Two known benign SDK cleanup races that surface here:
            #   - 'aclose(): asynchronous generator is already running'
            #   - 'Attempted to exit cancel scope in a different task
            #      than it was entered in'
            if 'aclose()' in msg or 'cancel scope' in msg:
                log.debug(
                    'MCPClient.connect() suppressed known SDK cleanup race: %s',
                    exc,
                )
            else:
                raise

    async def list_tool_specs(self) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.list_tools()
        tools = result.tools

        tool_specs = []
        for tool in tools:
            name = tool.name
            description = tool.description

            inputSchema = tool.inputSchema

            # TODO: handle outputSchema if needed
            outputSchema = getattr(tool, 'outputSchema', None)

            tool_specs.append({'name': name, 'description': description, 'parameters': inputSchema})

        return tool_specs

    async def call_tool(self, function_name: str, function_args: dict) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.call_tool(function_name, function_args)
        if not result:
            raise Exception('No result returned from MCP tool call.')

        result_dict = result.model_dump(mode='json')
        result_content = result_dict.get('content', {})

        if result.isError:
            raise Exception(result_content)
        else:
            return result_content

    async def list_resources(self, cursor: Optional[str] = None) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.list_resources(cursor=cursor)
        if not result:
            raise Exception('No result returned from MCP list_resources call.')

        result_dict = result.model_dump()
        resources = result_dict.get('resources', [])

        return resources

    async def read_resource(self, uri: str) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.read_resource(uri)
        if not result:
            raise Exception('No result returned from MCP read_resource call.')
        result_dict = result.model_dump()

        return result_dict

    async def disconnect(self):
        """Clean up and close the session.

        This method is idempotent — calling it multiple times or on a
        client that was never connected is safe.
        """
        exit_stack = self.exit_stack
        if exit_stack is None:
            return

        # Prevent double-close from concurrent callers
        self.exit_stack = None
        self.session = None

        try:
            # IMPORTANT: Do NOT use asyncio.shield() or asyncio.wait_for()
            # because they create a new asyncio task, which violates the MCP SDK's
            # requirement that its TaskGroup be exited in the exact same task.
            # ALSO do NOT use anyio.CancelScope(shield=True) or anyio.fail_after(),
            # because they push a new cancel scope onto the task, violating LIFO
            # order when aclose() attempts to exit the inner TaskGroup.
            # We simply call aclose() directly. If the task is cancelled, the
            # sockets will eventually be cleaned up by garbage collection.
            await exit_stack.aclose()
        except asyncio.CancelledError as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            log.debug('MCPClient.disconnect() suppressed internal cancellation: %s', exc)
        except RuntimeError as exc:
            # Both known MCP SDK cleanup race conditions surface here:
            #   - "aclose(): asynchronous generator is already running"
            #     (streamablehttp_client's inner task group is still mid-iteration)
            #   - "Attempted to exit cancel scope in a different task than it
            #      was entered in" (anyio task group cancelled from a sibling task)
            # Both are benign at this layer — the connection is already being torn
            # down; the sockets will eventually be reaped by the MCP SDK's GC.
            log.debug('MCPClient.disconnect() suppressed RuntimeError during aclose(): %s', exc)
        except Exception as exc:
            log.debug('MCPClient.disconnect() error: %s', exc)

    async def __aenter__(self):
        await self.exit_stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.exit_stack.__aexit__(exc_type, exc_value, traceback)
        await self.disconnect()
