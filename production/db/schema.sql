-- Baton multi-tenant schema (reference — Alembic migrations are the source of truth)
-- Conventions: every tenant-owned table carries tenant_id; event tables are APPEND-ONLY
-- (no UPDATE/DELETE grants for the app role); JSONB for workflow aggregates mirroring the prototype.

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL, short TEXT NOT NULL,
  address TEXT, trn TEXT, phone TEXT, email TEXT NOT NULL,
  accent TEXT DEFAULT '#14606B',
  services JSONB NOT NULL DEFAULT '[]',
  templates JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, designation TEXT, email CITEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('Admin','Manager','Staff','Accountant')),
  signatory BOOLEAN NOT NULL DEFAULT false,
  sig_specimen JSONB,
  password_hash TEXT NOT NULL,
  must_reset BOOLEAN NOT NULL DEFAULT true,
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);

CREATE TABLE proposals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  ref TEXT NOT NULL,
  prospect JSONB NOT NULL,
  services JSONB NOT NULL,
  payment_terms_rough TEXT, payment_terms TEXT,
  status TEXT NOT NULL,
  assigned_to UUID REFERENCES users(id),
  holder UUID REFERENCES users(id),
  checklist JSONB NOT NULL DEFAULT '[]',
  versions JSONB NOT NULL DEFAULT '[]',
  el JSONB NOT NULL DEFAULT '{}',
  last_rejection JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  onboarding_completed_at TIMESTAMPTZ,
  UNIQUE (tenant_id, ref)
);

CREATE TABLE proposal_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, proposal_id UUID NOT NULL REFERENCES proposals(id),
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  by_user UUID,
  kind TEXT NOT NULL DEFAULT 'log',
  text TEXT NOT NULL, meta JSONB
);
CREATE INDEX ON proposal_events (proposal_id, at);

CREATE TABLE holder_log (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, proposal_id UUID NOT NULL REFERENCES proposals(id),
  user_id UUID,
  started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ, reason TEXT
);
CREATE INDEX ON holder_log (proposal_id);

CREATE TABLE clients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  ref TEXT NOT NULL, name TEXT NOT NULL, contact JSONB,
  from_proposal UUID REFERENCES proposals(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ref)
);

CREATE TABLE duties (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  staff_id UUID NOT NULL REFERENCES users(id),
  client_name TEXT NOT NULL, client_id UUID REFERENCES clients(id),
  service TEXT NOT NULL, kind TEXT NOT NULL,
  contact JSONB,
  cadence TEXT NOT NULL, next_due TIMESTAMPTZ NOT NULL,
  closed BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON duties (tenant_id, next_due) WHERE NOT closed;

CREATE TABLE duty_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, duty_id UUID NOT NULL REFERENCES duties(id),
  at TIMESTAMPTZ NOT NULL DEFAULT now(), by_user UUID, text TEXT NOT NULL
);

CREATE TABLE duty_completions (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, duty_id UUID NOT NULL REFERENCES duties(id),
  due_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ NOT NULL,
  late_ms BIGINT NOT NULL DEFAULT 0,
  method TEXT NOT NULL CHECK (method IN ('sent','proof','declared')),
  emailed_to TEXT, reason TEXT, note TEXT,
  record JSONB,
  evidence JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  client_id UUID REFERENCES clients(id), proposal_id UUID REFERENCES proposals(id),
  label TEXT NOT NULL, amount NUMERIC(12,2) NOT NULL, due_at TIMESTAMPTZ NOT NULL,
  invoice_raised BOOLEAN NOT NULL DEFAULT false,
  receipts JSONB NOT NULL DEFAULT '[]',
  events JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  entity TEXT NOT NULL, entity_id UUID NOT NULL,
  name TEXT NOT NULL, size BIGINT, blob_path TEXT NOT NULL,
  uploaded_by UUID, at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE signature_uses (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, user_id UUID NOT NULL REFERENCES users(id),
  at TIMESTAMPTZ NOT NULL DEFAULT now(), document TEXT NOT NULL, context TEXT
);

CREATE TABLE notices (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, user_id UUID NOT NULL REFERENCES users(id),
  at TIMESTAMPTZ NOT NULL DEFAULT now(), text TEXT NOT NULL, read BOOLEAN DEFAULT false
);
