"""LLM-driven metadata improvement pipeline.

One background task per scan: iterate the KB's files, ask the trusted
model for improved {title, description, summary, tags}, REDACT PII
(replace with ``[pii-redacted]`` — never reject), normalise + clamp the
output, and store a *proposal*. Proposals are inert until an admin
applies them; the model never mutates a ``file`` row directly.

Parallelism is bounded by a semaphore whose size is
``min(scan.max_parallel, knowledge.metadata.max_parallel config)`` —
server-side default 1 so a locally-hosted single-GPU model is never hit
with concurrent inference unless explicitly raised.
"""

import asyncio
import json
import logging
import re
from types import SimpleNamespace
import time
from typing import Any, Optional

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.metadata_proposal import (
    METADATA_ATTRIBUTES,
    MetadataProposals,
    MetadataScans,
    MetadataScanModel,
)
from open_webui.utils.chat import generate_chat_completion
from open_webui.retrieval.utils import build_metadata_header

log = logging.getLogger(__name__)

# How much of each document is fed to the model. Enough for metadata;
# keeps cost and prompt-injection surface small.
CONTENT_EXCERPT_CHARS = 6000
# How much of the previous body is kept for the test-run preview.
CONTENT_PREVIEW_CHARS = 2000
# A rewritten body shorter than this fraction of the original is treated as
# a model truncation (not an improvement) and discarded.
MIN_CONTENT_RATIO = 0.4
# Below this many characters of non-link text a document is considered
# low value (empty, or nothing but links to other sites).
LOW_VALUE_MIN_TEXT = 40
TITLE_MIN, TITLE_MAX = 5, 80
DESCRIPTION_MAX = 280
SUMMARY_MAX = 600
TAGS_MAX_COUNT, TAGS_MAX_LEN = 8, 32

# ── PII / secret patterns — matched in PROPOSED values only ──────────────

PII_REDACTED = '[pii-redacted]'

_PII_PATTERNS = [
    # API keys / bearer tokens
    re.compile(
        r'\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b'
    ),
    # JWTs
    re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b'),
    # Email addresses
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    # Long hex / base64 blobs (high-entropy secrets) — 40+ chars
    re.compile(r'\b(?:[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{40,}={0,2})\b'),
    # Phone numbers (loose international)
    re.compile(r'\+?\d[\d\s().-]{7,}\d'),
    # SSN / credit-card-shaped
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    re.compile(r'\b(?:\d[ -]?){13,16}\b'),
]


def redact_pii(text: str) -> tuple[str, int]:
    """Replace every PII/secret match with ``[pii-redacted]``.

    Returns (redacted_text, match_count). Used on every attribute the
    model proposes before the proposal is persisted — redaction, never
    rejection, per the admin's preference.
    """
    if not text:
        return text, 0
    count = 0
    for pattern in _PII_PATTERNS:
        text, n = pattern.subn(PII_REDACTED, text)
        count += n
    return text, count


# ── Normalisation (soft — fix shapes instead of rejecting) ───────────────


