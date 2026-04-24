"""Phase 9 Backoffice surface tests — admin Box exceptions API.

Covers the three endpoints added to close the deck's Backoffice
promise for the exceptions half of the Box contract:

- GET /api/admin/box/exceptions returns the org-scoped unresolved queue,
  respects severity and box_type filters, ordered severity-first.
- GET /api/admin/box/exceptions/stats returns counts by severity/type/box_type.
- POST /api/admin/box/exceptions/{id}/resolve transitions a row to
  resolved, attributes the acting user, and prevents cross-tenant
  resolution.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app  # noqa: E402
from clearledgr.api import box_exceptions_admin as admin_module  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import TokenData  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _seed_ap_box(db, box_id: str) -> None:
    db.create_ap_item({
        "id": box_id,
        "invoice_key": f"inv-{box_id}",
        "thread_id": f"thr-{box_id}",
        "message_id": f"msg-{box_id}",
        "subject": "Invoice",
        "sender": "billing@vendor.com",
        "vendor_name": "Acme",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": f"INV-{box_id}",
        "state": "needs_approval",
        "organization_id": "default",
    })


def _seed_exception(
    db, box_id: str, exception_type: str, severity: str = "medium",
    organization_id: str = "default",
) -> dict:
    return db.raise_box_exception(
        box_id=box_id,
        box_type="ap_item",
        organization_id=organization_id,
        exception_type=exception_type,
        severity=severity,
        reason=f"{exception_type} seeded for test",
        raised_by="test",
        raised_actor_type="system",
    )


def _as_admin(email: str = "admin@acme.com", organization_id: str = "default") -> TokenData:
    return TokenData(
        user_id=f"u-{email}",
        email=email,
        organization_id=organization_id,
        role="owner",
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture()
def client(db):
    app.dependency_overrides[admin_module.get_current_user] = lambda: _as_admin()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(admin_module.get_current_user, None)


# ---------------------------------------------------------------------------
# GET /api/admin/box/exceptions
# ---------------------------------------------------------------------------


def test_list_returns_unresolved_exceptions_ordered_by_severity(client, db):
    _seed_ap_box(db, "AP-EXC-LIST-1")
    _seed_ap_box(db, "AP-EXC-LIST-2")
    _seed_ap_box(db, "AP-EXC-LIST-3")
    _seed_exception(db, "AP-EXC-LIST-1", "amount_anomaly", severity="low")
    _seed_exception(db, "AP-EXC-LIST-2", "bank_details_changed", severity="critical")
    _seed_exception(db, "AP-EXC-LIST-3", "po_required_missing", severity="high")

    resp = client.get("/api/admin/box/exceptions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    severities = [row["severity"] for row in body["items"]]
    # critical → high → low
    assert severities == ["critical", "high", "low"]


def test_list_severity_filter(client, db):
    _seed_ap_box(db, "AP-EXC-SEV-1")
    _seed_ap_box(db, "AP-EXC-SEV-2")
    _seed_exception(db, "AP-EXC-SEV-1", "type_a", severity="critical")
    _seed_exception(db, "AP-EXC-SEV-2", "type_b", severity="medium")

    resp = client.get("/api/admin/box/exceptions?severity=critical")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["exception_type"] == "type_a"


def test_list_rejects_invalid_severity(client):
    resp = client.get("/api/admin/box/exceptions?severity=apocalyptic")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_severity"


def test_list_requires_admin_role(db):
    def _viewer():
        return TokenData(
            user_id="u-viewer",
            email="viewer@acme.com",
            organization_id="default",
            role="viewer",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[admin_module.get_current_user] = _viewer
    try:
        resp = TestClient(app).get("/api/admin/box/exceptions")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "admin_required"
    finally:
        app.dependency_overrides.pop(admin_module.get_current_user, None)


def test_list_is_org_scoped(client, db):
    _seed_ap_box(db, "AP-EXC-OWN")
    _seed_exception(db, "AP-EXC-OWN", "my_exception", severity="high")
    # Seed an ap_item belonging to another tenant + its exception
    db.create_ap_item({
        "id": "AP-EXC-OTHER",
        "invoice_key": "inv-other",
        "thread_id": "thr-other",
        "message_id": "msg-other",
        "subject": "Other tenant invoice",
        "sender": "billing@other.com",
        "vendor_name": "OtherVendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": "INV-OTHER",
        "state": "needs_approval",
        "organization_id": "other-tenant",
    })
    db.raise_box_exception(
        box_id="AP-EXC-OTHER",
        box_type="ap_item",
        organization_id="other-tenant",
        exception_type="their_exception",
        severity="critical",
        reason="tenant-isolation test",
        raised_by="test",
        raised_actor_type="system",
    )

    resp = client.get("/api/admin/box/exceptions")
    assert resp.status_code == 200
    types = [row["exception_type"] for row in resp.json()["items"]]
    assert types == ["my_exception"]


# ---------------------------------------------------------------------------
# GET /api/admin/box/exceptions/stats
# ---------------------------------------------------------------------------


def test_stats_groups_by_severity_type_and_box_type(client, db):
    _seed_ap_box(db, "AP-EXC-STAT-1")
    _seed_ap_box(db, "AP-EXC-STAT-2")
    _seed_ap_box(db, "AP-EXC-STAT-3")
    _seed_exception(db, "AP-EXC-STAT-1", "type_x", severity="high")
    _seed_exception(db, "AP-EXC-STAT-2", "type_x", severity="medium")
    _seed_exception(db, "AP-EXC-STAT-3", "type_y", severity="high")

    resp = client.get("/api/admin/box/exceptions/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_unresolved"] == 3
    assert body["by_severity"]["high"] == 2
    assert body["by_severity"]["medium"] == 1
    assert body["by_type"]["type_x"] == 2
    assert body["by_type"]["type_y"] == 1
    assert body["by_box_type"]["ap_item"] == 3


# ---------------------------------------------------------------------------
# POST /api/admin/box/exceptions/{id}/resolve
# ---------------------------------------------------------------------------


def test_resolve_attributes_current_user_and_removes_from_queue(client, db):
    _seed_ap_box(db, "AP-EXC-RES-1")
    excp = _seed_exception(db, "AP-EXC-RES-1", "po_required_missing", severity="high")

    resp = client.post(
        f"/api/admin/box/exceptions/{excp['id']}/resolve",
        json={"resolution_note": "PO attached manually"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["exception"]["resolved_by"] == "admin@acme.com"
    assert body["exception"]["resolution_note"] == "PO attached manually"

    # Resolved rows disappear from the queue
    q = client.get("/api/admin/box/exceptions").json()
    assert q["count"] == 0


def test_resolve_idempotent_on_already_resolved(client, db):
    _seed_ap_box(db, "AP-EXC-IDEM")
    excp = _seed_exception(db, "AP-EXC-IDEM", "amount_anomaly", severity="medium")
    client.post(f"/api/admin/box/exceptions/{excp['id']}/resolve", json={})

    resp2 = client.post(f"/api/admin/box/exceptions/{excp['id']}/resolve", json={})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "already_resolved"


def test_resolve_unknown_returns_404(client):
    resp = client.post("/api/admin/box/exceptions/does-not-exist/resolve", json={})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "exception_not_found"


def test_resolve_refuses_cross_tenant(db):
    # Act as admin@acme but the exception belongs to other-tenant.
    def _acme():
        return _as_admin(email="admin@acme.com", organization_id="default")
    app.dependency_overrides[admin_module.get_current_user] = _acme
    try:
        db.create_ap_item({
            "id": "AP-EXC-XTENANT",
            "invoice_key": "inv-xtenant",
            "thread_id": "thr-xtenant",
            "message_id": "msg-xtenant",
            "subject": "Other tenant",
            "sender": "billing@other.com",
            "vendor_name": "OtherVendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-XT",
            "state": "needs_approval",
            "organization_id": "other-tenant",
        })
        excp = db.raise_box_exception(
            box_id="AP-EXC-XTENANT",
            box_type="ap_item",
            organization_id="other-tenant",
            exception_type="secret_exc",
            severity="critical",
            reason="cross-tenant fence test",
            raised_by="test",
            raised_actor_type="system",
        )

        client = TestClient(app)
        resp = client.post(f"/api/admin/box/exceptions/{excp['id']}/resolve", json={})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "org_mismatch"
    finally:
        app.dependency_overrides.pop(admin_module.get_current_user, None)
