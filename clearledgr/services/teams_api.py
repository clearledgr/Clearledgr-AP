"""Microsoft Teams helpers for AP approval and KPI card delivery."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Bot Framework OAuth token endpoint for client-credentials flow
_BOT_FRAMEWORK_TOKEN_URL = "https://login.botframework.com/v1/.oauth/token"
# Cache: {"token": str, "expires_at": float}
_bot_token_cache: Dict[str, Any] = {}


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
    def from_env(cls, organization_id: Optional[str] = None) -> "TeamsAPIClient":
        webhook_url = ""
        if organization_id:
            try:
                from clearledgr.core.database import get_db

                db = get_db()
                integration = db.get_organization_integration(organization_id, "teams") or {}
                metadata = integration.get("metadata") if isinstance(integration.get("metadata"), dict) else {}
                webhook_url = str((metadata or {}).get("webhook_url") or "").strip()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Could not load Teams integration metadata for %s: %s", organization_id, exc)
        if not webhook_url:
            webhook_url = str(os.getenv("TEAMS_APPROVAL_WEBHOOK_URL", "")).strip()
        return cls(webhook_url=webhook_url)

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
        decision_reason_summary: Optional[str] = None,
        next_step_lines: Optional[List[str]] = None,
        requested_by_text: Optional[str] = None,
        source_of_truth_text: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = str((budget or {}).get("status") or "unknown")
        requires_decision = bool((budget or {}).get("requires_decision"))
        normalized_reason = str(decision_reason_summary or "").strip()
        next_step_lines = [str(line).strip() for line in (next_step_lines or []) if str(line).strip()]
        requested_by = str(requested_by_text or "Requested by Clearledgr AP Agent on behalf of the AP workflow.").strip()
        source_of_truth = str(source_of_truth_text or "Source of truth: Gmail thread and Clearledgr AP context.").strip()
        gmail_url = str(source_url or f"https://mail.google.com/mail/u/0/#search/{email_id}").strip()

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
        if normalized_reason:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": f"**Why this needs your decision:** {normalized_reason}",
                }
            )
        if next_step_lines:
            body.append({"type": "TextBlock", "wrap": True, "text": "**What happens next**"})
            for line in next_step_lines[:3]:
                body.append({"type": "TextBlock", "wrap": True, "spacing": "None", "text": f"• {line}"})
        if requires_decision:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "color": "Attention",
                    "text": "Decision required: Approve override (with justification), request budget adjustment, or reject.",
                }
            )
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "Small", "text": requested_by})
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "None", "text": source_of_truth})

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
                        "title": "Request info",
                        "data": {
                            "action": "request_info",
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
                    {
                        "type": "Action.Submit",
                        "title": "Request info",
                        "data": {
                            "action": "request_info",
                            "email_id": email_id,
                            "organization_id": organization_id,
                            "justification": "Additional info requested in Teams",
                        },
                    },
                ]
            )
        if gmail_url:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "Open Gmail context",
                    "url": gmail_url,
                }
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

    # ------------------------------------------------------------------
    # Bot Framework card update (replaces the original approval card
    # with a result card once the approver has acted)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_bot_framework_token() -> Optional[str]:
        """Acquire (and cache) an OAuth2 client-credentials token for the
        Bot Framework REST API.  Requires ``TEAMS_APP_ID`` and
        ``TEAMS_APP_PASSWORD`` environment variables.

        Returns the bearer token string, or ``None`` if credentials are
        not configured or the request fails.
        """
        app_id = os.getenv("TEAMS_APP_ID", "").strip()
        app_password = os.getenv("TEAMS_APP_PASSWORD", "").strip()
        if not app_id or not app_password:
            return None

        cached = _bot_token_cache
        if cached.get("token") and time.time() < cached.get("expires_at", 0) - 60:
            return str(cached["token"])

        payload = (
            f"grant_type=client_credentials"
            f"&client_id={app_id}"
            f"&client_secret={app_password}"
            f"&scope=https%3A%2F%2Fapi.botframework.com%2F.default"
        ).encode("utf-8")

        req = Request(
            _BOT_FRAMEWORK_TOKEN_URL,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 3600))
            if token:
                _bot_token_cache["token"] = token
                _bot_token_cache["expires_at"] = time.time() + expires_in
                return token
        except Exception as exc:
            logger.warning("Failed to acquire Bot Framework token: %s", exc)
        return None

    def update_activity(
        self,
        *,
        service_url: str,
        conversation_id: str,
        activity_id: str,
        result_status: str,
        actor_display: str,
        action: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update the original Teams approval card with a result summary.

        Posts to the Bot Framework REST API:
        ``{service_url}/v3/conversations/{conversationId}/activities/{activityId}``

        The updated card replaces the action buttons with a plain result
        so operators see the decision immediately in their Teams channel.

        Returns a dict with ``status`` (``"updated"``, ``"skipped"``, or
        ``"error"``) and any relevant detail.
        """
        token = self._get_bot_framework_token()
        if not token:
            return {"status": "skipped", "reason": "bot_framework_credentials_not_configured"}

        service_url = service_url.rstrip("/")
        url = f"{service_url}/v3/conversations/{conversation_id}/activities/{activity_id}"

        icon = "✅" if action == "approve" else ("❌" if action == "reject" else "ℹ️")
        result_text = f"{icon} {actor_display} — {result_status.replace('_', ' ').title()}"
        if reason:
            result_text += f"\n> {reason}"

        updated_card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Medium",
                                "weight": "Bolder",
                                "text": "Invoice Approval — Decision Recorded",
                            },
                            {
                                "type": "TextBlock",
                                "wrap": True,
                                "text": result_text,
                            },
                        ],
                    },
                }
            ],
        }

        body = json.dumps(updated_card).encode("utf-8")
        req = Request(
            url,
            data=body,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=10) as resp:
                status_code = int(getattr(resp, "status", 200))
            if 200 <= status_code < 300:
                return {"status": "updated", "status_code": status_code}
            return {"status": "error", "status_code": status_code}
        except URLError as exc:
            logger.error("Teams card update failed: %s", exc)
            return {"status": "error", "reason": str(exc)}
        except Exception as exc:
            logger.error("Teams card update unexpected error: %s", exc)
            return {"status": "error", "reason": str(exc)}

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
        decision_reason_summary: Optional[str] = None,
        next_step_lines: Optional[List[str]] = None,
        requested_by_text: Optional[str] = None,
        source_of_truth_text: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        card = self.build_invoice_budget_card(
            email_id=email_id,
            organization_id=organization_id,
            vendor=vendor,
            amount=amount,
            currency=currency,
            invoice_number=invoice_number,
            budget=budget,
            decision_reason_summary=decision_reason_summary,
            next_step_lines=next_step_lines,
            requested_by_text=requested_by_text,
            source_of_truth_text=source_of_truth_text,
            source_url=source_url,
        )
        result = self._post_json(card)
        result["card"] = card
        return result

    @staticmethod
    def build_ap_kpi_digest_card(kpis: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        kpis = kpis or {}
        agentic = kpis.get("agentic_telemetry") if isinstance(kpis, dict) else {}
        agentic = agentic if isinstance(agentic, dict) else {}

        def _percent(metric: Any) -> float:
            if isinstance(metric, dict):
                raw = metric.get("value", metric.get("rate"))
            else:
                raw = metric
            value = _safe_float(raw)
            if 0 <= value <= 1:
                return value * 100.0
            return value

        def _hours(metric: Any) -> float:
            if isinstance(metric, dict):
                return _safe_float(metric.get("avg_hours", metric.get("avg")))
            return _safe_float(metric)

        touchless = _percent(kpis.get("touchless_rate"))
        exception_rate = _percent(kpis.get("exception_rate"))
        cycle_time = _hours(kpis.get("cycle_time_hours"))
        on_time = _percent(kpis.get("on_time_approvals"))
        fallback_rate = _percent(agentic.get("erp_browser_fallback_rate"))
        accepted_rate = _percent(agentic.get("agent_suggestion_acceptance"))
        manual_override_rate = _percent(agentic.get("agent_actions_requiring_manual_override"))
        awaiting_hours = _hours(agentic.get("awaiting_approval_time_hours"))
        window_hours = int(agentic.get("window_hours") or 0)

        blocker_lines: List[str] = []
        blockers = agentic.get("top_blocker_reasons")
        if isinstance(blockers, dict):
            top = blockers.get("top_reasons")
            if isinstance(top, list):
                for entry in top[:3]:
                    if not isinstance(entry, dict):
                        continue
                    reason = str(entry.get("reason") or "").replace("_", " ").strip()
                    count = int(entry.get("count") or 0)
                    if reason:
                        blocker_lines.append(f"• {reason} ({count})")
        blocker_text = "\n".join(blocker_lines) if blocker_lines else "No blocker telemetry yet."

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
                            {
                                "type": "TextBlock",
                                "weight": "Bolder",
                                "text": "Agentic telemetry" + (f" ({window_hours}h window)" if window_hours > 0 else ""),
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Browser fallback", "value": f"{fallback_rate:.1f}%"},
                                    {"title": "Agent accepted", "value": f"{accepted_rate:.1f}%"},
                                    {"title": "Manual override req.", "value": f"{manual_override_rate:.1f}%"},
                                    {"title": "Awaiting approval", "value": f"{awaiting_hours:.1f}h"},
                                ],
                            },
                            {"type": "TextBlock", "wrap": True, "text": f"Top blockers:\n{blocker_text}"},
                        ],
                    },
                }
            ],
        }
