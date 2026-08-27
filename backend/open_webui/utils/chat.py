import asyncio
import logging
import random
import sys
import time
import uuid
from typing import Any, Optional

from aiocache import cached
from fastapi import HTTPException, Request, status
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, GLOBAL_LOG_LEVEL
from open_webui.functions import generate_function_chat_completion
from open_webui.models.models import Models
from open_webui.models.users import UserModel
from open_webui.routers.ollama import (
    generate_chat_completion as generate_ollama_chat_completion,
)
from open_webui.routers.openai import (
    generate_chat_completion as generate_openai_chat_completion,
)
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
    sio,
)
from open_webui.utils.filter import (
    get_filter_functions,
    process_filter_functions,
)
from open_webui.utils.failover import resolve_failover_candidates
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.utils.payload import convert_payload_openai_to_ollama
from open_webui.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)
from starlette.responses import JSONResponse, Response, StreamingResponse

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


async def _resolve_via_failover_chain(
    request: Request,
    model_id: str,
    form_data: dict,
    user,
) -> Optional[dict]:
    """Find the wrapper whose ``base_model_id`` matches ``model_id`` and
    resolve a failover chain for it. Returns a minimal model dict built
    from the first viable candidate so the rest of the flow can proceed;
    the actual HTTP failover happens in ``generate_openai_chat_completion``.

    Used as a fallback when the static ``app.state.MODELS`` lookup misses
    (typically because the wrapper's primary provider was disabled and
    the underlying ``base_model_id`` is no longer in the cache). Without
    this, the chat UI path raises 'Model not found' *before* the failover
    resolver gets a chance to try alternative providers.

    The wrapper's own access check is applied here — if the caller can't
    use the wrapper, we return ``None`` so the upstream 'Model not found'
    is preserved (it's the same security posture as if the wrapper's
    base_model_id were still cached).
    """
    health_cache = getattr(request.app.state, 'PROVIDER_HEALTH', None)

    for wrapper_dict in (request.app.state.MODELS or {}).values():
        info = wrapper_dict.get('info') if isinstance(wrapper_dict, dict) else {}
        if not isinstance(info, dict):
            continue
        if info.get('base_model_id') != model_id:
            continue
        wrapper_id = wrapper_dict.get('id')
        if not wrapper_id:
            continue

        # Caller must already have access to the wrapper itself, otherwise
        # we'd be routing them to a model they can't see.
        try:
            await check_model_access(user, wrapper_dict)
        except Exception:
            continue

        wrapper_model_info = await Models.get_model_by_id(wrapper_id)
        if wrapper_model_info is None:
            continue

        # Try to resolve a candidate chain for this wrapper. Any failure
        # here (e.g. a malformed entry) just means we move on to the next
        # candidate wrapper; only a successfully-built candidate chain
        # returns a synthetic model.
        try:
            candidates = await resolve_failover_candidates(
                request=request,
                model_info=wrapper_model_info,
                payload=form_data,
                health_cache=health_cache,
            )
        except Exception:
            continue
        if candidates:
            first = candidates[0]
            # Minimal model dict — the actual chat completion reads
            # ``form_data['model']`` (the rewritten base_model_id), not
            # ``model``. This dict just lets the downstream checks
            # (``check_model_access``, arena/pipeline gating) pass.
            return {
                'id': first.model_name,
                'name': first.model_name,
                'info': {'meta': {}},
            }
    return None


