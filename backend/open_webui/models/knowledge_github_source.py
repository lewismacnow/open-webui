"""Knowledge GitHub sync sources.

One row = one (repo, branch, directory) triple bound to a knowledge base,
with scheduling + last-run bookkeeping for the sync worker
(``retrieval/github_sync.py``). Content is ALWAYS read-only from GitHub's
perspective; the sync only ever performs GETs against
``api.github.com`` / ``raw.githubusercontent.com``.
"""

import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, JSON, Text

from open_webui.internal.db import Base, get_async_db_context

log = logging.getLogger(__name__)

# Never sync faster than this — protects the instance's rate limit.
MIN_SYNC_INTERVAL_SECONDS = 300
DEFAULT_SYNC_INTERVAL_SECONDS = 86400  # daily


class KnowledgeGithubSource(Base):
    __tablename__ = 'knowledge_github_source'

    id = Column(Text, primary_key=True, unique=True)
    knowledge_id = Column(Text, nullable=False)  # FK enforced at app level
    user_id = Column(Text, nullable=False)  # creating admin

    repo_owner = Column(Text, nullable=False)
    repo_name = Column(Text, nullable=False)
    branch = Column(Text, nullable=False)
    directory_path = Column(Text, nullable=False, default='')  # '' = repo root
    credential_id = Column(Text, nullable=True)  # NULL = unauthenticated

    # Sync policy — deny entries are ALWAYS unioned with the hardcoded
    # security deny-list in retrieval/github_sync.py; explicit deny wins.
    include_globs = Column(JSON, nullable=True)  # list[str] | None = all
    exclude_globs = Column(JSON, nullable=True)  # list[str] | None
    max_file_bytes = Column(BigInteger, nullable=False, default=5 * 1024 * 1024)
    max_files = Column(BigInteger, nullable=False, default=1000)
    remove_deleted = Column(BigInteger, nullable=False, default=1)  # bool as int
    # Per-source extension allow-list override. NULL = use admin default
    # (chat.api_tools.allowed_categories-style key: rag.github.allowed_extensions),
    # falling back to SYNC_ALLOWED_EXTENSIONS.
    allowed_extensions = Column(JSON, nullable=True)

    # Schedule — interval only (croniter-free). NULL disables scheduling;
    # manual "sync now" still works.
    interval_seconds = Column(BigInteger, nullable=True)
    next_run_at = Column(BigInteger, nullable=True)
    enabled = Column(BigInteger, nullable=False, default=1)

    # Bookkeeping
    last_commit_sha = Column(Text, nullable=True)
    last_sync_at = Column(BigInteger, nullable=True)
    last_sync_status = Column(Text, nullable=True)  # 'ok' | 'error'
    last_sync_result = Column(JSON, nullable=True)  # counts envelope
    consecutive_failures = Column(BigInteger, nullable=False, default=0)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_kgs_knowledge_id', 'knowledge_id'),
        Index('ix_kgs_next_run_at', 'next_run_at'),
        Index('ix_kgs_credential_id', 'credential_id'),
    )


class KnowledgeGithubSourceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    user_id: str
    repo_owner: str
    repo_name: str
    branch: str
    directory_path: str = ''
    credential_id: Optional[str] = None
    include_globs: Optional[list[str]] = None
    exclude_globs: Optional[list[str]] = None
    max_file_bytes: int = 5 * 1024 * 1024
    max_files: int = 1000
    remove_deleted: bool = True
    allowed_extensions: Optional[list[str]] = None
    interval_seconds: Optional[int] = None
    next_run_at: Optional[int] = None
    enabled: bool = True
    last_commit_sha: Optional[str] = None
    last_sync_at: Optional[int] = None
    last_sync_status: Optional[str] = None
    last_sync_result: Optional[dict] = None
    consecutive_failures: int = 0
    created_at: int
    updated_at: int


