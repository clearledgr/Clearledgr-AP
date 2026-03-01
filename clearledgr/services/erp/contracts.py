"""Provider-agnostic ERP adapter contract (API-first path)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol

from clearledgr.integrations.erp_router import Bill


class ERPBillAdapter(Protocol):
    """Canonical bill-posting adapter contract for GA ERP connectors."""

    erp_type: str

    def validate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    async def post(
        self,
        organization_id: str,
        bill: Bill,
        *,
        ap_item_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    async def get_status(
        self,
        organization_id: str,
        external_ref: str,
    ) -> Dict[str, Any]:
        ...

    async def reconcile(
        self,
        organization_id: str,
        entity_id: str,
    ) -> Dict[str, Any]:
        ...


@dataclass
class RouterBackedERPBillAdapter:
    """Adapter that delegates posting to the existing ERP router."""

    erp_type: str
    post_handler: Callable[..., Awaitable[Dict[str, Any]]]

    def validate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        required = ("invoice_number", "vendor_name", "amount", "currency")
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            return {
                "ok": False,
                "reason": "missing_required_fields",
                "missing_fields": missing,
                "erp_type": self.erp_type,
            }
        return {
            "ok": True,
            "reason": "ok",
            "missing_fields": [],
            "erp_type": self.erp_type,
        }

    async def post(
        self,
        organization_id: str,
        bill: Bill,
        *,
        ap_item_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.post_handler(
            organization_id,
            bill,
            ap_item_id=ap_item_id,
            idempotency_key=idempotency_key,
        )

    async def get_status(
        self,
        organization_id: str,
        external_ref: str,
    ) -> Dict[str, Any]:
        _ = organization_id
        return {
            "status": "not_implemented",
            "erp_type": self.erp_type,
            "external_ref": str(external_ref or ""),
            "reason": "status_lookup_not_available_in_router",
        }

    async def reconcile(
        self,
        organization_id: str,
        entity_id: str,
    ) -> Dict[str, Any]:
        _ = organization_id
        return {
            "status": "not_implemented",
            "erp_type": self.erp_type,
            "entity_id": str(entity_id or ""),
            "reason": "reconcile_not_available_in_router",
        }


def get_erp_bill_adapter(
    *,
    erp_type: str,
    post_handler: Callable[..., Awaitable[Dict[str, Any]]],
) -> ERPBillAdapter:
    """Factory for canonical ERP adapter.

    The current adapter implementation is router-backed for all GA connectors.
    Connector-specific adapters can replace this factory incrementally.
    """

    token = str(erp_type or "unconfigured").strip().lower() or "unconfigured"
    return RouterBackedERPBillAdapter(erp_type=token, post_handler=post_handler)

