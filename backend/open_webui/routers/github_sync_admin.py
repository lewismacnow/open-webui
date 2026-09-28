"""Admin API for GitHub sync sources and credentials.

All routes are admin-only. The sync itself is entirely read-only against
GitHub (GETs pinned to api.github.com / raw.githubusercontent.com).
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.models.github_credential import (
    GithubCredentialForm,
    GithubCredentialResponse,
    GithubCredentials,
)
from open_webui.models.knowledge import Knowledges
from open_webui.models.knowledge_github_source import (
    KnowledgeGithubSourceForm,
    KnowledgeGithubSourceModel,
    KnowledgeGithubSourceUpdateForm,
    KnowledgeGithubSources,
)
from open_webui.utils.auth import get_admin_user

log = logging.getLogger(__name__)
router = APIRouter()


def _to_response(source: KnowledgeGithubSourceModel, knowledge_name: Optional[str] = None) -> dict:
    data = source.model_dump()
    data['repo_full_name'] = f'{source.repo_owner}/{source.repo_name}'
    data['knowledge_name'] = knowledge_name
    return data


# ── Credentials ───────────────────────────────────────────────────────────


@router.post('/credentials', response_model=GithubCredentialResponse)
async def create_credential(form_data: GithubCredentialForm, user=Depends(get_admin_user)):
    token = (form_data.token or '').strip()
    if not token.startswith(('ghp_', 'github_pat_', 'gho_')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Token must be a GitHub PAT (ghp_… / github_pat_… / gho_…)',
        )
    cred = await GithubCredentials.insert_credential(form_data, user.id)
    if not cred:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Could not save token')
    return cred


@router.get('/credentials', response_model=list[GithubCredentialResponse])
async def list_credentials(user=Depends(get_admin_user)):
    creds = await GithubCredentials.get_credentials()
    out = []
    for c in creds:
        d = c.model_dump()
        d['source_count'] = await KnowledgeGithubSources.count_by_credential(c.id)
        out.append(d)
    return out


@router.delete('/credentials/{id}')
async def delete_credential(id: str, user=Depends(get_admin_user)):
    deleted = await GithubCredentials.delete_credential(id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Credential is still referenced by a sync source — remove the source first',
        )
    return {'status': True}


# ── Sources ───────────────────────────────────────────────────────────────


async def _validate_knowledge(knowledge_id: str, user) -> None:
    knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Knowledge base not found')
    if knowledge.user_id != user.id and user.role != 'admin':
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail='You do not own this knowledge base')


@router.post('/sources')
async def create_source(form_data: KnowledgeGithubSourceForm, user=Depends(get_admin_user)):
    await _validate_knowledge(form_data.knowledge_id, user)
    source = await KnowledgeGithubSources.insert_source(form_data, user.id)
    if not source:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Could not create source')
    return _to_response(source)


@router.get('/sources')
async def list_sources(user=Depends(get_admin_user)):
    out = []
    for source in await KnowledgeGithubSources.get_sources():
        kb = await Knowledges.get_knowledge_by_id(source.knowledge_id)
        out.append(_to_response(source, kb.name if kb else None))
    return out


@router.get('/sources/{id}')
async def get_source(id: str, user=Depends(get_admin_user)):
    source = await KnowledgeGithubSources.get_source(id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Source not found')
    kb = await Knowledges.get_knowledge_by_id(source.knowledge_id)
    return _to_response(source, kb.name if kb else None)


@router.patch('/sources/{id}')
async def update_source(id: str, form_data: KnowledgeGithubSourceUpdateForm, user=Depends(get_admin_user)):
    source = await KnowledgeGithubSources.get_source(id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Source not found')
    await _validate_knowledge(source.knowledge_id, user)
    updated = await KnowledgeGithubSources.update_source(id, form_data)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Source not found')
    return _to_response(updated)


@router.delete('/sources/{id}')
async def delete_source(
    id: str,
    remove_files: bool = False,
    user=Depends(get_admin_user),
):
    """Delete a source. ``remove_files=true`` also deletes the files the
    source synced into the KB; otherwise they stay as ordinary files."""
    from open_webui.models.files import Files

    source = await KnowledgeGithubSources.get_source(id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Source not found')
    await _validate_knowledge(source.knowledge_id, user)

    removed = 0
    if remove_files:
        for f in await Knowledges.get_files_by_id(source.knowledge_id):
            meta = (f.meta or {}).get('data') or {}
            if meta.get('github_source_id') == id:
                await Knowledges.remove_file_from_knowledge_by_id(source.knowledge_id, f.id)
                await Files.delete_file_by_id(f.id)
                removed += 1

    await KnowledgeGithubSources.delete_source(id)
    return {'status': True, 'files_removed': removed}


@router.post('/knowledge/{knowledge_id}/prune-failed')
async def prune_failed_files(knowledge_id: str, user=Depends(get_admin_user)):
    """Remove files whose processing failed (empty extraction, embed errors)
    from a knowledge base — deletes the KB link AND the stored file. Returns
    the removed count and the per-file reasons."""
    from open_webui.models.files import Files

    await _validate_knowledge(knowledge_id, user)

    removed, details = 0, []
    for f in await Knowledges.get_files_by_id(knowledge_id):
        data = f.data or {}
        status = data.get('status')
        error = data.get('error')
        failed = status == 'failed' or (error and status != 'completed')
        if not failed:
            continue
        reason = str(error) if error else f'status={status}'
        try:
            await Knowledges.remove_file_from_knowledge_by_id(knowledge_id, f.id)
            await Files.delete_file_by_id(f.id)
            removed += 1
        except Exception as e:
            details.append(f'{f.filename}: prune failed: {e}')
        else:
            details.append(f'{f.filename}: {reason[:120]}')
    return {'status': True, 'removed': removed, 'details': details}


@router.post('/sources/{id}/sync')
async def sync_source_now(id: str, request: Request, user=Depends(get_admin_user)):
    """Trigger a sync immediately (does not reset the schedule)."""
    from open_webui.retrieval.github_sync import sync_github_source

    source = await KnowledgeGithubSources.get_source(id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Source not found')
    await _validate_knowledge(source.knowledge_id, user)
    result = await sync_github_source(request.app, source)
    return result
