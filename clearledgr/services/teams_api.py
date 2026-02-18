"""Microsoft Teams helpers for AP approval and KPI card delivery."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class TeamsAPIClient:
    """Minimal Teams card client with webhook-based delivery."""

    def __init__(self, webhook_url: Optional[str] = None, timeout_seconds: float = 5.0) -> None:
        self.webhook_url = str(webhook_url or "").strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "TeamsAPIClient":
        return cls(webhook_url=os.getenv("TEAMS_APPROVAL_WEBHOOK_URL"))

    def _post_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.webhook_url:
            return {"status": "skipped", "reason": "teams_webhook_not_configured"}

        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))
            if 200 <= status_code < 300:
                return {"status": "sent", "status_code": status_code}
            return {"status": "error", "status_code": status_code}
        except URLError as exc:
            return {"status": "error", "reason": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive for runtime-only integrations
            return {"status": "error", "reason": str(exc)}

    @staticmethod
    def _budget_rows(budget: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        checks = budget.get("checks")
        if not isinstance(checks, list):
            return rows
        for check in checks[:3]:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name") or check.get("budget_name") or "Budget")
            status = str(check.get("status") or check.get("after_approval_status") or "unknown").lower()
            remaining = _safe_float(check.get("remaining"))
            pct = _safe_float(check.get("percent_after_approval") or check.get("after_approval_percent"))
            rows.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": f"{name}: {status.upper()} | {pct:.0f}% used | ${remaining:,.2f} remaining",
                }
            )
        return rows

    @classmethod
    def build_invoice_budget_card(
        cls,
        *,
        email_id: str,
        organization_id: str,
        vendor: str,
        amount: float,
        currency: str,
        invoice_number: Optional[str],
        budget: Dict[str, Any],
    ) -> Dict[str, Any]:
        status = str((budget or {}).get("status") or "unknown")
        requires_decision = bool((budget or {}).get("requires_decision"))
        body: List[Dict[str, Any]] = [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Invoice Approval Required"},
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Vendor", "value": vendor or "Unknown"},
                    {"title": "Amount", "value": f"{currency} {_safe_float(amount):,.2f}"},
                    {"title": "Invoice #", "value": str(invoice_number or "N/A")},
                    {"title": "Budget", "value": status.replace("_", " ")},
                ],
            },
        ]
        body.extend(cls._budget_rows(budget or {}))
        if requires_decision:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "color": "Attention",
                    "text": "Decision required: Approve override (with justification), request budget adjustment, or reject.",
                }
            )

        actions: List[Dict[str, Any]] = []
        if requires_decision:
            actions.extend(
                [
                    {
                        "type": "Action.Submit",
                        "title": "Approve override",
                        "data": {
                            "action": "approve_budget_override",
                            "email_id": email_id,
                            "organization_id": organization_id,
                            "justification": "Approved over budget in Teams",
                        },
                    },
                    {
                        "type": "Action.Submit",
                        "title": "Request adjustment",
                        "data": {
                            "action": "request_budget_adjustment",
                            "email_id": email_id,
                            "organization_id": organization_id,
                            "justification": "Budget adjustment requested in Teams",
                        },
                    },
                    {
                        "type": "Action.Submit",
                        "title": "Reject",
                        "data": {
                            "action": "reject_budget",
                            "email_id": email_id,
                            "organization_id": organization_id,
                            "justification": "Rejected over budget in Teams",
                        },
                    },
                ]
            )
        else:
            actions.extend(
                [
                    {
                        "type": "Action.Submit",
                        "title": "Approve",
                        "data": {
                            "action": "approve_invoice",
                            "email_id": email_id,
                            "organization_id": organization_id,
                        },
                    },
                    {
                        "type": "Action.Submit",
                        "title": "Reject",
                        "data": {
                            "action": "reject_invoice",
                            "email_id": email_id,
                            "organization_id": organization_id,
                            "justification": "Rejected in Teams",
                        },
                    },
                ]
            )

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                        "actions": actions,
                    },
                }
            ],
        }

    def send_invoice_budget_card(
        self,
        *,
        email_id: str,
        organization_id: str,
        vendor: str,
        amount: float,
        currency: str,
        invoice_number: Optional[str],
        budget: Dict[str, Any],
    ) -> Dict[str, Any]:
        card = self.build_invoice_budget_card(
            email_id=email_id,
            organization_id=organization_id,
            vendor=vendor,
            amount=amount,
            currency=currency,
            invoice_number=invoice_number,
            budget=budget,
        )
        result = self._post_json(card)
        result["card"] = card
        return result

    @staticmethod
    def build_ap_kpi_digest_card(kpis: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        kpis = kpis or {}
        touchless = _safe_float(kpis.get("touchless_rate_pct"))
        exception_rate = _safe_float(kpis.get("exception_rate_pct"))
        cycle_time = _safe_float(kpis.get("cycle_time_hours"))
        on_time = _safe_float(kpis.get("on_time_approvals_pct"))
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"AP KPI Digest ({organization_id})"},
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Touchless rate", "value": f"{touchless:.1f}%"},
                                    {"title": "Exception rate", "value": f"{exception_rate:.1f}%"},
                                    {"title": "Cycle time", "value": f"{cycle_time:.1f}h"},
                                    {"title": "On-time approvals", "value": f"{on_time:.1f}%"},
                                ],
                            },
                        ],
                    },
                }
            ],
        }
