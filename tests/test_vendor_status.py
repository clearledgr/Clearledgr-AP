"""Tests for Module 4 Pass B — vendor allowlist/blocklist + bill gate.

Coverage:
  * Store: set_vendor_status validates the token, persists status
    + reason + attribution, idempotent re-write doesn't double-count.
  * Store: is_vendor_blocked predicate returns True only for
    status='blocked'; missing vendors are not blocked.
  * HTTP: PATCH /vendors/{name}/status admin-gated (403 for clerks),
    valid status updates emit a vendor_status_changed audit event,
    invalid status returns 422 with structured detail.
  * Bill-validation gate: pre_post_validate rejects blocked vendors
    via the existing 'vendor_active' check.
  * List payload carries status + status_reason on each vendor row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.vendor_status import router as vendor_status_router  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import (  # noqa: E402
    ROLE_AP_CLERK,
    get_current_user,
)
from clearledgr.integrations.erp_router import pre_post_validate  # noqa: E402
from clearledgr.services import ap_vendor_analysis  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _user(role: str = "owner", uid: str = "owner-user"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=uid,
        organization_id="default",
        role=role,
    )


@pytest.fixture()
def client_factory():
    def _build(user_factory):
        app = FastAPI()
        app.include_router(vendor_status_router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


# ─── Store layer ────────────────────────────────────────────────────


def test_set_vendor_status_persists_with_attribution(db):
    db.upsert_vendor_profile(
        organization_id="default", vendor_name="Acme",
    )
    out = db.set_vendor_status(
        organization_id="default", vendor_name="Acme",
        status="blocked", reason="payment fraud investigation",
        actor="owner@example.test",
    )
    assert out["status"] == "blocked"
    assert out["status_reason"] == "payment fraud investigation"
    assert out["status_changed_by"] == "owner@example.test"
    assert out["status_changed_at"]


def test_set_vendor_status_rejects_unknown_token(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    with pytest.raises(ValueError):
        db.set_vendor_status(
            organization_id="default", vendor_name="Acme",
            status="dancing",
        )


def test_set_vendor_status_returns_none_for_missing_vendor(db):
    out = db.set_vendor_status(
        organization_id="default", vendor_name="NotInDB",
        status="blocked",
    )
    assert out is None


def test_is_vendor_blocked_predicate(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    assert db.is_vendor_blocked("default", "Acme") is False
    db.set_vendor_status(
        organization_id="default", vendor_name="Acme", status="blocked",
    )
    assert db.is_vendor_blocked("default", "Acme") is True
    db.set_vendor_status(
        organization_id="default", vendor_name="Acme", status="active",
    )
    assert db.is_vendor_blocked("default", "Acme") is False
    # Vendors that don't exist aren't blocked (they're new).
    assert db.is_vendor_blocked("default", "Phantom") is False


# ─── HTTP layer ─────────────────────────────────────────────────────


def test_patch_status_admin_only(db, client_factory):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    client = client_factory(lambda: _user(role=ROLE_AP_CLERK, uid="clerk"))
    resp = client.patch(
        "/api/vendors/Acme/status?organization_id=default",
        json={"status": "blocked"},
    )
    assert resp.status_code == 403


def test_patch_status_emits_audit_with_diff(db, client_factory):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    client = client_factory(_user)
    resp = client.patch(
        "/api/vendors/Acme/status?organization_id=default",
        json={"status": "blocked", "reason": "AML hit"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["status_reason"] == "AML hit"

    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_status_changed"],
    )
    matching = [e for e in events.get("events", []) if e.get("box_id") == "Acme"]
    assert matching, "expected vendor_status_changed audit event"
    payload = matching[0].get("payload_json") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["before"] == "active"
    assert payload["after"] == "blocked"
    assert payload["reason"] == "AML hit"


def test_patch_status_no_op_skips_audit(db, client_factory):
    """Re-saving the same status must not produce a second audit row —
    we only emit on real change."""
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    db.set_vendor_status(
        organization_id="default", vendor_name="Acme", status="blocked",
    )
    client = client_factory(_user)
    # Now PATCH with same status
    resp = client.patch(
        "/api/vendors/Acme/status?organization_id=default",
        json={"status": "blocked"},
    )
    assert resp.status_code == 200
    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_status_changed"],
    )
    matching = [e for e in events.get("events", []) if e.get("box_id") == "Acme"]
    # Setup ran set_vendor_status but the API call is the only one
    # that emits — and we just observed a no-op, so zero rows.
    assert len(matching) == 0


def test_patch_invalid_status_returns_422(db, client_factory):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    client = client_factory(_user)
    resp = client.patch(
        "/api/vendors/Acme/status?organization_id=default",
        json={"status": "shenanigans"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "invalid_status"


def test_get_status_returns_404_for_missing_vendor(db, client_factory):
    client = client_factory(_user)
    resp = client.get(
        "/api/vendors/Phantom/status?organization_id=default"
    )
    assert resp.status_code == 404


# ─── Bill-validation gate ───────────────────────────────────────────


def test_pre_post_validate_rejects_blocked_vendor(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    db.set_vendor_status(
        organization_id="default", vendor_name="Acme", status="blocked",
    )
    created = db.create_ap_item({
        "id": "AP-blocked-test-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "ready_to_post",
        "invoice_number": "INV-1",
    })
    ap_id = created.get("id") or "AP-blocked-test-1"
    out = pre_post_validate(ap_id, "default", db=db)
    assert out["valid"] is False
    failures = out["failures"]
    assert any(f["check"] == "vendor_active" for f in failures), (
        f"vendor_active not in {failures}"
    )


# ─── List payload ───────────────────────────────────────────────────


def test_summary_rows_carry_status(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="Acme")
    db.set_vendor_status(
        organization_id="default", vendor_name="Acme",
        status="blocked", reason="oversight",
    )
    db.create_ap_item({
        "ap_item_id": "ap-acme-status",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 250.0,
        "state": "received",
    })
    rows = ap_vendor_analysis._build_vendor_summary_rows(db, "default", limit=50)
    matching = next((r for r in rows if r["vendor_name"] == "Acme"), None)
    assert matching is not None
    assert matching["profile"]["status"] == "blocked"
    assert matching["profile"]["status_reason"] == "oversight"
