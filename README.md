<h2>Open WebUI</h2>

<p>Open WebUI is <a href="https://docs.openwebui.com/"><strong>a home for AI</strong></a>: an extensible, easy-to-use, self-hosted platform built to run entirely offline. Connect local models through Ollama or use OpenAI-compatible APIs for local and cloud models.</p>

[English](README.md) |
[简体中文](README.zh.md) |
[繁體中文](README.zht.md) |
[한국어](README.ko.md) |
[Deutsch](README.de.md) |
[Español](README.es.md) |
[Français](README.fr.md) |
[Italiano](README.it.md) |
[Dansk](README.da.md) |
[日本語](README.ja.md) |
[Polski](README.pl.md) |
[Русский](README.ru.md) |
[Bosanski](README.bs.md) |
[العربية](README.ar.md) |
[Norsk](README.no.md) |
[Português (Brasil)](README.br.md) |
[ไทย](README.th.md) |
[Türkçe](README.tr.md) |
[Українська](README.uk.md) |
[বাংলা](README.bn.md) |
[Ελληνικά](README.gr.md) |
[Tiếng Việt](README.vi.md)

[![Open WebUI Demo](https://cdn.openwebui.com/open-webui-demo.gif)](https://docs.openwebui.com/)

> **Forked from [https://github.com/open-webui/open-webui](https://github.com/open-webui/open-webui), with the following tweaks and enhancements:**
>
> **Latest sync:** upstream **v0.11.4** (327 commits, 466 files). All fork customisations preserved through the merge; the notable upstream changes adopted include the **RE2 regex engine** for knowledge grep (linear-time, bounded-memory — compounds with the fork's literal prefilter and admin-configurable match budget), **crash-safe streaming** (error frame + terminal `[DONE]` on mid-stream failures), the **per-worker shared-model-pool cache** and **single-message chat reads/writes** perf work, **ask_user "Recommended" option labelling**, **SVG-as-document uploads**, **memory off-switch enforcement**, **skills loaded for builtin-tools callers**, and the admin-settings i18n/grouping refresh. Conflict highlights: the fork's API Tools gate + ask_user wrapper staging, durable embedding worker, failover queue, and Path B `rag_sources` summary all survived intact (see the merge commit `5a1ab4db1` for the full resolution log). Previous sync: v0.11.3 (commit `a7ea89879`).
>
> This is a customised fork. Upstream is treated as a source of enhancements that we periodically merge in; our own changes are maintained on top. The high-level deviations from upstream are:
>
> ### Model resilience & tooling
>
> | Area                            | Tweak / Enhancement                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Why                                                                                                                                                                                                                                                                                                                               |
> | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **Model resilience**            | LLM provider **failover** — an ordered list of OpenAI-compatible providers per model (incl. base-model failover), with provider health tracking and streaming failover (`utils/failover.py`, `utils/provider_health.py`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | A single provider outage no longer breaks chat; requests transparently fall through to the next healthy provider.                                                                                                                                                                                                                 |
> | **Provider in-flight tracking** | Redis-backed per-provider in-flight request counters with in-memory fallback. Atomic `INCR`/`DECR` per request, used by the failover resolver and the capacity queue to decide "does this provider have a free slot?" (`utils/provider_inflight.py`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Foundation infrastructure for the capacity queue and the chain resolver. Without this, neither feature could detect saturation cleanly.                                                                                                                                                                                           |
> | **Failover capacity queue**     | **Redis-shared capacity queue** — when **every** provider in a chain is at `max_concurrent`, chat completions park in a shared FIFO and poll for a free slot every `poll_interval_seconds`. On overflow → 429 with `full_message`. Background tasks (title/tags/emoji/MoA/tool-calls) are exempt so a tool-using chat can't deadlock against its own serial reservations. Admin → Settings → AI → Failover Capacity Queue exposes `max_queue_length`, `poll_interval_seconds`, `full_message` (`utils/failover.py`, `routers/openai.py`, `Settings/FailoverQueue.svelte`).                                                                                                                                                                                                                                                                                                                                                                                                                                          | Prevents the 429-storm when capacity runs out: instead of every request firing into a saturated provider and walking a 3-provider retry loop, queued requests wait cooperatively across all workers.                                                                                                                              |
> | **External API tools**          | **API Tools** — for models with the `api_tools` capability, server-side tool execution is enabled for API callers (no browser socket). Path-C streaming handler runs the tool loop, attaches custom `x_open_webui.tool_event` / `x_open_webui.sources` / `x_open_webui.tools_used` fields to the OpenAI-format response, and auto-resolves `model.meta.toolIds` from the request. Privacy-bounded: only allowlisted builtin tools (`time`, `knowledge`, `web_search`, `ask_user` via the admin allowlist) are exposed. **`ask_user` for API callers** — accept `ask_user_answers: {question_id: option_label}` in the chat-completion request body. When the model emits `ask_user`, Open WebUI validates coverage and immediately emits a `function_call_output` with the resolved answers (no `pending` stage, no downstream socket dependency). If any question_id is missing, Open WebUI writes a structured `missing_answers` error to the function_call_output so the API caller can retry with the full set. | Lets external clients trigger the same builtin tools the chat UI can — web search, knowledge queries, clarifications via `ask_user` — without a browser session. The `ask_user_answers` mechanism means third-party API callers can use `ask_user` end-to-end over `POST /api/v1/chat/completions` without a downstream human UI. |
> | **API Tools Policy**            | **Admin → Settings → API Tools** — per-category builtin tool allowlist + master switch + external tool-server toggle, all config-driven (`chat.api_tools.allowed_categories`, `chat.api_tools.allow_tool_servers`) and editable in the UI.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Admins can grant/revoke specific builtin tools per API deployment without code changes.                                                                                                                                                                                                                                           |
>
> ### Hybrid API gateway
>
> | Area                             | Tweak / Enhancement                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Why                                                                                                                                                                                                                                                                                                                                                                 |
> | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **Service API keys**             | **Group-bound service keys** — admin-mintable long-lived keys for third-party integrations, NOT tied to a user account. `sk_live_<22-char-base62>` (32 chars; plaintext also recoverable later via `POST /api/v1/service-keys/{id}/reveal`). Optional expiry (`NULL` = infinite), IP whitelist (CIDR; respects `X-Forwarded-For` / `X-Real-IP` behind a reverse proxy), soft-revoke with `revoked_at`. The plaintext is **Fernet-encrypted at rest** in a new `encrypted_key` column, keyed off `WEBUI_SECRET_KEY` (same derivation as `utils/valves.py`); rotating `WEBUI_SECRET_KEY` invalidates every stored ciphertext — the reveal endpoint surfaces this as a 500 with a clear "re-mint to continue" message. **Four webhook events:** `service_key.minted`, `service_key.revoked`, `service_key.expired`, `service_key.revealed` (audit-only, never carries the plaintext). ON DELETE RESTRICT on the group FK — an admin must revoke active keys before deleting the group. Admin UI: **Settings → AI → Service Keys** with a Reveal button + auto-close modal (`models/service_api_key.py`, `routers/service_keys.py`, `Settings/ServiceKeys.svelte`). | Lets Open WebUI operate as a true hybrid gateway: external callers authenticate against the API without a user account, get the bound group's permissions, hit the bound group's token cap, and trigger webhooks on lifecycle events. The reveal flow gives admins a way to recover a lost plaintext (audit trail intact) without forcing a re-mint + redistribute. |
> | **Token caps & usage analytics** | **Per-target token caps** (user / group / model / api_key / service_key) with four rolling windows (hourly / daily / weekly / monthly), backed by Redis with in-memory fallback. Admin UI: **Settings → AI → Token Caps** with search-by-name picker (replaces native `<datalist>`). Caps for service keys are **group-bound**: a group's cap covers ALL its keys collectively (no cap → unlimited). **API token usage analytics** records per-call consumption in the `api_token_usage` table (with `service_key_id` attribution, `trace_id` for failover grouping) and surfaces it via `get_by_user` / `get_by_api_key` / `get_by_service_key` / `get_top_api_keys` / `get_top_service_keys`. Pre-flight token counter uses tiktoken (`utils/token_recorder.py`) for accuracy (`utils/token_caps.py`, `models/api_token_usage.py`, `Settings/TokenCaps.svelte`).                                                                                                                                                                                                                                                                                              | Admins enforce spend limits per user / group / model without code changes, and see exactly where tokens are going — user vs. service-key traffic, per-model breakdowns, top consumers — directly in Admin → Analytics.                                                                                                                                              |
>
> ### RAG & retrieval
>
> | Area                           | Tweak / Enhancement                                                                                                                                                                                                                                                                                                                                                                                                                                                | Why                                                                                                                                                                                 |
> | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **RAG tuning**                 | **Per-model and per-knowledge-base "Document Snippets Returned" (top-k) override.**                                                                                                                                                                                                                                                                                                                                                                                | Attaching several knowledge bases to a model otherwise returns `top_k × N` snippets and blows the context window; per-KB caps let us fine-tune retrieval (e.g. 5 from A, 2 from B). |
> | **RAG relevance threshold**    | Configurable **relevance threshold** filter per knowledge base (admin → Documents).                                                                                                                                                                                                                                                                                                                                                                                | Soft filter — scores below the threshold are dropped, but `top_k` is NOT shrunk, so a low-quality KB can't starve the others of `top_k` slots.                                      |
> | **Vision Image RAG**           | **Vision Support Model** — when a non-vision model receives images, a vision-capable support model (`rag.vision.support_model`) is called server-side to describe each image (concise visual description + OCR + context), then the description is injected into the prompt with an explicit "[VISION SUPPORT]…treat this as if you can see the image" framing. Configurable system prompt (`rag.vision.system_prompt`) lets admins tune the description behavior. | Makes vision-capable agents usable for non-vision models without changing the user-facing model selection.                                                                          |
> | **Vision description caching** | **Three-layer description cache** (in-memory LRU → Redis → message-level `vision_context` annotation on the DB message). Same image hashed + same user-context = cache hit, no re-describe.                                                                                                                                                                                                                                                                        | A 50-turn conversation with the same screenshots no longer re-pays the vision-model cost or latency on every turn.                                                                  |
> | **Context compaction**         | **Summarization-based compaction** — when message tokens exceed `chat.context_compaction.token_threshold`, older messages are summarized by a configurable model (`chat.context_compaction.model`) and the summary is persisted as `contextSummary` on the DB message. Customizable retention percentage and prompt template (`chat.context_compaction.prompt_template`).                                                                                          | Models with long-burning sessions don't run out of context window.                                                                                                                  |
> | **Context pruning**            | **Lightweight deterministic pruning** — three pre-compaction strategies: tool output replacement (keep N most recent, replace older with placeholders), identical consecutive tool-call dedup, and errored tool-call purge (after N turns). Configurable via `chat.context_pruning.*`.                                                                                                                                                                             | Token waste from old tool outputs and duplicate error messages is removed before the more expensive LLM summarization runs.                                                         |
>
> ### Knowledge ingestion & UX
>
> | Area                    | Tweak / Enhancement                                                                                                                                                                                                           | Why                                                                                                                                                 |
> | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
> | **Knowledge ingestion** | **Async, durable embedding pipeline** — files upload first, then a background worker embeds them, tracking per-file status (`pending`/`processing`/`completed`/`failed`) on the `knowledge_file` link. Resumes after restart. | Large imports (10k+ files) no longer require keeping a browser session open, and progress survives interruptions/restarts instead of starting over. |
> | **Knowledge ingestion** | **Stuck-file recovery** — extraction orphans (interrupted uploads) are re-driven automatically by the worker and via a manual "Recover" action.                                                                               | Files left mid-extraction by a crash/restart used to spin forever; they now self-heal.                                                              |
> | **Knowledge ingestion** | **Duplicate rejection by content hash** before vectorising.                                                                                                                                                                   | Avoids wasted storage, duplicate embeddings, and phantom entries from re-uploading the same document.                                               |
> | **Knowledge UX**        | **Per-file embedding status badges, embedding progress banner, retry, and bounded-concurrency (parallel) bulk/directory upload.**                                                                                             | Makes the success/failure of every file reviewable and speeds up large directory imports.                                                           |
> | **Knowledge sync**      | **Incremental directory sync** (checksum diff add/modify/delete) endpoints.                                                                                                                                                   | Re-syncing a source folder only uploads what actually changed.                                                                                      |
> | **Performance**         | **Paginated Files API** and a **`metadata_only` knowledge listing** view (defers the heavy `data` column).                                                                                                                    | Knowledge bases with tens of thousands of files load without timing out or exhausting memory.                                                       |
>
> ### Tool editor
>
> | Area                       | Tweak / Enhancement                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Why                                                                                                                                         |
> | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
> | **AI tool generator**      | **"Edit with Model" button** in the tool editor opens a popup where a user describes a tool in plain language; a selected model generates the full Python tool (`class Tools:`, typed methods, docstrings, optional `class Valves`). The backend validates the generated code via `load_tool_module_by_id` + `get_tool_specs` and self-improvement-loops up to 3 attempts on validation failure. System prompt includes the existing workspace's tool specs so the model can compose rather than duplicate.            | Lets non-developers create tools without writing Python; enables rapid iteration for power users.                                           |
> | **Tools Config admin UI**  | **Admin → Settings → Tools Config** — runtime budgets editable in the UI: regex match time (default 5.0s; was hardcoded 2.0), KB scan / grep output caps, view-file page size, and max regex quantifier counts. The KB relevance threshold UI lives in Documents. All changes apply without a restart (live cache). Matching runs on upstream's **RE2 engine** (linear-time, bounded memory) fronted by the fork's **literal prefilter** — lines that lack a branch's required literals never reach the engine at all. | Operators tune tool runtime and KB defaults without redeploying.                                                                            |
> | **Webhook custom headers** | **Optional custom HTTP headers** on event webhook config (e.g. `Authorization: Bearer <token>`). Editable key-value pairs in the webhook modal; stored on the webhook config and added to the downstream `session.post()` request.                                                                                                                                                                                                                                                                                     | External automation tools (n8n, Make, Zapier, custom bridges) that require API keys or signature headers can now receive webhooks reliably. |
>
> ### Ops
>
> | Area    | Tweak / Enhancement                               | Why                                      |
> | ------- | ------------------------------------------------- | ---------------------------------------- |
> | **Ops** | **Dockerfile fix and pinned `requirements.txt`.** | Reproducible builds and a working image. |
>
> _For the authoritative, commit-level diff: `git diff upstream/main...HEAD`._

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=Support&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/open-webui)

[Explore Open WebUI →](https://docs.openwebui.com/)

### Installation

#### Python

```bash
# With uv installed (macOS/Linux)
DATA_DIR=~/.open-webui uvx --python 3.12 open-webui@latest serve

# Or install and run with pip (Python 3.12)
pip install open-webui
open-webui serve
```

Then open [http://localhost:8080](http://localhost:8080).

#### Docker

```bash
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
```

Then open [http://localhost:3000](http://localhost:3000).

| Image tag | Includes                                                                                 |
| --------- | ---------------------------------------------------------------------------------------- |
| `:main`   | Standard image with local AI dependencies                                                |
| `:slim`   | Lightweight image using external services for embeddings, document extraction, and voice |
| `:cuda`   | Open WebUI with CUDA acceleration                                                        |
| `:ollama` | Open WebUI bundled with Ollama                                                           |
| `:dev`    | Latest development build                                                                 |

Add these options to `docker run` before the image name as needed:

| Option                                               | Purpose                                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `-e OLLAMA_BASE_URL=http://your-ollama-server:11434` | Connect to Ollama on another server                                                      |
| `-e OPENAI_API_KEY=your_secret_key`                  | Set your OpenAI API key                                                                  |
| `-e HF_HUB_OFFLINE=1`                                | Disable Hugging Face model downloads for offline use                                     |
| `--gpus all`                                         | Enable Nvidia GPU access for `:cuda` or `:ollama`; requires the Nvidia Container Toolkit |

See the [Docker installation guide](https://docs.openwebui.com/getting-started/quick-start/) for setup instructions and configuration options.

#### Desktop App

[Download the native desktop app](https://github.com/open-webui/desktop) for macOS, Windows, and Linux.

[Read the docs →](https://docs.openwebui.com/getting-started/)

### Ecosystem

- [Open Terminal](https://github.com/open-webui/open-terminal) / [Terminals (Enterprise)](https://github.com/open-webui/terminals): The action layer that turns Open WebUI into a full agent harness, with enterprise orchestration for per-user workspaces.
- [Knowledge Base Sync (oikb)](https://github.com/open-webui/oikb): Mirror folders, repos, buckets, and wikis into Open WebUI Knowledge Bases with incremental sync.
- [Open WebUI Computer](https://github.com/open-webui/computer): Your computer in any browser: files, editor, terminal, git, and optional AI agents, running on your real machine.

See the [ecosystem documentation](https://docs.openwebui.com/ecosystem/) for details.

### Troubleshooting and Updates

See the [troubleshooting guide](https://docs.openwebui.com/troubleshooting/) for connection issues and the [updating guide](https://docs.openwebui.com/getting-started/updating) to update your installation.

### Documentation

See the [docs](https://docs.openwebui.com/) for configuration and usage, and the [roadmap](https://docs.openwebui.com/roadmap/) for planned work.

For Docker Compose, Kubernetes, and other setups, see the [installation docs](https://docs.openwebui.com/getting-started/).

### Support

[Open an issue](https://github.com/open-webui/open-webui/issues) or [start a discussion](https://github.com/open-webui/open-webui/discussions).

Find us on [Reddit](https://www.reddit.com/r/OpenWebUI/), [X](https://x.com/OpenWebUI), and [Discord](https://discord.gg/5rJgQTnV4s).

### Security

Report vulnerabilities privately through [GitHub security advisories](https://github.com/open-webui/open-webui/security). Security reports are accepted only through GitHub.

### Star History

<a href="https://star-history.com/#open-webui/open-webui&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
  </picture>
</a>

#### License

Includes code under the Open WebUI License, which requires preserving the Open WebUI branding, and prior contributions under their original licenses. See [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY) for terms and history.

[Enterprise](https://docs.openwebui.com/enterprise) · [Careers](https://careers.openwebui.com/) · [Sponsor](https://github.com/sponsors/open-webui)

Created by [Timothy Jaeryang Baek](https://github.com/tjbck).
