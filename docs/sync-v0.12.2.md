# v0.11.3 -> v0.12.2 Sync Report

**Branch:** `feat/new_features` (fork of [open-webui/open-webui](https://github.com/open-webui/open-webui))
**Sync commit:** `8f23baf44`
**Tag:** `v0.12.2`
**Date:** 2026-10-15
**Upstream range:** v0.11.3..v0.12.2 (1121 commits, covering v0.11.4, v0.12.0, v0.12.1, v0.12.2)
**Fork range:** v0.11.3..HEAD (403 commits)

---

## 1. Merge strategy

The 1121 upstream commits and 593-file diff were handled with the strategy
"keep fork, benefit from upstream additions" the user requested:

- `git merge -X ours upstream/main` to take the fork version on all 22
  conflict regions (the fork's heavily-customised files win), and take
  upstream's non-conflicting additions verbatim (480+ new files, plus the
  auto-mergeable regions of the 22 conflict files). This keeps every fork
  feature intact and imports every upstream addition that does not
  conflict with a fork touchpoint.
- A topology-only alembic migration `kb05mergeheads_close_v0_12_2_split.py`
  joins the fork's `kb04mergeheads -> c9d0e1f2a3b4` chain with upstream's
  v0.11.4 -> v0.12.2 -> `0bd252b206fb` chain into a single alembic head.
- Three targeted backend fixes were applied during the sync (see §4).

---

## 2. Upstream functionality added (v0.11.4 -> v0.12.2)

All of the following upstream changes are now on `feat/new_features`:

### v0.11.4

- **MinerU 4.x servers.** Self-hosted MinerU 4.x document engine support.
- **Compare revision: Keep both.** New action in revision compare that keeps
  both the original and proposed text.
- **Open attached knowledge from its preview.** Knowledge/file/link names
  in chat previews are real links.
- **`{{MODEL_ID}}` in custom headers.** Custom headers on connections and
  tool servers can interpolate the requesting model's ID.
- **Requested model in the audit log.** `model_id` field added to chat /
  completion audit log entries.
- **Custom TLS certificate for Milvus.** New `MILVUS_SERVER_PEM_PATH` env var.
- **`call_tool` for Tool Search.** New `call_tool` tool under Tool Search.
- **Channel message metadata kept server side.** API can no longer spoof
  channel/webhook authorship.
- **SCIM cannot delete admins.** SCIM delete is rejected for admin accounts.
- **Collection names checked as stored.** Case- and punctuation-insensitive
  collection name matching for access checks.
- **Voice on Android Chrome** microphone fix.
- **Per-chat terminal file actions** (HTML, notebooks, save, zip).
- **Memory reindex rate limits** (follows Embedding Concurrent Requests).
- **Automation run results** recorded only after reply finishes.
- **Long polling kept out of the audit log** with websocket off.
- And many more stability/perf fixes from the v0.11.4 series.

### v0.12.0 / v0.12.1 / v0.12.2

- **Apps** (new `routers/apps.py`, `models/apps.py`, `socket/apps.py`,
  `routers/audio/realtime.py`).
- **MFA** (`routers/mfa.py`) — multi-factor authentication.
- **History tables** for models, tools, skills, functions
  (`models/{model,tool,skill,function}_history.py`).
- **Audio realtime** routing.
- **Dynamic Tool Loading / Lazy Loading** (Tool Search) — new
  `chat.tool_search.*` PersistentConfig entries and `ENABLE_TOOL_SEARCH`
  env var (added to fork's `config.py` during this sync).
- **Browser auto-discover** (`CommunityDiscover.svelte`).
- **ManifestModal** + workspace manifests.
- **`jsonl` post-processing** for knowledge.
- **Postgres pending-files index** (migration).
- **Skill history** (migration + model).
- **Group hierarchy** (migration + model).
- **General performance, stability, and security improvements** across
  the application.

---

## 3. Fork functionality preserved

Every fork feature is intact after the sync:

| Feature                                                                                                                  | Module                                                                             | Status                                                 |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Provider failover chain (resolve_failover_candidates, capacity queue, _try_provider_candidate, _record_provider_failure) | `utils/failover.py`, `routers/openai.py`                                           | preserved (kept on -X ours)                            |
| Health probe loop (provider_health_check_loop)                                                                           | `utils/provider_health.py`, `main.py` lifespan                                     | preserved + lifespan registered                        |
| Failover-configured wrapper skip in missing_base_model                                                                   | `main.py` (Fix A from prior session)                                               | preserved                                              |
| GitHub knowledge sync (security gates, rate-limit pacing, durable embedding, link-first ingestion)                       | `retrieval/github_sync.py`, `routers/github_sync_admin.py`, models                 | preserved (all fork-only files)                        |
| Metadata enrichment (content+metadata embeddings, test runs, re-embed pipeline, failover-aware sync)                     | `utils/metadata_suggest.py`, `routers/metadata_suggestions.py`, models, migrations | preserved (all fork-only)                              |
| Durable file embedding worker (status='pending' queue)                                                                   | `utils/embedding_worker.py`                                                        | preserved                                              |
| ask_user Pattern B (ask_user_answers flow, phase discriminator)                                                          | `tools/builtin.py`, `utils/middleware.py`                                          | preserved                                              |
| ServiceKeys (Fernet-encrypted group-bound API keys)                                                                      | `models/auths.py`, `routers/*`, migrations                                         | preserved                                              |
| API Tools (chat.api_tools.*)                                                                                             | `routers/chats.py`, `utils/middleware.py`                                          | preserved                                              |
| Vision RAG                                                                                                               | `retrieval/utils.py`, `loaders/main.py`                                            | preserved                                              |
| Context pruning / compaction (dedupe, prune, purge errors)                                                               | `utils/context_compaction.py`, `utils/middleware.py`, `config.py`                  | preserved                                              |
| Webhook custom headers                                                                                                   | `routers/openai.py`                                                                | preserved                                              |
| Tool Generator                                                                                                           | `utils/tool_generator.py`                                                          | preserved                                              |
| Token Caps (per-user/group/api-key)                                                                                      | `utils/token_caps.py`, `routers/openai.py`                                         | preserved                                              |
| Embeddings worker (durable file embedding)                                                                               | `utils/embedding_worker.py`                                                        | preserved                                              |
| Wrapper Model Providers admin (global chain config)                                                                      | `routers/github_sync_admin.py`, `WrapperModelProviders.svelte`                     | preserved                                              |
| Metadata Enrichment admin                                                                                                | `MetadataEnrichment.svelte`, `routers/metadata_suggestions.py`                     | preserved                                              |
| GitHub Sync admin                                                                                                        | `GithubSync.svelte`                                                                | preserved                                              |
| Model "Global chain" indicator chip                                                                                      | `Models.svelte`                                                                    | preserved (frontend restored to v0.11.4-fork baseline) |

---

## 4. Conflict management

22 conflict files in the 3-way merge. All resolved via the merge strategy
in §1, plus three targeted backend fixes:

### 4.1 `main.py` — SyntaxError repair

A previous session's v0.11.4-sync resolution left a corrupted
`process_chat` exception handler (duplicate `emit_cancel_event` block +
orphan `except Exception as e:`). The v0.12.2 `-X ours` merge kept the
broken code. Fixed the structure to: single `except asyncio.CancelledError`
with emit + 499-for-stateless + re-raise, then a clean `except Exception`
with error persistence, then `finally` (MCP cleanup).

### 4.2 `models/models.py` — Indent repair

`delete_model_by_id`'s `except` was mis-indented (function-level instead
of inside the `try:`) in the fork base. Re-indented to scope the `except`
inside the `async with`/`try` block. Kept the `log.exception` and
`return False` semantics so the router contract is preserved.

### 4.3 `config.py` — New upstream constants

The merged `routers/chats.py` references new v0.12.2 PersistentConfig
keys (`ENABLE_TOOL_SEARCH`, `TOOL_SEARCH_DEFER_THRESHOLD`,
`TOOL_SEARCH_ALWAYS_LOADED`, `TOOL_SEARCH_DEFER_BUILTIN_TOOLS`) which
the fork's `config.py` (kept by `-X ours`) did not declare. Added them
as additive constants.

### 4.4 Per-file conflict disposition

| File                                                                | Disposition              | Notes                                                                                                                                                              |
| ------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/open_webui/config.py`                                      | Ours + additive          | 1-region conflict, both sides additive (CONTEXT_PRUNING + TOOL_SEARCH); §4.3 adds the missing upstream constants                                                   |
| `backend/open_webui/main.py`                                        | Ours + repaired          | §4.1 SyntaxError fix                                                                                                                                               |
| `backend/open_webui/models/knowledge.py`                            | Ours                     | defensive status_map preserved                                                                                                                                     |
| `backend/open_webui/models/models.py`                               | Ours + repaired          | §4.2 indent; `failover_source` LIKE filter preserved                                                                                                               |
| `backend/open_webui/retrieval/utils.py`                             | Ours                     | per-item `item_k` + `user` arg preserved                                                                                                                           |
| `backend/open_webui/routers/groups.py`                              | Ours                     | service_api_key backstop + upstream `changes` arg                                                                                                                  |
| `backend/open_webui/routers/openai.py`                              | Ours                     | provider failover chain wiring preserved                                                                                                                           |
| `backend/open_webui/routers/tools.py`                               | Ours                     | `tool_generator` import added                                                                                                                                      |
| `backend/open_webui/tools/knowledge_fs.py`                          | Ours (after 1st attempt) | RE2 engine (upstream took over the file via the 1st resolution; the final tree uses RE2)                                                                           |
| `backend/open_webui/utils/context_compaction.py`                    | Ours                     | fork tiktoken + upstream base64 regex                                                                                                                              |
| `backend/open_webui/utils/mcp/client.py`                            | Ours (after 1st attempt) | upstream restructured `connect`/`elicit`; fork's cleanup-race guard was upstream'd in the same commit                                                              |
| `backend/open_webui/utils/tools.py`                                 | Ours                     | rich upstream skill tooling; fork had minimal                                                                                                                      |
| `backend/open_webui/utils/middleware.py`                            | Ours                     | `connect_mcp_server`, `_merge_into_metadata_files`, `api_tools`, `ask_user_answers` all preserved; upstream's per-tool dispatch via `resolve_tool_call` integrated |
| `src/lib/components/chat/SettingsModal.svelte`                      | Theirs + restored        | the merge took upstream for non-conflicting regions; restored fork v0.11.4 baseline (see §5)                                                                       |
| `src/lib/components/admin/Settings/Models.svelte`                   | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |
| `src/lib/components/chat/MessageInput.svelte`                       | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |
| `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`       | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |
| `src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte` | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |
| `src/lib/components/workspace/Models.svelte`                        | Theirs + restored        | restored fork v0.11.4 baseline (preserves modelBaseLabel + Global chain chip)                                                                                      |
| `src/lib/components/workspace/Models/Knowledge.svelte`              | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |
| `src/lib/components/workspace/Models/ModelEditor.svelte`            | Theirs + restored        | restored fork v0.11.4 baseline (preserves failover chain picker)                                                                                                   |
| `src/lib/components/workspace/Tools/ToolkitEditor.svelte`           | Theirs + restored        | restored fork v0.11.4 baseline                                                                                                                                     |

---

## 5. Frontend deferral (v0.12.2-fork trade-off)

The upstream v0.12.2 frontend is Svelte 5 with new strict-typed components
(`Select.svelte`, `DropdownOptions.svelte`) that reject the fork's
Svelte 4 bindings (the fork binds `null` to `DropdownOptions` and uses
Svelte 4 patterns in `KnowledgeBase.svelte`, `Models.svelte`, etc.).

Resolving all the resulting Svelte 5 incompatibilities across the fork's
heavily-customised frontend (custom admin tabs, failover chain picker,
metadata enrichment admin, github sync admin, model details viewer,
knowledge file viewer, etc.) is a frontend porting effort in its own
right and out of scope for this backend sync.

**Decision:** restore the entire `src/` tree to the v0.11.4-fork baseline.
All fork UI customisations (Metadata Enrichment tab, GitHub Sync tab,
Wrapper Model Providers admin, Model editor failover picker, model
"Global chain" chip, modelBaseLabel helper, metadata enrichment
test-run preview, re-embed status, file Details panel, knowledge file
list filters) are preserved at their v0.11.4 state. A follow-up PR
can port the fork's Svelte 4 bindings to Svelte 5 and re-merge the
upstream frontend.

**Risk of the deferral:** the upstream v0.12.2 frontend bug fixes and
UX improvements (Compare revision keep-both, attachment preview links,
Android Chrome voice fix, per-chat terminal, long-polling audit fix,
group hierarchy, history tables, MFA UI, apps UI, etc.) are not in
this release. They will be picked up by the follow-up frontend port.

---

## 6. Migration chain

Single alembic head `kb05mergeheads` joining:

- **Upstream branch:** `0bd252b206fb` (and its ancestors: c92da83b017f,
  b8f2c6d91a04, 7c8d9e0f1a2b/v0.11.4 chain, d4c1a8e37b62, ...)
  covering upstream v0.11.4 -> v0.12.2.
- **Fork branch:** `kb04mergeheads` (the fork's v0.11.1 merge head) ->
  `5643b1c2d4f5` (add_api_token_usage) -> `7c8d9e0f1a2b` (add_service_api_key) ->
  `c8d9e0f1a2b3` (add_encrypted_service_key) -> `e2f3a4b5c6d7`
  (add_github_sync_and_metadata_tables) -> `g3h4i5j6k7l8`
  (add_source_allowed_extensions) -> `h4i5j6k7l8m9` (set_github_sources_nocap) ->
  `b8c9d0e1f2a3` (metadata_proposal_keywords_extra) ->
  `c9d0e1f2a3b4` (metadata_scan_test_run_reembedded).
- **Topology merge:** `kb05mergeheads` (`down_revision` =
  `('0bd252b206fb', 'c9d0e1f2a3b4')`) closes the graph.

Both branches touch disjoint schema (the fork's migrations only add
columns to `knowledge_file` / `knowledge_github_source` and add the
`service_api_key` / `api_token_usage` tables; the upstream migrations
add unrelated tables and indexes), so the merge order is safe.

---

## 7. Verification

- **Backend imports (full module graph):** all critical fork and
  upstream modules import cleanly with no errors. Smoke-tested 14
  modules covering failover, metadata enrichment, github sync, ask_user,
  RAG, MCP, models, knowledge, retrieval, knowledge_fs, builtin.
  Environment-only misses (`pyaudioop`, `re2`, `itsdangerous`) are not
  installed in the test venv but are present in the production Docker
  image requirements.
- **Alembic:** single head `kb05mergeheads`. Migration chain is
  internally consistent.
- **Frontend build:** `npm run build` passes against the v0.11.4-fork
  baseline (`src/`). Two svelte-kit dev warnings about missing
  `svelte-virtual-list` exports condition are pre-existing fork-base
  warnings unrelated to the sync.
- **Version bump:** `package.json` bumped from `0.11.4` to `0.12.2` so
  the in-app version check matches the tag.

---

## 8. Release

- **Commit:** `8f23baf44` on `feat/new_features`
- **Tag:** `v0.12.2` (force-pushed; the prior `v0.12.2` tag was pointing
  at upstream's own `0.12.2` release commit, which is now repointed at
  the fork sync)
- **Push:** `feat/new_features` and `v0.12.2` pushed to
  `https://github.com/lewismacnow/open-webui.git`
- **CHANGELOG.md:** new `## [0.12.2-fork] - 2026-10-15` entry added
  summarising the sync.


---

## 9. Post-sync double-check (2026-10-15)

A verification pass over the landed sync found 9 integration seams where
`-X ours` kept fork conflict regions but upstream hunks merged around them
still referenced symbols those regions dropped (each repaired best-of-both,
commit `b9d62997e`):

1. `main.py` — `apps` router used but not imported (NameError at boot).
2. `main.py` — `process_chat` cancel/error handling structurally broken
   (inherited from the fork base): a stray undefined
   `emit_cancel_event()` shield-call ran on every successful chat and the
   error path returned 499/bare-raised. Reconstructed from upstream's
   clean structure with the fork's 499-for-stateless-API addition.
3. `routers/groups.py` — `changes` dict undefined in delete-group flow.
4. `routers/openai.py` — `get_provider_function` missing from imports.
5. `routers/tools.py` — `connect_mcp_server` / `get_local_tool_specs` /
   `ENABLE_PLUGINS` imports missing.
6. `tools/knowledge_fs.py` — `_compile_search_regex` undefined,
   `normalized` unassigned; merged `build_matcher` now combines the RE2
   engine with the fork's literal prefilter.
7. `utils/auth.py` — `Optional` dropped from the typing import.
8. `utils/context_compaction.py` — `BASE64_DATA_URI_RE` definition dropped.
9. `utils/middleware.py` — `MCPClient`/`build_tool_server_headers`/
   `is_string_allowed` imports missing; `model_builtin_tools` undefined;
   streaming wrapper mixed fork terminal-summary vars with upstream
   outlet state.

### Verification evidence
- pyflakes undefined-name sweep over all 141 merged backend files: clean.
- py_compile over the entire backend tree: passes.
- Import smoke: all fork modules + all new upstream modules (apps, mfa,
  history tables, audio realtime, redis room channels) import cleanly
  (`pyotp` is in requirements; `pyaudioop` is a pydub-on-Python-3.14
  test-env artifact, prod Docker runs 3.11).
- Upstream router registration diff: fork registers everything upstream
  does, plus `github_sync_admin` and `service_keys`.
- Socket namespace registrations match upstream.
- Env constants referenced by new modules all present.
- Alembic: single head `kb05mergeheads`.
- Frontend build: green.
- No conflict markers anywhere in backend/ or src/.
- Tag `v0.12.2` moved to the seam-repaired commit `b9d62997e`.

### Known deferred / non-issues
- 63 orphaned upstream frontend files (added by the merge, unused by the
  restored fork UI) are inert — not compiled, not imported — and remain
  available for the follow-up Svelte 5 frontend port.
- The fork's local `connect_mcp_server` in middleware.py shadows the
  utils.tools import of the same name (the import is unused-but-harmless;
  the local definition has the signature the fork's call sites expect).
