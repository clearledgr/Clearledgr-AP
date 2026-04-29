"""SAML SSO orchestration (Module 6 Pass C).

Glues together:
  * Per-tenant SAML config (settings_json["saml"]) — loaded /
    saved by ``get_saml_config`` / ``save_saml_config``.
  * SP-initiated login — ``build_authn_request_redirect`` produces
    the URL we redirect the user to (HTTP-Redirect binding).
  * IdP-initiated and SP-completed login — ``handle_assertion``
    validates the SAMLResponse, JIT-provisions the user, applies
    the attribute mapping (role, default entity), records the
    AssertionID for replay protection, and returns the Clearledgr
    user row + a session token.
  * SP metadata XML — ``render_sp_metadata`` produces the XML the
    customer's IdP needs to register Clearledgr as a SAML SP.

Replay protection: we use the existing ``audit_events`` idempotency
key constraint. Inserting an audit row keyed on
``saml:assertion:<AssertionID>`` raises an integrity error on the
second attempt, which we surface as ``replay_detected``.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import xml.sax.saxutils as _xml_escape
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from clearledgr.services.saml_validator import (
    SAMLValidationError,
    ValidatedAssertion,
    parse_and_validate_saml_response,
)

logger = logging.getLogger(__name__)


# ─── Config persistence ────────────────────────────────────────────


@dataclass(frozen=True)
class SAMLConfig:
    """Per-tenant SAML configuration.

    Stored as a JSON blob under ``organizations.settings_json["saml"]``
    rather than a dedicated table — exactly one config per org, no
    versioning needed (rotations replace the row in place after an
    audit event).
    """

    enabled: bool
    idp_entity_id: str
    idp_sso_url: str
    idp_certificate_pem: str
    sp_entity_id: str
    sp_acs_url: str
    attribute_email: str
    attribute_role: Optional[str]
    attribute_entity: Optional[str]
    default_role: str
    default_entity_id: Optional[str]
    jit_provisioning: bool
    # Optional Single Logout endpoint published by the IdP (e.g.
    # https://login.microsoftonline.com/.../saml2/logout). When set,
    # SP-initiated logout redirects here so the IdP also closes its
    # session. Empty/None disables SP-initiated logout (cookie is
    # still cleared locally).
    idp_slo_url: Optional[str] = None
    sp_slo_url: Optional[str] = None

    def to_redacted_dict(self) -> Dict[str, Any]:
        """JSON view safe to return on the GET endpoint.

        The IdP cert is replaced with a fingerprint so admins can
        sanity-check rotations without ever exfiltrating the cert
        through the workspace.
        """
        cert = (self.idp_certificate_pem or "").strip()
        cert_summary = _summarize_cert(cert) if cert else None
        return {
            "enabled": self.enabled,
            "idp_entity_id": self.idp_entity_id,
            "idp_sso_url": self.idp_sso_url,
            "idp_certificate": cert_summary,
            "sp_entity_id": self.sp_entity_id,
            "sp_acs_url": self.sp_acs_url,
            "attribute_email": self.attribute_email,
            "attribute_role": self.attribute_role,
            "attribute_entity": self.attribute_entity,
            "default_role": self.default_role,
            "default_entity_id": self.default_entity_id,
            "jit_provisioning": self.jit_provisioning,
            "idp_slo_url": self.idp_slo_url,
            "sp_slo_url": self.sp_slo_url,
        }


def get_saml_config(db, organization_id: str) -> Optional[SAMLConfig]:
    """Load the persisted SAML config for one tenant. Returns ``None``
    if SAML hasn't been configured."""
    org = db.get_organization(organization_id)
    if not org:
        return None
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return None
    if not isinstance(settings, dict):
        return None
    raw = settings.get("saml") or {}
    if not isinstance(raw, dict) or not raw:
        return None
    return SAMLConfig(
        enabled=bool(raw.get("enabled", False)),
        idp_entity_id=str(raw.get("idp_entity_id") or "").strip(),
        idp_sso_url=str(raw.get("idp_sso_url") or "").strip(),
        idp_certificate_pem=str(raw.get("idp_certificate_pem") or ""),
        sp_entity_id=str(raw.get("sp_entity_id") or "").strip(),
        sp_acs_url=str(raw.get("sp_acs_url") or "").strip(),
        attribute_email=str(raw.get("attribute_email") or "email").strip(),
        attribute_role=(str(raw.get("attribute_role") or "").strip() or None),
        attribute_entity=(str(raw.get("attribute_entity") or "").strip() or None),
        default_role=str(raw.get("default_role") or "ap_clerk").strip().lower(),
        default_entity_id=(str(raw.get("default_entity_id") or "").strip() or None),
        jit_provisioning=bool(raw.get("jit_provisioning", True)),
        idp_slo_url=(str(raw.get("idp_slo_url") or "").strip() or None),
        sp_slo_url=(str(raw.get("sp_slo_url") or "").strip() or None),
    )


