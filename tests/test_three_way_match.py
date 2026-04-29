"""Tests for Wave 5 / G1 — 3-way match runner + API.

Covers:
  * Happy match: invoice + PO + GR all line up → match_status='matched',
    match_status persisted on the AP item, audit emit with the
    canonical idempotency key.
  * No PO: invoice has no po_number and no fuzzy match → 'no_po'.
  * Price variance > tolerance: matched PO with invoice 10% over →
    'exception' + price_mismatch in exceptions list.
  * Currency mismatch: EUR invoice vs USD PO → 'exception' even if
    amounts equal.
  * Per-line breakdown: invoice line maps to a PO line, GR receipt
    accounted for; price + quantity variance computed per row.
  * Idempotent re-run: same audit event idempotency key → no
    duplicate event.
  * API: POST + GET both run the match; cross-org 404.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import three_way_match as tw_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.purchase_orders import (  # noqa: E402
    get_purchase_order_service,
)
from clearledgr.services.three_way_match_runner import (  # noqa: E402
    run_three_way_match,
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
    app.include_router(tw_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_ap_item(
    db, *,
    item_id: str,
    org: str = "orgA",
    amount: float = 1000.0,
    currency: str = "USD",
    po_number: str = "",
    vendor: str = "Vendor X",
    line_items=None,
) -> dict:
    metadata = {}
    if line_items is not None:
        metadata["line_items"] = line_items
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": amount,
        "currency": currency,
        "invoice_number": f"INV-{item_id}",
        "po_number": po_number,
        "state": "received",
        "metadata": metadata,
    })
    return db.get_ap_item(item["id"])


def _make_po(
    org: str,
    *,
    vendor: str = "Vendor X",
    total: float = 1000.0,
    currency: str = "USD",
    line_items=None,
) -> str:
    """Create + approve a PO; return its po_number."""
    svc = get_purchase_order_service(org)
    po = svc.create_po(
        vendor_id=vendor,
        vendor_name=vendor,
        requested_by="ops@" + org,
        line_items=line_items,
        currency=currency,
        tax_amount=0.0,
        subtotal=total if not line_items else 0.0,
    )
    if line_items:
        po.calculate_totals()
        from clearledgr.services.purchase_orders import _po_to_store_dict
        svc._db.save_purchase_order(_po_to_store_dict(po))
    if not line_items:
        po.total_amount = total
        from clearledgr.services.purchase_orders import _po_to_store_dict
        svc._db.save_purchase_order(_po_to_store_dict(po))
    svc.approve_po(po.po_id, approved_by="ops@" + org)
    return po.po_number


def _create_gr_for_po(org: str, po_number: str, line_quantities: list) -> str:
    svc = get_purchase_order_service(org)
    po = svc.get_po_by_number(po_number)
    line_items = []
    for idx, qty in enumerate(line_quantities):
        po_line = po.line_items[idx] if idx < len(po.line_items) else None
        line_items.append({
            "po_line_id": po_line.line_id if po_line else "",
            "item_number": po_line.item_number if po_line else f"ITEM-{idx}",
            "description": po_line.description if po_line else "",
            "quantity_received": qty,
        })
    gr = svc.create_goods_receipt(
        po_id=po.po_id,
        received_by="warehouse@" + org,
        line_items=line_items,
    )
    return gr.gr_id


# ─── Happy match ────────────────────────────────────────────────────


def test_happy_match_persists_status(db):
    po_lines = [
        {"item_number": "SKU-1", "description": "Server", "quantity": 5, "unit_price": 200.0},
    ]
    po_num = _make_po(
        "orgA", line_items=po_lines, total=1000.0,
    )
    _create_gr_for_po("orgA", po_num, [5])
    item = _make_ap_item(
        db, item_id="AP-tw-happy", po_number=po_num, amount=1000.0,
        currency="USD",
        line_items=[{
            "item_code": "SKU-1", "description": "Server",
            "quantity": 5, "unit_price": 200.0, "amount": 1000.0,
        }],
    )

    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
        actor="ops@orgA",
    )
    assert summary is not None
    assert summary.match_status == "matched"
    assert summary.po_number == po_num

    fresh = db.get_ap_item(item["id"])
    assert fresh["match_status"] == "matched"


def test_no_po_path(db):
    item = _make_ap_item(
        db, item_id="AP-tw-no-po", po_number="", amount=1234.56,
        vendor="No-Such-Vendor",
    )
    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert summary.match_status == "no_po"
    assert summary.po_id is None


def test_price_variance_flags_exception(db):
    po_lines = [
        {"item_number": "SKU-2", "description": "Widget",
         "quantity": 10, "unit_price": 100.0},
    ]
    po_num = _make_po(
        "orgA", line_items=po_lines, total=1000.0,
    )
    item = _make_ap_item(
        db, item_id="AP-tw-pv", po_number=po_num,
        amount=1500.0,  # 50% over PO
    )
    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert summary.match_status == "exception"
    assert any(
        "price" in (e.get("type") or "").lower()
        or "price" in (e.get("message") or "").lower()
        for e in summary.exceptions
    )


def test_currency_mismatch_flags_exception(db):
    po_lines = [
        {"item_number": "SKU-3", "description": "Server",
         "quantity": 1, "unit_price": 1000.0},
    ]
    po_num = _make_po(
        "orgA", line_items=po_lines, total=1000.0, currency="USD",
    )
    item = _make_ap_item(
        db, item_id="AP-tw-ccy", po_number=po_num,
        amount=1000.0, currency="EUR",
    )
    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert summary.match_status == "exception"
    assert any(
        "currency" in (e.get("message") or "").lower()
        for e in summary.exceptions
    )


# ─── Per-line breakdown ────────────────────────────────────────────


def test_line_breakdown_aligns_invoice_to_po(db):
    po_lines = [
        {"item_number": "SKU-A", "description": "Server",
         "quantity": 5, "unit_price": 200.0},
        {"item_number": "SKU-B", "description": "Cable",
         "quantity": 100, "unit_price": 1.0},
    ]
    po_num = _make_po(
        "orgA", line_items=po_lines, total=1100.0,
    )
    _create_gr_for_po("orgA", po_num, [5, 100])
    item = _make_ap_item(
        db, item_id="AP-tw-lines",
        po_number=po_num,
        amount=1100.0,
        line_items=[
            {"item_code": "SKU-A", "description": "Server",
             "quantity": 5, "unit_price": 200.0, "amount": 1000.0},
            {"item_code": "SKU-B", "description": "Cable",
             "quantity": 100, "unit_price": 1.0, "amount": 100.0},
        ],
    )
    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert len(summary.line_breakdown) == 2
    flags = [ln["match_flag"] for ln in summary.line_breakdown]
    assert all(f == "matched" for f in flags), flags
    # PO/GR quantities populated.
    server_line = next(
        ln for ln in summary.line_breakdown if ln["item_code"] == "SKU-A"
    )
    assert server_line["po_quantity"] == 5
    assert server_line["po_unit_price"] == 200.0
    assert server_line["gr_quantity_received"] == 5


def test_line_breakdown_flags_price_variance_per_row(db):
    po_lines = [
        {"item_number": "SKU-X", "description": "Widget",
         "quantity": 1, "unit_price": 100.0},
    ]
    po_num = _make_po("orgA", line_items=po_lines, total=100.0)
    item = _make_ap_item(
        db, item_id="AP-tw-line-pv",
        po_number=po_num,
        amount=200.0,
        line_items=[{
            "item_code": "SKU-X", "description": "Widget",
            "quantity": 1, "unit_price": 200.0, "amount": 200.0,
        }],
    )
    summary = run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert summary.line_breakdown[0]["match_flag"] == "price_variance"
    assert summary.line_breakdown[0]["price_variance"] == 100.0


# ─── Idempotency ────────────────────────────────────────────────────


def test_idempotent_audit_emit(db):
    po_num = _make_po(
        "orgA",
        line_items=[
            {"item_number": "SKU-I", "description": "X",
             "quantity": 1, "unit_price": 50.0},
        ],
        total=50.0,
    )
    item = _make_ap_item(
        db, item_id="AP-tw-idem", po_number=po_num, amount=50.0,
    )
    run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    run_three_way_match(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    # Two runs, but only one audit event with the matched-status key.
    matching = [
        e for e in db.list_box_audit_events("ap_item", item["id"])
        if e.get("event_type") == "three_way_match_evaluated"
    ]
    assert len(matching) == 1


# ─── API ────────────────────────────────────────────────────────────


def test_api_post_runs_match(db, client_orgA):
    po_num = _make_po(
        "orgA",
        line_items=[{"item_number": "K", "description": "K",
                     "quantity": 1, "unit_price": 100.0}],
        total=100.0,
    )
    item = _make_ap_item(
        db, item_id="AP-tw-api-1", po_number=po_num, amount=100.0,
    )
    resp = client_orgA.post(
        f"/api/workspace/ap-items/{item['id']}/three-way-match",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["po_number"] == po_num
    assert data["match_status"] in ("matched", "partial_match", "exception")


def test_api_get_alias_runs_match(db, client_orgA):
    item = _make_ap_item(
        db, item_id="AP-tw-api-get", po_number="", amount=100.0,
        vendor="No-PO-Vendor",
    )
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{item['id']}/three-way-match",
    )
    assert resp.status_code == 200
    assert resp.json()["match_status"] == "no_po"


def test_api_unknown_ap_item_404(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/ap-items/AP-does-not-exist/three-way-match",
    )
    assert resp.status_code == 404


def test_api_cross_org_404(db, client_orgA):
    other = _make_ap_item(
        db, item_id="AP-tw-cross", org="orgB", amount=100.0,
    )
    resp = client_orgA.post(
        f"/api/workspace/ap-items/{other['id']}/three-way-match",
    )
    assert resp.status_code == 404