class KnowledgeGithubSourceForm(BaseModel):
    knowledge_id: str
    repo_owner: str
    repo_name: str
    branch: str = 'main'
    directory_path: str = ''
    credential_id: Optional[str] = None
    include_globs: Optional[list[str]] = None
    exclude_globs: Optional[list[str]] = None
    max_file_bytes: int = 5 * 1024 * 1024
    max_files: int = 1000
    remove_deleted: bool = True
    interval_seconds: Optional[int] = DEFAULT_SYNC_INTERVAL_SECONDS
    enabled: bool = True
    allowed_extensions: Optional[list[str]] = None


class KnowledgeGithubSourceUpdateForm(BaseModel):
    branch: Optional[str] = None
    directory_path: Optional[str] = None
    credential_id: Optional[str] = None
    include_globs: Optional[list[str]] = None
    exclude_globs: Optional[list[str]] = None
    max_file_bytes: Optional[int] = None
    max_files: Optional[int] = None
    remove_deleted: Optional[bool] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    allowed_extensions: Optional[list[str]] = None


def _compute_next_run(interval_seconds: Optional[int], now: Optional[int] = None) -> Optional[int]:
    if not interval_seconds:
        return None
    interval = max(int(interval_seconds), MIN_SYNC_INTERVAL_SECONDS)
    return (now or int(time.time())) + interval


