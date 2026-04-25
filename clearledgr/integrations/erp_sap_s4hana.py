"""SAP S/4HANA OData write-back helpers for the ERP-native approval flow.

These calls are the SAP-side counterparts to the NetSuite TBA REST
write-backs in :mod:`clearledgr.services.erp_native_approval`. Where
NetSuite uses OAuth-1.0 TBA + a flat REST surface, S/4HANA uses
OData v2 with OAuth-2.0 client credentials (Cloud) or principal
propagation (on-prem via BTP Cloud Connector).

The supplier-invoice document key is composite: ``CompanyCode`` +
``SupplierInvoice`` (doc number) + ``FiscalYear``. We read all three
from the AP item's metadata (set at intake by the dispatcher) and
construct the OData entity URL accordingly.

Called from :mod:`clearledgr.services.erp_native_approval` when the
AP item's ``metadata.source == "sap_native"``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.core.http_client import get_http_client
from clearledgr.integrations.erp_router import ERPConnection, _erp_connection_from_row

logger = logging.getLogger(__name__)


# OData service path — the canonical S/4HANA Supplier Invoice service.
# Matches API_SUPPLIERINVOICE_PROCESS_SRV in S/4HANA Cloud and the
# equivalent in on-prem 1809+. Customers who've remapped the path
# can override via ``erp_connections.credentials.s4hana_supplier_invoice_path``.
DEFAULT_SUPPLIER_INVOICE_PATH = "/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV"


async def release_payment_block(
    *,
    organization_id: str,
    company_code: str,
    supplier_invoice: str,
    fiscal_year: str,
) -> Dict[str, Any]:
    """Clear the ``PaymentBlockingReason`` field on a Supplier Invoice.

    PATCH ``A_SupplierInvoice(CompanyCode=...,SupplierInvoice=...,FiscalYear=...)``
    with body ``{"PaymentBlockingReason": ""}``. The empty-string write
    removes the block — different from NetSuite's boolean toggle but
    semantically equivalent.
    """
    return await _odata_patch(
        organization_id=organization_id,
        company_code=company_code,
        supplier_invoice=supplier_invoice,
        fiscal_year=fiscal_year,
        body={"PaymentBlockingReason": ""},
        op_label="payment_block_release",
    )


async def cancel_supplier_invoice(
    *,
    organization_id: str,
    company_code: str,
    supplier_invoice: str,
    fiscal_year: str,
    reason_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Reverse / cancel a Supplier Invoice on Slack reject.

    S/4HANA's OData service exposes a bound action
    ``Cancel`` (or ``SupplierInvoiceCancellation``) on
    ``A_SupplierInvoice``. We POST to the action endpoint with
    ``ReversalReason`` if provided. Falls back to a PATCH that sets
    ``ReverseDocument = X`` for accounts that don't expose the
    action.

    On Cloud accounts the action endpoint is the canonical path and
    handles GL reversal + payment cancellation atomically. On
    on-prem 1809+ both forms work; we try the action first.
    """
    # Primary: bound action.
    action_result = await _odata_action(
        organization_id=organization_id,
        company_code=company_code,
        supplier_invoice=supplier_invoice,
        fiscal_year=fiscal_year,
        action="SupplierInvoiceCancellation",
        body={"ReversalReason": "01"} if reason_text is None else {"ReversalReason": "01", "ReversalReasonText": reason_text[:50]},
        op_label="cancel_action",
    )
    if action_result.get("ok"):
        return action_result

    # Fallback: direct PATCH with ReverseDocument flag.
    patch_result = await _odata_patch(
        organization_id=organization_id,
        company_code=company_code,
        supplier_invoice=supplier_invoice,
        fiscal_year=fiscal_year,
        body={"ReverseDocument": True},
        op_label="cancel_patch",
    )
    if patch_result.get("ok"):
        return patch_result

    return {
        "ok": False,
        "reason": "cancel_failed",
        "primary_error": {k: v for k, v in action_result.items() if k != "ok"},
        "fallback_error": {k: v for k, v in patch_result.items() if k != "ok"},
    }


# ─── OData primitives ───────────────────────────────────────────────


async def _odata_patch(
    *,
    organization_id: str,
    company_code: str,
    supplier_invoice: str,
    fiscal_year: str,
    body: Dict[str, Any],
    op_label: str,
) -> Dict[str, Any]:
    connection, base_url, service_path, error = _resolve_connection(organization_id)
    if error:
        return {"ok": False, "op": op_label, **error}

    entity_url = (
        f"{base_url}{service_path}/A_SupplierInvoice("
        f"CompanyCode='{_escape_odata(company_code)}',"
        f"SupplierInvoice='{_escape_odata(supplier_invoice)}',"
        f"FiscalYear='{_escape_odata(fiscal_year)}'"
        f")"
    )
    headers = await _build_auth_headers(connection)
    if "error" in headers:
        return {"ok": False, "op": op_label, "reason": headers["error"]}

    csrf = await _fetch_csrf_token(base_url, service_path, headers)
    if csrf:
        headers["x-csrf-token"] = csrf

    headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    client = get_http_client()
    try:
        response = await client.request("PATCH", entity_url, headers=headers, json=body, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "op": op_label, "reason": "request_failed", "error": str(exc)}

    return _interpret_odata_response(response, op_label)


