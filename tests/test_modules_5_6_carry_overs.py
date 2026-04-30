"""Tests for Modules 5 + 6 carry-overs:
  * ERP test-connection endpoint (per ERP)
  * ERP credential rotation
  * SAML SLO (SP-initiated logout, IdP-initiated callback)
  * Invite-time entity-restriction propagation on accept
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import erp_connection_ops as erp_ops_routes  # noqa: E402
from clearledgr.api import saml as saml_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user, ROLE_OWNER  # noqa: E402
from clearledgr.integrations.erp_router import (  # noqa: E402
    ERPConnection,
    set_erp_connection,
)
from clearledgr.services import saml_sso  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    return inst


def _user(uid: str = "owner-1", role: str = ROLE_OWNER) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid, email=f"{uid}@orgA.com",
        organization_id="orgA", role=role,
    )


@pytest.fixture()
def client_erp_ops(db):
    app = FastAPI()
    app.include_router(erp_ops_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app)


@pytest.fixture()
def client_saml(db):
    app = FastAPI()
    app.include_router(saml_routes.saml_admin_router)
    app.include_router(saml_routes.saml_public_router)
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app)


# ─── ERP test-connection ────────────────────────────────────────────


def _seed_erp_connection(org: str, *, erp_type: str, **fields) -> None:
    conn = ERPConnection(
        type=erp_type,
        access_token=fields.get("access_token", "tok"),
        refresh_token=fields.get("refresh_token", "rtok"),
        client_id=fields.get("client_id"),
        client_secret=fields.get("client_secret"),
        realm_id=fields.get("realm_id"),
        tenant_id=fields.get("tenant_id"),
        base_url=fields.get("base_url"),
        company_code=fields.get("company_code"),
        account_id=fields.get("account_id"),
        consumer_key=fields.get("consumer_key"),
        consumer_secret=fields.get("consumer_secret"),
        token_id=fields.get("token_id"),
        token_secret=fields.get("token_secret"),
        webhook_secret=fields.get("webhook_secret"),
    )
    set_erp_connection(org, conn)


def _patch_http_get(captured: list, status: int = 200, body: dict = None):
    body = body or {}

    class FakeResp:
        status_code = status

        def json(self):
            return body

        @property
        def text(self):
            return ""

    async def fake_get(url, headers=None, timeout=None, params=None, **kwargs):
        captured.append({"url": url, "headers": headers})
        return FakeResp()

    return patch(
        "clearledgr.core.http_client.get_http_client",
        return_value=SimpleNamespace(get=fake_get, post=fake_get),
    )


def test_erp_test_unsupported_type_400(db, client_erp_ops):
    resp = client_erp_ops.post("/api/workspace/integrations/erp/oracle/test")
    assert resp.status_code == 400


def test_erp_test_no_connection_404(db, client_erp_ops):
    resp = client_erp_ops.post("/api/workspace/integrations/erp/quickbooks/test")
    assert resp.status_code == 404


def test_erp_test_quickbooks_success(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="quickbooks", realm_id="9999")
    captured: list = []
    body = {"CompanyInfo": {"CompanyName": "Acme Inc", "Country": "US"}}
    with _patch_http_get(captured, 200, body):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/quickbooks/test",
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["response_summary"]["company_name"] == "Acme Inc"
    assert "/companyinfo/9999" in captured[0]["url"]


def test_erp_test_quickbooks_token_expired(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="quickbooks", realm_id="9999")
    captured: list = []
    with _patch_http_get(captured, 401, {}):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/quickbooks/test",
        )
    data = resp.json()
    assert data["ok"] is False
    assert data["detail"] == "token_expired"


def test_erp_test_xero_success(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="xero", tenant_id="tnt-1")
    captured: list = []
    body = {"Organisations": [{"Name": "Acme UK Ltd", "CountryCode": "GB"}]}
    with _patch_http_get(captured, 200, body):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/xero/test",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["response_summary"]["organisation_name"] == "Acme UK Ltd"
    assert "Organisation" in captured[0]["url"]


def test_erp_test_netsuite_success(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="netsuite", account_id="123456")
    captured: list = []
    body = {"items": [{"id": "v-1"}], "totalResults": 1}
    with _patch_http_get(captured, 200, body):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/netsuite/test",
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "vendor?limit=1" in captured[0]["url"]


def test_erp_test_sap_b1_routes_to_b1_endpoint(db, client_erp_ops):
    _seed_erp_connection(
        "orgA", erp_type="sap",
        base_url="https://sap-b1.example.com:50000/b1s/v1",
        company_code="1000",
    )
    captured: list = []
    body = {"value": [{"CardCode": "V001"}]}
    with _patch_http_get(captured, 200, body):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/sap/test",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["response_summary"]["flavor"] == "b1"
    assert "BusinessPartners" in captured[0]["url"]


def test_erp_test_sap_s4hana_routes_to_api_business_partner(db, client_erp_ops):
    _seed_erp_connection(
        "orgA", erp_type="sap",
        base_url="https://my-s4.api.sap",
        company_code="1000",
    )
    captured: list = []
    body = {"d": {"results": [{"BusinessPartner": "1"}]}}
    with _patch_http_get(captured, 200, body):
        resp = client_erp_ops.post(
            "/api/workspace/integrations/erp/sap/test",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["response_summary"]["flavor"] == "s4hana"
    assert "API_BUSINESS_PARTNER" in captured[0]["url"]


def test_erp_test_emits_audit(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="quickbooks", realm_id="9999")
    body = {"CompanyInfo": {"CompanyName": "X", "Country": "US"}}
    with _patch_http_get([], 200, body):
        client_erp_ops.post(
            "/api/workspace/integrations/erp/quickbooks/test",
        )
    events = db.list_box_audit_events(
        "erp_connection",
        "erp_connection:quickbooks:orgA",
    )
    types = [e.get("event_type") for e in events]
    assert "erp_connection_test_ok" in types


# ─── ERP credential rotation ───────────────────────────────────────


def test_erp_rotate_unsupported_400(db, client_erp_ops):
    resp = client_erp_ops.post(
        "/api/workspace/integrations/erp/oracle/rotate-credentials",
        json={"access_token": "new"},
    )
    assert resp.status_code == 400


def test_erp_rotate_no_connection_404(db, client_erp_ops):
    resp = client_erp_ops.post(
        "/api/workspace/integrations/erp/quickbooks/rotate-credentials",
        json={"access_token": "new"},
    )
    assert resp.status_code == 404


def test_erp_rotate_no_fields_400(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="quickbooks", realm_id="9999")
    resp = client_erp_ops.post(
        "/api/workspace/integrations/erp/quickbooks/rotate-credentials",
        json={},
    )
    assert resp.status_code == 400


def test_erp_rotate_quickbooks_partial(db, client_erp_ops):
    _seed_erp_connection(
        "orgA", erp_type="quickbooks",
        access_token="old", refresh_token="old-rt", realm_id="9999",
    )
    resp = client_erp_ops.post(
        "/api/workspace/integrations/erp/quickbooks/rotate-credentials",
        json={"access_token": "new", "refresh_token": "new-rt"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data["fields_updated"]
    assert "refresh_token" in data["fields_updated"]
    # realm_id stays unchanged
    assert "realm_id" not in data["fields_updated"]


def test_erp_rotate_audit_does_not_log_secret_values(db, client_erp_ops):
    _seed_erp_connection("orgA", erp_type="xero", tenant_id="t")
    client_erp_ops.post(
        "/api/workspace/integrations/erp/xero/rotate-credentials",
        json={"access_token": "secret-xyz", "refresh_token": "sec-rt"},
    )
    events = db.list_box_audit_events(
        "erp_connection", "erp_connection:xero:orgA",
    )
    rotate_events = [
        e for e in events if e.get("event_type") == "erp_credentials_rotated"
    ]
    assert rotate_events
    payload = rotate_events[0].get("payload_json") or {}
    payload_str = str(payload)
    assert "secret-xyz" not in payload_str
    assert "sec-rt" not in payload_str
    assert "fields_rotated" in payload


# ─── SAML SLO ──────────────────────────────────────────────────────


def _seed_saml_config(db, *, idp_slo_url=None, sp_slo_url=None) -> None:
    cfg = saml_sso.SAMLConfig(
        enabled=True,
        idp_entity_id="https://idp.example/saml",
        idp_sso_url="https://idp.example/sso",
        idp_certificate_pem=_DUMMY_CERT_PEM,
        sp_entity_id="https://clearledgr.com/saml/orgA",
        sp_acs_url="https://api.clearledgr.com/saml/orgA/acs",
        attribute_email="email",
        attribute_role=None,
        attribute_entity=None,
        default_role="ap_clerk",
        default_entity_id=None,
        jit_provisioning=True,
        idp_slo_url=idp_slo_url,
        sp_slo_url=sp_slo_url,
    )
    saml_sso.save_saml_config(db, "orgA", cfg)


def _make_test_cert() -> str:
    """Generate a self-signed X.509 cert PEM at test time. Real SAML
    setups upload the IdP's signing cert; tests just need a parsable
    PEM since the SAML config validator calls ``_certificate_parses``."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