def save_saml_config(db, organization_id: str, config: SAMLConfig) -> None:
    """Persist a SAML config under settings_json["saml"].

    Validates required fields here so callers never persist a
    half-configured row that would break login flows. The cert PEM
    must parse as a real X.509 certificate.
    """
    if not config.idp_entity_id:
        raise ValueError("idp_entity_id required")
    if not config.idp_sso_url.startswith("https://"):
        raise ValueError("idp_sso_url must be https://")
    if not config.sp_entity_id:
        raise ValueError("sp_entity_id required")
    if not config.sp_acs_url.startswith("https://"):
        raise ValueError("sp_acs_url must be https://")
    if not config.idp_certificate_pem.strip():
        raise ValueError("idp_certificate_pem required")
    if not _certificate_parses(config.idp_certificate_pem):
        raise ValueError("idp_certificate_pem is not a valid X.509 PEM")
    if not config.attribute_email:
        raise ValueError("attribute_email required")

    org = db.get_organization(organization_id)
    if not org:
        raise ValueError(f"organization {organization_id!r} not found")
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings["saml"] = {
        "enabled": config.enabled,
        "idp_entity_id": config.idp_entity_id,
        "idp_sso_url": config.idp_sso_url,
        "idp_certificate_pem": config.idp_certificate_pem,
        "sp_entity_id": config.sp_entity_id,
        "sp_acs_url": config.sp_acs_url,
        "attribute_email": config.attribute_email,
        "attribute_role": config.attribute_role,
        "attribute_entity": config.attribute_entity,
        "default_role": config.default_role,
        "default_entity_id": config.default_entity_id,
        "jit_provisioning": config.jit_provisioning,
        "idp_slo_url": config.idp_slo_url,
        "sp_slo_url": config.sp_slo_url,
    }
    db.update_organization(organization_id, settings_json=settings)


def delete_saml_config(db, organization_id: str) -> bool:
    """Remove the SAML config from settings_json. Returns True if a
    config existed."""
    org = db.get_organization(organization_id)
    if not org:
        return False
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return False
    if not isinstance(settings, dict) or "saml" not in settings:
        return False
    settings.pop("saml", None)
    db.update_organization(organization_id, settings_json=settings)
    return True


# ─── SP-initiated login: build redirect ─────────────────────────────


def build_authn_request_redirect(
    *,
    config: SAMLConfig,
    relay_state: Optional[str] = None,
) -> Tuple[str, str]:
    """Construct an HTTP-Redirect URL the user should be sent to.

    Returns ``(redirect_url, request_id)``. The ``request_id`` is the
    AuthnRequest ID — caller should stash it somewhere short-lived
    (e.g. a signed cookie) so the ACS handler can confirm InResponseTo
    matches.
    """
    request_id = "_" + secrets.token_hex(16)
    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    authn_request = (
        f'<samlp:AuthnRequest '
        f'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{request_id}" Version="2.0" '
        f'IssueInstant="{issue_instant}" '
        f'Destination="{_xml_escape.quoteattr(config.idp_sso_url)[1:-1]}" '
        f'AssertionConsumerServiceURL="{_xml_escape.quoteattr(config.sp_acs_url)[1:-1]}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer>{_xml_escape.escape(config.sp_entity_id)}</saml:Issuer>'
        f'<samlp:NameIDPolicy AllowCreate="true" '
        f'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"/>'
        f'</samlp:AuthnRequest>'
    )
    # HTTP-Redirect binding: deflate + base64 + URL-encode.
    deflated = zlib.compress(authn_request.encode("utf-8"))[2:-4]  # raw deflate
    encoded = base64.b64encode(deflated).decode("ascii")
    params = [f"SAMLRequest={quote_plus(encoded)}"]
    if relay_state:
        params.append(f"RelayState={quote_plus(relay_state)}")
    sep = "&" if "?" in config.idp_sso_url else "?"
    redirect = config.idp_sso_url + sep + "&".join(params)
    return redirect, request_id


