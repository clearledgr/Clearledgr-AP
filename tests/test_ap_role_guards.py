"""Role-guard inventory for AP mutation routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from clearledgr.api import agent_intents, ap_items, gmail_extension
from clearledgr.core.auth import TokenData, get_current_user, has_ops_access, require_ops_user


OPS_MUTATION_ROUTES = {
    ("POST", "/api/agent/intents/execute"),
    ("POST", "/api/agent/intents/execute-request"),
    ("POST", "/api/ap/items/compose/create"),
    ("POST", "/api/ap/items/{ap_item_id}/comments"),
    ("POST", "/api/ap/items/{ap_item_id}/compose-link"),
    ("PATCH", "/api/ap/items/{ap_item_id}/fields"),
    ("POST", "/api/ap/items/{ap_item_id}/files"),
    ("POST", "/api/ap/items/{ap_item_id}/gmail-link"),
    ("POST", "/api/ap/items/{ap_item_id}/notes"),
    ("POST", "/api/ap/items/{ap_item_id}/sources/link"),
    ("POST", "/api/ap/items/{ap_item_id}/tasks"),
    ("POST", "/api/ap/items/tasks/{task_id}/assign"),
    ("POST", "/api/ap/items/tasks/{task_id}/comments"),
    ("POST", "/api/ap/items/tasks/{task_id}/status"),
    ("POST", "/api/ap/items/{ap_item_id}/field-review/resolve"),
    ("POST", "/api/ap/items/{ap_item_id}/resubmit"),
    ("POST", "/api/ap/items/{ap_item_id}/merge"),
    ("POST", "/api/ap/items/{ap_item_id}/split"),
    ("POST", "/api/ap/items/{ap_item_id}/retry-post"),
    ("POST", "/extension/process"),
    ("POST", "/extension/scan"),
    ("POST", "/extension/approve-and-post"),
    ("POST", "/extension/escalate"),
    ("POST", "/extension/submit-for-approval"),
    ("POST", "/extension/reject-invoice"),
    ("POST", "/extension/budget-decision"),
    ("POST", "/extension/approval-nudge"),
    ("POST", "/extension/vendor-followup"),
    ("POST", "/extension/route-low-risk-approval"),
    ("POST", "/extension/retry-recoverable-failure"),
    ("POST", "/extension/finance-summary-share"),
    ("POST", "/extension/record-field-correction"),
}


def _dependency_names(route: APIRoute) -> set[str]:
    return {
        getattr(dep.call, "__name__", "")
        for dep in route.dependant.dependencies
    }


def test_ops_mutation_routes_require_ops_dependency():
    missing = set()
    for router in (agent_intents.router, ap_items.router, gmail_extension.router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            dependency_names = _dependency_names(route)
            for method in sorted(route.methods or []):
                key = (method, route.path)
                if key not in OPS_MUTATION_ROUTES:
                    continue
                if "require_ops_user" not in dependency_names:
                    missing.add(key)

    assert missing == set()


def test_require_ops_user_rejects_read_only_roles():
    viewer = TokenData(
        user_id="viewer-1",
        email="viewer@example.com",
        organization_id="org-1",
        role="viewer",
        exp=datetime.now(timezone.utc),
    )

    with pytest.raises(HTTPException) as exc_info:
        require_ops_user(viewer)

    # Phase 2.3: require_ops_user now returns the thesis-taxonomy
    # error code naming the required role explicitly.
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "ap_manager_role_required"


def test_get_current_user_requires_auth():
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=None, x_api_key=None, workspace_access_cookie=None)

    assert exc_info.value.status_code == 401
    assert "Not authenticated" in str(exc_info.value.detail)


def test_has_ops_access_allows_operator_roles():
    assert has_ops_access("operator") is True
    assert has_ops_access("admin") is True
    assert has_ops_access("owner") is True
    assert has_ops_access("viewer") is False