_DUMMY_CERT_PEM = _make_test_cert()


def test_saml_logout_no_config_clears_cookie_and_redirects(db, client_saml):
    resp = client_saml.get(
        "/saml/orgA/logout", follow_redirects=False,
    )
    assert resp.status_code == 302
    # Cookie cleared (Set-Cookie with deletion flag)
    set_cookie = resp.headers.get("set-cookie", "")
    assert "clearledgr_session" in set_cookie
    assert resp.headers["location"].endswith("/workspace/login")


def test_saml_logout_with_idp_slo_url_redirects_there(db, client_saml):
    _seed_saml_config(db, idp_slo_url="https://idp.example/saml/logout")
    resp = client_saml.get(
        "/saml/orgA/logout", follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://idp.example/saml/logout"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "clearledgr_session" in set_cookie


def test_saml_logout_emits_audit(db, client_saml):
    _seed_saml_config(db, idp_slo_url="https://idp.example/saml/logout")
    client_saml.get("/saml/orgA/logout", follow_redirects=False)
    events = db.list_box_audit_events("saml_config", "orgA")
    types = [e.get("event_type") for e in events]
    assert "saml_logout_initiated" in types


def test_saml_slo_callback_clears_cookie_logout_request(db, client_saml):
    _seed_saml_config(db)
    resp = client_saml.post(
        "/saml/orgA/slo",
        data={"SAMLRequest": "<base64-saml-request>", "RelayState": "/x"},
    )
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "clearledgr_session" in set_cookie


def test_saml_slo_callback_classifies_logout_request_vs_response(db, client_saml):
    _seed_saml_config(db)
    client_saml.post(
        "/saml/orgA/slo",
        data={"SAMLResponse": "<base64-resp>"},
    )
    events = db.list_box_audit_events("saml_config", "orgA")
    callbacks = [
        e for e in events
        if e.get("event_type") == "saml_logout_callback_received"
    ]
    assert callbacks
    payload = callbacks[0].get("payload_json") or {}
    if isinstance(payload, str):
        import json as _json
        payload = _json.loads(payload)
    assert payload.get("direction") == "logout_response"


def test_saml_admin_put_persists_slo_urls(db, client_saml):
    """Admin PUT body now accepts idp_slo_url + sp_slo_url and they
    round-trip through the GET."""
    body = {
        "enabled": True,
        "idp_entity_id": "https://idp.example/saml",
        "idp_sso_url": "https://idp.example/sso",
        "idp_certificate_pem": _DUMMY_CERT_PEM,
        "sp_entity_id": "https://clearledgr.com/saml/orgA",
        "sp_acs_url": "https://api.clearledgr.com/saml/orgA/acs",
        "idp_slo_url": "https://idp.example/saml/logout",
        "sp_slo_url": "https://api.clearledgr.com/saml/orgA/slo",
    }
    put_resp = client_saml.put(
        "/api/workspace/saml/config", json=body,
    )
    assert put_resp.status_code == 200, put_resp.text
    get_resp = client_saml.get("/api/workspace/saml/config")
    cfg = get_resp.json()["config"]
    assert cfg["idp_slo_url"] == "https://idp.example/saml/logout"
    assert cfg["sp_slo_url"] == "https://api.clearledgr.com/saml/orgA/slo"


# ─── Invite-time entity scoping ────────────────────────────────────


def test_accept_team_invite_propagates_entity_restrictions(db):
    """An invite created with entity_restrictions should result in
    user_entity_roles rows being written when the invite is accepted."""
    # Create the user first (the accept handler doesn't create — it
    # just flips status + scopes the existing user).
    user = db.create_user(
        email="alice@orgA.com",
        name="Alice",
        organization_id="orgA",
        role="ap_clerk",
    )
    invite = db.create_team_invite(
        organization_id="orgA",
        email="alice@orgA.com",
        role="ap_manager",
        created_by="owner-1",
        expires_at=None,
        entity_restrictions=["EU", "UK"],
    )
    accepted = db.accept_team_invite(invite["id"], accepted_by=user["id"])
    assert accepted is True

    roles = db.list_user_entity_roles(user["id"])
    entity_ids = {r["entity_id"] for r in roles}
    assert entity_ids == {"EU", "UK"}
    for r in roles:
        assert r["role"] == "ap_manager"


def test_accept_team_invite_no_restrictions_no_entity_roles(db):
    """An invite without entity_restrictions leaves the user with
    org-wide access (no per-entity row written)."""
    user = db.create_user(
        email="bob@orgA.com",
        name="Bob",
        organization_id="orgA",
        role="ap_clerk",
    )
    invite = db.create_team_invite(
        organization_id="orgA",
        email="bob@orgA.com",
        role="ap_clerk",
        created_by="owner-1",
        expires_at=None,
    )
    db.accept_team_invite(invite["id"], accepted_by=user["id"])
    roles = db.list_user_entity_roles(user["id"])
    assert roles == []


def test_accept_team_invite_idempotent(db):
    """Calling accept twice is a no-op on the second call."""
    user = db.create_user(
        email="carol@orgA.com",
        name="Carol",
        organization_id="orgA",
        role="ap_clerk",
    )
    invite = db.create_team_invite(
        organization_id="orgA",
        email="carol@orgA.com",
        role="ap_manager",
        created_by="owner-1",
        expires_at=None,
        entity_restrictions=["EU"],
    )
    first = db.accept_team_invite(invite["id"], accepted_by=user["id"])
    second = db.accept_team_invite(invite["id"], accepted_by=user["id"])
    assert first is True
    assert second is False