async def _odata_action(
    *,
    organization_id: str,
    company_code: str,
    supplier_invoice: str,
    fiscal_year: str,
    action: str,
    body: Dict[str, Any],
    op_label: str,
) -> Dict[str, Any]:
    connection, base_url, service_path, error = _resolve_connection(organization_id)
    if error:
        return {"ok": False, "op": op_label, **error}

    action_url = (
        f"{base_url}{service_path}/A_SupplierInvoice("
        f"CompanyCode='{_escape_odata(company_code)}',"
        f"SupplierInvoice='{_escape_odata(supplier_invoice)}',"
        f"FiscalYear='{_escape_odata(fiscal_year)}'"
        f")/{action}"
    )
    headers = await _build_auth_headers(connection)
    if "error" in headers:
        return {"ok": False, "op": op_label, "reason": headers["error"]}

    csrf = await _fetch_csrf_token(base_url, service_path, headers)
    if csrf:
        headers["x-csrf-token"] = csrf

    headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    client = get_http_client()
    try:
        response = await client.post(action_url, headers=headers, json=body, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "op": op_label, "reason": "request_failed", "error": str(exc)}

    return _interpret_odata_response(response, op_label)


# ─── Helpers ────────────────────────────────────────────────────────


def _resolve_connection(organization_id: str):
    """Look up the org's S/4HANA connection from ``erp_connections``.

    Returns (connection, base_url, service_path, error_dict) — error_dict
    is empty on success.
    """
    from clearledgr.core.database import get_db
    db = get_db()
    connection: Optional[ERPConnection] = None
    try:
        if hasattr(db, "get_erp_connections"):
            for row in db.get_erp_connections(organization_id):
                erp_type = str(row.get("erp_type") or "").lower()
                if erp_type in {"sap_s4hana", "s4hana", "sap_s4"}:
                    connection = _erp_connection_from_row(row)
                    break
    except Exception as exc:  # noqa: BLE001
        return None, None, None, {"reason": "erp_connection_lookup_failed", "error": str(exc)}

    if connection is None:
        return None, None, None, {"reason": "no_s4hana_connection"}
    if not connection.base_url:
        return None, None, None, {"reason": "missing_base_url"}

    base_url = connection.base_url.rstrip("/")
    # Read optional service path override from credentials JSON.
    import json
    creds = {}
    try:
        if hasattr(db, "get_erp_connections"):
            for row in db.get_erp_connections(organization_id):
                if str(row.get("erp_type") or "").lower() in {"sap_s4hana", "s4hana", "sap_s4"}:
                    raw = row.get("credentials") or {}
                    if isinstance(raw, str):
                        try:
                            raw = json.loads(raw)
                        except Exception:
                            raw = {}
                    creds = raw if isinstance(raw, dict) else {}
                    break
    except Exception:
        creds = {}
    service_path = str(creds.get("s4hana_supplier_invoice_path") or DEFAULT_SUPPLIER_INVOICE_PATH).strip()
    if not service_path.startswith("/"):
        service_path = "/" + service_path
    return connection, base_url, service_path, {}


async def _build_auth_headers(connection: ERPConnection) -> Dict[str, str]:
    """Build Authorization header for a S/4HANA OData call.

    Two paths:

    1. **OAuth 2.0 client credentials** (S/4HANA Cloud): the connection
       has ``access_token`` already minted. We assume the existing
       ERP-router refresh path keeps it fresh; if the token is expired
       the OData call will return 401, the SDK error path here logs
       it, and the next refresh cycle picks up the rotation.
    2. **Basic auth** (on-prem fallback): connection has
       ``client_id`` + ``client_secret`` (or ``access_token`` containing
       a base64-encoded ``user:pass``). Used by smaller on-prem
       customers without an OAuth gateway in front.

    Picks based on the presence of ``access_token``.
    """
    if connection.access_token:
        return {"Authorization": f"Bearer {connection.access_token}"}
    if connection.client_id and connection.client_secret:
        import base64
        token = base64.b64encode(
            f"{connection.client_id}:{connection.client_secret}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    return {"error": "no_credentials_on_connection"}


async def _fetch_csrf_token(base_url: str, service_path: str, auth_headers: Dict[str, str]) -> Optional[str]:
    """S/4HANA OData v2 requires an x-csrf-token for write operations.

    GET against the service root with header ``x-csrf-token: fetch``
    returns the token in the response header. We cache for 5 minutes
    per process — this is a hot-path optimisation that matters when
    the same backend serves many ERP-native approvals back-to-back.
    """
    if not auth_headers or "Authorization" not in auth_headers:
        return None
    cache_key = base_url + service_path
    cached = _CSRF_CACHE.get(cache_key)
    if cached:
        token, expires_at = cached
        import time
        if time.time() < expires_at:
            return token

    client = get_http_client()
    headers = {**auth_headers, "x-csrf-token": "fetch", "Accept": "application/json"}
    try:
        response = await client.get(f"{base_url}{service_path}/", headers=headers, timeout=15)
    except Exception:
        return None
    token = response.headers.get("x-csrf-token") or response.headers.get("X-CSRF-Token")
    if token:
        import time
        _CSRF_CACHE[cache_key] = (token, time.time() + 300)
    return token


_CSRF_CACHE: Dict[str, tuple] = {}


def _interpret_odata_response(response, op_label: str) -> Dict[str, Any]:
    if response.status_code >= 400:
        snippet = ""
        try:
            snippet = response.text[:500]
        except Exception:
            snippet = ""
        return {
            "ok": False,
            "op": op_label,
            "reason": "s4hana_error",
            "status_code": response.status_code,
            "body": snippet,
        }
    return {"ok": True, "op": op_label, "status_code": response.status_code}


def _escape_odata(value: str) -> str:
    """Escape single quotes for embedding into an OData v2 key segment."""
    return str(value or "").replace("'", "''")
