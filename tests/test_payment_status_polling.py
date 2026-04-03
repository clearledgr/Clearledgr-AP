"""Tests for end-to-end payment tracking via ERP payment status polling.

Covers:
- ERP-specific payment status lookup functions (QuickBooks, Xero, NetSuite, SAP)
- get_bill_payment_status dispatcher (erp_router)
- _poll_payment_statuses background job
- Slack notifications for payment status changes
- Payment store partial status support
- Worklist enrichment with payment completion info
- AP skill check_payment_readiness enriched response
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_db(tmp_path: str):
    """Create a fresh ClearledgrDB instance at a temp path."""
    os.environ["CLEARLEDGR_DB_PATH"] = tmp_path
    from clearledgr.core.database import ClearledgrDB
    db = ClearledgrDB(tmp_path)
    db.initialize()
    return db


# ==================== ERP Payment Status Lookups ====================


class TestGetPaymentStatusQuickbooks:
    def test_fully_paid(self, monkeypatch):
        from clearledgr.integrations import erp_quickbooks as mod

        qb_response = {
            "QueryResponse": {
                "Bill": [{
                    "Id": "123",
                    "DocNumber": "INV-001",
                    "TotalAmt": 1000.0,
                    "Balance": 0.0,
                }]
            }
        }

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return qb_response

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))

        class Conn:
            access_token = "tok"
            realm_id = "123"

        result = _run(mod.get_payment_status_quickbooks(Conn(), "123"))
        assert result["paid"] is True
        assert result["payment_amount"] == 1000.0
        assert result["partial"] is False
        assert result["remaining_balance"] == 0.0

    def test_partial_payment(self, monkeypatch):
        from clearledgr.integrations import erp_quickbooks as mod

        qb_response = {
            "QueryResponse": {
                "Bill": [{
                    "Id": "123",
                    "TotalAmt": 1000.0,
                    "Balance": 400.0,
                }]
            }
        }

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return qb_response

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))

        class Conn:
            access_token = "tok"
            realm_id = "123"

        result = _run(mod.get_payment_status_quickbooks(Conn(), "123"))
        assert result["paid"] is False
        assert result["partial"] is True
        assert result["payment_amount"] == 600.0
        assert result["remaining_balance"] == 400.0

    def test_not_found(self, monkeypatch):
        from clearledgr.integrations import erp_quickbooks as mod

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"QueryResponse": {"Bill": []}}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))

        class Conn:
            access_token = "tok"
            realm_id = "123"

        result = _run(mod.get_payment_status_quickbooks(Conn(), "999"))
        assert result["paid"] is False
        assert result.get("reason") == "not_found"

    def test_no_connection(self):
        from clearledgr.integrations.erp_quickbooks import get_payment_status_quickbooks

        class Conn:
            access_token = None
            realm_id = None

        result = _run(get_payment_status_quickbooks(Conn(), "123"))
        assert result["paid"] is False
        assert "error" in result


class TestGetPaymentStatusXero:
    def test_fully_paid(self, monkeypatch):
        from clearledgr.integrations import erp_xero as mod

        xero_response = {
            "Invoices": [{
                "InvoiceID": "abc-123",
                "Status": "PAID",
                "Total": 500.0,
                "AmountDue": 0.0,
                "AmountPaid": 500.0,
                "Payments": [{"PaymentID": "pay-1", "Date": "2026-03-15"}],
            }]
        }

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return xero_response

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))

        class Conn:
            access_token = "tok"
            tenant_id = "tid"

        result = _run(mod.get_payment_status_xero(Conn(), "abc-123"))
        assert result["paid"] is True
        assert result["payment_amount"] == 500.0
        assert result["payment_reference"] == "pay-1"


class TestGetPaymentStatusNetsuite:
    def test_fully_paid(self, monkeypatch):
        from clearledgr.integrations import erp_netsuite as mod

        ns_response = {
            "items": [{
                "id": "456",
                "tranid": "VB-100",
                "status": "Paid In Full",
                "amount": 750.0,
                "amountremaining": 0.0,
                "amountpaid": 750.0,
            }]
        }

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return ns_response

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))
        monkeypatch.setattr(mod, "_oauth_header", lambda *a, **kw: "OAuth fake")

        class Conn:
            account_id = "12345"

        result = _run(mod.get_payment_status_netsuite(Conn(), "456"))
        assert result["paid"] is True
        assert result["payment_amount"] == 750.0


class TestGetPaymentStatusSAP:
    def test_fully_paid(self, monkeypatch):
        from clearledgr.integrations import erp_sap as mod

        sap_response = {
            "DocEntry": 100,
            "DocTotal": 2000.0,
            "PaidToDate": 2000.0,
            "UpdateDate": "2026-03-20",
        }

        session_result = {
            "status": "success",
            "session_cookie": "abc",
            "csrf_token": None,
            "headers": {"Cookie": "B1SESSION=abc"},
        }

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return sap_response

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, *a, **kw): return FakeResponse()

        monkeypatch.setattr(mod, "httpx", type("M", (), {"AsyncClient": lambda **kw: FakeClient(), "HTTPStatusError": Exception}))
        monkeypatch.setattr(mod, "_open_sap_service_layer_session", AsyncMock(return_value=session_result))

        class Conn:
            access_token = "tok"
            base_url = "https://sap.example.com/b1s/v1"

        result = _run(mod.get_payment_status_sap(Conn(), "100"))
        assert result["paid"] is True
        assert result["payment_amount"] == 2000.0
        assert result["payment_date"] == "2026-03-20"


# ==================== Dispatcher ====================


class TestGetBillPaymentStatusDispatcher:
    def test_no_connection(self, monkeypatch):
        from clearledgr.integrations import erp_router

        monkeypatch.setattr(erp_router, "get_erp_connection", lambda *a, **kw: None)
        result = _run(erp_router.get_bill_payment_status("org-1", "ref-1"))
        assert result["paid"] is False
        assert result["reason"] == "no_erp_connection"

    def test_dispatches_to_quickbooks(self, monkeypatch):
        from clearledgr.integrations import erp_router

        class FakeConn:
            type = "quickbooks"

        monkeypatch.setattr(erp_router, "get_erp_connection", lambda *a, **kw: FakeConn())

        async def fake_lookup(conn, ref):
            return {"paid": True, "payment_amount": 100.0, "partial": False, "remaining_balance": 0.0, "payment_date": "", "payment_method": "", "payment_reference": "REF-1"}

        # Patch the lookup dict to point to our fake
        monkeypatch.setitem(erp_router._PAYMENT_STATUS_LOOKUPS, "quickbooks", fake_lookup)
        result = _run(erp_router.get_bill_payment_status("org-1", "ref-1"))
        assert result["paid"] is True
        assert result["payment_amount"] == 100.0

    def test_token_refresh_on_reauth(self, monkeypatch):
        from clearledgr.integrations import erp_router

        class FakeConn:
            type = "quickbooks"

        call_count = {"n": 0}

        async def fake_lookup(conn, ref):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"paid": False, "needs_reauth": True}
            return {"paid": True, "payment_amount": 500.0, "partial": False, "remaining_balance": 0.0, "payment_date": "", "payment_method": "", "payment_reference": ""}

        monkeypatch.setattr(erp_router, "get_erp_connection", lambda *a, **kw: FakeConn())
        monkeypatch.setitem(erp_router._PAYMENT_STATUS_LOOKUPS, "quickbooks", fake_lookup)
        monkeypatch.setattr(erp_router, "refresh_quickbooks_token", AsyncMock(return_value="new-tok"))
        monkeypatch.setattr(erp_router, "set_erp_connection", lambda *a, **kw: None)

        result = _run(erp_router.get_bill_payment_status("org-1", "ref-1"))
        assert result["paid"] is True
        assert call_count["n"] == 2


# ==================== Background Poll ====================


class TestPollPaymentStatuses:
    def test_poll_updates_completed_payment(self, monkeypatch):
        from clearledgr.services import agent_background as mod

        payments = [
            {
                "id": "PAY-001",
                "ap_item_id": "AP-001",
                "erp_reference": "ERP-REF-1",
                "vendor_name": "Acme Corp",
                "amount": 1000.0,
                "currency": "USD",
                "status": "ready_for_payment",
            },
        ]

        updated_payments = {}

        class FakeDB:
            def list_payments_by_status(self, org_id, status):
                return payments if status == "ready_for_payment" else []

            def update_payment(self, payment_id, **kwargs):
                updated_payments[payment_id] = kwargs
                return None

            def update_ap_item_metadata_merge(self, ap_item_id, patch):
                updated_payments[f"meta:{ap_item_id}"] = patch
                return True

        monkeypatch.setattr(
            "clearledgr.core.database.get_db",
            lambda: FakeDB(),
        )

        async def fake_status(*a, **kw):
            return {
                "paid": True,
                "payment_amount": 1000.0,
                "payment_date": "2026-03-20",
                "payment_method": "ACH",
                "payment_reference": "PMT-999",
                "partial": False,
                "remaining_balance": 0.0,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_bill_payment_status",
            fake_status,
        )

        # Suppress Slack notification
        monkeypatch.setattr(
            "clearledgr.services.slack_notifications.send_payment_completed_notification",
            AsyncMock(return_value=True),
        )

        result = _run(mod._poll_payment_statuses("org-1"))
        assert result["checked"] == 1
        assert result["updated"] == 1
        assert updated_payments["PAY-001"]["status"] == "completed"
        assert updated_payments["PAY-001"]["payment_reference"] == "PMT-999"
        assert updated_payments["meta:AP-001"]["payment_status"] == "completed"

    def test_poll_updates_partial_payment(self, monkeypatch):
        from clearledgr.services import agent_background as mod

        payments = [
            {
                "id": "PAY-002",
                "ap_item_id": "AP-002",
                "erp_reference": "ERP-REF-2",
                "vendor_name": "Widget Inc",
                "amount": 2000.0,
                "currency": "USD",
                "status": "scheduled",
            },
        ]

        updated_payments = {}

        class FakeDB:
            def list_payments_by_status(self, org_id, status):
                return payments if status == "scheduled" else []

            def update_payment(self, payment_id, **kwargs):
                updated_payments[payment_id] = kwargs
                return None

            def update_ap_item_metadata_merge(self, ap_item_id, patch):
                updated_payments[f"meta:{ap_item_id}"] = patch
                return True

        monkeypatch.setattr(
            "clearledgr.core.database.get_db",
            lambda: FakeDB(),
        )

        async def fake_status(*a, **kw):
            return {
                "paid": False,
                "payment_amount": 500.0,
                "partial": True,
                "remaining_balance": 1500.0,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_bill_payment_status",
            fake_status,
        )

        monkeypatch.setattr(
            "clearledgr.services.slack_notifications.send_payment_partial_notification",
            AsyncMock(return_value=True),
        )

        result = _run(mod._poll_payment_statuses("org-1"))
        assert result["checked"] == 1
        assert result["updated"] == 1
        assert updated_payments["PAY-002"]["status"] == "partial"
        assert updated_payments["PAY-002"]["paid_amount"] == 500.0

    def test_poll_skips_payments_without_erp_reference(self, monkeypatch):
        from clearledgr.services import agent_background as mod

        payments = [
            {
                "id": "PAY-003",
                "ap_item_id": "AP-003",
                "erp_reference": None,
                "vendor_name": "NoRef Inc",
                "amount": 100.0,
                "status": "ready_for_payment",
            },
        ]

        class FakeDB:
            def list_payments_by_status(self, org_id, status):
                return payments if status == "ready_for_payment" else []

        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: FakeDB())

        result = _run(mod._poll_payment_statuses("org-1"))
        assert result["checked"] == 0
        assert result["updated"] == 0

    def test_poll_caps_at_50(self, monkeypatch):
        from clearledgr.services import agent_background as mod

        payments = [
            {
                "id": f"PAY-{i}",
                "ap_item_id": f"AP-{i}",
                "erp_reference": f"REF-{i}",
                "vendor_name": f"Vendor-{i}",
                "amount": 100.0,
                "currency": "USD",
                "status": "ready_for_payment",
            }
            for i in range(60)
        ]

        checked_refs = []

        class FakeDB:
            def list_payments_by_status(self, org_id, status):
                return payments if status == "ready_for_payment" else []

            def update_payment(self, payment_id, **kwargs):
                return None

            def update_ap_item_metadata_merge(self, ap_item_id, patch):
                return True

        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: FakeDB())

        async def fake_status(organization_id, erp_reference, **kw):
            checked_refs.append(erp_reference)
            return {"paid": False, "reason": "unpaid"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_bill_payment_status",
            fake_status,
        )

        result = _run(mod._poll_payment_statuses("org-1"))
        assert result["checked"] == 50
        assert len(checked_refs) == 50

    def test_poll_error_non_blocking(self, monkeypatch):
        """One payment failing should not stop others from being checked."""
        from clearledgr.services import agent_background as mod

        payments = [
            {"id": "PAY-A", "ap_item_id": "AP-A", "erp_reference": "REF-A", "vendor_name": "A", "amount": 100.0, "currency": "USD", "status": "ready_for_payment"},
            {"id": "PAY-B", "ap_item_id": "AP-B", "erp_reference": "REF-B", "vendor_name": "B", "amount": 200.0, "currency": "USD", "status": "ready_for_payment"},
        ]

        checked_refs = []
        updated_ids = []

        class FakeDB:
            def list_payments_by_status(self, org_id, status):
                return payments if status == "ready_for_payment" else []

            def update_payment(self, payment_id, **kwargs):
                updated_ids.append(payment_id)
                return None

            def update_ap_item_metadata_merge(self, ap_item_id, patch):
                return True

        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: FakeDB())

        async def fake_status(organization_id, erp_reference, **kw):
            checked_refs.append(erp_reference)
            if erp_reference == "REF-A":
                raise RuntimeError("ERP timeout")
            return {"paid": True, "payment_amount": 200.0, "payment_date": "", "payment_method": "", "payment_reference": "PMT-B", "partial": False, "remaining_balance": 0.0}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_bill_payment_status",
            fake_status,
        )
        monkeypatch.setattr(
            "clearledgr.services.slack_notifications.send_payment_completed_notification",
            AsyncMock(return_value=True),
        )

        result = _run(mod._poll_payment_statuses("org-1"))
        assert result["checked"] == 2
        assert result["updated"] == 1
        assert "PAY-B" in updated_ids


# ==================== Payment Store ====================


class TestPaymentStorePartialStatus:
    def test_update_payment_with_partial_and_paid_amount(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            db = _make_db(tmp_path)
            payment = db.create_payment({
                "ap_item_id": "AP-1",
                "organization_id": "org-1",
                "vendor_name": "TestVendor",
                "amount": 1000.0,
                "erp_reference": "ERP-1",
                "status": "ready_for_payment",
            })

            updated = db.update_payment(
                payment["id"],
                status="partial",
                paid_amount=400.0,
                notes="Partial payment detected",
            )

            assert updated is not None
            assert updated["status"] == "partial"

            # Verify paid_amount is stored (via re-read)
            fetched = db.get_payment(payment["id"])
            assert fetched is not None
            assert fetched["status"] == "partial"
        finally:
            os.unlink(tmp_path)


# ==================== Slack Notifications ====================


class TestSlackPaymentNotifications:
    def test_send_payment_completed_notification(self, monkeypatch):
        from clearledgr.services import slack_notifications as mod

        sent_args = {}

        async def fake_send(**kwargs):
            sent_args.update(kwargs)
            return True

        monkeypatch.setattr(mod, "send_with_retry", fake_send)

        result = _run(mod.send_payment_completed_notification(
            organization_id="org-1",
            vendor_name="Acme Corp",
            amount=1500.0,
            currency="USD",
            payment_reference="PMT-123",
            payment_method="ACH",
        ))

        assert result is True
        assert "Acme Corp" in sent_args["text"]
        assert "PMT-123" in sent_args["text"]

    def test_send_payment_partial_notification(self, monkeypatch):
        from clearledgr.services import slack_notifications as mod

        sent_args = {}

        async def fake_send(**kwargs):
            sent_args.update(kwargs)
            return True

        monkeypatch.setattr(mod, "send_with_retry", fake_send)

        result = _run(mod.send_payment_partial_notification(
            organization_id="org-1",
            vendor_name="Widget Inc",
            amount=2000.0,
            paid_amount=800.0,
            remaining=1200.0,
            currency="EUR",
        ))

        assert result is True
        assert "Widget Inc" in sent_args["text"]
        assert "800" in sent_args["text"]
        assert "1,200" in sent_args["text"]


# ==================== Worklist Enrichment ====================


class TestWorlistPaymentEnrichment:
    def test_enrichment_includes_payment_fields_for_posted_items(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            db = _make_db(tmp_path)

            metadata = json.dumps({
                "payment_status": "completed",
                "payment_completed_at": "2026-03-20T10:00:00Z",
                "payment_method": "ACH",
                "payment_reference": "PMT-456",
                "payment_paid_amount": 1000.0,
                "payment_remaining": 0.0,
            })

            item = {
                "id": "AP-1",
                "organization_id": "org-1",
                "vendor_name": "TestVendor",
                "amount": 1000.0,
                "state": "posted_to_erp",
                "metadata": metadata,
                "created_at": "2026-03-15T10:00:00Z",
                "updated_at": "2026-03-20T10:00:00Z",
            }

            from clearledgr.services.ap_item_service import build_worklist_item
            result = build_worklist_item(db, item)

            assert result["payment_status"] == "completed"
            assert result["payment_completed_at"] == "2026-03-20T10:00:00Z"
            assert result["payment_method"] == "ACH"
            assert result["payment_reference"] == "PMT-456"
            assert result["payment_paid_amount"] == 1000.0
            assert result["payment_remaining"] == 0.0
        finally:
            os.unlink(tmp_path)

    def test_enrichment_nulls_payment_fields_for_non_posted_items(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            db = _make_db(tmp_path)

            item = {
                "id": "AP-2",
                "organization_id": "org-1",
                "vendor_name": "TestVendor",
                "amount": 500.0,
                "state": "needs_approval",
                "metadata": "{}",
                "created_at": "2026-03-15T10:00:00Z",
                "updated_at": "2026-03-15T10:00:00Z",
            }

            from clearledgr.services.ap_item_service import build_worklist_item
            result = build_worklist_item(db, item)

            assert result["payment_status"] is None
            assert result["payment_completed_at"] is None
            assert result["payment_method"] is None
            assert result["payment_reference"] is None
            assert result["payment_paid_amount"] is None
            assert result["payment_remaining"] is None
        finally:
            os.unlink(tmp_path)


# ==================== AP Skill Enrichment ====================


class TestAPSkillCheckPaymentReadiness:
    def test_returns_completion_details(self, monkeypatch):
        from clearledgr.core.skills import ap_skill as mod

        ap_item = {
            "id": "AP-1",
            "state": "posted_to_erp",
            "metadata": json.dumps({
                "payment_completed_at": "2026-03-20T10:00:00Z",
                "payment_remaining": None,
            }),
        }

        payment = {
            "id": "PAY-1",
            "status": "completed",
            "vendor_name": "Acme Corp",
            "amount": 1000.0,
            "currency": "USD",
            "due_date": "2026-03-15",
            "erp_reference": "ERP-1",
            "payment_method": "ACH",
            "scheduled_date": None,
            "completed_date": "2026-03-20",
            "payment_reference": "PMT-999",
            "paid_amount": 1000.0,
        }

        class FakeDB:
            def get_ap_item(self, aid): return ap_item
            def get_invoice_status(self, gid): return None
            def get_payment_by_ap_item(self, aid): return payment

        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: FakeDB())

        result = _run(mod._handle_check_payment_readiness(
            {"ap_item_id": "AP-1"},
            organization_id="org-1",
        ))

        assert result["ok"] is True
        assert result["payment_status"] == "completed"
        assert result["completed_date"] == "2026-03-20"
        assert result["payment_reference"] == "PMT-999"
        assert result["payment_completed_at"] == "2026-03-20T10:00:00Z"

    def test_returns_partial_details(self, monkeypatch):
        from clearledgr.core.skills import ap_skill as mod

        ap_item = {
            "id": "AP-2",
            "state": "posted_to_erp",
            "metadata": json.dumps({
                "payment_remaining": 500.0,
            }),
        }

        payment = {
            "id": "PAY-2",
            "status": "partial",
            "vendor_name": "Widget Inc",
            "amount": 1000.0,
            "currency": "USD",
            "due_date": "2026-03-15",
            "erp_reference": "ERP-2",
            "payment_method": None,
            "scheduled_date": None,
            "completed_date": None,
            "payment_reference": None,
            "paid_amount": 500.0,
        }

        class FakeDB:
            def get_ap_item(self, aid): return ap_item
            def get_invoice_status(self, gid): return None
            def get_payment_by_ap_item(self, aid): return payment

        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: FakeDB())

        result = _run(mod._handle_check_payment_readiness(
            {"ap_item_id": "AP-2"},
            organization_id="org-1",
        ))

        assert result["ok"] is True
        assert result["payment_status"] == "partial"
        assert result["paid_amount"] == 500.0
        assert result["remaining_balance"] == 500.0
