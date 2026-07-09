# Azure Provisioning Checklist (human steps, ~1 hour)

Sign in at portal.azure.com (create account with the client's or your business email; enable MFA).

## 1. Resource group
- Create resource group `baton-prod` — Region: **UAE North**.

## 2. PostgreSQL
- Create **Azure Database for PostgreSQL Flexible Server** in `baton-prod`:
  - Name: `baton-db` · Region UAE North · PostgreSQL 16
  - Workload: Development → Burstable **B1ms** (1 vCore, 2 GiB) — resize later without data loss
  - Auth: PostgreSQL authentication; admin user `batonadmin`, strong password → store in a password manager
  - Networking: Public access ON for now, firewall rule "Allow Azure services" + your home IP for admin. (Tighten to VNet later.)
  - Backup: 7-day retention (default).
- After creation: create database `baton` (Server → Databases → Add).

## 3. Blob Storage
- Storage account `batonfiles` (UAE North, Standard LRS).
- Container `tenant-files` — **Private** access level.
- Copy the connection string (Access keys) for the API env.

## 4. App Service (API)
- Create **Web App**: name `baton-api` (URL becomes baton-api.azurewebsites.net), Linux, Python 3.11, region UAE North, plan **B1**.
- Configuration → Application settings (env vars):
  - `DATABASE_URL` = postgresql+psycopg://batonadmin:PASSWORD@baton-db.postgres.database.azure.com:5432/baton?sslmode=require
  - `JWT_SECRET` = long random string (generate: `openssl rand -hex 32`)
  - `AZURE_BLOB_CONN` = storage connection string
  - `EMAIL_CONN` / `EMAIL_FROM` = from step 5
  - `FRONTEND_ORIGIN` = your frontend URL (CORS)
- Enable HTTPS Only. Deployment Center → GitHub Actions (connect repo, branch main, path /api).

## 5. Email — Azure Communication Services
- Create **Communication Services** resource `baton-comms` (data location: UAE if offered, else Europe — email metadata only; note this to the client).
- Add **Email Communication Service** + free Azure subdomain (donotreply@...azurecomm.net) to start; connect a custom domain (mail.batonapp.com) later for deliverability.
- Copy connection string → `EMAIL_CONN`.

## 6. Frontend hosting
- Option A (simplest): keep GitHub Pages / Vercel — set `VITE_API_URL=https://baton-api.azurewebsites.net`.
- Option B (all-Azure): Static Web App (free tier) pointed at the repo's frontend build.

## 7. Domain (recommended before go-live)
- Buy `batonapp.com` (or .ae via UAE registrar). Map frontend → app.batonapp.com, API → api.batonapp.com (App Service custom domain + free managed cert).

## PDPL / residency statement for the client
Database, files and application compute run in Microsoft Azure UAE North (Dubai). Backups: Azure-managed, 7-day point-in-time restore. Access: role-scoped logins, TLS everywhere, signature specimens in private storage with per-use audit logging.
