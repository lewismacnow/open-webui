"""Vision-based Image RAG.

When a user sends an image to a model that has knowledge to retrieve against,
describe the image with a vision-capable model so the standard RAG pipeline can
build retrieval queries from the description. The chatting model's resolved
system prompt frames the description, and the description replaces the raw
image in the final user message — so query generation, vector search, and the
final completion all treat it as text. This also lets non-vision models use
images via an admin-configured global "vision support model".

Activation rules (see ``process_image_rag``):
  - last user message contains at least one ``image_url`` content part, AND
  - there is something to retrieve (model/folder/user knowledge or files), OR
    the chatting model is non-vision but a global vision model is configured
    (so images can still be described for the response), AND
  - the chatting model is vision-capable OR ``rag.vision.support_model`` is set.

Vision-capable chatting models with NO retrieval target are left untouched
(the model sees the image directly) — describing it would be pure overhead.

The describe call is best-effort: on any failure it logs and returns False so
the normal chat flow continues unaffected.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict

from open_webui.models.config import Config
from open_webui.models.models import Models
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.payload import resolve_system_prompt

log = logging.getLogger(__name__)


_REDIS_CLIENT = None
_REDIS_CHECKED = False


async def _get_redis():
    """Lazily create and cache a Redis connection. Returns None if Redis unavailable."""
    global _REDIS_CLIENT, _REDIS_CHECKED
    if _REDIS_CHECKED:
        return _REDIS_CLIENT
    _REDIS_CHECKED = True
    redis_url = os.getenv('REDIS_URL', '')
    if not redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        _REDIS_CLIENT = aioredis.Redis.from_url(redis_url, decode_responses=True)
        await _REDIS_CLIENT.ping()
    except Exception as e:
        log.debug(f'Vision RAG: Redis unavailable, using in-memory cache only: {e}')
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


DEFAULT_DESCRIBE_PROMPT = (
    'You are a vision support model describing an image for a text-only chat model. '
    'Produce a structured response with these exact sections:\n'
    '\n'
    '## Visual Description\n'
    'A concise but accurate description of the image. Cover the most important elements: '
    'objects, people, scenes, actions, colors, layout, and notable details. Be factual; '
    'avoid speculation. 2-4 sentences.\n'
    '\n'
    '## Text Content (OCR)\n'
    'Transcribe ALL visible text verbatim. Include: titles, headings, body text, labels, '
    'captions, UI elements, error messages, code snippets, and watermarks. Preserve line breaks '
    'and code formatting. If text is partial or unclear, transcribe what you can and note '
    'uncertainty in square brackets. If no text is visible, write "None."\n'
    '\n'
    '## Context\n'
    'One or two sentences noting what kind of image this is (photo, screenshot, diagram, '
    'document, chart, etc.) and any obvious purpose.\n'
    '\n'
    'Do not add information that is not visible. The output will be embedded in a text-only '
    'conversation to help the chat model respond to the user.'
)


# Simple in-memory LRU cache for image descriptions. Avoids re-describing the
# same image on follow-up messages.  Non-persistent, short TTL, tiny footprint.
_VISION_DESC_CACHE: OrderedDict = OrderedDict()
_VISION_DESC_CACHE_MAX = 50  # max 50 cached descriptions
_VISION_DESC_CACHE_TTL = 300  # 5 minutes


def _image_cache_key(image_parts: list[dict], context_hash: str = '') -> str:
    """Build a cache key from image content + optional context (user text)."""
    h = hashlib.sha256()
    for part in image_parts:
        url = (part.get('image_url') or {}).get('url', '') if isinstance(part, dict) else ''
        if url:
            # For data URIs, hash the full content; for URLs, hash the URL itself
            h.update(url.encode('utf-8')[:10000])  # cap to avoid hashing huge base64
    if context_hash:
        h.update(context_hash.encode('utf-8'))
    return h.hexdigest()


async def _get_cached_description(cache_key: str) -> str | None:
    """Check in-memory cache, then Redis. Returns description or None."""
    # Layer 1: in-memory LRU
    if cache_key in _VISION_DESC_CACHE:
        desc, ts = _VISION_DESC_CACHE[cache_key]
        if time.time() - ts < _VISION_DESC_CACHE_TTL:
            _VISION_DESC_CACHE.move_to_end(cache_key)
            return desc
        else:
            del _VISION_DESC_CACHE[cache_key]

    # Layer 2: Redis (shared across workers, 24h TTL)
    redis = await _get_redis()
    if redis:
        try:
            cached = await redis.get(f'vision_desc:{cache_key}')
            if cached:
                # Populate in-memory for faster subsequent access
                _VISION_DESC_CACHE[cache_key] = (cached, time.time())
                while len(_VISION_DESC_CACHE) > _VISION_DESC_CACHE_MAX:
                    _VISION_DESC_CACHE.popitem(last=False)
                return cached
        except Exception as e:
            log.debug(f'Vision RAG: Redis cache read failed: {e}')

    return None


async def _set_cached_description(cache_key: str, description: str) -> None:
    """Store description in in-memory cache AND Redis."""
    # Layer 1: in-memory LRU
    _VISION_DESC_CACHE[cache_key] = (description, time.time())
    while len(_VISION_DESC_CACHE) > _VISION_DESC_CACHE_MAX:
        _VISION_DESC_CACHE.popitem(last=False)

    # Layer 2: Redis (24h TTL)
    redis = await _get_redis()
    if redis:
        try:
            await redis.setex(f'vision_desc:{cache_key}', 86400, description)
        except Exception as e:
            log.debug(f'Vision RAG: Redis cache write failed: {e}')


def _image_parts(message: dict) -> list[dict]:
    """Return the ``image_url`` content parts of a message (OpenAI multimodal)."""
    content = message.get('content')
    if not isinstance(content, list):
        return []
    return [p for p in content if isinstance(p, dict) and p.get('type') == 'image_url']


def _message_text(message: dict) -> str:
    """Concatenate ALL text parts of a message (handles multi-part content)."""
    content = message.get('content')
    if isinstance(content, list):
        parts = [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
        return '\n'.join(p for p in parts if p)
    return content or ''


def _resolve_effective_vision_capability(model: dict, request) -> bool:
    """
    Resolve whether the effective model (wrapper → base chain) supports vision.

    For wrapper models (info.base_model_id set), look up the BASE model and use
    ITS vision capability. This fixes the bug where wrappers defaulted to
    vision=True even when wrapping non-vision bases.

    For non-wrapper models (no base_model_id), check the model's own capability
    as before (preserving existing behavior).

    Falls back to True (vision-capable) when:
    - The wrapper has no base_model_id (treat as base model)
    - The base model is not found in request.app.state.MODELS
    - No explicit vision capability is set on the resolved base
    """
    # Walk the wrapper chain (handles wrapper-of-wrapper edge cases)
    current = model
    seen_ids = set()
    while current and current.get('id') not in seen_ids:
        seen_ids.add(current.get('id'))
        base_model_id = (current.get('info', {}) or {}).get('base_model_id')
        if not base_model_id:
            # This is the effective base model — check its vision capability
            capabilities = (current.get('info', {}) or {}).get('meta', {}).get('capabilities') or {}
            return bool(capabilities.get('vision', True))
        # Resolve base from app state
        models_dict = getattr(getattr(request, 'app', None), 'state', None)
        models_dict = getattr(models_dict, 'MODELS', None) if models_dict else None
        if not models_dict:
            return True  # can't resolve — best-effort default
        current = models_dict.get(base_model_id)
        if not current:
            return True  # base not found — best-effort default
    return True  # cycle/empty — best-effort default


async def process_image_rag(
    request,
    form_data: dict,
    metadata: dict,
    user,
    model: dict,
    event_emitter=None,
) -> bool:
    """Describe images in the last user message and inject the description.

    Mutates ``form_data['messages']`` in place: the image parts of the last
    user message are replaced by a text block holding the description (followed
    by the user's original text), so the downstream RAG query-generation and the
    final model call both see text only.

    Returns True if a description was injected, False otherwise (no image,
    nothing to retrieve, no vision capability available, or the describe call
    failed).
    """
    # Never run on sub-task calls (title/query generation, or this module's own
    # describe call) — they must not re-trigger image description.
    if (metadata or {}).get('task'):
        return False

    messages = form_data.get('messages', [])

    # Find ALL user messages that contain image parts (not just the last one).
    # On follow-up turns, earlier messages loaded from DB still have raw images
    # that must be described for non-vision models.
    messages_with_images = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get('role') != 'user':
            continue
        parts = _image_parts(msg)
        if parts:
            messages_with_images.append((msg, parts))

    if not messages_with_images:
        return False

    # Decide which model describes the image.
    chatting_supports_vision = _resolve_effective_vision_capability(model, request)
    # Batch both config reads to avoid two sequential DB round-trips.
    vision_support_task = Config.get('rag.vision.support_model', '')
    system_prompt_task = Config.get('rag.vision.system_prompt', '')
    vision_support_model_id_raw, admin_system_prompt_raw = await asyncio.gather(vision_support_task, system_prompt_task)
    vision_support_model_id = (vision_support_model_id_raw or '').strip()
    admin_system_prompt = (admin_system_prompt_raw or '').strip()

    # Is there anything for the downstream RAG step to retrieve? Model-attached
    # and folder-attached knowledge are already moved from form_data['files']
    # into metadata['files'] by this point (form_data['files'] is popped around
    # line 2512 and merged into metadata), so read from metadata.
    has_retrieval_target = bool((metadata or {}).get('files')) or bool((metadata or {}).get('folder_knowledge'))

    if chatting_supports_vision:
        # A vision-capable model that has nothing to retrieve should just see the
        # image directly — describing it only adds cost and loses pixel detail.
        if not has_retrieval_target:
            return False
        vision_model_id = model.get('id')
        bypass_filter = False
    elif vision_support_model_id:
        # Non-vision chatting model: describe via the global vision model so the
        # response can still use the image (and RAG, if there's a target).
        vision_model_id = vision_support_model_id
        # Admin-designated infrastructure model — usable by any chatting user so
        # image-RAG works uniformly (matches the intent of a global setting).
        bypass_filter = True
    else:
        # Non-vision chatting model and no global vision model configured.
        return False

    # Resolve the chatting model's system prompt ONCE (per-model, not per-message).
    if not admin_system_prompt:
        system_raw = None
        try:
            chatting_model_row = await Models.get_model_by_id(model.get('id'))
        except Exception as e:
            log.warning(f'Vision RAG: could not load chatting model for system prompt: {e}')
            chatting_model_row = None
        if chatting_model_row and chatting_model_row.params:
            params_obj = chatting_model_row.params
            params_dict = params_obj.model_dump() if hasattr(params_obj, 'model_dump') else dict(params_obj)
            system_raw = params_dict.get('system')

        system_prompt = await resolve_system_prompt(system_raw, metadata, user)

    # Process each user message with images.
    any_replaced = False
    for idx, (msg, image_parts) in enumerate(messages_with_images):
        is_last_message = idx == len(messages_with_images) - 1
        user_text = _message_text(msg)

        # Layer 0: DB annotation — check if this message already has a persisted
        # vision_context from a prior turn. This is set by process_image_rag itself
        # after the first describe, then carried in the message dict on subsequent loads.
        db_vision_context = msg.get('vision_context') or ''
        if db_vision_context:
            description = db_vision_context
            # Replace image parts with the pre-existing description
            combined = f'[Image description]\n{description}'
            if user_text:
                combined = f'{combined}\n\n{user_text}'
            msg['content'] = [{'type': 'text', 'text': combined}]
            any_replaced = True
            continue  # next message — no need to check cache or describe

        cache_key = _image_cache_key(image_parts, user_text or '')
        cached = await _get_cached_description(cache_key)

        if cached:
            description = cached
            # Back-fill DB annotation if missing (populates from cache for messages
            # described before DB persistence was available).
            msg_id = msg.get('id')
            chat_id = (metadata or {}).get('chat_id')
            if msg_id and chat_id and not msg.get('vision_context'):
                try:
                    from open_webui.models.chats import Chats

                    await Chats.update_message_vision_context_by_id_and_message_id(chat_id, msg_id, description)
                except Exception:
                    pass  # best-effort

            if is_last_message and event_emitter is not None:
                try:
                    await event_emitter(
                        {
                            'type': 'status',
                            'data': {
                                'action': 'vision_rag',
                                'description': 'Vision Support: image analyzed (cached)',
                                'done': True,
                            },
                        }
                    )
                except Exception:
                    pass
        else:
            if is_last_message and event_emitter is not None:
                try:
                    await event_emitter(
                        {
                            'type': 'status',
                            'data': {
                                'action': 'vision_rag',
                                'description': 'Vision Support: analyzing image...'
                                if not user_text
                                else f'Vision Support: analyzing image for: {user_text[:80]}',
                                'done': False,
                            },
                        }
                    )
                except Exception:
                    pass

            # Build describe messages (per-message: uses THIS message's
            # user_text and image_parts, not the shared system prompt).
            if admin_system_prompt:
                describe_messages = [{'role': 'system', 'content': admin_system_prompt}]
                describe_user_content: list[dict] = [{'type': 'text', 'text': DEFAULT_DESCRIBE_PROMPT}]
                if user_text:
                    describe_user_content.append({'type': 'text', 'text': f"User's message: {user_text}"})
                describe_user_content.extend(image_parts)
                describe_messages.append({'role': 'user', 'content': describe_user_content})
            else:
                describe_instruction = DEFAULT_DESCRIBE_PROMPT
                if user_text:
                    describe_instruction = f"{describe_instruction}\n\nUser's message: {user_text}"

                describe_content: list[dict] = [{'type': 'text', 'text': describe_instruction}]
                describe_content.extend(image_parts)

                describe_messages: list[dict] = []
                if system_prompt:
                    describe_messages.append({'role': 'system', 'content': system_prompt})
                describe_messages.append({'role': 'user', 'content': describe_content})

            payload = {
                'model': vision_model_id,
                'messages': describe_messages,
                'stream': False,
                'max_tokens': 800,  # Cap description length
                'temperature': 0.1,  # Low temperature for factual descriptions
                # Mark this as a sub-task so it can never re-trigger vision-RAG.
                'metadata': {
                    'task': 'vision_rag_description',
                    'chat_id': (metadata or {}).get('chat_id'),
                },
            }

            # generate_chat_completion writes bypass_filter / bypass_system_prompt
            # onto request.state. Save and restore so the parent chat isn't tainted.
            saved_bf = getattr(request.state, 'bypass_filter', False)
            saved_bsp = getattr(request.state, 'bypass_system_prompt', False)
            try:
                response = await generate_chat_completion(
                    request,
                    form_data=payload,
                    user=user,
                    bypass_filter=bypass_filter,
                    # Chatting model's system prompt is injected above as a
                    # system message; don't let the support model's params merge.
                    bypass_system_prompt=True,
                )
            except Exception as e:
                log.warning(f'Vision RAG: describe call to {vision_model_id} failed: {e}')
                continue  # skip this message, try the next
            finally:
                request.state.bypass_filter = saved_bf
                request.state.bypass_system_prompt = saved_bsp

            description = ''
            try:
                description = (
                    (((response or {}).get('choices') or [{}])[0].get('message', {}) or {}).get('content') or ''
                ).strip()
            except Exception:
                description = ''

            if not description:
                log.warning('Vision RAG: describe call returned empty content; skipping.')
                continue  # skip this message

            await _set_cached_description(cache_key, description)

            # Persist to DB so subsequent turns (which load messages from DB)
            # find the description without re-describing.
            msg_id = msg.get('id')
            chat_id = (metadata or {}).get('chat_id')
            if msg_id and chat_id:
                try:
                    from open_webui.models.chats import Chats

                    await Chats.update_message_vision_context_by_id_and_message_id(chat_id, msg_id, description)
                except Exception as e:
                    log.debug(f'Vision RAG: could not persist vision_context to DB: {e}')

            if is_last_message and event_emitter is not None:
                try:
                    await event_emitter(
                        {
                            'type': 'status',
                            'data': {
                                'action': 'vision_rag',
                                'description': 'Vision Support: image analyzed',
                                'done': True,
                            },
                        }
                    )
                except Exception:
                    pass

        # Replace image parts with the description text. Keep the user's original
        # text so the final prompt is: description + original prompt.
        combined = f'[Image description]\n{description}'
        if user_text:
            combined = f'{combined}\n\n{user_text}'
        msg['content'] = [{'type': 'text', 'text': combined}]
        any_replaced = True

    return any_replaced
