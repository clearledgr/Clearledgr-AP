from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _runtime_surface_contract, app


def _mounted_paths() -> set[str]:
    paths: set[str] = set()
    for route in app.router.routes:
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str):
            paths.add(route_path)
    return paths


def test_strict_profile_blocks_legacy_surfaces(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(app) as client:
        blocked = client.get("/email/tasks")
        assert blocked.status_code == 404
        body = blocked.json()
        assert body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert "/email/tasks" not in _mounted_paths()

        outlook_blocked = client.get("/outlook/status/user-1")
        assert outlook_blocked.status_code == 404
        outlook_body = outlook_blocked.json()
        assert outlook_body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert outlook_body["reason"] == "non_canonical_surface_disabled"

        config_blocked = client.get("/config/organizations/default")
        assert config_blocked.status_code == 404
        assert config_blocked.json()["detail"] == "endpoint_disabled_in_ap_v1_profile"

        erp_legacy_blocked = client.get("/erp/status/default")
        assert erp_legacy_blocked.status_code == 404
        assert erp_legacy_blocked.json()["detail"] == "endpoint_disabled_in_ap_v1_profile"

        canonical = client.get("/health")
        assert canonical.status_code == 200


def test_strict_profile_contract_ignores_legacy_runtime_flags(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "false")
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")
    monkeypatch.setenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", "true")

    contract = _runtime_surface_contract()
    assert contract["production_like"] is True
    assert contract["strict_requested"] is True
    assert contract["strict_forced_on_in_production"] is False
    assert contract["strict_effective"] is True
    assert contract["legacy_override_requested"] is True
    assert contract["legacy_override_effective"] is False
    warnings = set(contract.get("warnings") or [])
    assert "legacy_override_ignored_strict_ap_v1" in warnings
    assert "strict_disable_request_ignored_strict_ap_v1" in warnings
    assert "allow_legacy_in_production_ignored_strict_ap_v1" in warnings

    with TestClient(app) as client:
        response = client.get("/email/tasks")
        assert response.status_code == 404
        body = response.json()
        assert body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert "/email/tasks" not in _mounted_paths()

        mounted = _mounted_paths()
        assert "/outlook/status/{user_id}" not in mounted


def test_legacy_surface_override_does_not_restore_deleted_legacy_routes(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "false")
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")
    monkeypatch.setenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", "true")

    with TestClient(app) as client:
        response = client.get("/email/tasks")
        assert response.status_code == 404
        assert "/email/tasks" not in _mounted_paths()


def test_strict_profile_filters_legacy_paths_from_openapi(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/email/tasks" not in paths
        assert "/audit/trail" not in paths
        assert "/outlook/status/{user_id}" not in paths
        assert "/config/organizations/{organization_id}" not in paths
        assert "/erp/status/{organization_id}" not in paths
        assert "/api/agent/intents/preview" in paths


def test_strict_profile_route_surface_is_minimized(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(app) as _client:
        paths = _mounted_paths()
        assert len(paths) <= 150
        assert not any(path.startswith("/config/") for path in paths)
        assert "/erp/status/{organization_id}" not in paths
        assert "/erp/quickbooks/connect" not in paths
        assert "/erp/xero/connect" not in paths
        assert "/api/admin/vendor-intelligence/bootstrap" not in paths
        assert "/api/admin/integrations/slack/manifest" not in paths
        # OAuth callbacks remain available for admin ERP install flows.
        assert "/erp/quickbooks/callback" in paths
        assert "/erp/xero/callback" in paths
