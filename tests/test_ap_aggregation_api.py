from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ap-aggregation-api.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    return TestClient(app)


def _create_item(db, item_id: str, vendor: str, amount: float) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": f"Invoice for {vendor}",
            "sender": "billing@example.com",
            "vendor_name": vendor,
            "amount": amount,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": "needs_approval",
            "organization_id": "default",
        }
    )


def test_ap_aggregation_endpoints_return_multi_system_metrics(client, db):
    item_one = _create_item(db, "AGG-API-1", "Google", 125.0)
    item_two = _create_item(db, "AGG-API-2", "Google", 300.0)
    db.link_ap_item_source(
        {
            "ap_item_id": item_one["id"],
            "source_type": "spreadsheet",
            "source_ref": "sheet-1",
            "subject": "Sheet 1",
            "sender": "sheets",
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item_two["id"],
            "source_type": "card_statement",
            "source_ref": "card-txn-1",
            "subject": "Card statement 1",
            "sender": "amex",
        }
    )

    ap_items_response = client.get("/api/ap/items/metrics/aggregation?organization_id=default")
    assert ap_items_response.status_code == 200
    ap_items_metrics = ap_items_response.json()["metrics"]
    assert ap_items_metrics["totals"]["items"] >= 2
    assert ap_items_metrics["sources"]["total_links"] >= 2
    assert any(row["vendor_name"] == "Google" for row in ap_items_metrics["spend_by_vendor"])

    ops_response = client.get("/api/ops/ap-aggregation?organization_id=default")
    assert ops_response.status_code == 200
    ops_metrics = ops_response.json()["metrics"]
    assert ops_metrics["totals"]["items"] >= 2
    assert "spreadsheet" in ops_metrics["sources"]["connected_systems"] or "card_statement" in ops_metrics["sources"]["connected_systems"]
