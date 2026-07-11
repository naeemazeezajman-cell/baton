"""vat client profiles + supply category (VAT engine module — see REMOVING-VAT-ENGINE.md)

Revision ID: d8a4e63f9b15
Revises: c9e5a12b8d47
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8a4e63f9b15'
down_revision: Union[str, None] = 'c9e5a12b8d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vat_client_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('nature_of_business', sa.Text(), nullable=True),
        sa.Column('business_category', sa.Text(), nullable=False),
        sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('other_notes', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.UniqueConstraint('tenant_id', 'client_id'),
    )
    op.add_column('vat_filing_items',
                  sa.Column('category', sa.Text(), server_default='standard', nullable=False))


def downgrade() -> None:
    op.drop_column('vat_filing_items', 'category')
    op.drop_table('vat_client_profiles')
