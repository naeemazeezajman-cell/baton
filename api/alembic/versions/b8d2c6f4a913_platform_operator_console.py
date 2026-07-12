"""platform operator console: operators, subscriptions, platform events, last_login_at

Revision ID: b8d2c6f4a913
Revises: a3d9f82b6c41
Create Date: 2026-07-12 00:00:00.000000

"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.security import hash_password

# revision identifiers, used by Alembic.
revision: str = 'b8d2c6f4a913'
down_revision: Union[str, None] = 'a3d9f82b6c41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_operators',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('email', postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('must_reset', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('plan_name', sa.Text(), server_default='Trial', nullable=False),
        sa.Column('status', sa.Text(), server_default='trial', nullable=False),
        sa.Column('seats_limit', sa.Integer(), nullable=False),
        sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('current_period_end', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id'),
        sa.CheckConstraint("status IN ('trial','active','suspended','cancelled')",
                           name='subscriptions_status_check'),
    )
    op.create_table(
        'platform_events',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('operator_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
    )
    op.add_column('users', sa.Column('last_login_at', postgresql.TIMESTAMP(timezone=True), nullable=True))

    bind = op.get_bind()
    # seed ONE operator from env (forced reset applies); skipped when env is not set
    email = os.getenv("OPERATOR_EMAIL", "").strip()
    pw = os.getenv("OPERATOR_INITIAL_PASSWORD", "")
    if email and pw:
        bind.execute(sa.text(
            "INSERT INTO platform_operators (email, password_hash, must_reset) "
            "VALUES (:e, :h, true) ON CONFLICT (email) DO NOTHING"),
            {"e": email, "h": hash_password(pw)})
        bind.execute(sa.text("INSERT INTO platform_events (text) VALUES (:x)"),
                     {"x": f"Platform operator seeded from environment: {email}"})
    # existing tenants get a 30-day trial so enforcement never bricks a live firm
    seats = int(os.getenv("DEFAULT_TRIAL_SEATS", "10"))
    bind.execute(sa.text(
        "INSERT INTO subscriptions (tenant_id, plan_name, status, seats_limit, current_period_end) "
        "SELECT t.id, 'Trial', 'trial', GREATEST(:s, (SELECT count(*) FROM users u WHERE u.tenant_id = t.id)), "
        "       now() + interval '30 days' "
        "FROM tenants t WHERE NOT EXISTS (SELECT 1 FROM subscriptions x WHERE x.tenant_id = t.id)"),
        {"s": seats})


def downgrade() -> None:
    op.drop_column('users', 'last_login_at')
    op.drop_table('platform_events')
    op.drop_table('subscriptions')
    op.drop_table('platform_operators')
