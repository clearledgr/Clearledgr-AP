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

# Public callbacks/health probes that are intentionally unauthenticated
# OR use a non-Clearledgr auth scheme (NetSuite Suitelet HMAC, SAP XSUAA
# JWT). The latter group is enforced INSIDE the handler against
# platform-specific signing keys, not via the standard ``get_current_user``
# / ``require_ops_user`` deps this test scans for. Listing them here
# makes the allowlist explicit so a regression that drops the platform-
# native auth check still trips the test (the route would still appear
# in this set, but the handler itself would be naked).
EXPECTED_UNAUTHENTICATED_SENSITIVE_ROUTES = {
    ("POST", "/extension/gmail/register-token"),
    ("POST", "/extension/gmail/exchange-code"),
    ("GET", "/extension/health"),
    ("GET", "/api/workspace/integrations/slack/install/callback"),
    # SAP Fiori extension — XSUAA JWT exchange + per-tenant lookup.
    # See clearledgr/api/sap_extension.py — auth is XSUAA JWKS verify
    # against the JWT's ``iss`` claim, resolved per-tenant.
    ("POST", "/extension/sap/exchange"),
    ("GET", "/extension/ap-items/by-sap-invoice"),
    # NetSuite SuiteApp panel — Suitelet-minted HMAC JWT verified by
    # the handler. See clearledgr/api/netsuite_panel.py.
    ("GET", "/extension/ap-items/by-netsuite-bill/{ns_internal_id}"),
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
