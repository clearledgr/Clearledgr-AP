"""AP skill module for the finance-agent runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.finance_contracts import SkillCapabilityManifest
from clearledgr.services.finance_skills.base import FinanceSkill
from clearledgr.services.invoice_workflow import get_invoice_workflow


class APFinanceSkill(FinanceSkill):
    """Finance skill for AP v1 operational intents."""

    _INTENTS = frozenset(
        {
            "request_approval",
            "approve_invoice",
            "request_info",
            "nudge_approval",
            "reject_invoice",
            "post_to_erp",
            "prepare_vendor_followups",
            "route_low_risk_for_approval",
            "retry_recoverable_failures",
        }
    )
    _MANIFEST = SkillCapabilityManifest(
        skill_id="ap_v1",
        version="1.0",
        state_machine={
            "primary_path": [
                "received",
                "validated",
                "needs_approval",
                "approved",
                "ready_to_post",
                "posted_to_erp",
                "closed",
            ],
            "exception_paths": [
                ["validated", "needs_info"],
                ["needs_approval", "rejected"],
                ["ready_to_post", "failed_post"],
                ["failed_post", "ready_to_post"],
                ["needs_info", "validated"],
            ],
            "resubmission": {
                "terminal_rejected": True,
                "linkage_fields": [
                    "supersedes_ap_item_id",
                    "supersedes_invoice_key",
                    "resubmission_reason",
                ],
            },
        },
        action_catalog=[
            {
                "intent": "request_approval",
                "class": "mutating",
                "description": "Route a validated AP item to the configured approval surface.",
            },
            {
                "intent": "approve_invoice",
                "class": "mutating",
                "description": "Record an approval decision from a channel surface and continue ERP posting flow.",
            },
            {
                "intent": "request_info",
                "class": "mutating",
                "description": "Return an AP item to needs-info with a recorded reason.",
            },
            {
                "intent": "nudge_approval",
                "class": "mutating",
                "description": "Send a reminder for an approval request that is still pending.",
            },
            {
                "intent": "reject_invoice",
                "class": "mutating",
                "description": "Reject an AP item with a recorded operator reason.",
            },
            {
                "intent": "post_to_erp",
                "class": "mutating",
                "description": "Post an approved AP item to ERP through the canonical workflow path.",
            },
            {
                "intent": "route_low_risk_for_approval",
                "class": "mutating",
                "description": "Route eligible AP items to approval surfaces.",
            },
            {
                "intent": "prepare_vendor_followups",
                "class": "mutating",
                "description": "Prepare vendor info-request follow-up draft with SLA safeguards.",
            },
            {
                "intent": "retry_recoverable_failures",
                "class": "mutating",
                "description": "Retry recoverable AP posting failures via canonical resume path.",
            },
        ],
        policy_pack={
            "deterministic_prechecks": [
                "state_guard",
                "approval_waiting_guard",
                "posting_readiness_guard",
                "recoverability_guard",
                "followup_sla_guard",
                "followup_attempt_limit_guard",
                "approval_eligibility_guard",
            ],
            "hitl_gates": [
                "approval_required",
                "reject_reason_capture",
                "followup_reason_capture",
                "retry_recoverability_confirmation",
            ],
        },
        evidence_schema={
            "material_refs": [
                "ap_item_id",
                "email_id",
                "audit_event_id",
                "idempotency_key",
                "correlation_id",
            ],
            "optional_refs": [
                "draft_id",
                "erp_reference",
                "slack_ts",
                "teams_message_id",
            ],
        },
        adapter_bindings={
            "email": ["gmail"],
            "approval": ["slack", "teams", "email"],
            "erp": ["netsuite", "sap", "quickbooks", "xero"],
        },
        kpi_contract={
            "metrics": [
                "agentic_telemetry.straight_through_rate.rate",
                "agentic_telemetry.human_intervention_rate.rate",
                "on_time_approvals.rate",
                "post_failure_rate.rate_24h",
                "agentic_telemetry.top_blocker_reasons",
            ],
            "promotion_gates": {
                "legal_transition_correctness_min": 0.99,
                "audit_coverage_min": 0.99,
                "idempotency_integrity_min": 0.99,
                "operator_acceptance_min": 0.8,
                "enabled_connector_readiness_min": 1.0,
            },
        },
    )

    @property
    def skill_id(self) -> str:
        return "ap_v1"

    @property
    def intents(self) -> frozenset[str]:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    @staticmethod
    def _with_autonomy_policy(
        runtime,
        *,
        ap_item: Dict[str, Any],
        payload: Dict[str, Any],
        precheck: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        merged = dict(precheck or {})
        reason_codes = list(merged.get("reason_codes") or [])
        autonomous_requested = runtime.is_autonomous_request(payload)
        autonomy_policy = runtime.ap_autonomy_policy(
            vendor_name=ap_item.get("vendor_name") or ap_item.get("vendor"),
            action=action,
            autonomous_requested=autonomous_requested,
        )
        merged["autonomous_requested"] = autonomous_requested
        merged["autonomy_policy"] = autonomy_policy
        if autonomous_requested and not autonomy_policy.get("autonomous_allowed"):
            reason_codes.extend(
                [
                    "autonomy_gate_blocked",
                    f"autonomy_mode_{autonomy_policy.get('mode')}",
                    *(autonomy_policy.get("reason_codes") or []),
                ]
            )
            merged["eligible"] = False
        merged["reason_codes"] = list(dict.fromkeys([code for code in reason_codes if code]))
        if "eligible" not in merged:
            merged["eligible"] = len(merged["reason_codes"]) == 0
        return merged

    def policy_precheck(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        reference = runtime._item_reference(payload)
        ap_item = runtime._resolve_ap_item(reference)
        ap_item_id = str(ap_item.get("id") or reference)
        email_id = str(ap_item.get("thread_id") or reference)

        if normalized_intent == "prepare_vendor_followups":
            precheck = runtime._evaluate_prepare_vendor_followup(
                ap_item,
                force=runtime._as_bool(payload.get("force")),
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
            }

        workflow = get_invoice_workflow(runtime.organization_id)
        if normalized_intent == "request_approval":
            state = str(ap_item.get("state") or "").strip().lower()
            reason_codes = []
            if state not in {"received", "validated"}:
                reason_codes.append("state_not_ready_for_approval")
            precheck = {
                "eligible": not reason_codes,
                "reason_codes": reason_codes,
                "state": state,
            }
            precheck = self._with_autonomy_policy(
                runtime,
                ap_item=ap_item,
                payload=payload,
                precheck=precheck,
                action=normalized_intent,
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "approve_invoice":
            precheck = workflow.evaluate_financial_action_precheck(
                ap_item,
                allowed_states=["needs_approval", "pending_approval"],
                state_reason_code="state_not_waiting_for_approval",
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "request_info":
            state = str(ap_item.get("state") or "").strip().lower()
            reason_codes = []
            if state not in {"validated", "needs_approval", "pending_approval"}:
                reason_codes.append("state_not_request_info_allowed")
            precheck = {
                "eligible": not reason_codes,
                "reason_codes": reason_codes,
                "state": state,
            }
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "nudge_approval":
            state = str(ap_item.get("state") or "").strip().lower()
            reason_codes = []
            if state not in {"needs_approval", "pending_approval"}:
                reason_codes.append("state_not_waiting_for_approval")
            precheck = {
                "eligible": not reason_codes,
                "reason_codes": reason_codes,
                "state": state,
            }
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "reject_invoice":
            state = str(ap_item.get("state") or "").strip().lower()
            reason_codes = []
            if state not in {"received", "validated", "needs_info", "needs_approval", "pending_approval"}:
                reason_codes.append("state_not_rejectable")
            if not str(payload.get("reason") or "").strip():
                reason_codes.append("rejection_reason_required")
            precheck = {
                "eligible": not reason_codes,
                "reason_codes": reason_codes,
                "state": state,
            }
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "post_to_erp":
            precheck = workflow.evaluate_financial_action_precheck(
                ap_item,
                allowed_states=["approved", "ready_to_post"],
                state_reason_code="state_not_ready_to_post",
            )
            precheck = self._with_autonomy_policy(
                runtime,
                ap_item=ap_item,
                payload=payload,
                precheck=precheck,
                action=normalized_intent,
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "route_low_risk_for_approval":
            precheck = workflow.evaluate_batch_route_low_risk_for_approval(ap_item)
            precheck = self._with_autonomy_policy(
                runtime,
                ap_item=ap_item,
                payload=payload,
                precheck=precheck,
                action=normalized_intent,
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        if normalized_intent == "retry_recoverable_failures":
            precheck = workflow.evaluate_batch_retry_recoverable_failure(ap_item)
            precheck = self._with_autonomy_policy(
                runtime,
                ap_item=ap_item,
                payload=payload,
                precheck=precheck,
                action=normalized_intent,
            )
            return {
                "intent": normalized_intent,
                "ap_item": ap_item,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "workflow": workflow,
            }

        raise ValueError(f"unsupported_intent:{normalized_intent or 'missing'}")

    def audit_contract(self, intent: str) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        if normalized_intent == "request_approval":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "approval_request_routed",
                    "approval_request_blocked",
                    "approval_request_failed",
                ],
            }
        if normalized_intent == "nudge_approval":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": False,
                "events": [
                    "approval_nudge_sent",
                    "approval_nudge_failed",
                    "approval_nudge_blocked",
                ],
            }
        if normalized_intent == "approve_invoice":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "invoice_approved",
                    "invoice_approval_blocked",
                    "invoice_approval_failed",
                ],
            }
        if normalized_intent == "request_info":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "info_request_recorded",
                    "info_request_blocked",
                    "info_request_failed",
                ],
            }
        if normalized_intent == "reject_invoice":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "invoice_rejected",
                    "invoice_reject_blocked",
                    "invoice_reject_failed",
                ],
            }
        if normalized_intent == "post_to_erp":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "erp_post_completed",
                    "erp_post_failed",
                    "erp_post_blocked",
                ],
            }
        if normalized_intent == "prepare_vendor_followups":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": False,
                "events": [
                    "vendor_followup_waiting_sla",
                    "vendor_followup_blocked",
                    "vendor_followup_failed",
                    "vendor_followup_draft_prepared",
                ],
            }
        if normalized_intent == "route_low_risk_for_approval":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "route_low_risk_for_approval",
                    "route_low_risk_for_approval_blocked",
                    "route_low_risk_for_approval_failed",
                ],
            }
        if normalized_intent == "retry_recoverable_failures":
            return {
                "source": "finance_agent_runtime",
                "idempotent": True,
                "mutates_ap_state": True,
                "events": [
                    "retry_recoverable_failure_blocked",
                    "retry_recoverable_failure_completed",
                    "retry_recoverable_failure_failed",
                ],
            }
        return {
            "source": "finance_agent_runtime",
            "idempotent": True,
            "mutates_ap_state": False,
            "events": [],
        }

    def preview(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        context = self.policy_precheck(runtime, normalized_intent, payload)
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        status = "eligible" if precheck.get("eligible") else "blocked"

        if normalized_intent == "request_approval":
            operator_copy = {
                "what_happened": "Validated this invoice for approval routing from Gmail.",
                "why_now": "Clearledgr checks the current invoice state before sending an approval request.",
                "recommended_now": (
                    "Request approval now."
                    if precheck.get("eligible")
                    else "Resolve the blocking state before requesting approval."
                ),
            }
        elif normalized_intent == "approve_invoice":
            operator_copy = {
                "what_happened": "Validated that this invoice can still be approved from the approval surface.",
                "why_now": "Approval decisions are only accepted while the invoice is still waiting on approval.",
                "recommended_now": (
                    "Approve this invoice."
                    if precheck.get("eligible")
                    else "Refresh the invoice and use the allowed next step."
                ),
            }
        elif normalized_intent == "request_info":
            operator_copy = {
                "what_happened": "Validated that this invoice can be sent back for more information.",
                "why_now": "Clearledgr only records info requests while the invoice is still in a reviewable state.",
                "recommended_now": (
                    "Send this invoice back for more information."
                    if precheck.get("eligible")
                    else "Refresh the invoice and use the allowed next step."
                ),
            }
        elif normalized_intent == "nudge_approval":
            operator_copy = {
                "what_happened": "Validated that this invoice is still waiting on an approver.",
                "why_now": "Nudges are only allowed while the approval request is still pending.",
                "recommended_now": (
                    "Send an approval reminder."
                    if precheck.get("eligible")
                    else "Wait until the invoice is back in an approval-pending state."
                ),
            }
        elif normalized_intent == "reject_invoice":
            operator_copy = {
                "what_happened": "Validated that this invoice can still be rejected.",
                "why_now": "Clearledgr requires a rejection reason and a rejectable state before recording the decision.",
                "recommended_now": (
                    "Reject this invoice with a reason."
                    if precheck.get("eligible")
                    else "Provide a reason or return the invoice to a rejectable state."
                ),
            }
        elif normalized_intent == "post_to_erp":
            operator_copy = {
                "what_happened": "Validated that this invoice is ready for ERP posting.",
                "why_now": "Posting is only allowed once approval and posting-readiness checks are complete.",
                "recommended_now": (
                    "Post this invoice to ERP."
                    if precheck.get("eligible")
                    else "Wait until the invoice reaches a postable state."
                ),
            }
        elif normalized_intent == "prepare_vendor_followups":
            operator_copy = {
                "what_happened": "Validated vendor follow-up draft eligibility for a needs-info item.",
                "why_now": "Follow-up attempts and SLA timing were checked before preparing a draft.",
                "recommended_now": (
                    "Prepare the vendor follow-up draft."
                    if precheck.get("eligible")
                    else "Resolve blockers or wait until the SLA window opens."
                ),
            }
        elif normalized_intent == "route_low_risk_for_approval":
            operator_copy = {
                "what_happened": "Validated AP item reviewed for low-risk approval routing.",
                "why_now": "Policy prechecks were evaluated before routing to approval surfaces.",
                "recommended_now": (
                    "Run route-low-risk-for-approval."
                    if precheck.get("eligible")
                    else "Address blockers before routing."
                ),
            }
        else:
            operator_copy = {
                "what_happened": "Validated recoverability and state checks for failed-post retry.",
                "why_now": "Recoverable retry prechecks were evaluated before resume execution.",
                "recommended_now": (
                    "Run recoverable retry."
                    if precheck.get("eligible")
                    else "Resolve the blocking recoverability condition first."
                ),
            }

        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "mode": "preview",
            "status": status,
            "organization_id": runtime.organization_id,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "next_step": "execute_intent",
            "operator_copy": operator_copy,
        }

    async def execute(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        context = self.policy_precheck(runtime, normalized_intent, payload)
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        correlation_id = runtime._correlation_id_for_item(ap_item)

        if normalized_intent == "prepare_vendor_followups":
            metadata = runtime._parse_json_dict(ap_item.get("metadata"))
            attempts = max(0, runtime._safe_int(metadata.get("followup_attempt_count"), 0))

            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                waiting_sla = "waiting_for_sla_window" in reason_codes
                limit_reached = "followup_attempt_limit_reached" in reason_codes
                state_invalid = "state_not_needs_info" in reason_codes

                if waiting_sla:
                    next_allowed = precheck.get("next_allowed_at")
                    runtime._merge_item_metadata(
                        ap_item,
                        {
                            "followup_next_action": "await_vendor_response",
                            "followup_sla_due_at": next_allowed,
                            "followup_attempt_count": attempts,
                        },
                    )
                    response = {
                        "skill_id": self.skill_id,
                        "intent": normalized_intent,
                        "status": "waiting_sla",
                        "reason": "waiting_for_sla_window",
                        "email_id": email_id,
                        "ap_item_id": ap_item_id,
                        "policy_precheck": precheck,
                        "followup_attempt_count": attempts,
                        "followup_next_action": "await_vendor_response",
                        "next_allowed_at": next_allowed,
                        "needs_info_draft_id": metadata.get("needs_info_draft_id"),
                        "audit_contract": self.audit_contract(normalized_intent),
                    }
                    audit_row = runtime._append_runtime_audit(
                        ap_item_id=ap_item_id,
                        event_type="vendor_followup_waiting_sla",
                        reason="vendor_followup_waiting_sla",
                        metadata={
                            "intent": normalized_intent,
                            "policy_precheck": precheck,
                            "response": response,
                        },
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                    )
                    response["audit_event_id"] = (audit_row or {}).get("id")
                    return response

                if limit_reached:
                    runtime._merge_item_metadata(
                        ap_item,
                        {
                            "followup_next_action": "manual_vendor_escalation",
                            "followup_attempt_count": attempts,
                        },
                    )
                    response = {
                        "skill_id": self.skill_id,
                        "intent": normalized_intent,
                        "status": "blocked",
                        "reason": "followup_attempt_limit_reached",
                        "email_id": email_id,
                        "ap_item_id": ap_item_id,
                        "policy_precheck": precheck,
                        "followup_attempt_count": attempts,
                        "max_attempts": precheck.get("max_attempts"),
                        "followup_next_action": "manual_vendor_escalation",
                        "audit_contract": self.audit_contract(normalized_intent),
                    }
                    audit_row = runtime._append_runtime_audit(
                        ap_item_id=ap_item_id,
                        event_type="vendor_followup_blocked",
                        reason="vendor_followup_attempt_limit",
                        metadata={
                            "intent": normalized_intent,
                            "policy_precheck": precheck,
                            "response": response,
                        },
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                    )
                    response["audit_event_id"] = (audit_row or {}).get("id")
                    return response

                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "item_not_in_needs_info_state" if state_invalid else "policy_precheck_failed",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="vendor_followup_blocked",
                    reason="policy_precheck_failed",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            from clearledgr.services.auto_followup import AutoFollowUpService
            from clearledgr.services.gmail_api import GmailAPIClient

            question = (
                str(payload.get("reason") or "").strip()
                or str(metadata.get("needs_info_question") or "").strip()
                or str(ap_item.get("last_error") or "").strip()
                or "additional information is required before we can process this invoice"
            )
            sender_email = str(ap_item.get("sender") or "").strip()
            user_id = str(ap_item.get("user_id") or runtime.actor_email or "me").strip() or "me"
            gmail_client = GmailAPIClient(user_id=user_id)
            authenticated = await gmail_client.ensure_authenticated()

            if not authenticated:
                runtime._merge_item_metadata(
                    ap_item,
                    {
                        "needs_info_question": question,
                        "followup_next_action": "prepare_vendor_followup_draft",
                        "followup_attempt_count": attempts,
                    },
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "draft_unavailable",
                    "reason": "gmail_auth_unavailable",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "followup_attempt_count": attempts,
                    "followup_next_action": "prepare_vendor_followup_draft",
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="vendor_followup_failed",
                    reason="gmail_auth_unavailable",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            followup_svc = AutoFollowUpService(organization_id=runtime.organization_id)
            draft_id = await followup_svc.create_gmail_draft(
                gmail_client=gmail_client,
                ap_item_id=ap_item_id,
                thread_id=email_id,
                to_email=sender_email,
                invoice_data={
                    "subject": ap_item.get("subject") or "",
                    "vendor_name": ap_item.get("vendor_name") or ap_item.get("vendor") or "",
                    "amount": ap_item.get("amount") or 0.0,
                    "invoice_number": ap_item.get("invoice_number") or "",
                },
                question=question,
            )

            if not draft_id:
                runtime._merge_item_metadata(
                    ap_item,
                    {
                        "needs_info_question": question,
                        "followup_next_action": "prepare_vendor_followup_draft",
                        "followup_attempt_count": attempts,
                    },
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "draft_unavailable",
                    "reason": "draft_not_created",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "followup_attempt_count": attempts,
                    "followup_next_action": "prepare_vendor_followup_draft",
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="vendor_followup_failed",
                    reason="vendor_followup_draft_failed",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            sent_at = datetime.now(timezone.utc)
            next_attempt = attempts + 1
            next_due = sent_at + timedelta(hours=runtime._vendor_followup_sla_hours())
            merged = runtime._merge_item_metadata(
                ap_item,
                {
                    "needs_info_question": question,
                    "needs_info_draft_id": draft_id,
                    "followup_last_sent_at": sent_at.isoformat(),
                    "followup_attempt_count": next_attempt,
                    "followup_sla_due_at": next_due.isoformat(),
                    "followup_next_action": "await_vendor_response",
                },
            )
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": "prepared",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "draft_id": draft_id,
                "needs_info_draft_id": draft_id,
                "followup_attempt_count": next_attempt,
                "followup_last_sent_at": merged.get("followup_last_sent_at"),
                "followup_sla_due_at": merged.get("followup_sla_due_at"),
                "followup_next_action": merged.get("followup_next_action"),
                "audit_contract": self.audit_contract(normalized_intent),
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="vendor_followup_draft_prepared",
                reason="vendor_followup_nudge_prepared",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "request_approval":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "state_not_ready_for_approval",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="approval_request_blocked",
                    reason="state_not_ready_for_approval",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            invoice = workflow.build_invoice_data_from_ap_item(ap_item, actor_id=runtime.actor_email)
            if not invoice.gmail_id:
                raise ValueError("missing_gmail_reference")
            workflow_result = await workflow._send_for_approval(
                invoice,
                extra_context={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                },
            )
            routed = str((workflow_result or {}).get("status") or "").strip().lower() == "pending_approval"
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": "pending_approval" if routed else "error",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "result": workflow_result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "wait_for_approval" if routed else "review_blockers",
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_request_routed" if routed else "approval_request_failed",
                reason="runtime_request_approval",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": workflow_result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "approve_invoice":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                blocked_reason = (
                    "field_review_required"
                    if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                    else "state_not_waiting_for_approval"
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="invoice_approval_blocked",
                    reason=blocked_reason,
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            approve_override = (
                str(payload.get("action_variant") or "").strip().lower() == "budget_override"
                or runtime._as_bool(payload.get("approve_override"))
            )
            justification = str(
                payload.get("reason")
                or payload.get("override_justification")
                or ""
            ).strip() or None
            result = await workflow.approve_invoice(
                gmail_id=email_id,
                approved_by=str(payload.get("actor_id") or runtime.actor_email or runtime.actor_id or "approval_surface"),
                source_channel=str(payload.get("source_channel") or "approval_surface").strip() or "approval_surface",
                source_channel_id=str(payload.get("source_channel_id") or "").strip() or None,
                source_message_ref=str(payload.get("source_message_ref") or email_id).strip() or email_id,
                actor_display=str(payload.get("actor_display") or "").strip() or None,
                action_run_id=str(payload.get("action_run_id") or "").strip() or None,
                decision_request_ts=str(payload.get("decision_request_ts") or "").strip() or None,
                decision_idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                allow_budget_override=approve_override,
                override_justification=justification,
            )
            result_status = str((result or {}).get("status") or "").strip().lower()
            blocked = result_status in {"blocked", "needs_field_review"}
            approved = result_status in {"approved", "posted", "posted_to_erp"}
            response_status = "blocked" if blocked else (result_status or ("approved" if approved else "error"))
            blocked_reason = (
                str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
            )
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": response_status,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "result": result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "none" if approved else "review_blockers",
            }
            if blocked:
                response["reason"] = blocked_reason
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type=(
                    "invoice_approved"
                    if approved
                    else ("invoice_approval_blocked" if blocked else "invoice_approval_failed")
                ),
                reason=blocked_reason if blocked else "runtime_approve_invoice",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "request_info":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "state_not_request_info_allowed",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="info_request_blocked",
                    reason="state_not_request_info_allowed",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            result = await workflow.request_budget_adjustment(
                gmail_id=email_id,
                requested_by=str(payload.get("actor_id") or runtime.actor_email or runtime.actor_id or "approval_surface"),
                reason=str(payload.get("reason") or "request_info").strip() or "request_info",
                source_channel=str(payload.get("source_channel") or "approval_surface").strip() or "approval_surface",
                source_channel_id=str(payload.get("source_channel_id") or "").strip() or None,
                source_message_ref=str(payload.get("source_message_ref") or email_id).strip() or email_id,
                actor_display=str(payload.get("actor_display") or "").strip() or None,
                action_run_id=str(payload.get("action_run_id") or "").strip() or None,
                decision_request_ts=str(payload.get("decision_request_ts") or "").strip() or None,
                decision_idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            result_status = str((result or {}).get("status") or "").strip().lower()
            moved_to_needs_info = result_status == "needs_info"
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": result_status or ("needs_info" if moved_to_needs_info else "error"),
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "result": result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "wait_for_vendor_response" if moved_to_needs_info else "review_blockers",
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="info_request_recorded" if moved_to_needs_info else "info_request_failed",
                reason="runtime_request_info",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "nudge_approval":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "state_not_waiting_for_approval",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="approval_nudge_blocked",
                    reason="state_not_waiting_for_approval",
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            message = str(payload.get("message") or "").strip()
            try:
                amount_num = float(ap_item.get("amount") or 0.0)
            except (TypeError, ValueError):
                amount_num = 0.0
            nudge_text = message or (
                f"Reminder: approval is still pending for "
                f"{ap_item.get('vendor_name') or ap_item.get('vendor') or 'invoice'} "
                f"({ap_item.get('currency') or 'USD'} {amount_num:,.2f}). "
                "Please review when available."
            )

            slack_result: Dict[str, Any] = {"status": "skipped", "reason": "no_slack_thread"}
            teams_result: Dict[str, Any] = {"status": "skipped", "reason": "teams_unavailable"}

            slack_thread = runtime.db.get_slack_thread(email_id) if hasattr(runtime.db, "get_slack_thread") else None
            if slack_thread and getattr(workflow, "slack_client", None):
                try:
                    sent = await workflow.slack_client.send_message(
                        channel=str(slack_thread.get("channel_id") or ""),
                        thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                        text=nudge_text,
                    )
                    slack_result = {
                        "status": "sent",
                        "channel_id": sent.channel,
                        "thread_ts": sent.thread_ts or sent.ts,
                        "message_ts": sent.ts,
                    }
                except Exception as exc:
                    slack_result = {"status": "error", "reason": str(exc)}

            teams_meta = runtime._parse_json_dict(ap_item.get("metadata")).get("teams")
            if isinstance(teams_meta, dict) and getattr(workflow, "teams_client", None):
                try:
                    budget_payload = {
                        "status": ap_item.get("budget_status") or "unknown",
                        "requires_decision": bool(ap_item.get("budget_requires_decision")),
                    }
                    result = workflow.teams_client.send_invoice_budget_card(
                        email_id=email_id,
                        organization_id=runtime.organization_id,
                        vendor=str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown"),
                        amount=amount_num,
                        currency=str(ap_item.get("currency") or "USD"),
                        invoice_number=ap_item.get("invoice_number"),
                        budget=budget_payload,
                    )
                    teams_result = result if isinstance(result, dict) else {"status": "sent"}
                except Exception as exc:
                    teams_result = {"status": "error", "reason": str(exc)}

            sent_any = slack_result.get("status") == "sent" or teams_result.get("status") == "sent"
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": "nudged" if sent_any else "error",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "slack": slack_result,
                "teams": teams_result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "wait_for_approval",
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_nudge_sent" if sent_any else "approval_nudge_failed",
                reason="approval_nudge",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "message": nudge_text[:400],
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "reject_invoice":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                blocked_reason = (
                    "rejection_reason_required"
                    if "rejection_reason_required" in reason_codes
                    else "state_not_rejectable"
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="invoice_reject_blocked",
                    reason=blocked_reason,
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            result = await workflow.reject_invoice(
                gmail_id=email_id,
                reason=str(payload.get("reason") or "").strip(),
                rejected_by=runtime.actor_email or "gmail_extension",
                source_channel="gmail_extension",
                source_channel_id="gmail_extension",
                source_message_ref=email_id,
                decision_idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            rejected = str((result or {}).get("status") or "").strip().lower() == "rejected"
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": "rejected" if rejected else "error",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "result": result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "none" if rejected else "review_blockers",
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_rejected" if rejected else "invoice_reject_failed",
                reason="runtime_reject_invoice",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "post_to_erp":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                blocked_reason = (
                    "autonomy_gate_blocked"
                    if "autonomy_gate_blocked" in reason_codes
                    else (
                    "field_review_required"
                    if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                    else "state_not_ready_to_post"
                    )
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "detail": (
                        ((precheck.get("autonomy_policy") or {}).get("detail"))
                        if blocked_reason == "autonomy_gate_blocked"
                        else None
                    ),
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="erp_post_blocked",
                    reason=blocked_reason,
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            override = runtime._as_bool(payload.get("override"))
            justification = str(payload.get("override_justification") or payload.get("reason") or "").strip() or None
            field_confidences = payload.get("field_confidences")
            if not isinstance(field_confidences, dict):
                metadata = runtime._parse_json_dict(ap_item.get("metadata"))
                field_confidences = metadata.get("field_confidences") if isinstance(metadata.get("field_confidences"), dict) else None

            override_ctx = None
            if override:
                from clearledgr.core.ap_states import OverrideContext, OVERRIDE_TYPE_MULTI

                override_ctx = OverrideContext(
                    override_type=OVERRIDE_TYPE_MULTI,
                    justification=justification or "override_requested_in_gmail",
                    actor_id=runtime.actor_email or "gmail_extension",
                )

            result = await workflow.approve_invoice(
                gmail_id=email_id,
                approved_by=runtime.actor_email or "gmail_extension",
                source_channel="gmail_extension",
                source_channel_id="gmail_extension",
                source_message_ref=email_id,
                allow_budget_override=override,
                allow_confidence_override=override,
                override_justification=justification,
                allow_po_exception_override=override,
                po_override_reason=justification,
                field_confidences=field_confidences,
                override_context=override_ctx,
                decision_idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            result_status = str((result or {}).get("status") or "").strip().lower()
            blocked = result_status in {"blocked", "needs_field_review"}
            posted = result_status in {"posted", "approved", "posted_to_erp"}
            response_status = "blocked" if blocked else (result_status or ("posted_to_erp" if posted else "error"))
            blocked_reason = (
                str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
            )
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": response_status,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "erp_reference": (result or {}).get("erp_reference") if isinstance(result, dict) else None,
                "policy_precheck": precheck,
                "result": result,
                "audit_contract": self.audit_contract(normalized_intent),
                "next_step": "none" if posted else "review_blockers",
            }
            if blocked:
                response["reason"] = blocked_reason
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type=(
                    "erp_post_completed"
                    if posted
                    else ("erp_post_blocked" if blocked else "erp_post_failed")
                ),
                reason=blocked_reason if blocked else "runtime_post_to_erp",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "route_low_risk_for_approval":
            workflow = context["workflow"]
            reason = str(payload.get("reason") or "agent_runtime_route_low_risk_for_approval")

            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                blocked_reason = (
                    "autonomy_gate_blocked"
                    if "autonomy_gate_blocked" in reason_codes
                    else (
                    "field_review_required"
                    if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                    else "policy_precheck_failed"
                    )
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "detail": (
                        ((precheck.get("autonomy_policy") or {}).get("detail"))
                        if blocked_reason == "autonomy_gate_blocked"
                        else None
                    ),
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="route_low_risk_for_approval_blocked",
                    reason=blocked_reason,
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            invoice = workflow.build_invoice_data_from_ap_item(ap_item, actor_id=runtime.actor_email)
            if not invoice.gmail_id:
                raise ValueError("missing_gmail_reference")
            workflow_result = await workflow._send_for_approval(
                invoice,
                extra_context={
                    "intent": normalized_intent,
                    "batch_reason": reason,
                    "policy_precheck": precheck,
                },
            )
            routed = str((workflow_result or {}).get("status") or "").strip().lower() == "pending_approval"
            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": "pending_approval" if routed else "error",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "result": workflow_result,
                "audit_contract": self.audit_contract(normalized_intent),
            }
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="route_low_risk_for_approval" if routed else "route_low_risk_for_approval_failed",
                reason="agent_runtime_route_low_risk_for_approval",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": workflow_result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if normalized_intent == "retry_recoverable_failures":
            workflow = context["workflow"]
            if not precheck.get("eligible"):
                reason_codes = set(precheck.get("reason_codes") or [])
                blocked_reason = (
                    "autonomy_gate_blocked"
                    if "autonomy_gate_blocked" in reason_codes
                    else (
                    "field_review_required"
                    if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                    else "retry_not_recoverable"
                    )
                )
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "detail": (
                        ((precheck.get("autonomy_policy") or {}).get("detail"))
                        if blocked_reason == "autonomy_gate_blocked"
                        else None
                    ),
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="retry_recoverable_failure_blocked",
                    reason=blocked_reason,
                    metadata={
                        "intent": normalized_intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            try:
                result = await workflow.resume_workflow(ap_item_id)
            except Exception as exc:
                result = {"status": "error", "reason": str(exc)}

            resume_status = str((result or {}).get("status") or "").strip().lower()
            blocked = resume_status == "blocked"
            blocked_reason = (
                str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
            )
            if resume_status == "recovered":
                response_status = "posted"
            elif blocked:
                response_status = "blocked"
            elif resume_status == "not_resumable":
                response_status = "ready_to_post"
            elif resume_status == "error":
                response_status = "error"
            else:
                response_status = resume_status or "error"

            response = {
                "skill_id": self.skill_id,
                "intent": normalized_intent,
                "status": response_status,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "erp_reference": result.get("erp_reference") if isinstance(result, dict) else None,
                "policy_precheck": precheck,
                "result": result,
                "audit_contract": self.audit_contract(normalized_intent),
            }
            if blocked:
                response["reason"] = blocked_reason
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type=(
                    "retry_recoverable_failure_completed"
                    if response_status in {"posted", "ready_to_post"}
                    else ("retry_recoverable_failure_blocked" if blocked else "retry_recoverable_failure_failed")
                ),
                reason=blocked_reason if blocked else "batch_retry_recoverable_failures",
                metadata={
                    "intent": normalized_intent,
                    "policy_precheck": precheck,
                    "result": result,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        raise ValueError(f"unsupported_intent:{normalized_intent or 'missing'}")
