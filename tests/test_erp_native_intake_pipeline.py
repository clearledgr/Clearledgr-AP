"""Tests for the ERP-native intake pipeline (Phase A-E refactor).

Covers the seams between the channel-agnostic ``InvoiceData`` shape
and the existing coordination primitives:

* Synthetic ``gmail_id`` derivation for non-Gmail sources
* ``GmailLabelObserver`` short-circuits when the event isn't Gmail-origin
* ``VendorDomainTrackingObserver`` short-circuits for ERP-native
* ``InvoicePostingMixin._post_to_erp`` returns a successful PostResult
  without calling ``post_bill_to_*`` when ``invoice.erp_native``
* Approval card builder produces source-aware deeplinks
* NetSuite dispatcher's lightweight-update state derivation
* SAP dispatcher's event normalization (CloudEvents + ABAP shapes)
* SAP dispatcher's composite-key extraction from both naming conventions
* SAP dispatcher's state derivation
* PO-sync idempotency on replay

Avoids Postgres dependencies — everything here is pure-Python or
uses a minimal in-memory mock DB.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── InvoiceData synthetic gmail_id ────────────────────────────────


def test_invoice_data_gmail_path_unchanged():
    """Gmail callers don't have to set source_type — defaults preserve
    backward compatibility."""
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(gmail_id="msg-abc", subject="x", sender="y", vendor_name="V", amount=100)
    assert inv.gmail_id == "msg-abc"
    assert inv.source_type == "gmail"
    assert inv.source_id == "msg-abc"  # mirrored
    assert inv.erp_native is False


def test_invoice_data_netsuite_synthesises_gmail_id():
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(
        source_type="netsuite", source_id="5135",
        subject="x", sender="y", vendor_name="V", amount=100,
        erp_native=True,
        erp_metadata={"ns_internal_id": "5135"},
    )
    assert inv.gmail_id == "netsuite-bill:5135"
    assert inv.source_type == "netsuite"
    assert inv.erp_native is True
    assert inv.erp_metadata == {"ns_internal_id": "5135"}


def test_invoice_data_sap_synthesises_gmail_id():
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(
        source_type="sap_s4hana", source_id="1010/5105600123/2026",
        subject="x", sender="y", vendor_name="V", amount=100,
        erp_native=True,
    )
    assert inv.gmail_id == "sap_s4hana-bill:1010/5105600123/2026"
    assert inv.erp_native is True


# ─── Observer guards ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gmail_label_observer_skips_non_gmail():
    """ERP-native state transitions must NOT call the Gmail labels API
    — the synthetic gmail_id like 'netsuite-bill:5135' isn't a real
    Gmail message, so calling Gmail would 404 every time."""
    from clearledgr.services.state_observers import (
        GmailLabelObserver, StateTransitionEvent,
    )
    db = MagicMock()
    db.get_invoice_status.return_value = {"id": "AP-1", "user_id": "u1"}
    obs = GmailLabelObserver(db)

    erp_native_event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org",
        old_state="received", new_state="validated",
        gmail_id="netsuite-bill:5135",
        source_type="netsuite", erp_native=True,
    )
    # If the observer didn't short-circuit, it would call get_invoice_status.
    await obs.on_transition(erp_native_event)
    db.get_invoice_status.assert_not_called()


@pytest.mark.asyncio
async def test_gmail_label_observer_runs_for_gmail():
    """Sanity: the observer does fire for genuine Gmail events."""
    from clearledgr.services.state_observers import (
        GmailLabelObserver, StateTransitionEvent,
    )
    db = MagicMock()
    db.get_invoice_status.return_value = {"message_id": "real-gmail-msg-id", "user_id": "u1"}

    obs = GmailLabelObserver(db)
    gmail_event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org",
        old_state="received", new_state="validated",
        gmail_id="real-gmail-msg-id",
        source_type="gmail", erp_native=False,
    )
    # It will try to get_invoice_status; we don't care if Gmail-API
    # call after that fails, only that the early-return guard didn't fire.
    with patch("clearledgr.services.gmail_api.GmailAPIClient") as fake_client_cls:
        fake_client = MagicMock()
        fake_client.ensure_authenticated = AsyncMock(return_value=False)
        fake_client_cls.return_value = fake_client
        await obs.on_transition(gmail_event)
    db.get_invoice_status.assert_called_once_with("real-gmail-msg-id")


@pytest.mark.asyncio
async def test_vendor_domain_tracking_observer_skips_erp_native():
    """The synthetic ERP-native sender ('<netsuite@erp-native>') must
    not poison the trusted-domain TOFU set."""
    from clearledgr.services.state_observers import (
        StateTransitionEvent, VendorDomainTrackingObserver,
    )
    db = MagicMock()
    obs = VendorDomainTrackingObserver(db)
    erp_native_event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org",
        old_state="ready_to_post", new_state="posted_to_erp",
        gmail_id="netsuite-bill:5135",
        source_type="netsuite", erp_native=True,
    )
    await obs.on_transition(erp_native_event)
    db.get_ap_item.assert_not_called()


# ─── _post_to_erp short-circuit ────────────────────────────────────


@pytest.mark.asyncio
async def test_post_to_erp_short_circuits_for_erp_native():
    """ERP-native bills must not trigger a real ERP write — a duplicate
    bill in the customer's ERP would be a serious bug."""
    from clearledgr.services.invoice_models import InvoiceData
    from clearledgr.services.invoice_posting import InvoicePostingMixin

    invoice = InvoiceData(
        source_type="netsuite", source_id="5135", erp_native=True,
        subject="NS bill", sender="vendor@ns", vendor_name="Acme",
        amount=1000, currency="USD", invoice_number="INV-001",
        organization_id="org-1",
        erp_metadata={"ns_internal_id": "5135"},
    )

    class _FakeMixin(InvoicePostingMixin):
        def __init__(self):
            self.organization_id = "org-1"
            self.db = MagicMock()
            self.db.get_ap_item.return_value = {"id": "AP-x", "erp_reference": "5135", "state": "ready_to_post"}

        def _lookup_ap_item_id(self, **kwargs):
            return "AP-x"

    mixin = _FakeMixin()
    # Spy on the actual ERP-write path. If our short-circuit works,
    # post_bill_to_* is never called.
    with patch("clearledgr.integrations.erp_router.post_bill_to_quickbooks", new_callable=AsyncMock) as qb_post, \
         patch("clearledgr.integrations.erp_router.post_bill_to_xero", new_callable=AsyncMock) as xero_post, \
         patch("clearledgr.integrations.erp_router.post_bill_to_sap", new_callable=AsyncMock) as sap_post, \
         patch("clearledgr.integrations.erp_router.post_bill_to_netsuite", new_callable=AsyncMock) as ns_post:
        result = await mixin._post_to_erp(invoice)

    assert result["status"] == "success"
    assert result["skipped_post"] is True
    assert result["posted_by_erp_native"] is True
    assert result["erp_reference"] == "5135"
    qb_post.assert_not_called()
    xero_post.assert_not_called()
    sap_post.assert_not_called()
    ns_post.assert_not_called()


