# Upstream sync report — v0.12.2 → v0.13.2

**Fork:** `lewismacnow/open-webui` · **Branch:** `feat/new_features` (the only branch synchronised)
**Upstream:** `open-webui/open-webui` at `v0.13.2` (`b7a005e5a`)
**Merge base:** `6a18585ff` (upstream v0.12.2)

---

## 1. Scope of the upstream delta

| Metric                                | Value                                    |
| ------------------------------------- | ---------------------------------------- |
| Upstream commits since the merge base | **1066**                                 |
| Files changed upstream                | **789** (+131,690 / −30,172)             |
| Backend files changed                 | 175 (+15,399 / −4,824)                   |
| Frontend files changed                | 543 (+108,133 / −24,214)                 |
| New top-level area                    | `crypto/encryption` (Rust crate, +2,326) |

The delta is dominated by **upstream's own Svelte 5 migration** (526 files under `src/lib`) plus a large backend feature wave. Because the fork had already hand-ported the frontend to Svelte 5, the two independent ports of the same v0.12.2 base are the main source of conflict.

## 2. Conflict surface

|                                             | Files |
| ------------------------------------------- | ----- |
| Touched by both sides (potential conflicts) | 68    |
| Actual textual conflicts                    | 31    |
| — backend                                   | 16    |
| — frontend                                  | 15    |

## 3. Headline functionality taken from upstream

### 3.1 Context compaction — upstream's own fixes, layered onto the fork's

