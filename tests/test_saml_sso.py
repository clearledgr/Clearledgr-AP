"""Tests for Module 6 Pass C — SAML SSO.

The SAML validator is security-critical — every check it makes is a
defence against a different attack class:
  * Signature verification → assertion forgery.
  * NotOnOrAfter → assertion replay outside the issuance window.
  * AudienceRestriction → cross-SP token reuse.
  * Issuer pinning → IdP-impersonation.
  * Replay protection (idempotency_key on AssertionID) → identical-
    assertion replay inside the issuance window.

These tests build a self-signed RSA cert + an in-memory SAML
assertion, sign it with signxml, and exercise the validator end-to-
end. The signing path mirrors what a real IdP does, so any future
spec drift in signxml is caught here.
"""
from __future__ import annotations

import base64
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.saml import (  # noqa: E402
    saml_admin_router,
    saml_public_router,
)
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services import saml_sso  # noqa: E402
from clearledgr.services.saml_validator import (  # noqa: E402
    SAMLValidationError,
    parse_and_validate_saml_response,
)


# ─── Cert + assertion builder ───────────────────────────────────────


def _make_self_signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-idp.example"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key, cert, key_pem, cert_pem


def _build_signed_response_b64(
    *,
    key,
    cert,
    issuer: str,
    audience: str,
    destination: str,
    name_id: str,
    email: str,
    role=None,
    not_before_offset_seconds: int = -60,
    not_on_or_after_offset_seconds: int = 600,
    assertion_id=None,
):
    """Construct a Response with a single signed Assertion."""
    from signxml import XMLSigner
    from lxml import etree

    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_before = (
        datetime.now(timezone.utc) + timedelta(seconds=not_before_offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_on_or_after = (
        datetime.now(timezone.utc) + timedelta(seconds=not_on_or_after_offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    aid = assertion_id or "_" + uuid.uuid4().hex
    rid = "_" + uuid.uuid4().hex

    role_attr = ""
    if role:
        role_attr = (
            '<saml:Attribute Name="role">'
            f'<saml:AttributeValue>{role}</saml:AttributeValue>'
            '</saml:Attribute>'
        )

    xml = (
        '<samlp:Response '
        'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{rid}" Version="2.0" '
        f'IssueInstant="{issue_instant}" '
        f'Destination="{destination}">'
        f'<saml:Issuer>{issuer}</saml:Issuer>'
        '<samlp:Status>'
        '<samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>'
        '</samlp:Status>'
        f'<saml:Assertion ID="{aid}" Version="2.0" IssueInstant="{issue_instant}">'
        f'<saml:Issuer>{issuer}</saml:Issuer>'
        '<saml:Subject>'
        f'<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id}</saml:NameID>'
        '</saml:Subject>'
        '<saml:Conditions '
        f'NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">'
        '<saml:AudienceRestriction>'
        f'<saml:Audience>{audience}</saml:Audience>'
        '</saml:AudienceRestriction>'
        '</saml:Conditions>'
        '<saml:AuthnStatement '
        f'AuthnInstant="{issue_instant}" SessionIndex="sess-1">'
        '<saml:AuthnContext>'
        '<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>'
        '</saml:AuthnContext>'
        '</saml:AuthnStatement>'
        '<saml:AttributeStatement>'
        f'<saml:Attribute Name="email">'
        f'<saml:AttributeValue>{email}</saml:AttributeValue>'
        '</saml:Attribute>'
        f'{role_attr}'
        '</saml:AttributeStatement>'
        '</saml:Assertion>'
        '</samlp:Response>'
    )

    root = etree.fromstring(xml)

    signer = XMLSigner(
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
    )
    # signxml expects ``cert`` as a list of PEM strings.
    cert_pem_str = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    # Sign the whole Response — most real IdPs do this. The validator
    # path handles both Assertion-signed and Response-signed shapes.
    signed = signer.sign(root, key=key, cert=[cert_pem_str])
    full = etree.tostring(signed, xml_declaration=False)
    return base64.b64encode(full).decode("ascii"), aid


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def keypair():
    return _make_self_signed()


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


@pytest.fixture()
def saml_config(db, keypair):
    _key, _cert, _key_pem, cert_pem = keypair
    cfg = saml_sso.SAMLConfig(
        enabled=True,
        idp_entity_id="https://idp.example.test",
        idp_sso_url="https://idp.example.test/sso",
        idp_certificate_pem=cert_pem,
        sp_entity_id="https://workspace.clearledgr.test/saml/default",
        sp_acs_url="https://workspace.clearledgr.test/saml/default/acs",
        attribute_email="email",
        attribute_role="role",
        attribute_entity=None,
        default_role="ap_clerk",
        default_entity_id=None,
        jit_provisioning=True,
    )
    saml_sso.save_saml_config(db, "default", cfg)
    return cfg


def _user(role: str = "owner", uid: str = "owner-user"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=uid,
        organization_id="default",
        role=role,
    )


@pytest.fixture()
def admin_client():
    app = FastAPI()
    app.include_router(saml_admin_router)
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


@pytest.fixture()
def public_client():
    app = FastAPI()
    app.include_router(saml_public_router)
    return TestClient(app)


# ─── Validator unit tests ───────────────────────────────────────────


def test_valid_response_returns_assertion(keypair):
    key, cert, _kpem, cpem = keypair
    response_b64, aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://sp.example.test",
        destination="https://sp.example.test/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
        role="ap_manager",
    )
    out = parse_and_validate_saml_response(
        response_b64=response_b64,
        idp_certificate_pem=cpem,
        idp_entity_id="https://idp.example.test",
        sp_entity_id="https://sp.example.test",
        sp_acs_url="https://sp.example.test/acs",
    )
    assert out.assertion_id == aid
    assert out.name_id == "alice@acme.test"
    assert out.get_attribute("email") == "alice@acme.test"
    assert out.get_attribute("role") == "ap_manager"


def test_signature_invalid_when_cert_swapped(keypair):
    key, cert, _kpem, cpem = keypair
    # Build with the real cert ...
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://sp.example.test",
        destination="https://sp.example.test/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
    )
    # ... but verify against a different cert.
    _other_key, _other_cert, _, other_cpem = _make_self_signed()
    with pytest.raises(SAMLValidationError) as excinfo:
        parse_and_validate_saml_response(
            response_b64=response_b64,
            idp_certificate_pem=other_cpem,
            idp_entity_id="https://idp.example.test",
            sp_entity_id="https://sp.example.test",
            sp_acs_url="https://sp.example.test/acs",
        )
    assert str(excinfo.value) == "signature_invalid"


def test_audience_mismatch_rejected(keypair):
    key, cert, _kpem, cpem = keypair
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://attacker.example",
        destination="https://sp.example.test/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        parse_and_validate_saml_response(
            response_b64=response_b64,
            idp_certificate_pem=cpem,
            idp_entity_id="https://idp.example.test",
            sp_entity_id="https://sp.example.test",
            sp_acs_url="https://sp.example.test/acs",
        )
    assert str(excinfo.value) == "audience_mismatch"


def test_expired_assertion_rejected(keypair):
    key, cert, _kpem, cpem = keypair
    # NotOnOrAfter five minutes ago
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://sp.example.test",
        destination="https://sp.example.test/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
        not_before_offset_seconds=-3600,
        not_on_or_after_offset_seconds=-300,
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        parse_and_validate_saml_response(
            response_b64=response_b64,
            idp_certificate_pem=cpem,
            idp_entity_id="https://idp.example.test",
            sp_entity_id="https://sp.example.test",
            sp_acs_url="https://sp.example.test/acs",
        )
    assert str(excinfo.value) == "expired"


def test_issuer_mismatch_rejected(keypair):
    key, cert, _kpem, cpem = keypair
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://other-idp.example.test",
        audience="https://sp.example.test",
        destination="https://sp.example.test/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        parse_and_validate_saml_response(
            response_b64=response_b64,
            idp_certificate_pem=cpem,
            idp_entity_id="https://idp.example.test",
            sp_entity_id="https://sp.example.test",
            sp_acs_url="https://sp.example.test/acs",
        )
    assert str(excinfo.value) == "issuer_mismatch"


