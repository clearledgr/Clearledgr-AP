from __future__ import annotations

import json
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


def test_field_review_resolution_endpoint_updates_canonical_record_and_clears_blocker(client, db):
    item = db.create_ap_item(
        _item_payload(
            "resolve-1",
            "default",
            amount=400.0,
            state="received",
            extra={
                "confidence": 0.84,
                "field_confidences": {"amount": 0.62, "vendor": 0.99, "invoice_number": 0.98, "due_date": 0.97},
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
                            "email_value": 400.0,
                            "attachment_value": 440.0,
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
                },
            },
        )
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/field-review/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "field": "amount",
            "source": "attachment",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["selected_source"] == "attachment"
    assert payload["selected_value"] == 440.0
    assert payload["requires_field_review"] is False
    assert payload["ap_item"]["amount"] == 440.0
    assert payload["ap_item"]["requires_field_review"] is False
    assert payload["ap_item"]["field_review_blockers"] == []

    stored = db.get_ap_item(item["id"])
    assert stored["amount"] == 440.0
    metadata = stored["metadata"] if isinstance(stored["metadata"], dict) else json.loads(stored["metadata"])
    assert metadata["requires_field_review"] is False
    assert metadata["field_provenance"]["amount"]["source"] == "attachment"
    assert metadata["field_review_resolutions"]["amount"]["selected_source"] == "attachment"
    assert metadata["source_conflicts"][0]["blocking"] is False
    assert metadata["confidence_blockers"] == []

    audit_events = db.list_ap_audit_events(item["id"])
    assert any(event["event_type"] == "field_correction" for event in audit_events)


