"""vat profile tax period stagger (VAT engine module — see REMOVING-VAT-ENGINE.md)

Revision ID: e2b9c74a5d18
Revises: d8a4e63f9b15
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2b9c74a5d18'
down_revision: Union[str, None] = 'd8a4e63f9b15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vat_client_profiles', sa.Column('tax_period_stagger', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('vat_client_profiles', 'tax_period_stagger')
