"""Connector capability strategy for API-first ERP execution."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class ConnectorCapability:
    erp_type: str
    supports_api_post_bill: bool
    supports_api_apply_credit: bool
    supports_api_apply_settlement: bool
    api_priority: int
    rollout_stage: str
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ERPConnectorStrategy:
    def __init__(self) -> None:
        self._capabilities: Dict[str, ConnectorCapability] = {
            "quickbooks": ConnectorCapability(
                erp_type="quickbooks",
                supports_api_post_bill=True,
                supports_api_apply_credit=True,
                supports_api_apply_settlement=True,
                api_priority=100,
                rollout_stage="api_primary",
                notes="QBO bill posting, standard vendor-credit application, and standard bill payments are API-first.",
            ),
            "xero": ConnectorCapability(
                erp_type="xero",
                supports_api_post_bill=True,
                supports_api_apply_credit=True,
                supports_api_apply_settlement=True,
                api_priority=100,
                rollout_stage="api_primary",
                notes="Xero ACCPAY bill posting, credit allocations, and standard bill payments are API-first.",
            ),
            "netsuite": ConnectorCapability(
                erp_type="netsuite",
                supports_api_post_bill=True,
                supports_api_apply_credit=True,
                supports_api_apply_settlement=True,
                api_priority=95,
                rollout_stage="api_primary",
                notes="NetSuite REST bill posting, vendor-credit application, and standard vendor payments are API-first.",
            ),
            "sap": ConnectorCapability(
                erp_type="sap",
                supports_api_post_bill=True,
                supports_api_apply_credit=True,
                supports_api_apply_settlement=True,
                api_priority=90,
                rollout_stage="api_primary",
                notes="SAP Business One Service Layer bill posting, standard purchase-credit-note creation, and standard vendor payments are API-first.",
            ),
            "unconfigured": ConnectorCapability(
                erp_type="unconfigured",
                supports_api_post_bill=False,
                supports_api_apply_credit=False,
                supports_api_apply_settlement=False,
                api_priority=0,
                rollout_stage="manual_only",
                notes="No ERP connector configured; manual review required.",
            ),
            "unknown": ConnectorCapability(
                erp_type="unknown",
                supports_api_post_bill=False,
                supports_api_apply_credit=False,
                supports_api_apply_settlement=False,
                api_priority=0,
                rollout_stage="disabled",
                notes="Unknown connector type; fail safe until connector capability is declared.",
            ),
        }

    def resolve(self, erp_type: str) -> ConnectorCapability:
        key = str(erp_type or "").strip().lower() or "unconfigured"
        return self._capabilities.get(key, self._capabilities["unknown"])

    def list_capabilities(self) -> List[Dict[str, Any]]:
        rows = [cap.as_dict() for cap in self._capabilities.values()]
        return sorted(rows, key=lambda row: (-int(row.get("api_priority") or 0), str(row.get("erp_type") or "")))

    def build_route_plan(
        self,
        *,
        erp_type: str,
        connection_present: bool,
        action: str = "post_bill",
    ) -> Dict[str, Any]:
        capability = self.resolve(erp_type if connection_present else "unconfigured")
        normalized_action = str(action or "post_bill").strip().lower() or "post_bill"
        api_supported = False
        if normalized_action == "post_bill":
            api_supported = bool(capability.supports_api_post_bill)
        elif normalized_action == "apply_credit":
            api_supported = bool(capability.supports_api_apply_credit)
        elif normalized_action == "apply_settlement":
            api_supported = bool(capability.supports_api_apply_settlement)

        if api_supported and connection_present:
            primary_mode = "api"
        else:
            primary_mode = "manual_review"
        return {
            "erp_type": capability.erp_type,
            "action": normalized_action,
            "connection_present": bool(connection_present),
            "rollout_stage": capability.rollout_stage,
            "primary_mode": primary_mode,
            "api_supported": bool(api_supported),
            "api_priority": int(capability.api_priority),
            "notes": capability.notes,
        }


_STRATEGY = ERPConnectorStrategy()


def get_erp_connector_strategy() -> ERPConnectorStrategy:
    return _STRATEGY
