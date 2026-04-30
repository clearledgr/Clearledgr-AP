"""QuickBooks + Xero IntakeAdapter tests.

Three layers:

  1. ``parse_envelope`` — synthetic per-entity payload shape that
     the webhook route fans out into. Covers Create / Update /
     Delete / unknown-operation / empty payload.
  2. ``enrich`` — happy path with mocked REST GET, fallback to
     thin envelope when fetch fails, and (Xero only) ACCREC →
     not_a_bill marker.
  3. ``derive_state_update`` — paid / cancelled / update mappings.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# Importing the adapter modules registers them via side-effect.
from clearledgr.integrations.erp_quickbooks_intake_adapter import (  # noqa: E402
    QuickBooksIntakeAdapter,
)
from clearledgr.integrations.erp_xero_intake_adapter import (  # noqa: E402
    XeroIntakeAdapter,
)


# ---------------------------------------------------------------------------
# QuickBooks
# ---------------------------------------------------------------------------


class TestQuickBooksParseEnvelope:
    @pytest.mark.asyncio
    async def test_create_event_maps_to_create(self):
        adapter = QuickBooksIntakeAdapter()
        synthetic = json.dumps({
            "realmId": "9341453000000000",
            "entity_id": "42",
            "operation": "Create",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == "create"
        assert env.source_id == "42"
        assert env.source_type == "quickbooks"
        assert env.organization_id == "org-1"
        assert env.channel_metadata["qb_realm_id"] == "9341453000000000"
        assert env.channel_metadata["qb_operation"] == "Create"

    @pytest.mark.asyncio
    async def test_update_event_maps_to_update(self):
        adapter = QuickBooksIntakeAdapter()
        synthetic = json.dumps({
            "realmId": "1", "entity_id": "42", "operation": "Update",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == "update"

    @pytest.mark.asyncio
    async def test_delete_event_maps_to_cancelled(self):
        adapter = QuickBooksIntakeAdapter()
        synthetic = json.dumps({
            "realmId": "1", "entity_id": "42", "operation": "Delete",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == "cancelled"

    @pytest.mark.asyncio
    async def test_unknown_operation_yields_empty_event_type(self):
        # The dispatcher treats empty event_type as a no-op; that's
        # the safe fallback for events QBO might add in the future.
        adapter = QuickBooksIntakeAdapter()
        synthetic = json.dumps({
            "realmId": "1", "entity_id": "42", "operation": "Audit",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == ""

    @pytest.mark.asyncio
    async def test_empty_payload_returns_empty_envelope(self):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(b"", {}, "org-1")
        assert env.event_type == ""
        assert env.source_id == ""


class TestQuickBooksVerifySignature:
    @pytest.mark.asyncio
    async def test_authentic_signature_verifies(self):
        secret = "dev-verifier-token"
        body = b'{"eventNotifications":[]}'
        sig = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")
        adapter = QuickBooksIntakeAdapter()
        assert await adapter.verify_signature(body, {"intuit-signature": sig}, secret)

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self):
        adapter = QuickBooksIntakeAdapter()
        body = b'{"eventNotifications":[]}'
        bogus = base64.b64encode(b"x" * 32).decode("utf-8")
        assert not await adapter.verify_signature(
            body, {"intuit-signature": bogus}, "dev-verifier-token",
        )

    @pytest.mark.asyncio
    async def test_missing_signature_header_rejected(self):
        adapter = QuickBooksIntakeAdapter()
        assert not await adapter.verify_signature(b"", {}, "dev-verifier-token")


class TestQuickBooksEnrich:
    @pytest.mark.asyncio
    async def test_enrich_with_full_bill_returns_invoice_data(self, monkeypatch):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "realmId": "9341453",
                "entity_id": "42",
                "operation": "Create",
            }).encode("utf-8"),
            {},
            "org-1",
        )
        connection = MagicMock()
        connection.access_token = "tok"
        connection.realm_id = "9341453"

        bill_response = {
            "Bill": {
                "Id": "42",
                "DocNumber": "INV-9001",
                "TotalAmt": 1234.56,
                "DueDate": "2026-05-15",
                "SyncToken": "0",
                "Balance": 1234.56,
                "VendorRef": {"value": "13", "name": "Acme Supplies"},
                "APAccountRef": {"value": "33", "name": "AP"},
                "CurrencyRef": {"value": "USD"},
                "Line": [{
                    "Description": "Widgets",
                    "Amount": 1000.0,
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "60", "name": "Office Supplies"},
                    },
                }],
            },
        }
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json = MagicMock(return_value=bill_response)

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)

        monkeypatch.setattr(
            QuickBooksIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: connection),
        )
        with patch(
            "clearledgr.core.http_client.get_http_client",
            return_value=fake_client,
        ):
            invoice = await adapter.enrich("org-1", env)

        assert invoice.source_type == "quickbooks"
        assert invoice.source_id == "42"
        assert invoice.vendor_name == "Acme Supplies"
        assert invoice.amount == 1234.56
        assert invoice.invoice_number == "INV-9001"
        assert invoice.due_date == "2026-05-15"
        assert invoice.erp_native is True
        assert invoice.erp_metadata["qb_bill_id"] == "42"
        assert invoice.erp_metadata["qb_doc_number"] == "INV-9001"
        assert invoice.erp_metadata["qb_realm_id"] == "9341453"
        assert len(invoice.line_items) == 1
        assert invoice.line_items[0]["description"] == "Widgets"
        assert invoice.line_items[0]["gl_code"] == "60"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_thin_when_no_connection(self, monkeypatch):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "realmId": "9341453", "entity_id": "42", "operation": "Create",
            }).encode("utf-8"),
            {}, "org-1",
        )
        monkeypatch.setattr(
            QuickBooksIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: None),
        )
        invoice = await adapter.enrich("org-1", env)
        assert invoice.source_type == "quickbooks"
        assert invoice.erp_metadata["fallback_thin_intake"] is True
        assert invoice.invoice_number == "42"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_when_bill_fetch_404s(self, monkeypatch):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "realmId": "9341453", "entity_id": "42", "operation": "Create",
            }).encode("utf-8"),
            {}, "org-1",
        )
        connection = MagicMock(access_token="tok", realm_id="9341453")
        fake_response = MagicMock()
        fake_response.status_code = 404
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        monkeypatch.setattr(
            QuickBooksIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: connection),
        )
        with patch(
            "clearledgr.core.http_client.get_http_client",
            return_value=fake_client,
        ):
            invoice = await adapter.enrich("org-1", env)
        assert invoice.erp_metadata["fallback_thin_intake"] is True


class TestQuickBooksDeriveStateUpdate:
    @pytest.mark.asyncio
    async def test_cancelled_event_targets_closed(self):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({"realmId": "1", "entity_id": "42", "operation": "Delete"}).encode("utf-8"),
            {}, "org-1",
        )
        update = await adapter.derive_state_update("org-1", env)
        from clearledgr.core.ap_states import APState
        assert update.target_state == APState.CLOSED.value

    @pytest.mark.asyncio
    async def test_update_event_is_idempotent_no_op(self):
        adapter = QuickBooksIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({"realmId": "1", "entity_id": "42", "operation": "Update"}).encode("utf-8"),
            {}, "org-1",
        )
        update = await adapter.derive_state_update("org-1", env)
        assert update.target_state is None
        assert update.idempotent_no_op_allowed is True


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------


class TestXeroParseEnvelope:
    @pytest.mark.asyncio
    async def test_create_event_maps_to_create(self):
        adapter = XeroIntakeAdapter()
        synthetic = json.dumps({
            "tenant_id": "abc",
            "resource_id": "inv-guid",
            "event_type": "CREATE",
            "event_category": "INVOICE",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == "create"
        assert env.source_id == "inv-guid"
        assert env.channel_metadata["xero_tenant_id"] == "abc"

    @pytest.mark.asyncio
    async def test_delete_event_maps_to_cancelled(self):
        adapter = XeroIntakeAdapter()
        synthetic = json.dumps({
            "tenant_id": "abc",
            "resource_id": "inv-guid",
            "event_type": "DELETE",
            "event_category": "INVOICE",
        }).encode("utf-8")
        env = await adapter.parse_envelope(synthetic, {}, "org-1")
        assert env.event_type == "cancelled"


class TestXeroEnrich:
    @pytest.mark.asyncio
    async def test_enrich_accpay_invoice_returns_real_invoice_data(self, monkeypatch):
        adapter = XeroIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "tenant_id": "abc", "resource_id": "inv-1",
                "event_type": "CREATE", "event_category": "INVOICE",
            }).encode("utf-8"),
            {}, "org-1",
        )
        connection = MagicMock(access_token="tok", tenant_id="abc")
        xero_response = {
            "Invoices": [{
                "InvoiceID": "inv-1",
                "Type": "ACCPAY",
                "InvoiceNumber": "INV-7",
                "Total": 999.99,
                "SubTotal": 900.0,
                "TotalTax": 99.99,
                "AmountDue": 999.99,
                "AmountPaid": 0,
                "Status": "AUTHORISED",
                "DueDate": "2026-05-15",
                "CurrencyCode": "EUR",
                "Contact": {"Name": "Beta GmbH", "ContactID": "v1", "EmailAddress": "ap@beta.de"},
                "LineItems": [{"Description": "x", "Quantity": 1, "UnitAmount": 900.0, "LineAmount": 900.0, "AccountCode": "6000"}],
            }],
        }
        fake_response = MagicMock(status_code=200)
        fake_response.json = MagicMock(return_value=xero_response)
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        monkeypatch.setattr(
            XeroIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: connection),
        )
        with patch(
            "clearledgr.core.http_client.get_http_client",
            return_value=fake_client,
        ):
            invoice = await adapter.enrich("org-1", env)

        assert invoice.source_type == "xero"
        assert invoice.vendor_name == "Beta GmbH"
        assert invoice.amount == 999.99
        assert invoice.currency == "EUR"
        assert invoice.invoice_number == "INV-7"
        assert invoice.due_date == "2026-05-15"
        assert invoice.erp_metadata["xero_invoice_type"] == "ACCPAY"
        assert invoice.erp_metadata.get("not_a_bill") in (None, False)
        assert invoice.sender == "ap@beta.de"

    @pytest.mark.asyncio
    async def test_enrich_accrec_invoice_returns_not_a_bill_marker(self, monkeypatch):
        # Critical: ACCREC sales invoices arrive on the same INVOICE
        # channel as ACCPAY bills; the adapter MUST mark them
        # not_a_bill so the dispatcher short-circuits without
        # creating a phantom AP item.
        adapter = XeroIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "tenant_id": "abc", "resource_id": "inv-1",
                "event_type": "CREATE", "event_category": "INVOICE",
            }).encode("utf-8"),
            {}, "org-1",
        )
        connection = MagicMock(access_token="tok", tenant_id="abc")
        sales_response = {
            "Invoices": [{
                "InvoiceID": "inv-1",
                "Type": "ACCREC",
                "InvoiceNumber": "SALE-1",
                "Total": 500.0,
                "Contact": {"Name": "Customer Co", "ContactID": "c1"},
            }],
        }
        fake_response = MagicMock(status_code=200)
        fake_response.json = MagicMock(return_value=sales_response)
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        monkeypatch.setattr(
            XeroIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: connection),
        )
        with patch(
            "clearledgr.core.http_client.get_http_client",
            return_value=fake_client,
        ):
            invoice = await adapter.enrich("org-1", env)

        assert invoice.erp_metadata.get("not_a_bill") is True
        assert invoice.erp_metadata.get("skip_reason") == "non_accpay_invoice"
        assert invoice.erp_metadata.get("xero_invoice_type") == "ACCREC"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_thin_when_fetch_fails(self, monkeypatch):
        adapter = XeroIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "tenant_id": "abc", "resource_id": "inv-1",
                "event_type": "CREATE", "event_category": "INVOICE",
            }).encode("utf-8"),
            {}, "org-1",
        )
        connection = MagicMock(access_token="tok", tenant_id="abc")
        fake_response = MagicMock(status_code=404)
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        monkeypatch.setattr(
            XeroIntakeAdapter, "_resolve_connection",
            staticmethod(lambda org_id: connection),
        )
        with patch(
            "clearledgr.core.http_client.get_http_client",
            return_value=fake_client,
        ):
            invoice = await adapter.enrich("org-1", env)
        assert invoice.erp_metadata["fallback_thin_intake"] is True


class TestXeroDeriveStateUpdate:
    @pytest.mark.asyncio
    async def test_cancelled_event_targets_closed(self):
        adapter = XeroIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "tenant_id": "abc", "resource_id": "inv-1",
                "event_type": "DELETE", "event_category": "INVOICE",
            }).encode("utf-8"),
            {}, "org-1",
        )
        update = await adapter.derive_state_update("org-1", env)
        from clearledgr.core.ap_states import APState
        assert update.target_state == APState.CLOSED.value

    @pytest.mark.asyncio
    async def test_update_event_is_idempotent_no_op(self):
        adapter = XeroIntakeAdapter()
        env = await adapter.parse_envelope(
            json.dumps({
                "tenant_id": "abc", "resource_id": "inv-1",
                "event_type": "UPDATE", "event_category": "INVOICE",
            }).encode("utf-8"),
            {}, "org-1",
        )
        update = await adapter.derive_state_update("org-1", env)
        assert update.target_state is None
        assert update.idempotent_no_op_allowed is True


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def test_qb_and_xero_adapters_registered():
    """Both adapters register on import. Without this, the universal
    handler returns ``no_adapter`` and the webhook silently drops
    intake events."""
    from clearledgr.services.intake_adapter import list_registered_sources
    sources = list_registered_sources()
    assert "quickbooks" in sources
    assert "xero" in sources


# ---------------------------------------------------------------------------
# Dispatcher: ``not_a_bill`` short-circuit
# ---------------------------------------------------------------------------


class TestDispatcherSkipsNotABill:
    @pytest.mark.asyncio
    async def test_not_a_bill_invoice_does_not_create_ap_item(self, monkeypatch):
        # When XeroIntakeAdapter.enrich returns a not_a_bill marker
        # (ACCREC sales invoice), _dispatch_create_like must skip
        # process_new_invoice — otherwise we'd mint a phantom AP
        # item for a customer invoice.
        from clearledgr.services.intake_adapter import (
            IntakeEnvelope,
            _dispatch_create_like,
        )
        from clearledgr.services.invoice_models import InvoiceData

        envelope = IntakeEnvelope(
            source_type="xero",
            event_type="create",
            source_id="inv-1",
            organization_id="org-1",
            raw_payload={},
        )

        marker_invoice = InvoiceData(
            source_type="xero",
            source_id="inv-1",
            erp_native=True,
            erp_metadata={"not_a_bill": True, "skip_reason": "non_accpay_invoice"},
            subject="x",
            sender="x",
            vendor_name="(non-bill)",
            amount=0.0,
            currency="USD",
            invoice_number="inv-1",
            confidence=1.0,
            organization_id="org-1",
        )

        adapter = MagicMock()
        adapter.enrich = AsyncMock(return_value=marker_invoice)

        process_new_invoice = AsyncMock()

        fake_db = MagicMock()
        fake_db.get_ap_item_by_erp_reference = MagicMock(return_value=None)

        with patch(
            "clearledgr.services.intake_adapter.get_db",
            return_value=fake_db,
        ), patch(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
        ) as mock_get_workflow:
            mock_get_workflow.return_value.process_new_invoice = process_new_invoice
            result = await _dispatch_create_like(adapter, envelope)

        assert result["ok"] is True
        assert result["reason"] == "skipped_non_bill"
        process_new_invoice.assert_not_awaited()
