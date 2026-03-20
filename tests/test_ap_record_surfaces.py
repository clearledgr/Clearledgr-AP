from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _apply_runtime_surface_profile, app  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import create_access_token  # noqa: E402
from clearledgr.api.ap_items import build_worklist_item  # noqa: E402


def _item_payload(
    item_id: str,
    org_id: str,
    *,
    vendor_name: str = "Acme",
    invoice_number: str | None = None,
    state: str = "needs_approval",
    amount: float = 125.0,
    metadata: dict | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice {item_id}",
        "sender": f"ap@{vendor_name.lower().replace(' ', '')}.example",
        "vendor_name": vendor_name,
        "amount": amount,
        "currency": "USD",
        "invoice_number": invoice_number or f"INV-{item_id}",
        "state": state,
        "confidence": 0.97,
        "organization_id": org_id,
        "metadata": metadata or {},
    }
    if extra:
        payload.update(extra)
    return payload


def _jwt_for(org_id: str, user_id: str = "user-test", role: str = "operator") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.example",
        organization_id=org_id,
        role=role,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str, user_id: str = "user-test", role: str = "operator") -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id, user_id, role)}"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ap-record-surfaces.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "true")
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    _apply_runtime_surface_profile()
    db_module._DB_INSTANCE = None
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    return TestClient(app)


def test_upcoming_and_vendor_directory_endpoints_are_org_scoped(client, db):
    db.create_ap_item(
        _item_payload(
            "alpha-approval",
            "org-alpha",
            vendor_name="Northwind",
            state="needs_approval",
            extra={"approval_requested_at": "2026-03-17T08:00:00+00:00"},
        )
    )
    db.create_ap_item(
        _item_payload(
            "alpha-info",
            "org-alpha",
            vendor_name="Blue Supply",
            state="needs_info",
            metadata={
                "followup_sla_due_at": "2026-03-18T09:00:00+00:00",
                "followup_next_action": "prepare_vendor_followup_draft",
            },
        )
    )
    db.create_ap_item(
        _item_payload(
            "beta-post",
            "org-beta",
            vendor_name="Outside Org",
            state="ready_to_post",
        )
    )

    db.upsert_vendor_profile(
        "org-alpha",
        "Northwind",
        requires_po=True,
        payment_terms="Net 30",
        anomaly_flags=["bank_change_recent"],
    )

    upcoming = client.get(
        "/api/ap/items/upcoming?organization_id=org-alpha&limit=10",
        headers=_auth_headers("org-alpha"),
    )
    assert upcoming.status_code == 200
    upcoming_payload = upcoming.json()
    assert upcoming_payload["summary"]["total"] == 2
    assert {task["kind"] for task in upcoming_payload["tasks"]} == {
        "approval_follow_up",
        "vendor_follow_up",
    }
    assert all(task["ap_item_id"].startswith("alpha-") for task in upcoming_payload["tasks"])

    vendors = client.get(
        "/api/ap/items/vendors?organization_id=org-alpha&limit=20",
        headers=_auth_headers("org-alpha"),
    )
    assert vendors.status_code == 200
    vendor_rows = vendors.json()["vendors"]
    assert {row["vendor_name"] for row in vendor_rows} == {"Northwind", "Blue Supply"}
    northwind = next(row for row in vendor_rows if row["vendor_name"] == "Northwind")
    assert northwind["open_count"] == 1
    assert northwind["profile"]["requires_po"] is True
    assert northwind["profile"]["payment_terms"] == "Net 30"


def test_build_worklist_item_surfaces_attachment_metadata_from_sources(db):
    item = db.create_ap_item(
        _item_payload(
            "attachment-1",
            "default",
            extra={"attachment_url": "https://files.example/invoice.pdf"},
        )
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "gmail_message",
            "source_ref": "msg-attachment-1",
            "subject": "Invoice attachment",
            "sender": "billing@example.com",
            "metadata": {
                "has_attachment": True,
                "attachment_count": 2,
                "attachment_names": ["invoice.pdf", "backup.pdf"],
            },
        }
    )

    normalized = build_worklist_item(db, item)

    assert normalized["has_attachment"] is True
    assert normalized["attachment_count"] == 2
    assert normalized["attachment_url"] == "https://files.example/invoice.pdf"
    assert normalized["attachment_names"] == ["invoice.pdf", "backup.pdf"]


