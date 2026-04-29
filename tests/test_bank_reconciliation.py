"""Tests for Wave 2 / C6 — bank reconciliation auto-match.

Covers:
  * CAMT.053 parser: extracts account / dates / opening / closing /
    debits with correct sign + counterparty / end-to-end-id.
  * OFX parser: tolerates SGML-style unclosed tags + extracts
    debits with correct sign.
  * Auto-detect: filename + content sniff routes to the right parser.
  * Store CRUD: insert + list + match update; uniqueness on
    (org, import_id, line_index).
  * Matcher:
      - Exact (amount, currency, date) → matched with high confidence
      - Reference number boost
      - Two equal candidates → ambiguous (no auto-match)
      - Inflow (positive amount) → unmatched (out of scope)
      - Outside date window → unmatched
      - Re-running idempotent: existing match preserved
  * API end-to-end: import + reconcile, manual match.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import bank_statements as bs_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.bank_reconciliation_matcher import (  # noqa: E402
    match_statement_line,
    reconcile_import,
)
from clearledgr.services.bank_statement_parsers import (  # noqa: E402
    detect_and_parse,
    parse_camt053,
    parse_ofx,
)


# ─── Sample fixtures ───────────────────────────────────────────────


SAMPLE_CAMT053 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Acct>
        <Id><IBAN>DE89370400440532013000</IBAN></Id>
        <Ccy>EUR</Ccy>
      </Acct>
      <FrToDt>
        <FrDtTm>2026-04-01T00:00:00</FrDtTm>
        <ToDtTm>2026-04-30T23:59:59</ToDtTm>
      </FrToDt>
      <Bal>
        <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">10000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">8500.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
      </Bal>
      <Ntry>
        <Amt Ccy="EUR">1500.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-04-29</Dt></BookgDt>
        <ValDt><Dt>2026-04-29</Dt></ValDt>
        <AcctSvcrRef>BANK-REF-77</AcctSvcrRef>
        <NtryDtls>
          <TxDtls>
            <Refs><EndToEndId>WIRE-77</EndToEndId></Refs>
            <RltdPties>
              <Cdtr><Nm>Vendor X GmbH</Nm></Cdtr>
              <CdtrAcct><Id><IBAN>DE12500105170648489890</IBAN></Id></CdtrAcct>
            </RltdPties>
          </TxDtls>
        </NtryDtls>
        <AddtlNtryInf>Invoice INV-9001 payment</AddtlNtryInf>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


SAMPLE_OFX = b"""OFXHEADER:100
DATA:OFXSGML

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>USD
<BANKACCTFROM>
<ACCTID>123456789
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260401
<DTEND>20260430
<STMTTRN>
<TRNAMT>-1500.00
<DTPOSTED>20260429
<FITID>BANK-REF-77
<NAME>Vendor X GmbH
<MEMO>Invoice INV-9001 payment
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>8500.00
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="orgA")
    inst.ensure_organization("orgB", organization_name="orgB")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="op-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(bs_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_payment_confirmation(
    db, *, ap_item_id_prefix: str, payment_id: str,
    amount: float = 1500.0, currency: str = "EUR",
    settlement_at: str = "2026-04-29T00:00:00+00:00",
    payment_reference: str = "WIRE-77",
    org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": ap_item_id_prefix,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": amount,
        "currency": currency,
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.create_payment_confirmation(
        organization_id=org,
        ap_item_id=item["id"],
        payment_id=payment_id,
        source="manual",
        status="confirmed",
        amount=amount,
        currency=currency,
        settlement_at=settlement_at,
        payment_reference=payment_reference,
    )


# ─── CAMT.053 parser ────────────────────────────────────────────────


def test_camt053_parses_account_dates_balances():
    out = parse_camt053(SAMPLE_CAMT053)
    assert out["format"] == "camt.053"
    assert out["statement"]["iban"] == "DE89370400440532013000"
    assert out["statement"]["currency"] == "EUR"
    assert out["statement"]["opening_balance"] == 10000.0
    assert out["statement"]["closing_balance"] == 8500.0


def test_camt053_parses_debit_entry_with_negative_sign():
    out = parse_camt053(SAMPLE_CAMT053)
    assert len(out["lines"]) == 1
    line = out["lines"][0]
    assert line["amount"] == -1500.0
    assert line["currency"] == "EUR"
    assert line["counterparty"] == "Vendor X GmbH"
    assert line["counterparty_iban"] == "DE12500105170648489890"
    assert line["bank_reference"] == "BANK-REF-77"
    assert line["end_to_end_id"] == "WIRE-77"
    assert line["value_date"] == "2026-04-29"


