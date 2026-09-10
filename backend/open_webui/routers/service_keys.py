"""Admin API for group-bound service keys.

All endpoints are admin-gated. The full key is returned exactly ONCE on
mint (POST) AND is recoverable later via ``POST /service-keys/{id}/reveal``
(Fernet-encrypted at rest, keyed off ``WEBUI_SECRET_KEY`` — same derivation
as ``utils/valves.py``). Every other surface exposes only ``key_prefix``
(see ``models/service_api_key`` for the format and hashing details).

State model: binary Active/Revoked computed from
``revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())``.
DELETE soft-revokes (stamps ``revoked_at``); expiry is detected lazily at
auth time. PATCHing ``expires_at`` into the future clears ``revoked_at``,
which is how an admin un-expires a key.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from open_webui.events import EVENTS, publish_event
from open_webui.models.groups import Groups
from open_webui.models.service_api_key import (
    ServiceApiKeyMintResponse,
    ServiceApiKeyModel,
    ServiceApiKeyResponse,
    ServiceApiKeys,
    _to_response,
    decrypt_service_key,
    encrypt_service_key,
    generate_service_key,
)
from open_webui.utils.auth import get_admin_user, get_password_hash
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

# Prefix-collision retries at mint time: 62^4 ≈ 14.7M four-char bodies,
# so a collision is vanishingly rare — a couple of retries is plenty.
MAX_MINT_ATTEMPTS = 5


class ServiceKeyCreateForm(BaseModel):
    group_id: str
    name: str
    # epoch seconds; NULL/omitted = infinite
    expires_at: Optional[int] = None
    # ["1.2.3.0/24", ...]; NULL/omitted = any origin
    ip_whitelist: Optional[list[str]] = None


class ServiceKeyUpdateForm(BaseModel):
    """PATCH payload. ``group_id`` deliberately absent — the bound group
    cannot change after mint. Provided-ness is read via
    ``model_fields_set`` so an explicit ``null`` expires_at (back to
    infinite) is distinguishable from "leave unchanged"."""

    name: Optional[str] = None
    expires_at: Optional[int] = None
    ip_whitelist: Optional[list[str]] = None


class ServiceKeyListResponse(BaseModel):
    """Stable wrapped response shape for the list endpoint. Room for
    ``total``/``next_cursor`` later without breaking the frontend
    (which already wraps the array as ``{items: [...]}``)."""

    items: list[ServiceApiKeyResponse]


def _payload_for_event(model: ServiceApiKeyModel) -> dict:
    """Webhook payload — key_id/group_id/name/prefix plus lifecycle fields.
    NEVER includes key material."""
    return {
        'key_id': model.id,
        'group_id': model.group_id,
        'name': model.name,
        'prefix': model.key_prefix,
        'expires_at': model.expires_at,
        'ip_whitelist': model.ip_whitelist,
    }


@router.get('', response_model=ServiceKeyListResponse)
@router.get('/', response_model=ServiceKeyListResponse, include_in_schema=False)
async def list_service_keys(
    query: Optional[str] = Query(None, description='Filter by name / key prefix substring'),
    group_id: Optional[str] = Query(None, description='Restrict to one group'),
    include_revoked: bool = Query(False, description='Include revoked/expired keys'),
    user=Depends(get_admin_user),
):
    rows = await ServiceApiKeys.get_keys(query=query, group_id=group_id, include_revoked=include_revoked)
    return ServiceKeyListResponse(items=[_to_response(model) for model in rows])


@router.post('', response_model=ServiceApiKeyMintResponse)
@router.post('/', response_model=ServiceApiKeyMintResponse, include_in_schema=False)
async def create_service_key(request: Request, form_data: ServiceKeyCreateForm, user=Depends(get_admin_user)):
    """Mint a new group-bound service key.

    The response carries the plaintext key exactly once — it is never
    stored or shown again. Fires the ``service_key.minted`` event (no key
    material in the payload).
    """
    if not form_data.name or not form_data.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name must be a non-empty string')
    if not await Groups.get_group_by_id(form_data.group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Group {form_data.group_id} not found',
        )

    minted = None
    plaintext_key = None
    for _ in range(MAX_MINT_ATTEMPTS):
        plaintext_key, key_prefix = generate_service_key()
        key_hash = await get_password_hash(plaintext_key)  # argon2id / bcrypt, as configured
        # Fernet-encrypt for at-rest storage so the admin can re-reveal
        # the plaintext later via the /reveal endpoint. Cached Fernet
        # instance; see utils/valves.py for the WEBUI_SECRET_KEY
        # derivation.
        encrypted_key = encrypt_service_key(plaintext_key)
        minted = await ServiceApiKeys.insert_service_api_key(
            group_id=form_data.group_id,
            name=form_data.name.strip(),
            key_prefix=key_prefix,
            key_hash=key_hash,
            encrypted_key=encrypted_key,
            created_by=user.id,
            expires_at=form_data.expires_at,
            ip_whitelist=form_data.ip_whitelist,
        )
        if minted is not None:
            break
    if minted is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Could not mint a unique service key; please retry',
        )

    assert plaintext_key is not None, 'mint loop succeeded but produced no key'
    response = ServiceApiKeyMintResponse(
        **_to_response(minted).model_dump(), key=plaintext_key, plaintext=plaintext_key
    )
    await publish_event(
        request,
        EVENTS.SERVICE_KEY_MINTED,
        actor=user,
        subject_id=minted.id,
        subject_type='service_key',
        data={
            **_payload_for_event(minted),
            'created_by': minted.created_by,
            'created_at': minted.created_at,
        },
    )
    return response


@router.post('/{id}/reveal', response_model=dict)
async def reveal_service_key_plaintext(request: Request, id: str, user=Depends(get_admin_user)):
    """Return the full plaintext of an existing service key.

    The plaintext is Fernet-encrypted at rest; the reveal endpoint
    decrypts on-demand. Use sparingly — anyone with the response body
    has the bearer credential. Prefer re-minting over repeated reveal.

    Errors:
      - 404 if the key doesn't exist OR if it was minted before the
        ``encrypted_key`` column existed (legacy record; not recoverable).
      - 500 with a clear message if Fernet decryption fails — that means
        ``WEBUI_SECRET_KEY`` has rotated since the key was minted; the
        only recovery is to mint a new key.
    """
    model = await ServiceApiKeys.get_key_by_id(id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')
    if model.encrypted_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Plaintext not recoverable for this key (legacy record minted before encrypted_key column). Re-mint to continue.',
        )
    try:
        plaintext = decrypt_service_key(model.encrypted_key)
    except InvalidToken:
        # WEBUI_SECRET_KEY rotated since this key was minted — the
        # Fernet key derivation differs and decryption fails. Loud log
        # so ops can correlate, then a 500 with an actionable message.
        log.error(
            'Failed to decrypt service key %s — likely WEBUI_SECRET_KEY rotated since mint',
            id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Plaintext not recoverable — encryption key has rotated. Re-mint to continue.',
        )

    # Fire a webhook so admin reveal events are auditable alongside
    # mint/revoke/expire. No plaintext in the payload — just the
    # key_id, who revealed it, and when.
    await publish_event(
        request,
        EVENTS.SERVICE_KEY_REVEALED,
        actor=user,
        subject_id=model.id,
        subject_type='service_key',
        data={
            'key_id': model.id,
            'group_id': model.group_id,
            'name': model.name,
            'prefix': model.key_prefix,
            'revealed_at': int(time.time()),
            'revealed_by': user.id,
        },
    )

    return {'plaintext': plaintext, 'prefix': model.key_prefix}


@router.get('/{id}', response_model=ServiceApiKeyResponse)
async def get_service_key_by_id(id: str, user=Depends(get_admin_user)):
    model = await ServiceApiKeys.get_key_by_id(id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')
    return _to_response(model)


@router.patch('/{id}', response_model=ServiceApiKeyResponse)
async def update_service_key_by_id(
    request: Request, id: str, form_data: ServiceKeyUpdateForm, user=Depends(get_admin_user)
):
    """Rename / change expires_at / change ip_whitelist.

    ``group_id`` is immutable after mint (not a field on the form). An
    explicit ``null`` expires_at reverts to infinite; setting a future
    expires_at on an expired key CLEARS revoked_at — that is the
    documented un-expire path (no separate state machine).
    """
    existing = await ServiceApiKeys.get_key_by_id(id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')

    provided = form_data.model_fields_set
    fields: dict = {}

    if 'name' in provided:
        name = (form_data.name or '').strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name must be a non-empty string')
        fields['name'] = name
    if 'expires_at' in provided:
        if form_data.expires_at is not None and form_data.expires_at <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='expires_at must be a positive epoch timestamp or null',
            )
        fields['expires_at'] = form_data.expires_at
    if 'ip_whitelist' in provided:
        entries = [str(entry).strip() for entry in (form_data.ip_whitelist or []) if str(entry).strip()]
        fields['ip_whitelist'] = entries or None

    # The documented un-expire: a (re)supplied future expiry reactivates
    # a lazily-expired key. A manual revoke stays final until undone this way.
    if 'expires_at' in fields:
        now = int(time.time())
        future = fields['expires_at'] is None or fields['expires_at'] > now
        if future and existing.revoked_at is not None:
            # Only auto-clear when the key expired (revoked_at was set by
            # the lazy expiry path); manually revoked keys stay revoked
            # unless the admin explicitly re-enables via expiry edit.
            if existing.expires_at is not None and existing.revoked_at is not None:
                fields['revoked_at'] = None

    updated = await ServiceApiKeys.update_key_by_id(id, fields=fields)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')
    return _to_response(updated)


async def _revoke_service_key(request: Request, id: str, user) -> ServiceApiKeyResponse:
    """Shared soft-revoke: stamps ``revoked_at = now()``. Fires
    ``service_key.revoked`` with reason ``manual`` and the revoking
    admin's id. The row is retained (audit trail + prefix uniqueness)."""
    existing = await ServiceApiKeys.get_key_by_id(id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')
    if existing.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Service key is already revoked',
        )

    updated = await ServiceApiKeys.revoke_key_by_id(id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Service key not found')
    await publish_event(
        request,
        EVENTS.SERVICE_KEY_REVOKED,
        actor=user,
        subject_id=updated.id,
        subject_type='service_key',
        data={
            **_payload_for_event(updated),
            'revoked_at': updated.revoked_at,
            'reason': 'manual',
            'revoked_by': user.id,
        },
    )
    return _to_response(updated)


@router.delete('/{id}', response_model=ServiceApiKeyResponse)
async def revoke_service_key_by_id(request: Request, id: str, user=Depends(get_admin_user)):
    """Soft-revoke via DELETE (spec surface)."""
    return await _revoke_service_key(request, id, user)


@router.post('/{id}/revoke', response_model=dict)
async def revoke_service_key_via_post(request: Request, id: str, user=Depends(get_admin_user)):
    """Soft-revoke via POST — the frontend lane's client contract
    (``$lib/apis/serviceKeys`` calls ``POST /service-keys/{id}/revoke``
    and expects ``{success: boolean}``). The 200 response is just the
    success marker; clients refetch via GET to pick up the updated row.
    """
    await _revoke_service_key(request, id, user)
    return {'success': True}
