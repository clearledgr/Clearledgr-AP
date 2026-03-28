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
    "entity_route_review_required": "Choose the legal entity before approval can continue.",
    "assignee_required": "A new approver is required before approval can be reassigned.",
    "illegal_transition": "Requested action is not allowed from the current invoice status.",
    "browser_session_created": "Prepared secure ERP browser fallback session.",
    "state_not_ready_for_approval": "Invoice is not ready to request approval.",
    "state_not_waiting_for_approval": "Invoice is no longer waiting for approval.",
    "state_not_request_info_allowed": "Invoice cannot be sent back for more information from its current status.",
    "state_not_rejectable": "Invoice cannot be rejected from its current status.",
    "rejection_reason_required": "A rejection reason is required before this invoice can be rejected.",
    "state_not_ready_to_post": "Invoice is not ready to post yet.",
    "policy_precheck_failed": "Policy and workflow checks blocked this action.",
    "followup_attempt_limit_reached": "Clearledgr reached the vendor follow-up attempt limit.",
    "waiting_for_sla_window": "Clearledgr is waiting for the next vendor follow-up window.",
    "gmail_auth_unavailable": "Gmail authorization is required before Clearledgr can prepare the follow-up draft.",
    "draft_not_created": "Clearledgr could not prepare the vendor follow-up draft.",
    "retry_not_recoverable": "This posting failure is not safe to retry automatically.",
    "finance_summary_email_draft": "Prepared a finance summary email draft.",
    "fallback_preview_confirmed_and_dispatched": "ERP fallback session was confirmed and dispatched.",
    "runtime_request_approval": "Clearledgr routed this invoice into the approval queue.",
    "runtime_approve_invoice": "Approval was recorded and the workflow moved forward.",
    "runtime_request_info": "Clearledgr moved this invoice back to needs info.",
    "runtime_reject_invoice": "Clearledgr recorded the rejection decision.",
    "runtime_post_to_erp": "Clearledgr completed the ERP posting action.",
    "runtime_escalate_approval": "Clearledgr escalated this approval request for finance review.",
    "runtime_reassign_approval": "Clearledgr reassigned this approval request to a new approver.",
    "agent_runtime_route_low_risk_for_approval": "Clearledgr routed this low-risk invoice for approval.",
    "batch_retry_recoverable_failures": "Clearledgr retried the posting step for this invoice.",
    "runtime_escalate_invoice_review": "Clearledgr escalated this invoice for review.",
    "runtime_record_field_correction": "Recorded an operator correction on this invoice.",
    "manual_entity_route_resolution": "The legal entity route was resolved for this invoice.",
}


_HIGH_IMPORTANCE_EVENT_TYPES = {
    "approval_request_blocked",
    "approval_request_failed",
    "approval_routed_from_extension",
    "approval_request_routed",
    "route_for_approval",
    "route_low_risk_for_approval",
    "invoice_approved",
    "invoice_approval_failed",
    "invoice_approval_blocked",
    "invoice_rejected",
    "invoice_reject_failed",
    "invoice_reject_blocked",
    "erp_post_completed",
    "erp_api_success",
    "erp_browser_fallback_completed",
    "erp_post_failed",
    "erp_api_failed",
    "erp_browser_fallback_failed",
    "erp_post_blocked",
    "invoice_escalated",
    "approval_escalation_sent",
    "approval_escalation_deduped",
    "approval_escalation_failed",
    "approval_reassigned",
    "approval_reassignment_failed",
    "state_transition_rejected",
}

