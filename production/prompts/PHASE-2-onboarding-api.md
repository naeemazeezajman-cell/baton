# Phase 2 — Onboarding workflow API

The behavioural specification is src/baton-prototype.jsx (the reducer/action functions) plus docs/PRODUCT-SPEC.md. Implement in api/:

1. Proposals router with ACTION endpoints per production/api/STRUCTURE.md §1 — every transition validates role, holder, and current status; invalid → 409 {reason}. Holder changes per §2 (transactional holder_log + event).
2. Checklist slot lifecycle exactly as the prototype: request-items (manager←staff), provide, waive request/approve/still-required, reject-with-reason, withdraw-with-reason (allowed while baton is with the other side).
3. Document generation: /generate composes the proposal (or EL) JSON, calls Anthropic messages API server-side (env ANTHROPIC_API_KEY) to professionalize payment terms — model claude-sonnet-4-6, temperature 0, prompt: rewrite rough terms into formal client-ready wording, preserve every figure exactly, return only the rewritten text. On any error use the raw text. Store version metadata + field-level diff vs previous version in a proposal_event (kind=diff).
4. Files router per §6 (Azure Blob; local filesystem fallback when AZURE_BLOB_CONN unset). upload-signed endpoint performs the conversion: prospect→client row, status flip, EL prepared, events written.
5. Signatures: sign endpoints verify the caller is the signatory, write signature_uses, embed specimen ref into the version metadata.
6. EL flow: staff-activity assignments (with workload summary endpoint GET /users/workload), el-route (senior/Admin only), el-sign, el-send (writes payment schedule rows per the prototype's basis rules).
7. Emails: client sends (proposal/EL) accept {to, subject, body, attach version id} — send via ACS and log as proposal_event kind=email.
8. Tests: full happy path (request→…→el_sent) as one integration test; rejection loop; dirty-version guard (submit requires latest generated version id); conversion on upload-signed.

Do not modify Phase 1 auth/tenancy code beyond adding routers. Show test output when done.
