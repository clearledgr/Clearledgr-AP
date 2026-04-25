"""Backend endpoints for the SAP Fiori extension panel.

Two routes:

1. **`POST /extension/sap/exchange`** — exchanges a BTP XSUAA-issued
   user JWT for a short-lived Clearledgr access JWT. The Fiori app
   (deployed via SAP BTP HTML5 Repo + Approuter) hits this once on
   page load to bootstrap a session against api.clearledgr.com.

2. **`GET /extension/ap-items/by-sap-invoice`** — given the supplier-
   invoice composite key (``CompanyCode`` + ``SupplierInvoice`` +
   ``FiscalYear``) plus a Clearledgr Bearer JWT (from step 1), returns
   the Box state, timeline, exceptions, outcome, and a rendered
   summary block. Mirrors the NetSuite-side
   ``/extension/ap-items/by-netsuite-bill/{id}`` shape.

Auth model:

* The Fiori app is wrapped by SAP Approuter. Approuter forwards the
  XSUAA-signed JWT to ``/extension/sap/exchange``.
* We verify the JWT against XSUAA's JWKS (cached 1h), extract the
  ``email`` claim, look up the matching Clearledgr user, and mint a
  5-minute Clearledgr JWT via ``create_access_token``.
* The Fiori app caches that token in memory and uses it as Bearer
  for the read endpoint + any action endpoints (approve / reject).

XSUAA JWKS verification path is asymmetric (RS256 signed) — different
trust root than the NetSuite SuiteApp's HMAC-symmetric panel JWT.
That's intentional: BTP is the customer's tenant; we don't have a
shared secret with them and shouldn't pretend to.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from clearledgr.api.ap_items_read_routes import shared
from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import TokenData, create_access_token, decode_token, _token_data_from_payload
from clearledgr.core.http_client import get_http_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["sap-fiori-extension"])
_security = HTTPBearer(auto_error=False)


# ─── XSUAA JWKS verification ────────────────────────────────────────


# Cache: { jwks_url: (keys_dict, expires_at_unix) }
_JWKS_CACHE: Dict[str, tuple] = {}
_JWKS_TTL_SECONDS = 3600


async def _fetch_jwks(jwks_url: str) -> Dict[str, Any]:
    cached = _JWKS_CACHE.get(jwks_url)
    if cached:
        keys, expires_at = cached
        if time.time() < expires_at:
            return keys
    client = get_http_client()
    response = await client.get(jwks_url, timeout=15)
    response.raise_for_status()
    keys_doc = response.json()
    _JWKS_CACHE[jwks_url] = (keys_doc, time.time() + _JWKS_TTL_SECONDS)
    return keys_doc


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


async def _verify_xsuaa_jwt(token: str, *, jwks_url: str, expected_audience: Optional[str]) -> Dict[str, Any]:
    """Asymmetric (RS256) verification of a BTP XSUAA-signed JWT.

    Returns the decoded payload dict on success. Raises HTTPException(401)
    on any failure with a generic message — we don't echo which check
    failed (signature, exp, audience) to avoid probing.
    """
    if not token:
        raise HTTPException(status_code=401, detail="sap_xsuaa: missing token")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="sap_xsuaa: malformed token")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except Exception:
        raise HTTPException(status_code=401, detail="sap_xsuaa: token decode failed")

    kid = header.get("kid")
    alg = (header.get("alg") or "").upper()
    if alg != "RS256":
        raise HTTPException(status_code=401, detail="sap_xsuaa: unsupported alg")

    keys_doc = await _fetch_jwks(jwks_url)
    keys = keys_doc.get("keys") or []
    matching = next((k for k in keys if k.get("kid") == kid), None)
    if matching is None and keys:
        # XSUAA sometimes ships an unkeyed JWT during local dev — only
        # accept the unkeyed match if there's exactly one key in the doc
        if len(keys) == 1:
            matching = keys[0]
    if matching is None:
        # Bust the cache once — JWKS rotation can leave the cache stale.
        _JWKS_CACHE.pop(jwks_url, None)
        raise HTTPException(status_code=401, detail="sap_xsuaa: kid not in JWKS")

    try:
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        n = int.from_bytes(_b64url_decode(matching["n"]), "big")
        e = int.from_bytes(_b64url_decode(matching["e"]), "big")
        public_key = RSAPublicNumbers(e=e, n=n).public_key()
        signing_input = (header_b64 + "." + payload_b64).encode("ascii")
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        logger.warning("sap_xsuaa: signature verify failed (%s)", exc)
        raise HTTPException(status_code=401, detail="sap_xsuaa: signature invalid")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= float(exp):
        raise HTTPException(status_code=401, detail="sap_xsuaa: token expired")

    if expected_audience:
        aud = payload.get("aud")
        aud_list = aud if isinstance(aud, list) else ([aud] if aud else [])
        if expected_audience not in aud_list:
            # Some XSUAA configs put the audience in `client_id` not `aud`
            # for client-credentials-style tokens. Be tolerant.
            if str(payload.get("client_id") or "") != expected_audience:
                raise HTTPException(status_code=401, detail="sap_xsuaa: audience mismatch")

    return payload


# ─── Endpoints ──────────────────────────────────────────────────────


@router.post("/sap/exchange")
async def exchange_xsuaa_for_clearledgr_jwt(
    body: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    """Exchange an XSUAA-signed user JWT for a short-lived Clearledgr JWT.

    Body shape:
    ```
    {
        "xsuaa_jwt": "eyJ...",
        "organization_id": "<org_id>"  // optional; resolved from email if absent
    }
    ```

    Response:
    ```
    {
        "access_token": "<clearledgr-jwt>",
        "token_type": "bearer",
        "expires_in": 300
    }
    ```
    """
    xsuaa_jwt = str((body or {}).get("xsuaa_jwt") or "").strip()
    if not xsuaa_jwt:
        raise HTTPException(status_code=400, detail="missing_xsuaa_jwt")

    jwks_url = os.getenv("SAP_XSUAA_JWKS_URL", "").strip()
    if not jwks_url:
        raise HTTPException(
            status_code=503,
            detail="sap_xsuaa: SAP_XSUAA_JWKS_URL env var not configured",
        )
    expected_audience = os.getenv("SAP_XSUAA_AUDIENCE", "").strip() or None

    payload = await _verify_xsuaa_jwt(
        xsuaa_jwt,
        jwks_url=jwks_url,
        expected_audience=expected_audience,
    )

    user_email = str(payload.get("email") or payload.get("user_name") or "").strip().lower()
    if not user_email:
        raise HTTPException(status_code=401, detail="sap_xsuaa: no email claim in token")

    db = shared.get_db()
    user_row = None
    if hasattr(db, "get_user_by_email"):
        try:
            user_row = db.get_user_by_email(user_email)
        except Exception:
            user_row = None
    if not user_row:
        raise HTTPException(
            status_code=403,
            detail=f"sap_xsuaa: no Clearledgr user matches email {user_email}",
        )

    organization_id = str(
        (body or {}).get("organization_id")
        or user_row.get("organization_id")
        or "default"
    ).strip() or "default"

    # Mint a 5-minute Clearledgr JWT scoped to this user/org.
    user_id = str(user_row.get("id") or user_email).strip()
    role = str(user_row.get("role") or "user").strip()
    access_token = create_access_token(
        user_id=user_id,
        email=user_email,
        organization_id=organization_id,
        role=role,
        expires_delta=timedelta(minutes=5),
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 300,
    }


@router.get("/ap-items/by-sap-invoice")
def get_ap_item_by_sap_invoice(
    company_code: str = Query(..., min_length=1),
    supplier_invoice: str = Query(..., min_length=1, description="SAP supplier invoice document number"),
    fiscal_year: str = Query(..., min_length=1),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Dict[str, Any]:
    """Return the Clearledgr Box for the AP item linked to a SAP supplier invoice.

    Lookup key: ``erp_reference == "{CompanyCode}/{SupplierInvoice}/{FiscalYear}"``
    against ``ap_items``. The composite key is set at intake by
    :mod:`clearledgr.services.sap_webhook_dispatch`.

    Response shape mirrors ``GET /api/ap/items/{id}/box`` plus a ``summary``
    block + ``ap_item_id`` for deep-linking.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="sap_panel: missing bearer token")
    try:
        decoded = decode_token(credentials.credentials)
    except HTTPException:
        raise
    if decoded.get("type") != "access":
        raise HTTPException(status_code=401, detail="sap_panel: token is not an access token")
    user = _token_data_from_payload(decoded)

    composite_key = f"{company_code}/{supplier_invoice}/{fiscal_year}"
    db = shared.get_db()
    item = db.get_ap_item_by_erp_reference(user.organization_id, composite_key)
    if not item:
        raise HTTPException(
            status_code=404,
            detail={"reason": "no_clearledgr_item_for_invoice", "composite_key": composite_key},
        )
    verify_org_access(item.get("organization_id") or "default", user)
    ap_item_id = str(item.get("id") or "").strip()

    timeline: list = []
    try:
        from clearledgr.services.ap_operator_audit import normalize_operator_audit_events
        timeline = normalize_operator_audit_events(db.list_ap_audit_events(ap_item_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("sap_panel: timeline fetch failed for %s — %s", ap_item_id, exc)

    exceptions: list = []
    if hasattr(db, "list_box_exceptions"):
        try:
            exceptions = db.list_box_exceptions(box_type="ap_item", box_id=ap_item_id)
        except Exception:
            exceptions = []

    outcome = None
    if hasattr(db, "get_box_outcome"):
        try:
            outcome = db.get_box_outcome(box_type="ap_item", box_id=ap_item_id)
        except Exception:
            outcome = None

    summary = {
        "vendor_name": item.get("vendor_name"),
        "amount": item.get("amount"),
        "currency": item.get("currency"),
        "invoice_number": item.get("invoice_number"),
        "due_date": item.get("due_date"),
    }

    return {
        "ap_item_id": ap_item_id,
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "state": item.get("state"),
        "summary": summary,
        "timeline": timeline,
        "exceptions": exceptions,
        "outcome": outcome,
        "composite_key": composite_key,
    }