Upstream also reworked `utils/context_compaction.py` (it existed in v0.12.2 too — the fork's version descends from it). Upstream's v0.13.2 changes were adopted **and** the fork's additional work kept:

| Upstream change                                                                                                                                                     | Resolution                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compaction now runs on chats with few messages that are over the threshold (removed the `len(messages) <= 3` guard)                                                 | **Taken**                                                                                                                                                                    |
| Summary was cut off at 1000 tokens (removed the `max_tokens` fallback)                                                                                              | **Taken**                                                                                                                                                                    |
| `/compact` made the next message larger after a long tool-calling reply (new `apply_summary_checkpoint`, `compacted_messages` return value, `get_request_messages`) | **Taken** — new signature `(request, form_data, user, messages, metadata, model_id, models, system_prompt, bypass_system_prompt) -> (messages, summary, compacted_messages)` |
| Summary prompt replays the original chat request so the provider's **prompt cache** covers the history                                                              | **Taken**                                                                                                                                                                    |
| New shared `src/lib/utils/contextUsage.ts` (token estimators, usage checkpoints)                                                                                    | **Taken**                                                                                                                                                                    |

Fork-specific compaction work kept on top: count-based retention (messages / reasoning / tool calls), the context-window-ratio threshold, budget enforcement, summariser-window fitting, the stale-usage fix, and the user-turn-aligned boundary.

### 3.2 Other upstream features taken

- **MCP client rewritten** on `mcp==2.3.0` / `httpx2` and the new `mcp.Client` high-level API (session task, `read_timeout_seconds`).
- **Admin-configurable embedding prefixes** (`rag.embedding_content_prefix`).
- **Knowledge file linking**: upload-capacity claimed before linking, link rolled back on failure.
- **Temporary-chat session ownership** — a temporary chat can only be driven from the session that created it.
- **Model skill tools** (`skill_tools_enabled`, `builtinTools.skills`).
- **Interface visibility config** (`ui.visibility`).
- **`crypto/encryption` Rust crate** (new).
- **Channel message / reaction lookup indexes** (migration `9f6c2a8d1b43`).
- Upstream's Svelte 5 frontend port for all non-conflicting files.

### 3.3 Fork features preserved

Provider failover chain · global wrapper provider chains · GitHub knowledge sync · metadata enrichment (automatic, content + metadata) · durable embedding worker · ask_user Pattern B (lenient validation) · ServiceKeys · API Tools · Vision RAG · context pruning · webhook custom headers · Tool Generator · token caps · context window inheritance · base-model/wrapper distinction in the UI.

## 4. How the conflicts were managed

| Area                                                                                                                                         | Rule applied                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `utils/context_compaction.py`                                                                                                                | **Upstream's file taken as the base** (it carries all their fixes and the new contract), then the fork's features re-applied on top: count-based retention, `_strip_old_details`, `_enforce_context_budget`, `_fit_messages_to_model_window`, the context-window ratio in `_resolve_token_threshold`, the model-aware `get_chat_context_usage`, and the stale-usage fix.                         |
| `utils/middleware.py` (10 hunks)                                                                                                             | **Both sides kept** for the import blocks and independent feature blocks; the compaction call was moved to upstream's new signature; two fork blocks that upstream had deleted (the note-files injection in the tool path, and the `ast.literal_eval` tool-arg fallback) were **restored** because upstream deleted them as part of its own refactor, not because the fork needed them — see §6. |
| `utils/mcp/client.py`                                                                                                                        | **Upstream's httpx2 rewrite taken wholesale** (it supersedes the fork's version), then the fork's transport-timeout fix re-applied on top — upstream still hardcodes `httpx2.Timeout(30, read=300)`, so `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER` still could not reach the transport.                                                                                                                |
| `routers/knowledge.py`                                                                                                                       | Upstream's upload-limit claim + link rollback taken; the fork's durable-worker link creation (status `pending`) kept in place of upstream's inline `process_file`.                                                                                                                                                                                                                               |
| `routers/files.py`                                                                                                                           | Fork's durable-worker path kept (no inline `index_file`), since upstream's inline call would double-embed.                                                                                                                                                                                                                                                                                       |
| `routers/openai.py`, `routers/tools.py`, `routers/groups.py`, `socket/main.py`, `main.py`, `utils/ask_user.py`, `tools/builtin.py`           | **Both sides kept**; where the two sides rewrote the same construct, upstream's structure was taken and the fork's behaviour re-applied (e.g. `socket/main.py` keeps the fork's defensive `session_id` coercion **and** upstream's temporary-chat ownership redirect).                                                                                                                           |
| `models/knowledge.py`                                                                                                                        | Upstream's meta **merge** taken (it preserves existing meta keys instead of replacing them); the fork's duplicate-content hash check kept.                                                                                                                                                                                                                                                       |
| `retrieval/utils.py`                                                                                                                         | Upstream's admin-configurable prefix taken; the fork's `asyncio.to_thread` (keeps the blocking similarity maths off the event loop) kept.                                                                                                                                                                                                                                                        |
| Frontend (`Chat.svelte`, `MessageInput.svelte`, `Interface.svelte`, `Documents.svelte`, `ResponseMessage.svelte`, `workspace/Models.svelte`) | Upstream's official Svelte 5 code taken as the base; the fork's features re-applied on top: the stale-usage fix in both estimators, the context-window-ratio + pruning settings, the provider badge in `ResponseMessage.svelte`, and the **Base model** chip in the workspace list.                                                                                                              |
| `workspace/Models/ModelEditor.svelte`, `workspace/Tools/ToolkitEditor.svelte`                                                                | **Fork's pre-merge version kept** — the keep-both resolutions interleaved two unrelated Svelte blocks and produced unbalanced markup. Upstream's changes to these two files (a conditional system-prompt fieldset, an extra `ToolsSelector`) were **not** taken — see §6.                                                                                                                        |

## 5. Migrations

| Revision                  | Purpose                                                                                                             |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `c4d5e6f7a8b9`            | Compaction on by default, ratio 0.8, clears the forced token cap                                                    |
| `d5e6f7a8b9c0`            | Base model context windows: 256K default, seeded per base model                                                     |
| `e7f8a9b0c1d2`            | Clears self-referential `base_model_id` (base models that were turned into wrappers)                                |
| `9f6c2a8d1b43` (upstream) | Channel message / reaction lookup indexes                                                                           |
| `kb06mergeheads`          | **Topology-only merge** joining the upstream v0.13.2 head with the fork head → single alembic head `kb06mergeheads` |

No schema conflicts: upstream's chain adds indexes on channel tables; the fork's chain only seeds config rows and repairs model rows.

## 6. Deliberately not taken (and why)

