"""demo tenant flag — marks the publicly-credentialed showcase firm

Revision ID: c4a71e2b9f60
Revises: e9f3a54c8b17
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4a71e2b9f60'
down_revision: Union[str, None] = 'e9f3a54c8b17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('demo', sa.Boolean(), server_default=sa.text('false'),
                                       nullable=False))


def downgrade() -> None:
    op.drop_column('tenants', 'demo')
