"""Tests for Wave 5 / G5 — auto-post accrual JE for received-not-billed.

Covers:
  * Empty period: no GRNs / no eligible POs → empty proposal with
    "no liability identified" note, balanced totals=0.
  * Single GRN with no invoice → one accrual line; aggregated Dr/Cr
    balanced; reversal_date = period_end + 1 day.
  * Multiple GRNs across two POs → multiple lines, single rolled-up
    Dr expense + Cr accrued.
  * GRN whose PO already has an invoice posted → excluded.
  * Invoice posted AFTER period_end → still excluded (only invoices
    in/before the period bypass the accrual).
  * Accrual_account fallback: org without accrued_expenses GL code
    falls back to accounts_payable + emits a note.
  * API: preview returns rendered text + structured proposal;
    cross-org isolation.
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

from clearledgr.api import accrual_journal_entry as accrual_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.accrual_journal_entry import (  # noqa: E402
    build_accrual_je_proposal,
)
from clearledgr.services.purchase_orders import (  # noqa: E402
    get_purchase_order_service,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(accrual_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_po_with_gr(
    org: str,
    *,
    vendor: str = "Vendor X",
    line_items=None,
    received_quantities=None,
    total: float = 1000.0,
    currency: str = "USD",
):
    """Build a PO + GR pair. Returns the (po, gr) objects."""
    svc = get_purchase_order_service(org)
    po = svc.create_po(
        vendor_id=vendor,
        vendor_name=vendor,
        requested_by="ops@" + org,
        line_items=line_items,
        currency=currency,
        tax_amount=0.0,
    )
    if not line_items:
        po.total_amount = total
    from clearledgr.services.purchase_orders import _po_to_store_dict
    svc._db.save_purchase_order(_po_to_store_dict(po))
    svc.approve_po(po.po_id, approved_by="ops@" + org)

    gr_lines = []
    received = received_quantities or [1]
    for idx, qty in enumerate(received):
        po_line = po.line_items[idx] if idx < len(po.line_items) else None
        gr_lines.append({
            "po_line_id": po_line.line_id if po_line else "",
            "item_number": po_line.item_number if po_line else f"ITEM-{idx}",
            "description": po_line.description if po_line else "Item",
            "quantity_received": qty,
        })
    gr = svc.create_goods_receipt(
        po_id=po.po_id,
        received_by="warehouse@" + org,
        line_items=gr_lines,
    )
    return po, gr


def _post_invoice_for_po(
    db, *, ap_item_id: str, org: str, po_number: str,
    posted_at: str = "2026-04-25T10:00:00+00:00",
):
    """Walk an AP item all the way to posted_to_erp so the accrual
    builder sees it as 'invoice already posted'."""
    item = db.create_ap_item({
        "id": ap_item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": 1000.0,
        "currency": "USD",
        "po_number": po_number,
        "state": "received",
    })
    for s in ("validated", "needs_approval", "approved", "ready_to_post", "posted_to_erp"):
        db.update_ap_item(item["id"], state=s)
    db.update_ap_item(item["id"], erp_posted_at=posted_at)


# ─── Empty / boundary cases ────────────────────────────────────────


def test_empty_period_yields_zero_lines(db):
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert proposal.lines == []
    assert proposal.je_lines == []
    assert proposal.debit_total == Decimal("0.00")
    assert proposal.balanced is True
    assert any("No received-not-billed" in n for n in proposal.notes)


def test_reversal_date_is_next_day(db):
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert proposal.accrual_date == "2026-04-30"
    assert proposal.reversal_date == "2026-05-01"


# ─── Happy path ────────────────────────────────────────────────────


def test_single_gr_without_invoice_creates_accrual(db):
    """A GR exists but no posted invoice → accrue."""
    po, gr = _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "SKU-A", "description": "Server",
             "quantity": 5, "unit_price": 200.0},
        ],
        received_quantities=[5],
        currency="USD",
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
        currency="USD",
    )
    assert len(proposal.lines) == 1
    line = proposal.lines[0]
    assert line.po_id == po.po_id
    assert line.gr_id == gr.gr_id
    assert line.accrual_amount == Decimal("1000.00")  # 5 × 200
    assert line.expense_account
    assert line.accrual_account
    assert proposal.debit_total == Decimal("1000.00")
    assert proposal.credit_total == Decimal("1000.00")
    assert proposal.balanced is True


def test_multiple_grs_aggregate_to_single_je_pair(db):
    """Two GRs across two POs → many lines, one Dr + one Cr."""
    _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "A", "description": "Server",
             "quantity": 5, "unit_price": 100.0},
        ],
        received_quantities=[5],
    )
    _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "B", "description": "Cable",
             "quantity": 100, "unit_price": 1.0},
        ],
        received_quantities=[100],
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert len(proposal.lines) == 2
    assert len(proposal.je_lines) == 2
    debit = next(je for je in proposal.je_lines if je.direction == "debit")
    credit = next(je for je in proposal.je_lines if je.direction == "credit")
    assert debit.amount == Decimal("600.00")  # 500 + 100
    assert credit.amount == Decimal("600.00")


def test_gr_excluded_when_invoice_already_posted(db):
    po, _gr = _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "A", "description": "Server",
             "quantity": 5, "unit_price": 200.0},
        ],
        received_quantities=[5],
    )
    _post_invoice_for_po(
        db, ap_item_id="AP-already-posted",
        org="orgA",
        po_number=po.po_number,
        posted_at="2026-04-15T00:00:00+00:00",
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert proposal.lines == []


def test_invoice_posted_after_period_end_still_accrues(db):
    """Period close is a snapshot — an invoice that posts after
    period_end shouldn't suppress the accrual for that period."""
    po, _gr = _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "A", "description": "Server",
             "quantity": 5, "unit_price": 200.0},
        ],
        received_quantities=[5],
    )
    _post_invoice_for_po(
        db, ap_item_id="AP-future-posted",
        org="orgA",
        po_number=po.po_number,
        posted_at="2026-05-15T00:00:00+00:00",
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    # The future invoice doesn't suppress this period's accrual.
    assert len(proposal.lines) == 1


def test_accrual_account_fallback_emits_note(db):
    """Org without an accrued_expenses GL code falls back to
    accounts_payable + emits a note."""
    _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "A", "description": "X",
             "quantity": 1, "unit_price": 50.0},
        ],
        received_quantities=[1],
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert any(
        "accrued_expenses" in n and "accounts_payable" in n
        for n in proposal.notes
    )


def test_tenant_isolation(db):
    """An accrual run for orgA must NOT see orgB's GRNs."""
    _make_po_with_gr(
        "orgB",
        line_items=[
            {"item_number": "B", "description": "B",
             "quantity": 5, "unit_price": 100.0},
        ],
        received_quantities=[5],
    )
    proposal = build_accrual_je_proposal(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    assert proposal.lines == []


# ─── API ────────────────────────────────────────────────────────────


def test_api_preview_returns_rendered_text(db, client_orgA):
    _make_po_with_gr(
        "orgA",
        line_items=[
            {"item_number": "API", "description": "Service",
             "quantity": 1, "unit_price": 250.0},
        ],
        received_quantities=[1],
    )
    resp = client_orgA.post(
        "/api/workspace/accrual-je/preview",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "erp_type": "xero",
            "currency": "USD",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accrual_count"] == 1
    assert data["debit_total"] == 250.0
    assert "rendered_text" in data
    assert "Month-end accrual" in data["rendered_text"]


def test_api_preview_empty_period(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/accrual-je/preview",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accrual_count"] == 0
    assert data["balanced"] is True
