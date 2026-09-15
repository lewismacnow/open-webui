import time
from collections.abc import Callable
from typing import Optional

from open_webui.utils.json_codec import JSONCodec


ASK_USER_NAME = 'ask_user'


def get_ask_user_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], str | None]:
    ask_user_calls = [
        tool_call for tool_call in tool_calls if tool_call.get('function', {}).get('name') == ASK_USER_NAME
    ]
    if not ask_user_calls:
        return [], None
    if len(tool_calls) != 1:
        return (
            ask_user_calls,
            'Error: ask_user must be the only tool call, so it did not run. Call ask_user on its own.',
        )
    if len(ask_user_calls) != 1:
        return ask_user_calls, 'Error: only one ask_user call is allowed per turn.'
    return ask_user_calls, None


def normalize_ask_user_request(arguments: dict) -> dict:
    questions = arguments.get('questions')
    # 1-5 questions per call (relaxed from the upstream 1-3 limit). The
    # tool's published docstring on tools/builtin.py:ask_user says the
    # same thing — keep the two in sync.
    if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
        raise ValueError('ask_user requires 1-5 questions.')

    normalized_questions = []
    seen_ids = set()
    allow_other = bool(arguments.get('allow_other', True))
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError('Each question must be an object.')

        question_id = str(question.get('id') or '').strip()[:64]
        if not question_id:
            raise ValueError('Each question requires a non-empty id.')
        if question_id in seen_ids:
            raise ValueError(f'Duplicate question id: {question_id}')
        seen_ids.add(question_id)

        options = question.get('options')
        # 2-5 options per question (relaxed from the upstream 2-3 limit).
        # Updated to match the tool's published docstring so the model's
        # contract is consistent with the validator.
        if not isinstance(options, list) or not 2 <= len(options) <= 5:
            raise ValueError('Each question requires 2-5 options.')

        normalized_options = []
        for option in options:
            if not isinstance(option, dict):
                raise ValueError('Each option must be an object.')
            label = str(option.get('label') or '').strip()[:80]
            description = str(option.get('description') or '').strip()[:240]
            if not label or not description:
                raise ValueError('Each option requires a label and description.')
            normalized_options.append({'label': label, 'description': description})

        question_text = str(question.get('question') or '').strip()[:500]
        if not question_text:
            raise ValueError('Each question requires question text.')

        normalized_questions.append(
            {
                'id': question_id,
                'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                'question': question_text,
                'options': normalized_options,
                'allow_other': bool(question.get('allow_other', allow_other)),
            }
        )

    timeout_ms = arguments.get('timeout_ms', 120_000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
        timeout_ms = 120_000

    return {
        'questions': normalized_questions,
        'allow_other': allow_other,
        'timeout_ms': timeout_ms,
    }


def stage_ask_user_tool_calls(
    tool_calls: list[dict],
    output: list[dict],
    make_output_id: Callable[[str], str],
    answers: Optional[dict[str, str]] = None,
) -> tuple[bool, str | None]:
    """Validate and stage the model's ``ask_user`` tool calls.

    Two modes:

    - **No answers supplied** (legacy / UI-socket flow): stage as
      ``pending`` and let a downstream UI socket write the function_call_output
      back. In API Tools mode this deadlocks — the caller must supply
      ``answers`` explicitly.
    - **Answers supplied** (API Tools flow): validate the answers cover
      every question, then immediately emit a ``function_call_output`` with
      the resolved answers as ``completed``. The model sees the answers on
      its next turn and continues. No pending stage, no downstream dependency.
    """
    ask_user_calls, error = get_ask_user_tool_calls(tool_calls)
    if not ask_user_calls:
        return False, None

    for tool_call in ask_user_calls:
        call_id = tool_call.get('id') or make_output_id('fc')
        raw_arguments = tool_call.get('function', {}).get('arguments', '{}')
        arguments = raw_arguments

        if not error:
            try:
                parsed_arguments = JSONCodec.loads(raw_arguments or '{}')
                if not isinstance(parsed_arguments, dict):
                    raise ValueError('ask_user arguments must be an object.')
                arguments = JSONCodec.dumps(normalize_ask_user_request(parsed_arguments))
            except (JSONCodec.JSONDecodeError, TypeError, ValueError) as exc:
                error = f'Error: {exc}'

        item = {
            'type': 'function_call',
            'id': call_id or make_output_id('fc'),
            'call_id': call_id,
            'name': ASK_USER_NAME,
            'arguments': arguments,
            'status': 'completed' if error else 'pending',
        }

        existing_item = next(
            (
                existing
                for existing in output
                if existing.get('type') == 'function_call'
                and (
                    existing.get('call_id') == call_id
                    or existing.get('id') == tool_call.get('id')
                    or (
                        not existing.get('call_id')
                        and existing.get('name') == ASK_USER_NAME
                        and existing.get('status') not in {'rejected', 'failed'}
                    )
                )
            ),
            None,
        )
        if existing_item:
            existing_item.update(item)
        else:
            output.append(item)

        # Every invalid call needs its own result, or the UI waits on it forever.
        if error:
            output.append(
                {
                    'type': 'function_call_output',
                    'id': make_output_id('fco'),
                    'call_id': call_id,
                    'output': [{'type': 'input_text', 'text': error}],
                    'status': 'completed',
                }
            )
            continue

        # API Tools path: if the caller supplied ask_user_answers and they
        # cover every question in this tool call, immediately emit the
        # resolved function_call_output so the model can continue. This is
        # the only viable path for non-UI consumers — there's no socket to
        # wait for, so the legacy "pending" stage would deadlock forever.
        parsed_args: dict | None = None
        resolved: dict[str, str] = {}
        missing: list[str] = []

        # Defensive: a malformed ask_user_answers (None-as-list, a bare
        # string, etc.) shouldn't crash the loop. Treat anything that
        # isn't a dict[str, str] as "no answers" and fall back to the
        # legacy pending stage.
        answers_dict: Optional[dict[str, str]] = (
            answers
            if isinstance(answers, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items())
            else None
        )

        if answers_dict is not None:
            try:
                parsed_args = JSONCodec.loads(arguments)
            except JSONCodec.JSONDecodeError:
                # Staging already normalised the args; this should be
                # unreachable. Fall back to pending rather than crash.
                parsed_args = None

            if isinstance(parsed_args, dict):
                questions = parsed_args.get('questions') or []
                # Normalise every answer value to string so downstream
                # consumers always see a consistent shape. Non-string
                # values (e.g. the caller passed numbers) fall through as
                # missing rather than silently corrupting the resolved
                # function_call_output.
                for q in questions:
                    qid = q.get('id')
                    if not isinstance(qid, str) or not qid:
                        missing.append(str(qid) if qid is not None else '<missing>')
                        continue
                    selected = answers_dict.get(qid)
                    if not isinstance(selected, str) or not selected:
                        missing.append(qid)
                    else:
                        resolved[qid] = selected
                if missing:
                    output.append(
                        {
                            'type': 'function_call_output',
                            'id': make_output_id('fco'),
                            'call_id': call_id,
                            'output': [
                                {
                                    'type': 'input_text',
                                    'text': JSONCodec.dumps(
                                        {
                                            'status': 'error',
                                            'error_type': 'missing_answers',
                                            'error': (
                                                f'ask_user is missing answers for question_ids: {missing}. '
                                                'Resupply the request with ask_user_answers covering every question '
                                                "in the model's ask_user call."
                                            ),
                                            'missing_question_ids': missing,
                                        }
                                    ),
                                }
                            ],
                            'status': 'completed',
                        }
                    )
                    continue

                # All answers present — emit the resolved function_call_output
                # so the model can continue.
                resolved_payload = {
                    'status': 'ok',
                    'answers': resolved,
                    'responder': 'api-supplied',
                    'answered_at': int(time.time()),
                }
                item['status'] = 'completed'
                output.append(
                    {
                        'type': 'function_call_output',
                        'id': make_output_id('fco'),
                        'call_id': call_id,
                        'output': [{'type': 'input_text', 'text': JSONCodec.dumps(resolved_payload)}],
                        'status': 'completed',
                    }
                )
                continue

    return True, error
