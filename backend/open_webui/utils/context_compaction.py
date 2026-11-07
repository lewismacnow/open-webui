from __future__ import annotations

import logging
import re
from typing import Any

from fastapi.responses import JSONResponse
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.misc import get_content_from_message, get_last_user_message, get_message_list
from open_webui.utils.payload import apply_params_to_form_data
from open_webui.utils.task import (
    prompt_template,
    prompt_variables_template,
    replace_messages_variable,
    replace_prompt_variable,
)

log = logging.getLogger(__name__)

_TIKTOKEN_ENC = None
_TIKTOKEN_AVAILABLE = None  # None = unknown, True/False after first attempt

# v0.12.2 sync: upstream constant — inline base64 data URIs are excluded
# from file-token estimation (they are not sent to the model as tags).
BASE64_DATA_URI_RE = re.compile(r'data:[\w/+.;=%-]*;base64,[A-Za-z0-9+/=]*')

DEFAULT_CONTEXT_COMPACTION_PROMPT = """### Task:
Summarize the conversation history that will be compacted out of the active chat context.

### Instructions:
- Preserve key decisions, user preferences, and constraints.
- Preserve files, artifacts, tool results, and code changes that matter going forward.
- Preserve the current task state, unresolved questions, and next steps.
- Be factual and specific. Do not invent details.
- Keep the summary concise, but complete enough for the assistant to continue without the removed messages.
- The recent messages that follow stay in the context verbatim. Summarize only the
  history above; do not restate or duplicate the recent messages.

### Previous Summary:
{{PREVIOUS_SUMMARY}}

### Messages Being Compacted:
{{COMPACTED_MESSAGES}}"""


async def compact_messages_for_request(
    request,
    user,
    messages: list[dict],
    metadata: dict,
    model_id: str,
    models: dict,
    system_prompt: str = '',
) -> tuple[list[dict], str | None, bool]:
    config = await _load_config()
    if not config['enable']:
        return messages, None, False

    system_messages = [messages[0]] if messages and messages[0].get('role') == 'system' else []
    messages = messages[1:] if system_messages else messages

    messages, previous_summary = _apply_latest_summary_checkpoint(messages)
    system_prompt = system_prompt or (get_content_from_message(system_messages[0]) if system_messages else '')
    token_threshold = _resolve_token_threshold(
        config['token_threshold'],
        config['token_cap'],
        metadata,
        config['context_window_ratio'],
    )
    if not _exceeds_token_threshold(messages, system_prompt, previous_summary, token_threshold) or len(messages) <= 3:
        return [*system_messages, *messages], previous_summary, False

    boundary = _find_compaction_boundary(messages, config['retention_percentage'])
    compacted_messages = messages[:boundary]
    recent_messages = messages[boundary:]
    if not compacted_messages or not recent_messages:
        return [*system_messages, *messages], previous_summary, False

    event_emitter = None
    if metadata.get('chat_id') and metadata.get('message_id'):
        from open_webui.socket.main import get_event_emitter

        event_emitter = await get_event_emitter(metadata)

    if event_emitter:
        await event_emitter(
            {
                'type': 'context_compaction',
                'data': {
                    'action': 'context_compaction',
                    'description': 'Compacting context',
                    'done': False,
                },
            }
        )

    try:
        summary = await _generate_summary(
            request,
            user,
            model_id,
            models,
            compacted_messages,
            recent_messages,
            previous_summary,
            config['prompt_template'],
        )
    except Exception:
        if event_emitter:
            await event_emitter(
                {
                    'type': 'context_compaction',
                    'data': {
                        'action': 'context_compaction',
                        'description': 'Context compaction failed',
                        'done': True,
                        'error': True,
                    },
                }
            )
        raise

    chat_id = metadata.get('chat_id')
    checkpoint_message_id = (
        recent_messages[0].get('id') or metadata.get('user_message_id') or metadata.get('message_id')
    )

    # Did the compaction actually buy us anything? If the retained window plus
    # the summary is no smaller than what we started with, compaction cannot
    # rescue this conversation - it will keep re-triggering on every turn and
    # each attempt costs a full-context LLM call. Say so explicitly, because
    # the usual causes are a retention percentage that is too high or a handful
    # of very large messages that survive the cut.
    pre_tokens = (
        _estimate_tokens(system_prompt)
        + _estimate_tokens(previous_summary or '')
        + _estimate_messages_tokens([*compacted_messages, *recent_messages])
    )
    post_tokens = (
        _estimate_tokens(system_prompt) + _estimate_tokens(summary) + _estimate_messages_tokens(recent_messages)
    )
    if post_tokens >= pre_tokens:
        log.warning(
            'Context compaction did not reduce the context (chat=%s): ~%d tokens before, ~%d after '
            '(threshold=%d, retention=%d%%). The retained window is already larger than the budget - '
            'lower the retention percentage, lower the token threshold, or start a new chat.',
            chat_id,
            pre_tokens,
            post_tokens,
            token_threshold,
            config['retention_percentage'],
        )
    elif post_tokens > token_threshold:
        log.warning(
            'Context compaction left the context above the threshold (chat=%s): ~%d tokens after '
            'compaction, threshold=%d. Compaction will re-run on the next turn.',
            chat_id,
            post_tokens,
            token_threshold,
        )

    # Only whole-turn user boundaries correspond to persisted chat checkpoints.
    if recent_messages[0].get('role') == 'user' and is_saved_chat_id(chat_id) and checkpoint_message_id:
        await Chats.upsert_message_to_chat_by_id_and_message_id(
            chat_id,
            checkpoint_message_id,
            {'contextSummary': summary},
            touch=False,
        )

    log.info(
        'Compacted chat context for chat=%s checkpoint=%s response=%s dropped=%d kept=%d summary_chars=%d',
        chat_id,
        checkpoint_message_id,
        metadata.get('message_id'),
        len(compacted_messages),
        len(recent_messages),
        len(summary),
    )

    if event_emitter:
        await event_emitter(
            {
                'type': 'context_compaction',
                'data': {
                    'action': 'context_compaction',
                    'description': 'Context compacted',
                    'done': True,
                },
            }
        )

    return [*system_messages, *recent_messages], summary, True


