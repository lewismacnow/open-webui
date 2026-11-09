"""Guards for the tool-call dispatch in ``utils/middleware.py``.

Two things are covered.

**1. The merge-conflict duplication guard (structural).**

The v0.13.2 upstream sync resolved the ``utils/middleware.py`` conflict by keeping
both sides, and the two sides each consumed the tool-call list:

    response_tool_calls = tool_calls.pop(0)                                  # the fork's ask_user block
    ...
    for tool_call in split_tool_call_batches(tool_calls.pop(0), ...):        # upstream's dispatch

The first pop removed the batch, the second popped from an empty list, so every
tool call died with ``pop from empty list`` before a single tool ran. The
keep-both also dropped the fork's ``answers=`` argument, which the API Tools
path (ask_user Pattern B) depends on.

Those statements are a couple of hundred lines apart in the source and sit in
one of the largest functions in the project, so nothing in the type checker or
the linter sees them. They are asserted here instead.

**2. The ask_user staging behaviour (functional).**

The fork's ask_user handling is deliberately lenient: models routinely emit
malformed ask_user payloads, and upstream's strict validator turns that into a
hard failure for the whole turn. The fork skips the bad entries and keeps
going. Upstream's validator is the strict one, so these assertions also pin
the fork's behaviour against the next sync.
"""

import ast
import json
from pathlib import Path

import pytest

from open_webui.utils.ask_user import ASK_USER_NAME, stage_ask_user_tool_calls

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIDDLEWARE_PATH = BACKEND_DIR / 'open_webui' / 'utils' / 'middleware.py'


def _make_output_id(prefix: str) -> str:
    _make_output_id.counter += 1
    return f'{prefix}_{_make_output_id.counter}'


_make_output_id.counter = 0


def _ask_user_call(arguments: dict, call_id: str = 'call_1') -> dict:
    return {
        'id': call_id,
        'type': 'function',
        'function': {'name': ASK_USER_NAME, 'arguments': json.dumps(arguments)},
    }


def _valid_question(question_id: str = 'q1', option_count: int = 2) -> dict:
    return {
        'id': question_id,
        'question': 'Pick one',
        'options': [
            {'id': f'o{i}', 'label': f'Option {i}', 'description': f'Option {i} in detail'} for i in range(option_count)
        ],
    }


# --------------------------------------------------------------------------------------
# 1. Structural guards
# --------------------------------------------------------------------------------------


def _middleware_functions() -> dict:
    tree = ast.parse(MIDDLEWARE_PATH.read_text())
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _dispatch_functions() -> dict:
    """Functions that dispatch tool calls, keyed by function name.

    A dispatch function is one that iterates ``split_tool_call_batches(...)``.
    """
    out = {}
    for name, fn in _middleware_functions().items():
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'split_tool_call_batches'
            ):
                out[name] = fn
                break
    return out


def test_middleware_has_tool_call_dispatch():
    dispatch = _dispatch_functions()
    assert dispatch, 'no split_tool_call_batches() call found in utils/middleware.py'


def test_tool_call_list_is_popped_once_per_dispatch():
    """The bug: two pops on one list -> ``pop from empty list`` on every tool call."""
    for name, fn in _dispatch_functions().items():
        pops = []
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'pop'
                and isinstance(node.func.value, ast.Name)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 0
            ):
                pops.append((node.func.value.id, node.lineno))

        names = {var for var, _ in pops}
        assert names, f'{name}(): the dispatch no longer pops the tool-call list at all'
        for var in names:
            count = sum(1 for v, _ in pops if v == var)
            assert count == 1, (
                f'{name}(): {var!r} is index-popped {count} times (lines {[ln for v, ln in pops if v == var]}). '
                'Two pops on one list means the second one pops an empty list -> '
                '"pop from empty list" on every tool call.'
            )


def test_ask_user_is_staged_once_with_answers():
    """The bug: the keep-both staged ask_user twice and dropped ``answers=``.

    ``answers=`` is what the API Tools path (Pattern B) needs — without it the
    staged question waits on a socket that never answers and the request
    deadlocks.
    """
    for name, fn in _dispatch_functions().items():
        calls = [
            node
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'stage_ask_user_tool_calls'
        ]
        assert len(calls) == 1, f'{name}(): stage_ask_user_tool_calls() is called {len(calls)} times, expected 1'
        keywords = {kw.arg for kw in calls[0].keywords}
        assert 'answers' in keywords, (
            f'{name}(): stage_ask_user_tool_calls() is called without answers= — '
            'the API Tools path would stage the question and wait forever.'
        )


