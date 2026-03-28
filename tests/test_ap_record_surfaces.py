from __future__ import annotations

import json
import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _apply_runtime_surface_profile, _is_strict_profile_allowed_path, app  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import create_access_token  # noqa: E402
from clearledgr.api.ap_items import (  # noqa: E402
    ResolveEntityRouteRequest,
    build_worklist_item,
    resolve_ap_item_entity_route,
)
from clearledgr.services.correction_learning import CorrectionLearningService  # noqa: E402


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


def test_detail_endpoint_returns_canonical_ap_item(client, db):
    item = db.create_ap_item(
        _item_payload(
            "detail-alpha",
            "default",
            vendor_name="Google Payments",
            invoice_number="5499678906",
            state="received",
        )
    )

    response = client.get(
        f"/api/ap/items/{item['id']}?organization_id=default",
        headers=_auth_headers("default"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == item["id"]
    assert payload["invoice_number"] == "5499678906"
    assert payload["vendor_name"] == "Google Payments"


def test_detail_endpoint_resolves_invoice_number_alias(client, db):
    item = db.create_ap_item(
        _item_payload(
            "detail-beta",
            "default",
            vendor_name="Google Payments",
            invoice_number="INV-GOOGLE-2026-02",
            state="received",
        )
    )

    response = client.get(
        "/api/ap/items/INV-GOOGLE-2026-02?organization_id=default",
        headers=_auth_headers("default"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == item["id"]
    assert payload["invoice_number"] == "INV-GOOGLE-2026-02"


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
    assert normalized["field_review_blockers"][0]["paused_reason"] == (
        "Workflow paused until amount is confirmed because the email and attachment disagree."
    )
    assert normalized["field_review_blockers"][0]["field_label"] == "Amount"
    assert normalized["field_review_blockers"][0]["email_value_display"] == "USD 400.00"
    assert normalized["field_review_blockers"][0]["attachment_value_display"] == "USD 440.00"
    assert normalized["field_review_blockers"][0]["winning_source_label"] == "Invoice attachment"
    assert normalized["pipeline_blockers"][0]["kind"] == "confidence"
    assert normalized["pipeline_blockers"][0]["type"] == "source_conflict"
    assert normalized["pipeline_blockers"][0]["chip_label"] == "Field review"
    assert normalized["pipeline_blockers"][0]["title"] == "Amount blocked"
    assert normalized["pipeline_blockers"][0]["detail"] == "Email USD 400.00 · Attachment USD 440.00"


def test_build_worklist_item_surfaces_specific_failed_post_connector_reason(db):
    item = db.create_ap_item(
        _item_payload(
            "failed-post-no-erp",
            "default",
            state="failed_post",
            extra={
                "last_error": "No ERP connected for organization",
                "exception_code": "erp_not_connected",
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["workflow_paused_reason"] == "Connect an ERP before this invoice can be posted."
    assert normalized["exception_code"] == "erp_not_connected"
    assert normalized["erp_status"] == "failed"


def test_build_worklist_item_surfaces_confidence_threshold_in_pipeline_blockers(db):
    item = db.create_ap_item(
        _item_payload(
            "confidence-review-1",
            "default",
            state="received",
            extra={"due_date": "2026-04-01",
                "confidence": 0.91,
                "field_confidences": {
                    "vendor": 0.94,
                    "amount": 0.95,
                    "invoice_number": 0.94,
                    "due_date": 0.89,
                },
                "metadata": {
                    "requires_field_review": True,
                    "confidence_blockers": [
                        {"field": "vendor", "reason": "critical_field_low_confidence"},
                        {"field": "invoice_number", "reason": "critical_field_low_confidence"},
                        {"field": "due_date", "reason": "critical_field_low_confidence"},
                    ],
                },
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert [row["type"] for row in normalized["pipeline_blockers"][:3]] == [
        "confidence_review",
        "confidence_review",
        "confidence_review",
    ]
    assert normalized["pipeline_blockers"][0]["title"] == "Vendor needs review"
    assert normalized["pipeline_blockers"][0]["detail"] == (
        "Vendor confidence is 94%, below the 95% review threshold."
    )
    assert normalized["pipeline_blockers"][2]["title"] == "Due date needs review"
    assert normalized["pipeline_blockers"][2]["detail"] == (
        "Due date confidence is 89%, below the 95% review threshold."
    )


def test_build_worklist_item_recalibrates_google_sender_confidence_gate(db):
    item = db.create_ap_item(
        _item_payload(
            "google-calibration-1",
            "default",
            vendor_name="Google Cloud EMEA Limited",
            state="received",
            extra={
                "sender": "Google Payments <payments-noreply@google.com>",
                "confidence": 0.91,
                "field_confidences": {
                    "vendor": 0.94,
                    "amount": 0.95,
                    "invoice_number": 0.94,
                    "due_date": 0.89,
                },
                "metadata": {
                    "document_type": "invoice",
                    "primary_source": "attachment",
                    "has_attachment": True,
                    "source_sender_domain": "google.com",
                    "requires_field_review": True,
                    "confidence_blockers": [
                        {"field": "vendor", "reason": "critical_field_low_confidence"},
                        {"field": "invoice_number", "reason": "critical_field_low_confidence"},
                        {"field": "due_date", "reason": "critical_field_low_confidence"},
                    ],
                },
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["confidence_gate"]["profile_id"] == "known_billing_attachment_invoice"
    assert normalized["requires_field_review"] is False
    assert normalized["confidence_blockers"] == []
    assert normalized["pipeline_blockers"] == []


def test_build_worklist_item_hides_planner_failed_behind_field_review_blockers(db):
    item = db.create_ap_item(
        _item_payload(
            "planner-failed-1",
            "default",
            state="received",
            extra={
                "field_confidences": {
                    "vendor": 0.94,
                    "amount": 0.99,
                    "invoice_number": 0.99,
                    "due_date": 0.99,
                },
                "metadata": {
                    "exception_code": "planner_failed",
                    "requires_field_review": True,
                    "confidence_blockers": [
                        {"field": "vendor", "reason": "critical_field_low_confidence", "confidence_pct": 94, "threshold_pct": 95}
                    ],
                },
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert [row["kind"] for row in normalized["pipeline_blockers"]] == ["confidence"]


def test_build_worklist_item_surfaces_planner_failed_as_processing_issue_without_user_blockers(db):
    item = db.create_ap_item(
        _item_payload(
            "planner-failed-2",
            "default",
            state="received",
            extra={
                "metadata": {
                    "exception_code": "planner_failed",
                    "requires_field_review": False,
                    "confidence_blockers": [],
                },
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["pipeline_blockers"] == [
        {
            "kind": "processing",
            "type": "processing_issue",
            "chip_label": "Processing issue",
            "title": "Processing issue",
            "detail": "Invoice processing needs retry or refresh before it can continue.",
            "field": None,
            "severity": "medium",
            "code": "planner_failed",
        }
    ]


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

    snapshot = CorrectionLearningService("default").get_extraction_review_calibration_snapshot(
        vendor_name="Acme",
        sender_domain="ap@acme.example",
        document_type="invoice",
    )
    assert snapshot["status"] == "available"
    assert snapshot["fields"]["amount"]["review_count"] == 1
    assert snapshot["fields"]["amount"]["confirmed_count"] == 1
    assert snapshot["fields"]["amount"]["source_win_rates"]["attachment"] == 1.0


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
    assert related_metadata["vendor_credit_summary"]["erp_application_status"] == "pending_target_post"
    assert "linked_credit_application_pending" in related_metadata["finance_effect_summary"]["blocked_reason_codes"]
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


def test_build_worklist_item_surfaces_entity_routing_and_approval_followup(db):
    item = db.create_ap_item(
        _item_payload(
            "entity-approval-1",
            "default",
            state="needs_approval",
            metadata={
                "approval_requested_at": "2026-03-18T08:00:00+00:00",
                "approval_sent_to": ["approver@clearledgr.com"],
                "approval_escalation_count": 1,
                "entity_candidates": [
                    {"entity_code": "US-01", "entity_name": "Acme US"},
                    {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                ],
            },
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["entity_routing_status"] == "needs_review"
    assert normalized["approval_followup"]["sla_breached"] is True
    assert normalized["approval_followup"]["escalation_due"] is True
    assert normalized["approval_followup"]["sla_minutes"] == 240
    assert normalized["approval_followup"]["escalation_minutes"] == 1440
    assert normalized["approval_pending_assignees"] == ["approver@clearledgr.com"]
    assert normalized["next_action"] == "resolve_entity_route"
    assert "entity" in {blocker["kind"] for blocker in normalized["pipeline_blockers"]}


def test_entity_route_resolution_handler_clears_entity_blocker(db):
    item = db.create_ap_item(
        _item_payload(
            "entity-route-1",
            "default",
            state="validated",
            metadata={
                "entity_candidates": [
                    {"entity_code": "US-01", "entity_name": "Acme US"},
                    {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                ],
            },
        )
    )

    payload = asyncio.run(
        resolve_ap_item_entity_route(
            item["id"],
            ResolveEntityRouteRequest(selection="GH-01"),
            organization_id="default",
            user=SimpleNamespace(
                email="user-test@default.example",
                user_id="user-test",
                organization_id="default",
                role="operator",
            ),
        )
    )
    assert payload["status"] == "resolved"
    assert payload["entity_selection"]["entity_code"] == "GH-01"
    assert payload["ap_item"]["entity_routing_status"] == "resolved"
    assert payload["ap_item"]["entity_code"] == "GH-01"
    assert payload["ap_item"]["next_action"] == "route_for_approval"
    assert _is_strict_profile_allowed_path("/api/ap/items/entity-route-1/entity-route/resolve") is True


def test_build_worklist_item_applies_org_entity_routing_rules(db):
    if hasattr(db, "ensure_organization"):
        db.ensure_organization("default", organization_name="default")
    db.update_organization(
        "default",
        settings={
            "entity_routing": {
                "entities": [
                    {"entity_code": "US-01", "entity_name": "Acme US"},
                    {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                ],
                "rules": [
                    {
                        "entity_code": "GH-01",
                        "sender_domains": ["ghana.vendor.example"],
                        "currencies": ["USD"],
                    }
                ],
            }
        },
    )
    item = db.create_ap_item(
        _item_payload(
            "entity-org-rule-1",
            "default",
            state="validated",
            extra={"sender": "billing@ghana.vendor.example"},
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["entity_routing_status"] == "resolved"
    assert normalized["entity_code"] == "GH-01"
    assert normalized["entity_name"] == "Acme Ghana"


def test_build_worklist_item_requires_manual_review_when_multi_entity_rules_do_not_match(db):
    if hasattr(db, "ensure_organization"):
        db.ensure_organization("default", organization_name="default")
    db.update_organization(
        "default",
        settings={
            "entity_routing": {
                "entities": [
                    {"entity_code": "US-01", "entity_name": "Acme US"},
                    {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                ],
                "rules": [
                    {
                        "entity_code": "US-01",
                        "sender_domains": ["us.vendor.example"],
                    }
                ],
            }
        },
    )
    item = db.create_ap_item(
        _item_payload(
            "entity-org-rule-2",
            "default",
            state="validated",
            extra={"sender": "billing@unknown.vendor.example"},
        )
    )

    normalized = build_worklist_item(db, item)

    assert normalized["entity_routing_status"] == "needs_review"
    assert len(normalized["entity_candidates"]) == 2
    assert normalized["entity_route_reason"] == "No entity routing rule matched this invoice."
    assert normalized["next_action"] == "resolve_entity_route"
