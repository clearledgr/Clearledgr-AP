"""Per-intent AP handler registry used by the runtime skill."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, Optional

from clearledgr.core.ap_entity_routing import resolve_entity_routing
from clearledgr.core.utils import safe_int

logger = logging.getLogger(__name__)


def _base_context(intent: str, runtime, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    action_context = runtime.create_ap_action_context(normalized_payload)
    reference, ap_item = action_context.reference, action_context.ap_item
    ap_item_id = str(ap_item.get("id") or reference)
    email_id = str(
        ap_item.get("thread_id")
        or ap_item.get("message_id")
        or normalized_payload.get("email_id")
        or reference
    )
    return {
        "intent": intent,
        "payload": normalized_payload,
        "ap_item": ap_item,
        "ap_item_id": ap_item_id,
        "email_id": email_id,
    }


def _append_runtime_audit_best_effort(
    runtime,
    response: Dict[str, Any],
    *,
    suppress_errors: bool = False,
    **kwargs,
):
    try:
        audit_row = runtime.append_runtime_audit(**kwargs)
    except Exception as exc:
        if not suppress_errors:
            raise
        logger.warning(
            "runtime audit append failed for %s on %s: %s",
            kwargs.get("event_type"),
            kwargs.get("ap_item_id"),
            exc,
        )
        response["audit_status"] = "error"
        response["audit_error"] = str(exc)
        return None
    response["audit_event_id"] = (audit_row or {}).get("id")
    response["audit_status"] = "recorded" if audit_row else "missing"
    return audit_row


def _resolve_actor_fields(runtime, payload: Optional[Dict[str, Any]], *, fallback: str = "approval_surface") -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    actor_email = str(data.get("actor_email") or runtime.actor_email or "").strip()
    actor_platform_id = str(data.get("actor_id") or runtime.actor_id or "").strip()
    actor_display = str(data.get("actor_display") or "").strip()
    source_channel = str(data.get("source_channel") or "").strip().lower() or None
    raw_identity = data.get("actor_identity") if isinstance(data.get("actor_identity"), dict) else {}
    actor_identity = {
        "platform": str(raw_identity.get("platform") or source_channel or "").strip() or None,
        "platform_user_id": str(raw_identity.get("platform_user_id") or actor_platform_id or "").strip() or None,
        "email": str(raw_identity.get("email") or actor_email or "").strip() or None,
        "display_name": str(raw_identity.get("display_name") or actor_display or "").strip() or None,
    }
    canonical_actor = actor_identity["email"] or actor_identity["platform_user_id"] or fallback
    return {
        "actor_email": actor_identity["email"],
        "actor_display": actor_identity["display_name"],
        "actor_platform_id": actor_identity["platform_user_id"],
        "actor_identity": actor_identity,
        "canonical_actor": canonical_actor,
    }


class APIntentHandler:
    intent = ""

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError

    async def execute(
        self,
        skill,
        runtime,
        context: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


class PrepareVendorFollowupsHandler(APIntentHandler):
    intent = "prepare_vendor_followups"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        precheck = runtime.evaluate_prepare_vendor_followup(
            ap_item,
            force=runtime.coerce_bool(context["payload"].get("force")),
        )
        return {
            **context,
            "policy_precheck": precheck,
        }

    async def execute(
        self,
        skill,
        runtime,
        context: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        correlation_id = runtime.correlation_id_for_item(ap_item)
        metadata = runtime.parse_json_dict(ap_item.get("metadata"))
        attempts = max(0, safe_int(metadata.get("followup_attempt_count"), 0))

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            waiting_sla = "waiting_for_sla_window" in reason_codes
            limit_reached = "followup_attempt_limit_reached" in reason_codes
            state_invalid = "state_not_needs_info" in reason_codes

            if waiting_sla:
                next_allowed = precheck.get("next_allowed_at")
                runtime.merge_item_metadata(
                    ap_item,
                    {
                        "followup_next_action": "await_vendor_response",
                        "followup_sla_due_at": next_allowed,
                        "followup_attempt_count": attempts,
                    },
                )
                response = {
                    "skill_id": skill.skill_id,
                    "intent": self.intent,
                    "status": "waiting_sla",
                    "reason": "waiting_for_sla_window",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "followup_attempt_count": attempts,
                    "followup_next_action": "await_vendor_response",
                    "next_allowed_at": next_allowed,
                    "needs_info_draft_id": metadata.get("needs_info_draft_id"),
                    "audit_contract": skill.audit_contract(self.intent),
                }
                audit_row = runtime.append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="vendor_followup_waiting_sla",
                    reason="vendor_followup_waiting_sla",
                    metadata={
                        "intent": self.intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            if limit_reached:
                runtime.merge_item_metadata(
                    ap_item,
                    {
                        "followup_next_action": "manual_vendor_escalation",
                        "followup_attempt_count": attempts,
                    },
                )
                response = {
                    "skill_id": skill.skill_id,
                    "intent": self.intent,
                    "status": "blocked",
                    "reason": "followup_attempt_limit_reached",
                    "email_id": email_id,
                    "ap_item_id": ap_item_id,
                    "policy_precheck": precheck,
                    "followup_attempt_count": attempts,
                    "max_attempts": precheck.get("max_attempts"),
                    "followup_next_action": "manual_vendor_escalation",
                    "audit_contract": skill.audit_contract(self.intent),
                }
                audit_row = runtime.append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="vendor_followup_blocked",
                    reason="vendor_followup_attempt_limit",
                    metadata={
                        "intent": self.intent,
                        "policy_precheck": precheck,
                        "response": response,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": "item_not_in_needs_info_state" if state_invalid else "policy_precheck_failed",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="vendor_followup_blocked",
                reason="policy_precheck_failed",
                metadata={
                    "intent": self.intent,
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
            runtime.merge_item_metadata(
                ap_item,
                {
                    "needs_info_question": question,
                    "followup_next_action": "prepare_vendor_followup_draft",
                    "followup_attempt_count": attempts,
                },
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "draft_unavailable",
                "reason": "gmail_auth_unavailable",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "followup_attempt_count": attempts,
                "followup_next_action": "prepare_vendor_followup_draft",
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="vendor_followup_failed",
                reason="gmail_auth_unavailable",
                metadata={
                    "intent": self.intent,
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
            runtime.merge_item_metadata(
                ap_item,
                {
                    "needs_info_question": question,
                    "followup_next_action": "prepare_vendor_followup_draft",
                    "followup_attempt_count": attempts,
                },
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "draft_unavailable",
                "reason": "draft_not_created",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "followup_attempt_count": attempts,
                "followup_next_action": "prepare_vendor_followup_draft",
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="vendor_followup_failed",
                reason="vendor_followup_draft_failed",
                metadata={
                    "intent": self.intent,
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
        next_due = sent_at + timedelta(hours=runtime.vendor_followup_sla_hours())
        merged = runtime.merge_item_metadata(
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
            "skill_id": skill.skill_id,
            "intent": self.intent,
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
            "audit_contract": skill.audit_contract(self.intent),
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="vendor_followup_draft_prepared",
            reason="vendor_followup_nudge_prepared",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class RequestApprovalHandler(APIntentHandler):
    intent = "request_approval"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        workflow = skill.get_workflow(runtime)
        state = str(ap_item.get("state") or "").strip().lower()
        org_settings = skill.load_org_settings(runtime)
        entity_routing = resolve_entity_routing(
            runtime.parse_json_dict(ap_item.get("metadata")),
            ap_item,
            organization_settings=org_settings,
        )
        reason_codes = []
        if state not in {"received", "validated"}:
            reason_codes.append("state_not_ready_for_approval")
        if entity_routing.get("status") == "needs_review":
            reason_codes.append("entity_route_review_required")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
            "entity_routing": entity_routing,
        }
        precheck = skill.with_autonomy_policy(
            runtime,
            ap_item=ap_item,
            payload=context["payload"],
            precheck=precheck,
            action=self.intent,
        )
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "entity_route_review_required"
                if "entity_route_review_required" in reason_codes
                else "state_not_ready_for_approval"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_request_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        invoice = workflow.build_invoice_data_from_ap_item(ap_item, actor_id=runtime.actor_email)
        resolved_gmail_id = str(
            getattr(invoice, "gmail_id", "")
            or email_id
            or ap_item.get("thread_id")
            or ap_item.get("message_id")
            or ap_item_id
            or ""
        ).strip()
        if not resolved_gmail_id:
            raise ValueError("missing_gmail_reference")
        if resolved_gmail_id != str(getattr(invoice, "gmail_id", "") or "").strip():
            try:
                invoice = replace(invoice, gmail_id=resolved_gmail_id)
            except TypeError:
                setattr(invoice, "gmail_id", resolved_gmail_id)
        workflow_result = await workflow._send_for_approval(
            invoice,
            extra_context={
                "intent": self.intent,
                "policy_precheck": precheck,
            },
        )
        routed = str((workflow_result or {}).get("status") or "").strip().lower() == "pending_approval"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "pending_approval" if routed else "error",
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": workflow_result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_approval" if routed else "review_blockers",
        }
        _append_runtime_audit_best_effort(
            runtime,
            response,
            ap_item_id=ap_item_id,
            event_type="approval_request_routed" if routed else "approval_request_failed",
            reason="runtime_request_approval",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": workflow_result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            suppress_errors=routed,
        )
        return response


class ApproveInvoiceHandler(APIntentHandler):
    intent = "approve_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        precheck = workflow.evaluate_financial_action_precheck(
            context["ap_item"],
            allowed_states=["needs_approval", "pending_approval"],
            state_reason_code="state_not_waiting_for_approval",
        )
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "field_review_required"
                if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                else "state_not_waiting_for_approval"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_approval_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
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
            or runtime.coerce_bool(payload.get("approve_override"))
        )
        actor = _resolve_actor_fields(runtime, payload)
        justification = str(
            payload.get("reason")
            or payload.get("override_justification")
            or ""
        ).strip() or None
        result = await workflow.approve_invoice(
            gmail_id=email_id,
            approved_by=actor["canonical_actor"],
            source_channel=str(payload.get("source_channel") or "approval_surface").strip() or "approval_surface",
            source_channel_id=str(payload.get("source_channel_id") or "").strip() or None,
            source_message_ref=str(payload.get("source_message_ref") or email_id).strip() or email_id,
            actor_display=actor["actor_display"],
            actor_email=actor["actor_email"],
            actor_platform_id=actor["actor_platform_id"],
            actor_identity=actor["actor_identity"],
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
        blocked_reason = str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": response_status,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "none" if approved else "review_blockers",
        }
        if blocked:
            response["reason"] = blocked_reason
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type=(
                "invoice_approved"
                if approved
                else ("invoice_approval_blocked" if blocked else "invoice_approval_failed")
            ),
            reason=blocked_reason if blocked else "runtime_approve_invoice",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class RequestInfoHandler(APIntentHandler):
    intent = "request_info"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        state = str(context["ap_item"].get("state") or "").strip().lower()
        reason_codes = []
        if state not in {"validated", "needs_approval", "pending_approval"}:
            reason_codes.append("state_not_request_info_allowed")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
        }
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": "state_not_request_info_allowed",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="info_request_blocked",
                reason="state_not_request_info_allowed",
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload)
        result = await workflow.request_budget_adjustment(
            gmail_id=email_id,
            requested_by=actor["canonical_actor"],
            reason=str(payload.get("reason") or "request_info").strip() or "request_info",
            source_channel=str(payload.get("source_channel") or "approval_surface").strip() or "approval_surface",
            source_channel_id=str(payload.get("source_channel_id") or "").strip() or None,
            source_message_ref=str(payload.get("source_message_ref") or email_id).strip() or email_id,
            actor_display=actor["actor_display"],
            actor_email=actor["actor_email"],
            actor_platform_id=actor["actor_platform_id"],
            actor_identity=actor["actor_identity"],
            action_run_id=str(payload.get("action_run_id") or "").strip() or None,
            decision_request_ts=str(payload.get("decision_request_ts") or "").strip() or None,
            decision_idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        result_status = str((result or {}).get("status") or "").strip().lower()
        moved_to_needs_info = result_status == "needs_info"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": result_status or ("needs_info" if moved_to_needs_info else "error"),
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_vendor_response" if moved_to_needs_info else "review_blockers",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="info_request_recorded" if moved_to_needs_info else "info_request_failed",
            reason="runtime_request_info",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class NudgeApprovalHandler(APIntentHandler):
    intent = "nudge_approval"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        state = str(context["ap_item"].get("state") or "").strip().lower()
        reason_codes = []
        if state not in {"needs_approval", "pending_approval"}:
            reason_codes.append("state_not_waiting_for_approval")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
        }
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": "state_not_waiting_for_approval",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_nudge_blocked",
                reason="state_not_waiting_for_approval",
                metadata={
                    "intent": self.intent,
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
        fallback_result: Dict[str, Any] = {"status": "skipped", "reason": "not_needed"}

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

        teams_meta = runtime.parse_json_dict(ap_item.get("metadata")).get("teams")
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
        if not sent_any:
            metadata = runtime.parse_json_dict(ap_item.get("metadata"))
            slack_runtime = skill.resolve_slack_runtime(runtime)
            fallback_channel = (
                str(ap_item.get("slack_channel_id") or "").strip()
                or str(metadata.get("approval_channel") or "").strip()
                or str(slack_runtime.get("approval_channel") or "").strip()
            )
            raw_approvers = metadata.get("approval_sent_to")
            if isinstance(raw_approvers, list):
                approver_ids = [str(value).strip() for value in raw_approvers if str(value).strip()]
            else:
                token = str(raw_approvers or "").strip()
                approver_ids = [token] if token else []
            requested_at_raw = (
                metadata.get("approval_requested_at")
                or ap_item.get("updated_at")
                or ap_item.get("created_at")
            )
            hours_pending = 4.0
            if requested_at_raw:
                try:
                    requested_at = datetime.fromisoformat(str(requested_at_raw).replace("Z", "+00:00"))
                    hours_pending = max(
                        1.0,
                        round((datetime.now(timezone.utc) - requested_at).total_seconds() / 3600, 1),
                    )
                except Exception:
                    pass
            fallback_sent = await skill.send_approval_reminder(
                ap_item={**dict(ap_item), "metadata": metadata},
                approver_ids=approver_ids,
                hours_pending=hours_pending,
                organization_id=runtime.organization_id,
                stage="reminder",
                escalation_channel=fallback_channel or None,
            )
            fallback_result = {
                "status": "sent" if fallback_sent else "error",
                "delivery": "approval_reminder_fallback",
                "reason": (
                    None
                    if fallback_sent
                    else "slack_not_connected"
                    if not slack_runtime.get("connected")
                    else "slack_delivery_failed"
                ),
                "channel": fallback_channel or None,
                "slack_connected": bool(slack_runtime.get("connected")),
                "slack_source": str(slack_runtime.get("source") or "").strip() or None,
            }
            sent_any = sent_any or fallback_sent
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "nudged" if sent_any else "error",
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "slack": slack_result,
            "teams": teams_result,
            "fallback": fallback_result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_approval",
        }
        _append_runtime_audit_best_effort(
            runtime,
            response,
            ap_item_id=ap_item_id,
            event_type="approval_nudge_sent" if sent_any else "approval_nudge_failed",
            reason="approval_nudge",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "message": nudge_text[:400],
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            suppress_errors=sent_any,
        )
        return response


class EscalateApprovalHandler(APIntentHandler):
    intent = "escalate_approval"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        state = str(context["ap_item"].get("state") or "").strip().lower()
        reason_codes = []
        if state not in {"needs_approval", "pending_approval"}:
            reason_codes.append("state_not_waiting_for_approval")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
        }
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": "state_not_waiting_for_approval",
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_escalation_blocked",
                reason="state_not_waiting_for_approval",
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        message = str(payload.get("message") or "").strip() or None
        result = await runtime.escalate_invoice_review(
            email_id=email_id,
            vendor=ap_item.get("vendor_name") or ap_item.get("vendor"),
            amount=ap_item.get("amount"),
            currency=str(ap_item.get("currency") or "USD"),
            confidence=ap_item.get("confidence"),
            mismatches=[],
            message=message,
            channel=str(payload.get("channel") or "").strip() or None,
        )
        escalated = str((result or {}).get("status") or "").strip().lower() == "escalated"
        delivery_status = str(((result or {}).get("delivery") or {}).get("status") or "").strip().lower()
        deduped = delivery_status == "deduped"
        metadata = runtime.parse_json_dict(ap_item.get("metadata"))
        if escalated and not deduped:
            runtime.merge_item_metadata(
                ap_item,
                {
                    "approval_escalation_count": max(0, safe_int(metadata.get("approval_escalation_count"), 0)) + 1,
                    "approval_last_escalated_at": datetime.now(timezone.utc).isoformat(),
                    "approval_last_escalation_message": message,
                    "approval_next_action": "wait_for_escalated_review",
                },
            )
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "deduped" if deduped else ("escalated" if escalated else "error"),
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_approval",
        }
        _append_runtime_audit_best_effort(
            runtime,
            response,
            ap_item_id=ap_item_id,
            event_type=(
                "approval_escalation_deduped"
                if deduped
                else "approval_escalation_sent"
                if escalated
                else "approval_escalation_failed"
            ),
            reason="runtime_escalate_approval_deduped" if deduped else "runtime_escalate_approval",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "message": message,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            suppress_errors=escalated or deduped,
        )
        return response


class ReassignApprovalHandler(APIntentHandler):
    intent = "reassign_approval"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        state = str(context["ap_item"].get("state") or "").strip().lower()
        assignee = str(
            context["payload"].get("assignee")
            or context["payload"].get("new_approver")
            or context["payload"].get("approver")
            or ""
        ).strip()
        reason_codes = []
        if state not in {"needs_approval", "pending_approval"}:
            reason_codes.append("state_not_waiting_for_approval")
        if not assignee:
            reason_codes.append("assignee_required")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
            "assignee": assignee,
        }
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        assignee = str(precheck.get("assignee") or "").strip()
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = "assignee_required" if "assignee_required" in reason_codes else "state_not_waiting_for_approval"
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="approval_reassignment_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        note = str(payload.get("note") or payload.get("reason") or "").strip()
        metadata = runtime.parse_json_dict(ap_item.get("metadata"))
        reassigned_at = datetime.now(timezone.utc).isoformat()
        slack_result: Dict[str, Any] = {"status": "skipped", "reason": "no_slack_thread"}

        chain_id = str(metadata.get("approval_chain_id") or "").strip()
        if chain_id and hasattr(runtime.db, "db_reassign_pending_step_approvers"):
            try:
                runtime.db.db_reassign_pending_step_approvers(chain_id, [assignee], comments=note)
            except Exception as exc:
                logger.error("Approval chain reassignment failed for chain %s: %s", chain_id, exc)

        slack_thread = runtime.db.get_slack_thread(email_id) if hasattr(runtime.db, "get_slack_thread") else None
        if slack_thread and getattr(workflow, "slack_client", None):
            try:
                sent = await workflow.slack_client.send_message(
                    channel=str(slack_thread.get("channel_id") or ""),
                    thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                    text=(
                        f"Approval reassigned to {assignee}."
                        if not note
                        else f"Approval reassigned to {assignee}. Note: {note}"
                    ),
                )
                slack_result = {
                    "status": "sent",
                    "channel_id": sent.channel,
                    "thread_ts": sent.thread_ts or sent.ts,
                    "message_ts": sent.ts,
                }
            except Exception as exc:
                slack_result = {"status": "error", "reason": str(exc)}

        runtime.merge_item_metadata(
            ap_item,
            {
                "approval_sent_to": [assignee],
                "approval_reassignment_count": max(0, safe_int(metadata.get("approval_reassignment_count"), 0)) + 1,
                "approval_last_reassigned_at": reassigned_at,
                "approval_last_reassigned_to": assignee,
                "approval_last_reassignment_note": note or None,
                "approval_next_action": "wait_for_new_approver",
            },
        )
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "reassigned",
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "assignee": assignee,
            "policy_precheck": precheck,
            "slack": slack_result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_approval",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="approval_reassigned",
            reason="runtime_reassign_approval",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "assignee": assignee,
                "note": note or None,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class RejectInvoiceHandler(APIntentHandler):
    intent = "reject_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        workflow = skill.get_workflow(runtime)
        state = str(context["ap_item"].get("state") or "").strip().lower()
        reason_codes = []
        if state not in {"received", "validated", "needs_info", "needs_approval", "pending_approval"}:
            reason_codes.append("state_not_rejectable")
        if not str(context["payload"].get("reason") or "").strip():
            reason_codes.append("rejection_reason_required")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
        }
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = "rejection_reason_required" if "rejection_reason_required" in reason_codes else "state_not_rejectable"
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_reject_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="gmail_extension")
        result = await workflow.reject_invoice(
            gmail_id=email_id,
            reason=str(payload.get("reason") or "").strip(),
            rejected_by=actor["canonical_actor"],
            source_channel=str(payload.get("source_channel") or "gmail_extension").strip() or "gmail_extension",
            source_channel_id=str(payload.get("source_channel_id") or "gmail_extension").strip() or "gmail_extension",
            source_message_ref=str(payload.get("source_message_ref") or email_id).strip() or email_id,
            actor_display=actor["actor_display"],
            actor_email=actor["actor_email"],
            actor_platform_id=actor["actor_platform_id"],
            actor_identity=actor["actor_identity"],
            decision_idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        rejected = str((result or {}).get("status") or "").strip().lower() == "rejected"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "rejected" if rejected else "error",
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "none" if rejected else "review_blockers",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_rejected" if rejected else "invoice_reject_failed",
            reason="runtime_reject_invoice",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class PostToERPHandler(APIntentHandler):
    intent = "post_to_erp"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        workflow = skill.get_workflow(runtime)
        precheck = workflow.evaluate_financial_action_precheck(
            ap_item,
            allowed_states=["approved", "ready_to_post"],
            state_reason_code="state_not_ready_to_post",
        )
        precheck = skill.with_autonomy_policy(
            runtime,
            ap_item=ap_item,
            payload=context["payload"],
            precheck=precheck,
            action=self.intent,
        )
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

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
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "detail": ((precheck.get("autonomy_policy") or {}).get("detail")) if blocked_reason == "autonomy_gate_blocked" else None,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="erp_post_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        override = runtime.coerce_bool(payload.get("override"))
        justification = str(payload.get("override_justification") or payload.get("reason") or "").strip() or None
        field_confidences = payload.get("field_confidences")
        if not isinstance(field_confidences, dict):
            metadata = runtime.parse_json_dict(ap_item.get("metadata"))
            field_confidences = metadata.get("field_confidences") if isinstance(metadata.get("field_confidences"), dict) else None

        override_ctx = None
        if override:
            from clearledgr.core.ap_states import OVERRIDE_TYPE_MULTI, OverrideContext

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
        blocked_reason = str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": response_status,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "erp_reference": (result or {}).get("erp_reference") if isinstance(result, dict) else None,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "none" if posted else "review_blockers",
        }
        if blocked:
            response["reason"] = blocked_reason
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="erp_post_completed" if posted else ("erp_post_blocked" if blocked else "erp_post_failed"),
            reason=blocked_reason if blocked else "runtime_post_to_erp",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class RouteLowRiskForApprovalHandler(APIntentHandler):
    intent = "route_low_risk_for_approval"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        workflow = skill.get_workflow(runtime)
        precheck = workflow.evaluate_batch_route_low_risk_for_approval(ap_item)
        org_settings = skill.load_org_settings(runtime)
        entity_routing = resolve_entity_routing(
            runtime.parse_json_dict(ap_item.get("metadata")),
            ap_item,
            organization_settings=org_settings,
        )
        reason_codes = list(precheck.get("reason_codes") or [])
        if entity_routing.get("status") == "needs_review":
            reason_codes.append("entity_route_review_required")
            precheck = {
                **precheck,
                "eligible": False,
                "reason_codes": list(dict.fromkeys(reason_codes)),
                "entity_routing": entity_routing,
            }
        precheck = skill.with_autonomy_policy(
            runtime,
            ap_item=ap_item,
            payload=context["payload"],
            precheck=precheck,
            action=self.intent,
        )
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        payload = context["payload"]
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)
        reason = str(payload.get("reason") or "agent_runtime_route_low_risk_for_approval")

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "autonomy_gate_blocked"
                if "autonomy_gate_blocked" in reason_codes
                else (
                    "state_not_validated"
                    if "state_not_validated" in reason_codes
                    else (
                        "budget_decision_required"
                        if "budget_decision_required" in reason_codes
                        else (
                            "exception_present"
                            if "exception_present" in reason_codes
                            else (
                                "non_invoice_document"
                                if "non_invoice_document" in reason_codes
                                else (
                                    "merged_source"
                                    if "merged_source" in reason_codes
                                    else (
                    "entity_route_review_required"
                    if "entity_route_review_required" in reason_codes
                    else (
                        "field_review_required"
                        if {"field_review_required", "blocking_source_conflicts"} & reason_codes
                        else "policy_precheck_failed"
                                    )
                                )
                            )
                        )
                    )
                ))
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "detail": ((precheck.get("autonomy_policy") or {}).get("detail")) if blocked_reason == "autonomy_gate_blocked" else None,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="route_low_risk_for_approval_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
                    "policy_precheck": precheck,
                    "response": response,
                },
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        invoice = workflow.build_invoice_data_from_ap_item(ap_item, actor_id=runtime.actor_email)
        resolved_gmail_id = str(
            getattr(invoice, "gmail_id", "")
            or email_id
            or ap_item.get("thread_id")
            or ap_item.get("message_id")
            or ap_item_id
            or ""
        ).strip()
        if not resolved_gmail_id:
            raise ValueError("missing_gmail_reference")
        if resolved_gmail_id != str(getattr(invoice, "gmail_id", "") or "").strip():
            try:
                invoice = replace(invoice, gmail_id=resolved_gmail_id)
            except TypeError:
                setattr(invoice, "gmail_id", resolved_gmail_id)
        workflow_result = await workflow._send_for_approval(
            invoice,
            extra_context={
                "intent": self.intent,
                "batch_reason": reason,
                "policy_precheck": precheck,
            },
        )
        routed = str((workflow_result or {}).get("status") or "").strip().lower() == "pending_approval"
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "pending_approval" if routed else "error",
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "result": workflow_result,
            "audit_contract": skill.audit_contract(self.intent),
        }
        _append_runtime_audit_best_effort(
            runtime,
            response,
            ap_item_id=ap_item_id,
            event_type="route_low_risk_for_approval" if routed else "route_low_risk_for_approval_failed",
            reason="agent_runtime_route_low_risk_for_approval",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": workflow_result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            suppress_errors=routed,
        )
        return response


class RetryRecoverableFailuresHandler(APIntentHandler):
    intent = "retry_recoverable_failures"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        workflow = skill.get_workflow(runtime)
        precheck = workflow.evaluate_batch_retry_recoverable_failure(ap_item)
        precheck = skill.with_autonomy_policy(
            runtime,
            ap_item=ap_item,
            payload=context["payload"],
            precheck=precheck,
            action=self.intent,
        )
        return {
            **context,
            "policy_precheck": precheck,
            "workflow": workflow,
        }

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        workflow = context["workflow"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

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
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "detail": ((precheck.get("autonomy_policy") or {}).get("detail")) if blocked_reason == "autonomy_gate_blocked" else None,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="retry_recoverable_failure_blocked",
                reason=blocked_reason,
                metadata={
                    "intent": self.intent,
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
        blocked_reason = str((result or {}).get("reason") or "").strip().lower() or "field_review_required"
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
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": response_status,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "erp_reference": result.get("erp_reference") if isinstance(result, dict) else None,
            "policy_precheck": precheck,
            "result": result,
            "audit_contract": skill.audit_contract(self.intent),
        }
        if blocked:
            response["reason"] = blocked_reason
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type=(
                "retry_recoverable_failure_completed"
                if response_status in {"posted", "ready_to_post"}
                else ("retry_recoverable_failure_blocked" if blocked else "retry_recoverable_failure_failed")
            ),
            reason=blocked_reason if blocked else "batch_retry_recoverable_failures",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "result": result,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


_HANDLERS: Dict[str, APIntentHandler] = {
    handler.intent: handler
    for handler in (
        RequestApprovalHandler(),
        ApproveInvoiceHandler(),
        RequestInfoHandler(),
        NudgeApprovalHandler(),
        EscalateApprovalHandler(),
        ReassignApprovalHandler(),
        RejectInvoiceHandler(),
        PostToERPHandler(),
        PrepareVendorFollowupsHandler(),
        RouteLowRiskForApprovalHandler(),
        RetryRecoverableFailuresHandler(),
    )
}


def get_ap_intent_handler(intent: str) -> APIntentHandler:
    normalized_intent = str(intent or "").strip().lower()
    handler = _HANDLERS.get(normalized_intent)
    if handler is None:
        raise ValueError(f"unsupported_intent:{normalized_intent or 'missing'}")
    return handler