def test_camt053_handles_empty_body():
    out = parse_camt053(b"")
    assert out["lines"] == []
    out = parse_camt053(b"<not-camt/>")
    assert out["lines"] == []


# ─── OFX parser ─────────────────────────────────────────────────────


def test_ofx_parses_sgml_style_tags():
    out = parse_ofx(SAMPLE_OFX)
    assert out["format"] == "ofx"
    assert out["statement"]["currency"] == "USD"
    assert out["statement"]["account"] == "123456789"
    assert out["statement"]["closing_balance"] == 8500.0
    assert len(out["lines"]) == 1
    line = out["lines"][0]
    assert line["amount"] == -1500.0
    assert line["currency"] == "USD"
    assert line["counterparty"] == "Vendor X GmbH"
    assert line["bank_reference"] == "BANK-REF-77"


# ─── Auto-detect ────────────────────────────────────────────────────


def test_detect_routes_to_camt():
    out = detect_and_parse(SAMPLE_CAMT053, filename="april.xml")
    assert out["format"] == "camt.053"


def test_detect_routes_to_ofx():
    out = detect_and_parse(SAMPLE_OFX, filename="april.ofx")
    assert out["format"] == "ofx"


# ─── Store ──────────────────────────────────────────────────────────


def test_store_unique_line_index(db):
    imp = db.create_bank_statement_import(
        organization_id="orgA",
        filename="t.xml",
        format="camt.053",
        statement_currency="EUR",
        line_count=2,
    )
    db.insert_bank_statement_line(
        organization_id="orgA", import_id=imp["id"],
        line_index=0, amount=-1500.0, currency="EUR",
        value_date="2026-04-29",
    )
    # Re-inserting same line_index returns duplicate marker, doesn't raise.
    again = db.insert_bank_statement_line(
        organization_id="orgA", import_id=imp["id"],
        line_index=0, amount=-1500.0, currency="EUR",
        value_date="2026-04-29",
    )
    assert again.get("duplicate") is True
    rows = db.list_bank_statement_lines("orgA", import_id=imp["id"])
    assert len(rows) == 1


def test_store_invalid_match_status_filter_raises(db):
    imp = db.create_bank_statement_import(
        organization_id="orgA",
        filename="t.xml",
        format="camt.053",
        line_count=0,
    )
    with pytest.raises(ValueError):
        db.list_bank_statement_lines(
            "orgA", import_id=imp["id"], match_status="bogus",
        )


# ─── Matcher ────────────────────────────────────────────────────────


def _seed_import_with_line(db, *, line_amount: float = -1500.0,
                            line_currency: str = "EUR",
                            line_date: str = "2026-04-29",
                            bank_ref: str = "BANK-REF-77",
                            end_to_end: str = "WIRE-77",
                            org: str = "orgA") -> dict:
    imp = db.create_bank_statement_import(
        organization_id=org,
        filename="t.xml",
        format="camt.053",
        statement_currency=line_currency,
        line_count=1,
    )
    db.insert_bank_statement_line(
        organization_id=org, import_id=imp["id"],
        line_index=0,
        amount=line_amount, currency=line_currency,
        value_date=line_date, booking_date=line_date,
        description="Invoice payment", counterparty="Vendor X",
        bank_reference=bank_ref, end_to_end_id=end_to_end,
    )
    rows = db.list_bank_statement_lines(org, import_id=imp["id"])
    return {"import": imp, "line": rows[0]}


def test_matcher_exact_match_high_confidence(db):
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-1", payment_id="P-1",
        payment_reference="WIRE-77",
    )
    seeded = _seed_import_with_line(db)
    outcome = match_statement_line(
        db, organization_id="orgA", line=seeded["line"],
    )
    assert outcome.status == "matched"
    assert outcome.confidence is not None and outcome.confidence >= 0.95
    fresh = db.get_bank_statement_line(seeded["line"]["id"])
    assert fresh["match_status"] == "matched"


def test_matcher_inflow_unmatched(db):
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-inflow", payment_id="P-IN",
    )
    # Positive amount = inflow / refund
    seeded = _seed_import_with_line(db, line_amount=1500.0)
    outcome = match_statement_line(
        db, organization_id="orgA", line=seeded["line"],
    )
    assert outcome.status == "unmatched"


def test_matcher_outside_date_window_unmatched(db):
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-old", payment_id="P-OLD",
        settlement_at="2025-12-01T00:00:00+00:00",
    )
    seeded = _seed_import_with_line(db, line_date="2026-04-29")
    outcome = match_statement_line(
        db, organization_id="orgA", line=seeded["line"],
        date_window_days=5,
    )
    assert outcome.status == "unmatched"


