"""Admin API for LLM-driven metadata improvement.

POST /scan starts a background scan (scope: whole KB / missing-only /
attribute-targeted / single file) and returns the scan row for polling.
Proposals are applied ONLY via the explicit apply endpoints — the model
never writes to files directly.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.models.knowledge import Knowledges
from open_webui.models.metadata_proposal import (
    MetadataProposalModel,
    MetadataProposals,
    MetadataScanForm,
    MetadataScanModel,
    MetadataScans,
)
from open_webui.utils.auth import get_admin_user
from open_webui.utils.metadata_suggest import apply_proposal

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
    status_filter: Optional[str] = 'pending',
    limit: int = 200,
    user=Depends(get_admin_user),
):
    return await MetadataProposals.get_proposals(
        knowledge_id=knowledge_id, scan_id=scan_id, status=status_filter, limit=limit
    )


@router.post('/proposals/{id}/apply')
async def apply_proposal_by_id(id: str, user=Depends(get_admin_user)):
    proposal = await MetadataProposals.get_proposal(id)
    if not proposal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Proposal not found')
    if proposal.status != 'pending':
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f'Proposal already {proposal.status}')
    try:
        await apply_proposal(proposal)
    except ValueError as e:
        raise HTTPException(status.HTTP_410_GONE, detail=str(e))
    await MetadataProposals.set_status(id, 'applied', applied_by=user.id)
    return {'status': True}


@router.post('/proposals/{id}/dismiss')
async def dismiss_proposal_by_id(id: str, user=Depends(get_admin_user)):
    proposal = await MetadataProposals.get_proposal(id)
    if not proposal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Proposal not found')
    if proposal.status != 'pending':
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f'Proposal already {proposal.status}')
    await MetadataProposals.set_status(id, 'dismissed')
    return {'status': True}


@router.post('/proposals/apply-all')
async def apply_all_proposals(knowledge_id: str, scan_id: Optional[str] = None, user=Depends(get_admin_user)):
    """Bulk-apply every pending proposal in a KB (optionally one scan)."""
    pending = await MetadataProposals.get_proposals(
        knowledge_id=knowledge_id, scan_id=scan_id, status='pending', limit=10000
    )
    applied, skipped = 0, []
    for proposal in pending:
        try:
            await apply_proposal(proposal)
            await MetadataProposals.set_status(proposal.id, 'applied', applied_by=user.id)
            applied += 1
        except ValueError as e:
            skipped.append(f'{proposal.id}: {e}')
            await MetadataProposals.set_status(proposal.id, 'dismissed')
    return {'status': True, 'applied': applied, 'skipped': skipped}
