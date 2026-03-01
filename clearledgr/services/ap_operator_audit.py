"""Operator-facing AP audit event normalization.

Backends emit canonical audit rows. This module derives a stable operator
contract so embedded clients render plain-language status without maintaining
their own event/reason copy maps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_STATE_LABELS = {
    "received": "Received",
    "validated": "Validated",
    "needs_info": "Needs info",
    "needs_approval": "Needs approval",
    "approved": "Approved",
    "ready_to_post": "Ready to post",
    "posted_to_erp": "Posted to ERP",
    "closed": "Closed",
    "rejected": "Rejected",
    "failed_post": "Failed post",
}


_REASON_LABELS = {
    "policy_requirement_amt_500": "Policy requires approval for invoices above $500.",
    "po_match_no_gr": "PO/GR check failed because goods receipt is missing.",
    "confidence_field_review_required": "Key invoice fields need human review before posting.",
    "route_for_approval": "Approval request was sent to the approver channel.",
    "autonomous_retry_attempt": "Automatic retry was blocked to protect workflow state.",
    "autonomous_retry_failed": "Auto-retry failed and needs manual follow-up.",
    "autonomous_retry_succeeded": "Auto-retry completed successfully.",
    "approval_nudge": "Approval reminder was sent.",
    "approval_nudge_auto_4h": "Agent sent an automatic approval reminder after 4 hours pending.",
    "approval_nudge_auto_24h": "Agent escalated approval reminder after 24 hours pending.",
    "illegal_transition": "Requested action is not allowed from the current invoice status.",
    "browser_session_created": "Prepared secure ERP browser fallback session.",
}


def _humanize_snake_text(value: Any) -> str:
    text = str(value or "").strip().replace("_", " ")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _normalize_event_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text.replace("-", "_").replace(" ", "_")


def _state_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return "Unknown"
    return _STATE_LABELS.get(key, _humanize_snake_text(key))


def _is_reason_code(value: str) -> bool:
    return bool(value) and value.replace("_", "").replace("-", "").isalnum() and value == value.lower()


def _parse_reason_codes(raw: Any) -> List[str]:
    text = str(raw or "").strip().lower()
    if not text:
        return []
    parts = [part.strip() for part in text.split(",") if str(part).strip()]
    if not parts:
        return []
    if not all(_is_reason_code(part) for part in parts):
        return []
    return parts


def _reason_message(reason_raw: Any) -> str:
    text = str(reason_raw or "").strip()
    if not text:
        return ""
    codes = _parse_reason_codes(text)
    if not codes:
        return text
    lines: List[str] = []
    for code in codes:
        lines.append(_REASON_LABELS.get(code, f"{_humanize_snake_text(code)}."))
    return " ".join(lines).strip()


def _event_reason(event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    return str(
        event.get("decision_reason")
        or event.get("reason")
        or payload.get("reason")
        or payload.get("error_message_redacted")
        or payload.get("error_message")
        or ""
    ).strip()


def _operator_view_for_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload_json") if isinstance(event.get("payload_json"), dict) else {}
    event_type = _normalize_event_type(event.get("event_type") or event.get("eventType"))
    from_state = str(event.get("from_state") or payload.get("from_state") or payload.get("fromState") or "").strip()
    to_state = str(event.get("to_state") or payload.get("to_state") or payload.get("toState") or "").strip()
    reason_raw = _event_reason(event, payload)
    reason = _reason_message(reason_raw)
    reason_codes = _parse_reason_codes(reason_raw)

    operator: Dict[str, Any] = {
        "code": event_type or "audit_event",
        "title": _humanize_snake_text(event_type or "audit event"),
        "message": reason,
        "severity": "info",
        "next_action": None,
    }

    if event_type == "deterministic_validation_failed":
        operator.update(
            {
                "code": "validation_failed",
                "title": "Validation checks failed",
                "message": reason or "Invoice failed one or more validation checks.",
                "severity": "warning",
                "next_action": "Review blocking checks and route for approval.",
            }
        )
        return operator

    if event_type in {"approval_routed_from_extension", "route_for_approval"}:
        operator.update(
            {
                "code": "approval_request_sent",
                "title": "Approval request sent",
                "message": reason or "Sent to approver in Slack or Teams.",
                "severity": "info",
                "next_action": "Wait for approval callback or send a reminder.",
            }
        )
        return operator

    if event_type == "approval_nudge_failed":
        operator.update(
            {
                "code": "approval_reminder_failed",
                "title": "Approval reminder failed",
                "message": 'Could not send reminder to approver. Try "Nudge approver" again.',
                "severity": "warning",
                "next_action": 'Retry "Nudge approver".',
            }
        )
        return operator

    if event_type in {"approval_nudge", "approval_nudge_sent"}:
        operator.update(
            {
                "code": "approval_reminder_sent",
                "title": "Reminder sent",
                "message": reason or "Approval reminder was sent to the approver.",
                "severity": "info",
                "next_action": "Wait for approval callback.",
            }
        )
        return operator

    if event_type in {
        "browser_session_created",
        "erp_api_fallback_preview_created",
        "erp_api_fallback_confirmation_captured",
        "erp_api_fallback_requested",
    }:
        operator.update(
            {
                "code": "erp_backup_ready",
                "title": "ERP fallback prepared",
                "message": reason or "Prepared secure ERP browser fallback session.",
                "severity": "info",
                "next_action": "Continue approval/posting flow.",
            }
        )
        return operator

    if event_type == "state_transition_rejected":
        if "autonomous_retry_attempt" in reason_codes:
            operator.update(
                {
                    "code": "retry_paused",
                    "title": "Action blocked for safety",
                    "message": "Automatic retry was blocked to protect workflow state.",
                    "severity": "warning",
                    "next_action": "Complete required approval/policy steps, then retry.",
                }
            )
            return operator
        if "illegal_transition" in reason_codes:
            operator.update(
                {
                    "code": "step_blocked",
                    "title": "Action blocked for safety",
                    "message": "Requested action is not allowed from the current invoice status.",
                    "severity": "warning",
                    "next_action": "Run the allowed next step for the current status.",
                }
            )
            return operator
        operator.update(
            {
                "code": "step_blocked",
                "title": "Action blocked for safety",
                "message": reason or "Requested action is not allowed from the current invoice status.",
                "severity": "warning",
                "next_action": "Use the recommended next action for the current status.",
            }
        )
        return operator

    if event_type == "state_transition":
        target_label = _state_label(to_state) if to_state else "Updated"
        detail = reason
        if from_state and to_state:
            detail = f"Moved from {_state_label(from_state)} to {_state_label(to_state)}."
        operator.update(
            {
                "code": f"state_transition:{str(to_state or '').strip().lower()}" if to_state else "state_transition",
                "title": f"Status updated: {target_label}",
                "message": detail,
                "severity": (
                    "success"
                    if str(to_state).strip().lower() in {"posted_to_erp", "closed"}
                    else "warning"
                    if str(to_state).strip().lower() in {"failed_post", "rejected"}
                    else "info"
                ),
                "next_action": None,
            }
        )
        return operator

    if event_type in {"erp_api_success", "erp_browser_fallback_completed"}:
        operator.update(
            {
                "code": "erp_posted",
                "title": "Posted to ERP",
                "message": reason or "Invoice posting completed successfully.",
                "severity": "success",
                "next_action": "No action required.",
            }
        )
        return operator

    if event_type in {"erp_api_failed", "erp_browser_fallback_failed"}:
        operator.update(
            {
                "code": "erp_post_failed",
                "title": "ERP posting failed",
                "message": reason or "Posting did not complete.",
                "severity": "error",
                "next_action": "Retry ERP post or escalate for review.",
            }
        )
        return operator

    return operator


def normalize_operator_audit_event(event: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(event or {})
    existing_operator = row.get("operator") if isinstance(row.get("operator"), dict) else {}
    operator = _operator_view_for_event(row)
    if existing_operator:
        merged = dict(existing_operator)
        # Canonical operator mapping wins over stale/legacy operator payloads.
        for key, value in operator.items():
            if value not in (None, "", []):
                merged[key] = value
        operator = merged

    row["operator"] = operator
    row["operator_code"] = operator.get("code")
    row["operator_title"] = operator.get("title")
    row["operator_message"] = operator.get("message")
    row["operator_severity"] = operator.get("severity")
    row["operator_next_action"] = operator.get("next_action")
    row["operator_action_hint"] = operator.get("next_action")
    return row


def normalize_operator_audit_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_operator_audit_event(event) for event in (events or [])]
