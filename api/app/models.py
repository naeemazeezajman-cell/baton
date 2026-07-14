import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Mirrors production/db/schema.sql. Alembic migrations are the source of truth for the DB;
# event tables (proposal_events, holder_log, duty_events, duty_completions, signature_uses)
# are append-only by convention — the app role gets INSERT/SELECT only.

TS = TIMESTAMP(timezone=True)
NOW = text("now()")
GEN_UUID = text("gen_random_uuid()")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    name: Mapped[str] = mapped_column(Text)
    short: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    trn: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    accent: Mapped[str | None] = mapped_column(Text, server_default="#14606B")
    services: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    templates: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint("role IN ('Admin','Manager','Staff','Accountant')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(Text)
    designation: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(CITEXT)
    role: Mapped[str] = mapped_column(Text)
    signatory: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    sig_specimen: Mapped[dict | None] = mapped_column(JSONB)
    password_hash: Mapped[str] = mapped_column(Text)
    must_reset: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(TS)  # feeds operator activity counts only
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("tenant_id", "ref"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    ref: Mapped[str] = mapped_column(Text)
    prospect: Mapped[dict] = mapped_column(JSONB)
    services: Mapped[list] = mapped_column(JSONB)
    payment_terms_rough: Mapped[str | None] = mapped_column(Text)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    holder: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    signatory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    checklist: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    versions: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    el: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    draft: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    signatures: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    revision_note: Mapped[dict | None] = mapped_column(JSONB)
    senior_note: Mapped[dict | None] = mapped_column(JSONB)
    last_rejection: Mapped[dict | None] = mapped_column(JSONB)
    proposal_sent_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(TS)


class ProposalEvent(Base):
    __tablename__ = "proposal_events"
    __table_args__ = (Index("ix_proposal_events_proposal_id_at", "proposal_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, server_default="log")
    text_: Mapped[str] = mapped_column("text", Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)


class PlatformOperator(Base):
    """The developer's own login ABOVE all tenants — deliberately separate from users
    (no tenant_id). Operator JWTs carry scope=platform and are rejected by every tenant
    endpoint; tenant tokens are rejected by operator endpoints."""

    __tablename__ = "platform_operators"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_reset: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id"),
                      CheckConstraint("status IN ('trial','active','suspended','cancelled')",
                                      name="subscriptions_status_check"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    plan_name: Mapped[str] = mapped_column(Text, server_default="Trial")
    status: Mapped[str] = mapped_column(Text, server_default="trial")
    seats_limit: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    current_period_end: Mapped[datetime | None] = mapped_column(TS)
    notes: Mapped[str | None] = mapped_column(Text)


class PlatformEvent(Base):
    """Append-only operator action log — never tenant business content."""

    __tablename__ = "platform_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text_: Mapped[str] = mapped_column("text", Text)


class HolderLog(Base):
    """Holding spans for proposals AND onboardings — exactly one of proposal_id /
    onboarding_id is set per row."""

    __tablename__ = "holder_log"
    __table_args__ = (Index("ix_holder_log_proposal_id", "proposal_id"),
                      Index("ix_holder_log_onboarding_id", "onboarding_id"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    onboarding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboardings.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    reason: Mapped[str | None] = mapped_column(Text)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("tenant_id", "ref"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    ref: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    contact: Mapped[dict | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(Text, server_default="proposal")  # proposal | pre_baton
    from_proposal: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    confirmation_basis: Mapped[str | None] = mapped_column(Text)  # signed_upload | email_approval | ...
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Onboarding(Base):
    """One documentation relay per staffed activity, created automatically at EL send."""

    __tablename__ = "onboardings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    service: Mapped[str] = mapped_column(Text)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(Text, server_default="in_progress")  # in_progress | complete
    holder: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    holder_since: Mapped[datetime | None] = mapped_column(TS)
    duty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("duties.id"))
    stars: Mapped[list | None] = mapped_column(JSONB)  # at completion: [{user_id, stars, total_held_ms, holdings}]
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class OnboardingEvent(Base):
    __tablename__ = "onboarding_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    onboarding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboardings.id"))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text_: Mapped[str] = mapped_column("text", Text)


class OnboardingItem(Base):
    __tablename__ = "onboarding_items"
    __table_args__ = (
        CheckConstraint("kind IN ('document','information','credential')", name="onboarding_items_kind_check"),
        CheckConstraint("status IN ('requested','provided','answered','not_available','withdrawn')",
                        name="onboarding_items_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    onboarding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboardings.id"))
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="requested")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    answer_text: Mapped[str | None] = mapped_column(Text)  # information answers; legacy single-blob credentials
    credential: Mapped[dict | None] = mapped_column(JSONB)  # {portal_label, username, password, extra_note} — password masked by default
    qualifier: Mapped[str | None] = mapped_column(Text)  # null | audited | unaudited | draft | copy
    files: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    accepted_at: Mapped[datetime | None] = mapped_column(TS)


class Duty(Base):
    __tablename__ = "duties"
    __table_args__ = (
        Index(
            "ix_duties_tenant_id_next_due",
            "tenant_id",
            "next_due",
            postgresql_where=text("NOT closed"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    client_name: Mapped[str] = mapped_column(Text)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"))
    service: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    contact: Mapped[dict | None] = mapped_column(JSONB)
    cadence: Mapped[str] = mapped_column(Text)
    next_due: Mapped[datetime] = mapped_column(TS)
    closed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class DutyEvent(Base):
    __tablename__ = "duty_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    duty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("duties.id"))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text_: Mapped[str] = mapped_column("text", Text)


class DutyCompletion(Base):
    __tablename__ = "duty_completions"
    __table_args__ = (
        CheckConstraint("method IN ('sent','proof','declared')", name="duty_completions_method_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    duty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("duties.id"))
    due_at: Mapped[datetime] = mapped_column(TS)
    completed_at: Mapped[datetime] = mapped_column(TS)
    late_ms: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    method: Mapped[str] = mapped_column(Text)
    emailed_to: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    record: Mapped[dict | None] = mapped_column(JSONB)
    evidence: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proposals.id"))
    label: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    due_at: Mapped[datetime] = mapped_column(TS)
    invoice_raised: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    invoice_raised_at: Mapped[datetime | None] = mapped_column(TS)
    invoice: Mapped[dict | None] = mapped_column(JSONB)  # {number, date, files, by, declared, reason}
    receipts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    events: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    blob_path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class SignatureUse(Base):
    __tablename__ = "signature_uses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    document: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)


class DigestRun(Base):
    """One row per completed daily-digest run — the idempotency record."""

    __tablename__ = "digest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_date: Mapped[str] = mapped_column(Date, unique=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    text_: Mapped[str] = mapped_column("text", Text)
    read: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))


class PerformanceConfig(Base):
    """Firm-definable performance targets — append-only versions (the audit log itself).
    Star computation resolves the version active at an item's completion time, so a
    config change applies to future scoring only. Version 0 = built-in defaults."""

    __tablename__ = "performance_config"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
