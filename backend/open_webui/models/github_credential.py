"""Central GitHub credentials for knowledge sync.

One named credential row per PAT, Fernet-encrypted at rest with the same
``WEBUI_SECRET_KEY`` derivation as ServiceKeys / valves. Multiple knowledge
GitHub sources share one credential; ``last_used_at`` lets the admin see
which tokens are actively needed.

The token is write-only from the API's perspective: the response model
never includes the ciphertext, and there is deliberately no reveal
endpoint (unlike ServiceKeys) — a GitHub PAT is re-obtainable from GitHub
at any time, so there is no operational need to read it back, and
removing the read path removes an entire attack surface.
"""

import hashlib
import logging
import time
import uuid
from base64 import urlsafe_b64encode
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, JSON, Text

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import Base, get_async_db_context

log = logging.getLogger(__name__)

####################
# Crypto (shared derivation with service_api_key / valves)
####################


@lru_cache(maxsize=1)
def _github_fernet() -> Fernet:
    key = WEBUI_SECRET_KEY.encode()
    if len(WEBUI_SECRET_KEY) != 44:
        key = urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(key)


def encrypt_github_token(plaintext: str) -> str:
    return _github_fernet().encrypt(plaintext.encode()).decode()


def decrypt_github_token(ciphertext: str) -> str:
    """Raises ``InvalidToken`` when WEBUI_SECRET_KEY rotated since write."""
    return _github_fernet().decrypt(ciphertext.encode()).decode()


####################
# DB Models
####################


class GithubCredential(Base):
    __tablename__ = 'github_credential'

    id = Column(Text, primary_key=True, unique=True)
    name = Column(Text, nullable=False)
    # Fernet ciphertext of the PAT — never exposed via the API.
    encrypted_token = Column(Text, nullable=False)
    # Last 4 chars of the plaintext, stored at creation so the UI can show
    # "ghp_…ab12" without any ability to read the token back.
    token_suffix = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)
    last_used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_github_credential_created_by', 'created_by'),)


class GithubCredentialModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_suffix: str
    note: Optional[str] = None
    created_by: str
    last_used_at: Optional[int] = None
    created_at: int
    updated_at: int


class GithubCredentialResponse(BaseModel):
    """API shape — never includes token material."""

    id: str
    name: str
    token_suffix: str
    note: Optional[str] = None
    created_by: str
    last_used_at: Optional[int] = None
    created_at: int
    updated_at: int
    source_count: int = 0


class GithubCredentialForm(BaseModel):
    name: str
    token: str
    note: Optional[str] = None


####################
# Table
####################


class GithubCredentialsTable:
    async def insert_credential(self, form_data: GithubCredentialForm, user_id: str) -> Optional[GithubCredentialModel]:
        token = (form_data.token or '').strip()
        if not token:
            return None
        now = int(time.time())
        id = str(uuid.uuid4())
        async with get_async_db_context() as db:
            await db.execute(
                GithubCredential.__table__.insert().values(
                    id=id,
                    name=form_data.name.strip() or 'GitHub token',
                    encrypted_token=encrypt_github_token(token),
                    token_suffix=token[-4:],
                    note=form_data.note,
                    created_by=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            row = await db.get(GithubCredential, id)
            return GithubCredentialModel.model_validate(row) if row else None

    async def get_credential(self, id: str) -> Optional[GithubCredentialModel]:
        async with get_async_db_context() as db:
            row = await db.get(GithubCredential, id)
            return GithubCredentialModel.model_validate(row) if row else None

    async def get_credentials(self) -> list[GithubCredentialModel]:
        async with get_async_db_context() as db:
            from sqlalchemy import select

            rows = (await db.execute(select(GithubCredential).order_by(GithubCredential.created_at))).scalars().all()
            return [GithubCredentialModel.model_validate(r) for r in rows]

    async def delete_credential(self, id: str) -> bool:
        # Refuse to delete while sources still reference it — the UI checks
        # first, but enforce here too so an API caller can't orphan sources.
        from open_webui.models.knowledge_github_source import KnowledgeGithubSources

        if await KnowledgeGithubSources.count_by_credential(id):
            return False
        async with get_async_db_context() as db:
            row = await db.get(GithubCredential, id)
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def mark_used(self, id: str) -> None:
        now = int(time.time())
        async with get_async_db_context() as db:
            await db.execute(
                GithubCredential.__table__.update().where(GithubCredential.id == id).values(last_used_at=now)
            )
            await db.commit()

    async def resolve_token(self, id: str) -> Optional[str]:
        """Decrypt and return the PAT, stamping last_used_at. Admin/sync
        paths only."""
        cred = await self.get_credential(id)
        if not cred:
            return None
        async with get_async_db_context() as db:
            row = await db.get(GithubCredential, id)
            try:
                token = decrypt_github_token(row.encrypted_token)
                await self.mark_used(id)
                return token
            except InvalidToken:
                log.error(
                    'GitHub credential %s undecryptable (WEBUI_SECRET_KEY rotated?) — re-create it',
                    id,
                )
                return None


GithubCredentials = GithubCredentialsTable()
