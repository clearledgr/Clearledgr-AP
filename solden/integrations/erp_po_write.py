"""Purchase-order write-back to the ERP.

The AP side posts *bills*; this is the procurement side — creating a
purchase order in the customer's ERP once it's approved. No PO-create
path existed before (ERP PO support was read-only, for 3-way match).

Structure mirrors ``erp_router.post_bill``: idempotency guard (skip if the
PO already carries an ``erp_po_id``), resolve the org's ERP connection,
dispatch to a per-ERP poster, stamp the returned id back. Gated behind
``FEATURE_PROCUREMENT_ERP_WRITE``.

Status of the per-ERP posters:
  * quickbooks, xero — reference implementations, request shape unit-tested
    with mocked HTTP. NEED live-sandbox validation before the flag flips.
  * netsuite, sap — explicit not-implemented stubs (OAuth1 TBA / session
    auth + their PO schemas are a follow-on).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from solden.core.feature_flags import is_procurement_erp_write_enabled
from solden.core.http_client import get_http_client

logger = logging.getLogger(__name__)


def _po_amount(po: Dict[str, Any]) -> float:
    return float(po.get("total_amount") or 0.0)


async def create_po_quickbooks(
    connection: Any, po: Dict[str, Any], idempotency_key: Optional[str] = None
) -> Dict[str, Any]:
    """Create a PurchaseOrder in QuickBooks Online.

    Reference implementation — request shape mirrors the QB bill poster
    (v3 company endpoint + ``requestid`` idempotency). NEEDS live validation.
    """
    realm = getattr(connection, "realm_id", None)
    token = getattr(connection, "access_token", None)
    if not realm or not token:
        return {"status": "error", "erp": "quickbooks", "reason": "missing_credentials"}
    url = f"https://quickbooks.api.intuit.com/v3/company/{realm}/purchaseorder"
    if idempotency_key:
        url = f"{url}?requestid={str(idempotency_key)[:50]}"
    payload = {
        "VendorRef": {"value": str(po.get("vendor_id") or ""), "name": po.get("vendor_name")},
        "TotalAmt": _po_amount(po),
        "Line": [
            {
                "DetailType": "ItemBasedExpenseLineDetail",
                "Amount": float(li.get("line_total") or li.get("unit_price") or 0.0),
                "Description": li.get("description", ""),
                "ItemBasedExpenseLineDetail": {},
            }
            for li in (po.get("line_items") or [])
        ] or [{"DetailType": "ItemBasedExpenseLineDetail", "Amount": _po_amount(po), "ItemBasedExpenseLineDetail": {}}],
    }
    client = get_http_client()
    resp = await client.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "Content-Type": "application/json"},
        json=payload, timeout=20,
    )
    if resp.status_code == 401:
        return {"status": "error", "erp": "quickbooks", "needs_reauth": True}
    if resp.status_code >= 300:
        return {"status": "error", "erp": "quickbooks", "reason": f"http_{resp.status_code}"}
    body = resp.json()
    erp_po_id = (body.get("PurchaseOrder") or {}).get("Id")
    return {"status": "success", "erp": "quickbooks", "erp_po_id": erp_po_id}


async def create_po_xero(
    connection: Any, po: Dict[str, Any], idempotency_key: Optional[str] = None
) -> Dict[str, Any]:
    """Create a PurchaseOrder in Xero. Reference implementation; NEEDS live validation."""
    tenant = getattr(connection, "tenant_id", None)
    token = getattr(connection, "access_token", None)
    if not tenant or not token:
        return {"status": "error", "erp": "xero", "reason": "missing_credentials"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Xero-tenant-id": str(tenant),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = str(idempotency_key)[:128]
    payload = {
        "PurchaseOrders": [
            {
                "Contact": {"Name": po.get("vendor_name", "")},
                "LineItems": [
                    {
                        "Description": li.get("description", "PO line"),
                        "Quantity": float(li.get("quantity") or 1),
                        "UnitAmount": float(li.get("unit_price") or 0.0),
                    }
                    for li in (po.get("line_items") or [])
                ] or [{"Description": po.get("po_number") or "Purchase order",
                       "Quantity": 1, "UnitAmount": _po_amount(po)}],
            }
        ]
    }
    client = get_http_client()
    resp = await client.post(
        "https://api.xero.com/api.xro/2.0/PurchaseOrders",
        headers=headers, json=payload, timeout=20,
    )
    if resp.status_code == 401:
        return {"status": "error", "erp": "xero", "needs_reauth": True}
    if resp.status_code >= 300:
        return {"status": "error", "erp": "xero", "reason": f"http_{resp.status_code}"}
    body = resp.json()
    orders = body.get("PurchaseOrders") or []
    erp_po_id = orders[0].get("PurchaseOrderID") if orders else None
    return {"status": "success", "erp": "xero", "erp_po_id": erp_po_id}


async def _create_po_not_implemented(erp: str):
    async def _poster(connection: Any, po: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return {"status": "error", "erp": erp, "reason": "po_write_not_implemented"}
    return _poster


# Per-ERP PO-create posters. patch.dict-able in tests, mirroring _BILL_FINDERS.
_PO_POSTERS: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {
    "quickbooks": create_po_quickbooks,
    "xero": create_po_xero,
}


async def create_purchase_order(
    organization_id: str,
    po: Dict[str, Any],
    *,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create the PO in the org's ERP and return the erp_po_id.

    No-op-ish when the flag is off (returns ``disabled``). Idempotent on
    an already-stamped ``erp_po_id``.
    """
    if not is_procurement_erp_write_enabled():
        return {"status": "disabled", "reason": "feature_flag_off"}

    if po.get("erp_po_id"):
        return {"status": "already_issued", "erp_po_id": po["erp_po_id"]}

    from solden.integrations.erp_router import get_erp_connection

    connection = get_erp_connection(organization_id)
    if not connection:
        return {"status": "error", "reason": "no_erp_connection"}

    poster = _PO_POSTERS.get(connection.type)
    if poster is None:
        return {"status": "error", "erp": connection.type, "reason": "po_write_not_implemented"}

    try:
        result = await poster(connection, po, idempotency_key=idempotency_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[erp_po_write] create PO failed org=%s erp=%s: %s",
            organization_id, connection.type, exc,
        )
        return {"status": "error", "erp": connection.type, "reason": f"exception:{type(exc).__name__}"}

    if isinstance(result, dict) and idempotency_key and not result.get("idempotency_key"):
        result["idempotency_key"] = idempotency_key
    return result