def test_build_worklist_item_recovers_google_invoice_attachment_signal_for_legacy_rows(db):
    item = db.create_ap_item(
        _item_payload(
            "google-attachment-1",
            "default",
            vendor_name="Google Payments",
            extra={
                "subject": "Google Workspace: Your invoice is available for clearledgr.com",
                "sender": "Google Payments <payments-noreply@google.com>",
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["has_attachment"] is True
    assert normalized["attachment_count"] == 1


def test_build_worklist_item_surfaces_extraction_conflicts_and_provenance(db):
    item = db.create_ap_item(
        _item_payload(
            "conflict-1",
            "default",
            extra={
                "exception_code": "field_conflict",
                "exception_severity": "high",
                "metadata": {
                    "requires_field_review": True,
                    "requires_extraction_review": True,
                    "field_provenance": {
                        "amount": {
                            "source": "attachment",
                            "value": 440.0,
                            "candidates": {"email": 400.0, "attachment": 440.0},
                        }
                    },
                    "field_evidence": {
                        "amount": {
                            "source": "attachment",
                            "selected_value": 440.0,
                            "attachment_name": "invoice.pdf",
                        }
                    },
                    "source_conflicts": [
                        {
                            "field": "amount",
                            "blocking": True,
                            "reason": "source_value_mismatch",
                            "preferred_source": "attachment",
                            "values": {"email": 400.0, "attachment": 440.0},
                        }
                    ],
                    "confidence_blockers": [
                        {"field": "amount", "reason": "source_value_mismatch", "severity": "high"}
                    ],
                    "conflict_actions": [
                        {"action": "review_fields", "field": "amount", "blocking": True}
                    ],
                },
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["requires_field_review"] is True
    assert normalized["requires_extraction_review"] is True
    assert normalized["exception_code"] == "field_conflict"
    assert normalized["field_provenance"]["amount"]["source"] == "attachment"
    assert normalized["source_conflicts"][0]["field"] == "amount"
    assert normalized["conflict_actions"][0]["action"] == "review_fields"
    assert normalized["blocked_fields"] == ["amount"]
    assert normalized["workflow_paused_reason"] == (
        "Workflow paused until amount is confirmed because the email and attachment disagree."
    )
    assert normalized["field_review_blockers"][0]["field_label"] == "Amount"
    assert normalized["field_review_blockers"][0]["email_value_display"] == "USD 400.00"
    assert normalized["field_review_blockers"][0]["attachment_value_display"] == "USD 440.00"
    assert normalized["field_review_blockers"][0]["winning_source_label"] == "Attachment"


def test_vendor_record_endpoint_returns_shared_vendor_context(client, db):
    db.create_ap_item(
        _item_payload(
            "vend-1",
            "default",
            vendor_name="Acme",
            state="ready_to_post",
            amount=400.0,
        )
    )
    db.create_ap_item(
        _item_payload(
            "vend-2",
            "default",
            vendor_name="Acme",
            state="posted_to_erp",
            amount=650.0,
            extra={"erp_reference": "ERP-22"},
        )
    )
    db.upsert_vendor_profile(
        "default",
        "Acme",
        requires_po=True,
        payment_terms="Net 15",
        anomaly_flags=["duplicate_sender_domain"],
        vendor_aliases=["Acme Corp"],
    )
    db.record_vendor_invoice(
        "default",
        "Acme",
        "vend-hist-1",
        invoice_number="INV-HIST-1",
        amount=320.0,
        final_state="posted_to_erp",
        was_approved=True,
    )

    response = client.get(
        "/api/ap/items/vendors/Acme?organization_id=default",
        headers=_auth_headers("default"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["vendor_name"] == "Acme"
    assert payload["summary"]["invoice_count"] == 2
    assert payload["summary"]["posted_count"] == 1
    assert bool(payload["profile"]["requires_po"]) is True
    assert payload["profile"]["payment_terms"] == "Net 15"
    assert "duplicate_sender_domain" in payload["profile"]["anomaly_flags"]
    assert any(item["id"] == "vend-1" for item in payload["recent_items"])
    assert payload["history"][0]["invoice_number"] == "INV-HIST-1"


def test_context_endpoint_includes_related_records_and_source_groups(client, db):
    previous = db.create_ap_item(
        _item_payload(
            "ctx-prev",
            "default",
            vendor_name="Acme",
            invoice_number="INV-OLD-1",
            state="rejected",
        )
    )
    current = db.create_ap_item(
        _item_payload(
            "ctx-current",
            "default",
            vendor_name="Acme",
            invoice_number="INV-42",
            state="needs_info",
            metadata={"supersedes_ap_item_id": previous["id"]},
        )
    )
    duplicate = db.create_ap_item(
        _item_payload(
            "ctx-dup",
            "default",
            vendor_name="Other Vendor",
            invoice_number="INV-42",
            state="validated",
        )
    )
    vendor_recent = db.create_ap_item(
        _item_payload(
            "ctx-vendor",
            "default",
            vendor_name="Acme",
            invoice_number="INV-77",
            state="ready_to_post",
        )
    )

    db.link_ap_item_source(
        {
            "ap_item_id": current["id"],
            "source_type": "email",
            "source_ref": "gmail-thread-1",
            "subject": "Invoice email",
            "sender": "billing@acme.example",
            "metadata": {"kind": "gmail_thread"},
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": current["id"],
            "source_type": "procurement",
            "source_ref": "po-7788",
            "subject": "PO 7788",
            "sender": "procurement",
            "metadata": {"kind": "po_match"},
        }
    )

    response = client.get(
        f"/api/ap/items/{current['id']}/context?refresh=true",
        headers=_auth_headers("default"),
    )
    assert response.status_code == 200
    payload = response.json()
    related = payload["related_records"]
    source_groups = payload["email"]["source_groups"]

    assert any(item["id"] == duplicate["id"] for item in related["same_invoice_number_items"])
    assert any(item["id"] == vendor_recent["id"] for item in related["vendor_recent_items"])
    assert related["supersession"]["previous_item"]["id"] == previous["id"]
    assert source_groups["count"] == 2
    assert {group["source_type"] for group in source_groups["groups"]} == {"email", "procurement"}