async def compact_chat_branch(request, user, chat: Any, model_id: str, models: dict) -> dict:
    config = await _load_config()
    if not config['enable']:
        return {'ok': True, 'compacted': False, 'reason': 'disabled'}

    chat_data = chat.chat or {}
    history = chat_data.get('history') or {}
    current_id = getattr(chat, 'current_message_id', None) or history.get('currentId')
    if not current_id:
        current_id = chat_data.get('currentId') or chat_data.get('branchPointMessageId')
    if not current_id and isinstance(chat_data.get('messages'), list) and chat_data['messages']:
        current_id = chat_data['messages'][-1].get('id')
    if not current_id:
        return {'ok': True, 'compacted': False, 'reason': 'empty'}

    messages_map = await Chats.get_messages_map_by_chat_id(chat.id)
    if not messages_map:
        messages_map = history.get('messages') or {}

    messages, previous_summary = _apply_latest_summary_checkpoint(get_message_list(messages_map, current_id))
    compacted_messages = messages[:-1]
    recent_messages = messages[-1:]
    if not compacted_messages or not recent_messages:
        return {'ok': True, 'compacted': False, 'reason': 'too_short'}

    summary = await _generate_summary(
        request,
        user,
        model_id,
        models,
        compacted_messages,
        recent_messages,
        previous_summary,
        config['prompt_template'],
    )
    await Chats.upsert_message_to_chat_by_id_and_message_id(
        chat.id, current_id, {'contextSummary': summary}, touch=False
    )

    return {
        'ok': True,
        'compacted': True,
        'dropped_messages': len(compacted_messages),
        'kept_messages': len(recent_messages),
        'summary_chars': len(summary),
    }


async def _load_config() -> dict:
    values = await Config.get_many(
        'chat.context_compaction.enable',
        'chat.context_compaction.token_threshold',
        'chat.context_compaction.token_cap',
        'chat.context_compaction.retention_percentage',
        'chat.context_compaction.prompt_template',
        'chat.context_compaction.context_window_ratio',
    )
    token_threshold = _parse_positive_int(values.get('chat.context_compaction.token_threshold')) or 80000
    return {
        'enable': bool(values.get('chat.context_compaction.enable', False)),
        'token_threshold': token_threshold,
        'token_cap': _parse_positive_int(values.get('chat.context_compaction.token_cap')) or token_threshold,
        'retention_percentage': _clamp_retention_percentage(values.get('chat.context_compaction.retention_percentage')),
        'prompt_template': values.get('chat.context_compaction.prompt_template', '') or '',
        'context_window_ratio': _clamp_context_window_ratio(values.get('chat.context_compaction.context_window_ratio')),
    }


