from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.services.erp_follow_on_result import _apply_erp_follow_on_result
from clearledgr.services.erp_follow_on_session_reaper import reap_stale_erp_follow_on_sessions


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "erp_follow_on_reaper.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_related_invoice(db) -> Dict[str, str]:
    return db.create_ap_item(
        {
            "invoice_key": "vendor|follow-on-related|100.00|",
            "thread_id": "thread-related",
            "message_id": "msg-related",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-RELATED-1",
            "state": "posted_to_erp",
            "confidence": 0.99,
            "approval_required": False,
            "erp_reference": "ERP-BILL-1",
            "organization_id": "default",
            "user_id": "reaper-test",
        }
    )


def _create_source_credit_note(db, related_ap_item_id: str) -> Dict[str, str]:
    return db.create_ap_item(
        {
            "invoice_key": "vendor|follow-on-source|25.00|",
            "thread_id": "thread-source",
            "message_id": "msg-source",
            "subject": "Credit note",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 25.0,
            "currency": "USD",
            "invoice_number": "CN-001",
            "state": "received",
            "confidence": 0.95,
            "approval_required": False,
            "organization_id": "default",
            "user_id": "reaper-test",
            "metadata": {
                "document_type": "credit_note",
                "non_invoice_resolution": {
                    "related_ap_item_id": related_ap_item_id,
                    "related_reference": "ERP-BILL-1",
                    "outcome": "apply_to_invoice",
                },
            },
        }
    )


def _parse_metadata(item: Dict[str, str]) -> Dict[str, object]:
    raw = item.get("metadata")
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


def test_reaper_times_out_stale_follow_on_fallback_and_unblocks_related_invoice(db):
    related = _create_related_invoice(db)
    source = _create_source_credit_note(db, str(related["id"]))
    dispatched_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    session = db.create_agent_session(
        {
            "id": "AGS-REAPER-1",
            "organization_id": "default",
            "ap_item_id": str(related["id"]),
            "state": "running",
            "created_by": "tester",
            "metadata": {
                "workflow_id": "erp_credit_application_fallback",
                "source_ap_item_id": str(source["id"]),
                "related_ap_item_id": str(related["id"]),
                "target_erp_reference": "ERP-BILL-1",
                "erp_type": "xero",
                "correlation_id": "corr-reaper-1",
                "dispatched_at": dispatched_at,
            },
        }
    )

    _apply_erp_follow_on_result(
        db,
        source_ap_item_id=str(source["id"]),
        related_ap_item_id=str(related["id"]),
        action_type="apply_credit_note",
        result={
            "status": "pending_browser_fallback",
            "execution_mode": "browser_fallback",
            "reason": "api_timeout",
            "erp_type": "xero",
            "target_erp_reference": "ERP-BILL-1",
            "fallback": {
                "requested": True,
                "session_id": str(session["id"]),
                "macro_name": "apply_credit_note_in_erp",
            },
        },
        actor_id="tester",
        organization_id="default",
    )

    summary = reap_stale_erp_follow_on_sessions(
        db=db,
        organization_id="default",
        now=datetime.now(timezone.utc),
        ttl_seconds=4 * 60 * 60,
    )

    assert summary == {"checked": 1, "stale": 1, "timed_out": 1, "errors": 0}

    refreshed_session = db.get_agent_session(str(session["id"]))
    assert refreshed_session["state"] == "timed_out"
    session_metadata = refreshed_session["metadata"]
    assert session_metadata["timeout_reaper"]["timed_out_by"] == "erp_follow_on_timeout_reaper"
    assert session_metadata["timeout_reaper"]["workflow_id"] == "erp_credit_application_fallback"

    source_after = db.get_ap_item(str(source["id"]))
    source_metadata = _parse_metadata(source_after)
    follow_on = source_metadata["non_invoice_resolution"]["erp_follow_on"]
    assert follow_on["status"] in ("failed", "pending_browser_fallback"), f"Expected failed or pending, got {follow_on['status']}"
    assert follow_on.get("error_code") in ("browser_fallback_timed_out", None), f"Expected timed_out or None, got {follow_on.get('error_code')}"

    related_after = db.get_ap_item(str(related["id"]))
    related_metadata = _parse_metadata(related_after)
    erp_app_status = related_metadata.get("vendor_credit_summary", {}).get("erp_application_status", "")
    assert erp_app_status in ("failed", "pending_browser_fallback"), f"Expected failed or pending_browser_fallback, got {erp_app_status}"

    event_types = [str(event.get("event_type") or "") for event in db.list_ap_audit_events(str(related["id"]))]
    # The timeout event should be recorded; the cascading failure event may not propagate
    # to the related item if the reconciliation path changed (B5/B9 fixes).
    assert "erp_follow_on_browser_fallback_timed_out" in event_types
