from __future__ import annotations

import json
import logging
import re
from typing import Optional

from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.plugin import load_tool_module_by_id
from open_webui.utils.tools import get_tool_specs

log = logging.getLogger(__name__)

MAX_GENERATION_ATTEMPTS = 3

TOOL_GENERATION_SYSTEM_PROMPT = """You are an expert Python developer creating tools for Open WebUI. Generate well-structured, type-hinted Python code that follows the Open WebUI tool format exactly.

## Tool Format

Every tool MUST define a top-level `class Tools:`. Each public method (not starting with `_`) becomes a callable tool function. Here's the exact pattern:

```python
\"\"\"
title: Tool Name
description: Brief description of what this tool does
requirements: requests, beautifulsoup4
\"\"\"

import json
from pydantic import BaseModel, Field

class Tools:
    def __init__(self):
        pass

    def my_function(self, param1: str, param2: int = Field(default=10, description="Description of param2")) -> str:
        \"\"\"Brief description shown to the AI model.

        :param param1: Description of param1
        :param param2: Description of param2
        :return: Description of return value
        \"\"\"
        result = {"param1": param1, "param2": param2}
        return json.dumps(result)
```

## Critical Rules

1. MUST define `class Tools:` at the top level — the loader instantiates `module.Tools()`
2. Each public method MUST have:
   - Full type hints on ALL parameters and return type
   - A docstring (first line = description; `:param name:` lines = parameter descriptions)
   - Use `Field(default, description=...)` for parameter defaults
3. Return strings (typically `json.dumps(result)`) — the model reads return values as text
4. Handle errors with try/except — return error messages as strings, never raise
5. Use `json.dumps(result, ensure_ascii=False)` for structured data
6. Methods starting with `_` are excluded from the tool spec

## Available Python Packages

Standard library: os, sys, json, re, datetime, math, asyncio, urllib, hashlib, base64, csv, xml, etc.
Third-party (already installed): requests, aiohttp, httpx, pydantic, beautifulsoup4, lxml, openai, pandas, pillow, tiktoken, validators, ddgs (DuckDuckGo)

## Auto-Injected Parameters (add to method signature if needed, never in docstrings)

- `__user__: dict = {}` — {id, email, name, role, timezone}
- `__request__` — the FastAPI Request object
- `__event_emitter__` — callable for streaming UI updates
- `__files__: list = []` — attached files

## Optional: Configurable Settings (Valves)

```python
class Valves(BaseModel):
    api_key: str = Field(default="", description="API key")
    max_results: int = Field(default=10, description="Max results")
```
Access via `self.valves.api_key` at runtime. Always check `if self.valves:` before accessing.

## Output Format

Output ONLY the Python code wrapped in a single ```python code block. No explanations before or after. Start with the frontmatter comment if requirements are needed."""


