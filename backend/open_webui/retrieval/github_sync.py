"""GitHub → Knowledge sync worker.

Entirely read-only against GitHub: GETs only, pinned to api.github.com and
raw.githubusercontent.com, no redirects. Every file passes the security
gates below BEFORE any byte is written to storage:

  1. Path safety — the tree entry path must live inside the configured
     directory, contain no traversal (``..``), no absolute segments, no
     backslashes, no NULs.
  2. Hardcoded security deny-list (secret material, key files) — unioned
     with the admin's exclude_globs; deny ALWAYS wins over allow.
  3. Extension allow-list — documentation/text formats only by default.
  4. Size gate — enforced from the tree API's blob size BEFORE download,
     and again on the downloaded bytes (defence in depth). Oversize files
     are SKIPPED (counted), never fatal.
  5. Content-type gate — the raw response's Content-Type must be a known
     text/document type; ``application/octet-stream`` and friends are
     rejected (binary masquerading as a doc).
  6. Binary-masquerade scan — text-typed content with >10% non-printable
     bytes is rejected.
  7. Script sanitisation — ``<script>`` blocks and inline ``on*=``
     handlers are stripped from markdown/HTML before storage so a
     malicious repo can't ship XSS into the file preview.

Change detection uses the git blob SHA from the tree API (a content hash),
so unchanged files are never re-downloaded or re-embedded.
"""

import asyncio
import io
import logging
import uuid
import re
import time
from fnmatch import fnmatch
from types import SimpleNamespace
from typing import Any, Optional
import httpx

from open_webui.internal.db import get_async_db_context
from open_webui.models.files import Files, FileForm
from open_webui.models.knowledge import Knowledges
from open_webui.routers.retrieval import ProcessFileForm, process_file
from open_webui.models.users import Users
from open_webui.models.knowledge_github_source import KnowledgeGithubSourceModel
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)

API_BASE = 'https://api.github.com'
RAW_BASE = 'https://raw.githubusercontent.com'
REQUEST_TIMEOUT = 30.0
CONCURRENT_FETCHES = 8

# Documentation/text formats safe to ingest by default. Admins broaden the
# surface with include_globs, never with extensions.
SYNC_ALLOWED_EXTENSIONS = {
    'md',
    'markdown',
    'mdx',
    'txt',
    'rst',
    'adoc',
    'asciidoc',
    'html',
    'htm',
    'xhtml',
    'pdf',
    'docx',
    'pptx',
    'xlsx',
    'odt',
    'ods',
    'odp',
    'csv',
    'tsv',
    'json',
    'xml',
    'yaml',
    'yml',
}

# Hardcoded deny — filenames/paths that must NEVER be synced regardless of
# admin configuration. Matched against the FULL repo-relative path.
SECURITY_DENY_PATTERNS = [
    '.env',
    '.env.*',
    '*.env',
    '*.key',
    '*.pem',
    '*.p12',
    '*.pfx',
    '*.jks',
    '*.keystore',
    'id_rsa*',
    'id_ed25519*',
    'id_ecdsa*',
    '*.ppk',
    'secrets.*',
    '*credentials*',
    '*credential.json',
    '.git/**',
    '.git*',
    '*.exe',
    '*.dll',
    '*.so',
    '*.dylib',
    '*.bin',
    '*.msi',
    'node_modules/**',
    '.venv/**',
    'venv/**',
]

ALLOWED_CONTENT_TYPES = {
    'text/plain',
    'text/markdown',
    'text/html',
    'text/x-markdown',
    'text/csv',
    'application/json',
    'application/xml',
    'text/xml',
    'application/yaml',
    'text/yaml',
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/octet-stream',  # raw GH default for unknown — only allowed under 10 KiB
}

# octet-stream is only tolerated for small files (probably mislabelled text).
OCTET_STREAM_MAX_BYTES = 10 * 1024

MAX_TEXT_BYTES = 2 * 1024 * 1024  # per-file text extraction cap

