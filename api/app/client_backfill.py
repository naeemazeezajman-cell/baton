"""Backfill: pre-Baton duty clients become first-class client rows.

For every tenant, orphan duties (client_id IS NULL) get a client record: names are
deduplicated case-insensitively with whitespace collapsed; the client keeps the FIRST
duty's contact (later same-name duties with a different contact get a duty-trail note);
refs continue the tenant's normal CL- sequence. Connection-level (raw SQL) so it runs
identically from the alembic data migration and from tests. Idempotent."""

import json
import re

from sqlalchemy import text

PRE_BATON_BASIS = "pre-existing relationship (pre-Baton deployment)"


def _norm(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def backfill_pre_baton_clients(conn) -> int:
    """Returns the number of client rows created."""
    created = 0
    tenant_ids = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT tenant_id FROM duties WHERE client_id IS NULL")).all()]
    for tid in tenant_ids:
        by_norm = {_norm(name): (cid, contact) for cid, name, contact in conn.execute(text(
            "SELECT id, name, contact FROM clients WHERE tenant_id = :t"), {"t": tid}).all()}
        count = conn.execute(text("SELECT count(*) FROM clients WHERE tenant_id = :t"),
                             {"t": tid}).scalar()
        # "first registered" = the duty's earliest trail event (registration), so the first
        # duty's contact deterministically becomes the client contact
        duties = conn.execute(text(
            "SELECT d.id, d.client_name, d.contact FROM duties d "
            "LEFT JOIN (SELECT duty_id, min(at) AS first_at FROM duty_events GROUP BY duty_id) e "
            "  ON e.duty_id = d.id "
            "WHERE d.tenant_id = :t AND d.client_id IS NULL "
            "ORDER BY e.first_at NULLS LAST, d.next_due, d.id"), {"t": tid}).all()
        for did, cname, contact in duties:
            key = _norm(cname)
            if key not in by_norm:
                count += 1
                cid = conn.execute(text(
                    "INSERT INTO clients (tenant_id, ref, name, contact, origin, confirmation_basis) "
                    "VALUES (:t, :r, :n, cast(:c AS jsonb), 'pre_baton', :b) RETURNING id"),
                    {"t": tid, "r": f"CL-{count:03d}", "n": re.sub(r"\s+", " ", cname.strip()),
                     "c": json.dumps(contact or {}), "b": PRE_BATON_BASIS}).scalar()
                by_norm[key] = (cid, contact)
                created += 1
            else:
                cid, first_contact = by_norm[key]
                if (contact or {}) and (contact or {}) != (first_contact or {}):
                    conn.execute(text(
                        "INSERT INTO duty_events (tenant_id, duty_id, by_user, text) "
                        "VALUES (:t, :d, NULL, :x)"),
                        {"t": tid, "d": did,
                         "x": "Linked to the existing client record during the pre-Baton client "
                              "backfill — the client record keeps the contact from the first "
                              "registered duty; this duty's own contact is retained on the duty."})
            conn.execute(text("UPDATE duties SET client_id = :c WHERE id = :d"), {"c": cid, "d": did})
    return created