class KnowledgeGithubSourcesTable:
    async def insert_source(
        self, form_data: KnowledgeGithubSourceForm, user_id: str
    ) -> Optional[KnowledgeGithubSourceModel]:
        now = int(time.time())
        id = str(uuid.uuid4())
        async with get_async_db_context() as db:
            await db.execute(
                KnowledgeGithubSource.__table__.insert().values(
                    id=id,
                    knowledge_id=form_data.knowledge_id,
                    user_id=user_id,
                    repo_owner=form_data.repo_owner.strip(),
                    repo_name=form_data.repo_name.strip(),
                    branch=form_data.branch.strip() or 'main',
                    directory_path=(form_data.directory_path or '').strip().strip('/'),
                    credential_id=form_data.credential_id,
                    include_globs=form_data.include_globs,
                    exclude_globs=form_data.exclude_globs,
                    max_file_bytes=max(int(form_data.max_file_bytes), 1024),
                    max_files=max(int(form_data.max_files), 1),
                    remove_deleted=1 if form_data.remove_deleted else 0,
                    interval_seconds=form_data.interval_seconds,
                    next_run_at=_compute_next_run(form_data.interval_seconds, now),
                    enabled=1 if form_data.enabled else 0,
                    allowed_extensions=form_data.allowed_extensions,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            row = await db.get(KnowledgeGithubSource, id)
            return KnowledgeGithubSourceModel.model_validate(row) if row else None

    async def get_source(self, id: str) -> Optional[KnowledgeGithubSourceModel]:
        async with get_async_db_context() as db:
            row = await db.get(KnowledgeGithubSource, id)
            return KnowledgeGithubSourceModel.model_validate(row) if row else None

    async def get_sources(self) -> list[KnowledgeGithubSourceModel]:
        from sqlalchemy import select

        async with get_async_db_context() as db:
            rows = (
                (await db.execute(select(KnowledgeGithubSource).order_by(KnowledgeGithubSource.created_at)))
                .scalars()
                .all()
            )
            return [KnowledgeGithubSourceModel.model_validate(r) for r in rows]

    async def get_sources_by_knowledge(self, knowledge_id: str) -> list[KnowledgeGithubSourceModel]:
        from sqlalchemy import select

        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(KnowledgeGithubSource).where(KnowledgeGithubSource.knowledge_id == knowledge_id)
                    )
                )
                .scalars()
                .all()
            )
            return [KnowledgeGithubSourceModel.model_validate(r) for r in rows]

    async def get_due_sources(self, now: int, limit: int = 5) -> list[KnowledgeGithubSourceModel]:
        """Enabled sources whose next_run_at has passed. Called from the
        scheduler tick; each row is claimed by stamping next_run_at forward
        BEFORE the sync runs so a concurrent worker can't double-fire."""
        from sqlalchemy import select, update

        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(KnowledgeGithubSource)
                        .where(
                            KnowledgeGithubSource.enabled == 1,
                            KnowledgeGithubSource.next_run_at.is_not(None),
                            KnowledgeGithubSource.next_run_at <= now,
                        )
                        .order_by(KnowledgeGithubSource.next_run_at)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            models = [KnowledgeGithubSourceModel.model_validate(r) for r in rows]
            # Claim: push next_run far out; the real next_run is recomputed
            # from the source's interval when the sync finishes.
            for m in models:
                await db.execute(
                    update(KnowledgeGithubSource)
                    .where(KnowledgeGithubSource.id == m.id)
                    .values(next_run_at=now + 86400 * 7)
                )
            await db.commit()
            return models

    async def update_source(
        self, id: str, form_data: KnowledgeGithubSourceUpdateForm
    ) -> Optional[KnowledgeGithubSourceModel]:
        from sqlalchemy import update

        async with get_async_db_context() as db:
            values: dict = {'updated_at': int(time.time())}
            for field in (
                'branch',
                'directory_path',
                'credential_id',
                'include_globs',
                'exclude_globs',
                'max_file_bytes',
                'max_files',
            ):
                v = getattr(form_data, field)
                if v is not None:
                    values[field] = v
            if form_data.remove_deleted is not None:
                values['remove_deleted'] = 1 if form_data.remove_deleted else 0
            if form_data.enabled is not None:
                values['enabled'] = 1 if form_data.enabled else 0
            if form_data.interval_seconds is not None:
                values['interval_seconds'] = form_data.interval_seconds
                values['next_run_at'] = _compute_next_run(form_data.interval_seconds)
            if form_data.allowed_extensions is not None:
                values['allowed_extensions'] = form_data.allowed_extensions
            await db.execute(update(KnowledgeGithubSource).where(KnowledgeGithubSource.id == id).values(**values))
            await db.commit()
        return await self.get_source(id)

    async def finish_sync(
        self,
        id: str,
        *,
        status: str,
        result: dict,
        commit_sha: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        failure: bool = False,
    ) -> None:
        """Persist the outcome and recompute the next scheduled run. On
        failure the backoff is exponential (capped at 1h) so a broken repo
        or revoked token doesn't hammer GitHub every tick."""
        from sqlalchemy import update

        async with get_async_db_context() as db:
            row = await db.get(KnowledgeGithubSource, id)
            if not row:
                return
            now = int(time.time())
            failures = int(row.consecutive_failures or 0) + 1 if failure else 0
            interval = (
                int(interval_seconds) if interval_seconds is not None else int(row.interval_seconds or 0)
            )
            if failure:
                backoff = min(60 * (2 ** min(failures, 6)), 3600)
                next_run = now + backoff
            else:
                next_run = _compute_next_run(interval, now)
            values = {
                'last_sync_at': now,
                'last_sync_status': status,
                'last_sync_result': result,
                'consecutive_failures': failures,
                'next_run_at': next_run,
                'updated_at': now,
            }
            if commit_sha and not failure:
                values['last_commit_sha'] = commit_sha
            await db.execute(update(KnowledgeGithubSource).where(KnowledgeGithubSource.id == id).values(**values))
            await db.commit()

    async def delete_source(self, id: str) -> bool:
        async with get_async_db_context() as db:
            row = await db.get(KnowledgeGithubSource, id)
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def count_by_credential(self, credential_id: str) -> int:
        from sqlalchemy import func, select

        async with get_async_db_context() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(KnowledgeGithubSource)
                .where(KnowledgeGithubSource.credential_id == credential_id)
            )
            return int(count or 0)


KnowledgeGithubSources = KnowledgeGithubSourcesTable()
