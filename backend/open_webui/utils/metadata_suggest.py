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

log = logging.getLogger(__name__)

# How much of each document is fed to the model. Enough for metadata;
# keeps cost and prompt-injection surface small.
CONTENT_EXCERPT_CHARS = 6000
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


SYSTEM_PROMPT = """You improve library metadata for a documentation retrieval system.
Given a document excerpt and its current metadata, propose IMPROVED metadata.

Rules:
- Respond with ONLY a JSON object, no prose, no code fences.
- Keys: "title", "description", "summary", "tags", "keywords", "category", "doc_type", "audience". Use null for any field you cannot improve.
- title: 5-80 chars, human-readable, no file extension, no leading numbers.
- description: one sentence, max 280 chars, states what the document covers.
- summary: max 600 chars, the key points a searcher needs.
- tags: 1-8 short lowercase slug tags (e.g. "mid-server", "discovery").
- keywords: 5-15 lowercase search terms someone would type to find this doc - include product names, feature names, error codes, table/property names where present.
- category: short hierarchical path, e.g. "platform/mid-server/discovery" or "itom/agent-guide".
- doc_type: exactly one of "reference", "how-to", "troubleshooting", "concept", "release-notes", "api", "overview".
- audience: exactly one of "admin", "developer", "itom", "end-user", "all".
- Never invent facts not present in the excerpt. Never include emails, keys, tokens or personal data.
"""


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


