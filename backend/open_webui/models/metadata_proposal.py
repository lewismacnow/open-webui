"""LLM-driven metadata improvement of embedded knowledge files.

Two tables:

* ``metadata_scan`` — one row per admin-triggered batch run (whole KB,
  missing-only, attribute-targeted, or single file). Tracks scope,
  progress, and the server-side parallelism ceiling. The UI polls these
  rows for live progress.
* ``metadata_proposal`` — one row per file per scan, holding the LLM's
  suggested {title, description, summary, tags} AFTER validation and
  PII redaction. Proposals are inert until an admin explicitly applies
  them — the model NEVER writes to ``file`` rows directly.
"""

import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, JSON, Text

from open_webui.internal.db import Base, get_async_db_context

log = logging.getLogger(__name__)

# Attribute universe the LLM may propose values for.
METADATA_ATTRIBUTES = ('title', 'description', 'summary', 'tags', 'keywords', 'category', 'doc_type', 'audience')

SCAN_MODES = ('all', 'missing', 'attributes', 'file')


class MetadataScan(Base):
    __tablename__ = 'metadata_scan'

    id = Column(Text, primary_key=True, unique=True)
    knowledge_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)  # admin who started it
    model_id = Column(Text, nullable=False)  # trusted model used for suggestions

    mode = Column(Text, nullable=False)  # all | missing | attributes | file
    attributes = Column(JSON, nullable=True)  # for mode='attributes'
    file_id = Column(Text, nullable=True)  # for mode='file'
    max_parallel = Column(BigInteger, nullable=False, default=1)

    status = Column(Text, nullable=False, default='running')  # running|completed|failed|cancelled
    total_files = Column(BigInteger, nullable=False, default=0)
    processed_files = Column(BigInteger, nullable=False, default=0)
    proposals_created = Column(BigInteger, nullable=False, default=0)
    redactions = Column(BigInteger, nullable=False, default=0)  # PII redactions applied
    errors = Column(JSON, nullable=True)  # list[str]
    started_at = Column(BigInteger, nullable=False)
    finished_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index('ix_metadata_scan_knowledge_id', 'knowledge_id'),
        Index('ix_metadata_scan_status', 'status'),
    )


class MetadataProposal(Base):
    __tablename__ = 'metadata_proposal'

    id = Column(Text, primary_key=True, unique=True)
    scan_id = Column(Text, nullable=False)
    knowledge_id = Column(Text, nullable=False)
    file_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)  # admin who started the scan

    # Proposed values (validated + PII-redacted at write time). NULL = the
    # LLM had no improvement for that attribute / it was out of scope.
    proposed_title = Column(Text, nullable=True)
    proposed_description = Column(Text, nullable=True)
    proposed_summary = Column(Text, nullable=True)
    proposed_tags = Column(JSON, nullable=True)  # list[str]
    proposed_keywords = Column(JSON, nullable=True)  # list[str]
    proposed_extra = Column(JSON, nullable=True)  # {category, doc_type, audience}
    previous_keywords = Column(JSON, nullable=True)
    previous_extra = Column(JSON, nullable=True)

    # Previous values for the diff view + undo semantics.
    previous_title = Column(Text, nullable=True)
    previous_description = Column(Text, nullable=True)
    previous_summary = Column(Text, nullable=True)
    previous_tags = Column(JSON, nullable=True)

    redaction_count = Column(BigInteger, nullable=False, default=0)
    proposer_model_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='pending')  # pending|no_change|auto_applied|applied|dismissed
    applied_at = Column(BigInteger, nullable=True)
    applied_by = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_metadata_proposal_scan_id', 'scan_id'),
        Index('ix_metadata_proposal_knowledge_id', 'knowledge_id'),
        Index('ix_metadata_proposal_file_id', 'file_id'),
        Index('ix_metadata_proposal_status', 'status'),
    )


class MetadataScanModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    user_id: str
    model_id: str
    mode: str
    attributes: Optional[list[str]] = None
    file_id: Optional[str] = None
    max_parallel: int = 1
    status: str = 'running'
    total_files: int = 0
    processed_files: int = 0
    proposals_created: int = 0
    redactions: int = 0
    errors: Optional[list[str]] = None
    started_at: int
    finished_at: Optional[int] = None


class MetadataProposalModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scan_id: str
    knowledge_id: str
    file_id: str
    user_id: str
    proposed_title: Optional[str] = None
    proposed_description: Optional[str] = None
    proposed_summary: Optional[str] = None
    proposed_tags: Optional[list[str]] = None
    proposed_keywords: Optional[list[str]] = None
    proposed_extra: Optional[dict] = None
    previous_title: Optional[str] = None
    previous_description: Optional[str] = None
    previous_summary: Optional[str] = None
    previous_tags: Optional[list[str]] = None
    previous_keywords: Optional[list[str]] = None
    previous_extra: Optional[dict] = None
    redaction_count: int = 0
    proposer_model_id: str
    status: str = 'pending'
    applied_at: Optional[int] = None
    applied_by: Optional[str] = None
    created_at: int


class MetadataScanForm(BaseModel):
    knowledge_id: str
    model_id: str
    mode: str = 'all'  # all | missing | attributes | file
    attributes: Optional[list[str]] = None
    file_id: Optional[str] = None
    max_parallel: int = 1


