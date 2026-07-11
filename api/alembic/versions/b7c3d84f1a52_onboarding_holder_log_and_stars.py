"""onboarding holder log and stars

Revision ID: b7c3d84f1a52
Revises: a4f2c91e7d38
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.onboarding_backfill import backfill_onboarding_holder_log

# revision identifiers, used by Alembic.
revision: str = 'b7c3d84f1a52'
down_revision: Union[str, None] = 'a4f2c91e7d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('onboardings', sa.Column('stars', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('holder_log', sa.Column('onboarding_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.alter_column('holder_log', 'proposal_id', existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_foreign_key('fk_holder_log_onboarding_id', 'holder_log', 'onboardings', ['onboarding_id'], ['id'])
    op.create_index('ix_holder_log_onboarding_id', 'holder_log', ['onboarding_id'])
    # data backfill: derive holder spans from the append-only trail for pre-existing
    # onboardings, and store star ratings on the already-completed ones
    backfill_onboarding_holder_log(op.get_bind())


def downgrade() -> None:
    op.execute("DELETE FROM holder_log WHERE onboarding_id IS NOT NULL")
    op.drop_index('ix_holder_log_onboarding_id', table_name='holder_log')
    op.drop_constraint('fk_holder_log_onboarding_id', 'holder_log', type_='foreignkey')
    op.drop_column('holder_log', 'onboarding_id')
    op.alter_column('holder_log', 'proposal_id', existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_column('onboardings', 'stars')