def normalize_title(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    t = ' '.join(raw.split())
    if len(t) < TITLE_MIN:
        return None
    return t[:TITLE_MAX]


def normalize_text(raw: Any, max_len: int) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    t = ' '.join(raw.split())
    if not t:
        return None
    return t[:max_len]


DOC_TYPES = ('reference', 'how-to', 'troubleshooting', 'concept', 'release-notes', 'api', 'overview')
AUDIENCES = ('admin', 'developer', 'itom', 'end-user', 'all')


def normalize_keywords(raw):
    """Fine-grained search terms (product names, error codes, feature ids).
    Higher ceiling than tags because these feed BM25 retrieval."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, str):
            continue
        slug = re.sub(r'[^a-z0-9\-]+', '-', item.lower()).strip('-')
        if 2 <= len(slug) <= 48 and slug not in out:
            out.append(slug)
        if len(out) >= 15:
            break
    return out or None


def normalize_category(raw):
    """Short hierarchical path, e.g. 'platform/mid-server/discovery'."""
    if not isinstance(raw, str):
        return None
    parts = [re.sub(r'[^a-z0-9\-]+', '-', seg.lower()).strip('-') for seg in raw.split('/')]
    parts = [p for p in parts if p][:4]
    return '/'.join(parts)[:96] if parts else None


def normalize_doc_type(raw):
    if not isinstance(raw, str):
        return None
    t = raw.lower().strip().replace(' ', '-')
    for known in DOC_TYPES:
        if t == known or t.startswith(known):
            return known
    if 'trouble' in t or 'issue' in t:
        return 'troubleshooting'
    if 'how' in t or 'install' in t or 'config' in t:
        return 'how-to'
    return 'reference'


def normalize_audience(raw):
    if not isinstance(raw, str):
        return None
    a = raw.lower().strip().replace(' ', '-')
    for known in AUDIENCES:
        if known in a:  # containment: 'system-administrators' -> 'admin'
            return known
    return 'all'


def normalize_tags(raw: Any) -> Optional[list[str]]:
    if not isinstance(raw, list):
        return None
    tags: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        # slug-case: lowercase, non-alphanumerics → '-', collapse, trim
        slug = re.sub(r'[^a-z0-9]+', '-', item.lower()).strip('-')
        if slug and len(slug) <= TAGS_MAX_LEN and slug not in tags:
            tags.append(slug)
        if len(tags) >= TAGS_MAX_COUNT:
            break
    return tags or None


SYSTEM_PROMPT = """You improve library documents for a documentation retrieval system.
Given a document excerpt and its current metadata, propose an IMPROVED title,
metadata, and (optionally) an improved document body.

Rules:
- Respond with ONLY a JSON object, no prose, no code fences.
- Keys: "title", "description", "summary", "tags", "keywords", "category",
  "doc_type", "audience", "content". Use null for any field you cannot improve.
- title: 5-80 chars, human-readable, NO file extension, no leading numbers,
  and it must describe what the document IS (not the product area it lives in).
  Prefer a real subject phrase over a generic label like "Overview" or
  "Getting started".
- description: one sentence, max 280 chars, states what the document covers.
- summary: max 600 chars, the key points a searcher needs.
- tags: 1-8 short lowercase slug tags (e.g. "mid-server", "discovery").
- keywords: 5-15 lowercase search terms someone would type to find this doc -
  include product names, feature names, error codes, table/property names.
- category: short hierarchical path, e.g. "platform/mid-server/discovery".
- doc_type: exactly one of "reference", "how-to", "troubleshooting",
  "concept", "release-notes", "api", "overview".
- audience: exactly one of "admin", "developer", "itom", "end-user", "all".
- content: the improved document body in markdown. Keep EVERY fact from the
  excerpt (names, values, steps, tables) and only improve structure,
  headings, ordering and wording. Do not invent facts. Do not add a
  "Generated by" note. If the excerpt is already good, return null.
- Never invent facts not present in the excerpt. Never include emails, keys,
  tokens or personal data.
"""


# ── Low-value detection (cheap, no model call) ───────────────────────────

_MD_LINK_RE = re.compile(r'\[[^\]]*\]\([^)]*\)')
_BARE_URL_RE = re.compile(r'https?://\S+')
_MD_NOISE_RE = re.compile(r'[#>*_`\-|\s]')


def detect_low_value(text: str) -> tuple[bool, Optional[str]]:
    """Cheap pre-model check for documents with no retrieval value.

    Returns ``(is_low_value, reason)`` where reason is ``'empty'`` for an
    empty body or ``'links_only'`` for a body that is essentially nothing
    but links to other sites. Such files still get embedded and returned by
    vector queries, which is noise - the enrichment flow can mark them
    ignored instead.

    No model call: this runs before the expensive part of the scan.
    """
    if not text or not text.strip():
        return True, 'empty'
    stripped = _MD_LINK_RE.sub(' ', text)
    stripped = _BARE_URL_RE.sub(' ', stripped)
    stripped = _MD_NOISE_RE.sub('', stripped)
    if len(stripped.strip()) < LOW_VALUE_MIN_TEXT:
        return True, 'links_only'
    return False, None


def _extract_json(content: str) -> Optional[dict]:
    if not content:
        return None
    # Tolerate ```json fences and stray prose around the object.
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _current_metadata(file) -> dict:
    meta = file.meta or {}
    return {
        'title': file.filename or '',
        'description': meta.get('description') or '',
        'summary': meta.get('summary') or '',
        'tags': meta.get('tags') or [],
        'keywords': meta.get('keywords') or [],
        'category': meta.get('category') or '',
        'doc_type': meta.get('doc_type') or '',
        'audience': meta.get('audience') or '',
    }


def _file_excerpt(file) -> str:
    """Read the stored file bytes and return a bounded text excerpt.

    Goes through the Storage abstraction (Storage.get_file) so S3-backed
    deployments work, not just local UPLOAD_DIR. Blocking IO is acceptable
    here - the scan worker is already bounded by max_parallel=1 default.
    """
    try:
        from open_webui.storage.provider import Storage

        if not file.path:
            return ''
        local = Storage.get_file(file.path)
        with open(local, 'rb') as fh:
            raw = fh.read(CONTENT_EXCERPT_CHARS * 4)  # bytes headroom for multi-byte
        text = raw.decode('utf-8', errors='replace')
        return text[:CONTENT_EXCERPT_CHARS]
    except Exception:
        return ''
        with open(local, 'rb') as fh:
            raw = fh.read(CONTENT_EXCERPT_CHARS * 4)  # bytes headroom for multi-byte
        text = raw.decode('utf-8', errors='replace')
        return text[:CONTENT_EXCERPT_CHARS]
    except Exception:
        return ''


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.strip()) == 0
    if isinstance(value, list):
        return len(value) == 0
    return False


async def _suggest_one(
    request, user, model_id: str, file, attributes: list[str], improve_content: bool = False
) -> Optional[dict]:
    """One model call for one file. Returns the raw proposal dict or None."""
    excerpt = _file_excerpt(file)
    if not excerpt.strip():
        return None

    current = _current_metadata(file)
    scoped = {a: current.get(a) for a in attributes}
    content_hint = '"content": the improved body (markdown) or null, ' if improve_content else '"content": null, '
    user_prompt = (
        f'Current metadata:\n{json.dumps(scoped, ensure_ascii=False)}\n\n'
        f'Document excerpt:\n"""\n{excerpt}\n"""\n\n'
        f'Return the improved JSON now (include {content_hint}the other keys only when you can improve them).'
    )
    payload = {
        'model': model_id,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': False,
        'temperature': 0.2,
        # Disable tool calling for this request. The scan worker uses a
        # minimal request shim that does not run the tool-calling loop,
        # so a model that returns tool_calls (e.g. a function-model
        # with knowledge attached that wants to search before answering)
        # would leave us with content=null and finish_reason='tool_calls'.
        # Server-side auto-RAG (knowledge context injection) still runs
        # because it is independent of the model's tool choice.
        'tool_choice': 'none',
    }

    def _diagnose(resp) -> str:
        """One-line description of a response's shape - used in logs
        and in the user-facing error so admins can see why a model
        returned empty content without dumping the whole payload."""
        if resp is None:
            return 'response is None'
        if not isinstance(resp, dict):
            return f'response is {type(resp).__name__} (not a dict): {str(resp)[:120]!r}'
        if 'error' in resp:
            return f'response has error field: {str(resp["error"])[:200]!r}'
        choices = resp.get('choices') or []
        if not choices:
            return f'response has no choices; keys={list(resp.keys())}'
        ch0 = choices[0] or {}
        msg = ch0.get('message') or {}
        finish = ch0.get('finish_reason')
        if not msg:
            return f'choices[0] has no message; finish_reason={finish!r}; keys={list(ch0.keys())}'
        c = msg.get('content')
        rc = msg.get('reasoning_content')
        tc = msg.get('tool_calls')
        refusal = msg.get('refusal')
        bits = [
            f'content_type={type(c).__name__}',
            f'content_len={len(c) if isinstance(c, (str, list)) else "n/a"}',
            f'has_reasoning={bool(rc)}',
            f'has_tool_calls={bool(tc)}',
            f'refusal={refusal!r}' if refusal else None,
            f'finish_reason={finish!r}',
        ]
        return ' '.join(b for b in bits if b)

    def _extract_content(resp) -> str:
        """Pull text out of a non-stream completion response.

        Handles the common shapes: content as str; content as a list of
        parts ({type: text, text: ...}); and message objects that carry
        the payload under reasoning_content. Returns stripped text."""
        try:
            msg = (((resp or {}).get('choices') or [{}])[0].get('message')) or {}
        except Exception as e:
            raise RuntimeError(f'unparseable model response ({type(resp).__name__}): {e}')
        content = msg.get('content')
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        if not (content or '').strip():
            content = msg.get('reasoning_content') or ''
        if not (content or '').strip():
            # Fallback: some wrappers/pipelines put the answer in a
            # nonstandard field ('text', 'response', 'output', ...).
            # Take the longest string value found anywhere in the
            # message object as a best-effort recovery.
            best = ''
            stack = [msg]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)
                elif isinstance(cur, str) and len(cur) > len(best):
                    best = cur
            content = best
        return (content or '').strip()

    # Some providers intermittently return an empty first response
    # (reasoning models, rate-limit soft-fails). One retry before failing.
    last_err = None
    last_diag = None
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(1.5)
        response = await generate_chat_completion(request, form_data=payload, user=user)
        diag = _diagnose(response)
        try:
            content = _extract_content(response)
        except RuntimeError as e:
            last_err = str(e)
            last_diag = diag
            log.warning('metadata_suggest: model %r attempt %d - %s | %s', model_id, attempt + 1, e, diag)
            continue
        if not content:
            last_err = f'model {model_id!r} returned an empty message (attempt {attempt + 1}/2)'
            last_diag = diag
            log.warning(
                'metadata_suggest: model %r attempt %d - empty content | %s | full response: %.600s',
                model_id,
                attempt + 1,
                diag,
                response,
            )
            # Wrapper/workspace models can carry saved advanced params
            # (stop sequences, templates) that silently blank the output;
            # include them once so the cause is visible.
            try:
                from open_webui.models.models import Models

                mi = await Models.get_model_by_id(model_id)
                if mi is not None:
                    log.warning(
                        'metadata_suggest: %r is a workspace model; base=%r params=%s',
                        model_id,
                        mi.base_model_id,
                        mi.params.model_dump() if mi.params else None,
                    )
            except Exception:
                pass
            continue
        parsed = _extract_json(content)
        if parsed is None:
            last_err = f'model response contained no JSON object: {content[:120]!r}'
            last_diag = diag
            log.warning('metadata_suggest: model %r attempt %d - no JSON | %s', model_id, attempt + 1, diag)
            continue
        return parsed
    # Surface the diagnostic in the user-facing error so admins see why
    # without grepping the server log.
    msg = last_err or 'model returned an empty message'
    if last_diag:
        msg = f'{msg} | {last_diag}'
    raise RuntimeError(msg)


async def write_file_content(file, new_text: str) -> bool:
    """Replace a file's stored body with ``new_text``.

    Writes the new bytes to storage, repoints the file row at them and
    updates the cached extracted text (``data.content``) so the next
    extraction/embedding pass sees the improved body.

    Returns True on success.
    """
    import io

    try:
        from open_webui.storage.provider import Storage
        from open_webui.models.files import FileUpdateForm  # noqa: F401

        stored_name = f'{file.id}_{file.filename or "document"}'
        _contents, file_path = await asyncio.to_thread(
            Storage.upload_file,
            io.BytesIO(new_text.encode('utf-8')),
            stored_name,
            {'OpenWebUI-Metadata-Enrichment': 'content'},
        )
        data = dict(file.data or {})
        data['content'] = new_text
        await Files.update_file_by_id(file.id, FileUpdateForm(path=file_path, data=data))
        return True
    except Exception as e:
        log.warning('Could not write enriched content for %s: %s', file.id, e)
        return False


async def set_file_ignored(file, reason: str = 'manual', knowledge_id: str | None = None) -> bool:
    """Mark a file as ignored.

    An ignored file is excluded from knowledge queries: its vectors are
    removed from the knowledge collection and the durable worker will not
    embed it again. The file itself stays in the knowledge base (so it can
    be un-ignored later) - unlike a delete, a GitHub-synced file is not
    re-added by the next sync because the ignore flag travels with the
    file's metadata.
    """
    try:
        meta = dict(file.meta or {})
        meta['ignored'] = True
        meta['ignored_reason'] = reason
        meta['ignored_at'] = int(time.time())
        await Files.update_file_metadata_by_id(file.id, meta)

        # Drop the vectors so the file stops being returned by queries.
        try:
            from open_webui.retrieval.vector import ASYNC_VECTOR_DB_CLIENT

            for kb in await Knowledges.get_knowledges_by_file_id(file.id):
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=kb.id, filter={'file_id': file.id})
                await Knowledges.set_embedding_status(kb.id, file.id, 'ignored')
        except Exception as e:
            log.warning('Could not purge vectors of ignored file %s: %s', file.id, e)
        return True
    except Exception as e:
        log.warning('Could not ignore file %s: %s', file.id, e)
        return False


async def clear_file_ignored(file, knowledge_id: str | None = None) -> bool:
    """Un-ignore a file: clear the flag and re-queue it for embedding."""
    try:
        meta = dict(file.meta or {})
        meta.pop('ignored', None)
        meta.pop('ignored_reason', None)
        meta.pop('ignored_at', None)
        await Files.update_file_metadata_by_id(file.id, meta)

        # Re-queue the link so the durable worker re-embeds the file and
        # it becomes queryable again.
        for kb in await Knowledges.get_knowledges_by_file_id(file.id):
            await Knowledges.set_embedding_status(kb.id, file.id, 'pending')
        return True
    except Exception as e:
        log.warning('Could not un-ignore file %s: %s', file.id, e)
        return False


async def apply_enrichment(
    app,
    file,
    scan,
    proposal: dict,
    *,
    update_titles: bool = True,
    improve_content: bool = True,
) -> dict:
    """Write an enrichment result to the file and re-embed it.

    This is the ONLY write path of the enrichment feature (the approval
    flow was removed - enrichment applies automatically).

    - the improved title goes to ``meta.title`` (NOT the file name: the
      name is the source-of-truth filename used by the GitHub sync, and
      renaming it made the sync re-add the file as a duplicate)
    - the improved body is written to storage and the extracted text
      cache is updated
    - the file is re-embedded once when anything changed, so the vectors
      pick up the new metadata header and the new body

    Returns a dict with the applied fields and counters.
    """
    result: dict = {
        'title_updated': False,
        'content_updated': False,
        'metadata_updated': False,
        'reembedded': False,
        'redaction_count': 0,
    }

    meta = dict(file.meta or {})
    current = _current_metadata(file)

    # ── Title ────────────────────────────────────────────────────────
    new_title = proposal.get('proposed_title')
    if update_titles and new_title and new_title != current['title']:
        meta['title'] = new_title
        result['title_updated'] = True

    # ── Metadata ─────────────────────────────────────────────────────
    for key, meta_key in (
        ('proposed_description', 'description'),
        ('proposed_summary', 'summary'),
        ('proposed_tags', 'tags'),
        ('proposed_keywords', 'keywords'),
    ):
        value = proposal.get(key)
        if value:
            meta[meta_key] = value
            result['metadata_updated'] = True
    for k, v in (proposal.get('proposed_extra') or {}).items():
        meta[k] = v
        result['metadata_updated'] = True

    if result['title_updated'] or result['metadata_updated']:
        meta['metadata_improved_at'] = int(time.time())
        meta['metadata_improved_by_model'] = scan.model_id
        await Files.update_file_metadata_by_id(file.id, meta)
        file = await Files.get_file_by_id(file.id) or file

    # ── Content ──────────────────────────────────────────────────────
    new_content = proposal.get('proposed_content')
    if improve_content and new_content:
        original = (file.data or {}).get('content') or ''
        # A much shorter body is a model truncation, not an improvement.
        if original and len(new_content.strip()) < len(original.strip()) * MIN_CONTENT_RATIO:
            log.info(
                'Discarding proposed content for %s: %d chars vs %d original (truncation?)',
                file.id,
                len(new_content.strip()),
                len(original.strip()),
            )
            new_content = None
        if new_content and await write_file_content(file, new_content):
            meta['content_improved_at'] = int(time.time())
            meta['content_improved_by_model'] = scan.model_id
            await Files.update_file_metadata_by_id(file.id, meta)
            file = await Files.get_file_by_id(file.id) or file
            result['content_updated'] = True

    # ── Re-embed once ────────────────────────────────────────────────
    if result['title_updated'] or result['metadata_updated'] or result['content_updated']:
        if await reembed_file(app, file, scan.knowledge_id):
            result['reembedded'] = True

    return result


async def reembed_file(app, file, knowledge_id: str) -> bool:
    """Re-chunk + re-embed ONE file so its enriched metadata flows into the
    embeddings (content+metadata header). Deletes the per-file collection and
    the file's chunks inside the KB collection, then re-runs process_file —
    the same force-reembed path the /knowledge/reindex loop uses."""
    from open_webui.internal.db import get_async_db
    from open_webui.retrieval.vector import ASYNC_VECTOR_DB_CLIENT
    from open_webui.routers.retrieval import ProcessFileForm, process_file

    class _R:
        def __init__(self, app):
            self.app = app
            self.state = SimpleNamespace()
            self.headers = {}
            self.cookies = {}
            self.method = 'POST'
            self.url = SimpleNamespace(query='', path='/x')

        async def body(self):
            return b''

    try:
        file_collection = f'file-{file.id}'
        if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
            await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
        # Remove this file's stale chunks from the KB collection only.
        await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file.id})
        from open_webui.models.users import Users as _Users

        user = await _Users.get_user_by_id(file.user_id)
        async with get_async_db() as db:
            await process_file(
                _R(app),
                ProcessFileForm(file_id=file.id, collection_name=knowledge_id),
                user=user,
                db=db,
            )
        return True
    except Exception as e:
        log.warning('reembed_file failed for %s: %s', getattr(file, 'filename', file.id), e)
        return False


async def run_metadata_scan(app, scan: MetadataScanModel) -> None:
    """Background task body. Bounded parallelism, per-file isolation — one
    failure is logged and counted, never aborts the scan."""
    from open_webui.models.users import Users

    class _ScanRequestShim:
        """Minimal Request stand-in for generate_chat_completion outside an
        HTTP context. Covers every attribute the completion path touches:
        .app.state.*, .state (bypass flags / metadata), .headers.get
        (X-Skip-Provider-URLs in the openai router), .cookies.get (oauth),
        .method/.url.query (passthrough helpers), and `await .body()`."""

        def __init__(self, app):
            self.app = app
            self.state = SimpleNamespace()
            self.headers = {}
            self.cookies = {}
            self.method = 'POST'
            self.url = SimpleNamespace(query='', path='/api/v1/chat/completions')

        async def body(self):
            return b''

    request = _ScanRequestShim(app)
    user = await Users.get_user_by_id(scan.user_id)
    if not user:
        await MetadataScans.finish_scan(scan.id, 'failed', ['Owning admin user not found'])
        return

    # Scope attributes
    if scan.mode == 'attributes':
        attributes = [a for a in (scan.attributes or []) if a in METADATA_ATTRIBUTES] or list(METADATA_ATTRIBUTES)
    else:
        attributes = list(METADATA_ATTRIBUTES)

    # Scope files
    files = await Knowledges.get_files_by_id(scan.knowledge_id)
    if scan.mode == 'file' and scan.file_id:
        files = [f for f in files if f.id == scan.file_id]
    elif scan.mode == 'missing':
        files = [f for f in files if any(_is_missing(v) for v in _current_metadata(f).values())]
    # Ignored files are excluded from enrichment unless explicitly asked
    # for (they are still queryable=false, i.e. they have no vectors).
    if not scan.include_ignored:
        files = [f for f in files if not (f.meta or {}).get('ignored')]

    # Test runs process exactly one file - report 1, not the full KB size.
    total = 1 if scan.test_run else len(files)
    await MetadataScans.update_progress(scan.id, total=total)
    if total == 0:
        await MetadataScans.finish_scan(scan.id, 'completed', ['No matching files'])
        return

    if scan.test_run:
        # Test run: exactly one file, propose ONLY (never applied, never
        # re-embedded). Preview shows what would change on the file record
        # AND in the embedding header.
        file = files[0]
        current = _current_metadata(file)
        header_before = build_metadata_header(
            {'title': current['title'], 'description': current['description'], **current}
        )
        try:
            raw = await _suggest_one(request, user, scan.model_id, file, list(METADATA_ATTRIBUTES))
        except Exception as e:
            await MetadataScans.finish_scan(scan.id, 'failed', [f'{file.filename}: {e}'])
            return
        preview_fields = {
            'title': raw.get('title') if isinstance(raw, dict) else None,
            'description': raw.get('description') if isinstance(raw, dict) else None,
            'summary': raw.get('summary') if isinstance(raw, dict) else None,
            'tags': raw.get('tags') if isinstance(raw, dict) else None,
            'keywords': raw.get('keywords') if isinstance(raw, dict) else None,
            'category': raw.get('category') if isinstance(raw, dict) else None,
            'doc_type': raw.get('doc_type') if isinstance(raw, dict) else None,
            'audience': raw.get('audience') if isinstance(raw, dict) else None,
        }
        after = dict(current)
        for k, v in preview_fields.items():
            if v:
                after[k] = v
        header_after = build_metadata_header(after)
        # Content before/after (only when the scan asked for content
        # improvement) so the preview shows the rewritten body too.
        content_before = ((file.data or {}).get('content') or '')[:CONTENT_PREVIEW_CHARS] or None
        content_after = None
        if scan.improve_content and isinstance(raw, dict):
            candidate = raw.get('content')
            if isinstance(candidate, str) and candidate.strip():
                content_after = candidate.strip()[:CONTENT_PREVIEW_CHARS]
        await MetadataScans.set_test_preview(
            scan.id,
            {
                'file_id': file.id,
                'filename': file.filename,
                'current': current,
                'proposed': preview_fields,
                'header_before': header_before or '(none - content-only embedding)',
                'header_after': header_after or '(none)',
                'content_before': content_before,
                'content_after': content_after,
            },
        )
        await MetadataScans.update_progress(scan.id, processed=1)
        await MetadataScans.finish_scan(scan.id, 'completed', None)
        return

    # Server-side parallelism ceiling (admin config); scan value is the
    # user's request, clamped by the ceiling. Default 1.
    from open_webui.models.config import Config

    try:
        ceiling = int(await Config.get('knowledge.metadata.max_parallel', 1))
    except Exception:
        ceiling = 1
    max_parallel = max(1, min(int(scan.max_parallel or 1), ceiling))
    sem = asyncio.Semaphore(max_parallel)

    processed = 0
    proposals = 0
    redactions_total = 0
    reembedded_total = 0
    content_updated = 0
    titles_updated = 0
    ignored_total = 0
    errors: list[str] = []
    lock = asyncio.Lock()

    async def handle_file(file):
        """Enrich ONE file automatically (no approval step).

        Order of operations (cheap first, model call last):
          1. skip ignored files (unless the scan asked to include them)
          2. skip files already enriched (unless 'force') - saves LLM cost
          3. cheap low-value detection (empty / links-only) - no model call
          4. one model call -> metadata + (optionally) a better body
          5. apply automatically, re-embed once
        """
        nonlocal processed, proposals, redactions_total, reembedded_total
        nonlocal content_updated, titles_updated, ignored_total

        # Cancellation checkpoint: the cancel endpoint flips status to
        # 'cancelled'; stop picking up new files (in-flight ones finish).
        current = await MetadataScans.get_scan(scan.id)
        if current and current.status == 'cancelled':
            return

        file_meta = file.meta or {}

        # ── 1. ignored files ──────────────────────────────────────────
        if file_meta.get('ignored') and not scan.include_ignored:
            async with lock:
                processed += 1
                ignored_total += 1
                await MetadataScans.update_progress(scan.id, processed=processed, ignored=ignored_total)
            return

        # ── 2. already enriched ──────────────────────────────────────
        if file_meta.get('metadata_improved_at') and not scan.force:
            async with lock:
                processed += 1
                await MetadataScans.update_progress(scan.id, processed=processed)
            return

        # ── 3. low-value detection (no model call) ───────────────────
        excerpt = _file_excerpt(file)
        if scan.ignore_low_value:
            low, reason = detect_low_value(excerpt)
            if low:
                await set_file_ignored(file, reason=reason, knowledge_id=scan.knowledge_id)
                async with lock:
                    processed += 1
                    ignored_total += 1
                    await MetadataScans.update_progress(scan.id, processed=processed, ignored=ignored_total)
                return

        async with sem:
            try:
                raw = await _suggest_one(
                    request,
                    user,
                    scan.model_id,
                    file,
                    attributes,
                    improve_content=scan.improve_content,
                )
            except Exception as e:
                async with lock:
                    errors.append(f'{file.filename}: model call failed: {e}')
                    processed += 1
                    await MetadataScans.update_progress(scan.id, processed=processed)
                return

            async with lock:
                processed += 1
                if not raw:
                    await MetadataScans.update_progress(scan.id, processed=processed)
                    return

            current = _current_metadata(file)
            proposal: dict[str, Any] = {}
            redaction_count = 0

            if 'title' in attributes:
                title = normalize_title(raw.get('title'))
                if title and title != current['title']:
                    t, n = redact_pii(title)
                    redaction_count += n
                    proposal['proposed_title'] = t
            if 'description' in attributes:
                desc = normalize_text(raw.get('description'), DESCRIPTION_MAX)
                if desc and desc != current['description']:
                    d, n = redact_pii(desc)
                    redaction_count += n
                    proposal['proposed_description'] = d
            if 'summary' in attributes:
                summ = normalize_text(raw.get('summary'), SUMMARY_MAX)
                if summ and summ != current['summary']:
                    s, n = redact_pii(summ)
                    redaction_count += n
                    proposal['proposed_summary'] = s
            if 'tags' in attributes:
                tags = normalize_tags(raw.get('tags'))
                if tags and sorted(tags) != sorted(current['tags'] or []):
                    proposal['proposed_tags'] = [redact_pii(t)[0] for t in tags]
            if 'keywords' in attributes:
                kws = normalize_keywords(raw.get('keywords'))
                if kws and kws != (current['keywords'] or []):
                    proposal['proposed_keywords'] = [redact_pii(k)[0] for k in kws]
            extra: dict = {}
            if 'category' in attributes:
                cat = normalize_category(raw.get('category'))
                if cat and cat != current['category']:
                    extra['category'] = cat
            if 'doc_type' in attributes:
                dt = normalize_doc_type(raw.get('doc_type'))
                if dt and dt != current['doc_type']:
                    extra['doc_type'] = dt
            if 'audience' in attributes:
                aud = normalize_audience(raw.get('audience'))
                if aud and aud != current['audience']:
                    extra['audience'] = aud
            if extra:
                proposal['proposed_extra'] = extra

            # ── Content proposal ───────────────────────────────────────
            proposed_content = None
            if scan.improve_content:
                candidate = raw.get('content')
                if isinstance(candidate, str) and candidate.strip():
                    c, n = redact_pii(candidate)
                    redaction_count += n
                    proposed_content = c.strip()

            # ── 5. apply automatically ─────────────────────────────────
            applied = {'title_updated': False, 'content_updated': False}
            payload_status = 'no_change'
            if proposal or proposed_content:
                if proposed_content:
                    proposal['proposed_content'] = proposed_content
                try:
                    applied = await apply_enrichment(
                        app,
                        file,
                        scan,
                        proposal,
                        update_titles=scan.update_titles,
                        improve_content=scan.improve_content,
                    )
                    payload_status = 'applied'
                except Exception as e:
                    log.warning('Enrichment failed for %s: %s', file.filename, e)
                    errors.append(f'{file.filename}: enrichment failed: {e}')
                    payload_status = 'failed'

            async with lock:
                if payload_status == 'applied':
                    proposals += 1
                    if applied.get('title_updated'):
                        titles_updated += 1
                    if applied.get('content_updated'):
                        content_updated += 1
                    if applied.get('reembedded'):
                        reembedded_total += 1
                redactions_total += redaction_count

                # Audit trail: the proposal row records what changed and
                # what it was before (no approval queue any more).
                payload_base: dict = {
                    'previous_title': current['title'] or None,
                    'previous_description': current['description'] or None,
                    'previous_summary': current['summary'] or None,
                    'previous_tags': current['tags'] or None,
                    'previous_keywords': current['keywords'] or None,
                }
                payload_base.update(proposal)
                payload_base.update(
                    {
                        'scan_id': scan.id,
                        'knowledge_id': scan.knowledge_id,
                        'file_id': file.id,
                        'user_id': scan.user_id,
                        'redaction_count': redaction_count,
                        'proposer_model_id': scan.model_id,
                        'status': payload_status,
                        'content_changed': 1 if applied.get('content_updated') else 0,
                    }
                )
                if proposed_content:
                    payload_base['previous_content_excerpt'] = ((file.data or {}).get('content') or '')[
                        :CONTENT_PREVIEW_CHARS
                    ] or None
                try:
                    await MetadataProposals.insert_proposal(payload_base)
                except Exception as e:
                    log.warning('Could not record proposal for %s: %s', file.id, e)

                await MetadataScans.update_progress(
                    scan.id,
                    processed=processed,
                    proposals=proposals,
                    redactions=redactions_total,
                    reembedded=reembedded_total,
                    content_updated=content_updated,
                    titles_updated=titles_updated,
                    ignored=ignored_total,
                )

    await asyncio.gather(*(handle_file(f) for f in files))
    final = await MetadataScans.get_scan(scan.id)
    final_status = 'cancelled' if (final and final.status == 'cancelled') else 'completed'
    await MetadataScans.finish_scan(scan.id, final_status, errors or None)
    log.info(
        'Metadata scan %s finished: %s/%s files, %s proposals, %s redactions',
        scan.id,
        processed,
        total,
        proposals,
        redactions_total,
    )


async def apply_proposal(proposal) -> None:
    """Apply a stored proposal to its file.

    The approval flow is gone - the scan applies changes automatically via
    ``apply_enrichment``. This shim stays for compatibility with any
    caller that still holds a proposal row: it re-applies the proposal
    through the same write path (meta.title for the title - the file NAME
    is never touched, it is the GitHub sync's source of truth).
    """
    file = await Files.get_file_by_id(proposal.file_id)
    if not file:
        raise ValueError('File no longer exists')

    payload = {
        'proposed_title': proposal.proposed_title,
        'proposed_description': proposal.proposed_description,
        'proposed_summary': proposal.proposed_summary,
        'proposed_tags': proposal.proposed_tags,
        'proposed_keywords': proposal.proposed_keywords,
        'proposed_extra': proposal.proposed_extra,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if not payload:
        return

    meta = dict(file.meta or {})
    changed = False
    if payload.get('proposed_title'):
        meta['title'] = payload['proposed_title']
        changed = True
    for key, meta_key in (
        ('proposed_description', 'description'),
        ('proposed_summary', 'summary'),
        ('proposed_tags', 'tags'),
        ('proposed_keywords', 'keywords'),
    ):
        value = payload.get(key)
        if value is not None:
            meta[meta_key] = value
            changed = True
    for k, v in (payload.get('proposed_extra') or {}).items():
        meta[k] = v
        changed = True
    if changed:
        meta['metadata_improved_at'] = int(time.time())
        meta['metadata_improved_by_model'] = proposal.proposer_model_id
        await Files.update_file_metadata_by_id(file.id, meta)
