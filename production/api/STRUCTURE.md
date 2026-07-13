# API — FastAPI Structure & Non-negotiable Patterns

```
api/
  app/
    main.py            # FastAPI app, CORS (FRONTEND_ORIGIN only), routers
    config.py          # env settings (pydantic-settings)
    db.py              # engine, session dependency
    models.py          # SQLAlchemy models (mirror ../db/schema.sql)
    security.py        # bcrypt hashing, JWT create/verify, deps: current_user, require_roles
    tenancy.py         # tenant scoping helpers — EVERY query filtered by current_user.tenant_id
    blobs.py           # Azure Blob upload/download, short-lived SAS links
    emails.py          # ACS email send; templates: invite, reset, client_send, daily_digest
    scheduler.py       # APScheduler: daily 07:00 GST overdue/receivables digest
    routers/
      auth.py          # POST /auth/login, /auth/refresh, /auth/reset-password
      tenants.py       # bootstrap (one-time firm setup = wizard), firm settings, catalog
      users.py         # CRUD (Admin), invite resend, signature specimen upload
      proposals.py     # list/detail + ACTION endpoints (see below)
      duties.py        # list, complete (multipart: fields + evidence files)
      payments.py      # list, invoice-raised, record-receipt
      files.py         # upload (multipart), GET /files/{id}/link (SAS)
      import_.py       # POST /admin/import-baton-json — accepts prototype export file
  alembic/             # migrations (init from models)
  tests/               # pytest: auth, tenancy isolation, state machine
  requirements.txt
  Dockerfile           # production image — Azure Container Apps runs this (see prompts/PHASE-5-containerapps.md)
```

## Patterns Claude Code must follow
1. **State machine on the server.** Proposal transitions are ACTION endpoints, not generic PATCH:
   `POST /proposals/{id}/assign`, `/request-items`, `/provide-item`, `/waive`, `/reject-item`,
   `/withdraw-item`, `/generate` (returns rendered doc), `/submit`, `/sign-route`, `/senior-approve`,
   `/senior-reject`, `/send-client`, `/upload-signed` (conversion), `/staff-activity`, `/el-route`,
   `/el-sign`, `/el-send`. Each validates: caller role, caller-is-holder where required, current
   status. Invalid transition → 409 with reason. The prototype's reducer logic is the spec.
2. **Holder changes are transactional:** close open holder_log row, open the next, write the
   proposal_event — one DB transaction.
3. **Append-only enforcement:** app role gets INSERT/SELECT only on *_events, holder_log,
   signature_uses, duty_completions. No UPDATE/DELETE grants.
4. **Tenancy:** `current_user` dependency decodes JWT → user + tenant_id. Every query joins/filters
   on tenant_id. Tests MUST include a cross-tenant isolation test (user A cannot read tenant B).
5. **Auth:** bcrypt; JWT access 30 min + refresh 14 days; `must_reset=true` blocks everything except
   /auth/reset-password. Invite email carries a one-time set-password link (signed token, 72h).
6. **Files:** private container `tenant-files/{tenant_id}/{entity}/{uuid}-{name}`. Download only via
   15-minute SAS from GET /files/{id}/link after tenancy check. Signature specimens are NEVER
   returned as raw bytes to non-owners — preview via SAS, logged.
7. **Daily digest job:** 07:00 Asia/Dubai — for each user with overdue duties or unraised/overdue
   receivables (accountant), send one summary email; also insert notices rows.
8. **AI rewording:** POST /proposals/{id}/polish-terms → calls Anthropic API server-side with
   ANTHROPIC_API_KEY env (never exposed to the browser). Graceful fallback to raw text on error.
9. **Import:** /admin/import-baton-json maps the prototype's export JSON → rows (tenants, users
   [random temp passwords, must_reset], duties, proposals best-effort). This is how the client's
   demo data enters production.
