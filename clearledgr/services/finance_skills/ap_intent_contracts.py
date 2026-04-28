"""Shared AP intent contracts used by the runtime skill."""

from __future__ import annotations

from typing import Any, Dict


_AUDIT_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "request_approval": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "approval_request_routed",
            "approval_request_blocked",
            "approval_request_failed",
        ],
    },
    "nudge_approval": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": False,
        "events": [
            "approval_nudge_sent",
            "approval_nudge_failed",
            "approval_nudge_blocked",
        ],
    },
    "escalate_approval": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": False,
        "events": [
            "approval_escalation_sent",
            "approval_escalation_failed",
            "approval_escalation_blocked",
        ],
    },
    "reassign_approval": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": False,
        "events": [
            "approval_reassigned",
            "approval_reassignment_failed",
            "approval_reassignment_blocked",
        ],
    },
    "approve_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_approved",
            "invoice_approval_blocked",
            "invoice_approval_failed",
        ],
    },
    "request_info": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "info_request_recorded",
            "info_request_blocked",
            "info_request_failed",
        ],
    },
    "reject_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_rejected",
            "invoice_reject_blocked",
            "invoice_reject_failed",
        ],
    },
    "post_to_erp": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "erp_post_completed",
            "erp_post_failed",
            "erp_post_blocked",
        ],
    },
    "prepare_vendor_followups": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": False,
        "events": [
            "vendor_followup_waiting_sla",
            "vendor_followup_blocked",
            "vendor_followup_failed",
            "vendor_followup_draft_prepared",
        ],
    },
    "route_low_risk_for_approval": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "route_low_risk_for_approval",
            "route_low_risk_for_approval_blocked",
            "route_low_risk_for_approval_failed",
        ],
    },
    "retry_recoverable_failures": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "retry_recoverable_failure_blocked",
            "retry_recoverable_failure_completed",
            "retry_recoverable_failure_failed",
        ],
    },
    "snooze_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_snoozed",
            "invoice_snooze_blocked",
            "invoice_snooze_failed",
        ],
    },
    "unsnooze_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_unsnoozed",
            "invoice_unsnooze_blocked",
            "invoice_unsnooze_failed",
        ],
    },
    "reverse_invoice_post": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_reversed",
            "invoice_reverse_blocked",
            "invoice_reverse_failed",
        ],
    },
    "manually_classify_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_manually_classified",
            "invoice_manual_classify_blocked",
            "invoice_manual_classify_failed",
        ],
    },
    "resubmit_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_resubmitted",
            "invoice_resubmit_blocked",
            "invoice_resubmit_failed",
        ],
    },
    "split_invoice": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_split",
            "invoice_split_blocked",
            "invoice_split_failed",
        ],
    },
    "merge_invoices": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoices_merged",
            "invoices_merge_blocked",
            "invoices_merge_failed",
        ],
    },
    "resolve_non_invoice_review": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "non_invoice_resolved",
            "non_invoice_resolve_blocked",
            "non_invoice_resolve_failed",
        ],
    },
    "resolve_entity_route": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "entity_route_resolved",
            "entity_route_resolve_blocked",
            "entity_route_resolve_failed",
        ],
    },
    "update_invoice_fields": {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": True,
        "events": [
            "invoice_fields_updated",
            "invoice_fields_update_blocked",
            "invoice_fields_update_failed",
        ],
    },
}

