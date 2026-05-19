"""Phase 4 — SAP Fiori extension ↔ Phase 1 audit-trail integration.

The Vendor Invoice panel (a SAPUI5 app deployed via SAP BTP HTML5
Repo + Approuter) calls Solden via the new
``POST /extension/ap-items/by-sap-invoice/<action>?company_code=&
supplier_invoice=&fiscal_year=`` endpoints. Each dispatches the
matching runtime intent (``approve_invoice`` / ``reject_invoice`` /
``request_info``) with ``source_channel="erp_native_sap"`` so
Phase 1's ``decision_context`` auto-build records ``ui_surface =
"erp_native_sap"`` on the resulting state_transition audit row.

These tests prove that compose end-to-end through the FastAPI layer:
Solden JWT auth (the panel obtains this via the XSUAA exchange
endpoint after BTP login) → AP item lookup by composite key
``CompanyCode/SupplierInvoice/FiscalYear`` → runtime dispatch →
workflow state transition → audit row with the expected
``decision_context`` shape.

The contract is the same one Phase 2 (Teams) and Phase 3 (NetSuite
panel) verified, exercised through the SAP-side surface. A bug in
the endpoint's ``source_channel`` value, the JWT verification, or
the Phase 2 propagation fix would each fail one of these tests.
They exist to keep the audit chain provably correct across every
render-target surface.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from solden.api.sap_extension import router as sap_extension_router
from solden.core.auth import create_access_token
from solden.core.database import get_db


_TEST_ORG_ID = "sap-fiori-test-org"
_TEST_USER_ID = "fiori-user-1"
_TEST_USER_EMAIL = "fiori-user@bookingcorp.example.com"
_TEST_COMPANY_CODE = "1010"
_TEST_FISCAL_YEAR = "2026"


@pytest.fixture()
def db():
    inst = get_db()
    inst.initialize()
    inst.ensure_organization(_TEST_ORG_ID, organization_name="SAP Fiori Test Org")
    return inst


@pytest.fixture()
def panel_client():
    """FastAPI TestClient with the SAP extension router mounted. The
    Fiori panel authenticates with a Solden JWT minted by the
    XSUAA-exchange endpoint; tests bypass that exchange and mint the
    JWT directly via ``create_access_token``, which is exactly the
    function the exchange endpoint uses internally.
    """
    app = FastAPI()
    app.include_router(sap_extension_router)
    return TestClient(app)


def _mint_panel_token(*, organization_id: str = _TEST_ORG_ID, user_id: str = _TEST_USER_ID, email: str = _TEST_USER_EMAIL) -> str:
    return create_access_token(
        user_id=user_id,
        email=email,
        organization_id=organization_id,
        role="user",
        expires_delta=timedelta(minutes=15),
    )


def _seed_ap_item_at_needs_approval(
    db,
    *,
    ap_item_id: str,
    supplier_invoice: str,
    organization_id: str = _TEST_ORG_ID,
) -> Dict[str, Any]:
    """Seed an AP item with the SAP composite-key ``erp_reference`` shape
    the Fiori panel + the by-sap-invoice endpoints look up by, plus
    the Phase 1 metadata the auto-built decision_context snapshot
    reads from.

    The composite key is ``{CompanyCode}/{SupplierInvoice}/{FiscalYear}``
    per ``clearledgr/services/sap_webhook_dispatch.py``'s intake.
    """
    composite_key = f"{_TEST_COMPANY_CODE}/{supplier_invoice}/{_TEST_FISCAL_YEAR}"
    metadata = {
        "ap_decision_recommendation": "approve",
        "validation_gate": {
            "passed": True,
            "reason_codes": [],
            "rule_results": [
                {"rule_id": "field_presence", "verdict": "pass"},
                {"rule_id": "amount_cross_validation", "verdict": "pass"},
            ],
        },
        "vendor_intelligence": {
            "trusted": True,
            "invoice_count": 31,
            "default_currency": "EUR",
        },
        "fraud_flags": ["low_risk"],
        "reasoning_risks": ["amount_within_normal_range"],
        "field_confidences": {"vendor_name": 0.99, "amount": 0.96},
        "source": "sap_native",
        "sap_company_code": _TEST_COMPANY_CODE,
        "sap_supplier_invoice": supplier_invoice,
        "sap_fiscal_year": _TEST_FISCAL_YEAR,
    }
    payload = {
        "id": ap_item_id,
        "invoice_key": f"AcmeSAP::{supplier_invoice}",
        "thread_id": f"sap-supplier-invoice:{composite_key}",
        "vendor_name": "Acme SAP Supplier",
        "amount": 12500.0,
        "currency": "EUR",
        "invoice_number": f"SAP-{supplier_invoice}",
        "subject": f"SAP Supplier Invoice {composite_key} — Acme SAP Supplier",
        "sender": "<sap-s4hana@erp-native>",
        "state": "received",
        "approval_policy_version": "v2",
        "confidence": 0.96,
        "field_confidences": {"vendor_name": 0.99, "amount": 0.96},
        "organization_id": organization_id,
        "erp_reference": composite_key,
        "metadata": metadata,
    }
    db.create_ap_item(payload)
    db.update_ap_item(
        ap_item_id, state="validated",
        _actor_type="agent", _actor_id="invoice_validation",
        _decision_reason="validation_passed",
    )
    db.update_ap_item(
        ap_item_id, state="needs_approval",
        _actor_type="agent", _actor_id="invoice_validation",
        _decision_reason="route_for_approval",
    )
    return db.get_ap_item(ap_item_id)


def _state_transition_audits(db, ap_item_id: str) -> List[Dict[str, Any]]:
    rows = db.list_ap_audit_events(ap_item_id, limit=50, order="desc") or []
    return [r for r in rows if r.get("event_type") == "state_transition"]


def _payload_decision_context(audit_row: Dict[str, Any]) -> Dict[str, Any]:
    raw = audit_row.get("payload_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        return {}
    decision_context = parsed.get("decision_context")
    return decision_context if isinstance(decision_context, dict) else {}


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_sap_panel_approve_lands_ui_surface_erp_native_sap(
    postgres_test_db, db, panel_client
):
    """POST /approve from inside the SAP Fiori Vendor Invoice panel
    produces a state_transition audit row with
    ``ui_surface="erp_native_sap"`` and the auto-built
    decision_context snapshot from the AP item's metadata.
    """
    ap_item_id = "AP-SAPFIORI-APPROVE"
    supplier_invoice = "5105600101"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    token = _mint_panel_token()

    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/approve",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={"reason": "approved_in_sap_fiori_panel"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "approve_invoice"
    assert body["composite_key"] == f"{_TEST_COMPANY_CODE}/{supplier_invoice}/{_TEST_FISCAL_YEAR}"
    assert body["ap_item_id"] == ap_item_id

    audits = _state_transition_audits(db, ap_item_id)
    sap_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_sap"
    ]
    assert sap_transitions, (
        "No state_transition audit row carries ui_surface='erp_native_sap'. "
        f"Audit rows: {[(a.get('prev_state'), a.get('new_state'), _payload_decision_context(a).get('ui_surface')) for a in audits]}"
    )

    decision_context = _payload_decision_context(sap_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_sap"
    assert decision_context.get("actor_type") == "user"
    assert decision_context.get("actor_id") == _TEST_USER_EMAIL or decision_context.get("actor_id") == _TEST_USER_ID
    # Auto-built fields from the AP item's metadata.
    assert decision_context.get("agent_recommendation") == "approve"
    assert decision_context.get("validation_gate_at_decision", {}).get("passed") is True
    assert decision_context.get("vendor_profile_snapshot", {}).get("trusted") is True
    assert "low_risk" in decision_context.get("risk_flags_shown", [])
    assert decision_context.get("policy_version") == "v2"
    assert "snapshotted_at" in decision_context


def test_sap_panel_reject_lands_ui_surface_erp_native_sap(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-SAPFIORI-REJECT"
    supplier_invoice = "5105600102"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    token = _mint_panel_token()

    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/reject",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={"reason": "duplicate_supplier_invoice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text

    audits = _state_transition_audits(db, ap_item_id)
    sap_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_sap"
    ]
    assert sap_transitions, (
        "Reject from SAP Fiori panel did not land ui_surface='erp_native_sap'."
    )
    decision_context = _payload_decision_context(sap_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_sap"
    assert decision_context.get("actor_type") == "user"


def test_sap_panel_request_info_lands_ui_surface_erp_native_sap(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-SAPFIORI-RFI"
    supplier_invoice = "5105600103"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    token = _mint_panel_token()

    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/request-info",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={"reason": "missing_purchase_order_match"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text

    audits = _state_transition_audits(db, ap_item_id)
    sap_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_sap"
    ]
    assert sap_transitions, (
        "request_info from SAP Fiori panel did not land ui_surface='erp_native_sap'."
    )
    decision_context = _payload_decision_context(sap_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_sap"


def test_sap_panel_action_rejects_missing_bearer_token(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-SAPFIORI-NOTOKEN"
    supplier_invoice = "5105600104"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/approve",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={},
    )
    assert response.status_code == 401, response.text


def test_sap_panel_action_rejects_invalid_token(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-SAPFIORI-BADTOKEN"
    supplier_invoice = "5105600105"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/approve",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert response.status_code == 401, response.text


def test_sap_panel_action_rejects_cross_org_access(
    postgres_test_db, db, panel_client
):
    """Authentication boundary: a JWT for one org cannot be used to
    approve an AP item belonging to a different org. Without this,
    a leaked token would let a caller approve bills against any
    tenant.
    """
    ap_item_id = "AP-SAPFIORI-XORG"
    supplier_invoice = "5105600106"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, supplier_invoice=supplier_invoice,
    )
    # Mint a token for a different org.
    db.ensure_organization("other-org", organization_name="Other Org")
    token = _mint_panel_token(organization_id="other-org", user_id="other-user", email="other@example.com")

    response = panel_client.post(
        "/extension/ap-items/by-sap-invoice/approve",
        params={
            "company_code": _TEST_COMPANY_CODE,
            "supplier_invoice": supplier_invoice,
            "fiscal_year": _TEST_FISCAL_YEAR,
        },
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Either 404 (lookup scoped to caller's org, no item in that org) or
    # 403 (verify_org_access). Both are acceptable cross-tenant guards.
    assert response.status_code in (403, 404), response.text
