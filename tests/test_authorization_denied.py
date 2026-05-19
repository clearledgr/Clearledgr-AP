"""Coverage for the AuthorizationDenied funnel.

The paper requires every authorisation decision, including denied ones,
to be recorded in audit_events. These tests check:

1. Each typed exception sets the expected structured fields.
2. ``forbid()`` raises ``AuthorizationDenied`` with the given context.
3. ``emit_authorization_denied_audit`` writes one row through
   ``db.append_audit_event`` with event_type=authorization_denied and
   the structured fields preserved in the payload.
4. The emit helper never propagates a DB failure (returns silently if
   the chain insert fails).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from solden.core.authorization import (
    AdminRequired,
    AuthorizationDenied,
    CrossTenantAccessDenied,
    OrganizationMismatch,
    RoleRequired,
    emit_authorization_denied_audit,
    forbid,
)


# ─── Exception construction ────────────────────────────────────────


def test_authorization_denied_carries_structured_context() -> None:
    exc = AuthorizationDenied(
        "test_reason",
        actor_id="user@example.com",
        resource_type="ap_item",
        resource_id="ap_123",
        organization_id="org_x",
        attempted_action="read",
        http_status=401,
        http_detail="custom_detail",
    )
    assert exc.denial_reason == "test_reason"
    assert exc.actor_id == "user@example.com"
    assert exc.resource_type == "ap_item"
    assert exc.resource_id == "ap_123"
    assert exc.organization_id == "org_x"
    assert exc.attempted_action == "read"
    assert exc.http_status == 401
    assert exc.http_detail == "custom_detail"


def test_authorization_denied_defaults() -> None:
    exc = AuthorizationDenied("test_reason")
    assert exc.http_status == 403
    assert exc.http_detail == "test_reason"  # defaults to denial_reason
    assert exc.actor_type == "user"
    assert exc.actor_id is None


def test_organization_mismatch_subclass() -> None:
    exc = OrganizationMismatch(actor_id="u1", organization_id="org_x")
    assert isinstance(exc, AuthorizationDenied)
    assert exc.denial_reason == "organization_mismatch"
    assert exc.http_detail == "org_mismatch"
    assert exc.http_status == 403


def test_cross_tenant_subclass() -> None:
    exc = CrossTenantAccessDenied(actor_id="u1", resource_type="vendor", resource_id="v9")
    assert exc.denial_reason == "cross_tenant_access_denied"
    assert exc.resource_type == "vendor"


def test_role_required_subclass() -> None:
    exc = RoleRequired(required_role="financial_controller", actor_id="u1")
    assert exc.denial_reason == "role_required:financial_controller"
    assert exc.http_detail == "financial_controller_required"
    assert exc.required_role == "financial_controller"


def test_admin_required_subclass() -> None:
    exc = AdminRequired(actor_id="u1")
    assert exc.denial_reason == "role_required:admin"
    assert exc.http_detail == "admin_required"
    assert exc.required_role == "admin"


def test_forbid_raises_authorization_denied() -> None:
    with pytest.raises(AuthorizationDenied) as info:
        forbid(
            "custom_reason",
            actor_id="u1",
            organization_id="org_x",
            attempted_action="read",
        )
    assert info.value.denial_reason == "custom_reason"
    assert info.value.actor_id == "u1"
    assert info.value.organization_id == "org_x"
    assert info.value.attempted_action == "read"


# ─── Audit emission ───────────────────────────────────────────────


class _StubDB:
    """Captures append_audit_event payloads for assertion."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def append_audit_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.events.append(payload)
        return {"id": f"EVT-{len(self.events)}", **payload}


def test_emit_writes_authorization_denied_row_for_resource_denial() -> None:
    stub = _StubDB()
    with patch("solden.core.authorization._get_db", return_value=stub):
        emit_authorization_denied_audit(
            denial_reason="organization_mismatch",
            actor_type="user",
            actor_id="user@example.com",
            resource_type="ap_item",
            resource_id="ap_123",
            organization_id="org_x",
            attempted_action="read_ap_item",
            request_path="/api/workspace/ap-items/ap_123",
            request_method="GET",
            http_status=403,
        )
    assert len(stub.events) == 1
    event = stub.events[0]
    assert event["event_type"] == "authorization_denied"
    # Box-keying: resource_type wins as box_type
    assert event["box_type"] == "ap_item"
    assert event["box_id"] == "ap_123"
    assert event["actor_id"] == "user@example.com"
    assert event["organization_id"] == "org_x"
    payload = event["payload_json"]
    assert payload["denial_reason"] == "organization_mismatch"
    assert payload["attempted_action"] == "read_ap_item"
    assert payload["request_path"] == "/api/workspace/ap-items/ap_123"
    assert payload["request_method"] == "GET"
    assert payload["http_status"] == 403


def test_emit_falls_back_to_organization_box_when_no_resource() -> None:
    """Org-level denials (admin pages, login attempts) don't have a
    specific resource. Box-key falls back to (organization, <org_id>)."""
    stub = _StubDB()
    with patch("solden.core.authorization._get_db", return_value=stub):
        emit_authorization_denied_audit(
            denial_reason="admin_required",
            actor_id="user@example.com",
            organization_id="org_x",
            request_path="/api/admin/users",
            request_method="POST",
        )
    event = stub.events[0]
    assert event["box_type"] == "organization"
    assert event["box_id"] == "org_x"
    assert event["payload_json"]["denial_reason"] == "admin_required"


def test_emit_handles_unknown_org() -> None:
    """If neither resource nor org is known (e.g., unauthenticated probe),
    the audit row still fires with box_id='unknown' and
    organization_id='_unknown'.

    The fallback sentinel was 'default' pre-M19, but migration v79's
    CHECK constraint rejects that literal at the DB level — '_unknown'
    is the canonical replacement for "no resolved org" on audit rows.
    """
    stub = _StubDB()
    with patch("solden.core.authorization._get_db", return_value=stub):
        emit_authorization_denied_audit(
            denial_reason="forbidden",
            request_path="/api/admin/users",
            request_method="GET",
        )
    event = stub.events[0]
    assert event["box_type"] == "organization"
    assert event["box_id"] == "unknown"
    assert event["actor_id"] == "unknown"
    assert event["organization_id"] == "_unknown"


def test_emit_never_raises_when_db_fails() -> None:
    """The 403 response path must not fail if the audit chain is down.
    The paper requires denials be recorded; it does not require denials
    be blocked when recording fails."""

    class _ExplodingDB:
        def append_audit_event(self, _payload: Dict[str, Any]) -> Dict[str, Any]:
            raise RuntimeError("audit chain unavailable")

    with patch("solden.core.authorization._get_db", return_value=_ExplodingDB()):
        # Must not raise
        emit_authorization_denied_audit(
            denial_reason="test",
            organization_id="org_x",
        )
