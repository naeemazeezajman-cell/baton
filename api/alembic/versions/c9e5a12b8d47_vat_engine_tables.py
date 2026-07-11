"""vat engine tables (separate, removable module — see REMOVING-VAT-ENGINE.md)

Revision ID: c9e5a12b8d47
Revises: b7c3d84f1a52
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9e5a12b8d47'
down_revision: Union[str, None] = 'b7c3d84f1a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = ("ledgers_pending", "invoices_pending", "reconciled", "computation_draft",
            "awaiting_client_approval", "ready_to_file", "complete")


def upgrade() -> None:
    op.create_table(
        'vat_filings',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('duty_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('duties.id'), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('staff_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('prev_period_start', sa.Date(), nullable=False),
        sa.Column('status', sa.Text(), server_default='ledgers_pending', nullable=False),
        sa.Column('ledger_file', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('invoice_file', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('invoice_evidence', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('recon', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('computation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('client_approval', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('fta_ack', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN {STATUSES}", name='vat_filings_status_check'),
    )
    op.create_index('ix_vat_filings_duty_id', 'vat_filings', ['duty_id'])
    op.create_table(
        'vat_filing_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filing_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vat_filings.id'), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('row_no', sa.BigInteger(), nullable=False),
        sa.Column('invoice_no', sa.Text(), nullable=False),
        sa.Column('invoice_no_norm', sa.Text(), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('party', sa.Text(), nullable=False),
        sa.Column('trn', sa.Text(), nullable=True),
        sa.Column('emirate', sa.Text(), nullable=False),
        sa.Column('net', sa.Numeric(14, 2), nullable=False),
        sa.Column('vat', sa.Numeric(14, 2), nullable=False),
        sa.Column('type', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('bucket', sa.Text(), nullable=True),
        sa.Column('resolution', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('included', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )
    op.create_index('ix_vat_filing_items_filing_id', 'vat_filing_items', ['filing_id'])
    op.create_table(
        'vat_filing_events',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filing_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vat_filings.id'), nullable=False),
        sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('by_user', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
    )
    op.create_table(
        'vat_client_requests',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filing_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vat_filings.id'), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('to_email', sa.Text(), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('sent_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('by_user', postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('vat_client_requests')
    op.drop_table('vat_filing_events')
    op.drop_index('ix_vat_filing_items_filing_id', table_name='vat_filing_items')
    op.drop_table('vat_filing_items')
    op.drop_index('ix_vat_filings_duty_id', table_name='vat_filings')
    op.drop_table('vat_filings')
