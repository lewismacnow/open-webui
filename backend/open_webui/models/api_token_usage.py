"""API token usage — records token consumption per external API request.

The UI-chat flow already records usage on the ``chat_message.usage`` column
(``process_chat_response`` in ``utils/middleware.py``). External clients
calling the OpenAI-compatible endpoint (``/openai/v1/chat/completions``
etc.) do not persist a ``chat_message`` row, so their token usage is
invisible to ``Admin > Analytics`` today. This model records that
usage directly so the admin can see per-key / per-model / per-user
breakdowns for the API path.

Rows are written from ``utils/token_recorder.py`` which the
OpenAI-compatible handlers invoke after the upstream response. The
fields are intentionally narrow (no message body, no chat_id) — this
is bookkeeping data, not a chat transcript.
"""

import json
import time
import uuid
from typing import Any, Optional

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.users import ApiKey
from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.sql import and_


class ApiTokenUsage(Base):
    """One row per external API call that consumed tokens.

    ``api_key_id`` is nullable for cases where the call is authenticated
    by a regular user JWT (e.g. the fork's own API-tools path) rather than
    a long-lived API key. ``endpoint`` distinguishes chat / embedding /
    response so the analytics view can split by surface.
    """

    __tablename__ = 'api_token_usage'

    id = Column(Text, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, nullable=False, index=True)
    api_key_id = Column(Text, nullable=True, index=True)
    model_id = Column(Text, nullable=False, index=True)
    endpoint = Column(Text, nullable=False)  # 'chat' | 'embedding' | 'response'

    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)

    duration_ms = Column(Integer, nullable=False, default=0)
    status_code = Column(Integer, nullable=False, default=200)
    trace_id = Column(Text, nullable=True)  # groups failover attempts of one logical request
    created_at = Column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000))

    __table_args__ = (
        Index('ix_api_token_usage_user_created', 'user_id', 'created_at'),
        Index('ix_api_token_usage_apikey_created', 'api_key_id', 'created_at'),
        Index('ix_api_token_usage_model_created', 'model_id', 'created_at'),
    )


