# Phase 1 — Backend skeleton (paste into Claude Code from the Baton folder)

Read production/README-BUILD-PLAN.md, production/api/STRUCTURE.md and production/db/schema.sql first — they are the specification. Then build, inside a new api/ folder:

1. A FastAPI project exactly per STRUCTURE.md: config from env (pydantic-settings), SQLAlchemy models mirroring schema.sql, Alembic initialized with an initial migration generated from the models.
2. Auth: bcrypt password hashing; JWT access (30 min) + refresh (14 days); endpoints /auth/login, /auth/refresh, /auth/reset-password; must_reset gate — any authenticated call other than reset returns 403 with code MUST_RESET while the flag is true.
3. Tenant bootstrap: POST /tenants/bootstrap accepting the setup-wizard payload (firm + services + templates metadata + employees with roles/signatory/pre-existing duties). Creates tenant, users (random temp passwords, must_reset=true), duties. Returns the temp passwords once. Refuses if any tenant already exists with the same email.
4. Users router: list/create/update/deactivate (Admin only), resend-invite.
5. Email module: Azure Communication Services send; invite + reset templates; if EMAIL_CONN is unset, log emails to console (dev mode).
6. Tenancy + security deps: current_user, require_roles(...); every query tenant-scoped.
7. Local dev: docker-compose.yml with Postgres 16; .env.example listing every variable; README-DEV.md with run instructions (uvicorn + alembic upgrade head).
8. Tests (pytest): login/refresh/reset flow, bootstrap, cross-tenant isolation (create two tenants, assert user A gets 404 on tenant B's user).

Do NOT touch the existing frontend or src/baton-prototype.jsx. When done: run the tests, show me the output, and list every endpoint with its method and role requirement.
