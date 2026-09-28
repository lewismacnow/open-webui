"""add github sync + metadata improvement tables

Revision ID: e2f3a4b5c6d7
Revises: c8d9e0f1a2b3
Create Date: 2026-09-28

Fork feature tables:
- github_credential: central Fernet-encrypted GitHub PATs for knowledge sync
- knowledge_github_source: (repo, branch, dir) sources bound to knowledge bases
- metadata_scan: LLM metadata-improvement batch runs (progress tracking)
- metadata_proposal: per-file proposals, applied only via explicit admin action
"""

import sqlalchemy as sa
from alembic import op

revision = 'e2f3a4b5c6d7'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'github_credential',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('encrypted_token', sa.Text(), nullable=False),
        sa.Column('token_suffix', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('last_used_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_github_credential_created_by', 'github_credential', ['created_by'])

    op.create_table(
        'knowledge_github_source',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('knowledge_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('repo_owner', sa.Text(), nullable=False),
        sa.Column('repo_name', sa.Text(), nullable=False),
        sa.Column('branch', sa.Text(), nullable=False),
        sa.Column('directory_path', sa.Text(), nullable=False, server_default=''),
        sa.Column('credential_id', sa.Text(), nullable=True),
        sa.Column('include_globs', sa.JSON(), nullable=True),
        sa.Column('exclude_globs', sa.JSON(), nullable=True),
        sa.Column('max_file_bytes', sa.BigInteger(), nullable=False),
        sa.Column('max_files', sa.BigInteger(), nullable=False),
        sa.Column('remove_deleted', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('interval_seconds', sa.BigInteger(), nullable=True),
        sa.Column('next_run_at', sa.BigInteger(), nullable=True),
        sa.Column('enabled', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('last_commit_sha', sa.Text(), nullable=True),
        sa.Column('last_sync_at', sa.BigInteger(), nullable=True),
        sa.Column('last_sync_status', sa.Text(), nullable=True),
        sa.Column('last_sync_result', sa.JSON(), nullable=True),
        sa.Column('consecutive_failures', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_kgs_knowledge_id', 'knowledge_github_source', ['knowledge_id'])
    op.create_index('ix_kgs_next_run_at', 'knowledge_github_source', ['next_run_at'])
    op.create_index('ix_kgs_credential_id', 'knowledge_github_source', ['credential_id'])

    op.create_table(
        'metadata_scan',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('knowledge_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('model_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=False),
        sa.Column('attributes', sa.JSON(), nullable=True),
        sa.Column('file_id', sa.Text(), nullable=True),
        sa.Column('max_parallel', sa.BigInteger(), nullable=False, server_default='1'),
        sa.Column('status', sa.Text(), nullable=False, server_default='running'),
        sa.Column('total_files', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('processed_files', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('proposals_created', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('redactions', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('errors', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.BigInteger(), nullable=False),
        sa.Column('finished_at', sa.BigInteger(), nullable=True),
    )
    op.create_index('ix_metadata_scan_knowledge_id', 'metadata_scan', ['knowledge_id'])
    op.create_index('ix_metadata_scan_status', 'metadata_scan', ['status'])

    op.create_table(
        'metadata_proposal',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('scan_id', sa.Text(), nullable=False),
        sa.Column('knowledge_id', sa.Text(), nullable=False),
        sa.Column('file_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('proposed_title', sa.Text(), nullable=True),
        sa.Column('proposed_description', sa.Text(), nullable=True),
        sa.Column('proposed_summary', sa.Text(), nullable=True),
        sa.Column('proposed_tags', sa.JSON(), nullable=True),
        sa.Column('previous_title', sa.Text(), nullable=True),
        sa.Column('previous_description', sa.Text(), nullable=True),
        sa.Column('previous_summary', sa.Text(), nullable=True),
        sa.Column('previous_tags', sa.JSON(), nullable=True),
        sa.Column('redaction_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('proposer_model_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('applied_at', sa.BigInteger(), nullable=True),
        sa.Column('applied_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_metadata_proposal_scan_id', 'metadata_proposal', ['scan_id'])
    op.create_index('ix_metadata_proposal_knowledge_id', 'metadata_proposal', ['knowledge_id'])
    op.create_index('ix_metadata_proposal_file_id', 'metadata_proposal', ['file_id'])
    op.create_index('ix_metadata_proposal_status', 'metadata_proposal', ['status'])


def downgrade() -> None:
    op.drop_table('metadata_proposal')
    op.drop_table('metadata_scan')
    op.drop_table('knowledge_github_source')
    op.drop_table('github_credential')
