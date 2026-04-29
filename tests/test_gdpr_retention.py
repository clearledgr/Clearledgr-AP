"""Tests for Wave 3 / E3 — GDPR retention + right-to-erasure.

Covers:
  * anonymize_vendor() — vendor PII fields nulled / [redacted];
    AP-item sender field redacted; metadata PII keys redacted but
    structure preserved; idempotent re-run.
  * Audit-event emission on anonymize.
  * identify_expired_vendors — picks vendors past cutoff with
    contact email; excludes already-anonymized.
  * run_retention_purge — bulk apply + retention_policy_runs row.
  * Data subject requests:
      - create_request — validates type + subject_kind, sets due_at
        to received + 30d.
      - process_access_request — payload includes vendor profile,
        AP items, vendor history.
      - process_erasure_request — anonymizes the vendor.
      - process_portability_request — same payload as access in
        machine-readable JSON.
      - reject_request — captures reason.
  * API: retention/eligible, retention/purge, DSR CRUD + process.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import gdpr as gdpr_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.data_subject_request import (  # noqa: E402
    create_request,
    process_access_request,
    process_erasure_request,
    process_portability_request,
    reject_request,
)
from clearledgr.services.gdpr_retention import (  # noqa: E402
    anonymize_vendor,
    identify_expired_vendors,
    run_retention_purge,
    _retention_cutoff,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta DE GmbH")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(gdpr_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_ap_item_with_pii(
    db, *, item_id: str, vendor: str = "Vendor X", org: str = "orgA",
) -> dict:
    return db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": 1500.0,
        "currency": "EUR",
        "sender": "ap-clerk@vendor-x.com",
        "metadata": {
            "contact_phone": "+44 20 1234 5678",
            "from_address": "10 Vendor Street, London",
            "non_pii_field": "sku-123",
        },
        "state": "received",
    })


# ─── anonymize_vendor() ─────────────────────────────────────────────


def test_anonymize_redacts_vendor_profile(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
        remittance_email="rem@vendor-x.com",
        registered_address="10 Vendor Street, London",
        director_names=["Alice Smith", "Bob Jones"],
        vendor_aliases=["Vendor X Limited"],
    )
    counters = anonymize_vendor(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert counters["vendor_profiles_anonymized"] == 1
    fresh = db.get_vendor_profile("orgA", "Vendor X")
    assert fresh["primary_contact_email"] is None
    assert fresh["remittance_email"] is None
    assert fresh["registered_address"] == "[redacted]"
    assert fresh["director_names"] == []
    assert fresh["vendor_aliases"] == []


def test_anonymize_redacts_ap_items_for_vendor(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    item = _make_ap_item_with_pii(db, item_id="AP-gdpr-1")
    counters = anonymize_vendor(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert counters["ap_items_anonymized"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["sender"] == "[redacted]"
    meta = fresh.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta) if meta else {}
    assert meta["contact_phone"] == "[redacted]"
    assert meta["from_address"] == "[redacted]"
    # Non-PII keys preserved.
    assert meta["non_pii_field"] == "sku-123"


def test_anonymize_emits_audit_event(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    anonymize_vendor(
        db, organization_id="orgA", vendor_name="Vendor X",
        actor="ops@orgA",
    )
    expected_key = "gdpr_anonymize:orgA:Vendor X"
    fetched = db.get_ap_audit_event_by_key(expected_key)
    assert fetched is not None
    assert fetched["event_type"] == "gdpr_vendor_anonymized"
    assert fetched["box_type"] == "vendor"


def test_anonymize_idempotent(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    item = _make_ap_item_with_pii(db, item_id="AP-gdpr-idem")
    anonymize_vendor(db, organization_id="orgA", vendor_name="Vendor X")
    # Second call: no raise, fields stay redacted.
    counters = anonymize_vendor(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert counters["errors"] == 0
    fresh = db.get_ap_item(item["id"])
    assert fresh["sender"] == "[redacted]"


def test_anonymize_tenant_isolation(db):
    db.upsert_vendor_profile(
        "orgA", "Shared",
        primary_contact_email="ap@shared.com",
    )
    db.upsert_vendor_profile(
        "orgB", "Shared",
        primary_contact_email="ap@shared.com",
    )
    anonymize_vendor(db, organization_id="orgA", vendor_name="Shared")
    a = db.get_vendor_profile("orgA", "Shared")
    b = db.get_vendor_profile("orgB", "Shared")
    assert a["primary_contact_email"] is None
    assert b["primary_contact_email"] == "ap@shared.com"


# ─── Retention identification + run ─────────────────────────────────


def _force_old_ap_timestamps(db, *, item_id: str, days_ago: int):
    """Test helper: set ap_items.updated_at + created_at to N days ago
    so the retention scanner picks them up."""
    old = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ap_items SET created_at = %s, updated_at = %s "
            "WHERE id = %s",
            (old, old, item_id),
        )
        conn.commit()


def test_identify_expired_vendors_picks_stale(db):
    db.upsert_vendor_profile(
        "orgA", "OldVendor",
        primary_contact_email="ap@old.com",
    )
    item = _make_ap_item_with_pii(db, item_id="AP-old-1", vendor="OldVendor")
    _force_old_ap_timestamps(db, item_id=item["id"], days_ago=3000)

    # Recent vendor — not eligible.
    db.upsert_vendor_profile(
        "orgA", "NewVendor",
        primary_contact_email="ap@new.com",
    )
    _make_ap_item_with_pii(db, item_id="AP-new-1", vendor="NewVendor")

    cutoff = _retention_cutoff(2555)
    expired = identify_expired_vendors(
        db, organization_id="orgA", cutoff_iso=cutoff,
    )
    assert "OldVendor" in expired
    assert "NewVendor" not in expired


def test_identify_excludes_already_anonymized(db):
    """A vendor whose contact_email is already [redacted] shouldn't
    re-appear in the eligible set."""
    db.upsert_vendor_profile(
        "orgA", "RedactedVendor",
        primary_contact_email="[redacted]",
    )
    cutoff = _retention_cutoff(2555)
    expired = identify_expired_vendors(
        db, organization_id="orgA", cutoff_iso=cutoff,
    )
    assert "RedactedVendor" not in expired


def test_run_retention_purge_records_run(db):
    db.upsert_vendor_profile(
        "orgA", "OldVendor",
        primary_contact_email="ap@old.com",
    )
    item = _make_ap_item_with_pii(db, item_id="AP-purge-1", vendor="OldVendor")
    _force_old_ap_timestamps(db, item_id=item["id"], days_ago=3000)

    result = run_retention_purge(
        db, organization_id="orgA", actor="ops@orgA",
    )
    assert result["vendors_processed"] >= 1
    assert result["ap_items_anonymized"] >= 1

    # retention_policy_runs row written.
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM retention_policy_runs WHERE id = %s",
            (result["id"],),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["run_kind"] == "automated_purge"
    assert int(row["ap_items_anonymized"]) >= 1


# ─── Data subject requests ──────────────────────────────────────────


def test_dsr_create_validates_type(db):
    with pytest.raises(ValueError):
        create_request(
            db, organization_id="orgA",
            request_type="bogus", subject_kind="vendor",
            subject_identifier="V",
        )


def test_dsr_create_sets_due_at_30_days(db):
    row = create_request(
        db, organization_id="orgA",
        request_type="access", subject_kind="vendor",
        subject_identifier="Vendor X",
    )
    received = datetime.fromisoformat(
        row["received_at"].replace("Z", "+00:00")
    )
    due = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
    delta = (due - received).total_seconds()
    assert abs(delta - 30 * 86400) < 5  # within 5s of 30 days


def test_dsr_access_payload_includes_vendor_data(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    _make_ap_item_with_pii(db, item_id="AP-dsr-access")
    req = create_request(
        db, organization_id="orgA",
        request_type="access", subject_kind="vendor",
        subject_identifier="Vendor X",
    )
    fresh = process_access_request(db, req["id"], actor="ops@orgA")
    assert fresh["status"] == "completed"
    payload = fresh["export_payload"]
    assert "vendor_profile" in payload
    assert len(payload["vendor_profile"]) == 1
    assert len(payload["ap_items"]) == 1


def test_dsr_erasure_anonymizes(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    _make_ap_item_with_pii(db, item_id="AP-dsr-erase")
    req = create_request(
        db, organization_id="orgA",
        request_type="erasure", subject_kind="vendor",
        subject_identifier="Vendor X",
    )
    fresh = process_erasure_request(db, req["id"], actor="ops@orgA")
    assert fresh["status"] == "completed"
    profile = db.get_vendor_profile("orgA", "Vendor X")
    assert profile["primary_contact_email"] is None


def test_dsr_portability_returns_machine_readable_summary(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    req = create_request(
        db, organization_id="orgA",
        request_type="portability", subject_kind="vendor",
        subject_identifier="Vendor X",
    )
    fresh = process_portability_request(db, req["id"], actor="ops@orgA")
    assert fresh["outcome_summary"]["format"] == "application/json"


def test_dsr_reject_records_reason(db):
    req = create_request(
        db, organization_id="orgA",
        request_type="erasure", subject_kind="vendor",
        subject_identifier="OnlyOneAtThisOrg",
    )
    fresh = reject_request(
        db, req["id"],
        reason="SOX retention not yet expired (records 4 years old; 7-year hold)",
        actor="ops@orgA",
    )
    assert fresh["status"] == "rejected"
    assert "SOX" in (fresh.get("processing_notes") or "")


# ─── API ────────────────────────────────────────────────────────────


def test_api_retention_eligible(db, client_orgA):
    resp = client_orgA.get("/api/workspace/gdpr/retention/eligible")
    assert resp.status_code == 200
    data = resp.json()
    assert "retention_days" in data
    assert "expired_vendor_count" in data


def test_api_retention_purge(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "OldAPIvendor",
        primary_contact_email="ap@old.com",
    )
    item = _make_ap_item_with_pii(db, item_id="AP-api-purge", vendor="OldAPIvendor")
    _force_old_ap_timestamps(db, item_id=item["id"], days_ago=3000)
    resp = client_orgA.post("/api/workspace/gdpr/retention/purge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["vendors_processed"] >= 1
    assert data["ap_items_anonymized"] >= 1


def test_api_dsr_create_list_get(db, client_orgA):
    resp = client_orgA.post(
        "/api/workspace/gdpr/data-subject-requests",
        json={
            "request_type": "access",
            "subject_kind": "vendor",
            "subject_identifier": "Vendor API",
            "requestor_email": "vendor-api@example.com",
        },
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]

    list_resp = client_orgA.get(
        "/api/workspace/gdpr/data-subject-requests",
    )
    assert list_resp.status_code == 200
    assert any(r["id"] == req_id for r in list_resp.json())

    get_resp = client_orgA.get(
        f"/api/workspace/gdpr/data-subject-requests/{req_id}",
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "pending"


def test_api_dsr_process_access(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor API",
        primary_contact_email="ap@vendor-api.com",
    )
    create_resp = client_orgA.post(
        "/api/workspace/gdpr/data-subject-requests",
        json={
            "request_type": "access",
            "subject_kind": "vendor",
            "subject_identifier": "Vendor API",
        },
    )
    req_id = create_resp.json()["id"]
    proc_resp = client_orgA.post(
        f"/api/workspace/gdpr/data-subject-requests/{req_id}/process",
        json={},
    )
    assert proc_resp.status_code == 200
    assert proc_resp.json()["status"] == "completed"


def test_api_dsr_process_already_done_400(db, client_orgA):
    create_resp = client_orgA.post(
        "/api/workspace/gdpr/data-subject-requests",
        json={
            "request_type": "access",
            "subject_kind": "vendor",
            "subject_identifier": "VV",
        },
    )
    req_id = create_resp.json()["id"]
    client_orgA.post(
        f"/api/workspace/gdpr/data-subject-requests/{req_id}/process",
        json={},
    )
    resp = client_orgA.post(
        f"/api/workspace/gdpr/data-subject-requests/{req_id}/process",
        json={},
    )
    assert resp.status_code == 400


def test_api_dsr_reject(db, client_orgA):
    create_resp = client_orgA.post(
        "/api/workspace/gdpr/data-subject-requests",
        json={
            "request_type": "erasure",
            "subject_kind": "vendor",
            "subject_identifier": "Active Vendor",
        },
    )
    req_id = create_resp.json()["id"]
    resp = client_orgA.post(
        f"/api/workspace/gdpr/data-subject-requests/{req_id}/reject",
        json={"reason": "Vendor still active under contract"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_api_dsr_cross_org_404(db, client_orgA):
    other = create_request(
        db, organization_id="orgB",
        request_type="access", subject_kind="vendor",
        subject_identifier="OrgB Vendor",
    )
    resp = client_orgA.get(
        f"/api/workspace/gdpr/data-subject-requests/{other['id']}",
    )
    assert resp.status_code == 404