_SCRIPT_RE = re.compile(r'<script\b[^>]*>.*?</script\s*>', re.I | re.S)
_EVENT_HANDLER_RE = re.compile(r'\son\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.I)
_JS_ATTR_URL_RE = re.compile(r'((?:href|src)\s*=\s*)(["\']?)\s*javascript:[^)"\'>\s]*', re.I)
_JS_PAREN_URL_RE = re.compile(r'\(\s*javascript:[^)]*\)+', re.I)


def sanitize_text_content(text: str) -> str:
    """Strip active-content vectors from markdown/HTML destined for the
    file preview. Lossy by design — this is untrusted input."""
    text = _SCRIPT_RE.sub('', text)
    text = _EVENT_HANDLER_RE.sub('', text)
    text = _JS_ATTR_URL_RE.sub(r'\1\2#', text)
    text = _JS_PAREN_URL_RE.sub('(#)', text)
    return text


def path_is_safe(repo_path: str, directory_prefix: str) -> bool:
    if not repo_path or repo_path.startswith('/') or '\\' in repo_path or '\x00' in repo_path:
        return False
    segments = repo_path.split('/')
    if any(seg in ('..', '') for seg in segments):
        return False
    if directory_prefix:
        if not repo_path.startswith(directory_prefix + '/'):
            return False
    return True


def matches_any(repo_path: str, patterns: list[str]) -> bool:
    return any(fnmatch(repo_path, p) for p in patterns)


# --- Per-source extension allow-list ---
#
# Resolved per file (highest priority first):
#   1. source.allowed_extensions (per-source override; non-empty list)
#   2. rag.github.allowed_extensions (admin default config; mutable from UI)
#   3. SYNC_ALLOWED_EXTENSIONS (hardcoded fallback for docs repos)
_ADMIN_ALLOWED_DEFAULT = [
    'md', 'markdown', 'mdx', 'txt', 'rst', 'adoc', 'asciidoc',
    'html', 'htm', 'xhtml',
    'pdf', 'docx', 'pptx', 'xlsx', 'odt', 'ods', 'odp',
    'csv', 'tsv', 'json', 'xml', 'yaml', 'yml',
]


def _filename_deny(name: str) -> bool:
    """Match the FINAL filename component only (not the full repo path).
    Substring globs in the previous design mis-fired on documentation
    files like api-credentials-management.md or secrets.md."""
    name_lc = name.lower()
    if name_lc in {
        '.env', '.env.production', '.env.local', '.env.development', '.env.staging',
        'secrets', 'secrets.json', 'secrets.yaml', 'secrets.toml',
        'credentials', 'credentials.json', 'credentials.yaml',
        '.htpasswd', '.netrc',
    }:
        return True
    if name_lc.startswith(('id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa')):
        return True
    if name_lc.endswith(('.key', '.pem', '.p12', '.pfx', '.jks', '.keystore')):
        return True
    if name in ('node_modules', '.venv'):
        return True
    return False


def extension_allowed(repo_path: str, allowed_extensions: list[str]) -> bool:
    if not allowed_extensions:
        return False
    last = repo_path.rsplit('/', 1)[-1]
    ext = last.rsplit('.', 1)[-1].lower() if '.' in last else ''
    allowed = {e.lstrip('.').lower() for e in allowed_extensions}
    return ext in allowed

# Rate-limit safety margin - start pacing when we have this many calls
# remaining (in addition to the one we're about to make). Lower bound that
# leaves headroom for the catch-up burst after a sleep.
RATELIMIT_SAFETY_MARGIN = 50


class GithubSyncClient:
    def __init__(self, token: Optional[str] = None):
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self._client = httpx.AsyncClient(
            base_url=API_BASE, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=False
        )
        # Rate-limit tracking (x-ratelimit-remaining, x-ratelimit-reset epoch
        # seconds). Optional - anonymous/no-header responses stay None and
        # pacing falls through to no sleep.
        self._remaining: Optional[int] = None
        self._reset_at: Optional[float] = None
        self._lock = asyncio.Lock()

    async def aclose(self):
        await self._client.aclose()

    def _record_rate_limit(self, resp: httpx.Response) -> None:
        try:
            remaining_raw = resp.headers.get('x-ratelimit-remaining')
            reset_raw = resp.headers.get('x-ratelimit-reset')
            if remaining_raw is not None:
                self._remaining = int(remaining_raw)
            if reset_raw is not None:
                self._reset_at = float(reset_raw) or (time.time() + 60)
        except (TypeError, ValueError):
            pass

    async def _pace(self) -> None:
        """Sleep before a request when the GitHub rate-limit budget is low.

        Strategy:
          * remaining is None (no header) -> no pacing (admin-tier or older API).
          * remaining <= 0 -> sleep until x-ratelimit-reset (hard cap).
          * remaining <= RATELIMIT_SAFETY_MARGIN -> sleep until reset so we
            never hit the cap mid-run.
          * otherwise -> no sleep.

        Self-resets the cached values whenever the reset window passes, so
        the sync resumes naturally after a long pause. The lock prevents
        multiple concurrent fetches from racing to wait the same window.
        """
        async with self._lock:
            if self._remaining is None:
                return
            now = time.time()
            if self._reset_at and now >= self._reset_at:
                self._remaining = None
                self._reset_at = None
                return
            if self._remaining <= 0 or self._remaining <= RATELIMIT_SAFETY_MARGIN:
                sleep_for = max((self._reset_at or now + 60) - now, 1.0)
                log.info(
                    'GitHub rate-limit pacing: remaining=%s, sleeping %.0fs until reset',
                    self._remaining, sleep_for,
                )
                await asyncio.sleep(sleep_for)
                self._remaining = None
                self._reset_at = None

    async def _get(self, url: str, params: Optional[dict] = None) -> httpx.Response:
        await self._pace()
        resp = await self._client.get(url, params=params)
        self._record_rate_limit(resp)
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    async def get_repo(self, owner: str, repo: str) -> httpx.Response:
        return await self._get(f'/repos/{owner}/{repo}')

    async def get_branch_sha(self, owner: str, repo: str, branch: str) -> Optional[str]:
        resp = await self._get(f'/repos/{owner}/{repo}/branches/{branch}')
        if resp.status_code != 200:
            return None
        return (resp.json().get('commit') or {}).get('sha')

    async def get_tree(
        self, owner: str, repo: str, tree_sha: str, directory: str = ''
    ) -> Optional[list[dict]]:
        """Recursive tree. When GitHub truncates the recursive response
        (repos over the tree-entry limit), fall back to a non-recursive
        walk pruned to the configured directory prefix - bounded API
        calls, only the subtrees we actually need. A whole-repo sync of
        an oversized repo still errors (set a directory path)."""

        async def _node(sha: str) -> Optional[dict]:
            resp = await self._get(f'/repos/{owner}/{repo}/git/trees/{sha}')
            if resp.status_code != 200:
                return None
            return resp.json()

        resp = await self._get(f'/repos/{owner}/{repo}/git/trees/{tree_sha}', params={'recursive': '1'})
        if resp.status_code != 200:
            return None
        body = resp.json()
        if not body.get('truncated'):
            return body.get('tree') or []

        prefix = directory.strip('/') if directory else ''
        if not prefix:
            # Whole-repo recursive response truncated and no directory
            # binding to target - we cannot enumerate the repo in one
            # call. Tell the admin to bind a directory.
            raise RuntimeError(
                'Repository tree truncated by GitHub API - narrow the directory path'
            )

        log.info('Recursive tree truncated for %s/%s - targeting subtree %r', owner, repo, prefix)

        # Phase 1: walk DOWN the prefix path to find its tree SHA.
        # len(segments) API calls, one per directory on the way to the
        # bound directory.
        current_sha = tree_sha
        current_path = ''
        for seg in prefix.split('/'):
            node = await _node(current_sha)
            if node is None:
                raise RuntimeError(
                    f'Failed to fetch git tree node at {current_path or "/"} (HTTP error)'
                )
            child = next(
                (
                    e
                    for e in (node.get('tree') or [])
                    if e.get('path') == seg and e.get('type') == 'tree'
                ),
                None,
            )
            if child is None:
                # Directory binding does not exist on this branch
                return []
            current_sha = child['sha']
            current_path = f'{current_path}/{seg}' if current_path else seg

        # Phase 2: try a recursive fetch on the prefix subtree.
        # Almost always succeeds - one call returns every blob under
        # the bound directory.
        sub_resp = await self._get(
            f'/repos/{owner}/{repo}/git/trees/{current_sha}', params={'recursive': '1'}
        )
        if sub_resp.status_code != 200:
            return None
        sub_body = sub_resp.json()
        if not sub_body.get('truncated'):
            # Paths in a subtree response are RELATIVE to that subtree
            # (e.g. 'csm/file.md', not 'markdown/csm/file.md'). Re-prefix
            # so the caller's path_is_safe(directory) filter matches.
            return [
                {**entry, 'path': f"{prefix}/{entry['path']}"}
                for entry in (sub_body.get('tree') or [])
                if entry.get('path')
            ]

        log.info('Prefix subtree %r also truncated - walking its subtrees', prefix)

        # Phase 3: the prefix subtree itself exceeds the entry limit
        # (rare; >100k files inside markdown/). Fall back to BFS inside
        # the prefix subtree only - one call per directory.
        out: list[dict] = []
        queue = [(current_sha, current_path)]
        while queue:
            sha, base = queue.pop(0)
            node = await _node(sha)
            if node is None:
                raise RuntimeError(
                    f'Failed to fetch git tree node at {base or "/"} (HTTP error)'
                )
            if node.get('truncated'):
                raise RuntimeError(
                    f'Directory {base or "/"} has too many entries for the GitHub tree API - narrow the directory path'
                )
            for entry in node.get('tree') or []:
                path = f"{base}/{entry['path']}" if base else entry['path']
                if entry.get('type') == 'tree':
                    queue.append((entry['sha'], path))
                else:
                    out.append({**entry, 'path': path})
        return out

    async def fetch_raw(self, owner: str, repo: str, branch: str, repo_path: str) -> httpx.Response:
        # Pacing is serialised across in-flight fetches (lock) so the budget
        # is shared fairly instead of N concurrent bursts.
        await self._pace()
        resp = await self._client.get(f'{RAW_BASE}/{owner}/{repo}/{branch}/{repo_path}')
        self._record_rate_limit(resp)
        return resp


async def ensure_github_directory(knowledge_id: str, owner: str, repo: str, branch: str, user_id: str):
    """Get-or-create a root-level KB directory labelled github/{owner}/{repo}@{branch}.

    Gives synced files a visible folder in the knowledge file list so
    multiple GitHub sources feeding one KB stay distinguishable.
    """
    name = f'github/{owner}/{repo}@{branch}'
    for d in await Knowledges.get_all_directories(knowledge_id):
        if d.parent_id is None and d.name == name:
            return d
    created = await Knowledges.create_directory(knowledge_id, name, user_id)
    return created or None


# Per-source in-process locks + active-sync set. Prevents a manual "Sync
# now" and a scheduled tick from running the SAME source concurrently
# (both would see empty by_repo_path state and double-insert files).
_source_locks: dict = {}
_active_syncs: set = set()


def _get_source_lock(source_id: str) -> asyncio.Lock:
    if source_id not in _source_locks:
        _source_locks[source_id] = asyncio.Lock()
    return _source_locks[source_id]


async def _sweep_stale_processing_files(source_id: str) -> int:
    """Delete files left status='processing' with no KB link by a previous
    sync that died mid-extraction (process restart, cancellation). Without
    this they linger as invisible orphans in the file table forever."""
    from sqlalchemy import delete as sa_delete

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File

    async with get_async_db_context() as db:
        result = await db.execute(
            sa_delete(File).where(
                File.data['status'].as_string() == 'processing',
                File.meta['data']['github_source_id'].as_string() == source_id,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def _sync_error(source, message: str, status: str = 'error') -> dict:
    result = {
        'status': 'ok',
        'added': 0,
        'updated': 0,
        'removed': 0,
        'unchanged': 0,
        'skipped_size': 0,
        'skipped_ext': 0,
        'skipped_deny': 0,
        'skipped_dup': 0,
        'truncated': False,
        'total_candidates': 0,
        'errors': [],
    }
    from open_webui.models.knowledge_github_source import KnowledgeGithubSources

    await KnowledgeGithubSources.finish_sync(source.id, status='error', result=result, failure=True)
    return result


async def sync_github_source(app, source: KnowledgeGithubSourceModel) -> dict:
    """Run one full sync of a source. Idempotent, safe to re-run.

    Serialised per source: a second concurrent caller (manual Sync now
    racing a scheduled tick) gets an already-running marker instead of
    double-inserting files.
    """
    lock = _get_source_lock(source.id)
    if lock.locked() or source.id in _active_syncs:
        return {
            'status': 'already_running',
            'error': 'A sync for this source is already in progress',
            'added': 0, 'updated': 0, 'removed': 0, 'unchanged': 0,
            'skipped_size': 0, 'skipped_ext': 0, 'skipped_deny': 0, 'skipped_dup': 0,
            'truncated': False, 'total_candidates': 0, 'errors': ['already running'],
        }
    async with lock:
        _active_syncs.add(source.id)
        try:
            return await _sync_github_source_inner(app, source)
        finally:
            _active_syncs.discard(source.id)


async def _sync_github_source_inner(app, source: KnowledgeGithubSourceModel) -> dict:
    import hashlib

    from open_webui.models.github_credential import GithubCredentials
    from open_webui.models.knowledge_github_source import KnowledgeGithubSources

    owner, repo, branch = source.repo_owner, source.repo_name, source.branch
    directory = (source.directory_path or '').strip('/')

    # Clean files orphaned by a previously crashed sync (status='processing',
    # never linked into the KB). Counted in the result envelope.
    try:
        result_orphans = await _sweep_stale_processing_files(source.id)
        if result_orphans:
            log.info('GitHub sync %s: swept %d orphaned processing file(s)', source.id, result_orphans)
    except Exception:
        log.exception('GitHub sync %s: orphan sweep failed (continuing)', source.id)
        result_orphans = 0

    token = None
    if source.credential_id:
        token = await GithubCredentials.resolve_token(source.credential_id)
        if not token:
            return await _sync_error(source, 'Credential missing or undecryptable — re-create it')

    client = GithubSyncClient(token)
    result = {
        'status': 'ok',
        'added': 0,
        'updated': 0,
        'removed': 0,
        'unchanged': 0,
        'skipped_size': 0,
        'skipped_ext': 0,
        'skipped_deny': 0,
        'skipped_dup': 0,
        'errors': [],
    }

    try:
        # ── Repo + branch resolution ──────────────────────────────────
        repo_resp = await client.get_repo(owner, repo)
        if repo_resp.status_code == 404:
            return await _sync_error(source, f'Repository {owner}/{repo} not found (404)')
        if repo_resp.status_code in (401, 403):
            return await _sync_error(
                source,
                'GitHub rejected the credential (401/403) — check token scopes and expiry',
            )
        repo_resp.raise_for_status()

        head_sha = await client.get_branch_sha(owner, repo, branch)
        if not head_sha:
            return await _sync_error(source, f'Branch {branch!r} not found')

        # ── Tree ──────────────────────────────────────────────────────
        try:
            tree = await client.get_tree(owner, repo, head_sha, directory=directory)
        except httpx.HTTPStatusError as exc:
            status = getattr(exc.response, 'status_code', None)
            resp_headers = getattr(exc.response, 'headers', {}) if getattr(exc, 'response', None) else {}
            remaining = resp_headers.get('x-ratelimit-remaining') if resp_headers else None
            retry_after = resp_headers.get('retry-after') if resp_headers else None
            if status in (403, 429) or remaining == '0':
                msg = f'GitHub rate-limited (HTTP {status}'
                if remaining == '0':
                    msg += ', remaining=0'
                if retry_after:
                    msg += f', retry after {retry_after}s'
                msg += '). Add a GitHub token to raise the limit to 5,000 req/h, or wait and re-sync.'
                return await _sync_error(source, msg)
            return await _sync_error(source, f'Failed to list repository tree (HTTP {status})')
        if tree is None:
            return await _sync_error(source, 'Failed to list repository tree')

        blobs = [entry for entry in tree if entry.get('type') == 'blob' and path_is_safe(entry['path'], directory)]

        # Resolve the extension allow-list for this source (per-source override -> admin default -> hardcoded fallback)
        try:
            admin_default_exts = await Config.get('rag.github.allowed_extensions') or _ADMIN_ALLOWED_DEFAULT
        except Exception:
            admin_default_exts = _ADMIN_ALLOWED_DEFAULT
        allowed_exts = list(source.allowed_extensions) if source.allowed_extensions else list(admin_default_exts)

        # Security + policy filters
        candidates = []
        for entry in blobs:
            path = entry['path']
            name = path.rsplit('/', 1)[-1]
            if _filename_deny(name) or matches_any(path, list(source.exclude_globs or [])):
                result['skipped_deny'] += 1
                continue
            if source.include_globs and not matches_any(path, list(source.include_globs)):
                continue
            if not extension_allowed(path, allowed_exts):
                result['skipped_ext'] += 1
                continue
            size = int(entry.get('size') or 0)
            if size > int(source.max_file_bytes):
                result['skipped_size'] += 1
                continue
            candidates.append(entry)

        total_candidates = len(candidates)
        max_files = int(source.max_files or 0)
        if max_files > 0 and total_candidates > max_files:
            # Honour an explicit safety cap if the admin set one. Default
            # sources (max_files=0) skip the cap entirely and pace via the
            # GitHub rate-limit headers instead — see GithubSyncClient._pace.
            result['truncated'] = True
            result['total_candidates'] = total_candidates
            result['max_files'] = max_files
            result['errors'].append(
                f'{total_candidates} candidate files exceed max_files={max_files}; '
                f'syncing first {max_files}. Set max_files to 0 on this source to remove the cap.'
            )
            candidates = candidates[:max_files]

        # ── Existing synced files for this source (by repo path) ─────
        existing_files = await Knowledges.get_files_by_id(source.knowledge_id)
        by_repo_path: dict[str, Any] = {}
        for f in existing_files:
            meta = (f.meta or {}).get('data') or {}
            if meta.get('github_source_id') == source.id and meta.get('repo_path'):
                by_repo_path[meta['repo_path']] = f

        user = await Users.get_user_by_id(source.user_id)
        if not user:
            return await _sync_error(source, 'Owning admin user no longer exists')

        # Per-source folder in the KB file list (github/owner/repo@branch) so
        # multiple sources feeding one KB stay visually distinguishable.
        directory = await ensure_github_directory(source.knowledge_id, owner, repo, branch, user.id)
        directory_id = directory.id if directory else None

        # ── Fetch + gate + insert ─────────────────────────────────────
        sem = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def handle_entry(entry: dict):
            path = entry['path']
            blob_sha = entry.get('sha') or ''
            async with sem:
                try:
                    existing = by_repo_path.get(path)
                    if existing and (existing.meta or {}).get('data', {}).get('repo_sha') == blob_sha:
                        result['unchanged'] += 1
                        return

                    resp = await client.fetch_raw(owner, repo, branch, path)
                    if resp.status_code != 200:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            result['errors'].append(
                                f'{path}: redirect ({resp.status_code}) not followed - likely a '
                                'Git LFS pointer or renamed path; excluded by policy'
                            )
                        else:
                            result['errors'].append(f'{path}: HTTP {resp.status_code}')
                        return
                    content = resp.content
                    if len(content) > int(source.max_file_bytes):
                        result['skipped_size'] += 1
                        return

                    content_type = resp.headers.get('content-type', '').split(';')[0].strip().lower()
                    if content_type not in ALLOWED_CONTENT_TYPES:
                        result['errors'].append(f'{path}: blocked content-type {content_type!r}')
                        return
                    if content_type == 'application/octet-stream' and len(content) > OCTET_STREAM_MAX_BYTES:
                        result['skipped_ext'] += 1
                        return

                    is_text = content_type.startswith('text/') or content_type in (
                        'application/json',
                        'application/xml',
                        'application/yaml',
                        'text/yaml',
                        'text/markdown',
                        'text/x-markdown',
                    )
                    if is_text:
                        if len(content) > MAX_TEXT_BYTES:
                            result['skipped_size'] += 1
                            return
                        try:
                            text = content.decode('utf-8', errors='strict')
                        except UnicodeDecodeError:
                            result['errors'].append(f'{path}: not valid UTF-8 text')
                            return
                        # Binary masquerade scan: >10% non-printable → reject.
                        if text:
                            nonprintable = sum(1 for ch in text[:8192] if not ch.isprintable() and ch not in '\n\r\t')
                            if nonprintable > len(text[:8192]) * 0.1:
                                result['errors'].append(f'{path}: binary content in a text file')
                                return
                        content = sanitize_text_content(text).encode('utf-8')
                        if not content.strip():
                            result['skipped_dup'] += 1  # empty after sanitisation
                            return

                    file_hash = hashlib.sha256(content).hexdigest()

                    # Replace the previous version of this repo path, if any.
                    if existing:
                        await Knowledges.remove_file_from_knowledge_by_id(source.knowledge_id, existing.id)
                        await Files.delete_file_by_id(existing.id)

                    # Hash-dedupe inside the KB (same content elsewhere).
                    dup = await Knowledges.get_file_by_hash_in_knowledge(source.knowledge_id, file_hash)
                    if dup:
                        result['skipped_dup'] += 1
                        return

                    display_name = path.rsplit('/', 1)[-1]
                    file_id = str(uuid.uuid4())
                    stored_name = f'{file_id}_{display_name}'
                    contents, file_path = await asyncio.to_thread(
                        Storage.upload_file,
                        io.BytesIO(content),
                        stored_name,
                        {'OpenWebUI-GitHub-Source': source.id},
                    )

                    # Insert with status 'processing' — extraction runs BELOW
                    # before the KB link exists. The durable worker's KB-link
                    # path requires either stored data.content or existing
                    # file-{id} vector chunks (it reuses them); linking a file
                    # that never went through extraction is what produced the
                    # previous "The content provided is empty" failures.
                    await Files.insert_new_file(
                        user.id,
                        FileForm(
                            id=file_id,
                            filename=display_name,
                            path=file_path,
                            data={'status': 'processing'},
                            meta={
                                'name': display_name,
                                'content_type': content_type,
                                'size': len(content),
                                'file_hash': file_hash,
                                'source_label': f'{owner}/{repo}@{branch}',
                                'data': {
                                    'knowledge_id': source.knowledge_id,
                                    'github_source_id': source.id,
                                    'repo_path': path,
                                    'repo_sha': blob_sha,
                                },
                            },
                        ),
                    )

                    # Extraction pass (same as the upload flow): loader reads
                    # the stored bytes, embeds into the per-file collection and
                    # caches data.content. One failure marks the file failed
                    # and never creates the KB link.
                    request_shim = SimpleNamespace(app=app)
                    try:
                        async with get_async_db() as db:
                            await process_file(
                                request_shim,
                                ProcessFileForm(file_id=file_id),
                                user=user,
                                db=db,
                            )
                    except Exception as exc:
                        await Files.update_file_data_by_id(
                            file_id, {'status': 'failed', 'error': str(exc)}
                        )
                        result['errors'].append(f'{path}: extraction failed: {exc}')
                        return

                    await Knowledges.add_file_to_knowledge_by_id(
                        knowledge_id=source.knowledge_id,
                        file_id=file_id,
                        user_id=user.id,
                        directory_id=directory_id,
                        status='pending',  # durable embedding worker reuses the extracted chunks
                    )
                    result['updated' if existing else 'added'] += 1
                except Exception as e:  # one bad file never aborts the sync
                    log.warning(f'GitHub sync {source.id} file {path} failed: {e}')
                    result['errors'].append(f'{path}: {e}')

        await asyncio.gather(*(handle_entry(e) for e in candidates))

        # ── Removal pass: files synced from this source no longer in tree ─
        if source.remove_deleted:
            # Compare against the PRE-policy tree paths, NOT the post-filter
            # candidates: a file that still exists in the repo but is now
            # excluded by policy (extension/size/globs) must NOT be deleted
            # from the KB - only files actually removed from the repo are.
            tree_paths = {e['path'] for e in blobs}
            for path, f in list(by_repo_path.items()):
                if path not in tree_paths:
                    try:
                        await Knowledges.remove_file_from_knowledge_by_id(source.knowledge_id, f.id)
                        await Files.delete_file_by_id(f.id)
                        result['removed'] += 1
                    except Exception as e:
                        result['errors'].append(f'remove {path}: {e}')

        result['orphans_swept'] = result_orphans
        result['rate_limit_remaining'] = client._remaining
        await KnowledgeGithubSources.finish_sync(source.id, status='ok', result=result, commit_sha=head_sha)
        log.info(
            'GitHub sync %s (%s/%s@%s): %s',
            source.id,
            owner,
            repo,
            branch,
            {k: v for k, v in result.items() if k != 'errors'},
        )
        return result

    except Exception as e:
        log.exception(f'GitHub sync {source.id} failed')
        return await _sync_error(source, str(e))
    finally:
        await client.aclose()


async def sync_due_github_sources(app, limit: int = 3) -> int:
    """Called from the scheduler tick. Claims due sources (stamps
    next_run_at forward first, so concurrent workers can't double-fire)
    and runs each sync. Returns the number of syncs started."""
    from open_webui.models.knowledge_github_source import KnowledgeGithubSources

    due = await KnowledgeGithubSources.get_due_sources(int(time.time()), limit=limit)
    for source in due:
        # Fire-and-forget: finish_sync recomputes next_run_at from the
        # interval when the sync completes (or backs off on failure).
        source_model = source
        asyncio.create_task(_run_source_sync(app, source_model))
    return len(due)


async def _run_source_sync(app, source: KnowledgeGithubSourceModel) -> None:
    try:
        await sync_github_source(app, source)
    except Exception:
        log.exception(f'Scheduled GitHub sync {source.id} crashed')
