# Phase 5 — Deploy & go-live (Azure Container Apps)

Supersedes PHASE-5-deploy.md's App Service path: App Service quota was unavailable in the
subscription, so the API ships as a container to **Azure Container Apps**. The Container Apps
environment **`baton-env`** is already provisioned in **UAE North** (resource group `baton-prod`);
Postgres, Blob Storage and Communication Services from AZURE-PROVISIONING.md are unchanged.
Secrets: I paste values into the app's env vars myself — never ask me for secret values and never
commit them.

## 1. The image

`api/Dockerfile` (already in the repo): python:3.11-slim, installs `requirements.txt`, runs
`alembic upgrade head` on every start (idempotent), then `exec uvicorn app.main:app` bound to
`0.0.0.0` on `$PORT` (default 8000). `api/.dockerignore` keeps `.env`, `var/`, tests and VCS
files out of the image. All configuration is read from environment variables (pydantic-settings
plus a few `os.getenv` reads) — nothing is baked in.

Because migrations run at container start, migration-time env vars (`OPERATOR_EMAIL`,
`OPERATOR_INITIAL_PASSWORD`) must be set on the container app **before the first revision runs**.

## 2. Build + deploy — one command

From the repo root in Azure Cloud Shell (Bash):

```bash
az containerapp up \
  --name baton-api \
  --resource-group baton-prod \
  --environment baton-env \
  --location uaenorth \
  --source api \
  --ingress external \
  --target-port 8000 \
  --env-vars \
    DATABASE_URL='<value>' \
    JWT_SECRET='<value>' \
    FRONTEND_ORIGIN='<value>' \
    EMAIL_CONN='<value>' \
    EMAIL_FROM='<value>' \
    AZURE_BLOB_CONN='<value>' \
    ANTHROPIC_API_KEY='<value>' \
    SCHEDULER_ENABLED='true' \
    VAT_ENGINE_ENABLED='true' \
    VAT_EXTRACT_MAX_FILES='25' \
    OPERATOR_EMAIL='<value>' \
    OPERATOR_INITIAL_PASSWORD='<value>' \
    DEFAULT_TRIAL_SEATS='10' \
    ACCESS_TOKEN_TTL_MIN='30' \
    REFRESH_TOKEN_TTL_DAYS='14' \
    SET_PASSWORD_TOKEN_TTL_HOURS='72'
```

`az containerapp up --source` builds the image in ACR (creates a registry on first run) and
creates/updates the app. Equivalent two-step alternative:
`az acr build --registry <acr> --image baton-api:<tag> api/` then
`az containerapp create ... --image <acr>.azurecr.io/baton-api:<tag>` with the same
`--ingress external --target-port 8000 --env-vars` flags.

After deploy, pin replicas so the APScheduler digest fires exactly once and the app never
scales to zero (JWT sessions survive restarts, but cold starts hurt first-request latency):

```bash
az containerapp update -n baton-api -g baton-prod --min-replicas 1 --max-replicas 1
```

### Env vars the API reads (names only — values are set by me)

| Name | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...@baton-db.postgres.database.azure.com:5432/baton?sslmode=require` |
| `JWT_SECRET` | long random string |
| `FRONTEND_ORIGIN` | exact origin the frontend is served from (CORS allowlist — see §4) |
| `EMAIL_CONN` | Azure Communication Services connection string (empty = console dev mode) |
| `EMAIL_FROM` | sender address from the ACS email domain |
| `AZURE_BLOB_CONN` | storage connection string (empty = local-disk fallback — never in prod; container disk is ephemeral) |
| `ANTHROPIC_API_KEY` | empty = AI features degrade gracefully |
| `SCHEDULER_ENABLED` | daily 07:00 Asia/Dubai digest |
| `VAT_ENGINE_ENABLED` / `VAT_EXTRACT_MAX_FILES` | removable VAT module |
| `OPERATOR_EMAIL` / `OPERATOR_INITIAL_PASSWORD` | read once by the operator-console migration at first start |
| `DEFAULT_TRIAL_SEATS` | trial seat default for new tenants |
| `ACCESS_TOKEN_TTL_MIN` / `REFRESH_TOKEN_TTL_DAYS` / `SET_PASSWORD_TOKEN_TTL_HOURS` | token lifetimes (defaults are fine; listed for completeness) |
| `FILES_DIR` | only used when `AZURE_BLOB_CONN` is empty — do not set in prod |

## 3. Ingress & URL

`--ingress external --target-port 8000` gives HTTPS-only ingress at
`https://baton-api.<hash>.uaenorth.azurecontainerapps.io`. Get it with:

```bash
az containerapp show -n baton-api -g baton-prod --query properties.configuration.ingress.fqdn -o tsv
```

## 4. CORS / frontend pairing — set both, together

- The frontend production build must be made with `VITE_API_URL=https://<fqdn from §3>` —
  the **Container Apps URL**, not the old `baton-api.azurewebsites.net`.
- The API's `FRONTEND_ORIGIN` must exactly match the origin serving that frontend build
  (scheme + host, no trailing slash; if the frontend is also hosted on Container Apps, that is
  its `*.azurecontainerapps.io` URL).

These are a matching pair: a stale value in either one blocks every API call with a CORS error.
Any later custom-domain move (api.batonapp.com / app.batonapp.com) means updating both again.

## 5. CI (GitHub Actions)

Workflow on pushes touching `api/**`: `azure/login` with an `AZURE_CREDENTIALS` service-principal
secret, then `az containerapp up --source api` (same flags as §2, minus `--env-vars` — values
persist on the app between deploys). Frontend workflow and the demo-site workflow stay as in the
original Phase 5.

## 6. Smoke test (unchanged from original Phase 5, new URL)

`scripts/smoke.sh` — health, login with a seeded test user, create + advance a proposal one
step, upload + link a file. Point it at the Container Apps URL:

```bash
API_URL="https://$(az containerapp show -n baton-api -g baton-prod --query properties.configuration.ingress.fqdn -o tsv)" \
  ./scripts/smoke.sh
```

Run it against production and show output.

## 7. Data import & go-live

- Import: `POST /admin/import-baton-json` with the client's exported prototype JSON if provided;
  otherwise run the setup wizard live with the client.
- Write GO-LIVE-CHECKLIST.md: DNS/custom domain (Container Apps custom domain + managed cert),
  first Admin login + forced resets, backup verification (restore drill note), support/update
  loop (branch → preview → merge → deploy), rollback — Container Apps keeps prior **revisions**:
  `az containerapp revision list` then route traffic back to the last good revision
  (`az containerapp ingress traffic set`); DB migrations still require a down-revision.

## Carry-overs

- Gate or disable `/tenants/bootstrap` in production (env `BOOTSTRAP_KEY` check) — still open.
- `GET /health` currently returns a static ok; extend it to ping the DB as originally specced.
