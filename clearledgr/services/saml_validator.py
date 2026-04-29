"""SAML 2.0 response validator (Module 6 Pass C).

Clearledgr's SAML SSO accepts SP-initiated and IdP-initiated flows
from any SAML 2.0 IdP (Azure AD, Okta, Google Workspace, OneLogin,
generic). Per scope §Module 6 §220 the design point is *bounded* and
*safe by default* — every assertion runs through this validator
before a session is issued. There is no path that skips signature
verification.

What this module does NOT do:
  * Construct AuthnRequests (that lives in saml_sso.py — small,
    just an XML template + base64 encode).
  * Parse Metadata XML (we accept the IdP's signing cert as a PEM
    string in the per-tenant config, not as Metadata XML — keeps
    the trust boundary small).
  * Single Logout (SLO is post-GA per scope).

What this module DOES:
  * Parse SAMLResponse XML safely (defusedxml entry to reject DTDs +
    entity expansion).
  * Verify the XML signature using the customer's pinned IdP cert
    (signxml), enforcing exclusive-c14n + the algorithms the major
    IdPs all advertise.
  * Validate StatusCode == Success.
  * Validate Issuer matches the configured IdP entity id.
  * Validate Conditions: NotBefore <= now <= NotOnOrAfter (with a
    small clock skew window), AudienceRestriction includes our SP
    entity id, OneTimeUse is honoured (treated as "must not have
    been seen before" — the caller does that via audit-event
    idempotency).
  * Extract NameID (used as the canonical user identifier) and
    AttributeStatement (used for role + entity mapping).

The module returns a frozen ``ValidatedAssertion`` that callers can
treat as ground truth — every field has been signature-verified and
time-checked.
"""
from __future__ import annotations

import base64
import logging
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)

# Tolerated clock skew between Clearledgr and the IdP. 60s is what
# the major IdPs (Azure AD, Okta) recommend.
_CLOCK_SKEW_SECONDS = 60

# SAML 2.0 namespaces — pinned so we don't accept assertions whose
# IdP shifts to non-standard namespaces.
_NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}


class SAMLValidationError(Exception):
    """Raised when the SAML response fails any check.

    The error message is a short token suitable for audit logging
    (``signature_invalid``, ``audience_mismatch``, ...). The full
    diagnostic detail is logged at debug level on the validator's
    logger.
    """


@dataclass(frozen=True)
class ValidatedAssertion:
    """The trusted view of a SAML assertion, post-validation.

    Every field on this dataclass has been:
      * signature-verified against the pinned IdP cert;
      * time-validated against NotBefore/NotOnOrAfter;
      * audience-validated against the SP entity id.

    Callers can treat the values as authoritative.
    """

    assertion_id: str
    issuer: str
    name_id: str
    name_id_format: Optional[str]
    session_index: Optional[str]
    not_on_or_after: datetime
    attributes: Dict[str, List[str]]
    audience: str

    def get_attribute(self, name: str) -> Optional[str]:
        """First value of an attribute, or ``None``.

        Most SAML attributes are single-valued; for multi-valued
        attributes (group memberships) callers should read
        ``attributes[name]`` directly.
        """
        values = self.attributes.get(name) or []
        return values[0] if values else None


