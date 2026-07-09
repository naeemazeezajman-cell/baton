# Security & Compliance Notes (v1)
- Passwords: bcrypt (cost 12); temp passwords random 12+ chars; must_reset enforced server-side.
- JWT: HS256 with 256-bit secret from env; access 30 min, refresh 14 days, rotation on refresh.
- Transport: HTTPS only (App Service setting + HSTS header); CORS restricted to the frontend origin.
- Tenancy: every query tenant-scoped from the JWT; cross-tenant tests mandatory in CI.
- Files: private Blob container; access only via 15-min SAS after authorization; signature specimens additionally logged per view/use (signature_uses).
- Append-only: DB grants deny UPDATE/DELETE on event/log tables to the app role.
- Secrets: only in App Service configuration (or Key Vault later) — never in the repo; .env.example documents names only.
- Backups: Azure PG 7-day PITR; monthly manual restore drill to a scratch server.
- PDPL: data resident in Azure UAE North; processors: Microsoft Azure, Anthropic (payment-terms text only — no client documents are sent to the AI), email provider (recipient addresses + message bodies). Document these in the client agreement.
- Rate limiting: slowapi on /auth/* (5/min/IP) in v1.
- Frontend token storage (Phase 4): the access token (30 min) is held in memory only; the
  refresh token (14 days) is stored in localStorage so the session survives a page refresh.
  httpOnly cookies are not available on static hosting, so an XSS compromise could read the
  refresh token — mitigations: no third-party scripts, React's escaping, CSP at go-live,
  short access-token life, and refresh rotation server-side. Revisit with a BFF/cookie
  session if the frontend later moves behind the API's own origin.