class ApiTokenUsageTable:
    async def record(
        self,
        *,
        user_id: str,
        api_key_id: Optional[str],
        model_id: str,
        endpoint: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        status_code: int,
        trace_id: Optional[str] = None,
        db: Optional[Any] = None,
    ) -> str:
        """Persist one usage row. Returns the new row id."""
        row_id = str(uuid.uuid4())
        total = max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))
        async with get_async_db_context(db) as session:
            await session.execute(
                ApiTokenUsage.__table__.insert().values(
                    id=row_id,
                    user_id=user_id,
                    api_key_id=api_key_id,
                    model_id=model_id,
                    endpoint=endpoint,
                    prompt_tokens=max(0, int(prompt_tokens or 0)),
                    completion_tokens=max(0, int(completion_tokens or 0)),
                    total_tokens=total,
                    duration_ms=max(0, int(duration_ms or 0)),
                    status_code=int(status_code or 200),
                    trace_id=trace_id,
                    created_at=int(time.time() * 1000),
                )
            )
            await session.commit()
        return row_id

    async def get_by_api_key(
        self,
        api_key_id: str,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        db: Optional[Any] = None,
    ) -> dict[str, dict]:
        """Aggregate token usage for a single API key, grouped by model_id.

        Returns ``{model_id: {prompt_tokens, completion_tokens, total_tokens, request_count}}``.
        """
        async with get_async_db_context(db) as session:
            stmt = select(
                ApiTokenUsage.model_id,
                func.coalesce(func.sum(ApiTokenUsage.prompt_tokens), 0).label('prompt'),
                func.coalesce(func.sum(ApiTokenUsage.completion_tokens), 0).label('completion'),
                func.coalesce(func.sum(ApiTokenUsage.total_tokens), 0).label('total'),
                func.count(ApiTokenUsage.id).label('request_count'),
            ).filter(ApiTokenUsage.api_key_id == api_key_id)
            if start_date:
                stmt = stmt.filter(ApiTokenUsage.created_at >= start_date)
            if end_date:
                stmt = stmt.filter(ApiTokenUsage.created_at <= end_date)
            stmt = stmt.group_by(ApiTokenUsage.model_id)
            result = await session.execute(stmt)
            return {
                row.model_id: {
                    'prompt_tokens': int(row.prompt),
                    'completion_tokens': int(row.completion),
                    'total_tokens': int(row.total),
                    'request_count': int(row.request_count),
                }
                for row in result.all()
            }

    async def get_by_user(
        self,
        user_id: str,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        db: Optional[Any] = None,
    ) -> dict[str, dict]:
        """Aggregate token usage for a user across all their API keys.

        Returns ``{model_id: {…}}`` (same shape as ``get_by_api_key``).
        """
        async with get_async_db_context(db) as session:
            stmt = select(
                ApiTokenUsage.model_id,
                func.coalesce(func.sum(ApiTokenUsage.prompt_tokens), 0).label('prompt'),
                func.coalesce(func.sum(ApiTokenUsage.completion_tokens), 0).label('completion'),
                func.coalesce(func.sum(ApiTokenUsage.total_tokens), 0).label('total'),
                func.count(ApiTokenUsage.id).label('request_count'),
            ).filter(ApiTokenUsage.user_id == user_id)
            if start_date:
                stmt = stmt.filter(ApiTokenUsage.created_at >= start_date)
            if end_date:
                stmt = stmt.filter(ApiTokenUsage.created_at <= end_date)
            stmt = stmt.group_by(ApiTokenUsage.model_id)
            result = await session.execute(stmt)
            return {
                row.model_id: {
                    'prompt_tokens': int(row.prompt),
                    'completion_tokens': int(row.completion),
                    'total_tokens': int(row.total),
                    'request_count': int(row.request_count),
                }
                for row in result.all()
            }

    async def get_by_endpoint(
        self,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        db: Optional[Any] = None,
    ) -> dict[str, dict]:
        """Aggregate by endpoint ('chat' | 'embedding' | 'response') so the
        admin can show UI vs API split (the chat_message path already
        records UI usage, this records API usage).
        """
        async with get_async_db_context(db) as session:
            stmt = select(
                ApiTokenUsage.endpoint,
                func.coalesce(func.sum(ApiTokenUsage.prompt_tokens), 0).label('prompt'),
                func.coalesce(func.sum(ApiTokenUsage.completion_tokens), 0).label('completion'),
                func.coalesce(func.sum(ApiTokenUsage.total_tokens), 0).label('total'),
                func.count(ApiTokenUsage.id).label('request_count'),
            )
            if start_date:
                stmt = stmt.filter(ApiTokenUsage.created_at >= start_date)
            if end_date:
                stmt = stmt.filter(ApiTokenUsage.created_at <= end_date)
            stmt = stmt.group_by(ApiTokenUsage.endpoint)
            result = await session.execute(stmt)
            return {
                row.endpoint: {
                    'prompt_tokens': int(row.prompt),
                    'completion_tokens': int(row.completion),
                    'total_tokens': int(row.total),
                    'request_count': int(row.request_count),
                }
                for row in result.all()
            }

    async def get_top_api_keys(
        self,
        limit: int = 50,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        db: Optional[Any] = None,
    ) -> list[dict]:
        """Top API keys by total token usage. Returns a list of dicts
        with ``api_key_id``, ``prompt_tokens``, ``completion_tokens``,
        ``total_tokens``, ``request_count``.
        """
        async with get_async_db_context(db) as session:
            stmt = select(
                ApiTokenUsage.api_key_id,
                func.coalesce(func.sum(ApiTokenUsage.prompt_tokens), 0).label('prompt'),
                func.coalesce(func.sum(ApiTokenUsage.completion_tokens), 0).label('completion'),
                func.coalesce(func.sum(ApiTokenUsage.total_tokens), 0).label('total'),
                func.count(ApiTokenUsage.id).label('request_count'),
            ).filter(ApiTokenUsage.api_key_id.isnot(None))
            if start_date:
                stmt = stmt.filter(ApiTokenUsage.created_at >= start_date)
            if end_date:
                stmt = stmt.filter(ApiTokenUsage.created_at <= end_date)
            stmt = (
                stmt.group_by(ApiTokenUsage.api_key_id)
                .order_by(func.sum(ApiTokenUsage.total_tokens).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [
                {
                    'api_key_id': row.api_key_id,
                    'prompt_tokens': int(row.prompt),
                    'completion_tokens': int(row.completion),
                    'total_tokens': int(row.total),
                    'request_count': int(row.request_count),
                }
                for row in result.all()
            ]


ApiTokenUsages = ApiTokenUsageTable()