# ─── Approval card source-aware deeplinks ──────────────────────────


def test_approval_link_gmail_path():
    from clearledgr.services.approval_card_builder import (
        _build_source_link, _source_link_label,
    )
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(gmail_id="msg-abc", subject="x", sender="y", vendor_name="V", amount=100)
    assert _build_source_link(inv) == "https://mail.google.com/mail/u/0/#search/msg-abc"
    assert _source_link_label(inv) == "Open in Gmail"


def test_approval_link_netsuite_full_metadata():
    from clearledgr.services.approval_card_builder import (
        _build_source_link, _source_link_label,
    )
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(
        source_type="netsuite", source_id="5135", erp_native=True,
        subject="x", sender="y", vendor_name="V", amount=100,
        erp_metadata={"ns_account_id": "TD2818617", "ns_internal_id": "5135"},
    )
    link = _build_source_link(inv)
    assert "td2818617.app.netsuite.com" in link
    assert "id=5135" in link
    assert _source_link_label(inv) == "Open in NetSuite"


def test_approval_link_sap_full_metadata():
    from clearledgr.services.approval_card_builder import (
        _build_source_link, _source_link_label,
    )
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(
        source_type="sap_s4hana", source_id="1010/5135/2026", erp_native=True,
        subject="x", sender="y", vendor_name="V", amount=100,
        erp_metadata={
            "sap_fiori_host": "fiori.bookingcorp.com",
            "company_code": "1010", "supplier_invoice": "5135", "fiscal_year": "2026",
        },
    )
    link = _build_source_link(inv)
    assert "fiori.bookingcorp.com" in link
    assert "CompanyCode=1010" in link
    assert _source_link_label(inv) == "Open in SAP"


