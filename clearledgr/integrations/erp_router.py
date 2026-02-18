"""
Minimal ERP connector for AP v1.

Supports:
- Mock posting (default)
- NetSuite posting (primary)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from clearledgr.core.database import get_db


@dataclass
class ERPConnection:
    type: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    base_url: Optional[str] = None
    realm_id: Optional[str] = None
    tenant_id: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None


@dataclass
class Bill:
    vendor_name: str
    amount: float
    currency: str
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    description: Optional[str] = None


def _get_db():
    return get_db()


def get_erp_connection(organization_id: str) -> Optional[ERPConnection]:
    db = _get_db()
    connections = db.get_erp_connections(organization_id)
    if not connections:
        return None
    conn = connections[0]
    credentials = conn.get("credentials")
    if isinstance(credentials, str):
        try:
            import json
            credentials = json.loads(credentials)
        except Exception:
            credentials = None
    return ERPConnection(
        type=conn.get("erp_type") or "unknown",
        access_token=conn.get("access_token"),
        refresh_token=conn.get("refresh_token"),
        base_url=conn.get("base_url"),
        realm_id=conn.get("realm_id"),
        tenant_id=conn.get("tenant_id"),
        credentials=credentials or None,
    )


def set_erp_connection(organization_id: str, connection: ERPConnection) -> None:
    db = _get_db()
    db.save_erp_connection(
        organization_id=organization_id,
        erp_type=connection.type,
        access_token=connection.access_token,
        refresh_token=connection.refresh_token,
        realm_id=connection.realm_id,
        tenant_id=connection.tenant_id,
        base_url=connection.base_url,
        credentials=connection.credentials,
    )


def delete_erp_connection(organization_id: str, erp_type: str) -> bool:
    db = _get_db()
    return db.delete_erp_connection(organization_id, erp_type)


async def post_bill(organization_id: str, bill: Bill) -> Dict[str, Any]:
    """
    Post a bill to ERP. Mock by default.
    Returns a stable bill_id or doc_num on success.
    """
    mode = os.getenv("ERP_MODE", "mock").lower()
    if mode == "mock":
        return {
            "status": "success",
            "bill_id": f"ERP-MOCK-{organization_id}-{abs(hash(bill.invoice_number or bill.vendor_name)) % 100000}",
            "currency": bill.currency,
            "amount": bill.amount,
        }

    connection = get_erp_connection(organization_id)
    if not connection:
        return {"status": "error", "reason": "erp_not_configured"}

    if (connection.type or "").lower() == "netsuite":
        return await _post_bill_to_netsuite(connection, bill)

    return {"status": "error", "reason": "erp_not_implemented"}


async def _post_bill_to_netsuite(connection: ERPConnection, bill: Bill) -> Dict[str, Any]:
    """
    Post vendor bill to NetSuite.

    This uses a pre-configured NetSuite integration endpoint stored in
    `erp_connections.base_url` and credentials payload:
    - credentials.account_id
    - credentials.integration_id (optional)
    """
    if not connection.base_url:
        return {"status": "error", "reason": "netsuite_base_url_missing"}

    if not connection.access_token:
        return {"status": "error", "reason": "netsuite_access_token_missing"}

    payload = {
        "vendorName": bill.vendor_name,
        "amount": bill.amount,
        "currency": bill.currency,
        "invoiceNumber": bill.invoice_number,
        "dueDate": bill.due_date,
        "memo": bill.description or "Clearledgr AP posting",
    }

    credentials = connection.credentials or {}
    account_id = credentials.get("account_id") if isinstance(credentials, dict) else None
    integration_id = credentials.get("integration_id") if isinstance(credentials, dict) else None
    if account_id:
        payload["accountId"] = account_id
    if integration_id:
        payload["integrationId"] = integration_id

    headers = {
        "Authorization": f"Bearer {connection.access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(connection.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"status": "error", "reason": "netsuite_post_failed", "error": str(exc)}

    erp_ref = (
        data.get("erp_reference_id")
        or data.get("bill_id")
        or data.get("internalId")
        or data.get("tranId")
    )

    if not erp_ref:
        stable = hashlib.sha1(
            f"{bill.vendor_name}|{bill.invoice_number}|{bill.amount}|{bill.currency}".encode("utf-8")
        ).hexdigest()[:12]
        erp_ref = f"NETSUITE-{stable}"

    return {
        "status": "success",
        "erp_reference_id": erp_ref,
        "bill_id": data.get("bill_id") or data.get("internalId"),
        "doc_num": data.get("doc_num") or data.get("tranId"),
        "raw_response_redacted": {
            "status": data.get("status"),
            "reference": erp_ref,
        },
    }
