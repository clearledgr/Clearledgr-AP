"""Phase 3 — NetSuite SuiteApp ↔ Phase 1 audit-trail integration.

The Vendor Bill panel inside NetSuite calls back to Solden via the
new ``POST /extension/ap-items/by-netsuite-bill/{ns_internal_id}/<action>``
endpoints. Each endpoint dispatches the matching runtime intent
(``approve_invoice`` / ``reject_invoice`` / ``request_info``) with
``source_channel="erp_native_netsuite"`` so Phase 1's
``decision_context`` auto-build records ``ui_surface =
"erp_native_netsuite"`` on the resulting state_transition audit row.

These tests prove that compose end-to-end through the FastAPI layer:
JWT-equivalent dev-token auth → org resolution via account_id → AP
item lookup by erp_reference → runtime dispatch → workflow state
transition → audit row with the expected decision_context shape.

The contract proven here is the same contract Phase 2 verified for
Teams, but exercised through a different surface: the NetSuite-side
UI calling the Solden API rather than the runtime layer directly.
A bug in the endpoint's source_channel value, the panel's auth
resolution, or the Phase 2 propagation fix would each fail one of
these tests. They exist to keep the audit chain provably correct
across every render-target surface.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from solden.api.netsuite_panel import router as netsuite_panel_router
from solden.core.database import get_db


_TEST_DEV_TOKEN = "test-netsuite-panel-dev-token-9f3c8b"
_TEST_ACCOUNT_ID = "TSTACCT123"
_TEST_ORG_ID = "ns-panel-test-org"


@pytest.fixture()
def db():
    inst = get_db()
    inst.initialize()
    inst.ensure_organization(_TEST_ORG_ID, organization_name="NetSuite Panel Test Org")
    inst.save_erp_connection(
        organization_id=_TEST_ORG_ID,
        erp_type="netsuite",
        credentials={"account_id": _TEST_ACCOUNT_ID, "webhook_secret": "unused-in-dev-token-path"},
    )
    return inst


@pytest.fixture()
def panel_client(monkeypatch):
    """FastAPI TestClient with the NetSuite panel router mounted and
    the dev-token auth path enabled. Avoids the JWT-signing dance —
    the dev token is exactly the contract that ships in Phase 1-2 and
    is the simplest way to exercise the dispatch path end-to-end.
    """
    monkeypatch.setenv("NETSUITE_PANEL_DEV_TOKEN", _TEST_DEV_TOKEN)
    app = FastAPI()
    app.include_router(netsuite_panel_router)
    return TestClient(app)


def _seed_ap_item_at_needs_approval(
    db,
    *,
    ap_item_id: str,
    ns_internal_id: str,
    organization_id: str = _TEST_ORG_ID,
) -> Dict[str, Any]:
    """Seed an AP item with the metadata Phase 1 reads for the auto-
    built decision_context snapshot, and walk it to ``needs_approval``
    via the canonical update_ap_item path so the state transitions
    that ship before the test runs already have correct attribution.

    ``erp_reference`` is the linkage point — the panel's read +
    action endpoints look up by ``ap_items.erp_reference == ns_internal_id``.
    """
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
            "invoice_count": 18,
            "default_currency": "USD",
        },
        "fraud_flags": ["low_risk"],
        "reasoning_risks": ["amount_within_normal_range"],
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
    }
    payload = {
        "id": ap_item_id,
        "invoice_key": f"AcmeNS::{ns_internal_id}",
        "thread_id": f"netsuite-bill:{ns_internal_id}",
        "vendor_name": "Acme NetSuite",
        "amount": 1500.0,
        "currency": "USD",
        "invoice_number": f"NS-{ns_internal_id}",
        "subject": f"NetSuite Bill {ns_internal_id} — Acme NetSuite",
        "sender": "<netsuite@erp-native>",
        "state": "received",
        "approval_policy_version": "v2",
        "confidence": 0.95,
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
        "organization_id": organization_id,
        "erp_reference": ns_internal_id,
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


def test_netsuite_panel_approve_lands_ui_surface_erp_native_netsuite(
    postgres_test_db, db, panel_client
):
    """POST /approve from inside the NetSuite Vendor Bill panel produces
    a state_transition audit row with ui_surface="erp_native_netsuite"
    and the auto-built decision_context snapshot from the AP item's
    metadata.
    """
    ap_item_id = "AP-NSPANEL-APPROVE"
    ns_internal_id = "9001"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, ns_internal_id=ns_internal_id,
    )

    response = panel_client.post(
        f"/extension/ap-items/by-netsuite-bill/{ns_internal_id}/approve",
        params={"account_id": _TEST_ACCOUNT_ID},
        json={"reason": "approved_in_netsuite_vendor_bill_panel"},
        headers={"Authorization": f"Bearer {_TEST_DEV_TOKEN}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "approve_invoice"
    assert body["ns_internal_id"] == ns_internal_id
    assert body["ap_item_id"] == ap_item_id

    audits = _state_transition_audits(db, ap_item_id)
    netsuite_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_netsuite"
    ]
    assert netsuite_transitions, (
        "No state_transition audit row carries ui_surface='erp_native_netsuite'. "
        f"Audit rows: {[(a.get('prev_state'), a.get('new_state'), _payload_decision_context(a).get('ui_surface')) for a in audits]}"
    )

    # The most recent NetSuite-driven transition should carry the
    # auto-built snapshot fields derived from the AP item's metadata.
    decision_context = _payload_decision_context(netsuite_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_netsuite"
    assert decision_context.get("actor_type") == "user"
    # Dev-token auth resolves to a synthetic ``netsuite-panel-dev@<account>``
    # email so the audit row records *some* identity rather than
    # falling back to "system".
    actor_id = decision_context.get("actor_id") or ""
    assert "netsuite" in actor_id.lower(), f"unexpected actor_id={actor_id!r}"
    # Auto-built fields from the AP item's metadata.
    assert decision_context.get("agent_recommendation") == "approve"
    assert decision_context.get("validation_gate_at_decision", {}).get("passed") is True
    assert decision_context.get("vendor_profile_snapshot", {}).get("trusted") is True
    assert "low_risk" in decision_context.get("risk_flags_shown", [])
    assert decision_context.get("policy_version") == "v2"
    assert "snapshotted_at" in decision_context


def test_netsuite_panel_reject_lands_ui_surface_erp_native_netsuite(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-NSPANEL-REJECT"
    ns_internal_id = "9002"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, ns_internal_id=ns_internal_id,
    )

    response = panel_client.post(
        f"/extension/ap-items/by-netsuite-bill/{ns_internal_id}/reject",
        params={"account_id": _TEST_ACCOUNT_ID},
        json={"reason": "duplicate_supplier_invoice"},
        headers={"Authorization": f"Bearer {_TEST_DEV_TOKEN}"},
    )
    assert response.status_code == 200, response.text

    audits = _state_transition_audits(db, ap_item_id)
    netsuite_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_netsuite"
    ]
    assert netsuite_transitions, (
        "Reject from NetSuite panel did not land ui_surface='erp_native_netsuite'."
    )
    decision_context = _payload_decision_context(netsuite_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_netsuite"
    assert decision_context.get("actor_type") == "user"


def test_netsuite_panel_request_info_lands_ui_surface_erp_native_netsuite(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-NSPANEL-RFI"
    ns_internal_id = "9003"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, ns_internal_id=ns_internal_id,
    )

    response = panel_client.post(
        f"/extension/ap-items/by-netsuite-bill/{ns_internal_id}/request-info",
        params={"account_id": _TEST_ACCOUNT_ID},
        json={"reason": "missing_purchase_order_match"},
        headers={"Authorization": f"Bearer {_TEST_DEV_TOKEN}"},
    )
    assert response.status_code == 200, response.text

    audits = _state_transition_audits(db, ap_item_id)
    netsuite_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "erp_native_netsuite"
    ]
    assert netsuite_transitions, (
        "request_info from NetSuite panel did not land ui_surface='erp_native_netsuite'."
    )
    decision_context = _payload_decision_context(netsuite_transitions[0])
    assert decision_context.get("ui_surface") == "erp_native_netsuite"


def test_netsuite_panel_action_rejects_unknown_account_id(
    postgres_test_db, db, panel_client
):
    """Authentication boundary: an account_id that doesn't map to any
    active NetSuite ERP connection is rejected with 401 even if the
    dev token is correct. Without this, a leaked dev token would let
    a caller approve bills against any tenant.
    """
    ap_item_id = "AP-NSPANEL-AUTH"
    ns_internal_id = "9004"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, ns_internal_id=ns_internal_id,
    )

    response = panel_client.post(
        f"/extension/ap-items/by-netsuite-bill/{ns_internal_id}/approve",
        params={"account_id": "WRONG_ACCOUNT_ID"},
        json={},
        headers={"Authorization": f"Bearer {_TEST_DEV_TOKEN}"},
    )
    assert response.status_code == 401, response.text


def test_netsuite_panel_action_rejects_missing_bearer_token(
    postgres_test_db, db, panel_client
):
    ap_item_id = "AP-NSPANEL-NOTOKEN"
    ns_internal_id = "9005"
    _seed_ap_item_at_needs_approval(
        db, ap_item_id=ap_item_id, ns_internal_id=ns_internal_id,
    )
    response = panel_client.post(
        f"/extension/ap-items/by-netsuite-bill/{ns_internal_id}/approve",
        params={"account_id": _TEST_ACCOUNT_ID},
        json={},
    )
    assert response.status_code == 401, response.text
