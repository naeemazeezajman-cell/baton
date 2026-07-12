"""pre-baton duty clients become first-class clients

Revision ID: a3d9f82b6c41
Revises: f4c8d15a7e29
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.client_backfill import backfill_pre_baton_clients

# revision identifiers, used by Alembic.
revision: str = 'a3d9f82b6c41'
down_revision: Union[str, None] = 'f4c8d15a7e29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clients', sa.Column('origin', sa.Text(), server_default='proposal', nullable=False))
    # data backfill: client rows for every tenant's orphan duty client-names (deduped,
    # case-insensitive / whitespace-collapsed), duties linked to their client_id
    backfill_pre_baton_clients(op.get_bind())


def downgrade() -> None:
    op.execute("UPDATE duties SET client_id = NULL WHERE client_id IN "
               "(SELECT id FROM clients WHERE origin = 'pre_baton')")
    op.execute("DELETE FROM clients WHERE origin = 'pre_baton'")
    op.drop_column('clients', 'origin')