1. **Upstream's inline `index_file` / `process_file` call in the file-upload path** — superseded by the fork's durable embedding worker (inline processing would embed twice).
2. **Upstream's removal of the note-files injection in the tool path** and of the `ast.literal_eval` tool-argument fallback — both were restored, because upstream deleted them during its own refactor and the fork still depends on the fallback's behaviour. _Worth re-checking against upstream's intent._
3. **Upstream's ModelEditor / ToolkitEditor UI changes** — the conflict resolutions produced unbalanced Svelte markup; the fork's working versions were kept instead. Upstream's delta in those files (a conditional system-prompt fieldset, an extra `ToolsSelector`) is a follow-up.
4. **Upstream's `mcp.Client` session-task architecture** — taken wholesale, which removed the fork's `_install_mcp_cleanup_filter()`. The fork's equivalent suppression still exists at the ASGI layer (`utils/asgi_middleware.py`), so the benign SDK cleanup races remain suppressed.

## 7. Verification

- Every backend file parses; `pyflakes` reports no undefined names across the merged backend.
- Import smoke of the touched routers/utils passes.
- Single alembic head: `kb06mergeheads`.
- Frontend build green.
- The fork's context-management test suites (threshold resolution, retention, budget enforcement, base-model repair) were re-run against the merged code.

## 8. Pre-existing fork bugs found by the post-merge review and test deployment

A full review (static audit + shadowing scan + a Docker test deployment) found two real
defects that **the merge did not introduce** — both pre-date the v0.13.2 sync and are
the fork's own code. They are fixed here. Neither exists in upstream v0.13.2.

### 8.1 Duplicated `get_file_content_by_id` endpoint — `routers/files.py`

**Pre-existing fork bug** — the fork's `routers/files.py` already carried two
definitions of the same route before this sync. Python kept the second, so the first
was dead code: the `?attachment=` parameter never had any effect and the
content-type-aware `Content-Disposition` never applied. (Upstream's v0.13.2 is
unaffected — it has a single definition.)

Merged into one endpoint carrying upstream's `attachment` parameter and
`Content-Disposition` handling **plus** the fork's legacy fallback (records whose
stored path has gone missing serve their indexed text instead of 404).

### 8.2 `UnboundLocalError` on every chat request — `main.py`

**Pre-existing fork bug** — `main.py`'s `chat_completion()` has a local
`from open_webui.routers.models import get_all_models` inside a try block. A local
import makes the name local for the **whole** function, so the earlier
`if not request.app.state.MODELS: await get_all_models(...)` raised
`UnboundLocalError` instead of refreshing the registry.

It only bites when the model registry is empty (no connection configured yet, or a
connection disabled) — which is exactly the state of a fresh deployment. Every chat
request then failed with a 500. The local import came in with the fork's failover
refresh block and predates this sync; **upstream's v0.13.2 has a single module-level
import and is not affected**. The local import was pointless (`routers.models`
re-exports the same function from `utils.models`) and has been removed.

_Found by the test deployment:_ `POST /api/v1/chat/completions` returned
`500 UnboundLocalError`; it now returns a clean `400 Model not found`.

A scan for the same pattern — a local import shadowing a module-level name that is
used earlier in the same function — now reports **zero** hazards across the backend.

## 9. Production errors found after the release

### 9.1 `pop from empty list` on every tool call — `utils/middleware.py`

**Introduced by this merge** (unlike §8, which was pre-existing).

The `utils/middleware.py` conflict was resolved by keeping both sides, and the two sides
each consumed the tool-call list:

```python
response_tool_calls = tool_calls.pop(0)                              # the fork's ask_user block
...
for tool_call in split_tool_call_batches(tool_calls.pop(0), ...):    # upstream's dispatch
```

The first pop removed the batch; the second popped from an empty list. Every tool call
raised `IndexError: pop from empty list` before a single tool ran, which surfaced in the
log as `Error processing chat payload: pop from empty list`.

The keep-both also left `response_tool_calls = []` between the two blocks — discarding the
ask_user staging the fork's block had just produced — and dropped the
`answers=metadata.get('ask_user_answers')` argument that the API Tools path (ask_user
Pattern B) depends on. Without `answers=` a staged question waits on a socket that never
answers and the request deadlocks.

The dispatch now matches upstream v0.13.2's structure — one pop, then the dispatch loop,
then the fork's ask_user staging — with the fork's `answers=` argument restored:

