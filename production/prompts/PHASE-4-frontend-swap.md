# Phase 4 — Frontend storage swap

Read production/frontend/STORAGE-SWAP.md — it is the specification. Rules:

1. Create src/api.js, src/auth.jsx, src/state.jsx as specified. Extract the prototype's UI into the production entry (you may split components into files for maintainability, but DO NOT redesign, rename user-visible text, or "improve" any workflow — pixel-for-pixel behaviour).
2. Real login: email+password → JWT; forced-reset screen on MUST_RESET; logout; session survives refresh via refresh-token (memory + httpOnly is not possible on static hosting — store refresh token in localStorage, access token in memory, and note this in SECURITY.md).
3. Replace every prototype action with its API call per the mechanical mapping table; refetch affected entities after each action. FilePick uploads to /files; FileLink opens SAS links.
4. VITE_DEMO_MODE=true builds the old self-contained in-memory demo (pick-a-user, clock simulation, Crescent Bay shortcut) — this is what the public portfolio site keeps serving. Production build (flag false) removes simulation controls, localStorage persistence, and the demo shortcut.
5. env: VITE_API_URL. Update vite config so `npm run build` produces the production app and `npm run build:demo` the demo.
6. Acceptance: run api + frontend locally (docker-compose up, uvicorn, vite dev), bootstrap a firm via the wizard, log in as two different users in two browsers, and verify an action by one is visible to the other after refresh. Describe exactly what you tested.
