"""Durable knowledge-base embedding worker.

Ingestion is split into two phases:

  1. Upload (fast)  — files are stored and a ``knowledge_file`` link row is
     created immediately with ``status='pending'``. No embedding happens on the
     request path, so the user never has to keep a session open.
  2. Embed (durable) — this worker drains pending links, embeds each file into
     its knowledge-base collection, and flips the link to ``completed`` (or
     ``failed`` + error).

Because the queue *is* the ``knowledge_file.status`` column in the database,
work survives a server restart: on startup the worker simply keeps draining
whatever is still ``pending``. The claim is atomic (UPDATE ... WHERE
status='pending'), so it is safe to run one of these per process even across
replicas.
"""

import asyncio
import logging
import os
import random
import time
from types import SimpleNamespace

from open_webui.internal.db import get_async_db
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.routers.retrieval import ProcessFileForm, process_file

log = logging.getLogger(__name__)

# How long to wait between polls when the queue is empty. Kept short because
# the loop already sleeps only when there is nothing to do.
EMBEDDING_WORKER_IDLE_INTERVAL = float(os.getenv('EMBEDDING_WORKER_IDLE_INTERVAL', '5'))

# A file uploaded toward a KB whose extraction never finished (status stuck at
# pending/processing, never linked) is recovered by re-driving extraction. Only
# files untouched for this many seconds are recovered, so a live upload still
# being processed is never disturbed.
EMBEDDING_RECOVERY_AGE = int(os.getenv('EMBEDDING_RECOVERY_AGE', '600'))
# Minimum gap between automatic recovery sweeps.
EMBEDDING_RECOVERY_INTERVAL = int(os.getenv('EMBEDDING_RECOVERY_INTERVAL', '300'))

# How many times a file may fail embedding before it is abandoned (removed
# from the knowledge base and the file table). Between attempts the file is
# re-queued at the END of the pending queue, so a transient provider error
# (rate limit, timeout, provider restart) is retried after the rest of the
# queue has drained instead of blocking it.
MAX_EMBED_ATTEMPTS = int(os.getenv('EMBEDDING_MAX_ATTEMPTS', '3'))
# How often to sweep links left in 'failed' (e.g. by a worker crash or a
# failure recorded before the retry policy existed) back into the queue.
FAILED_REQUEUE_INTERVAL = int(os.getenv('EMBEDDING_FAILED_REQUEUE_INTERVAL', '300'))


async def recover_stuck_extractions(app, knowledge_id: str | None = None, ignore_age: bool = False) -> int:
    """Re-drive files orphaned in the extraction phase.

    For each stuck file: re-run extraction from its stored bytes, then create a
    'pending' link so the embedding worker finishes the job. On failure the file
    is marked 'failed' so it surfaces in the UI instead of spinning forever.

    Returns the number of files re-driven. Scope to one KB and/or ignore the age
    threshold for the manual trigger.
    """
    cutoff = None if ignore_age else int(time.time()) - EMBEDDING_RECOVERY_AGE
    stuck = await Files.get_stuck_knowledge_files(cutoff=cutoff, knowledge_id=knowledge_id)
    if not stuck:
        return 0

    log.info(f'Recovering {len(stuck)} stuck extraction(s)')
    request = SimpleNamespace(app=app)
    recovered = 0

    for file in stuck:
        kb_id = (file.meta or {}).get('data', {}).get('knowledge_id')
        if not kb_id:
            continue
        user = await Users.get_user_by_id(file.user_id)
        if not user:
            await Files.update_file_data_by_id(file.id, {'status': 'failed', 'error': 'Owner user not found'})
            continue

        # Claim: stamping updated_at (now) moves the row past the age cutoff so a
        # concurrent/next sweep won't pick it again while we work on it.
        await Files.update_file_data_by_id(file.id, {'status': 'processing'})
        try:
            async with get_async_db() as db:
                await process_file(request, ProcessFileForm(file_id=file.id), user=user, db=db)
            if not await Knowledges.has_file(kb_id, file.id):
                await Knowledges.add_file_to_knowledge_by_id(
                    knowledge_id=kb_id,
                    file_id=file.id,
                    user_id=user.id,
                    directory_id=(file.meta or {}).get('data', {}).get('directory_id'),
                    status='pending',
                )
            recovered += 1
        except Exception as e:
            log.warning(f'Recovery failed for stuck file {file.id}: {e}')
            detail = getattr(e, 'detail', None) or str(e)
            await Files.update_file_data_by_id(file.id, {'status': 'failed', 'error': str(detail)})

    return recovered


