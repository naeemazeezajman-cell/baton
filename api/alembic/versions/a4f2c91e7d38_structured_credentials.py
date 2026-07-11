"""structured credentials

Revision ID: a4f2c91e7d38
Revises: db72df01e5c1
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4f2c91e7d38'
down_revision: Union[str, None] = 'db72df01e5c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing single-blob credentials stay in answer_text and render as legacy notes.
    op.add_column('onboarding_items', sa.Column('credential', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('onboarding_items', 'credential')
