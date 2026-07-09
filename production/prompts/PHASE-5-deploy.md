# Phase 5 — Deploy & go-live

Prereq: I have completed production/AZURE-PROVISIONING.md (resources exist; I will paste secrets into App Service settings myself — never ask me for secret values and never commit them).

1. GitHub Actions: workflow deploying api/ to Azure App Service (publish profile secret AZURE_WEBAPP_PUBLISH_PROFILE) on pushes touching api/**; and the frontend production build to the chosen static host on pushes touching src/**. Keep the existing demo-site workflow serving the demo build.
2. Startup: App Service runs alembic upgrade head before uvicorn (startup command). Health endpoint GET /health (DB ping).
3. CORS locked to the production frontend origin. HTTPS only.
4. Smoke-test script (scripts/smoke.sh): health, login with a seeded test user, create+advance a proposal one step, upload+link a file. Run it against production and show output.
5. Data import: use POST /admin/import-baton-json with the client's exported prototype JSON if provided; otherwise I'll run the setup wizard live with the client.
6. Write GO-LIVE-CHECKLIST.md: DNS/custom domain steps, first Admin login + forced resets, backup verification (restore drill note), the support/update loop (branch → preview → merge → deploy), and rollback (revert commit → redeploy; DB migrations require a down-revision).

## Carry-overs

- Gate or disable /tenants/bootstrap in production (env BOOTSTRAP_KEY check).
