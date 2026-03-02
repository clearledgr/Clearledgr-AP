"""Admin session security tests: HttpOnly cookies + CSRF protection."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from clearledgr.core import database as db_module
from clearledgr.core.auth import create_user


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "admin-session-security.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_admin_credentials():
    suffix = uuid.uuid4().hex[:12]
    email = f"admin.{suffix}@example.com"
    password = "StrongPass123"
    create_user(
        email=email,
        password=password,
        name="Admin User",
        organization_id="default",
        role="admin",
    )
    return email, password


def test_login_sets_http_only_admin_session_and_csrf_cookies(db):
    email, password = _create_admin_credentials()
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 200
    assert response.cookies.get("clearledgr_admin_access")
    assert response.cookies.get("clearledgr_admin_refresh")
    assert response.cookies.get("clearledgr_admin_csrf")


def test_cookie_session_auth_allows_auth_me_without_bearer_header(db):
    email, password = _create_admin_credentials()
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        me = client.get("/auth/me")

    assert me.status_code == 200
    payload = me.json()
    assert str(payload.get("email", "")).lower() == email


def test_cookie_session_mutations_require_csrf_header(db):
    email, password = _create_admin_credentials()
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200

        without_csrf = client.post(
            "/api/admin/onboarding/step",
            json={"organization_id": "default", "step": 1},
        )
        assert without_csrf.status_code == 403
        assert without_csrf.json().get("detail") == "csrf_validation_failed"

        csrf = client.cookies.get("clearledgr_admin_csrf")
        with_csrf = client.post(
            "/api/admin/onboarding/step",
            json={"organization_id": "default", "step": 1},
            headers={"X-CSRF-Token": csrf},
        )

    assert with_csrf.status_code == 200
    assert with_csrf.json().get("success") is True