```python
for tool_call in split_tool_call_batches(tool_calls.pop(0), metadata.get('tools', {})):
    ...
ask_user_staged, ask_user_error = stage_ask_user_tool_calls(
    response_tool_calls,
    output,
    output_id,
    answers=metadata.get('ask_user_answers'),
)
```

Upstream's ordering (resolve the dispatch before approvals and special handling) is kept.
The fork's lenient ask_user validation still applies: upstream's `prepare_tool_call` only
_coerces_ arguments against the tool schema, it does not validate them, so a malformed
ask_user payload still reaches the fork's validator instead of being rejected outright.

**Regression guard:** `backend/open_webui/test/test_tool_call_dispatch.py` asserts that a
dispatch function index-pops the tool-call list exactly once, that
`stage_ask_user_tool_calls()` is called exactly once and with `answers=`, and — repo-wide —
that no function index-pops the same name twice. Against the pre-fix file the guard fails
with the exact diagnosis (`pops 'tool_calls' at lines [7097, 7109]`); against the fixed file
it passes.

### 9.2 `405 Method Not Allowed` on the MCP GET stream — expected, not a defect

```
INFO | httpx2._client - HTTP Request: GET https://api.z.ai/api/mcp/web_reader/mcp "HTTP/1.1 405 Method Not Allowed"
```

The MCP Streamable HTTP transport opens a **GET** SSE stream alongside the POST channel so
the server can push messages to the client. The GET stream is optional in the spec, and
`api.z.ai` does not implement it: once the request is authenticated it reaches the MCP
route, which only handles POST and answers `405`.

Confirmed directly against the endpoint:

| Request                      | Result                                                                          |
| ---------------------------- | ------------------------------------------------------------------------------- |
| `GET` without credentials    | `200` + `{"code":1001,"msg":"Authentication parameter not received in Header"}` |
| `GET` with an invalid token  | `200` + `{"code":401,"msg":"token expired or incorrect"}`                       |
| `GET` with valid credentials | `405 Method Not Allowed`                                                        |

The gateway answers `GET` before routing while unauthenticated; with valid credentials the
request reaches the MCP handler, which only implements POST.

**Impact: none on tool calls.** All client→server traffic — including every tool call —
goes over POST, which the server accepts; the session initialises, tools list, tools run,
and the session terminates cleanly with `DELETE`. What is lost is server→client
notification delivery for such servers (e.g. `notifications/tools/list_changed`).

**This is not a regression from the sync.** The MCP SDK's `handle_get_stream()` behaves
identically in `mcp==1.27.2` (the version the fork used before the sync) and
`mcp==2.3.0`: a failed GET is caught, logged at DEBUG and retried once, then the SDK gives
up. It costs two extra GET requests per session and one INFO log line each. `mcp==2.3.0`
is the latest release, so there is no upstream SDK fix to pull in.

Deliberately **not** worked around: suppressing the GET would disable server→client
notifications for _every_ server, including the ones that do support it. The cost of the
noise is lower than the cost of that.

### 9.3 The API Tools block was dropped by the sync — `utils/middleware.py`

**Introduced by this merge.**

The `utils/middleware.py` conflict was resolved by taking upstream's shape, and the fork's
API Tools block went with it. It sat between upstream's `if api_tools_active:` feature
block and the fork's `tool_ids` block, so nothing marked it as a conflict. Three lines
were lost from `process_chat_payload`:

```python
metadata['api_tools_active'] = api_tools_active
metadata['ask_user_answers'] = form_data.get('ask_user_answers')
use_builtin_tools = (
    (internal_note or bool(metadata.get('session_id')) or api_tools_active)
    and (metadata.get('params', {}).get('function_calling') != 'legacy')
    and bool(model_capabilities.get('builtin_tools', True))
)
```

Every **reader** of those keys survived the merge — the Path C branch in
`non_streaming_chat_response_handler`, the Path C branch in `streaming_chat_response_handler`,
the ask_user answers lookup in `api_tool_stream_wrapper`, and the `answers=` argument in the
tool-call dispatch. Only the **writes** went missing, so:

- `metadata['api_tools_active']` was always falsy → the API Tools path never ran. A
  stateless API call returned the provider's `tool_calls` unexecuted, with no
  `x_open_webui.tool_events`.
