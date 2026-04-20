"""Inbound ERP webhook signature verification.

One verifier per ERP, each following the ERP's documented standard
or the industry-default HMAC-SHA256 pattern when the ERP does not
publish one. All comparisons use :func:`hmac.compare_digest` so
signature checks are constant-time.

**Fail-closed by design.** If the shared secret / verifier token is
not configured, verification returns False. A webhook endpoint that
accepts traffic without a configured secret is a trust-boundary
hole; callers must surface that as a 503 (service not configured),
not a 200.

Replay protection (where the ERP supports a timestamp header):
reject payloads older than :data:`REPLAY_WINDOW_SECONDS`. This
mirrors Slack's 5-minute window and Stripe's 5-minute tolerance.

No wall-clock ``time.time()`` inside the verifier so tests can
inject a ``now`` clock. Callers in production pass nothing and get
the real clock.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REPLAY_WINDOW_SECONDS = 300  # 5 minutes, aligns with Slack/Stripe
_ClockFn = Callable[[], float]


def _default_clock() -> float:
    import time
    return time.time()


def _timing_safe_equal(a: bytes, b: bytes) -> bool:
    """Wrapper so every verifier goes through the same primitive."""
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# QuickBooks Online
#
# Source: https://developer.intuit.com/app/developer/qbo/docs/develop/webhooks/
#         webhooks-signature-verification
#
# QBO sends: intuit-signature: base64(HMAC-SHA256(raw_body, verifier_token))
# ---------------------------------------------------------------------------


def verify_quickbooks_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    verifier_token: Optional[str],
) -> bool:
    """Return True iff the QBO signature is authentic."""
    if not verifier_token:
        logger.error("QBO webhook secret not configured — rejecting")
        return False
    if not signature_header:
        return False
    try:
        provided = base64.b64decode(signature_header, validate=True)
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    expected = hmac.new(
        verifier_token.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    return _timing_safe_equal(provided, expected)


# ---------------------------------------------------------------------------
# Xero
#
# Source: https://developer.xero.com/documentation/guides/webhooks/overview/
#
# Xero sends: x-xero-signature: base64(HMAC-SHA256(raw_body, webhook_key))
# Intent-to-Receive: Xero POSTs with ``events: []`` once; if the
# signature validates, respond 200. If it doesn't, respond 401 so
# Xero displays the failure state in the app config page.
# ---------------------------------------------------------------------------


def verify_xero_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    webhook_key: Optional[str],
) -> bool:
    """Return True iff the Xero signature is authentic."""
    if not webhook_key:
        logger.error("Xero webhook key not configured — rejecting")
        return False
    if not signature_header:
        return False
    try:
        provided = base64.b64decode(signature_header, validate=True)
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    expected = hmac.new(
        webhook_key.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    return _timing_safe_equal(provided, expected)


# ---------------------------------------------------------------------------
# NetSuite
#
# NetSuite has no first-class webhook product; customers push via
# SuiteScript/RESTlet or SuiteFlow HTTP actions. The defensible
# industry pattern for SuiteScript push → external endpoint is
# HMAC-SHA256 over (timestamp + "." + raw_body) with a shared
# per-tenant secret. Matches Tipalti / Stampli public docs.
#
# Header format:
#   X-NetSuite-Signature: v1=<hex>
#   X-NetSuite-Timestamp: <unix seconds>
# ---------------------------------------------------------------------------


def verify_netsuite_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    shared_secret: Optional[str],
    *,
    now: _ClockFn = _default_clock,
) -> bool:
    """Return True iff the NetSuite signature is authentic AND fresh."""
    return _verify_timestamped_hmac(
        raw_body=raw_body,
        signature_header=signature_header,
        timestamp_header=timestamp_header,
        shared_secret=shared_secret,
        erp_label="NetSuite",
        now=now,
    )


# ---------------------------------------------------------------------------
# SAP (S/4HANA Cloud Platform Integration outbound)
#
# SAP CPI supports HMAC-based message-level auth for outbound HTTP
# channels. Same timestamp+body contract as NetSuite to keep one
# shape across both "no-native-webhook" ERPs.
#
# Header format:
#   X-SAP-Signature: v1=<hex>
#   X-SAP-Timestamp: <unix seconds>
# ---------------------------------------------------------------------------


def verify_sap_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    shared_secret: Optional[str],
    *,
    now: _ClockFn = _default_clock,
) -> bool:
    """Return True iff the SAP signature is authentic AND fresh."""
    return _verify_timestamped_hmac(
        raw_body=raw_body,
        signature_header=signature_header,
        timestamp_header=timestamp_header,
        shared_secret=shared_secret,
        erp_label="SAP",
        now=now,
    )


# ---------------------------------------------------------------------------
# Shared primitive for ERPs that use the timestamp+body HMAC pattern
# (NetSuite + SAP). Stripe/Slack-style replay window.
# ---------------------------------------------------------------------------


def _verify_timestamped_hmac(
    *,
    raw_body: bytes,
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    shared_secret: Optional[str],
    erp_label: str,
    now: _ClockFn,
) -> bool:
    if not shared_secret:
        logger.error("%s webhook secret not configured — rejecting", erp_label)
        return False
    if not signature_header or not timestamp_header:
        return False

    # Parse timestamp; reject non-integer values without leaking
    # parse state to the caller.
    try:
        ts = int(timestamp_header.strip())
    except (TypeError, ValueError):
        return False

    # Reject future timestamps > REPLAY_WINDOW_SECONDS too; catches
    # clock-skew games and the "submit from 2099" trick.
    skew = now() - ts
    if skew < -REPLAY_WINDOW_SECONDS or skew > REPLAY_WINDOW_SECONDS:
        return False

    # Parse the v1=<hex> shape; anything else is malformed.
    sig_value = signature_header.strip()
    if sig_value.startswith("v1="):
        sig_value = sig_value[3:]
    try:
        provided = bytes.fromhex(sig_value)
    except ValueError:
        return False

    signed_payload = f"{ts}.".encode("utf-8") + raw_body
    expected = hmac.new(
        shared_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).digest()
    return _timing_safe_equal(provided, expected)


# ---------------------------------------------------------------------------
# Helpers for tests + sender-side signing (used by integration tests to
# construct authentic signatures without duplicating the HMAC shape).
# ---------------------------------------------------------------------------


def sign_quickbooks(raw_body: bytes, verifier_token: str) -> str:
    digest = hmac.new(
        verifier_token.encode("utf-8"), raw_body, hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def sign_xero(raw_body: bytes, webhook_key: str) -> str:
    digest = hmac.new(
        webhook_key.encode("utf-8"), raw_body, hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def sign_timestamped(raw_body: bytes, shared_secret: str, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(
        shared_secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).digest()
    return "v1=" + digest.hex()
