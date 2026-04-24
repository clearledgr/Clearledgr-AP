from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _apply_runtime_surface_profile, app  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import create_access_token  # noqa: E402


def _item_payload(item_id: str, org_id: str) -> dict:
    return {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": f"Vendor {item_id}",
        "amount": 123.45,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "needs_approval",
        "confidence": 0.91,
        "organization_id": org_id,
        "metadata": {},
    }


def _jwt_for(org_id: str, user_id: str = "user-test") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.example",
        organization_id=org_id,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str, user_id: str = "user-test") -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id, user_id)}"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "true")
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    _apply_runtime_surface_profile()
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    # §13 Agent Activity retention — FREE tier defaults to 7 days,
    # which would trim the backdated March 1 events in this suite.
    # These tests exercise cross-org scoping + normalization, not
    # retention behaviour, so upgrade the fixture orgs to
    # Professional (7-year window) to avoid coupling the suite to
    # the retention filter. Retention itself is covered in
    # test_subscription_quota_enforcement.
    import clearledgr.services.subscription as sub_mod
    sub_mod._subscription_service = None
    from clearledgr.services.subscription import get_subscription_service, PlanTier
    sub_svc = get_subscription_service()
    for org_id in ("org-alpha", "org-beta"):
        sub_svc.upgrade_plan(org_id, tier=PlanTier.PROFESSIONAL)

    return TestClient(app)


def test_recent_ap_audit_is_org_scoped_and_normalized(client, db):
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.create_ap_item(_item_payload("alpha-2", "org-alpha"))
    db.create_ap_item(_item_payload("beta-1", "org-beta"))

    db.append_audit_event(
        {
            "id": "evt-alpha-1",
            "ap_item_id": "alpha-1",
            "event_type": "deterministic_validation_failed",
            "decision_reason": "policy_requirement_amt_500",
            "organization_id": "org-alpha",
            "ts": "2026-03-01T01:00:00+00:00",
        }
    )
    db.append_audit_event(
        {
            "id": "evt-alpha-2",
            "ap_item_id": "alpha-2",
            "event_type": "approval_nudge_failed",
            "decision_reason": "approval_nudge",
            "organization_id": "org-alpha",
            "ts": "2026-03-01T02:00:00+00:00",
        }
    )
    db.append_audit_event(
        {
            "id": "evt-beta-1",
            "ap_item_id": "beta-1",
            "event_type": "state_transition",
            "from_state": "received",
            "to_state": "validated",
            "organization_id": "org-beta",
            "ts": "2026-03-01T03:00:00+00:00",
        }
    )

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&limit=10",
        headers=_auth_headers("org-alpha"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-alpha"

    events = payload["events"]
    assert len(events) == 2
    assert all(str(event.get("box_id", "")).startswith("alpha-") for event in events)
    assert events[0]["ts"] >= events[1]["ts"]  # newest first
    assert isinstance(events[0].get("operator_title"), str)
    assert isinstance(events[0].get("operator_message"), str)
    assert isinstance(events[0].get("operator_severity"), str)
    assert isinstance(events[0].get("operator_importance"), str)
    assert isinstance(events[0].get("operator_category"), str)
    assert isinstance(events[0].get("operator_evidence_label"), str)
    assert isinstance(events[0].get("operator_evidence_detail"), str)
    assert events[0]["operator_title"] == "Approval reminder failed"
    assert events[0]["operator_evidence_label"] == "Approval action"
    assert events[1]["operator_title"] == "Validation checks failed"
    assert events[1]["operator_evidence_label"] == "Policy check"


def test_recent_ap_audit_since_ts_filters_older_events(client, db):
    """since_ts filters out events before the requested timestamp —
    lets CS answer "what happened for this customer after 9am today"
    without paging through overnight activity.
    """
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.append_audit_event({
        "id": "evt-old",
        "ap_item_id": "alpha-1",
        "event_type": "state_transition",
        "to_state": "validated",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T08:00:00+00:00",
    })
    db.append_audit_event({
        "id": "evt-new",
        "ap_item_id": "alpha-1",
        "event_type": "state_transition",
        "to_state": "needs_approval",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T10:00:00+00:00",
    })

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&since_ts=2026-03-01T09:00:00Z",
        headers=_auth_headers("org-alpha"),
    )
    assert response.status_code == 200
    events = response.json()["events"]
    # Only the 10:00 event survives the 09:00 cutoff.
    assert len(events) == 1
    assert events[0]["ts"].startswith("2026-03-01T10")


def test_recent_ap_audit_event_type_filter(client, db):
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.append_audit_event({
        "id": "evt-st",
        "ap_item_id": "alpha-1",
        "event_type": "state_transition",
        "to_state": "validated",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T08:00:00+00:00",
    })
    db.append_audit_event({
        "id": "evt-post",
        "ap_item_id": "alpha-1",
        "event_type": "erp_post_attempted",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T09:00:00+00:00",
    })

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&event_type=erp_post_attempted",
        headers=_auth_headers("org-alpha"),
    )
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "erp_post_attempted"


def test_recent_ap_audit_failures_only_filter(client, db):
    """The most common CS question is 'what broke today?' —
    failures_only=true filters to the known-failure event types.
    """
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.append_audit_event({
        "id": "evt-ok",
        "ap_item_id": "alpha-1",
        "event_type": "state_transition",
        "to_state": "validated",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T08:00:00+00:00",
    })
    db.append_audit_event({
        "id": "evt-fail",
        "ap_item_id": "alpha-1",
        "event_type": "erp_post_failed",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T09:00:00+00:00",
    })
    db.append_audit_event({
        "id": "evt-nudge-fail",
        "ap_item_id": "alpha-1",
        "event_type": "approval_nudge_failed",
        "organization_id": "org-alpha",
        "ts": "2026-03-01T10:00:00+00:00",
    })

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&failures_only=true",
        headers=_auth_headers("org-alpha"),
    )
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 2
    types = {e["event_type"] for e in events}
    assert "state_transition" not in types
    assert "erp_post_failed" in types
    assert "approval_nudge_failed" in types


def test_recent_ap_audit_rejects_cross_org_access(client, db):
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.append_audit_event(
        {
            "id": "evt-alpha-1",
            "ap_item_id": "alpha-1",
            "event_type": "state_transition",
            "from_state": "received",
            "to_state": "validated",
            "organization_id": "org-alpha",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&limit=10",
        headers=_auth_headers("org-beta"),
    )
    assert response.status_code == 403