def _parse_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clamp_retention_percentage(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 40
    return min(50, max(10, parsed))


def _clamp_context_window_ratio(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.7
    return min(0.95, max(0.1, parsed))


def _model_context_window(metadata: dict) -> int | None:
    """The context window (in tokens) declared for the chatting model, if known.

    Models may declare ``params.context_window`` (Admin > Workspace > Models >
    Advanced Params). Nothing in Open WebUI knows a provider's real context
    length, so this is the only way to tie the compaction threshold to the
    window the model actually has.

    The value is read from ``metadata['params']`` first: the model registry
    strips ``info.params`` down to ``model_controls`` (see
    ``utils/models.public_model_params``), so the registry copy of the model
    never carries it. The request path copies it out of the model row into
    ``metadata['params']`` instead. The registry is still checked as a
    fallback for callers that do not go through that path.
    """
    params = metadata.get('params') or {}
    if isinstance(params, dict):
        window = _parse_positive_int(params.get('context_window'))
        if window:
            return window

    model = metadata.get('model') or {}
    if not isinstance(model, dict):
        return None
    info = model.get('info') or {}
    if not isinstance(info, dict):
        return None
    model_params = info.get('params') or {}
    if not isinstance(model_params, dict):
        return None
    return _parse_positive_int(model_params.get('context_window'))


def _resolve_token_threshold(
    global_threshold: int,
    global_cap: int,
    metadata: dict,
    context_window_ratio: float = 0.7,
) -> int:
    """Resolve the token count at which compaction triggers.

    Precedence:
      1. An explicit threshold (chat params, else the model's
         ``params.compact_token_threshold``) - always wins.
      2. Otherwise, if the chatting model declares a context window, derive
         the threshold from it (``window * ratio``). This is what keeps a
         32k model from being compacted at 80k tokens.
      3. Otherwise fall back to the global threshold.

    Everything is clamped by the token cap, so a model-specific override can
    never exceed the ceiling the admin set.
    """
    params = metadata.get('params') or {}
    configured_threshold = _parse_positive_int(
        params.get('compact_token_threshold') if isinstance(params, dict) else None
    )
    if configured_threshold:
        return min(configured_threshold, global_cap)

    window = _model_context_window(metadata)
    if window:
        derived = int(window * context_window_ratio)
        if derived > 0:
            return min(derived, global_cap)

    return min(global_threshold, global_cap)


def _usage_token_count(usage: dict) -> int:
    prompt_tokens = int(usage.get('prompt_tokens') or usage.get('prompt_eval_count') or 0)
    if not prompt_tokens and (usage.get('prompt_n') is not None or usage.get('cache_n') is not None):
        prompt_tokens = int(usage.get('prompt_n') or 0) + int(usage.get('cache_n') or 0)
    if not prompt_tokens:
        prompt_tokens = int(usage.get('input_tokens') or 0)

    completion_tokens = int(
        usage.get('completion_tokens')
        or usage.get('output_tokens')
        or usage.get('eval_count')
        or usage.get('predicted_n')
        or 0
    )
    return prompt_tokens + completion_tokens


async def get_chat_context_usage(chat: Any, model_id: str | None = None, models: dict | None = None) -> dict | None:
    chat_data = chat.chat or {}
    history = chat_data.get('history') or {}
    current_id = getattr(chat, 'current_message_id', None) or history.get('currentId')
    if not current_id:
        current_id = chat_data.get('currentId') or chat_data.get('branchPointMessageId')
    if not current_id and isinstance(chat_data.get('messages'), list) and chat_data['messages']:
        current_id = chat_data['messages'][-1].get('id')
    if not current_id:
        return None

    messages_map = await Chats.get_messages_map_by_chat_id(chat.id)
    messages = get_message_list(messages_map or history.get('messages') or {}, current_id)
    if not messages:
        return None

    config = await _load_config()
    if not config['enable']:
        return None

    params = ((chat.chat or {}).get('params') or {}).copy()
    if model_id:
        params['model'] = model_id
    # Resolve the threshold exactly the way the request path does, so the
    # percentage shown to the user is the number that actually triggers
    # compaction (including a model-derived threshold from its context window).
    model = (models or {}).get(model_id) if model_id else None
    metadata = {'params': params, 'model': model or {}}
    threshold = _resolve_token_threshold(
        config['token_threshold'],
        config['token_cap'],
        metadata,
        config['context_window_ratio'],
    )

    system_prompt = params.get('system') or ''
    if not system_prompt:
        system_message = next((m for m in messages if m.get('role') == 'system'), None)
        system_prompt = get_content_from_message(system_message) if system_message else ''

    messages, previous_summary = _apply_latest_summary_checkpoint(messages)
    tokens, source = _estimate_context_tokens(messages, system_prompt, previous_summary)
    return _build_context_usage(tokens, threshold, source)


def _build_context_usage(tokens: int, threshold: int, source: str = 'estimated') -> dict:
    return {
        'tokens': tokens,
        'estimated_tokens': tokens,
        'threshold': threshold,
        'percent': round((tokens / threshold) * 100) if threshold > 0 else 0,
        'source': source,
    }


def _apply_latest_summary_checkpoint(messages: list[dict]) -> tuple[list[dict], str | None]:
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        value = message.get('contextSummary') or message.get('context_summary')
        if isinstance(value, str) and value.strip():
            return messages[idx:], value

        marker = next(
            (
                item
                for item in reversed(message.get('output') or [])
                if item.get('type') == 'open_webui:context_compaction'
            ),
            None,
        )
        if not marker:
            continue

        # A tool-loop checkpoint can retain output from an earlier reply.
        for cut_idx in range(idx, -1, -1):
            output = messages[cut_idx].get('output') or []
            start = next(
                (
                    position
                    for position, item in enumerate(output)
                    if item.get('type') == 'function_call' and item.get('call_id') == marker['call_id']
                ),
                None,
            )
            if start is not None:
                while start and output[start - 1].get('type') != 'function_call_output':
                    start -= 1
                return [
                    *messages[max(0, cut_idx - 1) : cut_idx],
                    {
                        **messages[cut_idx],
                        'output': [
                            item for item in output[start:] if item.get('type') != 'open_webui:context_compaction'
                        ],
                    },
                    *messages[cut_idx + 1 :],
                ], marker['summary']

    return messages, None


def _estimate_context_tokens(
    messages: list[dict],
    system_prompt: str,
    summary: str | None,
) -> tuple[int, str]:
    """Estimate the context size and report how it was arrived at.

    Prefers the last recorded provider ``usage`` (real numbers) and falls back
    to a tiktoken estimate. Both the trigger and the context indicator use
    this, so the percentage the admin sees always matches the number that
    actually fires compaction.
    """
    for idx in range(len(messages) - 1, -1, -1):
        usage = messages[idx].get('usage') or (messages[idx].get('info') or {}).get('usage')
        if isinstance(usage, dict) and (tokens := _usage_token_count(usage)):
            # The recorded usage already includes the system prompt and the
            # previous summary - they were part of the request it measured.
            return tokens + _estimate_messages_tokens(messages[idx + 1 :]), 'usage'

    estimated = _estimate_tokens(system_prompt) + _estimate_tokens(summary or '') + _estimate_messages_tokens(messages)
    return estimated, 'estimated'


def _exceeds_token_threshold(messages: list[dict], system_prompt: str, summary: str | None, threshold: int) -> bool:
    if threshold <= 0:
        return False

    tokens, _ = _estimate_context_tokens(messages, system_prompt, summary)
    return tokens > threshold


def _find_compaction_boundary(messages: list[dict], retention_percentage: int = 40) -> int:
    retention_percentage = _clamp_retention_percentage(retention_percentage)
    keep_count = max(2, len(messages) * retention_percentage // 100)
    target = max(1, len(messages) - keep_count)
    if any(message.get('role') == 'tool' for message in messages):
        # Keep each call/result batch and its following image messages together.
        boundaries = [
            idx
            for idx, message in enumerate(messages)
            if message.get('role') == 'assistant' and message.get('tool_calls')
        ][1:]
    else:
        boundaries = [idx for idx, message in enumerate(messages) if message.get('role') == 'user'][1:]
    return next((idx for idx in reversed(boundaries) if idx <= target), 0)


def _fit_messages_to_model_window(
    compacted_messages: list[dict],
    recent_messages: list[dict],
    model_window: int | None,
) -> list[dict]:
    """Trim the oldest compacted messages so the summary prompt fits the model.

    The summariser is frequently a smaller model than the chatting one. When
    the history does not fit the summariser's window the summary call fails,
    the failure is swallowed, and the full history is sent to the chat model
    anyway - the exact overflow compaction was supposed to prevent. Dropping
    the oldest compacted messages (the previous summary still carries that
    ground forward) keeps the summary call inside the model.

    Returns the messages unchanged when the window is unknown or the prompt
    already fits.
    """
    if not model_window or model_window <= 0 or not compacted_messages:
        return compacted_messages

    # Leave headroom for the instruction template, the previous summary and
    # the reply the summariser produces.
    budget = int(model_window * 0.8)
    fixed = _estimate_messages_tokens(recent_messages)

    trimmed = list(compacted_messages)
    while len(trimmed) > 1:
        used = fixed + _estimate_messages_tokens(trimmed)
        if used <= budget:
            break
        trimmed = trimmed[1:]

    if len(trimmed) != len(compacted_messages):
        log.warning(
            'Trimmed %d message(s) from the compaction input to fit the summarisation model window (%d tokens).',
            len(compacted_messages) - len(trimmed),
            model_window,
        )
    return trimmed


async def _generate_summary(
    request,
    user,
    model_id: str,
    models: dict,
    compacted_messages: list[dict],
    recent_messages: list[dict],
    previous_summary: str | None,
    summary_prompt_template: str,
) -> str:
    from open_webui.utils.chat import generate_chat_completion

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {**dict(models.items()), request.state.model['id']: request.state.model}

    task_config = await Config.get_many(
        'task.model.params',
        'chat.context_compaction.model',
    )
    context_compaction_model = task_config.get('chat.context_compaction.model')
    task_model_id = context_compaction_model if context_compaction_model in models else model_id
    if context_compaction_model and context_compaction_model not in models:
        # Silently falling back to the chatting model means the summarisation
        # runs on a big (and expensive) model - and on one that may not even
        # be able to hold the history. Say so.
        log.warning(
            'Context compaction model %r is not available; falling back to the chat model %r.',
            context_compaction_model,
            model_id,
        )
    if task_model_id not in models:
        raise ValueError('No available model for context compaction')

    summary_prompt_template = summary_prompt_template.strip() or DEFAULT_CONTEXT_COMPACTION_PROMPT
    # The text-only prompt formatter otherwise omits tool names and arguments.
    compacted_messages, recent_messages = list(compacted_messages), list(recent_messages)
    for messages in (compacted_messages, recent_messages):
        for idx, message in enumerate(messages):
            if message.get('tool_calls'):
                messages[idx] = {
                    **message,
                    'content': f'{get_content_from_message(message) or ""}\n[TOOL CALLS] {JSONCodec.dumps(message["tool_calls"])}',
                }

    # The summariser may be a much smaller model than the chatting one (that
    # is the usual reason to pick a dedicated one). If the history does not
    # fit ITS window the summary call fails, the failure is swallowed and the
    # full history is sent anyway - so trim the oldest compacted messages
    # until the prompt fits.
    compacted_messages = _fit_messages_to_model_window(
        compacted_messages,
        recent_messages,
        _model_context_window({'model': models.get(task_model_id) or {}}),
    )

    all_messages = [*compacted_messages, *recent_messages]
    prompt = replace_prompt_variable(summary_prompt_template, get_last_user_message(all_messages) or '')
    prompt = replace_messages_variable(prompt, all_messages)
    prompt = replace_messages_variable(prompt, compacted_messages, 'COMPACTED_MESSAGES')
    prompt = replace_messages_variable(prompt, recent_messages, 'RECENT_MESSAGES')
    prompt = prompt_variables_template(prompt, {'{{PREVIOUS_SUMMARY}}': previous_summary or ''})
    prompt = await prompt_template(prompt, user)

    task_model_params = task_config.get('task.model.params') or {}
    if not isinstance(task_model_params, dict):
        task_model_params = {}
    task_model_params = {key: value for key, value in task_model_params.items() if value is not None and value != ''}
    task_model_params = task_model_params or {
        'max_tokens': models[task_model_id].get('info', {}).get('params', {}).get('max_tokens', 1000)
    }

    payload = {
        'model': task_model_id,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': 'context_compaction',
        },
    }

    payload = apply_params_to_form_data(payload, models[task_model_id], task_model_params)
    response = await generate_chat_completion(request, form_data=payload, user=user)
    summary = _response_text(response).strip()
    if summary:
        return summary

    # The summariser came back empty. Rather than fail (which would send the
    # full, uncompacted history and overflow the model), fall back to a crude
    # digest - but say so, because a digest is a much weaker summary and the
    # admin should know that is what the model is working from.
    log.warning(
        'The context compaction model %r returned an empty summary; falling back to a truncated digest.',
        task_model_id,
    )
    parts = [previous_summary] if previous_summary else []
    for message in compacted_messages:
        content = get_content_from_message(message)
        if content:
            parts.append(f'- {message.get("role", "unknown")}: {content[:500]}')
    return '\n'.join(parts)[:4000]


def _response_text(response: Any) -> str:
    if isinstance(response, list) and len(response) == 1:
        response = response[0]

    if isinstance(response, JSONResponse):
        try:
            response = JSONCodec.loads(response.body.decode('utf-8', 'replace'))
        except Exception:
            return ''

    if not isinstance(response, dict):
        return ''

    choices = response.get('choices') or []
    if choices:
        message = choices[0].get('message') or {}
        return message.get('content') or message.get('reasoning_content') or ''

    parts = []
    for item in response.get('output') or []:
        for content in item.get('content') or []:
            if isinstance(content, dict):
                parts.append(content.get('text') or content.get('content') or '')
    return '\n'.join(part for part in parts if part)


def _get_tiktoken_encoder():
    """
    Lazily load and cache the cl100k_base tiktoken encoder.

    Returns None if tiktoken is unavailable or fails to load (caller falls
    back to char-based heuristic).
    """
    global _TIKTOKEN_ENC, _TIKTOKEN_AVAILABLE
    if _TIKTOKEN_AVAILABLE is False:
        return None
    if _TIKTOKEN_ENC is not None:
        return _TIKTOKEN_ENC
    try:
        import tiktoken

        _TIKTOKEN_ENC = tiktoken.get_encoding('cl100k_base')
        _TIKTOKEN_AVAILABLE = True
        return _TIKTOKEN_ENC
    except Exception as e:
        log.warning(f'tiktoken unavailable, falling back to char-based token estimate: {e}')
        _TIKTOKEN_AVAILABLE = False
        return None


def _estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        total += 4
        content = message.get('content')
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    total += _estimate_tokens(item)
                elif item.get('type') in {'image', 'image_url'}:
                    total += 1000
                else:
                    total += _estimate_tokens(item.get('text') or item.get('content') or item)
        else:
            total += _estimate_tokens(content)

        total += _estimate_tokens(message.get('output'))
        total += _estimate_tokens(message.get('tool_calls'))
        files = message.get('files')
        if files:
            # Inline data is not part of the file tags sent to the model.
            total += _estimate_tokens(BASE64_DATA_URI_RE.sub('', JSONCodec.dumps(files, ensure_ascii=False)))
    return total


def _estimate_tokens(value: Any) -> int:
    """
    Estimate token count for a value using tiktoken (cl100k_base encoding)
    when available, falling back to len(str(value))//4 when not.
    """
    if value is None:
        return 0
    if not isinstance(value, str):
        try:
            value = JSONCodec.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    if not value:
        return 0
    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return len(enc.encode(value, disallowed_special=()))
        except Exception:
            # encode failure on unusual content — fall through to heuristic
            pass
    return max(1, len(value) // 4)
