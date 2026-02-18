from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.ap_items import _build_context_payload
from clearledgr.core.database import ClearledgrDB
from clearledgr.services.accruals import get_accruals_service
from clearledgr.services.purchase_orders import get_purchase_order_service


def _make_db(tmp_path: Path) -> ClearledgrDB:
    db_path = tmp_path / "ap-multi-context.db"
    db = ClearledgrDB(str(db_path))
    db.initialize()
    return db


def _create_item(db: ClearledgrDB, *, item_id: str, vendor: str, metadata: dict) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": f"Invoice for {vendor}",
            "sender": f"ap@{vendor.lower().replace(' ', '')}.com",
            "vendor_name": vendor,
            "amount": 125.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": "needs_info",
            "organization_id": "default",
            "metadata": metadata,
        }
    )


def _reset_procurement() -> None:
    service = get_purchase_order_service("default")
    service._purchase_orders.clear()
    service._goods_receipts.clear()
    service._matches.clear()
    service._po_by_number.clear()
    service._po_by_vendor.clear()


def _reset_payroll() -> None:
    service = get_accruals_service("default")
    service._accruals.clear()
    service._schedules.clear()


def test_context_links_bank_and_spreadsheet_sources(tmp_path: Path):
    db = _make_db(tmp_path)
    item = _create_item(
        db,
        item_id="AP-BANK-SHEET-1",
        vendor="Google",
        metadata={
            "bank_match": {
                "provider": "truelayer",
                "transaction_id": "txn-123",
                "amount": 125.0,
                "currency": "USD",
                "date": "2026-02-18",
            },
            "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abcDEF234567890example/edit#gid=0",
        },
    )

    context = _build_context_payload(db, item)
    sources = db.list_ap_item_sources(item["id"])
    source_types = {str(row.get("source_type")) for row in sources}

    assert context["bank"]["count"] == 1
    assert context["spreadsheets"]["count"] == 1
    assert "bank" in source_types
    assert "spreadsheet" in source_types
    assert context["web"]["connector_coverage"]["bank"] is True
    assert context["web"]["connector_coverage"]["spreadsheets"] is True


def test_context_links_card_and_dms_sources(tmp_path: Path):
    db = _make_db(tmp_path)
    item = _create_item(
        db,
        item_id="AP-CARD-DMS-1",
        vendor="Google",
        metadata={
            "credit_card_transactions": [
                {
                    "provider": "amex",
                    "transaction_id": "card-txn-22",
                    "amount": 125.0,
                    "currency": "USD",
                    "transaction_date": "2026-02-18",
                    "description": "Workspace Subscription",
                }
            ],
            "dms_documents": [
                {
                    "url": "https://dms.example.com/docs/invoice-443",
                    "document_id": "invoice-443",
                }
            ],
        },
    )

    context = _build_context_payload(db, item)
    sources = db.list_ap_item_sources(item["id"])
    source_types = {str(row.get("source_type")) for row in sources}

    assert context["card_statements"]["count"] == 1
    assert context["dms_documents"]["count"] == 1
    assert context["web"]["connector_coverage"]["card_statements"] is True
    assert context["web"]["connector_coverage"]["dms"] is True
    assert "card_statement" in source_types
    assert "dms" in source_types


def test_context_links_procurement_source_with_po_match(tmp_path: Path):
    _reset_procurement()
    db = _make_db(tmp_path)

    po_service = get_purchase_order_service("default")
    po = po_service.create_po(
        vendor_id="vendor-google",
        vendor_name="Google",
        requested_by="ops",
        po_number="PO-CTX-001",
        line_items=[
            {
                "item_number": "SVC-1",
                "description": "Workspace subscription",
                "quantity": 1,
                "unit_price": 125.0,
            }
        ],
    )
    po_service.approve_po(po.po_id, approved_by="manager")

    item = _create_item(
        db,
        item_id="AP-PROC-1",
        vendor="Google",
        metadata={"po_number": "PO-CTX-001"},
    )

    context = _build_context_payload(db, item)
    sources = db.list_ap_item_sources(item["id"])
    source_types = {str(row.get("source_type")) for row in sources}

    assert context["procurement"]["po"]["po_number"] == "PO-CTX-001"
    assert context["procurement"]["match"]["status"] in {"matched", "partial_match", "exception"}
    assert "procurement" in source_types
    assert context["web"]["connector_coverage"]["procurement"] is True


def test_context_links_payroll_source(tmp_path: Path):
    _reset_payroll()
    db = _make_db(tmp_path)

    payroll = get_accruals_service("default")
    payroll.create_payroll_accrual(
        payroll_period="2026-02",
        amount=4200.0,
        vendor_name="Google",
    )

    item = _create_item(
        db,
        item_id="AP-PAYROLL-1",
        vendor="Google",
        metadata={},
    )

    context = _build_context_payload(db, item)
    sources = db.list_ap_item_sources(item["id"])
    source_types = {str(row.get("source_type")) for row in sources}

    assert context["payroll"]["count"] >= 1
    assert context["approvals"]["payroll"]["count"] >= 1
    assert "payroll" in source_types
    assert context["web"]["connector_coverage"]["payroll"] is True


def test_context_budget_widget_exposes_decision_flags(tmp_path: Path):
    db = _make_db(tmp_path)
    item = _create_item(
        db,
        item_id="AP-BUDGET-1",
        vendor="Google",
        metadata={
            "budget_impact": [
                {
                    "budget_name": "Software",
                    "after_approval_status": "exceeded",
                    "after_approval_percent": 108.0,
                    "invoice_amount": 900.0,
                    "remaining": -500.0,
                }
            ]
        },
    )

    context = _build_context_payload(db, item)

    assert context["budget"]["status"] == "exceeded"
    assert context["budget"]["requires_decision"] is True
    assert len(context["budget"]["checks"]) == 1
    assert context["approvals"]["budget"]["status"] == "exceeded"


def test_ap_aggregation_metrics_exposes_vendor_spend_and_source_density(tmp_path: Path):
    db = _make_db(tmp_path)
    item_one = _create_item(
        db,
        item_id="AP-AGG-1",
        vendor="Google",
        metadata={"spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abcDEF234567890example/edit"},
    )
    item_two = _create_item(
        db,
        item_id="AP-AGG-2",
        vendor="Google",
        metadata={"credit_card_transactions": [{"provider": "amex", "transaction_id": "txn-2", "amount": 125}]},
    )

    _build_context_payload(db, item_one)
    _build_context_payload(db, item_two)

    metrics = db.get_ap_aggregation_metrics("default")
    assert metrics["totals"]["items"] >= 2
    assert metrics["sources"]["total_links"] >= 2
    assert metrics["sources"]["avg_links_per_item"] > 0
    assert any(row["vendor_name"] == "Google" for row in metrics["spend_by_vendor"])
