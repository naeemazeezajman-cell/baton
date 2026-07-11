"""VAT Filing Engine — separate, removable module. Template parsing, reconciliation
buckets (tolerance + window rule), the computation gate, computation math, duty
completion pre-fill, the env flag, and tenancy."""

import io
from datetime import date

from .conftest import bootstrap_tenant, login_after_reset
from .test_onboarding import setup_firm

H = lambda ctx, who: ctx[who]["headers"]  # noqa: E731

LEDGER_ROWS = [
    # Invoice No, Invoice Date, Party Name, TRN, Emirate, Net, VAT, Type
    ["INV-001", date(2026, 5, 10), "Acme LLC", "TRN-1", "Dubai", 1000, 50, "Output"],
    ["INV-002", date(2026, 6, 15), "Beta FZE", "TRN-2", "Sharjah", 2000, 100, "Output"],
    ["INV-003", date(2026, 7, 1), "Gamma DMCC", "TRN-3", "Dubai", 3000, 150, "Output"],
    ["INV-004", date(2026, 5, 20), "Delta Est", "TRN-4", "Ajman", 500, 25, "Output"],
    ["PUR-001", date(2026, 6, 1), "Supplier Co", "TRN-5", "Dubai", 800, 40, "Input"],
    ["OLD-001", date(2026, 1, 15), "Ancient LLC", "TRN-6", "Dubai", 999, 49.95, "Output"],
]
REGISTER_ROWS = [
    # Invoice No, Invoice Date, Party, Emirate, Net, VAT, Notes
    ["INV-001", date(2026, 5, 10), "Acme LLC", "Dubai", 1000, 50, ""],
    ["inv-002 ", date(2026, 6, 16), "Beta FZE", "Sharjah", 2000, 100.01, "case+space+tolerance; date differs — never matched on"],
    ["INV-003", date(2026, 7, 1), "Gamma DMCC", "Dubai", 3000, 151, "VAT differs beyond tolerance"],
    ["INV-900", date(2026, 6, 20), "Mystery Co", "Dubai", 700, 35, "not in ledger"],
    ["OLD-900", date(2026, 1, 2), "Ancient LLC", "Dubai", 400, 20, "out of window"],
]


def make_vat_duty(client, ctx, next_due="2026-08-28T00:00:00Z", cadence="quarterly"):
    r = client.post("/duties", json={
        "staff_id": ctx["staff"]["id"], "client_name": "Gulf Horizon Trading LLC",
        "service": "VAT Filing", "cadence": cadence, "next_due": next_due,
        "contact": {"name": "Mariam", "email": "accounts@gulfhorizon.ae"},
    }, headers=H(ctx, "manager"))
    assert r.status_code == 201, r.text
    return r.json()


def open_filing(client, ctx, duty_id):
    r = client.post("/vat-engine/filings/open", json={"duty_id": duty_id}, headers=H(ctx, "staff"))
    assert r.status_code == 200, r.text
    return r.json()


def get_template(client, ctx, which):
    r = client.get(f"/vat-engine/templates/{which}", headers=H(ctx, "staff"))
    assert r.status_code == 200
    assert r.content[:2] == b"PK"  # xlsx = zip container
    return r.content