# When the question has been asked, let silence not be the
# answer. But if the answer must wait, let it come honest.
async def generate_direct_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    models: dict,
):
    log.info('generate_direct_chat_completion')

    metadata = form_data.pop('metadata', {})

    user_id = metadata.get('user_id')
    session_id = metadata.get('session_id')
    request_id = str(uuid.uuid4())  # Generate a unique request ID

    event_caller = await get_event_call(metadata)
    if event_caller is None:
        raise Exception(
            'Direct connection requires an active WebSocket session; '
            'cannot generate completion in this context (e.g. background task).'
        )

    channel = f'{user_id}:{session_id}:{request_id}'
    logging.info('WebSocket channel: %s', channel)

    if form_data.get('stream'):
        q = asyncio.Queue()

        async def message_listener(sid, data):
            """
            Handle received socket messages and push them into the queue.
            """
            await q.put(data)

        # Register the listener
        sio.on(channel, message_listener)

        # Start processing chat completion in background
        res = await event_caller(
            {
                'type': 'request:chat:completion',
                'data': {
                    'form_data': form_data,
                    'model': models[form_data['model']],
                    'channel': channel,
                    'session_id': session_id,
                },
            }
        )

        log.info('res: %s', res)

        if res.get('status', False):
            # Define a generator to stream responses
            async def event_generator():
                nonlocal q
                try:
                    while True:
                        data = await q.get()  # Wait for new messages
                        if isinstance(data, dict):
                            if 'done' in data and data['done']:
                                break  # Stop streaming when 'done' is received

                            yield f'data: {JSONCodec.dumps(data)}\n\n'
                        elif isinstance(data, str):
                            if 'data:' in data:
                                yield f'{data}\n\n'
                            else:
                                yield f'data: {data}\n\n'
                except Exception as e:
                    log.debug('Error in event generator: %s', e)
                    pass

            # Define a background task to run the event generator
            async def background():
                try:
                    del sio.handlers['/'][channel]
                except Exception as e:
                    pass

            # Return the streaming response
            return StreamingResponse(event_generator(), media_type='text/event-stream', background=background)
        else:
            raise Exception(str(res))
    else:
        res = await event_caller(
            {
                'type': 'request:chat:completion',
                'data': {
                    'form_data': form_data,
                    'model': models[form_data['model']],
                    'channel': channel,
                    'session_id': session_id,
                },
            }
        )

        if 'error' in res and res['error']:
            raise Exception(res['error'])

        return res


