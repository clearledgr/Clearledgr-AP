from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def _mounted_paths() -> set[str]:
    paths: set[str] = set()
    for route in app.router.routes:
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str):
            paths.add(route_path)
    return paths


def test_strict_profile_blocks_legacy_surfaces(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)

    with TestClient(app) as client:
        blocked = client.get("/email/tasks")
        assert blocked.status_code == 404
        body = blocked.json()
        assert body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert "/email/tasks" not in _mounted_paths()

        canonical = client.get("/health")
        assert canonical.status_code == 200


def test_legacy_surface_override_reenables_access(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")

    with TestClient(app) as client:
        response = client.get("/email/tasks")
        assert response.status_code != 404
        assert "/email/tasks" in _mounted_paths()


def test_strict_profile_filters_legacy_paths_from_openapi(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)

    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/email/tasks" not in paths
        assert "/audit/trail" not in paths
        assert "/api/agent/intents/preview" in paths