def test_field_review_resolution_endpoint_auto_resumes_retry_path_when_last_blocker_clears(client, db, monkeypatch):
    item = db.create_ap_item(
        _item_payload(
            "resolve-resume-1",
            "default",
            amount=125.0,
            state="failed_post",
            extra={
                "field_confidences": {"amount": 0.51, "vendor": 0.99, "invoice_number": 0.99, "due_date": 0.99},
                "metadata": {
                    "requires_field_review": True,
                    "document_type": "invoice",
                    "source_conflicts": [
                        {
                            "field": "amount",
                            "blocking": True,
                            "reason": "source_value_mismatch",
                            "preferred_source": "email",
                            "values": {"email": 125.0, "attachment": 130.0},
                        }
                    ],
                    "field_evidence": {
                        "amount": {
                            "source": "email",
                            "selected_value": 125.0,
                            "email_value": 125.0,
                            "attachment_value": 130.0,
                        }
                    },
                    "confidence_blockers": [
                        {"field": "amount", "reason": "source_value_mismatch", "severity": "high"}
                    ],
                },
            },
        )
    )

    async def _fake_execute_intent(self, intent, input_payload=None, idempotency_key=None):
        assert intent == "retry_recoverable_failures"
        db.update_ap_item(item["id"], state="ready_to_post", _actor_type="system", _actor_id="test-runtime")
        return {"status": "ready_to_post", "reason": "resume_after_field_resolution"}

    monkeypatch.setattr(
        "clearledgr.api.ap_items.FinanceAgentRuntime.execute_intent",
        _fake_execute_intent,
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/field-review/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "field": "amount",
            "source": "email",
            "auto_resume": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved_and_resumed"
    assert payload["auto_resumed"] is True
    assert payload["auto_resume_result"]["status"] == "ready_to_post"
    assert payload["ap_item"]["state"] == "ready_to_post"


def test_bulk_field_review_resolution_endpoint_updates_multiple_items(client, db):
    first = db.create_ap_item(
        _item_payload(
            "bulk-resolve-1",
            "default",
            amount=100.0,
            state="received",
            extra={
                "metadata": {
                    "requires_field_review": True,
                    "source_conflicts": [
                        {
                            "field": "vendor",
                            "blocking": True,
                            "reason": "source_value_mismatch",
                            "preferred_source": "email",
                            "values": {"email": "Northwind", "attachment": "North Wind Ltd"},
                        }
                    ],
                    "field_evidence": {
                        "vendor": {
                            "source": "email",
                            "selected_value": "Northwind",
                            "email_value": "Northwind",
                            "attachment_value": "North Wind Ltd",
                        }
                    },
                    "confidence_blockers": [
                        {"field": "vendor", "reason": "source_value_mismatch", "severity": "high"}
                    ],
                },
            },
        )
    )
    second = db.create_ap_item(
        _item_payload(
            "bulk-resolve-2",
            "default",
            amount=200.0,
            state="received",
            extra={
                "metadata": {
                    "requires_field_review": True,
                    "source_conflicts": [
                        {
                            "field": "vendor",
                            "blocking": True,
                            "reason": "source_value_mismatch",
                            "preferred_source": "email",
                            "values": {"email": "Northwind", "attachment": "Northwind BV"},
                        }
                    ],
                    "field_evidence": {
                        "vendor": {
                            "source": "email",
                            "selected_value": "Northwind",
                            "email_value": "Northwind",
                            "attachment_value": "Northwind BV",
                        }
                    },
                    "confidence_blockers": [
                        {"field": "vendor", "reason": "source_value_mismatch", "severity": "high"}
                    ],
                },
            },
        )
    )

    response = client.post(
        "/api/ap/items/field-review/bulk-resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "ap_item_ids": [first["id"], second["id"]],
            "field": "vendor",
            "source": "email",
            "auto_resume": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["success_count"] == 2
    assert payload["failed_count"] == 0

    refreshed_first = db.get_ap_item(first["id"])
    refreshed_second = db.get_ap_item(second["id"])
    assert refreshed_first["vendor_name"] == "Northwind"
    assert refreshed_second["vendor_name"] == "Northwind"

    first_meta = refreshed_first["metadata"] if isinstance(refreshed_first["metadata"], dict) else json.loads(refreshed_first["metadata"])
    second_meta = refreshed_second["metadata"] if isinstance(refreshed_second["metadata"], dict) else json.loads(refreshed_second["metadata"])
    assert first_meta["field_review_resolutions"]["vendor"]["selected_source"] == "email"
    assert second_meta["field_review_resolutions"]["vendor"]["selected_source"] == "email"


def test_non_invoice_resolution_endpoint_closes_credit_note_with_reference(client, db):
    related_invoice = db.create_ap_item(
        _item_payload(
            "invoice-target-1",
            "default",
            state="ready_to_post",
            extra={
                "invoice_number": "INV-12345",
                "metadata": {
                    "document_type": "invoice",
                    "email_type": "invoice",
                },
            },
        )
    )
    item = db.create_ap_item(
        _item_payload(
            "credit-note-1",
            "default",
            state="received",
            extra={
                "invoice_number": "CN-001",
                "metadata": {
                    "document_type": "credit_note",
                    "email_type": "credit_note",
                },
            },
        )
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/non-invoice/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "outcome": "apply_to_invoice",
            "related_reference": "INV-12345",
            "close_record": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["document_type"] == "credit_note"
    assert payload["state"] == "received"
    assert payload["ap_item"]["state"] == "received"
    assert payload["ap_item"]["next_action"] == "none"
    assert payload["ap_item"]["non_invoice_review_required"] is False

    stored = db.get_ap_item(item["id"])
    metadata = stored["metadata"] if isinstance(stored["metadata"], dict) else json.loads(stored["metadata"])
    assert metadata["non_invoice_resolution"]["outcome"] == "apply_to_invoice"
    assert metadata["non_invoice_resolution"]["related_reference"] == "INV-12345"
    assert metadata["non_invoice_resolution"]["related_ap_item_id"] == related_invoice["id"]
    assert metadata["non_invoice_resolution"]["closed_record"] is True
    assert metadata["non_invoice_resolution"]["link_status"] == "linked"
    assert metadata["non_invoice_resolution"]["accounting_treatment"] == "vendor_credit_applied"
    assert metadata["non_invoice_resolution"]["downstream_queue"] == "vendor_credit_ledger"
    assert metadata["non_invoice_resolution"]["linked_record"]["id"] == related_invoice["id"]

    audit_events = db.list_ap_audit_events(item["id"])
    assert any(event["event_type"] == "non_invoice_review_resolved" for event in audit_events)

    related_stored = db.get_ap_item(related_invoice["id"])
    related_metadata = related_stored["metadata"] if isinstance(related_stored["metadata"], dict) else json.loads(related_stored["metadata"])
    assert related_metadata["linked_finance_summary"]["credit_note_count"] == 1
    assert related_metadata["linked_finance_summary"]["credit_note_total"] == 125.0
    assert related_metadata["vendor_credit_summary"]["applied_total"] == 125.0
    assert related_metadata["vendor_credit_summary"]["application_state"] == "fully_credited"
    assert related_metadata["finance_effect_summary"]["original_amount"] == 125.0
    assert related_metadata["finance_effect_summary"]["applied_credit_total"] == 125.0
    assert related_metadata["finance_effect_summary"]["remaining_payable_amount"] == 0.0
    assert related_metadata["finance_effect_summary"]["credit_application_state"] == "fully_credited"
    assert related_metadata["finance_effect_review_required"] is True
    assert "linked_credit_adjustment_present" in related_metadata["finance_effect_summary"]["blocked_reason_codes"]
    assert related_metadata["linked_finance_documents"][0]["source_ap_item_id"] == item["id"]
    normalized_related = build_worklist_item(db, related_stored)
    assert normalized_related["finance_effect_review_required"] is True
    assert normalized_related["next_action"] == "review_finance_effects"

    related_audit_events = db.list_ap_audit_events(related_invoice["id"])
    assert any(event["event_type"] == "credit_note_linked" for event in related_audit_events)


def test_non_invoice_resolution_endpoint_links_refund_to_related_payment_record(client, db):
    related_invoice = db.create_ap_item(
        _item_payload(
            "invoice-payment-target-1",
            "default",
            state="posted_to_erp",
            extra={
                "invoice_number": "PAY-APPLIED-9",
                "metadata": {
                    "document_type": "invoice",
                    "email_type": "invoice",
                },
            },
        )
    )
    item = db.create_ap_item(
        _item_payload(
            "refund-doc-1",
            "default",
            state="received",
            extra={
                "invoice_number": "RF-001",
                "metadata": {
                    "document_type": "refund",
                    "email_type": "refund",
                },
            },
        )
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/non-invoice/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "outcome": "link_to_payment",
            "related_reference": "PAY-APPLIED-9",
            "close_record": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "refund"
    assert payload["ap_item"]["linked_record"]["id"] == related_invoice["id"]

    stored = db.get_ap_item(item["id"])
    metadata = stored["metadata"] if isinstance(stored["metadata"], dict) else json.loads(stored["metadata"])
    assert metadata["non_invoice_resolution"]["accounting_treatment"] == "vendor_refund_linked"
    assert metadata["non_invoice_resolution"]["related_ap_item_id"] == related_invoice["id"]

    related_stored = db.get_ap_item(related_invoice["id"])
    related_metadata = related_stored["metadata"] if isinstance(related_stored["metadata"], dict) else json.loads(related_stored["metadata"])
    assert related_metadata["linked_finance_summary"]["refund_count"] == 1
    assert related_metadata["linked_finance_summary"]["refund_total"] == 125.0
    assert related_metadata["cash_application_summary"]["refund_total"] == 125.0
    assert related_metadata["finance_effect_summary"]["refund_total"] == 125.0
    assert related_metadata["finance_effect_summary"]["settlement_state"] == "refund_mismatch"
    assert related_metadata["finance_effect_summary"]["remaining_balance_amount"] == 125.0
    assert "linked_refund_exceeds_cash_out" in related_metadata["finance_effect_summary"]["blocked_reason_codes"]

    related_audit_events = db.list_ap_audit_events(related_invoice["id"])
    assert any(event["event_type"] == "refund_linked" for event in related_audit_events)


def test_non_invoice_resolution_endpoint_records_payment_confirmation(client, db):
    item = db.create_ap_item(
        _item_payload(
            "payment-doc-1",
            "default",
            state="received",
            extra={
                "invoice_number": "PAY-001",
                "metadata": {
                    "document_type": "payment_confirmation",
                    "email_type": "payment_confirmation",
                },
            },
        )
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/non-invoice/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "outcome": "record_payment_confirmation",
            "close_record": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "payment"
    assert payload["ap_item"]["document_type"] == "payment"
    assert payload["ap_item"]["next_action"] == "none"
    assert payload["ap_item"]["non_invoice_review_required"] is False

    stored = db.get_ap_item(item["id"])
    metadata = stored["metadata"] if isinstance(stored["metadata"], dict) else json.loads(stored["metadata"])
    assert metadata["non_invoice_resolution"]["outcome"] == "record_payment_confirmation"
    assert metadata["non_invoice_resolution"]["accounting_treatment"] == "payment_confirmation_recorded"
    assert metadata["non_invoice_resolution"]["downstream_queue"] == "cash_disbursements"


def test_non_invoice_resolution_endpoint_sends_bank_statement_to_reconciliation(client, db):
    item = db.create_ap_item(
        _item_payload(
            "statement-doc-1",
            "default",
            state="received",
            extra={
                "invoice_number": "STMT-001",
                "metadata": {
                    "document_type": "bank_statement",
                    "email_type": "bank_statement",
                },
            },
        )
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/non-invoice/resolve?organization_id=default",
        headers=_auth_headers("default"),
        json={
            "outcome": "send_to_reconciliation",
            "close_record": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "statement"
    assert payload["ap_item"]["document_type"] == "statement"
    assert payload["ap_item"]["next_action"] == "none"
    assert payload["ap_item"]["non_invoice_review_required"] is False

    stored = db.get_ap_item(item["id"])
    metadata = stored["metadata"] if isinstance(stored["metadata"], dict) else json.loads(stored["metadata"])
    assert metadata["non_invoice_resolution"]["outcome"] == "send_to_reconciliation"
    assert metadata["non_invoice_resolution"]["accounting_treatment"] == "queued_for_reconciliation"
    assert metadata["non_invoice_resolution"]["downstream_queue"] == "reconciliation"
    assert metadata["non_invoice_resolution"]["reconciliation_session_id"]
    assert metadata["non_invoice_resolution"]["reconciliation_item_id"]
    session = db.get_recon_session(metadata["non_invoice_resolution"]["reconciliation_session_id"])
    assert session["source_type"] == "gmail_statement"
    recon_items = db.list_recon_items(session["id"])
    assert len(recon_items) == 1
    assert recon_items[0]["id"] == metadata["non_invoice_resolution"]["reconciliation_item_id"]
    assert recon_items[0]["state"] == "review"


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