_LOW_IMPORTANCE_EVENT_TYPES = {
    "browser_session_created",
    "erp_api_fallback_preview_created",
    "erp_api_fallback_confirmation_captured",
    "erp_api_fallback_requested",
    "finance_summary_share_previewed",
    "finance_summary_share_prepared",
    "finance_summary_shared",
    "finance_summary_share_failed",
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
    lowered = text.lower()
    if lowered.startswith("approval_nudge_auto_") and lowered.endswith("h"):
        hours = lowered[len("approval_nudge_auto_"):-1].replace("_", ".")
        return f"Agent sent an automatic approval reminder after {hours} hours pending."
    if lowered.startswith("approval_escalation_auto_") and lowered.endswith("h"):
        hours = lowered[len("approval_escalation_auto_"):-1].replace("_", ".")
        return f"Agent escalated the approval request after {hours} hours pending."
    direct = _REASON_LABELS.get(text.lower())
    if direct:
        return direct
    codes = _parse_reason_codes(text)
    if not codes:
        if _is_reason_code(lowered):
            return _REASON_LABELS.get(lowered, f"{_humanize_snake_text(lowered)}.")
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


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _dict_value(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _channel_label(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == "slack":
        return "Slack"
    if token == "teams":
        return "Teams"
    if token in {"gmail", "gmail_extension", "gmail_route"}:
        return "Gmail"
    if token in {"approval_surface", "channel"}:
        return "Approval surface"
    return ""


def _extract_event_context(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = _dict_value(event.get("payload_json"))
    response = _dict_value(payload.get("response"))
    result = _dict_value(payload.get("result"))
    erp_result = _dict_value(result.get("erp_result"))
    metadata = _dict_value(event.get("metadata"))
    canonical = _dict_value(payload.get("canonical_audit_event"))
    event_type = _normalize_event_type(event.get("event_type") or event.get("eventType"))
    from_state = str(event.get("from_state") or payload.get("from_state") or payload.get("fromState") or "").strip()
    to_state = str(event.get("to_state") or payload.get("to_state") or payload.get("toState") or "").strip()
    reason_raw = _event_reason(event, payload)
    reason_codes = _parse_reason_codes(reason_raw)
    channel = _channel_label(
        _first_text(
            payload.get("source_channel"),
            response.get("source_channel"),
            result.get("source_channel"),
            metadata.get("source_channel"),
            event.get("source"),
        )
    )
    return {
        "payload": payload,
        "response": response,
        "result": result,
        "erp_result": erp_result,
        "metadata": metadata,
        "canonical": canonical,
        "event_type": event_type,
        "from_state": from_state,
        "to_state": to_state,
        "reason_raw": reason_raw,
        "reason": _reason_message(reason_raw),
        "reason_codes": reason_codes,
        "channel": channel,
    }


def _operator_importance(event_type: str, to_state: str) -> str:
    if event_type in _HIGH_IMPORTANCE_EVENT_TYPES:
        return "high"
    if event_type in _LOW_IMPORTANCE_EVENT_TYPES:
        return "low"
    if event_type == "state_transition":
        target = str(to_state or "").strip().lower()
        if target in {"needs_approval", "approved", "rejected", "failed_post", "posted_to_erp"}:
            return "high"
        if target in {"validated", "ready_to_post", "needs_info", "closed"}:
            return "medium"
    return "medium"


def _operator_category(event_type: str, to_state: str) -> str:
    token = str(event_type or "").strip().lower()
    target = str(to_state or "").strip().lower()
    if "approval" in token or token in {"invoice_approved", "invoice_rejected"} or target in {"needs_approval", "approved", "rejected"}:
        return "approval"
    if "erp" in token or "post" in token or target in {"ready_to_post", "posted_to_erp", "failed_post", "closed"}:
        return "posting"
    if "followup" in token or "summary" in token or "escalated" in token:
        return "collaboration"
    if "field_correction" in token:
        return "correction"
    if "blocked" in token or "validation" in token or token == "state_transition_rejected":
        return "policy"
    if token.startswith("browser_"):
        return "system"
    return "record"


def _operator_evidence(event: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
    payload = context["payload"]
    response = context["response"]
    result = context["result"]
    erp_result = context["erp_result"]
    metadata = context["metadata"]
    canonical = context["canonical"]
    event_type = context["event_type"]
    channel = context["channel"]

    field_name = _humanize_snake_text(payload.get("field") or metadata.get("field") or "")
    erp_reference = _first_text(
        payload.get("erp_reference"),
        response.get("erp_reference"),
        result.get("erp_reference"),
        erp_result.get("doc_num"),
        erp_result.get("document_number"),
        erp_result.get("erp_document"),
        erp_result.get("bill_id"),
    )
    evidence_refs = canonical.get("evidence_refs") if isinstance(canonical.get("evidence_refs"), list) else []
    source_ref = _first_text(
        payload.get("email_id"),
        response.get("email_id"),
        result.get("email_id"),
        event.get("source_message_ref"),
        evidence_refs[0] if evidence_refs else "",
    )

    if event_type in {
        "approval_routed_from_extension",
        "approval_request_routed",
        "route_for_approval",
        "route_low_risk_for_approval",
        "approval_request_blocked",
        "approval_request_failed",
        "invoice_approved",
        "invoice_approval_failed",
        "invoice_approval_blocked",
        "invoice_rejected",
        "invoice_reject_failed",
        "invoice_reject_blocked",
        "approval_nudge_sent",
        "approval_nudge_failed",
        "approval_nudge_blocked",
    }:
        label = f"{channel} approval action" if channel else "Approval action"
        detail = (
            f"Recorded from {channel} approval workflow."
            if channel
            else "Recorded from the approval workflow."
        )
        return {"label": label, "detail": detail}

    if event_type in {"erp_post_completed", "erp_api_success", "erp_browser_fallback_completed", "erp_post_failed", "erp_api_failed", "erp_browser_fallback_failed"}:
        detail = "Recorded from the ERP connector response."
        if erp_reference:
            detail = f"Recorded from the ERP connector response ({erp_reference})."
        return {"label": "ERP result", "detail": detail}

    if event_type in {"deterministic_validation_failed", "state_transition_rejected", "approval_request_blocked", "erp_post_blocked", "route_low_risk_for_approval_blocked"}:
        return {"label": "Policy check", "detail": "Recorded from workflow and policy guardrails."}

    if event_type in {"vendor_followup_waiting_sla", "vendor_followup_blocked", "vendor_followup_failed", "vendor_followup_draft_prepared"}:
        return {"label": "Vendor follow-up", "detail": "Recorded from the vendor information request workflow."}

    if event_type in {"finance_summary_share_previewed", "finance_summary_share_prepared", "finance_summary_shared", "finance_summary_share_failed"}:
        return {"label": "Record summary", "detail": "Prepared from the current invoice record and audit history."}

    if event_type == "field_correction":
        detail = f"Recorded from operator correction to {field_name.lower()}." if field_name else "Recorded from an operator field correction."
        return {"label": "Field correction", "detail": detail}

    if event_type in {"ap_item_resubmitted", "ap_item_resubmission_created", "ap_item_merged", "ap_item_merged_into", "ap_item_split_created"}:
        return {"label": "Record change", "detail": "Recorded from a shared AP record update."}

    if event_type == "invoice_escalated":
        return {"label": "Review escalation", "detail": "Recorded from the finance review escalation flow."}

    if event_type == "state_transition":
        target = str(context["to_state"] or "").strip().lower()
        if target in {"posted_to_erp", "failed_post"}:
            detail = "Recorded from the ERP connector response."
            if erp_reference:
                detail = f"Recorded from the ERP connector response ({erp_reference})."
            return {"label": "ERP result", "detail": detail}
        if target in {"ready_to_post", "closed"}:
            return {"label": "ERP workflow", "detail": "Recorded from the AP posting workflow state change."}
        if target in {"needs_approval", "approved", "rejected"}:
            label = f"{channel} approval action" if channel else "Approval action"
            detail = (
                f"Recorded from {channel} approval workflow state."
                if channel
                else "Recorded from the approval workflow state."
            )
            return {"label": label, "detail": detail}
        if target == "needs_info":
            return {"label": "Missing information workflow", "detail": "Recorded from the AP information request state change."}

    if source_ref:
        return {"label": "Source email", "detail": "Recorded from the source email and extracted invoice fields."}
    return {"label": "Workflow record", "detail": "Recorded from the shared AP workflow record."}


def _finalize_operator_view(event: Dict[str, Any], operator: Dict[str, Any]) -> Dict[str, Any]:
    context = _extract_event_context(event)
    event_type = context["event_type"]
    to_state = context["to_state"]
    enriched = dict(operator or {})
    enriched["importance"] = str(
        enriched.get("importance")
        or _operator_importance(event_type, to_state)
    )
    enriched["category"] = str(
        enriched.get("category")
        or _operator_category(event_type, to_state)
    )
    evidence = enriched.get("evidence") if isinstance(enriched.get("evidence"), dict) else {}
    if not evidence:
        evidence = _operator_evidence(event, context)
    enriched["evidence"] = evidence
    return enriched


def _operator_view_for_event(event: Dict[str, Any]) -> Dict[str, Any]:
    context = _extract_event_context(event)
    payload = context["payload"]
    event_type = context["event_type"]
    from_state = context["from_state"]
    to_state = context["to_state"]
    reason = context["reason"]
    reason_codes = context["reason_codes"]

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
                "title": "Approval requested",
                "message": reason or "Sent to approver in Slack or Teams.",
                "severity": "info",
                "next_action": "Wait for approval callback or send a reminder.",
            }
        )
        return operator

    if event_type == "approval_request_routed":
        operator.update(
            {
                "code": "approval_request_sent",
                "title": "Approval requested",
                "message": reason or "Clearledgr routed this invoice to the approver.",
                "severity": "info",
                "next_action": "Wait for approval callback or send a reminder.",
            }
        )
        return operator

    if event_type == "approval_request_blocked":
        operator.update(
            {
                "code": "approval_request_blocked",
                "title": "Approval request blocked",
                "message": reason or "This invoice could not be sent for approval from its current status.",
                "severity": "warning",
                "next_action": "Review the current blocker, then retry approval routing.",
            }
        )
        return operator

    if event_type == "approval_request_failed":
        operator.update(
            {
                "code": "approval_request_failed",
                "title": "Approval request failed",
                "message": reason or "Clearledgr could not send this invoice for approval.",
                "severity": "warning",
                "next_action": "Retry approval routing or review the connected approval channel.",
            }
        )
        return operator

    if event_type == "invoice_approved":
        operator.update(
            {
                "code": "invoice_approved",
                "title": "Approval received",
                "message": reason or "Approver decision was recorded and posting continued.",
                "severity": "success",
                "next_action": "Wait for ERP posting result if this invoice is still processing.",
            }
        )
        return operator

    if event_type == "invoice_rejected":
        operator.update(
            {
                "code": "invoice_rejected",
                "title": "Rejected",
                "message": reason or "Rejection was recorded and the invoice was stopped.",
                "severity": "warning",
                "next_action": "No action required unless the invoice is reopened or resubmitted.",
            }
        )
        return operator

    if event_type == "invoice_approval_failed":
        operator.update(
            {
                "code": "invoice_approval_failed",
                "title": "Approval could not complete",
                "message": reason or "Approval was received, but Clearledgr could not finish the next step.",
                "severity": "warning",
                "next_action": "Review the current invoice state and retry the allowed next step.",
            }
        )
        return operator

    if event_type == "invoice_approval_blocked":
        operator.update(
            {
                "code": "invoice_approval_blocked",
                "title": "Approval could not be recorded",
                "message": reason or "This invoice is no longer waiting for approval.",
                "severity": "warning",
                "next_action": "Refresh the invoice and use the allowed next step.",
            }
        )
        return operator

    if event_type == "info_request_recorded":
        operator.update(
            {
                "code": "info_request_recorded",
                "title": "More information requested",
                "message": reason or "This invoice was sent back for more information.",
                "severity": "info",
                "next_action": "Wait for the missing details, then continue review.",
            }
        )
        return operator

    if event_type == "info_request_blocked":
        operator.update(
            {
                "code": "info_request_blocked",
                "title": "Could not request more information",
                "message": reason or "This invoice cannot be moved to needs info from its current status.",
                "severity": "warning",
                "next_action": "Refresh the invoice and retry the allowed next step.",
            }
        )
        return operator

    if event_type == "info_request_failed":
        operator.update(
            {
                "code": "info_request_failed",
                "title": "Could not request more information",
                "message": reason or "Clearledgr could not move this invoice back to needs info.",
                "severity": "warning",
                "next_action": "Refresh the invoice and retry the allowed next step.",
            }
        )
        return operator

    if event_type == "approval_nudge_blocked":
        operator.update(
            {
                "code": "approval_reminder_blocked",
                "title": "Reminder could not be sent",
                "message": reason or "This invoice is no longer waiting for approval.",
                "severity": "warning",
                "next_action": "Refresh the invoice and use the allowed next step.",
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

    if event_type == "approval_escalation_blocked":
        operator.update(
            {
                "code": "approval_escalation_blocked",
                "title": "Escalation blocked",
                "message": reason or "This invoice is no longer waiting on approval.",
                "severity": "warning",
                "next_action": "Refresh the invoice and use the allowed next step.",
            }
        )
        return operator

    if event_type == "approval_escalation_failed":
        operator.update(
            {
                "code": "approval_escalation_failed",
                "title": "Escalation failed",
                "message": reason or "Clearledgr could not escalate this approval request.",
                "severity": "warning",
                "next_action": "Retry the escalation or reassign the approver.",
            }
        )
        return operator

    if event_type == "approval_escalation_sent":
        operator.update(
            {
                "code": "approval_escalation_sent",
                "title": "Approval escalated",
                "message": reason or "Clearledgr escalated this approval request.",
                "severity": "info",
                "next_action": "Wait for the escalated review or reassign the approver.",
            }
        )
        return operator

    if event_type == "approval_escalation_deduped":
        operator.update(
            {
                "code": "approval_escalation_deduped",
                "title": "Escalation suppressed",
                "message": reason or "Clearledgr skipped a duplicate escalation because the invoice was escalated recently.",
                "severity": "info",
                "next_action": "Wait for the existing escalation thread or reassign the approver.",
            }
        )
        return operator

    if event_type == "approval_reassignment_blocked":
        operator.update(
            {
                "code": "approval_reassignment_blocked",
                "title": "Reassignment blocked",
                "message": reason or "This invoice cannot be reassigned from its current status.",
                "severity": "warning",
                "next_action": "Refresh the invoice and retry only if approval is still pending.",
            }
        )
        return operator

    if event_type == "approval_reassignment_failed":
        operator.update(
            {
                "code": "approval_reassignment_failed",
                "title": "Reassignment failed",
                "message": reason or "Clearledgr could not reassign this approval request.",
                "severity": "warning",
                "next_action": "Retry with a valid approver or send an escalation.",
            }
        )
        return operator

    if event_type == "approval_reassigned":
        operator.update(
            {
                "code": "approval_reassigned",
                "title": "Approval reassigned",
                "message": reason or "A new approver now owns this approval request.",
                "severity": "info",
                "next_action": "Wait for the new approver or send a reminder later.",
            }
        )
        return operator

    if event_type == "entity_route_resolved":
        operator.update(
            {
                "code": "entity_route_resolved",
                "title": "Entity route resolved",
                "message": reason or "The legal entity was selected for this invoice.",
                "severity": "info",
                "next_action": "Continue approval routing from Pipeline.",
            }
        )
        return operator

    if event_type == "invoice_reject_blocked":
        operator.update(
            {
                "code": "invoice_reject_blocked",
                "title": "Rejection blocked",
                "message": reason or "This invoice cannot be rejected from its current status.",
                "severity": "warning",
                "next_action": "Refresh the invoice and use the allowed next step.",
            }
        )
        return operator

    if event_type == "invoice_reject_failed":
        operator.update(
            {
                "code": "invoice_reject_failed",
                "title": "Rejection failed",
                "message": reason or "Clearledgr could not reject this invoice.",
                "severity": "warning",
                "next_action": "Retry rejection or review the current invoice status.",
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

    if event_type == "erp_post_blocked":
        operator.update(
            {
                "code": "erp_post_blocked",
                "title": "ERP posting blocked",
                "message": reason or "This invoice is not ready to post yet.",
                "severity": "warning",
                "next_action": "Complete the required approval or review step first.",
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
        target_state = str(to_state).strip().lower()
        if target_state == "needs_approval":
            operator.update(
                {
                    "code": "approval_request_sent",
                    "title": "Approval requested",
                    "message": reason or "Invoice moved into the approval queue.",
                    "severity": "info",
                    "next_action": "Wait for approval callback or send a reminder.",
                }
            )
            return operator
        if target_state == "approved":
            operator.update(
                {
                    "code": "invoice_approved",
                    "title": "Approval received",
                    "message": reason or "Approval was recorded for this invoice.",
                    "severity": "success",
                    "next_action": "Wait for the posting step or continue to ERP posting.",
                }
            )
            return operator
        if target_state == "rejected":
            operator.update(
                {
                    "code": "invoice_rejected",
                    "title": "Rejected",
                    "message": reason or "Invoice was moved to rejected.",
                    "severity": "warning",
                    "next_action": "No action required unless the invoice is reopened or resubmitted.",
                }
            )
            return operator
        if target_state in {"failed_post"}:
            operator.update(
                {
                    "code": "erp_post_failed",
                    "title": "Posting failed",
                    "message": reason or "Clearledgr could not complete ERP posting.",
                    "severity": "error",
                    "next_action": "Retry ERP posting or review the connector result.",
                }
            )
            return operator
        if target_state in {"posted_to_erp", "closed"}:
            operator.update(
                {
                    "code": "erp_posted",
                    "title": "Posted to ERP" if target_state == "posted_to_erp" else "Record closed",
                    "message": reason or ("Invoice posting completed successfully." if target_state == "posted_to_erp" else "Invoice record was closed."),
                    "severity": "success",
                    "next_action": "No action required.",
                }
            )
            return operator
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

    if event_type == "erp_post_completed":
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
                "title": "Posting failed",
                "message": reason or "Posting did not complete.",
                "severity": "error",
                "next_action": "Retry ERP post or escalate for review.",
            }
        )
        return operator

    if event_type == "erp_post_failed":
        operator.update(
            {
                "code": "erp_post_failed",
                "title": "Posting failed",
                "message": reason or "Posting did not complete.",
                "severity": "error",
                "next_action": "Retry ERP post or escalate for review.",
            }
        )
        return operator

    if event_type == "route_low_risk_for_approval":
        operator.update(
            {
                "code": "approval_request_sent",
                "title": "Approval requested",
                "message": reason or "Clearledgr routed this low-risk invoice for approval.",
                "severity": "info",
                "next_action": "Wait for approval callback.",
            }
        )
        return operator

    if event_type == "route_low_risk_for_approval_blocked":
        operator.update(
            {
                "code": "approval_request_blocked",
                "title": "Approval request blocked",
                "message": reason or "This invoice did not pass the low-risk approval checks.",
                "severity": "warning",
                "next_action": "Review the blocker and route approval manually if needed.",
            }
        )
        return operator

    if event_type == "route_low_risk_for_approval_failed":
        operator.update(
            {
                "code": "approval_request_failed",
                "title": "Approval request failed",
                "message": reason or "Clearledgr could not route this invoice for approval.",
                "severity": "warning",
                "next_action": "Retry approval routing or review the approval channel.",
            }
        )
        return operator

    if event_type == "retry_recoverable_failure_blocked":
        operator.update(
            {
                "code": "retry_blocked",
                "title": "Retry blocked",
                "message": reason or "This failed ERP post is not safe to retry automatically.",
                "severity": "warning",
                "next_action": "Review the failure and decide the next step manually.",
            }
        )
        return operator

    if event_type == "retry_recoverable_failure_completed":
        operator.update(
            {
                "code": "retry_completed",
                "title": "Retry completed",
                "message": reason or "Clearledgr retried the ERP post successfully.",
                "severity": "success",
                "next_action": "No action required.",
            }
        )
        return operator

    if event_type == "retry_recoverable_failure_failed":
        operator.update(
            {
                "code": "retry_failed",
                "title": "Retry failed",
                "message": reason or "Clearledgr could not recover this ERP posting failure.",
                "severity": "warning",
                "next_action": "Review the connector result and retry manually if appropriate.",
            }
        )
        return operator

    if event_type == "vendor_followup_waiting_sla":
        operator.update(
            {
                "code": "vendor_followup_waiting",
                "title": "Waiting before next vendor follow-up",
                "message": reason or "Clearledgr is waiting for the vendor follow-up window.",
                "severity": "info",
                "next_action": "Wait for the next scheduled follow-up time.",
            }
        )
        return operator

    if event_type == "vendor_followup_blocked":
        operator.update(
            {
                "code": "vendor_followup_blocked",
                "title": "Vendor follow-up blocked",
                "message": reason or "A vendor follow-up could not be prepared from the current invoice state.",
                "severity": "warning",
                "next_action": "Review the invoice state or required evidence first.",
            }
        )
        return operator

    if event_type == "vendor_followup_failed":
        operator.update(
            {
                "code": "vendor_followup_failed",
                "title": "Vendor follow-up failed",
                "message": reason or "Clearledgr could not prepare the vendor follow-up.",
                "severity": "warning",
                "next_action": "Retry the follow-up or draft the request manually.",
            }
        )
        return operator

    if event_type == "vendor_followup_draft_prepared":
        operator.update(
            {
                "code": "vendor_followup_prepared",
                "title": "Vendor follow-up prepared",
                "message": reason or "A vendor follow-up draft is ready to send.",
                "severity": "info",
                "next_action": "Review the draft and send it when ready.",
            }
        )
        return operator

    if event_type == "invoice_escalated":
        operator.update(
            {
                "code": "invoice_escalated",
                "title": "Escalated for review",
                "message": reason or "Clearledgr escalated this invoice for finance review.",
                "severity": "warning",
                "next_action": "Review the exception details and decide the next step.",
            }
        )
        return operator

    if event_type == "field_correction":
        field_name = _humanize_snake_text(payload.get("field") or context["metadata"].get("field") or "")
        operator.update(
            {
                "code": "field_correction",
                "title": "Field corrected",
                "message": (
                    reason
                    or (f"{field_name} was corrected on this shared invoice record." if field_name else "An operator corrected invoice data on this shared record.")
                ),
                "severity": "info",
                "next_action": "Continue the next AP step with the corrected data.",
            }
        )
        return operator

    if event_type == "finance_summary_share_previewed":
        operator.update(
            {
                "code": "finance_summary_previewed",
                "title": "Finance summary previewed",
                "message": reason or "Prepared a preview of the finance summary for this invoice.",
                "severity": "info",
                "next_action": "Share the summary if finance review is needed.",
            }
        )
        return operator

    if event_type == "finance_summary_share_prepared":
        operator.update(
            {
                "code": "finance_summary_prepared",
                "title": "Finance summary prepared",
                "message": reason or "Prepared a finance summary draft for this invoice.",
                "severity": "info",
                "next_action": "Review the draft and share it when needed.",
            }
        )
        return operator

    if event_type == "finance_summary_shared":
        operator.update(
            {
                "code": "finance_summary_shared",
                "title": "Finance summary shared",
                "message": reason or "Shared the finance summary for this invoice.",
                "severity": "info",
                "next_action": "No action required unless follow-up is needed.",
            }
        )
        return operator

    if event_type == "finance_summary_share_failed":
        operator.update(
            {
                "code": "finance_summary_share_failed",
                "title": "Finance summary share failed",
                "message": reason or "Clearledgr could not share the finance summary.",
                "severity": "warning",
                "next_action": "Retry the share action or review the delivery surface.",
            }
        )
        return operator

    if event_type == "ap_item_resubmitted":
        operator.update(
            {
                "code": "ap_item_resubmitted",
                "title": "Invoice resubmitted",
                "message": reason or "Clearledgr marked this invoice as superseded by a corrected resubmission.",
                "severity": "info",
                "next_action": "Open the new AP item to continue review.",
            }
        )
        return operator

    if event_type == "ap_item_resubmission_created":
        operator.update(
            {
                "code": "ap_item_resubmission_created",
                "title": "Corrected invoice created",
                "message": reason or "Created a new AP item for the corrected resubmission.",
                "severity": "info",
                "next_action": "Continue review on the corrected invoice record.",
            }
        )
        return operator

    if event_type == "ap_item_merged":
        operator.update(
            {
                "code": "ap_item_merged",
                "title": "Records merged",
                "message": reason or "Clearledgr merged another invoice record into this AP item.",
                "severity": "info",
                "next_action": "Review the merged sources if needed.",
            }
        )
        return operator

    if event_type == "ap_item_merged_into":
        operator.update(
            {
                "code": "ap_item_merged_into",
                "title": "Merged into another record",
                "message": reason or "This AP item was merged into another shared record.",
                "severity": "info",
                "next_action": "Open the surviving AP item if further review is needed.",
            }
        )
        return operator

    if event_type == "ap_item_split_created":
        operator.update(
            {
                "code": "ap_item_split_created",
                "title": "Split record created",
                "message": reason or "Clearledgr created a new AP item from selected invoice sources.",
                "severity": "info",
                "next_action": "Review the new split item if further action is needed.",
            }
        )
        return operator

    return operator


def normalize_operator_audit_event(event: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(event or {})
    existing_operator = row.get("operator") if isinstance(row.get("operator"), dict) else {}
    operator = _finalize_operator_view(row, _operator_view_for_event(row))
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
    row["operator_importance"] = operator.get("importance")
    row["operator_category"] = operator.get("category")
    row["operator_evidence_label"] = _first_text(
        _dict_value(operator.get("evidence")).get("label"),
        operator.get("evidence_label"),
    )
    row["operator_evidence_detail"] = _first_text(
        _dict_value(operator.get("evidence")).get("detail"),
        operator.get("evidence_detail"),
    )
    return row


def normalize_operator_audit_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_operator_audit_event(event) for event in (events or [])]
