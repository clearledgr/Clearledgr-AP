"""Tests for Wave 3 / E4 — JE preview on approval cards.

Covers:
  * build_je_preview() shapes by treatment:
      - domestic: Dr expense (net) + Dr VAT (vat) + Cr AP (gross)
      - reverse_charge: Dr expense (gross) + Cr AP + Dr/Cr VAT pair
      - zero_rated / exempt / out_of_scope: Dr expense (gross) + Cr AP
      - missing VAT split (legacy): falls back to net=gross + note.
  * Balance: debit_total == credit_total for every treatment.
  * GL account map override: per-org map wins over DEFAULT_ACCOUNT_MAP.
  * render_je_preview_text() format: human-readable, balanced flag,
    treatment label, plain text suitable for Slack/Gmail/Teams.
  * API: GET /ap-items/{id}/journal-entry-preview, cross-org 404,
    erp override.
  * Slack approval block embeds the preview when provided in
    extra_context.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import journal_entry_preview as je_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.approval_card_builder import build_approval_blocks  # noqa: E402
from clearledgr.services.invoice_models import InvoiceData  # noqa: E402
from clearledgr.services.journal_entry_preview import (  # noqa: E402
    build_je_preview,
    render_je_preview_text,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta DE GmbH")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(je_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_ap_item(
    db, *,
    item_id: str,
    treatment: str = "domestic",
    gross: float = 120.0,
    net: float = 100.0,
    vat: float = 20.0,
    rate: float = 20.0,
    code: str = "T1",
    org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": gross,
        "currency": "GBP",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    db.update_ap_item(
        item["id"],
        net_amount=Decimal(str(net)),
        vat_amount=Decimal(str(vat)),
        vat_rate=Decimal(str(rate)),
        vat_code=code,
        tax_treatment=treatment,
        bill_country="GB",
    )
    return db.get_ap_item(item["id"])


# ─── build_je_preview() — treatments ───────────────────────────────


def test_domestic_three_line_preview(db):
    item = _make_ap_item(db, item_id="AP-je-dom")
    preview = build_je_preview(ap_item=item, erp_type="xero")
    assert preview.treatment == "domestic"
    assert len(preview.lines) == 3
    roles = [(ln.line_role, ln.direction) for ln in preview.lines]
    assert roles == [
        ("expense", "debit"),
        ("vat_input", "debit"),
        ("accounts_payable", "credit"),
    ]
    debit_amounts = {ln.line_role: ln.amount for ln in preview.lines if ln.direction == "debit"}
    assert debit_amounts["expense"] == Decimal("100.00")
    assert debit_amounts["vat_input"] == Decimal("20.00")
    credit_amount = next(ln.amount for ln in preview.lines if ln.direction == "credit")
    assert credit_amount == Decimal("120.00")
    assert preview.balanced is True


def test_reverse_charge_four_line_preview(db):
    item = _make_ap_item(
        db, item_id="AP-je-rc",
        treatment="reverse_charge",
        gross=1000.0, net=1000.0, vat=190.0, rate=19.0, code="RC",
    )
    preview = build_je_preview(ap_item=item, erp_type="xero")
    roles = [(ln.line_role, ln.direction, ln.amount) for ln in preview.lines]
    assert ("expense", "debit", Decimal("1000.00")) in roles
    assert ("accounts_payable", "credit", Decimal("1000.00")) in roles
    assert ("vat_input", "debit", Decimal("190.00")) in roles
    assert ("vat_output", "credit", Decimal("190.00")) in roles
    assert preview.balanced is True
    assert any("reverse charge" in n.lower() for n in preview.notes)


def test_zero_rated_two_line_preview(db):
    item = _make_ap_item(
        db, item_id="AP-je-zr",
        treatment="zero_rated",
        gross=500.0, net=500.0, vat=0.0, rate=0.0, code="T0",
    )
    preview = build_je_preview(ap_item=item, erp_type="xero")
    assert len(preview.lines) == 2
    assert preview.balanced is True
    assert any("zero-rated" in n.lower() for n in preview.notes)


def test_exempt_two_line_preview(db):
    item = _make_ap_item(
        db, item_id="AP-je-ex",
        treatment="exempt",
        gross=300.0, net=300.0, vat=0.0, rate=0.0, code="T2",
    )
    preview = build_je_preview(ap_item=item, erp_type="xero")
    assert len(preview.lines) == 2
    assert any("exempt" in n.lower() for n in preview.notes)


def test_out_of_scope_two_line_preview(db):
    item = _make_ap_item(
        db, item_id="AP-je-oos",
        treatment="out_of_scope",
        gross=400.0, net=400.0, vat=0.0, rate=0.0, code="OO",
    )
    preview = build_je_preview(ap_item=item, erp_type="xero")
    assert len(preview.lines) == 2
    assert any("out of scope" in n.lower() for n in preview.notes)


def test_missing_split_falls_back_to_gross(db):
    """Legacy AP item with no VAT split: net defaults to gross,
    preview surfaces a note pointing at the recalc endpoint."""
    item = db.create_ap_item({
        "id": "AP-je-legacy",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 250.0,
        "currency": "GBP",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    fresh = db.get_ap_item(item["id"])
    preview = build_je_preview(ap_item=fresh, erp_type="xero")
    assert preview.balanced is True
    assert any(
        "vat split not computed" in n.lower() or "vat-recalculate" in n.lower()
        for n in preview.notes
    )


def test_gl_map_override_wins(db):
    item = _make_ap_item(db, item_id="AP-je-glmap")
    custom = {
        "expenses": "9999",
        "accounts_payable": "8888",
        "vat_input": "7777",
    }
    preview = build_je_preview(
        ap_item=item, erp_type="xero", gl_account_map=custom,
    )
    codes = {ln.line_role: ln.account_code for ln in preview.lines}
    assert codes["expense"] == "9999"
    assert codes["accounts_payable"] == "8888"
    assert codes["vat_input"] == "7777"


# ─── render_je_preview_text() ──────────────────────────────────────


def test_text_render_contains_lines_and_balance(db):
    item = _make_ap_item(db, item_id="AP-je-text")
    preview = build_je_preview(ap_item=item, erp_type="xero")
    text = render_je_preview_text(preview)
    assert "Dr" in text
    assert "Cr" in text
    assert "100.00" in text
    assert "120.00" in text
    assert "balanced" in text.lower()
    assert "domestic" in text.lower()


# ─── API ────────────────────────────────────────────────────────────


def test_api_get_preview_for_ap_item(db, client_orgA):
    item = _make_ap_item(db, item_id="AP-je-api")
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{item['id']}/journal-entry-preview",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["treatment"] == "domestic"
    assert data["balanced"] is True
    assert len(data["lines"]) == 3
    assert "rendered_text" in data
    assert "Journal Entry preview" in data["rendered_text"]


def test_api_get_preview_with_erp_override(db, client_orgA):
    item = _make_ap_item(db, item_id="AP-je-erp-override")
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{item['id']}/journal-entry-preview"
        f"?erp=quickbooks",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["erp_type"] == "quickbooks"
    # QB default expense code is "7" per DEFAULT_ACCOUNT_MAP.
    expense_lines = [
        ln for ln in data["lines"] if ln["line_role"] == "expense"
    ]
    assert expense_lines and expense_lines[0]["account_code"] == "7"


def test_api_unknown_ap_item_404(client_orgA):
    resp = client_orgA.get(
        "/api/workspace/ap-items/AP-does-not-exist/journal-entry-preview",
    )
    assert resp.status_code == 404


def test_api_cross_org_404(db, client_orgA):
    other = _make_ap_item(db, item_id="AP-je-cross", org="orgB")
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{other['id']}/journal-entry-preview",
    )
    assert resp.status_code == 404


# ─── Slack block embed ──────────────────────────────────────────────


def test_slack_blocks_embed_je_preview_when_provided(db):
    """When extra_context carries journal_entry_preview, the Slack
    Block Kit output includes a section block with the rendered text."""
    item = _make_ap_item(db, item_id="AP-je-slack")
    preview = build_je_preview(ap_item=item, erp_type="xero")

    invoice = InvoiceData(
        gmail_id="gmail-1",
        sender="ap@vendor-x.com",
        vendor_name=item["vendor_name"],
        amount=float(item["amount"]),
        currency=item["currency"],
        invoice_number=item.get("invoice_number"),
        confidence=0.95,
    )
    blocks = build_approval_blocks(
        invoice,
        extra_context={"journal_entry_preview": preview.to_dict()},
    )
    section_texts = [
        b.get("text", {}).get("text", "")
        for b in blocks if b.get("type") == "section"
    ]
    matching = [t for t in section_texts if "Journal Entry preview" in t]
    assert matching, "JE preview block missing from approval blocks"
    assert "balanced" in matching[0].lower()
    assert "100.00" in matching[0]