def test_destination_mismatch_rejected(keypair):
    key, cert, _kpem, cpem = keypair
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://sp.example.test",
        destination="https://attacker.example/acs",
        name_id="alice@acme.test",
        email="alice@acme.test",
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        parse_and_validate_saml_response(
            response_b64=response_b64,
            idp_certificate_pem=cpem,
            idp_entity_id="https://idp.example.test",
            sp_entity_id="https://sp.example.test",
            sp_acs_url="https://sp.example.test/acs",
        )
    assert str(excinfo.value) == "destination_mismatch"


# ─── handle_assertion + JIT provisioning ────────────────────────────


def test_handle_assertion_provisions_user(db, saml_config, keypair):
    key, cert, _kpem, _cpem = keypair
    response_b64, aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer=saml_config.idp_entity_id,
        audience=saml_config.sp_entity_id,
        destination=saml_config.sp_acs_url,
        name_id="bob@acme.test",
        email="bob@acme.test",
        role="ap_manager",
    )
    out = saml_sso.handle_assertion(
        db=db, organization_id="default", response_b64=response_b64,
    )
    assert out.user_email == "bob@acme.test"
    assert out.user_role == "ap_manager"
    assert out.is_new_user is True
    assert out.assertion_id == aid


def test_handle_assertion_replay_detected(db, saml_config, keypair):
    key, cert, _kpem, _cpem = keypair
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer=saml_config.idp_entity_id,
        audience=saml_config.sp_entity_id,
        destination=saml_config.sp_acs_url,
        name_id="charlie@acme.test",
        email="charlie@acme.test",
    )
    saml_sso.handle_assertion(
        db=db, organization_id="default", response_b64=response_b64,
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        saml_sso.handle_assertion(
            db=db, organization_id="default", response_b64=response_b64,
        )
    assert str(excinfo.value) == "replay_detected"


def test_handle_assertion_blocks_when_jit_disabled_and_user_unknown(db, keypair):
    """If jit_provisioning is False and the user doesn't exist locally,
    the SAML login is rejected — admins must invite first."""
    key, cert, _kpem, cpem = keypair
    cfg = saml_sso.SAMLConfig(
        enabled=True,
        idp_entity_id="https://idp.example.test",
        idp_sso_url="https://idp.example.test/sso",
        idp_certificate_pem=cpem,
        sp_entity_id="https://sp2.example.test",
        sp_acs_url="https://sp2.example.test/acs",
        attribute_email="email",
        attribute_role=None,
        attribute_entity=None,
        default_role="ap_clerk",
        default_entity_id=None,
        jit_provisioning=False,
    )
    saml_sso.save_saml_config(db, "default", cfg)
    response_b64, _aid = _build_signed_response_b64(
        key=key, cert=cert,
        issuer="https://idp.example.test",
        audience="https://sp2.example.test",
        destination="https://sp2.example.test/acs",
        name_id="newcomer@acme.test",
        email="newcomer@acme.test",
    )
    with pytest.raises(SAMLValidationError) as excinfo:
        saml_sso.handle_assertion(
            db=db, organization_id="default", response_b64=response_b64,
        )
    assert str(excinfo.value) == "jit_disabled_user_unknown"


# ─── Admin endpoints ────────────────────────────────────────────────


def test_admin_get_returns_unconfigured(db, admin_client):
    # Wipe any saved config first.
    saml_sso.delete_saml_config(db, "default")
    resp = admin_client.get("/api/workspace/saml/config?organization_id=default")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False


def test_admin_put_persists_redacted_cert(db, admin_client, keypair):
    _k, _c, _kp, cpem = keypair
    body = {
        "enabled": True,
        "idp_entity_id": "https://idp.example.test",
        "idp_sso_url": "https://idp.example.test/sso",
        "idp_certificate_pem": cpem,
        "sp_entity_id": "https://sp.example.test",
        "sp_acs_url": "https://sp.example.test/acs",
        "attribute_email": "email",
        "default_role": "ap_clerk",
    }
    resp = admin_client.put(
        "/api/workspace/saml/config?organization_id=default",
        json=body,
    )
    assert resp.status_code == 200, resp.text
    redacted = resp.json()["config"]
    # Cert is replaced with a fingerprint summary
    assert isinstance(redacted["idp_certificate"], dict)
    assert "fingerprint_sha256" in redacted["idp_certificate"]
    assert "BEGIN CERTIFICATE" not in str(redacted["idp_certificate"])

    # Audit row recorded
    events = db.search_audit_events(
        organization_id="default",
        event_types=["saml_config_updated"],
    )
    assert any(e.get("box_id") == "default" for e in events.get("events", []))


def test_admin_put_rejects_bad_cert(db, admin_client):
    # Bad cert PEM that is long enough to clear the Pydantic
    # min_length=64 gate, but doesn't actually parse as X.509 — the
    # service-layer cert validator is what should reject it.
    bad_cert = "-----BEGIN CERTIFICATE-----\n" + ("GARBAGE" * 20) + "\n-----END CERTIFICATE-----"
    body = {
        "enabled": True,
        "idp_entity_id": "https://idp.example.test",
        "idp_sso_url": "https://idp.example.test/sso",
        "idp_certificate_pem": bad_cert,
        "sp_entity_id": "https://sp.example.test",
        "sp_acs_url": "https://sp.example.test/acs",
        "attribute_email": "email",
        "default_role": "ap_clerk",
    }
    resp = admin_client.put(
        "/api/workspace/saml/config?organization_id=default",
        json=body,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "validation_failed"


def test_admin_put_rejects_http_sso_url(db, admin_client, keypair):
    _k, _c, _kp, cpem = keypair
    body = {
        "enabled": True,
        "idp_entity_id": "https://idp.example.test",
        "idp_sso_url": "http://idp.example.test/sso",  # not https
        "idp_certificate_pem": cpem,
        "sp_entity_id": "https://sp.example.test",
        "sp_acs_url": "https://sp.example.test/acs",
        "attribute_email": "email",
        "default_role": "ap_clerk",
    }
    resp = admin_client.put(
        "/api/workspace/saml/config?organization_id=default",
        json=body,
    )
    assert resp.status_code == 422


# ─── Public endpoints ───────────────────────────────────────────────


def test_metadata_endpoint_returns_xml(db, public_client, saml_config):
    resp = public_client.get("/saml/default/sp-metadata")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/samlmetadata+xml")
    assert "EntityDescriptor" in resp.text
    assert saml_config.sp_entity_id in resp.text


def test_metadata_404_when_unconfigured(db, public_client):
    saml_sso.delete_saml_config(db, "default")
    resp = public_client.get("/saml/default/sp-metadata")
    assert resp.status_code == 404


def test_login_endpoint_redirects_to_idp(db, public_client, saml_config):
    resp = public_client.get(
        "/saml/default/login", follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(saml_config.idp_sso_url)
    assert "SAMLRequest=" in resp.headers["location"]


def test_acs_invalid_signature_emits_failure_audit(
    db, public_client, saml_config, keypair,
):
    """ACS with a forged response → 401 + saml_login_failed audit event."""
    # Build with a different keypair (forged signature)
    other_key, other_cert, _, _ = _make_self_signed()
    response_b64, _aid = _build_signed_response_b64(
        key=other_key, cert=other_cert,
        issuer=saml_config.idp_entity_id,
        audience=saml_config.sp_entity_id,
        destination=saml_config.sp_acs_url,
        name_id="attacker@evil.test",
        email="attacker@evil.test",
    )
    resp = public_client.post(
        "/saml/default/acs",
        data={"SAMLResponse": response_b64},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "signature_invalid"
    events = db.search_audit_events(
        organization_id="default",
        event_types=["saml_login_failed"],
    )
    assert events.get("events"), "expected saml_login_failed audit event"