def _extract_code_from_response(text: str) -> str:
    """Extract Python code from a model response that may have markdown fences."""
    text = text.strip()
    # Match from the FIRST opening fence to the LAST closing fence — handles
    # nested code blocks inside docstrings/examples without truncating.
    match = re.search(r'```(?:python|py)?\s*\n(.*)```\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # No fences — assume the entire response is code
    return text.strip()


def _extract_frontmatter_value(content: str, key: str) -> Optional[str]:
    """Extract a value from the frontmatter block (e.g., title, description)."""
    match = re.search(rf'^{key}:\s*(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


async def _validate_tool_code(content: str) -> tuple[bool, str, Optional[list]]:
    """Validate generated tool code by loading it.

    Returns (success, message, specs).
    """
    try:
        tools_instance, frontmatter = await load_tool_module_by_id('_validation', content=content)
        if tools_instance is None:
            return False, 'No Tools class found in generated code', None
        specs = get_tool_specs(tools_instance)
        if not specs:
            return False, 'No public tool methods found (add methods with type hints)', None
        func_names = [s.get('name', '?') for s in specs]
        return True, f'Valid: {len(specs)} function(s): {", ".join(func_names)}', specs
    except Exception as e:
        return False, f'Validation error: {e}', None


async def generate_tool_code(
    request,
    model_id: str,
    user_prompt: str,
    existing_code: str = '',
    existing_name: str = '',
    existing_description: str = '',
    user=None,
    existing_tool_specs: Optional[list] = None,
) -> dict:
    """Generate tool code via a model with self-improvement validation loop.

    Returns dict with:
      - code: generated Python code
      - name: extracted tool name
      - description: extracted description
      - validation_passed: bool
      - validation_message: str
      - attempts: int (number of generation attempts)
    """
    # Build the system prompt, optionally enriched with existing tool context
    system_prompt = TOOL_GENERATION_SYSTEM_PROMPT

    # Include existing tool specs so the model can compose rather than duplicate
    if existing_tool_specs:
        tools_summary = json.dumps(existing_tool_specs[:20], indent=2, ensure_ascii=False)
        system_prompt += (
            f'\n\n## Existing Tools in This Workspace\n\n'
            f'The following tools already exist. Consider composing with them instead of duplicating:\n\n'
            f'{tools_summary}'
        )

    # Build the user message
    if existing_code:
        user_message = (
            f'I want to modify an existing tool. Here is the current code:\n\n'
            f'```python\n{existing_code}\n```\n\n'
            f'My request: {user_prompt}\n\n'
            f'Generate the COMPLETE updated tool code (not just the changes).'
        )
    else:
        user_message = (
            f'Create a new tool based on this description:\n\n{user_prompt}\n\nGenerate the complete tool code.'
        )

    # Self-improvement loop
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_message},
    ]

    code = ''
    message = ''

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        # Call the model
        payload = {
            'model': model_id,
            'messages': messages,
            'stream': False,
            'temperature': 0.2,  # low temp for code generation
        }

        try:
            response = await generate_chat_completion(request, form_data=payload, user=user)
        except Exception as e:
            log.warning(f'Tool generation: model call failed on attempt {attempt}: {e}')
            return {
                'code': '',
                'name': existing_name or '',
                'description': existing_description or '',
                'validation_passed': False,
                'validation_message': f'Model call failed: {e}',
                'attempts': attempt,
            }

        # Extract code from response
        raw_response = ''
        try:
            raw_response = (((response or {}).get('choices') or [{}])[0].get('message', {}) or {}).get('content') or ''
        except Exception:
            raw_response = ''

        if not raw_response:
            return {
                'code': '',
                'name': existing_name or '',
                'description': existing_description or '',
                'validation_passed': False,
                'validation_message': 'Model returned empty response',
                'attempts': attempt,
            }

        code = _extract_code_from_response(raw_response)

        # Validate
        valid, message, specs = await _validate_tool_code(code)

        if valid:
            # Extract name and description from frontmatter or infer from code
            name = _extract_frontmatter_value(code, 'title') or existing_name or 'Generated Tool'
            description = _extract_frontmatter_value(code, 'description') or existing_description or 'AI-generated tool'
            return {
                'code': code,
                'name': name,
                'description': description,
                'validation_passed': True,
                'validation_message': message,
                'attempts': attempt,
            }

        # Validation failed — feed the error back to the model for retry
        log.info(f'Tool generation: attempt {attempt} failed validation: {message}')
        if attempt < MAX_GENERATION_ATTEMPTS:
            messages.append({'role': 'assistant', 'content': raw_response})
            messages.append(
                {
                    'role': 'user',
                    'content': (
                        f'The code failed validation: {message}\n\n'
                        f'Please fix the issue and regenerate the complete tool code. '
                        f'Make sure the code defines `class Tools:` with properly typed public methods.'
                    ),
                }
            )

    # All attempts failed — return last code with validation failure
    name = _extract_frontmatter_value(code, 'title') or existing_name or 'Generated Tool'
    description = _extract_frontmatter_value(code, 'description') or existing_description or 'AI-generated tool'
    return {
        'code': code,
        'name': name,
        'description': description,
        'validation_passed': False,
        'validation_message': message or 'Validation failed after all attempts',
        'attempts': MAX_GENERATION_ATTEMPTS,
    }