async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    bypass_filter: bool = False,
    bypass_system_prompt: bool = False,
):
    log.debug('generate_chat_completion: %s', form_data)
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    # Propagate bypass_filter and bypass_system_prompt via request.state so that
    # downstream route handlers (openai/ollama) can read them without exposing
    # them as query parameters.
    request.state.bypass_filter = bypass_filter
    request.state.bypass_system_prompt = bypass_system_prompt

    if hasattr(request.state, 'metadata'):
        if 'metadata' not in form_data:
            form_data['metadata'] = request.state.metadata
        else:
            form_data['metadata'] = {
                **form_data['metadata'],
                **request.state.metadata,
            }

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        # Merge the direct connection model into server models so that
        # task functions (title, tags, etc.) can resolve a server-side
        # task model while still having the direct model available.
        # dict(...items()) is one HGETALL on a Redis-backed pool; ``{**pool}``
        # would issue HKEYS plus one HGET per model.
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
        log.debug('direct connection to model: %s', request.state.model['id'])
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    # Single lookup — membership check plus getitem would be two Redis
    # round trips on a Redis-backed model pool.
    model = models.get(model_id)
    if model is None:
        # Static cache miss. The model_id here is typically the wrapper's
        # base_model_id (rewritten earlier in the pipeline). When the
        # wrapper's primary provider is disabled/removed, that base_model_id
        # isn't in app.state.MODELS — but the wrapper itself still has a
        # failover chain that can serve it. Look up the wrapper that uses
        # this base_model_id and resolve via its chain; the actual HTTP
        # failover happens downstream in generate_openai_chat_completion.
        model = await _resolve_via_failover_chain(request, model_id, form_data, user)
        if model is None:
            raise Exception('Model not found')

    if getattr(request.state, 'direct', False) and model_id == getattr(request.state, 'model', {}).get('id'):
        return await generate_direct_chat_completion(request, form_data, user=user, models=models)
    else:
        # Check if user has access to the model
        if not bypass_filter and user.role == 'user':
            try:
                await check_model_access(user, model)
            except Exception as e:
                raise e

        # Arena model — sub-model was already resolved by process_chat_payload.
        # Inject selected_model_id into the response for the frontend.
        metadata = form_data.get('metadata', {})
        selected_model_id = metadata.pop('selected_model_id', None)
        # Also clear from request.state.metadata to prevent the merge at
        # lines 177-179 from re-adding it on the recursive call.
        if hasattr(request.state, 'metadata'):
            request.state.metadata.pop('selected_model_id', None)

        # Fallback: if generate_chat_completion is called with an arena model
        # from a path that did NOT go through process_chat_payload (e.g.,
        # background tasks for title/follow-up/tags generation), resolve now.
        if not selected_model_id and model.get('owned_by') == 'arena':
            model_ids = model.get('info', {}).get('meta', {}).get('model_ids')
            filter_mode = model.get('info', {}).get('meta', {}).get('filter_mode')
            if model_ids and filter_mode == 'exclude':
                model_ids = [
                    available_model['id']
                    for available_model in list(request.app.state.MODELS.values())
                    if available_model.get('owned_by') != 'arena' and available_model['id'] not in model_ids
                ]

            if isinstance(model_ids, list) and model_ids:
                selected_model_id = random.choice(model_ids)
            else:
                model_ids = [
                    available_model['id']
                    for available_model in list(request.app.state.MODELS.values())
                    if available_model.get('owned_by') != 'arena'
                ]
                selected_model_id = random.choice(model_ids)

            form_data['model'] = selected_model_id

            # bypass_filter recursion below skips the line-200 check; gate the resolved model here.
            if not bypass_filter and user.role == 'user':
                selected_model = request.app.state.MODELS.get(selected_model_id)
                if selected_model:
                    await check_model_access(user, selected_model)

        if selected_model_id:
            if form_data.get('stream') == True:

                async def stream_wrapper(stream):
                    yield f'data: {JSONCodec.dumps({"selected_model_id": selected_model_id})}\n\n'
                    async for chunk in stream:
                        yield chunk

                response = await generate_chat_completion(
                    request,
                    form_data,
                    user,
                    bypass_filter=True,
                    bypass_system_prompt=bypass_system_prompt,
                )
                return StreamingResponse(
                    stream_wrapper(response.body_iterator),
                    media_type='text/event-stream',
                    background=response.background,
                )
            else:
                return {
                    **(
                        await generate_chat_completion(
                            request,
                            form_data,
                            user,
                            bypass_filter=True,
                            bypass_system_prompt=bypass_system_prompt,
                        )
                    ),
                    'selected_model_id': selected_model_id,
                }

        if model.get('pipe'):
            # Below does not require bypass_filter because this is the only route the uses this function and it is already bypassing the filter
            return await generate_function_chat_completion(request, form_data, user=user, models=models)
        if model.get('owned_by') == 'ollama':
            # Using /ollama/api/chat endpoint
            form_data = convert_payload_openai_to_ollama(form_data)
            response = await generate_ollama_chat_completion(
                request=request,
                form_data=form_data,
                user=user,
            )
            if form_data.get('stream'):
                response.headers['content-type'] = 'text/event-stream'
                return StreamingResponse(
                    convert_streaming_response_ollama_to_openai(response),
                    headers=dict(response.headers),
                    background=response.background,
                )
            else:
                return convert_response_ollama_to_openai(response)
        else:
            return await generate_openai_chat_completion(
                request=request,
                form_data=form_data,
                user=user,
            )


chat_completion = generate_chat_completion


async def chat_completed(request: Request, form_data: dict, user: Any):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **dict(request.app.state.MODELS.items()),
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    data = form_data

    if not data.get('id'):
        raise Exception('Missing message id')

    model_id = data['model']
    if model_id not in models:
        raise Exception('Model not found')

    model = models[model_id]

    try:
        data = await process_pipeline_outlet_filter(request, data, user, models)
    except HTTPException:
        raise
    except Exception as e:
        raise Exception(f'Error: {e}')

    if not data.get('id'):
        raise Exception('Missing message id')

    metadata = {
        'chat_id': data['chat_id'],
        'message_id': data['id'],
        'filter_ids': data.get('filter_ids', []),
        'session_id': data['session_id'],
        'user_id': user.id,
    }

    extra_params = {
        '__event_emitter__': await get_event_emitter(metadata),
        '__event_call__': await get_event_call(metadata),
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__request__': request,
        '__model__': model,
    }

    try:
        filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))

        result, _ = await process_filter_functions(
            request=request,
            filter_context=None,
            filter_functions=filter_functions,
            filter_type='outlet',
            form_data=data,
            extra_params=extra_params,
        )
        return result
    except Exception as e:
        raise Exception(f'Error: {e}')
