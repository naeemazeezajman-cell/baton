# Baton — Production Build Plan (Azure UAE)

Goal: take the working prototype to a multi-user, cloud-live product for a ~50-staff firm,
with data resident in Azure UAE North, real logins, and the established Claude Code update loop.

## Architecture
- **PostgreSQL** — Azure Database for PostgreSQL Flexible Server, UAE North (Dubai). One database, multi-tenant by `tenant_id`.
- **API** — FastAPI + SQLAlchemy + Alembic on Azure App Service (Linux, UAE North). JWT auth, server-side role enforcement, append-only event tables.
- **Files** — Azure Blob Storage (UAE North): uploads, signature specimens, generated documents. Private container; API issues short-lived SAS links.
- **Frontend** — the existing React app, served as static files (Azure Static Web Apps or keep Vercel/GitHub Pages). All state moves from localStorage to API calls.
- **Email** — Azure Communication Services Email (or SMTP provider): invites, password resets, client sends, daily overdue digests.
- **Scheduler** — APScheduler inside the API (v1) for the daily reminder sweep; upgrade to Azure Functions timer later if needed.

## Build phases (each has a paste-ready Claude Code prompt in /prompts)
0. **Provision Azure** — human steps in the portal (see AZURE-PROVISIONING.md). ~1 hour.
1. **Backend skeleton** — project, DB schema + migrations, auth (login / refresh / forced reset), tenant + user management, invite emails. Local dev via Docker Postgres.
2. **Onboarding workflow API** — proposals, state machine actions, checklist slots, versions/drafts, holder log, events, file uploads, signatures, client conversion, EL flow, emails.
3. **Duties + payments** — deadline engine, proof-of-work completion, filing records, payments/receipts, daily reminder job.
4. **Frontend swap** — api client module, real login screens, replace every local mutation with API calls; localStorage kept only as a "demo mode" flag.
5. **Deploy + go-live** — GitHub Actions to App Service + static hosting, env/secrets, smoke tests, import the firm's data (the prototype's export JSON imports straight into production).

## Working rhythm with Claude Code
- One phase at a time; each prompt tells Claude Code exactly what to build and what NOT to touch.
- After each phase: run the listed acceptance checks before moving on.
- All schema changes ship as Alembic migrations — never hand-edit the DB.
- The prototype (src/baton-prototype.jsx) is the behavioural specification; when in doubt, match it.

## Cost (approx, monthly)
DB Flexible Server B1ms ~$25–35 · App Service B1 ~$13–15 · Blob + email ~$2–5 · Static hosting free tier. Total ≈ **$40–60/mo**.