- `metadata['ask_user_answers']` was always `None` → ask_user Pattern B (an API caller
  supplying the answers) could never resolve, and the `answers=` argument restored in
  §9.1 was correct but inert.

The block is re-applied verbatim, in the position it held before the sync. The ordering
relative to upstream's early `use_builtin_tools` definition is unchanged: the early uses
(`include_file_context`, the memory branch, `can_author_skills`) keep upstream's value, and
the later uses (the skills block, the tool filter) get the fork's superset — exactly as
before the merge.

**Not restored:** upstream's `b34885efc` deliberately removed the fork's note-files
injection from the same region and replaced it with the `view_note` builtin tool. Re-adding
it would double-inject note content, so it stays out. Flagged for a decision: if you want a
note chat to auto-include the note's attached files again, that needs a deliberate design
call, not a merge resolution.

**Verified against a running container** with a mock OpenAI-compatible provider:

| check                                            | result                                                                                                                                                                           |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API Tools, stateless request, `stream:false`     | tools execute server-side; `x_open_webui.tool_events` carries `tool_call_start`/`tool_call_end` per tool; `tools_used` populated; final message built on the real tool output    |
| ask_user Pattern B (`ask_user_answers` supplied) | the question resolves server-side (`{"status":"ok","answers":{"q1":"Blue"},"responder":"api-supplied"}`) and the resolved answer is fed back to the model on the next round trip |
| UI/streaming tool path                           | unchanged — tools execute, container log free of `pop from empty list`                                                                                                           |

### 9.4 The API Tools loop dropped the model's final text — `utils/middleware.py`

**Pre-existing fork bug** (not merge-introduced), but only reachable once §9.3 restored the
API Tools path.

The tool loop asks the provider for `stream=true` on the follow-up round trip that carries
the tool result back to the model. A provider — or an OpenAI-compatible proxy — may answer
with a plain `chat.completion` body instead. `api_tool_stream_wrapper` then took the
"not a `StreamingResponse`" branch, emitted a synthetic stop chunk and **never read the
body**, so the model's final answer was thrown away: the client got an empty assistant
message with `finish_reason: stop`, even though the tool had run and the provider had
answered with text.

The body is now parsed and the assistant text is emitted as a regular content chunk before
the stop chunk, with the usage carried over when the body has it.

This is tool-agnostic and was never specific to `ask_user`. Measured on the running
container:

| case                                 | before            | after      |
| ------------------------------------ | ----------------- | ---------- |
| `ask_user`, streaming follow-up      | content OK        | content OK |
| `ask_user`, non-streaming follow-up  | content **empty** | content OK |
| normal tool, streaming follow-up     | content OK        | content OK |
| normal tool, non-streaming follow-up | content **empty** | content OK |

The branch is byte-identical in the fork's pre-merge tree
(`8b8d2527d:backend/open_webui/utils/middleware.py:5695-5710`) and came in with the fork's
own `fb4d350ba` ("feat(server): API Tools, response summaries, context pruning"). The
v0.13.2 sync did not touch it.

### 9.5 The tiktoken token estimator was lost — `utils/context_compaction.py`

**Introduced by this merge** — the only genuinely fork-authored loss found by the
provenance sweep.

The sync rewrote `context_compaction.py` on top of upstream's file and re-applied the fork's
features, but the fork's **tiktoken-based estimator** was missed and the merge shipped the
bare `len(text) // 4` heuristic.

The heuristic is not a rough approximation of tiktoken — it is wrong in the direction that
matters:

| content           | `len//4` | tiktoken | error |
| ----------------- | -------: | -------: | ----: |
| English prose     |      225 |      201 |  −11% |
| JSON tool payload |       95 |  **369** |  −74% |
| non-ASCII text    |       40 |  **160** |  −75% |

A tool-heavy chat was counted at roughly a quarter of its real size, so **compaction fired
several turns later than configured** — on exactly the content the fork's compaction work
exists to bound. The module's own docstring still claimed _"falls back to a tiktoken
estimate"_ while the code did no such thing, and `tiktoken==0.13.0` is a hard dependency in
both `requirements.txt` and `requirements-slim.txt`.

Restored verbatim from the fork's pre-merge tree:

- `_get_tiktoken_encoder()` — lazily loads and caches `cl100k_base`, with a char-heuristic
  fallback when tiktoken cannot be loaded;
- the tiktoken branch in `_estimate_tokens()`;
- the **"compaction did not reduce the context"** diagnostic — it reports when a compaction
  cannot shrink the context and will therefore re-trigger every turn at the cost of a
  full-context LLM call each time. That is the exact failure mode the fork's budget
  enforcement targets, and it was silent;
- the "summariser model is unavailable, falling back to the chat model" warning.

`backend/open_webui/test/test_context_compaction.py` pins the estimator: it fails on the
heuristic-only file (5 failures) and passes on this one.

### 9.6 Two cosmetic losses

- **`Models.svelte` — the global-failover wrapper label.** The fork's `modelBaseLabel()`
  helper was lost, so a global-failover wrapper now shows "No description" where it used to
  show "Global failover chain". The backend support is intact (`meta.failover_source` is
  still modelled and still set by the Model Editor) — only the label was lost. Restored, with
  the translation string added to `en-US` (it was never in the catalogue, so the fork was
  showing the raw key).
- **`utils/middleware.py` — a dead import.** Two `from open_webui.utils.payload import ...`
  lines were kept: the fork's, which named `resolve_system_prompt`, and upstream's. Upstream
  removed the `resolve_system_prompt()` call site in its own refactor (`8e2ba2937`), so the
  name was dead. The fork's line is removed; upstream's line is left as the single import.

### 9.7 The failover resolver could not explain itself — `utils/failover.py`

Reported in production as:

```
WARNING | resolve_failover_candidates:377 - wrapper 'minimax.MiniMax-M3.1-Flash'
(failover_source='custom') resolved to 1 candidate(s)
[zai.glm-5.3-flash@https://api.z.ai/api/coding/paas/v4]. Failover will NOT be possible for
this request - check the chain configuration and that chain entries resolve against current
connections (OPENAI_MODELS).
```

"Check the chain configuration" without saying **which** entry vanished or **why** is not
actionable, and one whole failure mode was completely silent: a wrapper whose
`failover_source` is `custom` but whose chain is **empty** fell back to the legacy
single-provider path with the source toggle read and then silently ignored.

Three fixes:

1. **Every dropped chain entry now carries a reason** — unknown model id, an unusable
   `urlIdx`, an index out of range for the snapshot, a connection removed since the snapshot
   was taken, or a connection the request skipped — and the summary warning repeats the
   dropped entries and their reasons.
2. **An empty custom chain now says so explicitly** instead of degrading silently.
3. **Correctness.** A chain entry's `urlIdx` is an index into the connection list the
   `OPENAI_MODELS` snapshot was built from, but the resolver indexed the **current** list with
   it. Removing a connection shifts every later index, so an entry could be dropped — or
   worse, routed to a **different provider with a different API key**. The entry now follows
   its URL to the connection that actually serves it and takes that connection's key and
   config; the snapshot's connection list is recorded alongside the model cache.

That last change also retired a guard that had never done anything:
`cache_entry.get('urlIdx') == idx` compared the snapshot's index with the candidate's own
index, and the candidate's index came from that very entry — so it was always `True`. With the
candidate routed by URL the two indices legitimately differ once the connection list is
edited, so keeping the comparison would have silently dropped the pipeline user-info injection
for a real pipeline.

`backend/open_webui/test/test_failover_resolve.py` — 8 tests; 7 fail on the pre-fix code, the
8th pins that configured order and health ranking must not change.

### 9.8 A provider's JSON error body was discarded because of its Content-Type — `routers/openai.py`

```
ERROR | open_webui.routers.openai:_try_provider_candidate:1917 -
  500, message='Attempt to decode JSON with unexpected mimetype: ', url='...'
```

The provider answered with HTTP 500 and a JSON body but a Content-Type that is not
`application/json` (`api.z.ai` sends none at all). aiohttp's `resp.json()` refuses to decode
anything else and raises `ContentTypeError`, whose `str()` is the odd
`500, message=..., url=...` line above.

The body was recovered as text, but the provider's structured error was never parsed, so the
failure detail reached the client as a JSON string embedded in a string:

```json
{ "detail": "{\"error\": {\"code\": \"1234\", \"message\": \"Internal network failure...\"}}" }
```

and a handled condition was logged at ERROR.

The body is authoritative, not the Content-Type. `_decode_provider_body()` now decodes it as
JSON whenever it parses — whatever the header says — and keeps the raw text only when it
really is not JSON. The mismatched-Content-Type case is logged at DEBUG ("parsed it anyway"),
and a genuinely non-JSON body likewise. The SSE branch uses the same helper.

`RetryableProviderError.detail` now carries the **parsed** body when the provider answered
with JSON and a string otherwise. Which errors are retryable is unchanged.

Verified against a running container with a mock provider that fails the way `api.z.ai` does:

| case                       | before                                                | after                                            |
| -------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| JSON body, no Content-Type | `{"detail":"{\"error\": {...}}"}` + an ERROR log line | `{"detail":{"error":{...}}}` + no ERROR log line |
| HTML body, no Content-Type | `{"detail":"<html>…</html>"}`                         | unchanged                                        |
| healthy provider           | 200                                                   | 200                                              |

`backend/open_webui/test/test_provider_error_body.py` — 15 tests; 14 fail on the pre-fix code.

### 9.9 A transient provider failure killed the tool-call continuation

```
ERROR | open_webui.utils.middleware:response_handler:7644 - Tool-call continuation failed:
  {"error":{"code":"1234","message":"Internal network failure, error id: ... please try again later."}}
  + a 30-line traceback
```

The tool-call continuation is the turn that turns tool results into the model's final
answer. The tool has **already run** by then — only the final answer is missing — yet a
transient provider failure ended the turn outright.

Three fixes:

1. **The continuation is retried** on a transient failure (5xx / 429), up to twice with a
   short backoff. A 4xx is _not_ retried: the request shape or the credentials are wrong and
   the same call would fail identically.
2. **The provider's message is no longer hidden.** The chat UI renders `error.content` as a
   _string_ — the frontend's `getErrorMessage()` takes the first string candidate and falls
   back to a generic "Server connection failed" when it finds none. A provider body that was
   still a dict therefore made the UI show "Server connection failed" instead of the
   provider's actual message. `utils/misc.py:extract_error_message()` now drills down to the
   first useful string (`error` → `message` → `detail`) and is used by **both** places that
   put a provider failure into the chat: the tool-call continuation
   (`utils/middleware.py`) and the top-level chat handler (`main.py`).

   This also repairs a regression from the previous commit: parsing the provider's JSON body
   turned `detail` into a dict, and that dict then reached the chat UI as `error.content`,
   where it matched no string candidate and the provider's message was replaced by
   "Server connection failed".

3. **No traceback for a provider failure.** The provider's message is the information; the
   stack is noise on top of it. An unexpected exception still logs the full traceback.

Verified against a running container with a mock provider that fails the continuation round
once, then recovers:

```
mock:  POST #1 roles=['user']                     -> emitting tool_call
       POST #2 roles=['user','assistant','tool']  -> failing with HTTP 500 (JSON body, no Content-Type)
       POST #3 roles=['user','assistant','tool']  -> replying with text
app:   WARNING | Tool-call continuation hit a transient provider error (HTTP 500);
                retrying in 1.0s (attempt 1 of 2).
       -> the chat completes: "Tool executed successfully after a retry."
```

With the provider failing every time, the turn ends with the readable message and no
traceback:

```
ERROR | Tool-call continuation failed: Internal network failure, error id: ... please try again later.
```

`backend/open_webui/test/test_tool_continuation.py` — 27 tests; 24 fail on the pre-fix code.

## 10. Known follow-ups

- The fork's note-files injection in `process_chat_payload` was **not** restored — upstream
  replaced it with the `view_note` builtin tool (§9.3). Decide whether a note chat should
  auto-include the note's attached files again.
- Upstream's intent on the restored `ast.literal_eval` tool-arg fallback.
- Upstream's ModelEditor / ToolkitEditor deltas still to be ported.
- `mcp==2.3.0` must be installed on the deployment (the merged `backend/requirements.txt` pins it).
- The fork's MCP cleanup filter is gone from `utils/mcp/client.py`; confirm the ASGI-level suppression is sufficient.
