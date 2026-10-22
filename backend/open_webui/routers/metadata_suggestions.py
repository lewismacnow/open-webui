"""Admin API for LLM-driven metadata improvement.

POST /scan starts a background scan (scope: whole KB / missing-only /
attribute-targeted / single file) and returns the scan row for polling.
Proposals are applied ONLY via the explicit apply endpoints — the model
never writes to files directly.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from open_webui.models.knowledge import Knowledges
from open_webui.models.metadata_proposal import (
    MetadataProposalModel,
    MetadataProposals,
    MetadataScanForm,
    MetadataScanModel,
    MetadataScans,
)
from open_webui.utils.auth import get_admin_user

log = logging.getLogger(__name__)
router = APIRouter()


@router.post('/scan', response_model=MetadataScanModel)
async def start_scan(request: Request, form_data: MetadataScanForm, user=Depends(get_admin_user)):
    from open_webui.utils.metadata_suggest import run_metadata_scan

    knowledge = await Knowledges.get_knowledge_by_id(form_data.knowledge_id)
    if not knowledge:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Knowledge base not found')

    if form_data.mode == 'file' and not form_data.file_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='file_id is required for mode=file')
    if form_data.mode == 'attributes' and not form_data.attributes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='attributes is required for mode=attributes'
        )

    # One running scan per KB — prevents accidental double-runs.
    running = await MetadataScans.get_running_scan(form_data.knowledge_id)
    if running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'A scan is already running for this knowledge base (started {running.started_at})',
        )

    scan = await MetadataScans.insert_scan(form_data, user.id)
    asyncio.create_task(run_metadata_scan(request.app, scan))
    return scan


@router.get('/scans', response_model=list[MetadataScanModel])
async def list_scans(knowledge_id: Optional[str] = None, limit: int = 20, user=Depends(get_admin_user)):
    return await MetadataScans.get_scans(knowledge_id=knowledge_id, limit=limit)


@router.post('/scans/{id}/cancel')
async def cancel_scan(id: str, user=Depends(get_admin_user)):
    """Request cancellation of a running scan. The worker checks the status
    between files and exits early; files already processed keep their
    proposals."""
    scan = await MetadataScans.get_scan(id)
    if not scan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Scan not found')
    if scan.status != 'running':
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f'Scan is {scan.status}, not running')
    await MetadataScans.finish_scan(id, 'cancelled')
    return {'status': True}


@router.get('/scans/{id}', response_model=MetadataScanModel)
async def get_scan(id: str, user=Depends(get_admin_user)):
    scan = await MetadataScans.get_scan(id)
    if not scan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Scan not found')
    return scan


@router.get('/proposals', response_model=list[MetadataProposalModel])
async def list_proposals(
    knowledge_id: Optional[str] = None,
    scan_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
    user=Depends(get_admin_user),
):
    """Audit trail of what the enrichment changed.

    The approval queue is gone: every proposal is applied automatically by
    the scan, so this endpoint is a history view. Pass no status to get
    everything, or 'applied' / 'no_change' / 'failed' to filter.
    """
    return await MetadataProposals.get_proposals(
        knowledge_id=knowledge_id, scan_id=scan_id, status=status, limit=limit
    )


@router.get('/files')
async def list_enrichment_files(
    knowledge_id: str,
    include_ignored: bool = True,
    user=Depends(get_admin_user),
):
    """List the knowledge base files with their enrichment state.

    Used by the Metadata Enrichment panel to show which files are ignored
    (and let the admin un-ignore them).
    """
    files = await Knowledges.get_files_by_id(knowledge_id)
    out = []
    for f in files:
        meta = f.meta or {}
        ignored = bool(meta.get('ignored'))
        if ignored and not include_ignored:
            continue
        out.append(
            {
                'id': f.id,
                'filename': f.filename,
                'title': meta.get('title'),
                'ignored': ignored,
                'ignored_reason': meta.get('ignored_reason'),
                'ignored_at': meta.get('ignored_at'),
                'improved_at': meta.get('metadata_improved_at'),
                'size': meta.get('size'),
                'content_type': meta.get('content_type'),
            }
        )
    return out


@router.post('/files/{id}/ignore')
async def ignore_file(
    id: str,
    body: Optional[dict] = Body(default=None),
    user=Depends(get_admin_user),
):
    """Mark a file as ignored.

    An ignored file is excluded from knowledge queries (its vectors are
    removed) but stays in the knowledge base, so it can be un-ignored
    later. Unlike a delete, a GitHub-synced file is not re-added by the
    next sync - the flag travels with the file metadata.
    """
    from open_webui.models.files import Files as _Files
    from open_webui.utils.metadata_suggest import set_file_ignored

    file = await _Files.get_file_by_id(id)
    if not file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='File not found')
    reason = (body or {}).get('reason') or 'manual'
    ok = await set_file_ignored(file, reason=reason)
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Could not ignore file')
    return {'status': True}


@router.post('/files/{id}/unignore')
async def unignore_file(id: str, user=Depends(get_admin_user)):
    """Un-ignore a file: it is re-embedded and becomes queryable again."""
    from open_webui.models.files import Files as _Files
    from open_webui.utils.metadata_suggest import clear_file_ignored

    file = await _Files.get_file_by_id(id)
    if not file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='File not found')
    ok = await clear_file_ignored(file)
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Could not un-ignore file')
    return {'status': True}
