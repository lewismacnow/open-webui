"""Group-bound service API keys — machine credentials for the public API.

A service key authenticates an external caller against ONE group: the
group's permissions are merged into a synthetic identity (role
``service``), and the key's token usage counts against the group's
existing token cap (``target_type='group'``) — there is deliberately no
per-key cap and no owning user.

Key material handling mirrors the fork's password auth: the full key
(``sk_live_<22-char-base62>``, 32 chars) is returned ONCE at mint time;
only ``key_prefix`` (first 12 chars) is recoverable afterwards. The
stored ``key_hash`` is produced by the same hasher as passwords
(argon2id when configured, else bcrypt — see ``utils/auth.py``), which
is SALTED. A salted hash cannot be looked up by equality, so the lookup
path is: fetch the row by UNIQUE ``key_prefix``, then verify the full
key against ``key_hash`` with ``verify_password``. The ``key_hash``
index mandated by the schema remains as a uniqueness/debug aid.

State model (per user spec): binary Active/Revoked computed from
``revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())``.
Expiry is detected lazily at auth time — the first 401 attempt past
``expires_at`` stamps ``revoked_at = now()``. An admin can un-expire by
PATCHing ``expires_at`` back into the future (revoked_at is cleared by
the PATCH handler when a new future expiry is set).
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Any, Optional

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, JSON, Column, ForeignKey, Index, Text, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# ── Key format ────────────────────────────────────────────────────────────
# ``sk_live_<22-char base62>`` — 32 chars total. base62 keeps the key
# copy-paste friendly while carrying ~131 bits of entropy (62^22).
SERVICE_KEY_PREFIX = 'sk_live_'
SERVICE_KEY_BODY_LENGTH = 22
SERVICE_KEY_PREFIX_LENGTH = 12  # sk_live_ + first 4 body chars, for UI lookup

_BASE62_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'


def generate_service_key() -> tuple[str, str]:
    """Return ``(full_key, key_prefix)`` with crypto-secure randomness.

    ``secrets.choice`` (CSPRNG) — these are bearer credentials.
    """
    body = ''.join(secrets.choice(_BASE62_ALPHABET) for _ in range(SERVICE_KEY_BODY_LENGTH))
    key = f'{SERVICE_KEY_PREFIX}{body}'
    return key, key[:SERVICE_KEY_PREFIX_LENGTH]


class ServiceApiKey(Base):
    """One machine credential bound to a group.

    ``group_id`` carries ON DELETE RESTRICT — a group with live (or even
    revoked) service keys cannot be deleted until the keys are removed,
    preserving the audit trail. ``revoked_at`` is the single source of
    truth for activity; ``expires_at`` is advisory until lazily enforced.
    """

    __tablename__ = 'service_api_key'

    id = Column(Text, primary_key=True, unique=True)
    group_id = Column(Text, ForeignKey('group.id', ondelete='RESTRICT'), nullable=False)
    name = Column(Text, nullable=False)
    key_prefix = Column(Text, unique=True, nullable=False)
    key_hash = Column(Text, nullable=False)
    created_by = Column(Text, ForeignKey('user.id'), nullable=False)
    expires_at = Column(BigInteger, nullable=True)  # epoch; NULL = infinite
    ip_whitelist = Column(JSON, nullable=True)  # ["1.2.3.0/24", ...]; NULL = any
    last_used_at = Column(BigInteger, nullable=True)
    revoked_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_service_api_key_group_id', 'group_id'),
        Index('ix_service_api_key_key_hash', 'key_hash'),
        Index('ix_service_api_key_key_prefix', 'key_prefix'),
        Index('ix_service_api_key_revoked_at', 'revoked_at'),
    )


class ServiceApiKeyModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    name: str
    key_prefix: str
    key_hash: str
    created_by: str
    expires_at: Optional[int] = None
    ip_whitelist: Optional[list[str]] = None
    last_used_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_at: int
    updated_at: int


class ServiceApiKeyResponse(BaseModel):
    """API-facing shape — NEVER includes key material (hash or full key).

    ``key_prefix`` is the schema name; ``prefix`` is the frontend lane's
    client shape (``$lib/apis/serviceKeys``) — both are populated with
    the same value so either consumer can read its preferred name.
    ``ip_whitelist`` normalizes to ``[]`` (never null) per the client type.
    """

    id: str
    group_id: str
    name: str
    key_prefix: str
    prefix: str = ''
    created_by: str
    expires_at: Optional[int] = None
    ip_whitelist: list[str] = Field(default_factory=list)
    last_used_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_at: int
    updated_at: int
    # Computed: revoked_at IS NULL AND (expires_at IS NULL OR > now)
    is_active: bool = False


class ServiceApiKeyMintResponse(ServiceApiKeyResponse):
    """Mint response — the ONLY time the plaintext key is returned.

    ``key`` is the spec's field name; ``plaintext`` is the frontend
    client's — both carry the same one-time value.
    """

    key: str
    plaintext: str = ''


def _to_response(model: ServiceApiKeyModel) -> ServiceApiKeyResponse:
    now = int(time.time())
    return ServiceApiKeyResponse(
        **model.model_dump(exclude={'key_hash', 'ip_whitelist'}),
        prefix=model.key_prefix,
        ip_whitelist=list(model.ip_whitelist or []),
        is_active=model.revoked_at is None and (model.expires_at is None or model.expires_at > now),
    )


class ServiceKeyIdentity(BaseModel):
    """Synthetic identity for a service-key-authenticated caller.

    Duck-types the UserModel surface downstream code touches (``id``,
    ``email``, ``role``, ``name``, ``info``, ``model_dump``) without
    subclassing it: ``UserModel.email`` is a required ``str`` for humans,
    while a service key deliberately has ``email=None`` and a dedicated
    ``service`` role. ``id`` is ``service_key:<row id>`` so downstream
    consumers can detect the shape
    (``utils/token_recorder.resolve_service_key_id``).
    ``permissions`` carries the bound group's permissions merged over the
    default user permissions — the same "most permissive wins" combine
    semantics as ``utils/access_control.get_permissions``.
    """

    id: str
    email: Optional[str] = None
    role: str = 'service'
    name: str
    profile_image_url: Optional[str] = None
    info: Optional[dict] = None
    settings: Optional[dict] = None
    oauth: Optional[dict] = None
    scim: Optional[dict] = None
    variables: dict = Field(default_factory=dict)
    last_active_at: int = 0  # timestamp in epoch
    updated_at: int = 0  # timestamp in epoch
    created_at: int = 0  # timestamp in epoch
    permissions: dict = Field(default_factory=dict)


def _combine_permissions(base: dict, group_permissions: dict) -> dict:
    """Most-permissive-wins merge, mirroring access_control.get_permissions."""

    def combine(current: dict, incoming: dict) -> dict:
        for key, value in incoming.items():
            if isinstance(value, dict):
                current[key] = combine(current.get(key) or {}, value)
            else:
                current[key] = current.get(key) or value  # True > False
        return current

    return combine(dict(base), dict(group_permissions or {}))


def build_service_key_identity(key_model: ServiceApiKeyModel, group) -> ServiceKeyIdentity:
    """Build the synthetic caller identity for a valid service key.

    ``group`` is the bound GroupModel; its ``permissions`` dict is merged
    over the configured default user permissions so the identity inherits
    the group's grants exactly like a human member would.
    """
    # Lazy import — config import is heavyweight (runs migrations).
    from open_webui.config import DEFAULT_USER_PERMISSIONS

    merged = _combine_permissions(DEFAULT_USER_PERMISSIONS or {}, getattr(group, 'permissions', None) or {})
    now = int(time.time())
    return ServiceKeyIdentity(
        id=f'service_key:{key_model.id}',
        email=None,
        role='service',
        name=key_model.name,
        info={
            'type': 'service_key',
            'service_key_id': key_model.id,
            'group_id': key_model.group_id,
        },
        last_active_at=now,
        updated_at=now,
        created_at=now,
        permissions=merged,
    )


class ServiceApiKeyTable:
    async def insert_service_api_key(
        self,
        *,
        group_id: str,
        name: str,
        key_prefix: str,
        key_hash: str,
        created_by: str,
        expires_at: Optional[int] = None,
        ip_whitelist: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[ServiceApiKeyModel]:
        async with get_async_db_context(db) as session:
            now = int(time.time())
            row = ServiceApiKey(
                id=str(uuid.uuid4()),
                group_id=group_id,
                name=name,
                key_prefix=key_prefix,
                key_hash=key_hash,
                created_by=created_by,
                expires_at=expires_at,
                ip_whitelist=ip_whitelist,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # UNIQUE(key_prefix) collision — caller regenerates and retries.
                await session.rollback()
                return None
            return ServiceApiKeyModel.model_validate(row)

    async def get_key_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[ServiceApiKeyModel]:
        async with get_async_db_context(db) as session:
            row = await session.get(ServiceApiKey, id)
            return ServiceApiKeyModel.model_validate(row) if row else None

    async def get_keys(
        self,
        query: Optional[str] = None,
        group_id: Optional[str] = None,
        include_revoked: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> list[ServiceApiKeyModel]:
        async with get_async_db_context(db) as session:
            stmt = select(ServiceApiKey)
            if not include_revoked:
                stmt = stmt.filter(ServiceApiKey.revoked_at.is_(None))
            if query:
                like = f'%{query}%'
                stmt = stmt.filter(or_(ServiceApiKey.name.ilike(like), ServiceApiKey.key_prefix.ilike(like)))
            if group_id:
                stmt = stmt.filter(ServiceApiKey.group_id == group_id)
            stmt = stmt.order_by(ServiceApiKey.created_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            return [ServiceApiKeyModel.model_validate(row) for row in rows]

    async def get_active_by_key_prefix(
        self, key_prefix: str, db: Optional[AsyncSession] = None
    ) -> Optional[ServiceApiKeyModel]:
        """Fetch a not-yet-revoked key row by its unique prefix.

        Auth-time lookup: the caller verifies the FULL key against the
        row's ``key_hash`` (salted — equality lookup impossible).
        """
        async with get_async_db_context(db) as session:
            stmt = select(ServiceApiKey).where(
                and_(ServiceApiKey.key_prefix == key_prefix, ServiceApiKey.revoked_at.is_(None))
            )
            row = (await session.execute(stmt)).scalars().first()
            return ServiceApiKeyModel.model_validate(row) if row else None

    async def update_key_by_id(
        self, id: str, *, fields: dict[str, Any], db: Optional[AsyncSession] = None
    ) -> Optional[ServiceApiKeyModel]:
        if not fields:
            return await self.get_key_by_id(id, db=db)
        async with get_async_db_context(db) as session:
            values = {k: v for k, v in fields.items() if hasattr(ServiceApiKey, k)}
            values['updated_at'] = int(time.time())
            await session.execute(update(ServiceApiKey).where(ServiceApiKey.id == id).values(**values))
            await session.commit()
        return await self.get_key_by_id(id, db=db)

    async def revoke_key_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[ServiceApiKeyModel]:
        return await self.update_key_by_id(id, fields={'revoked_at': int(time.time())}, db=db)

    async def touch_last_used(self, id: str, db: Optional[AsyncSession] = None) -> None:
        """Best-effort last_used_at stamp — never raises to the caller."""
        try:
            async with get_async_db_context(db) as session:
                await session.execute(
                    update(ServiceApiKey).where(ServiceApiKey.id == id).values(last_used_at=int(time.time()))
                )
                await session.commit()
        except Exception:
            log.warning('service key last_used update failed for %s', id, exc_info=True)

    async def count_by_group_id(self, group_id: str, db: Optional[AsyncSession] = None) -> int:
        """Number of service keys (any state) bound to a group — the
        app-level guard backing the schema's ON DELETE RESTRICT."""
        from sqlalchemy import func

        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(func.count()).select_from(ServiceApiKey).where(ServiceApiKey.group_id == group_id)
            )
            return int(result.scalar() or 0)


ServiceApiKeys = ServiceApiKeyTable()