def parse_and_validate_saml_response(
    *,
    response_b64: str,
    idp_certificate_pem: str,
    idp_entity_id: str,
    sp_entity_id: str,
    sp_acs_url: str,
    is_compressed: bool = False,
    now: Optional[datetime] = None,
) -> ValidatedAssertion:
    """Decode a base64 SAMLResponse, verify the signature, and return
    a ``ValidatedAssertion``.

    Args:
        response_b64: The ``SAMLResponse`` form parameter, base64-
            encoded as the binding requires.
        idp_certificate_pem: The customer's pinned IdP signing cert,
            PEM-encoded. Stored on the per-tenant SAML config and
            never rotated automatically — rotation is a deliberate
            admin action via the workspace UI.
        idp_entity_id: The customer's configured IdP entity id.
            Issuer on the assertion must match this exactly.
        sp_entity_id: Clearledgr's SP entity id for this tenant.
            Audience on the assertion must include this.
        sp_acs_url: The Assertion Consumer Service URL. The Response's
            Destination must equal this if the IdP set it (it always
            does for HTTP-POST binding).
        is_compressed: Set True for HTTP-Redirect binding payloads,
            which DEFLATE the AuthnRequest before base64. Currently
            unused for ACS (responses come via POST), reserved for
            symmetric SLO support.
        now: Override for tests. Defaults to ``datetime.utcnow()``.

    Raises:
        SAMLValidationError: with a token-style reason on every
            failure mode listed in this module's docstring.
    """
    if not response_b64:
        raise SAMLValidationError("empty_response")
    if not idp_certificate_pem or not idp_certificate_pem.strip():
        raise SAMLValidationError("idp_cert_missing")

    try:
        decoded = base64.b64decode(response_b64, validate=False)
    except Exception as exc:
        logger.debug("[saml] base64 decode failed: %s", exc)
        raise SAMLValidationError("base64_invalid") from exc

    if is_compressed:
        try:
            decoded = zlib.decompress(decoded, -zlib.MAX_WBITS)
        except Exception as exc:
            logger.debug("[saml] DEFLATE decompress failed: %s", exc)
            raise SAMLValidationError("compression_invalid") from exc

    # ─── Safe parse ─────────────────────────────────────────────
    try:
        from defusedxml.lxml import fromstring as _safe_parse  # type: ignore
    except ImportError:
        # defusedxml.lxml is optional; the lxml entry below uses our
        # own minimal hardening (no_network, resolve_entities=False).
        from lxml import etree as _et
        parser = _et.XMLParser(no_network=True, resolve_entities=False)
        try:
            root = _et.fromstring(decoded, parser=parser)
        except Exception as exc:
            logger.debug("[saml] xml parse failed: %s", exc)
            raise SAMLValidationError("xml_invalid") from exc
    else:
        try:
            root = _safe_parse(decoded, forbid_dtd=True, forbid_entities=True)
        except Exception as exc:
            logger.debug("[saml] xml parse failed: %s", exc)
            raise SAMLValidationError("xml_invalid") from exc

    # ─── Top-level Response checks ──────────────────────────────
    # Tag should be {samlp:}Response
    if root.tag != f"{{{_NS['samlp']}}}Response":
        raise SAMLValidationError("not_a_response")

    destination = root.get("Destination")
    if destination and destination != sp_acs_url:
        logger.debug("[saml] Destination mismatch: got=%r want=%r", destination, sp_acs_url)
        raise SAMLValidationError("destination_mismatch")

    # StatusCode must be Success
    status_code_el = root.find(f"samlp:Status/samlp:StatusCode", _NS)
    if status_code_el is None or status_code_el.get("Value") != \
            "urn:oasis:names:tc:SAML:2.0:status:Success":
        raise SAMLValidationError("status_not_success")

    # ─── Signature verification ─────────────────────────────────
    # We verify the Assertion's signature (most IdPs sign the
    # Assertion, some sign the Response and not the Assertion, some
    # sign both). Try Assertion first; fall back to Response.
    try:
        from signxml import (  # type: ignore
            DigestAlgorithm,
            SignatureConfiguration,
            SignatureMethod,
            XMLVerifier,
        )
    except ImportError as exc:  # pragma: no cover
        raise SAMLValidationError("signxml_unavailable") from exc

    assertion_el = root.find("saml:Assertion", _NS)
    if assertion_el is None:
        raise SAMLValidationError("no_assertion")

    # Locate the Signature element. We require it on the Assertion
    # (or directly on the Response — some IdPs only sign the wrapper).
    has_assertion_sig = assertion_el.find("ds:Signature", _NS) is not None
    has_response_sig = root.find("ds:Signature", _NS) is not None
    if not (has_assertion_sig or has_response_sig):
        raise SAMLValidationError("signature_missing")

    # Limit accepted algs to the modern set Azure/Okta/Google all
    # advertise. Refusing SHA-1 outright — NIST has deprecated it
    # for SAML signatures since 2016. signxml's enum names are
    # canonical XMLDSig URIs internally; the Python attribute names
    # below match the SignatureMethod / DigestAlgorithm enums.
    safe_signature_config = SignatureConfiguration(
        signature_methods=frozenset({
            SignatureMethod.RSA_SHA256,
            SignatureMethod.RSA_SHA384,
            SignatureMethod.RSA_SHA512,
            SignatureMethod.ECDSA_SHA256,
            SignatureMethod.ECDSA_SHA384,
            SignatureMethod.ECDSA_SHA512,
        }),
        digest_algorithms=frozenset({
            DigestAlgorithm.SHA256,
            DigestAlgorithm.SHA384,
            DigestAlgorithm.SHA512,
        }),
    )

    verifier = XMLVerifier()
    try:
        # signxml verifies whichever Signature element it finds at
        # or below the passed-in node. Prefer the Assertion for
        # tenant-of-trust — verifying just the wrapper would let an
        # attacker swap the Assertion content while keeping the
        # signed wrapper.
        target = assertion_el if has_assertion_sig else root
        verifier.verify(
            data=target,
            x509_cert=idp_certificate_pem,
            expect_config=safe_signature_config,
        )
    except SAMLValidationError:
        raise
    except Exception as exc:
        logger.debug("[saml] signature verification failed: %s", exc)
        raise SAMLValidationError("signature_invalid") from exc

    # ─── Issuer ─────────────────────────────────────────────────
    issuer_el = assertion_el.find("saml:Issuer", _NS)
    if issuer_el is None or not (issuer_el.text or "").strip():
        raise SAMLValidationError("issuer_missing")
    issuer = issuer_el.text.strip()
    if issuer != idp_entity_id:
        logger.debug(
            "[saml] Issuer mismatch: got=%r want=%r", issuer, idp_entity_id,
        )
        raise SAMLValidationError("issuer_mismatch")

    # ─── Subject + NameID ───────────────────────────────────────
    name_id_el = assertion_el.find("saml:Subject/saml:NameID", _NS)
    if name_id_el is None or not (name_id_el.text or "").strip():
        raise SAMLValidationError("name_id_missing")
    name_id = name_id_el.text.strip()
    name_id_format = name_id_el.get("Format")

    # ─── Conditions ─────────────────────────────────────────────
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    skew = timedelta(seconds=_CLOCK_SKEW_SECONDS)

    conditions_el = assertion_el.find("saml:Conditions", _NS)
    if conditions_el is None:
        raise SAMLValidationError("conditions_missing")

    not_before = _parse_xsd_datetime(conditions_el.get("NotBefore"))
    not_on_or_after = _parse_xsd_datetime(conditions_el.get("NotOnOrAfter"))
    if not_before is not None and now_dt + skew < not_before:
        raise SAMLValidationError("not_yet_valid")
    if not_on_or_after is not None and now_dt - skew >= not_on_or_after:
        raise SAMLValidationError("expired")
    if not_on_or_after is None:
        raise SAMLValidationError("expiry_missing")

    # AudienceRestriction must include our SP entity id.
    audience_match = False
    for audience_el in conditions_el.iter(f"{{{_NS['saml']}}}Audience"):
        if (audience_el.text or "").strip() == sp_entity_id:
            audience_match = True
            break
    if not audience_match:
        raise SAMLValidationError("audience_mismatch")

    # ─── AttributeStatement ─────────────────────────────────────
    attributes: Dict[str, List[str]] = {}
    for attr_el in assertion_el.iter(f"{{{_NS['saml']}}}Attribute"):
        name = attr_el.get("Name") or ""
        if not name:
            continue
        values: List[str] = []
        for v_el in attr_el.iter(f"{{{_NS['saml']}}}AttributeValue"):
            text = (v_el.text or "").strip()
            if text:
                values.append(text)
        if values:
            attributes[name] = values

    # ─── AssertionID + SessionIndex ─────────────────────────────
    assertion_id = assertion_el.get("ID") or ""
    if not assertion_id:
        raise SAMLValidationError("assertion_id_missing")

    session_index = None
    auth_stmt = assertion_el.find("saml:AuthnStatement", _NS)
    if auth_stmt is not None:
        session_index = auth_stmt.get("SessionIndex")

    return ValidatedAssertion(
        assertion_id=assertion_id,
        issuer=issuer,
        name_id=name_id,
        name_id_format=name_id_format,
        session_index=session_index,
        not_on_or_after=not_on_or_after,
        attributes=attributes,
        audience=sp_entity_id,
    )


def _parse_xsd_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an XSD dateTime (e.g. ``2026-04-29T12:34:56Z``) to UTC.

    Returns ``None`` if ``value`` is None or empty. Raises on malformed
    inputs because these timestamps are security-critical.
    """
    if not value:
        return None
    s = value.strip()
    # XSD allows ``Z``, ``+HH:MM``, or naive (no offset).
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        # fromisoformat handles "+HH:MM" and naive in 3.11+.
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise SAMLValidationError("xsd_datetime_invalid") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
