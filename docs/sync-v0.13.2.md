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

## 8. Known follow-ups

- Re-check upstream's intent on the two restored blocks (note-files injection, `ast` fallback).
- Upstream's ModelEditor / ToolkitEditor deltas still to be ported.
- `mcp==2.3.0` must be installed on the deployment (the merged `backend/requirements.txt` pins it).
- The fork's MCP cleanup filter is gone from `utils/mcp/client.py`; confirm the ASGI-level suppression is sufficient.