class MetadataScansTable:
    async def insert_scan(self, form_data: MetadataScanForm, user_id: str) -> MetadataScanModel:
        now = int(time.time())
        mode = form_data.mode if form_data.mode in SCAN_MODES else 'all'
        attrs = [a for a in (form_data.attributes or []) if a in METADATA_ATTRIBUTES]
        id = str(uuid.uuid4())
        async with get_async_db_context() as db:
            await db.execute(
                MetadataScan.__table__.insert().values(
                    id=id,
                    knowledge_id=form_data.knowledge_id,
                    user_id=user_id,
                    model_id=form_data.model_id,
                    mode=mode,
                    attributes=attrs or None,
                    file_id=form_data.file_id,
                    max_parallel=max(1, min(int(form_data.max_parallel), 8)),
                    total_files=0,
                    processed_files=0,
                    proposals_created=0,
                    redactions=0,
                    status='running',
                    started_at=now,
                )
            )
            await db.commit()
            row = await db.get(MetadataScan, id)
            return MetadataScanModel.model_validate(row)

    async def get_scan(self, id: str) -> Optional[MetadataScanModel]:
        async with get_async_db_context() as db:
            row = await db.get(MetadataScan, id)
            return MetadataScanModel.model_validate(row) if row else None

    async def get_scans(self, knowledge_id: Optional[str] = None, limit: int = 20) -> list[MetadataScanModel]:
        from sqlalchemy import select

        async with get_async_db_context() as db:
            q = select(MetadataScan).order_by(MetadataScan.started_at.desc()).limit(limit)
            if knowledge_id:
                q = q.where(MetadataScan.knowledge_id == knowledge_id)
            rows = (await db.execute(q)).scalars().all()
            return [MetadataScanModel.model_validate(r) for r in rows]

    async def get_running_scan(self, knowledge_id: str) -> Optional[MetadataScanModel]:
        from sqlalchemy import select

        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(MetadataScan)
                    .where(MetadataScan.knowledge_id == knowledge_id, MetadataScan.status == 'running')
                    .limit(1)
                )
            ).scalar_one_or_none()
            return MetadataScanModel.model_validate(row) if row else None

    async def update_progress(
        self,
        id: str,
        *,
        total: Optional[int] = None,
        processed: Optional[int] = None,
        proposals: Optional[int] = None,
        redactions: Optional[int] = None,
    ) -> None:
        from sqlalchemy import update

        values: dict = {}
        if total is not None:
            values['total_files'] = int(total)
        if processed is not None:
            values['processed_files'] = int(processed)
        if proposals is not None:
            values['proposals_created'] = int(proposals)
        if redactions is not None:
            values['redactions'] = int(redactions)
        if not values:
            return
        async with get_async_db_context() as db:
            await db.execute(update(MetadataScan).where(MetadataScan.id == id).values(**values))
            await db.commit()

    async def finish_scan(self, id: str, status: str, errors: Optional[list[str]] = None) -> None:
        from sqlalchemy import update

        async with get_async_db_context() as db:
            await db.execute(
                update(MetadataScan)
                .where(MetadataScan.id == id)
                .values(status=status, finished_at=int(time.time()), errors=errors or None)
            )
            await db.commit()


class MetadataProposalsTable:
    async def insert_proposal(self, model: dict) -> Optional[MetadataProposalModel]:
        id = str(uuid.uuid4())
        now = int(time.time())
        values = {**model, 'id': id, 'created_at': now, 'status': 'pending'}
        async with get_async_db_context() as db:
            await db.execute(MetadataProposal.__table__.insert().values(**values))
            await db.commit()
            row = await db.get(MetadataProposal, id)
            return MetadataProposalModel.model_validate(row) if row else None

    async def get_proposal(self, id: str) -> Optional[MetadataProposalModel]:
        async with get_async_db_context() as db:
            row = await db.get(MetadataProposal, id)
            return MetadataProposalModel.model_validate(row) if row else None

    async def get_proposals(
        self,
        knowledge_id: Optional[str] = None,
        scan_id: Optional[str] = None,
        status: Optional[str] = 'pending',
        limit: int = 200,
    ) -> list[MetadataProposalModel]:
        from sqlalchemy import select

        async with get_async_db_context() as db:
            q = select(MetadataProposal).order_by(MetadataProposal.created_at.desc()).limit(limit)
            if knowledge_id:
                q = q.where(MetadataProposal.knowledge_id == knowledge_id)
            if scan_id:
                q = q.where(MetadataProposal.scan_id == scan_id)
            if status:
                q = q.where(MetadataProposal.status == status)
            rows = (await db.execute(q)).scalars().all()
            return [MetadataProposalModel.model_validate(r) for r in rows]

    async def set_status(self, id: str, status: str, applied_by: Optional[str] = None) -> None:
        from sqlalchemy import update

        values: dict = {'status': status}
        if status == 'applied':
            values['applied_at'] = int(time.time())
            values['applied_by'] = applied_by
        async with get_async_db_context() as db:
            await db.execute(update(MetadataProposal).where(MetadataProposal.id == id).values(**values))
            await db.commit()


MetadataScans = MetadataScansTable()
MetadataProposals = MetadataProposalsTable()