async def _suggest_one(request, user, model_id: str, file, attributes: list[str]) -> Optional[dict]:
    """One model call for one file. Returns the raw proposal dict or None."""
    excerpt = _file_excerpt(file)
    if not excerpt.strip():
        return None

    current = _current_metadata(file)
    scoped = {a: current.get(a) for a in attributes}
    user_prompt = (
        f'Current metadata:\n{json.dumps(scoped, ensure_ascii=False)}\n\n'
        f'Document excerpt:\n"""\n{excerpt}\n"""\n\n'
        'Return the improved JSON now.'
    )
    payload = {
        'model': model_id,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': False,
        'temperature': 0.2,
    }
    response = await generate_chat_completion(request, form_data=payload, user=user)
    # Non-stream responses are dict-like (same parse as tool_generator).
    try:
        content = (((response or {}).get('choices') or [{}])[0].get('message', {}) or {}).get('content') or ''
    except Exception as e:
        raise RuntimeError(f'unparseable model response ({type(response).__name__}): {e}')
    if not content.strip():
        raise RuntimeError('model returned an empty message')
    parsed = _extract_json(content)
    if parsed is None:
        raise RuntimeError(f'model response contained no JSON object: {content[:120]!r}')
    return parsed


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

    total = len(files)
    await MetadataScans.update_progress(scan.id, total=total)
    if total == 0:
        await MetadataScans.finish_scan(scan.id, 'completed', ['No matching files'])
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
    errors: list[str] = []
    lock = asyncio.Lock()

    async def handle_file(file):
        nonlocal processed, proposals, redactions_total

        # Cancellation checkpoint: the cancel endpoint flips status to
        # 'cancelled'; stop picking up new files (in-flight ones finish).
        current = await MetadataScans.get_scan(scan.id)
        if current and current.status == 'cancelled':
            return

        async with sem:
            try:
                raw = await _suggest_one(request, user, scan.model_id, file, attributes)
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

            async with lock:
                # 3-tier policy: auto-apply on empty fields, queue on populated,
                # record 'no_change' when the model returned nothing or matched.
                payload_status = 'pending'
                if proposal:
                    non_empty_present = any(
                        (current[k] or '').strip() for k in ('title', 'description', 'summary', 'tags')
                    )
                    if not non_empty_present:
                        try:
                            from open_webui.models.files import Files as _Files
                            cur_meta = dict(file.meta) if isinstance(file.meta, dict) else {}
                            cur_meta['description'] = proposal.get('proposed_description', cur_meta.get('description'))
                            cur_meta['summary'] = proposal.get('proposed_summary', cur_meta.get('summary'))
                            cur_meta['tags'] = proposal.get('proposed_tags', cur_meta.get('tags') or [])
                            cur_meta['keywords'] = proposal.get('proposed_keywords', cur_meta.get('keywords') or [])
                            for k, v in (proposal.get('proposed_extra') or {}).items():
                                cur_meta[k] = v
                            cur_meta['metadata_improved_at'] = int(time.time())
                            cur_meta['metadata_improved_by_model'] = scan.model_id
                            cur_meta['metadata_improved_auto'] = True
                            await _Files.update_file_metadata_by_id(file.id, cur_meta)
                            new_title = proposal.get('proposed_title')
                            if new_title and new_title != (file.filename or ''):
                                await _Files.update_file_name_by_id(file.id, new_title)
                            payload_status = 'auto_applied'
                        except Exception as e:
                            log.warning('Auto-apply failed for %s: %s', file.filename, e)
                            payload_status = 'pending'
                else:
                    payload_status = 'no_change'
                # Dedupe older pending + no_change for this file
                try:
                    for older in await MetadataProposals.get_proposals(
                        knowledge_id=scan.knowledge_id, limit=10000
                    ):
                        if older.file_id == file.id and older.status in ('pending', 'no_change'):
                            await MetadataProposals.set_status(older.id, 'dismissed')
                except Exception:
                    pass
                payload_base = dict(proposal) if proposal else {}
                if not proposal:
                    payload_base.update({
                        'previous_title': current['title'] or None,
                        'previous_description': current['description'] or None,
                        'previous_summary': current['summary'] or None,
                        'previous_tags': current['tags'] or None,
                        'previous_keywords': current['keywords'] or None,
                    })
                payload_base.setdefault('previous_title', current['title'] or None)
                payload_base.setdefault('previous_description', current['description'] or None)
                payload_base.setdefault('previous_summary', current['summary'] or None)
                payload_base.setdefault('previous_tags', current['tags'] or None)
                payload_base.setdefault('previous_keywords', current['keywords'] or None)
                payload_base.update({
                    'scan_id': scan.id,
                    'knowledge_id': scan.knowledge_id,
                    'file_id': file.id,
                    'user_id': scan.user_id,
                    'redaction_count': redaction_count,
                    'proposer_model_id': scan.model_id,
                    'status': payload_status,
                })
                await MetadataProposals.insert_proposal(payload_base)
                if payload_status == 'pending':
                    proposals += 1
                redactions_total += redaction_count
                await MetadataScans.update_progress(
                    scan.id,
                    processed=processed,
                    proposals=proposals,
                    redactions=redactions_total,
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
    """Apply a pending proposal to its file. Called from the admin router
    after explicit confirmation — the ONLY write path."""
    file = await Files.get_file_by_id(proposal.file_id)
    if not file:
        raise ValueError('File no longer exists')

    if proposal.proposed_title:
        await Files.update_file_name_by_id(file.id, proposal.proposed_title)

    meta = dict(file.meta or {})
    changed = False
    if proposal.proposed_description is not None:
        meta['description'] = proposal.proposed_description
        changed = True
    if proposal.proposed_summary is not None:
        meta['summary'] = proposal.proposed_summary
        changed = True
    if proposal.proposed_tags is not None:
        meta['tags'] = proposal.proposed_tags
        changed = True
    if proposal.proposed_keywords is not None:
        meta['keywords'] = proposal.proposed_keywords
        changed = True
    for k, v in (proposal.proposed_extra or {}).items():
        meta[k] = v
        changed = True
    if changed:
        meta['metadata_improved_at'] = int(time.time())
        meta['metadata_improved_by_model'] = proposal.proposer_model_id
        await Files.update_file_metadata_by_id(file.id, meta)