def test_no_function_index_pops_the_same_list_twice():
    """Repo-wide guard for the same duplication class.

    A name index-popped with ``.pop(0)`` twice inside one function is the
    signature of a keep-both conflict resolution. If a hit is legitimate
    (a mapping keyed by 0, say), use a local alias rather than weakening
    this check.
    """
    offenders = []
    for path in sorted((BACKEND_DIR / 'open_webui').rglob('*.py')):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails CI elsewhere
            continue
        rel = path.relative_to(BACKEND_DIR)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            counts = {}
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'pop'
                    and isinstance(node.func.value, ast.Name)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 0
                    and not isinstance(node.args[0], bool)
                ):
                    counts.setdefault(node.func.value.id, []).append(node.lineno)
            for var, lines in counts.items():
                if len(lines) > 1:
                    offenders.append(f'{rel}:{fn.name}() pops {var!r} at lines {lines}')

    assert not offenders, 'index-pop duplication(s) found:\n  ' + '\n  '.join(offenders)


# --------------------------------------------------------------------------------------
# 2. ask_user staging behaviour
# --------------------------------------------------------------------------------------


def test_ask_user_without_answers_stages_pending():
    """Legacy / UI-socket flow: stage as pending and let the socket answer."""
    output = []
    staged, error = stage_ask_user_tool_calls(
        [_ask_user_call({'questions': [_valid_question()]})], output, _make_output_id
    )

    assert staged is True
    assert error is None
    assert output[0]['type'] == 'function_call'
    assert output[0]['name'] == ASK_USER_NAME
    assert output[0]['status'] == 'pending'
    assert not [item for item in output if item['type'] == 'function_call_output']


def test_ask_user_with_answers_resolves_immediately():
    """API Tools path (Pattern B): the answers come back in the same request."""
    output = []
    staged, error = stage_ask_user_tool_calls(
        [_ask_user_call({'questions': [_valid_question()]})],
        output,
        _make_output_id,
        answers={'q1': 'Option 0'},
    )

    assert staged is True
    assert error is None
    assert output[0]['status'] == 'completed'
    results = [item for item in output if item['type'] == 'function_call_output']
    assert len(results) == 1
    payload = json.loads(results[0]['output'][0]['text'])
    assert payload['status'] == 'ok'
    assert payload['answers'] == {'q1': 'Option 0'}


def test_ask_user_with_partial_answers_reports_the_missing_ids():
    output = []
    staged, error = stage_ask_user_tool_calls(
        [_ask_user_call({'questions': [_valid_question('q1'), _valid_question('q2')]})],
        output,
        _make_output_id,
        answers={'q1': 'Option 0'},
    )

    assert staged is True
    assert error is None
    results = [item for item in output if item['type'] == 'function_call_output']
    assert len(results) == 1
    payload = json.loads(results[0]['output'][0]['text'])
    assert payload['status'] == 'error'
    assert payload['error_type'] == 'missing_answers'
    assert payload['missing_question_ids'] == ['q2']


@pytest.mark.parametrize(
    'question',
    [
        pytest.param({**{k: v for k, v in _valid_question().items() if k != 'id'}}, id='question-without-id'),
        pytest.param(_valid_question(option_count=5), id='five-options'),
        pytest.param(
            {'id': 'q1', 'question': 'Pick one', 'options': ['Option 0', 'Option 1']},
            id='options-not-objects',
        ),
    ],
)
def test_ask_user_validation_is_lenient(question):
    """Malformed payloads are tolerated, not fatal.

    Upstream's validator raises on each of these; the fork skips the bad
    entry and keeps the turn alive.
    """
    output = []
    staged, error = stage_ask_user_tool_calls(
        [_ask_user_call({'questions': [question]})],
        output,
        _make_output_id,
        answers={'q1': 'Option 0'},
    )

    # Either the entry was normalised into something usable, or it was skipped
    # without taking the whole turn down. What must not happen is an
    # unhandled exception or a hard failure of the request.
    assert staged is True
    assert error is None or 'ask_user' in error


def test_ask_user_rejects_a_non_list_questions_payload():
    output = []
    staged, error = stage_ask_user_tool_calls(
        [_ask_user_call({'questions': 'not-a-list'})],
        output,
        _make_output_id,
    )

    assert staged is True
    assert error is not None
    assert 'ask_user' in error