# ─── ACS: validate + JIT provision ──────────────────────────────────


@dataclass
class AssertionOutcome:
    """Result of handling a successful SAML assertion."""

    user_id: str
    user_email: str
    user_role: str
    is_new_user: bool
    assertion_id: str


def handle_assertion(
    *,
    db,
    organization_id: str,
    response_b64: str,
    relay_state: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AssertionOutcome:
    """Full ACS pipeline: validate → JIT-provision → record replay key.

    Raises ``SAMLValidationError`` (with a token-style reason) on any
    validation failure. Caller is expected to translate the token to
    an HTTP error and emit an audit event regardless of outcome.
    """
    config = get_saml_config(db, organization_id)
    if not config:
        raise SAMLValidationError("saml_not_configured")
    if not config.enabled:
        raise SAMLValidationError("saml_disabled")

    assertion = parse_and_validate_saml_response(
        response_b64=response_b64,
        idp_certificate_pem=config.idp_certificate_pem,
        idp_entity_id=config.idp_entity_id,
        sp_entity_id=config.sp_entity_id,
        sp_acs_url=config.sp_acs_url,
        now=now,
    )

    # ─── Replay protection ──────────────────────────────────────
    # AssertionID is the SAML spec's natural unique-per-login key.
    # We pre-check the audit log for any prior insert with the same
    # idempotency key — append_audit_event treats duplicate keys as
    # idempotent and returns the existing row, so a "second call
    # raised" pattern won't catch replay. Explicit pre-check + write
    # is the right shape here.
    replay_key = f"saml:assertion:{assertion.assertion_id}"
    if hasattr(db, "get_ap_audit_event_by_key"):
        try:
            existing = db.get_ap_audit_event_by_key(replay_key)
        except Exception as exc:
            logger.warning(
                "[saml] replay-check DB error for %s: %s",
                assertion.assertion_id, exc,
            )
            existing = None
        if existing:
            raise SAMLValidationError("replay_detected")

    try:
        db.append_audit_event({
            "event_type": "saml_login_attempted",
            "actor_type": "external_idp",
            "actor_id": assertion.name_id,
            "organization_id": organization_id,
            "box_id": assertion.assertion_id,
            "box_type": "saml_assertion",
            "source": "saml_acs",
            "idempotency_key": replay_key,
            "payload_json": {
                "issuer": assertion.issuer,
                "name_id": assertion.name_id,
                "session_index": assertion.session_index,
                "not_on_or_after": assertion.not_on_or_after.isoformat(),
                "audience": assertion.audience,
            },
        })
    except Exception as exc:
        logger.warning(
            "[saml] audit emit failed for assertion %s: %s",
            assertion.assertion_id, exc,
        )
        raise SAMLValidationError("audit_emit_failed") from exc

    # ─── Resolve email from attribute mapping ───────────────────
    email = assertion.get_attribute(config.attribute_email) or ""
    if not email and "@" in assertion.name_id:
        # Fallback: NameID is often the email itself.
        email = assertion.name_id
    email = email.strip().lower()
    if not email:
        raise SAMLValidationError("email_missing")

    # ─── Resolve role ────────────────────────────────────────────
    role = config.default_role
    if config.attribute_role:
        attr_role = assertion.get_attribute(config.attribute_role)
        if attr_role:
            role = _normalize_role(attr_role) or config.default_role

    # ─── Find or JIT-provision ──────────────────────────────────
    user = None
    if hasattr(db, "get_user_by_email"):
        try:
            user = db.get_user_by_email(email)
        except Exception:
            user = None
    is_new_user = False
    if not user or str(user.get("organization_id") or "") != organization_id:
        if not config.jit_provisioning:
            raise SAMLValidationError("jit_disabled_user_unknown")
        if not hasattr(db, "create_user"):
            raise SAMLValidationError("user_provisioning_unavailable")
        user = db.create_user(
            email=email,
            name=email,
            organization_id=organization_id,
            role=role,
        )
        is_new_user = True

    user_id = str(user.get("id") or user.get("user_id") or "")

    # ─── Apply default per-entity assignment if configured ──────
    # The customer can set ``default_entity_id`` so JIT-provisioned
    # users land with a per-entity role override on first login. Only
    # applies on actual provisioning; existing users keep whatever
    # entity assignments the admin set.
    if (
        is_new_user
        and config.default_entity_id
        and hasattr(db, "set_user_entity_role")
    ):
        try:
            db.set_user_entity_role(
                user_id=user_id,
                entity_id=config.default_entity_id,
                organization_id=organization_id,
                role=role,
            )
        except Exception as exc:
            logger.warning(
                "[saml] set_user_entity_role failed for new user %s: %s",
                user_id, exc,
            )

    return AssertionOutcome(
        user_id=user_id,
        user_email=email,
        user_role=role,
        is_new_user=is_new_user,
        assertion_id=assertion.assertion_id,
    )


# ─── SP metadata XML ────────────────────────────────────────────────


def render_sp_metadata(*, sp_entity_id: str, sp_acs_url: str) -> str:
    """Produce the SP metadata XML the IdP needs to register us.

    Minimal but spec-compliant: EntityDescriptor with one
    AssertionConsumerService entry (HTTP-POST binding).
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<EntityDescriptor '
        'xmlns="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{_xml_escape.escape(sp_entity_id)}">'
        '<SPSSODescriptor '
        'AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<AssertionConsumerService '
        'index="0" isDefault="true" '
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{_xml_escape.escape(sp_acs_url)}"/>'
        '</SPSSODescriptor>'
        '</EntityDescriptor>'
    )


# ─── helpers ────────────────────────────────────────────────────────


def _summarize_cert(pem: str) -> Optional[Dict[str, Any]]:
    """Extract a non-secret summary of a PEM cert for the GET endpoint."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        fp = cert.fingerprint(hashes.SHA256())
        return {
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "not_valid_before": cert.not_valid_before_utc.isoformat()
                if hasattr(cert, "not_valid_before_utc")
                else cert.not_valid_before.isoformat(),
            "not_valid_after": cert.not_valid_after_utc.isoformat()
                if hasattr(cert, "not_valid_after_utc")
                else cert.not_valid_after.isoformat(),
            "fingerprint_sha256": fp.hex(":").upper(),
        }
    except Exception as exc:
        logger.debug("[saml] cert summary failed: %s", exc)
        return None


def _certificate_parses(pem: str) -> bool:
    """True iff the PEM string parses as a real X.509 certificate."""
    try:
        from cryptography import x509
        x509.load_pem_x509_certificate(pem.encode("ascii"))
        return True
    except Exception:
        return False


def _normalize_role(token: str) -> Optional[str]:
    """Map a free-form attribute value to a canonical Clearledgr role.

    Handles common IdP conventions: 'ClearledgrAPManager', 'AP-Manager',
    'ap_manager' all map to ``ap_manager``. Unknown tokens return
    ``None`` so callers fall back to the default_role.
    """
    if not token:
        return None
    t = token.strip().lower().replace("-", "_").replace(" ", "_")
    if t.startswith("clearledgr_"):
        t = t[len("clearledgr_"):]
    canonical = {
        "owner",
        "cfo",
        "financial_controller",
        "ap_manager",
        "ap_clerk",
        "read_only",
    }
    return t if t in canonical else None