_OPERATOR_COPY: Dict[str, Dict[str, str]] = {
    "request_approval": {
        "what_happened": "Validated this invoice for approval routing from Gmail.",
        "why_now": "Clearledgr checks the current invoice state before sending an approval request.",
        "recommended_allowed": "Request approval now.",
        "recommended_blocked": "Resolve the blocking state before requesting approval.",
    },
    "approve_invoice": {
        "what_happened": "Validated that this invoice can still be approved from the approval surface.",
        "why_now": "Approval decisions are only accepted while the invoice is still waiting on approval.",
        "recommended_allowed": "Approve this invoice.",
        "recommended_blocked": "Refresh the invoice and use the allowed next step.",
    },
    "request_info": {
        "what_happened": "Validated that this invoice can be sent back for more information.",
        "why_now": "Clearledgr only records info requests while the invoice is still in a reviewable state.",
        "recommended_allowed": "Send this invoice back for more information.",
        "recommended_blocked": "Refresh the invoice and use the allowed next step.",
    },
    "nudge_approval": {
        "what_happened": "Validated that this invoice is still waiting on an approver.",
        "why_now": "Nudges are only allowed while the approval request is still pending.",
        "recommended_allowed": "Send an approval reminder.",
        "recommended_blocked": "Wait until the invoice is back in an approval-pending state.",
    },
    "escalate_approval": {
        "what_happened": "Validated that this invoice can still be escalated for approval follow-up.",
        "why_now": "Escalations are only allowed while approval is still pending.",
        "recommended_allowed": "Escalate this approval request.",
        "recommended_blocked": "Refresh the invoice and use the allowed next step.",
    },
    "reassign_approval": {
        "what_happened": "Validated that this approval request can be reassigned.",
        "why_now": "Reassignment requires a pending approval state and a new approver.",
        "recommended_allowed": "Reassign this approval request.",
        "recommended_blocked": "Provide a new approver or refresh the invoice state.",
    },
    "reject_invoice": {
        "what_happened": "Validated that this invoice can still be rejected.",
        "why_now": "Clearledgr requires a rejection reason and a rejectable state before recording the decision.",
        "recommended_allowed": "Reject this invoice with a reason.",
        "recommended_blocked": "Provide a reason or return the invoice to a rejectable state.",
    },
    "post_to_erp": {
        "what_happened": "Validated that this invoice is ready for ERP posting.",
        "why_now": "Posting is only allowed once approval and posting-readiness checks are complete.",
        "recommended_allowed": "Post this invoice to ERP.",
        "recommended_blocked": "Wait until the invoice reaches a postable state.",
    },
    "prepare_vendor_followups": {
        "what_happened": "Validated vendor follow-up draft eligibility for a needs-info item.",
        "why_now": "Follow-up attempts and SLA timing were checked before preparing a draft.",
        "recommended_allowed": "Prepare the vendor follow-up draft.",
        "recommended_blocked": "Resolve blockers or wait until the SLA window opens.",
    },
    "route_low_risk_for_approval": {
        "what_happened": "Validated AP item reviewed for low-risk approval routing.",
        "why_now": "Policy prechecks were evaluated before routing to approval surfaces.",
        "recommended_allowed": "Run route-low-risk-for-approval.",
        "recommended_blocked": "Address blockers before routing.",
    },
    "retry_recoverable_failures": {
        "what_happened": "Validated recoverability and state checks for failed-post retry.",
        "why_now": "Recoverable retry prechecks were evaluated before resume execution.",
        "recommended_allowed": "Run recoverable retry.",
        "recommended_blocked": "Resolve the blocking recoverability condition first.",
    },
    "snooze_invoice": {
        "what_happened": "Validated that this invoice can be snoozed from its current state.",
        "why_now": "Snooze is only allowed from active states (received / validated / needs_info / needs_approval / failed_post).",
        "recommended_allowed": "Snooze this invoice.",
        "recommended_blocked": "Move the invoice out of its current state before snoozing.",
    },
    "unsnooze_invoice": {
        "what_happened": "Validated that this invoice is currently snoozed and has a pre-snooze state to restore.",
        "why_now": "Unsnooze is only valid for items currently in the snoozed state.",
        "recommended_allowed": "Unsnooze this invoice and restore its prior state.",
        "recommended_blocked": "Refresh the invoice — it is no longer in the snoozed state.",
    },
    "reverse_invoice_post": {
        "what_happened": "Validated that this invoice has an active override window and can be reversed at the ERP.",
        "why_now": "Reversal is only allowed inside the override window after a successful ERP post; a reason is mandatory.",
        "recommended_allowed": "Reverse this ERP post via the override window.",
        "recommended_blocked": "Provide a reason or refresh — the override window may have expired.",
    },
    "manually_classify_invoice": {
        "what_happened": "Validated that this AP item can accept a manual classification override.",
        "why_now": "Manual classification feeds the planning engine and updates downstream routing for the new document type.",
        "recommended_allowed": "Apply the manual classification.",
        "recommended_blocked": "Provide a valid classification token before re-routing the item.",
    },
    "resubmit_invoice": {
        "what_happened": "Validated that this rejected invoice can be resubmitted as a new linked record.",
        "why_now": "Resubmission requires the source to be in the rejected state and creates an attributable supersession chain.",
        "recommended_allowed": "Create the resubmission and link it to the rejected source.",
        "recommended_blocked": "Source must be in the rejected state with a valid initial state for the new item.",
    },
    "split_invoice": {
        "what_happened": "Validated that this invoice can be split into separate AP items by line.",
        "why_now": "Splits are only allowed before posting; line totals and remaining state must reconcile.",
        "recommended_allowed": "Split this invoice into the requested groupings.",
        "recommended_blocked": "Provide a valid split spec that reconciles to the source totals.",
    },
    "merge_invoices": {
        "what_happened": "Validated that the source and target invoices are eligible for merging.",
        "why_now": "Merges are only allowed across two compatible AP items in the same organization.",
        "recommended_allowed": "Merge source into target and suppress the source from the worklist.",
        "recommended_blocked": "Resolve the eligibility blocker (org mismatch, terminal state, posted source) before merging.",
    },
    "resolve_non_invoice_review": {
        "what_happened": "Validated that this non-invoice document can be resolved with the requested outcome.",
        "why_now": "Non-invoice resolution requires a recognised document type and an outcome compatible with that type.",
        "recommended_allowed": "Apply the non-invoice resolution.",
        "recommended_blocked": "Choose an outcome compatible with the document type and provide any required reference.",
    },
    "resolve_entity_route": {
        "what_happened": "Validated entity-routing selection against this AP item's vendor + entity policy.",
        "why_now": "Entity routing assigns the bill to the correct legal entity before approval and ERP posting.",
        "recommended_allowed": "Resolve the entity route with the chosen selection.",
        "recommended_blocked": "Choose a candidate that matches the vendor + entity policy.",
    },
    "update_invoice_fields": {
        "what_happened": "Validated the proposed field changes against the AP-item column whitelist.",
        "why_now": "Field updates are clamped to the canonical schema so the ERP post stays well-formed.",
        "recommended_allowed": "Apply the field updates.",
        "recommended_blocked": "Remove or rename fields that aren't in the AP-item column whitelist.",
    },
}


def get_intent_audit_contract(intent: str) -> Dict[str, Any]:
    normalized_intent = str(intent or "").strip().lower()
    contract = _AUDIT_CONTRACTS.get(normalized_intent)
    if contract:
        return contract
    return {
        "source": "finance_agent_runtime",
        "idempotent": True,
        "mutates_ap_state": False,
        "events": [],
    }


def build_operator_copy(intent: str, *, eligible: bool) -> Dict[str, str]:
    normalized_intent = str(intent or "").strip().lower()
    copy = _OPERATOR_COPY.get(normalized_intent) or _OPERATOR_COPY["retry_recoverable_failures"]
    return {
        "what_happened": copy["what_happened"],
        "why_now": copy["why_now"],
        "recommended_now": copy["recommended_allowed"] if eligible else copy["recommended_blocked"],
    }
