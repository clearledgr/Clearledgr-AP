"""Route-level auth policy inventory guard.

Ensures sensitive route prefixes remain protected by auth dependencies.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from main import app


SENSITIVE_PREFIXES = (
    "/api/workspace",
    "/api/ops",
    "/api/ap",
    "/api/agent",
    "/extension",
)

# Public callbacks/health probes that are intentionally unauthenticated.
EXPECTED_UNAUTHENTICATED_SENSITIVE_ROUTES = {
    ("POST", "/extension/gmail/register-token"),
    ("POST", "/extension/gmail/exchange-code"),
    ("GET", "/extension/health"),
    ("GET", "/api/workspace/integrations/slack/install/callback"),
}


def test_sensitive_route_inventory_requires_auth_by_default():
    missing = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path
        if not path.startswith(SENSITIVE_PREFIXES):
            continue
        dependency_names = {
            getattr(dep.call, "__name__", "")
            for dep in route.dependant.dependencies
        }
        has_auth_dependency = bool(
            {"get_current_user", "get_optional_user", "require_ops_user", "require_admin_user"}
            & dependency_names
        )
        if has_auth_dependency:
            continue
        for method in sorted(route.methods or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            missing.add((method, path))

    assert missing == EXPECTED_UNAUTHENTICATED_SENSITIVE_ROUTES
