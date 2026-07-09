# Frontend — Storage Swap Plan (localStorage → API)

Goal: keep the UI and workflow behaviour of src/baton-prototype.jsx pixel-for-pixel, replace the
data layer. The prototype file remains in the repo as the behavioural spec; production frontend
lives in src/ as a small set of modules extracted from it.

## New modules
- `src/api.js` — fetch wrapper: base URL from VITE_API_URL, attaches JWT from memory,
  auto-refresh on 401 (one retry), JSON + multipart helpers, typed error surface.
- `src/auth.jsx` — real Login screen (email + password), forced-reset screen, session context
  (user, tenant, logout). Replaces the pick-a-user simulation. Keep a `VITE_DEMO_MODE=true`
  build flag that restores the old in-memory simulation for the public portfolio demo.
- `src/state.jsx` — replaces the App-level useState blocks: server data fetched per screen
  (dashboard, proposals list, detail, duties, payments, admin) with a tiny cache + refetch-on-action
  pattern. Every prototype `actions.*` mutation becomes an API call followed by refetch of the
  affected entity. No optimistic writes in v1 — correctness first.

## Mechanical mapping (prototype → API)
- actions.createRequest        → POST /proposals
- slot provide/waive/reject/…  → POST /proposals/{id}/(provide-item|waive|reject-item|withdraw-item)
- generate / polish terms      → POST /proposals/{id}/generate (server calls Anthropic)
- submit / sign-route / senior → POST action endpoints; UI just renders returned proposal
- upload client-signed         → multipart POST /proposals/{id}/upload-signed
- staffing / EL flow           → POST /proposals/{id}/(staff-activity|el-route|el-sign|el-send)
- markDutyDone                 → multipart POST /duties/{id}/complete (fields + evidence files)
- payments actions             → POST /payments/{id}/(invoice-raised|receipt)
- FilePick                     → uploads to POST /files (multipart), stores returned file id;
                                 FileLink fetches GET /files/{id}/link and opens the SAS URL
- +1 day / +7 days simulation  → REMOVED in production build (real time only); kept in demo mode
- SetupWizard                  → POST /tenants/bootstrap (one-time; disabled once tenant exists)
- Export/Import                → Admin keeps Export (GET /admin/export) as backup;
                                 welcome-screen import removed (server is the shared truth)

## Delete in production build
- localStorage persistence layer (loadState/saveState/clearState) — behind the demo-mode flag only.
- The pick-a-user Login, clock simulation, "load Crescent Bay" shortcut (demo mode only).

## Acceptance
Two different machines, two different users, same firm: an action on machine A is visible on
machine B after refresh. Priya completes a duty on her laptop; Imran's compliance board shows it.