def test_approval_link_falls_back_to_clearledgr_when_metadata_missing(monkeypatch):
    """A NetSuite bill missing its ns_account_id should not produce a
    dead link — fall back to the Clearledgr workspace SPA.

    Pin APP_BASE_URL to the canonical workspace host so the test is
    self-contained and doesn't depend on whatever the local ``.env`` /
    Railway env has it set to.
    """
    monkeypatch.setenv("APP_BASE_URL", "https://workspace.clearledgr.com")
    from clearledgr.services.approval_card_builder import (
        _build_source_link, _source_link_label,
    )
    from clearledgr.services.invoice_models import InvoiceData
    inv = InvoiceData(
        source_type="netsuite", source_id="5135", erp_native=True,
        subject="x", sender="y", vendor_name="V", amount=100,
        erp_metadata={"ns_internal_id": "5135"},  # account_id missing
    )
    link = _build_source_link(inv)
    # The fallback uses APP_BASE_URL + /items/<id>; verify both halves
    # so a future re-route (e.g. /ap/items/<id>) still trips the test.
    assert "workspace.clearledgr.com" in link
    assert "/items/5135" in link
    assert _source_link_label(inv) == "Open in Clearledgr"


# ─── SAP IntakeAdapter event normalization + state derivation ──────


@pytest.mark.asyncio
async def test_sap_adapter_normalizes_cloudevents_shape():
    import json
    from clearledgr.integrations.erp_sap_s4hana_intake_adapter import SapS4HanaIntakeAdapter
    adapter = SapS4HanaIntakeAdapter()
    raw = json.dumps({
        "type": "sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created.v1",
        "data": {
            "CompanyCode": "1010", "SupplierInvoice": "5105600123",
            "FiscalYear": "2026", "PaymentBlockingReason": "A",
        },
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    assert env.event_type == "create"
    assert env.source_id == "1010/5105600123/2026"
    assert env.source_type == "sap_s4hana"

    update = await adapter.derive_state_update("org-1", env._replace(event_type="blocked") if False else env)
    # event_type is 'create', so derive_state_update returns no-op (None target)
    assert update.target_state is None

    # Now test blocked event derivation
    raw_blocked = json.dumps({
        "type": "sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Blocked.v1",
        "data": {
            "CompanyCode": "1010", "SupplierInvoice": "5105600123",
            "FiscalYear": "2026", "PaymentBlockingReason": "A",
        },
    }).encode()
    env_blocked = await adapter.parse_envelope(raw_blocked, {}, "org-1")
    update = await adapter.derive_state_update("org-1", env_blocked)
    assert update.target_state == "needs_approval"


@pytest.mark.asyncio
async def test_sap_adapter_normalizes_abap_shape():
    """ABAP BAdI senders use UPPER_SNAKE field names (BUKRS, BELNR, GJAHR)."""
    import json
    from clearledgr.integrations.erp_sap_s4hana_intake_adapter import SapS4HanaIntakeAdapter
    adapter = SapS4HanaIntakeAdapter()
    raw = json.dumps({
        "event_type": "supplier_invoice.posted",
        "invoice": {"BUKRS": "1010", "BELNR": "5105600123", "GJAHR": "2026", "ZLSPR": "", "InvoiceStatus": "Posted"},
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    assert env.event_type == "posted"
    assert env.source_id == "1010/5105600123/2026"


@pytest.mark.asyncio
async def test_sap_adapter_paid_event_resolves_to_closed():
    import json
    from clearledgr.integrations.erp_sap_s4hana_intake_adapter import SapS4HanaIntakeAdapter
    adapter = SapS4HanaIntakeAdapter()
    raw = json.dumps({
        "type": "sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Paid.v1",
        "data": {
            "CompanyCode": "1010", "SupplierInvoice": "5105600123",
            "FiscalYear": "2026", "InvoiceStatus": "Paid In Full",
        },
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    assert env.event_type == "paid"
    update = await adapter.derive_state_update("org-1", env)
    assert update.target_state == "closed"


@pytest.mark.asyncio
async def test_sap_adapter_missing_composite_key_returns_empty_source_id():
    import json
    from clearledgr.integrations.erp_sap_s4hana_intake_adapter import SapS4HanaIntakeAdapter
    adapter = SapS4HanaIntakeAdapter()
    raw = json.dumps({"type": "sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created.v1", "data": {"CompanyCode": "1010"}}).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    assert env.source_id == ""  # missing doc + fy


# ─── NetSuite IntakeAdapter state derivation ──────────────────────


@pytest.mark.asyncio
async def test_netsuite_adapter_state_from_paid_bill():
    import json
    from clearledgr.integrations.erp_netsuite_intake_adapter import NetSuiteIntakeAdapter
    adapter = NetSuiteIntakeAdapter()
    raw = json.dumps({
        "event_type": "vendorbill.update",
        "bill": {"ns_internal_id": "5135", "status_label": "Paid In Full"},
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    update = await adapter.derive_state_update("org-1", env)
    assert update.target_state == "closed"


@pytest.mark.asyncio
async def test_netsuite_adapter_state_from_payment_hold():
    import json
    from clearledgr.integrations.erp_netsuite_intake_adapter import NetSuiteIntakeAdapter
    adapter = NetSuiteIntakeAdapter()
    raw = json.dumps({
        "event_type": "vendorbill.update",
        "bill": {"ns_internal_id": "5135", "payment_hold": "T"},
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    update = await adapter.derive_state_update("org-1", env)
    assert update.target_state == "needs_approval"


@pytest.mark.asyncio
async def test_netsuite_adapter_state_from_open_bill():
    import json
    from clearledgr.integrations.erp_netsuite_intake_adapter import NetSuiteIntakeAdapter
    adapter = NetSuiteIntakeAdapter()
    raw = json.dumps({
        "event_type": "vendorbill.update",
        "bill": {"ns_internal_id": "5135", "status_label": "Open"},
    }).encode()
    env = await adapter.parse_envelope(raw, {}, "org-1")
    update = await adapter.derive_state_update("org-1", env)
    assert update.target_state == "posted_to_erp"


# ─── erp_intake_po_sync idempotency ────────────────────────────────


def test_upsert_netsuite_po_idempotent_on_replay():
    """Replays of the same SuiteScript event must not create
    duplicate POs."""
    from clearledgr.services.erp_intake_po_sync import upsert_netsuite_po

    fake_po_payload = {"id": "12345", "tranId": "PO-NS-001", "entity": {"id": "v1", "refName": "Acme"}}
    fake_po_lines = [{"line_id": "L1", "description": "widget", "quantity": 10, "unit_price": 100, "amount": 1000}]
    fake_receipts: List[Dict[str, Any]] = []

    captured_po: Dict[str, Any] = {"create_called": 0}
    # Shared DB mock so state persists across the two FakeService
    # instantiations (matches the real singleton get_db() behaviour).
    shared_db = MagicMock()
    shared_db.list_goods_receipts_for_po.return_value = []
    # Tracks whether we've already inserted a PO; second call returns
    # the existing row.
    shared_state = {"po_inserted": False}

    def _get_po_by_number(org_id, po_number):
        if shared_state["po_inserted"]:
            return {"po_id": "PO-CL-1", "po_number": po_number}
        return None
    shared_db.get_purchase_order_by_number.side_effect = _get_po_by_number

    class FakeService:
        def __init__(self, organization_id: str = "default"):
            self.organization_id = organization_id
            self._db = shared_db

        def create_po(self, **kwargs):
            captured_po["create_called"] += 1
            captured_po.update(kwargs)
            shared_state["po_inserted"] = True
            from clearledgr.services.purchase_orders import PurchaseOrder
            po = PurchaseOrder(
                vendor_id=kwargs.get("vendor_id"),
                vendor_name=kwargs.get("vendor_name"),
                requested_by=kwargs.get("requested_by"),
                organization_id=self.organization_id,
            )
            po.po_id = "PO-CL-1"
            po.po_number = kwargs.get("po_number") or "NS-PO-NS-001"
            return po

        def create_goods_receipt(self, **kwargs):
            return MagicMock()

    with patch("clearledgr.services.erp_intake_po_sync.PurchaseOrderService", FakeService):
        # First call: creates the PO
        po_id_1 = upsert_netsuite_po(
            organization_id="org-1",
            po_payload=fake_po_payload,
            po_lines=fake_po_lines,
            item_receipts=fake_receipts,
        )
        # Second call: idempotent — no new create_po
        po_id_2 = upsert_netsuite_po(
            organization_id="org-1",
            po_payload=fake_po_payload,
            po_lines=fake_po_lines,
            item_receipts=fake_receipts,
        )

    assert po_id_1 == "PO-CL-1"
    assert po_id_2 == "PO-CL-1"
    assert captured_po["create_called"] == 1, "create_po should only fire once across two replays"


# ─── Cross-tenant guard on SAP exchange (multi-tenant XSUAA) ───────


def test_sap_xsuaa_resolver_matches_correct_tenant():
    from clearledgr.api.sap_extension import _resolve_xsuaa_config_for_issuer

    class MockDB:
        def list_organizations(self):
            return [{"id": "booking-corp"}, {"id": "cowrywise"}]
        def get_erp_connections(self, org_id):
            if org_id == "booking-corp":
                return [{"erp_type": "sap_s4hana", "credentials": {
                    "s4hana_xsuaa_issuer": "https://booking.authentication.eu10.hana.ondemand.com/oauth/token",
                    "s4hana_xsuaa_jwks_url": "https://booking.authentication.eu10.hana.ondemand.com/token_keys",
                    "s4hana_xsuaa_audience": "clearledgr-boxpanel-prod",
                }}]
            return []

    db = MockDB()
    cfg = _resolve_xsuaa_config_for_issuer(
        db, "https://booking.authentication.eu10.hana.ondemand.com/oauth/token",
    )
    assert cfg is not None
    assert cfg["organization_id"] == "booking-corp"
    assert cfg["audience"] == "clearledgr-boxpanel-prod"


def test_sap_xsuaa_resolver_rejects_unknown_issuer():
    """Forged JWT with an issuer we've never seen should not match any
    tenant — falls through to the env-var fallback (or 503)."""
    from clearledgr.api.sap_extension import _resolve_xsuaa_config_for_issuer

    class MockDB:
        def list_organizations(self):
            return [{"id": "booking-corp"}]
        def get_erp_connections(self, org_id):
            return [{"erp_type": "sap_s4hana", "credentials": {
                "s4hana_xsuaa_issuer": "https://booking.authentication.eu10.hana.ondemand.com/oauth/token",
            }}]

    db = MockDB()
    cfg = _resolve_xsuaa_config_for_issuer(db, "https://attacker.example.com/oauth/token")
    assert cfg is None


# ─── End-to-end-ish: dispatcher routes ERP-native through workflow ──


@pytest.mark.asyncio
async def test_handle_intake_event_routes_through_full_pipeline_for_netsuite():
    """When the NetSuite adapter is registered + connection present,
    handle_intake_event should call workflow.process_new_invoice —
    NOT db.create_ap_item directly. The pipeline owns Box creation."""
    import json
    import clearledgr.services.intake_adapter as adapter_mod
    # Ensure adapter is registered
    import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401

    fake_db = MagicMock()
    fake_db.get_ap_item_by_erp_reference.return_value = None
    fake_db.get_erp_connections.return_value = [
        {"erp_type": "netsuite", "credentials": {"account_id": "ACCT", "consumer_key": "k", "consumer_secret": "s", "token_id": "t", "token_secret": "ts"}, "access_token": None, "refresh_token": None}
    ]
    fake_db.update_ap_item.return_value = True

    fake_intake = {
        "bill_header": {"ns_internal_id": "5135", "tran_id": "BILL-001", "vendor_name": "Acme", "amount": "1000"},
        "bill_lines": [], "expense_lines": [], "vendor": None,
        "linked_po": None, "linked_po_lines": [],
        "goods_receipts": [], "vendor_bank_history": [],
        "raw_payload": {"id": "5135"},
    }
    fake_workflow = MagicMock()
    fake_workflow.process_new_invoice = AsyncMock(return_value={"status": "received", "state": "needs_approval", "ap_item_id": "AP-NEW"})

    raw = json.dumps({
        "event_type": "vendorbill.create",
        "account_id": "ACCT", "event_id": "evt-1",
        "bill": {
            "ns_internal_id": "5135", "entity_name": "Acme",
            "amount": "1000", "currency": "USD", "invoice_number": "BILL-001",
        },
    }).encode()

    with patch.object(adapter_mod, "get_db", return_value=fake_db), \
         patch("clearledgr.integrations.erp_netsuite_intake_adapter.get_db", return_value=fake_db), \
         patch("clearledgr.integrations.erp_netsuite_intake.fetch_intake_context", new_callable=AsyncMock, return_value=fake_intake), \
         patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=fake_workflow), \
         patch("clearledgr.integrations.erp_netsuite_intake_adapter.verify_netsuite_signature", return_value=True):
        result = await adapter_mod.handle_intake_event(
            source_type="netsuite",
            organization_id="org-1",
            raw=raw,
            headers={"X-NetSuite-Signature": "v1=fake", "X-NetSuite-Timestamp": "0"},
            secret="any-secret",
        )

    assert result["ok"] is True
    assert result["action"] == "created"
    assert result["state"] == "needs_approval"
    fake_workflow.process_new_invoice.assert_awaited_once()
    invoice_passed = fake_workflow.process_new_invoice.call_args.args[0]
    assert invoice_passed.source_type == "netsuite"
    assert invoice_passed.erp_native is True
    assert invoice_passed.source_id == "5135"
    assert invoice_passed.gmail_id == "netsuite-bill:5135"
    fake_db.create_ap_item.assert_not_called()


@pytest.mark.asyncio
async def test_handle_intake_event_falls_back_to_thin_intake_without_connection():
    """Orgs without a configured NetSuite connection get a thin
    InvoiceData built from the envelope alone — pipeline still runs,
    just without the enrichment data."""
    import json
    import clearledgr.services.intake_adapter as adapter_mod
    import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401

    fake_db = MagicMock()
    fake_db.get_ap_item_by_erp_reference.return_value = None
    fake_db.get_erp_connections.return_value = []  # no connection

    fake_workflow = MagicMock()
    fake_workflow.process_new_invoice = AsyncMock(return_value={"status": "received", "state": "needs_approval", "ap_item_id": "AP-THIN"})

    raw = json.dumps({
        "event_type": "vendorbill.create",
        "account_id": "ACCT", "event_id": "evt-1",
        "bill": {"ns_internal_id": "5135", "entity_name": "Acme", "amount": "1000", "currency": "USD"},
    }).encode()

    with patch.object(adapter_mod, "get_db", return_value=fake_db), \
         patch("clearledgr.integrations.erp_netsuite_intake_adapter.get_db", return_value=fake_db), \
         patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=fake_workflow), \
         patch("clearledgr.integrations.erp_netsuite_intake_adapter.verify_netsuite_signature", return_value=True):
        result = await adapter_mod.handle_intake_event(
            source_type="netsuite",
            organization_id="org-1",
            raw=raw,
            headers={"X-NetSuite-Signature": "v1=fake", "X-NetSuite-Timestamp": "0"},
            secret="any-secret",
        )

    # Even without connection, the adapter builds a thin InvoiceData
    # and the pipeline is called — different from "no Box created at all".
    assert result["ok"] is True
    assert result["action"] == "created"
    fake_workflow.process_new_invoice.assert_awaited_once()
    invoice_passed = fake_workflow.process_new_invoice.call_args.args[0]
    assert invoice_passed.source_type == "netsuite"
    assert invoice_passed.erp_native is True
    # Thin invoice carries the fallback marker in metadata
    assert invoice_passed.erp_metadata.get("fallback_thin_intake") is True


@pytest.mark.asyncio
async def test_handle_intake_event_rejects_invalid_signature():
    """Forged webhook → handler returns ok=False, reason=signature_invalid."""
    import json
    import clearledgr.services.intake_adapter as adapter_mod
    import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401
    raw = json.dumps({"event_type": "vendorbill.create", "bill": {"ns_internal_id": "5135"}}).encode()
    with patch("clearledgr.integrations.erp_netsuite_intake_adapter.verify_netsuite_signature", return_value=False):
        result = await adapter_mod.handle_intake_event(
            source_type="netsuite", organization_id="org-1",
            raw=raw, headers={}, secret="any-secret",
        )
    assert result["ok"] is False
    assert result["reason"] == "signature_invalid"


@pytest.mark.asyncio
async def test_handle_intake_event_rejects_unknown_source():
    import clearledgr.services.intake_adapter as adapter_mod
    result = await adapter_mod.handle_intake_event(
        source_type="not_a_real_erp", organization_id="org-1",
        raw=b"{}", headers={}, secret="any-secret",
    )
    assert result["ok"] is False
    assert result["reason"] == "no_adapter"


@pytest.mark.asyncio
async def test_handle_intake_event_rejects_missing_secret():
    import clearledgr.services.intake_adapter as adapter_mod
    import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401
    result = await adapter_mod.handle_intake_event(
        source_type="netsuite", organization_id="org-1",
        raw=b"{}", headers={}, secret=None,
    )
    assert result["ok"] is False
    assert result["reason"] == "no_secret_provisioned"