async def _embed_one(app, link) -> None:
    """Embed a single claimed link into its knowledge-base collection."""
    # process_file only ever reads ``request.app.state`` — a light shim avoids
    # constructing a full ASGI request.
    request = SimpleNamespace(app=app)

    user = await Users.get_user_by_id(link.user_id)
    if not user:
        # Permanent failure - the owner is gone, retrying cannot help.
        await Knowledges.set_embedding_status(
            link.knowledge_id,
            link.file_id,
            'failed',
            error='Owner user not found',
        )
        await _remove_failed_file(link)
        return

    file = await Files.get_file_by_id(link.file_id)
    if not file:
        # Permanent failure - the file row is gone.
        await Knowledges.set_embedding_status(
            link.knowledge_id,
            link.file_id,
            'failed',
            error='File not found',
        )
        await _remove_failed_file(link)
        return

    try:
        # Link-first ingestion (GitHub sync, durable upload path): a linked
        # file may not have gone through extraction yet. The KB-copy branch
        # of process_file reuses file-{id} chunks and raises
        # EMPTY_CONTENT when neither chunks nor stored data.content exist -
        # so run the extraction pass first whenever the file is not marked
        # completed, then do the KB copy.
        async with get_async_db() as db:
            if (file.data or {}).get('status') != 'completed':
                await process_file(
                    request,
                    ProcessFileForm(file_id=link.file_id),
                    user=user,
                    db=db,
                )
            # process_file forwards `db` to its model calls, so it must receive a
            # real session (its default is a FastAPI Depends sentinel).
            await process_file(
                request,
                ProcessFileForm(file_id=link.file_id, collection_name=link.knowledge_id),
                user=user,
                db=db,
            )
        await Knowledges.set_embedding_status(link.knowledge_id, link.file_id, 'completed')
    except Exception as e:
        detail = getattr(e, 'detail', None) or str(e)
        # A duplicate-content conflict is permanent: the same bytes are
        # already in this knowledge collection under another file, so
        # re-adding them can never succeed. Retrying is pointless - the
        # duplicate file is removed instead.
        if 'duplicate content' in str(detail).lower():
            log.warning(
                'Embedding skipped for file %s in KB %s: %s - removing the duplicate file',
                link.file_id,
                link.knowledge_id,
                detail,
            )
            await Knowledges.set_embedding_status(link.knowledge_id, link.file_id, 'failed', error=str(detail))
            await _remove_failed_file(link)
            return
        outcome = await Knowledges.record_embedding_failure(
            link.knowledge_id,
            link.file_id,
            error=str(detail),
            max_attempts=MAX_EMBED_ATTEMPTS,
        )
        if outcome == 'requeued':
            log.warning(
                'Embedding failed for file %s in KB %s: %s - requeued at the end of the queue '
                'for another attempt (max %d)',
                link.file_id,
                link.knowledge_id,
                detail,
                MAX_EMBED_ATTEMPTS,
            )
            return
        if outcome == 'abandoned':
            log.warning(
                'Embedding failed for file %s in KB %s after %d attempts: %s - removing the file',
                link.file_id,
                link.knowledge_id,
                MAX_EMBED_ATTEMPTS,
                detail,
            )
            await _remove_failed_file(link)
            return
        log.warning(f'Embedding failed for file {link.file_id} in KB {link.knowledge_id}: {e}')


