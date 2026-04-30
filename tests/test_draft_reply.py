"""Phase 3.3 — vendor draft-reply synthesis.

Two layers under test:
  1. ``synthesize_reply_for_item(item, ...)`` — the deterministic
     mapping from AP-item exception state → template choice → rendered
     subject/body. Pure function, no DB / no HTTP.
  2. ``POST /extension/draft-reply`` — the FastAPI endpoint the
     extension hits when the user clicks "Suggest reply" on the
     thread-top exception banner. Resolves the item, looks up the
     org's company name, and returns the same shape the synthesizer
     produces.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from main import app
from clearledgr.core import database as db_module
from clearledgr.core.auth import create_access_token
from clearledgr.services.draft_reply import synthesize_reply_for_item


# ---------------------------------------------------------------------------
# Layer 1 — synthesize_reply_for_item (pure)
# ---------------------------------------------------------------------------


def test_synthesizer_uses_missing_po_template_when_exception_code_matches():
    item = {
        "exception_code": "po_missing",
        "vendor_name": "Acme Supplies",
        "invoice_number": "INV-123",
        "currency": "USD",
        "amount": "500.00",
        "sender": "ap@acme.example",
        "subject": "Invoice INV-123 attached",
    }
    out = synthesize_reply_for_item(item, company_name="Northwind")
    assert out["template_id"] == "missing_po"
    assert out["source"] == "template"
    # Subject reuses the original with "Re:" prefix and the template
    # tail — the user can read at a glance what's being asked.
    assert out["subject"].startswith("Re:")
    assert "Purchase Order Number Required" in out["subject"]
    # Body must mention the invoice + amount + vendor's company sign-off.
    assert "INV-123" in out["body"]
    assert "USD 500.00" in out["body"]
    assert "Northwind" in out["body"]
    # Reply targets the original sender so the user just clicks Send.
    assert out["to"] == "ap@acme.example"


def test_synthesizer_falls_back_to_blocker_field_name_when_exception_code_is_blank():
    # No exception_code set, but field_review_blockers points at
    # "po_number" — the synthesizer should still pick the missing_po
    # template via the blocker-field map.
    item = {
        "vendor_name": "Acme",
        "invoice_number": "INV-9",
        "currency": "EUR",
        "amount": "100.00",
        "sender": "billing@acme.example",
        "field_review_blockers": [
            {"field_name": "po_number", "reason": "missing"},
        ],
    }
    out = synthesize_reply_for_item(item, company_name="Customer Co")
    assert out["template_id"] == "missing_po"


def test_synthesizer_general_inquiry_question_lists_field_review_blockers():
    # When no specific template fits, general_inquiry's {question}
    # placeholder must read concretely — listing the actual blockers,
    # not a vague "we need more info".
    item = {
        "vendor_name": "Acme",
        "invoice_number": "INV-7",
        "currency": "USD",
        "amount": "75",
        "sender": "ap@acme.example",
        "field_review_blockers": [
            {"field_name": "tax_rate", "reason": "ambiguous"},
            {"field_name": "ship_to", "reason": "not specified"},
        ],
    }
    out = synthesize_reply_for_item(item, company_name="Customer Co")
    assert out["template_id"] == "general_inquiry"
    # Both blocker fields surface in the rendered body's question line.
    assert "tax rate" in out["body"]
    assert "ship to" in out["body"]
    # Reasons humanise too.
    assert "ambiguous" in out["body"]


def test_synthesizer_uses_general_inquiry_with_safe_fallback_when_no_signals():
    # No exception_code, no blockers — still produce a usable draft so
    # the extension's "Suggest reply" button never fails the user.
    item = {
        "vendor_name": "Acme",
        "invoice_number": "INV-1",
        "currency": "USD",
        "amount": "10",
        "sender": "ap@acme.example",
    }
    out = synthesize_reply_for_item(item, company_name="Customer Co")
    assert out["template_id"] == "general_inquiry"
    assert "Customer Co" in out["body"]
    # The safe fallback question must produce a non-empty, sendable body.
    assert "Could you please" in out["body"]


def test_synthesizer_bank_details_template_for_iban_change_signals():
    # Both the exception-code path and the blocker-field path map to
    # bank_details_verification — pin both to lock the contract.
    item_code = {
        "exception_code": "iban_change_pending",
        "vendor_name": "Acme",
        "invoice_number": "INV-2",
        "currency": "USD",
        "amount": "200",
        "sender": "ap@acme.example",
    }
    out_code = synthesize_reply_for_item(item_code, company_name="Customer Co")
    assert out_code["template_id"] == "bank_details_verification"

    item_blocker = {
        "vendor_name": "Acme",
        "invoice_number": "INV-3",
        "currency": "USD",
        "amount": "200",
        "sender": "ap@acme.example",
        "field_review_blockers": [{"field_name": "iban", "reason": "changed"}],
    }
    out_blocker = synthesize_reply_for_item(item_blocker, company_name="Customer Co")
    assert out_blocker["template_id"] == "bank_details_verification"


# ---------------------------------------------------------------------------
# Layer 2 — POST /extension/draft-reply
# ---------------------------------------------------------------------------


def _jwt_for(org_id: str, user_id: str = "draft-user", role: str = "operator") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.example",
        organization_id=org_id,
        role=role,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str, user_id: str = "draft-user", role: str = "operator") -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id, user_id, role)}"}


@pytest.fixture()
def db():
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    return TestClient(app)


def _seed_ap_item(db, org_id: str, *, item_id: str, exception_code: str | None = None) -> dict:
    payload = {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice {item_id} from Acme",
        "sender": "ap@acme.example",
        "vendor_name": "Acme Supplies",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": item_id,
        "state": "needs_info",
        "organization_id": org_id,
    }
    if exception_code:
        payload["exception_code"] = exception_code
    db.create_ap_item(payload)
    return payload


@pytest.mark.skip(


    reason=(


        "vendor_followup_dormant_2026_04_30 "


        "— see memory/project_vendor_followup_templates_dormant.md"


    ),


)


def test_draft_reply_endpoint_returns_template_payload_for_known_exception(client, db):
    db.create_organization("draft-org-1", "Northwind", settings={"company_name": "Northwind Trading"})
    _seed_ap_item(db, "draft-org-1", item_id="DR-PO-1", exception_code="po_missing")

    response = client.post(
        "/extension/draft-reply",
        headers=_auth_headers("draft-org-1"),
        json={"ap_item_id": "DR-PO-1"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["template_id"] == "missing_po"
    assert payload["source"] == "template"
    assert payload["to"] == "ap@acme.example"
    assert "Northwind Trading" in payload["body"]
    assert "DR-PO-1" in payload["body"]


@pytest.mark.skip(


    reason=(


        "vendor_followup_dormant_2026_04_30 "


        "— see memory/project_vendor_followup_templates_dormant.md"


    ),


)


def test_draft_reply_endpoint_resolves_by_thread_id(client, db):
    db.create_organization("draft-org-2", "Customer Co", settings={})
    _seed_ap_item(db, "draft-org-2", item_id="DR-TH-1", exception_code="missing_amount")

    response = client.post(
        "/extension/draft-reply",
        headers=_auth_headers("draft-org-2"),
        json={"thread_id": "thread-DR-TH-1"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["template_id"] == "missing_amount"
    # The org has no settings.company_name, so we fall through to the
    # organization name itself — pin that fallback so it can't silently
    # drop to the literal "Accounts Payable" default.
    assert "Customer Co" in payload["body"]


@pytest.mark.skip(


    reason=(


        "vendor_followup_dormant_2026_04_30 "


        "— see memory/project_vendor_followup_templates_dormant.md"


    ),


)


def test_draft_reply_endpoint_404s_on_missing_item(client, db):
    response = client.post(
        "/extension/draft-reply",
        headers=_auth_headers("draft-org-3"),
        json={"ap_item_id": "DOES-NOT-EXIST"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "ap_item_not_found"


@pytest.mark.skip(


    reason=(


        "vendor_followup_dormant_2026_04_30 "


        "— see memory/project_vendor_followup_templates_dormant.md"


    ),


)


def test_draft_reply_endpoint_blocks_cross_org_lookup(client, db):
    db.create_organization("draft-org-A", "Org A", settings={})
    db.create_organization("draft-org-B", "Org B", settings={})
    _seed_ap_item(db, "draft-org-A", item_id="DR-XORG-1", exception_code="po_missing")

    # User from org-B authenticates and tries to draft a reply for
    # org-A's item by id. Org isolation must reject with 404 (not 403,
    # to avoid leaking that the id exists).
    response = client.post(
        "/extension/draft-reply",
        headers=_auth_headers("draft-org-B"),
        json={"ap_item_id": "DR-XORG-1"},
    )
    assert response.status_code == 404
