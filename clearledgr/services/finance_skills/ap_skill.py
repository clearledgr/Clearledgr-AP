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
                "recoverability_guard",
                "followup_sla_guard",
                "followup_attempt_limit_guard",
                "approval_eligibility_guard",
            ],
            "hitl_gates": [
                "approval_required",
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
            "email": ["gmail", "outlook"],
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
        if normalized_intent == "route_low_risk_for_approval":
            precheck = workflow.evaluate_batch_route_low_risk_for_approval(ap_item)
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

        if normalized_intent == "prepare_vendor_followups":
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

        if normalized_intent == "route_low_risk_for_approval":
            workflow = context["workflow"]
            reason = str(payload.get("reason") or "agent_runtime_route_low_risk_for_approval")

            if not precheck.get("eligible"):
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "policy_precheck_failed",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="route_low_risk_for_approval_blocked",
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
                response = {
                    "skill_id": self.skill_id,
                    "intent": normalized_intent,
                    "status": "blocked",
                    "reason": "retry_not_recoverable",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "audit_contract": self.audit_contract(normalized_intent),
                }
                audit_row = runtime._append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="retry_recoverable_failure_blocked",
                    reason="retry_not_recoverable",
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
            if resume_status == "recovered":
                response_status = "posted"
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
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type=(
                    "retry_recoverable_failure_completed"
                    if response_status in {"posted", "ready_to_post"}
                    else "retry_recoverable_failure_failed"
                ),
                reason="batch_retry_recoverable_failures",
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
