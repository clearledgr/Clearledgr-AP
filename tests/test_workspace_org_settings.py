"""Tests for ``PATCH /api/workspace/org/settings``.

Covers the canonical contract added 2026-04-28 to replace a raw-SQL
band-aid for the org-rename use case:

  * Admin role gate — non-admin users 403.
  * Tenant scope — cross-org PATCH 403s with ``org_mismatch``.
  * Validation — empty / oversize / control-character names 422.
  * No-op writes don't churn the DB or audit table.
  * Real renames update ``organizations.name`` AND emit a single
    ``organization_renamed`` audit event with prev_state → new_state.
  * Domain + integration_mode each emit their own audit event when
    they change.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    # Seed the org so update_organization has something to mutate.
    inst.ensure_organization("default", organization_name="default")
    return inst


def _admin_user():
    return SimpleNamespace(
        email="admin@example.com",
        user_id="admin-user",
        organization_id="default",
        role="owner",
    )


def _operator_user():
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id="default",
        role="ops",
    )


@pytest.fixture()
def client_factory(db):
    """Yield a TestClient builder so individual tests can pick the user role."""
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _audit_events(db, *, org_id: str, event_type: str | None = None):
    """Return audit events for an org Box, optionally filtered by event_type."""
    rows = db.list_box_audit_events(box_type="organization", box_id=org_id)
    if event_type:
        rows = [r for r in rows if r.get("event_type") == event_type]
    return rows


# ---------------------------------------------------------------------------
# Role + tenant gate
# ---------------------------------------------------------------------------


def test_patch_org_settings_requires_admin(client_factory):
    client = client_factory(_operator_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"organization_name": "Clearledgr"}},
    )
    assert resp.status_code == 403
    # workspace_shell._require_admin uses has_admin_access (Financial
    # Controller rank or higher) and raises with this exact detail.
    assert resp.json()["detail"] == "admin_role_required"


def test_patch_org_settings_blocks_cross_tenant_rename(client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "other-org", "patch": {"organization_name": "Other"}},
    )
    # Cross-tenant: admin of org 'default' cannot rename 'other-org'.
    # _resolve_org_id raises ``org_access_denied`` (no platform-level
    # super-admin concept on tenant APIs).
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_patch_org_settings_rejects_empty_name(client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"organization_name": "   "}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "organization_name_required"


def test_patch_org_settings_rejects_oversize_name(client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"organization_name": "x" * 200}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "organization_name_too_long"


def test_patch_org_settings_rejects_control_characters(client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        # Embedded NUL — would break CSV export + breaks UI rendering.
        json={"organization_id": "default", "patch": {"organization_name": "Clear\x00ledgr"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "organization_name_invalid_characters"


def test_patch_org_settings_rejects_invalid_integration_mode(client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"integration_mode": "neither"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_integration_mode"


# ---------------------------------------------------------------------------
# Happy path + audit emission
# ---------------------------------------------------------------------------


def test_patch_org_settings_renames_and_audits(db, client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"organization_name": "Clearledgr"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["organization"]["name"] == "Clearledgr"

    # DB carried the rename.
    org = db.get_organization("default") or {}
    assert org.get("name") == "Clearledgr"

    # Exactly one audit event was emitted, of type organization_renamed,
    # with the old + new names captured for post-mortem grep.
    renames = _audit_events(db, org_id="default", event_type="organization_renamed")
    assert len(renames) == 1
    event = renames[0]
    assert event["actor_id"] == "admin@example.com"
    assert event["actor_type"] == "user"
    # prev_state / new_state columns carry the rename pair.
    assert event.get("prev_state") == "default"
    assert event.get("new_state") == "Clearledgr"


def test_patch_org_settings_noop_does_not_audit(db, client_factory):
    """Submitting the SAME name we already have shouldn't emit an audit event
    or churn the DB. Defensive against a UI that re-saves on blur."""
    client = client_factory(_admin_user)
    # Seed prior name.
    db.update_organization("default", name="Clearledgr")

    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"organization_name": "Clearledgr"}},
    )
    assert resp.status_code == 200

    renames = _audit_events(db, org_id="default", event_type="organization_renamed")
    assert renames == []


def test_patch_org_settings_audits_domain_change(db, client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"domain": "clearledgr.com"}},
    )
    assert resp.status_code == 200

    domain_events = _audit_events(db, org_id="default", event_type="organization_domain_changed")
    assert len(domain_events) == 1
    assert domain_events[0].get("new_state") == "clearledgr.com"


def test_patch_org_settings_audits_integration_mode_change(db, client_factory):
    client = client_factory(_admin_user)
    resp = client.patch(
        "/api/workspace/org/settings",
        json={"organization_id": "default", "patch": {"integration_mode": "per_org"}},
    )
    assert resp.status_code == 200

    mode_events = _audit_events(
        db, org_id="default", event_type="organization_integration_mode_changed",
    )
    assert len(mode_events) == 1
    assert mode_events[0].get("prev_state") == "shared"
    assert mode_events[0].get("new_state") == "per_org"
