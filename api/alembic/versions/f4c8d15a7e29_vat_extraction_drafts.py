"""vat extraction drafts + item origin (VAT engine module — see REMOVING-VAT-ENGINE.md)

Revision ID: f4c8d15a7e29
Revises: e2b9c74a5d18
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f4c8d15a7e29'
down_revision: Union[str, None] = 'e2b9c74a5d18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vat_extraction_drafts',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filing_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vat_filings.id'), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('file_name', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), server_default='extracted', nullable=False),
        sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('reviewed_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reviewed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column('vat_filing_items', sa.Column('origin', sa.Text(), server_default='register', nullable=False))


def downgrade() -> None:
    op.drop_column('vat_filing_items', 'origin')
    op.drop_table('vat_extraction_drafts')