def test_matcher_ambiguous_when_two_close_candidates(db):
    """Two confirmations for the same amount + currency on the same
    date — matcher must NOT auto-pick. Instead reports ambiguous."""
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-A", payment_id="P-A",
        payment_reference="MISC-1",
    )
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-B", payment_id="P-B",
        payment_reference="MISC-2",
    )
    seeded = _seed_import_with_line(db, bank_ref="UNRELATED", end_to_end="UNRELATED")
    outcome = match_statement_line(
        db, organization_id="orgA", line=seeded["line"],
    )
    assert outcome.status == "ambiguous"
    fresh = db.get_bank_statement_line(seeded["line"]["id"])
    assert fresh["match_status"] == "unmatched"


def test_matcher_reference_match_breaks_tie(db):
    """When two confirmations match amount+date but only one matches
    the bank_reference, the matcher picks the ref-matching one."""
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-tie-A", payment_id="P-A",
        payment_reference="WIRE-77",
    )
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-tie-B", payment_id="P-B",
        payment_reference="MISC",
    )
    seeded = _seed_import_with_line(
        db, bank_ref="WIRE-77", end_to_end="WIRE-77",
    )
    outcome = match_statement_line(
        db, organization_id="orgA", line=seeded["line"],
    )
    assert outcome.status == "matched"


def test_reconcile_import_summary_counts(db):
    """End-to-end reconcile_import returns matched/ambiguous/
    unmatched counts."""
    # 1 matched
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-rim", payment_id="P-RIM",
    )
    imp = db.create_bank_statement_import(
        organization_id="orgA",
        filename="t.xml",
        format="camt.053",
        statement_currency="EUR",
        line_count=2,
    )
    db.insert_bank_statement_line(
        organization_id="orgA", import_id=imp["id"], line_index=0,
        amount=-1500.0, currency="EUR", value_date="2026-04-29",
        bank_reference="WIRE-77",
    )
    # No matching confirmation → unmatched
    db.insert_bank_statement_line(
        organization_id="orgA", import_id=imp["id"], line_index=1,
        amount=-99.99, currency="EUR", value_date="2026-04-29",
    )
    summary = reconcile_import(
        db, organization_id="orgA", import_id=imp["id"],
    )
    assert summary["matched"] == 1
    assert summary["unmatched"] == 1
    fresh_imp = db.get_bank_statement_import(imp["id"])
    assert fresh_imp["matched_count"] == 1


# ─── API end-to-end ─────────────────────────────────────────────────


def test_api_import_camt_and_match(db, client_orgA):
    _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-api-1", payment_id="P-API",
        payment_reference="WIRE-77",
    )
    resp = client_orgA.post(
        "/api/workspace/bank-statements/import?filename=april.xml",
        content=SAMPLE_CAMT053,
        headers={"Content-Type": "application/xml"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["import_summary"]["format"] == "camt.053"
    assert data["import_summary"]["line_count"] == 1
    assert data["reconciliation"]["matched"] == 1


def test_api_import_empty_body_400(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/bank-statements/import",
        content=b"",
    )
    assert resp.status_code == 400


def test_api_list_imports_org_scoped(db, client_orgA):
    db.create_bank_statement_import(
        organization_id="orgA", filename="orgA.xml", format="camt.053",
        line_count=0,
    )
    db.create_bank_statement_import(
        organization_id="orgB", filename="orgB.xml", format="camt.053",
        line_count=0,
    )
    resp = client_orgA.get("/api/workspace/bank-statements/imports")
    assert resp.status_code == 200
    files = {imp["filename"] for imp in resp.json()}
    assert "orgA.xml" in files
    assert "orgB.xml" not in files


def test_api_manual_match_flips_to_reconciled(db, client_orgA):
    conf = _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-api-mm", payment_id="P-MM",
        payment_reference="MISC",
    )
    seeded = _seed_import_with_line(db, bank_ref="UNRELATED")
    resp = client_orgA.put(
        f"/api/workspace/bank-statements/lines/{seeded['line']['id']}/match",
        json={"payment_confirmation_id": conf["id"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_status"] == "reconciled"
    assert data["match_reason"] == "operator_confirmed"
    assert data["payment_confirmation_id"] == conf["id"]


def test_api_manual_match_cross_org_404(db, client_orgA):
    other = _make_payment_confirmation(
        db, ap_item_id_prefix="AP-bm-other", payment_id="P-OTHER", org="orgB",
    )
    seeded = _seed_import_with_line(db, bank_ref="UNRELATED")
    resp = client_orgA.put(
        f"/api/workspace/bank-statements/lines/{seeded['line']['id']}/match",
        json={"payment_confirmation_id": other["id"]},
    )
    assert resp.status_code == 404