def fill_template(template_bytes, rows):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(template_bytes))
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload_ledger(client, ctx, fid, xlsx, expect=200):
    r = client.post(f"/vat-engine/filings/{fid}/ledger",
                    files={"file": ("Ledger.xlsx", xlsx,
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    headers=H(ctx, "staff"))
    assert r.status_code == expect, r.text
    return r.json()


def upload_register(client, ctx, fid, xlsx, expect=200, pdfs=()):
    files = [("file", ("Register.xlsx", xlsx,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))]
    for name in pdfs:
        files.append(("evidence", (name, b"%PDF-1.4 evidence", "application/pdf")))
    r = client.post(f"/vat-engine/filings/{fid}/invoices", files=files, headers=H(ctx, "staff"))
    assert r.status_code == expect, r.text
    return r.json()


def drive_to_reconciled(client, ctx):
    duty = make_vat_duty(client, ctx)
    f = open_filing(client, ctx, duty["id"])
    ledger = fill_template(get_template(client, ctx, "ledger"), LEDGER_ROWS)
    upload_ledger(client, ctx, f["id"], ledger)
    register = fill_template(get_template(client, ctx, "invoice-register"), REGISTER_ROWS)
    f = upload_register(client, ctx, f["id"], register, pdfs=["inv-001.pdf"])
    return duty, f


def resolve_all_differences(client, ctx, f):
    items = {(i["source"], i["invoice_no"]): i for i in f["items"]}
    # INV-003 ledger side: request from client → mark resolved (include in filing)
    lid = items[("ledger", "INV-003")]["id"]
    r = client.post(f"/vat-engine/filings/{f['id']}/items/{lid}/request-invoice",
                    json={"to": "accounts@gulfhorizon.ae", "subject": "Missing invoice INV-003",
                          "body": "Please send INV-003."}, headers=H(ctx, "staff"))
    assert r.status_code == 200, r.text
    r = client.post(f"/vat-engine/filings/{f['id']}/items/{lid}/resolve", headers=H(ctx, "staff"))
    assert r.status_code == 200
    # the register-side INV-003 row, INV-004 and INV-900 are excluded with reasons
    for key in (("invoice", "INV-003"), ("ledger", "INV-004"), ("invoice", "INV-900")):
        iid = items[key]["id"]
        r = client.post(f"/vat-engine/filings/{f['id']}/items/{iid}/exclude",
                        json={"reason": f"Not part of this filing — {key[1]}"}, headers=H(ctx, "staff"))
        assert r.status_code == 200, r.text
    return client.get(f"/vat-engine/filings/{f['id']}", headers=H(ctx, "staff")).json()


def test_period_derivation_staggered_quarter(client):
    ctx = setup_firm(client)
    duty = make_vat_duty(client, ctx, next_due="2026-08-28T00:00:00Z")  # staggered quarter
    f = open_filing(client, ctx, duty["id"])
    assert f["period_start"] == "2026-05-01" and f["period_end"] == "2026-07-31"
    assert f["prev_period_start"] == "2026-02-01"
    assert f["status"] == "ledgers_pending" and f["holder"] == ctx["staff"]["id"]
    # opening again returns the same filing, not a duplicate
    again = open_filing(client, ctx, duty["id"])
    assert again["id"] == f["id"]


def test_template_parse_happy_and_malformed(client):
    ctx = setup_firm(client)
    duty = make_vat_duty(client, ctx)
    f = open_filing(client, ctx, duty["id"])
    tpl = get_template(client, ctx, "ledger")

    # malformed: wrong columns entirely
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["whatever"])
    wb.active.append(["Wrong", "Columns", "Here"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"/vat-engine/filings/{f['id']}/ledger",
                    files={"file": ("bad.xlsx", buf.getvalue(), "application/octet-stream")},
                    headers=H(ctx, "staff"))
    assert r.status_code == 422
    assert "Columns don't match the template" in r.json()["detail"]["reason"]

    # malformed rows: bad date, bad type, non-numeric amount → row-level errors, nothing stored
    bad = fill_template(tpl, [
        ["INV-A", "not-a-date", "Party", "T", "Dubai", 100, 5, "Output"],
        ["INV-B", date(2026, 6, 1), "Party", "T", "Dubai", 100, 5, "Sideways"],
        ["INV-C", date(2026, 6, 1), "Party", "T", "Atlantis", 100, 5, "Output"],
        ["INV-D", date(2026, 6, 1), "Party", "T", "Dubai", "lots", 5, "Output"],
    ])
    r = client.post(f"/vat-engine/filings/{f['id']}/ledger",
                    files={"file": ("bad2.xlsx", bad, "application/octet-stream")}, headers=H(ctx, "staff"))
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert len(errors) == 4
    assert any("Row 3" in e and "not a valid date" in e for e in errors)
    assert any("Row 4" in e and "Type must be Output or Input" in e for e in errors)
    assert any("Row 5" in e and "Emirate" in e for e in errors)
    assert any("Row 6" in e and "must be numbers" in e for e in errors)
    detail = client.get(f"/vat-engine/filings/{f['id']}", headers=H(ctx, "staff")).json()
    assert detail["status"] == "ledgers_pending" and detail["items"] == []

    # happy: parses, advances, counts Output/Input
    good = fill_template(tpl, LEDGER_ROWS)
    out = upload_ledger(client, ctx, f["id"], good)
    assert out["status"] == "invoices_pending"
    assert out["ledger_file"]["rows"] == len(LEDGER_ROWS)
    assert sum(1 for i in out["items"] if i["type"] == "Input") == 1


def test_recon_buckets_tolerance_and_window_rule(client):
    ctx = setup_firm(client)
    _, f = drive_to_reconciled(client, ctx)
    assert f["status"] == "reconciled"
    # matched: INV-001 exact; INV-002 via normalization + ±0.01 tolerance (dates differ — never matched on)
    assert f["recon"]["matched"] == 2
    # ledger-only: INV-003 (VAT differs beyond tolerance) + INV-004 (absent from register)
    assert f["recon"]["ledger_only"] == 2
    # invoice-only: register INV-003 (unmatched counterpart) + INV-900
    assert f["recon"]["invoice_only"] == 2
    # out of window: OLD-001 (ledger) + OLD-900 (register), dated before 2026-02-01
    assert f["recon"]["out_of_window"] == 2
    by = {(i["source"], i["invoice_no"]): i for i in f["items"]}
    assert by[("ledger", "INV-002")]["bucket"] == "matched"
    assert by[("ledger", "OLD-001")]["bucket"] == "out_of_window" and not by[("ledger", "OLD-001")]["included"]
    assert by[("invoice", "OLD-900")]["bucket"] == "out_of_window"
    # Input (purchase) rows never register-match — no bucket, still included
    assert by[("ledger", "PUR-001")]["bucket"] is None and by[("ledger", "PUR-001")]["included"]
    # the reconciliation workbook exists and is a valid xlsx with the four sheets
    import io as _io
    from openpyxl import load_workbook
    from app.db import engine
    from sqlalchemy import text as sql
    with engine.begin() as conn:
        blob_path = conn.execute(sql("SELECT blob_path FROM files WHERE id = :i"),
                                 {"i": f["recon"]["excel_file_id"]}).scalar()
    from app import blobs
    wb = load_workbook(_io.BytesIO(blobs.read_blob(blob_path)))
    assert wb.sheetnames == ["Summary", "Matched", "Differences", "Excluded"]


def test_computation_gate_blocks_until_resolved(client):
    ctx = setup_firm(client)
    _, f = drive_to_reconciled(client, ctx)
    r = client.post(f"/vat-engine/filings/{f['id']}/draft-computation", headers=H(ctx, "staff"))
    assert r.status_code == 409
    assert "unresolved" in r.json()["detail"]["reason"]
    f = resolve_all_differences(client, ctx, f)
    assert f["unresolved_differences"] == 0
    r = client.post(f"/vat-engine/filings/{f['id']}/draft-computation", headers=H(ctx, "staff"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "computation_draft"


def test_computation_math_from_fixture(client):
    ctx = setup_firm(client)
    _, f = drive_to_reconciled(client, ctx)
    f = resolve_all_differences(client, ctx, f)
    c = client.post(f"/vat-engine/filings/{f['id']}/draft-computation",
                    headers=H(ctx, "staff")).json()["computation"]
    # included: INV-001 (1000/50 Dubai) + INV-002 (2000/100 Sharjah) + resolved INV-003
    # (3000/150 Dubai) + Input PUR-001 (800/40). Excluded INV-004; out-of-window OLD-001.
    assert c["output_vat"] == 300.0
    assert c["input_vat"] == 40.0
    assert c["net"] == 260.0 and c["position"] == "payable"
    assert c["taxable_sales"] == 6000.0
    assert c["per_emirate"]["Dubai"] == {"taxable_sales": 4000.0, "output_vat": 200.0, "rows": 2}
    assert c["per_emirate"]["Sharjah"] == {"taxable_sales": 2000.0, "output_vat": 100.0, "rows": 1}
    assert c["counts"]["included"] == 4 and c["counts"]["output_rows"] == 3 and c["counts"]["input_rows"] == 1
    assert c["counts"]["matched"] == 2 and c["counts"]["excluded"] == 3 and c["counts"]["out_of_window"] == 2
    assert c["period"] == "01 May 2026 – 31 Jul 2026"


def drive_to_complete(client, ctx):
    duty, f = drive_to_reconciled(client, ctx)
    f = resolve_all_differences(client, ctx, f)
    client.post(f"/vat-engine/filings/{f['id']}/draft-computation", headers=H(ctx, "staff"))
    r = client.post(f"/vat-engine/filings/{f['id']}/confirm-computation", headers=H(ctx, "staff"))
    assert r.status_code == 200 and r.json()["status"] == "awaiting_client_approval"
    r = client.post(f"/vat-engine/filings/{f['id']}/send-computation",
                    json={"to": "accounts@gulfhorizon.ae", "subject": "VAT computation for approval",
                          "body": "Please approve."}, headers=H(ctx, "staff"))
    assert r.status_code == 200
    r = client.post(f"/vat-engine/filings/{f['id']}/client-approval",
                    data={"basis": "email_approval", "note": "Approved by Mariam by email 11 Jul"},
                    headers=H(ctx, "staff"))
    assert r.status_code == 200 and r.json()["status"] == "ready_to_file"
    r = client.post(f"/vat-engine/filings/{f['id']}/file-at-fta",
                    data={"note": "Filed on portal"},
                    files=[("acknowledgement", ("FTA-ack.pdf", b"%PDF ack", "application/pdf"))],
                    headers=H(ctx, "staff"))
    assert r.status_code == 200, r.text
    return duty, r.json()


def test_completion_prefills_duty_record_and_rolls_schedule(client):
    ctx = setup_firm(client)
    duty, out = drive_to_complete(client, ctx)
    assert out["filing"]["status"] == "complete"
    # duty completed through the existing machinery with method=proof + pre-filled record
    duties = client.get("/duties", headers=H(ctx, "staff")).json()
    d = next(x for x in duties if x["id"] == duty["id"])
    assert d["next_due"].startswith("2026-11-28")  # quarterly roll from 28 Aug
    comp = d["history"][-1]
    assert comp["method"] == "proof"
    rec = comp["record"]
    assert rec["period"] == "01 May 2026 – 31 Jul 2026"
    assert rec["position"] == "payable"
    assert rec["net VAT (AED)"] == "260.00"
    assert rec["output VAT (AED)"] == "300.00" and rec["input VAT (AED)"] == "40.00"
    assert "Dubai 4,000.00" in rec["taxable sales per emirate"]
    assert "Sharjah 2,000.00" in rec["taxable sales per emirate"]
    assert comp["evidence"][0]["name"] == "FTA-ack.pdf"
    assert any("Trail sealed" in e["text"] for e in out["filing"]["events"])


def test_sealed_filing_blocks_mutations(client):
    ctx = setup_firm(client)
    _, out = drive_to_complete(client, ctx)
    fid = out["filing"]["id"]
    item_id = out["filing"]["items"][0]["id"]
    tpl = fill_template(get_template(client, ctx, "ledger"), LEDGER_ROWS)
    assert upload_ledger(client, ctx, fid, tpl, expect=409)
    r = client.post(f"/vat-engine/filings/{fid}/items/{item_id}/exclude", json={"reason": "x"},
                    headers=H(ctx, "staff"))
    assert r.status_code == 409
    r = client.post(f"/vat-engine/filings/{fid}/file-at-fta", data={"note": ""},
                    files=[("acknowledgement", ("a.pdf", b"%PDF", "application/pdf"))],
                    headers=H(ctx, "staff"))
    assert r.status_code == 409
    # read stays open
    assert client.get(f"/vat-engine/filings/{fid}", headers=H(ctx, "staff")).status_code == 200


def test_flag_off_404s_everything(client, monkeypatch):
    # enabled by default: unauthenticated hits the auth wall, not a 404
    assert client.get("/vat-engine/status").status_code == 401
    monkeypatch.setenv("VAT_ENGINE_ENABLED", "false")
    assert client.get("/vat-engine/status").status_code == 404
    assert client.get("/vat-engine/templates/ledger").status_code == 404
    assert client.get("/vat-engine/filings").status_code == 404
    assert client.post("/vat-engine/filings/open", json={}).status_code == 404
    monkeypatch.delenv("VAT_ENGINE_ENABLED")
    assert client.get("/vat-engine/status").status_code == 401  # back on


def test_tenancy_isolation(client):
    ctx = setup_firm(client)
    duty = make_vat_duty(client, ctx)
    f = open_filing(client, ctx, duty["id"])

    boot_b = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")
    admin_b = next(u for u in boot_b["users"] if u["role"] == "Admin")
    tokens_b = login_after_reset(client, admin_b["email"], admin_b["temp_password"])
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    assert client.get(f"/vat-engine/filings/{f['id']}", headers=headers_b).status_code == 404
    assert f["id"] not in {x["id"] for x in client.get("/vat-engine/filings", headers=headers_b).json()}