async def _remove_failed_file(link) -> None:
    """Remove a file that exhausted its embedding attempts.

    Deletes the file's vector chunks (per-file collection + the KB
    collection), the knowledge_file link and the file row, so the file
    disappears from the knowledge base instead of being retried forever.
    Every step is best-effort: a failure here must not kill the worker
    loop.
    """
    file_id = link.file_id
    knowledge_id = link.knowledge_id
    try:
        from open_webui.retrieval.vector import ASYNC_VECTOR_DB_CLIENT

        file_collection = f'file-{file_id}'
        try:
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
        except Exception as exc:
            log.debug('Could not delete collection %s: %s', file_collection, exc)
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file_id})
        except Exception as exc:
            log.debug('Could not delete chunks of %s in %s: %s', file_id, knowledge_id, exc)
    except Exception as exc:
        log.debug('Vector cleanup for %s failed: %s', file_id, exc)

    try:
        await Knowledges.remove_file_from_knowledge_by_id(knowledge_id, file_id)
    except Exception as exc:
        log.warning('Could not remove link for file %s: %s', file_id, exc)
    try:
        await Files.delete_file_by_id(file_id)
    except Exception as exc:
        log.warning('Could not delete file %s: %s', file_id, exc)


async def recover_failed_embeddings(app) -> int:
    """Re-queue links left in 'failed' whose attempt budget is not spent.

    Files that failed before the retry policy existed (legacy rows have
    attempts IS NULL), or that failed while the worker was down, are put
    back into the queue. Files that already exhausted their attempts are
    left alone - the worker removes them on the next failure handling.

    Returns the number of links re-queued.
    """
    try:
        requeued = await Knowledges.requeue_failed_embeddings(max_attempts=MAX_EMBED_ATTEMPTS)
    except Exception:
        log.exception('Embedding worker: failed to re-queue failed embeddings')
        return 0
    if requeued:
        log.info('Re-queued %d failed embedding(s) for another attempt', requeued)
    return requeued


async def embedding_worker_loop(app) -> None:
    """Continuously drain pending knowledge-base embeddings."""
    log.info(f'Embedding worker started (idle interval: {EMBEDDING_WORKER_IDLE_INTERVAL}s)')

    # Recover links left 'processing' by a previous run that was killed
    # mid-embed — without this they would never be re-driven.
    try:
        recovered = await Knowledges.requeue_stale_processing_embeddings()
        if recovered:
            log.info(f'Re-queued {recovered} stale processing embedding(s) from a previous run')
    except Exception:
        log.exception('Embedding worker: failed to recover stale processing rows')

    # Recover extraction-phase orphans on startup, then periodically.
    last_recovery = 0.0
    last_failed_requeue = 0.0

    while True:
        try:
            # Periodic recovery sweep when due (also runs immediately on first
            # iteration since last_recovery starts at 0).
            if time.time() - last_recovery >= EMBEDDING_RECOVERY_INTERVAL:
                last_recovery = time.time()
                try:
                    recovered = await recover_stuck_extractions(app)
                    if recovered:
                        log.info(f'Recovered {recovered} stuck extraction(s)')
                except Exception:
                    log.exception('Embedding worker: stuck-extraction recovery failed')

            # Re-queue files whose embedding failed (legacy rows, or a
            # failure recorded while the worker was down) so they get
            # another attempt. Files that already used up their attempt
            # budget are left alone.
            if time.time() - last_failed_requeue >= FAILED_REQUEUE_INTERVAL:
                last_failed_requeue = time.time()
                try:
                    await recover_failed_embeddings(app)
                except Exception:
                    log.exception('Embedding worker: failed-embedding re-queue failed')

            link = await Knowledges.claim_next_pending_embedding()
            if link is None:
                # Nothing to do (or lost a claim race) — back off briefly.
                await asyncio.sleep(EMBEDDING_WORKER_IDLE_INTERVAL + random.uniform(0, 1))
                continue

            await _embed_one(app, link)
            # Immediately loop to grab the next item without sleeping.
        except Exception:
            log.exception('Embedding worker error')
            await asyncio.sleep(EMBEDDING_WORKER_IDLE_INTERVAL)
