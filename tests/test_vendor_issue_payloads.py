from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import ap_items as ap_items_module
from clearledgr.core import database as db_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "vendor-issues.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def test_vendor_payload_surfaces_open_issue_rollups(db):
    db.create_ap_item(
        {
            "id": "vendor-issues-1",
            "invoice_key": "inv-vendor-issues-1",
            "thread_id": "thread-vendor-issues-1",
            "message_id": "msg-vendor-issues-1",
            "subject": "PO missing for April invoice",
            "sender": "billing@acmevendor.com",
            "vendor_name": "Acme Vendor",
            "amount": 450.0,
            "currency": "USD",
            "invoice_number": "INV-ACME-1",
            "state": "needs_info",
            "organization_id": "default",
            "exception_code": "po_missing_reference",
            "metadata": {
                "needs_info_question": "Please send the PO number for INV-ACME-1.",
            },
        }
    )
    db.create_ap_item(
        {
            "id": "vendor-issues-2",
            "invoice_key": "inv-vendor-issues-2",
            "thread_id": "thread-vendor-issues-2",
            "message_id": "msg-vendor-issues-2",
            "subject": "Retry SAP post",
            "sender": "billing@acmevendor.com",
            "vendor_name": "Acme Vendor",
            "amount": 620.0,
            "currency": "USD",
            "invoice_number": "INV-ACME-2",
            "state": "failed_post",
            "organization_id": "default",
            "exception_code": "erp_post_failed",
        }
    )

    vendor_rows = ap_items_module._build_vendor_summary_rows(db, "default", search="Acme Vendor", limit=10)
    assert len(vendor_rows) == 1
    summary = vendor_rows[0]
    assert summary["vendor_name"] == "Acme Vendor"
    assert summary["issue_count"] == 2
    assert summary["issue_summary"]["needs_info"] == 1
    assert summary["issue_summary"]["failed_post"] == 1
    assert {row["exception_code"] for row in summary["top_exception_codes"]} == {
        "po_missing_reference",
        "erp_post_failed",
    }

    payload = ap_items_module._build_vendor_detail_payload(db, "default", "Acme Vendor")
    assert payload["issue_summary"]["total"] == 2
    assert payload["issue_summary"]["needs_info"] == 1
    assert payload["issue_summary"]["failed_post"] == 1
    assert [row["issue_kind"] for row in payload["open_issues"]] == ["failed_post", "needs_info"]
    assert payload["open_issues"][1]["issue_summary"] == "Please send the PO number for INV-ACME-1."
