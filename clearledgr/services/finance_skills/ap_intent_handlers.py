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


# ---------------------------------------------------------------------------
# Phase 2 audit-P0 handlers — workspace SPA actions previously bypassed
# the runtime via direct db.update_ap_item writes. These handlers hold the
# full business logic so the routes can become thin HTTP→intent wrappers.
# ---------------------------------------------------------------------------


_SNOOZEABLE_STATES = frozenset({
    "received", "validated", "needs_info", "needs_approval", "failed_post",
})


def _import_state_helpers():
    from clearledgr.core.ap_states import (
        APState,
        IllegalTransitionError,
        normalize_state,
        validate_transition,
    )
    return APState, IllegalTransitionError, normalize_state, validate_transition


def _safe_parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        import json as _json
        try:
            parsed = _json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


class SnoozeInvoiceHandler(APIntentHandler):
    intent = "snooze_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        state = str(ap_item.get("state") or "").strip().lower()
        duration = safe_int(context["payload"].get("duration_minutes"), 0)
        reason_codes = []
        if state not in _SNOOZEABLE_STATES:
            reason_codes.append("state_not_snoozeable")
        if duration <= 0:
            reason_codes.append("duration_minutes_required")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
            "duration_minutes": duration,
        }
        return {**context, "policy_precheck": precheck}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        from datetime import timedelta

        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason = (
                "duration_minutes_required"
                if "duration_minutes_required" in (precheck.get("reason_codes") or [])
                else "state_not_snoozeable"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_snooze_blocked",
                reason=reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        duration = int(precheck["duration_minutes"])
        now = datetime.now(timezone.utc)
        snoozed_until = now + timedelta(minutes=duration)
        prev_state = precheck["state"]

        metadata = _safe_parse_metadata(ap_item.get("metadata"))
        metadata["pre_snooze_state"] = prev_state
        metadata["snoozed_until"] = snoozed_until.isoformat()
        note = str(payload.get("note") or "").strip()
        if note:
            metadata["snooze_note"] = note

        try:
            runtime.db.update_ap_item(
                ap_item_id,
                state="snoozed",
                metadata=metadata,
                _actor_type="user",
                _actor_id=actor["canonical_actor"],
                _source="snooze_invoice_intent",
                _decision_reason=note or f"snoozed_for_{duration}_minutes",
                _correlation_id=correlation_id,
            )
        except Exception as exc:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "error",
                "reason": str(exc),
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_snooze_failed",
                reason=str(exc),
                metadata={"intent": self.intent, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if hasattr(runtime.db, "append_ap_item_timeline_entry"):
            runtime.db.append_ap_item_timeline_entry(ap_item_id, {
                "event_type": "snoozed",
                "summary": f"Snoozed for {duration} minutes.",
                "reason": note or "",
                "next_action": f"Returns to queue at {snoozed_until.strftime('%Y-%m-%d %H:%M UTC')}.",
                "actor": actor["canonical_actor"],
                "timestamp": now.isoformat(),
            })

        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "snoozed",
            "snoozed_until": snoozed_until.isoformat(),
            "pre_snooze_state": prev_state,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "wait_for_unsnooze",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_snoozed",
            reason=note or f"snoozed_for_{duration}_minutes",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "duration_minutes": duration,
                "snoozed_until": snoozed_until.isoformat(),
                "pre_snooze_state": prev_state,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class UnsnoozeInvoiceHandler(APIntentHandler):
    intent = "unsnooze_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        state = str(ap_item.get("state") or "").strip().lower()
        metadata = _safe_parse_metadata(ap_item.get("metadata"))
        restore_state = str(metadata.get("pre_snooze_state") or "").strip().lower()
        reason_codes = []
        if state != "snoozed":
            reason_codes.append("state_not_snoozed")
        if not restore_state:
            # Default fallback restore-state matches the legacy behaviour.
            restore_state = "needs_approval"
        # Ensure the implicit transition is legal.
        _, _, _, validate_transition = _import_state_helpers()
        if state == "snoozed" and not validate_transition("snoozed", restore_state):
            reason_codes.append("restore_state_not_reachable")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
            "restore_state": restore_state,
        }
        return {**context, "policy_precheck": precheck}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason = (
                "restore_state_not_reachable"
                if "restore_state_not_reachable" in (precheck.get("reason_codes") or [])
                else "state_not_snoozed"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_unsnooze_blocked",
                reason=reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        restore_state = precheck["restore_state"]
        metadata = _safe_parse_metadata(ap_item.get("metadata"))
        metadata.pop("pre_snooze_state", None)
        metadata.pop("snoozed_until", None)
        metadata.pop("snooze_note", None)

        try:
            runtime.db.update_ap_item(
                ap_item_id,
                state=restore_state,
                metadata=metadata,
                _actor_type="user",
                _actor_id=actor["canonical_actor"],
                _source="unsnooze_invoice_intent",
                _decision_reason="manual_unsnooze",
                _correlation_id=correlation_id,
            )
        except Exception as exc:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "error",
                "reason": str(exc),
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_unsnooze_failed",
                reason=str(exc),
                metadata={"intent": self.intent, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        if hasattr(runtime.db, "append_ap_item_timeline_entry"):
            runtime.db.append_ap_item_timeline_entry(ap_item_id, {
                "event_type": "unsnoozed",
                "summary": f"Unsnoozed manually. Restored to {restore_state.replace('_', ' ')}.",
                "actor": actor["canonical_actor"],
                "reason": "manual_unsnooze",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "unsnoozed",
            "restored_state": restore_state,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "resume_active_processing",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_unsnoozed",
            reason="manual_unsnooze",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "restored_state": restore_state,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class ReverseInvoicePostHandler(APIntentHandler):
    intent = "reverse_invoice_post"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        state = str(ap_item.get("state") or "").strip().lower()
        reason = str(context["payload"].get("reason") or "").strip()
        reason_codes = []
        if state != "posted_to_erp":
            reason_codes.append("state_not_posted")
        if not reason:
            reason_codes.append("reason_required")
        if len(reason) > 512:
            reason_codes.append("reason_too_long")
        # Override window must exist; load it lazily so the precheck is cheap.
        window = None
        try:
            if context["ap_item_id"]:
                window = runtime.db.get_override_window_by_ap_item_id(context["ap_item_id"])
        except Exception:
            window = None
        if not window:
            reason_codes.append("no_override_window")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "state": state,
            "reason_supplied": bool(reason),
            "window_id": (window or {}).get("id"),
        }
        return {**context, "policy_precheck": precheck, "_window": window}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        window = context.get("_window") or {}
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "no_override_window" if "no_override_window" in reason_codes
                else ("reason_required" if "reason_required" in reason_codes
                      else ("reason_too_long" if "reason_too_long" in reason_codes
                            else "state_not_posted"))
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
                event_type="invoice_reverse_blocked",
                reason=blocked_reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        from clearledgr.services.override_window import get_override_window_service

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        org_id = (
            window.get("organization_id")
            or ap_item.get("organization_id")
            or runtime.organization_id
            or "default"
        )
        service = get_override_window_service(org_id, db=runtime.db)
        try:
            outcome = await service.attempt_reversal(
                window_id=str(window.get("id")),
                actor_id=actor["canonical_actor"],
                reason=str(payload.get("reason") or "").strip(),
            )
        except Exception as exc:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "error",
                "reason": str(exc),
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_reverse_failed",
                reason=str(exc),
                metadata={"intent": self.intent, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        succeeded = outcome.status in {"reversed", "already_reversed"}
        response_status = outcome.status
        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": response_status,
            "ap_item_id": ap_item_id,
            "email_id": email_id,
            "window_id": outcome.window_id,
            "reversal_ref": outcome.reversal_ref,
            "reversal_method": outcome.reversal_method,
            "erp": outcome.erp,
            "policy_precheck": precheck,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "none" if succeeded else "review_blockers",
        }
        if outcome.message:
            response["message"] = outcome.message
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type=(
                "invoice_reversed"
                if outcome.status == "reversed"
                else (
                    "invoice_reverse_blocked"
                    if outcome.status in {"expired", "already_reversed"}
                    else "invoice_reverse_failed"
                )
            ),
            reason=str(payload.get("reason") or "").strip() or outcome.status,
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "outcome": {
                    "status": outcome.status,
                    "window_id": outcome.window_id,
                    "reversal_ref": outcome.reversal_ref,
                    "reversal_method": outcome.reversal_method,
                    "erp": outcome.erp,
                    "message": outcome.message,
                },
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


class ManuallyClassifyInvoiceHandler(APIntentHandler):
    intent = "manually_classify_invoice"

    _ALLOWED_CLASSIFICATIONS = frozenset({
        "invoice", "credit_note", "payment_query", "vendor_statement", "irrelevant",
        "purchase_order", "receipt", "statement", "bank_statement", "other",
    })

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        classification = str(context["payload"].get("classification") or "").strip().lower()
        reason_codes = []
        if not classification:
            reason_codes.append("classification_required")
        elif classification not in self._ALLOWED_CLASSIFICATIONS:
            reason_codes.append("classification_not_allowed")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "classification": classification,
            "current_state": str(context["ap_item"].get("state") or "").strip().lower(),
        }
        return {**context, "policy_precheck": precheck}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        correlation_id = runtime.correlation_id_for_item(ap_item)

        if not precheck.get("eligible"):
            reason = (
                "classification_required"
                if "classification_required" in (precheck.get("reason_codes") or [])
                else "classification_not_allowed"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": reason,
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_manual_classify_blocked",
                reason=reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        classification = precheck["classification"]

        try:
            runtime.db.update_ap_item(
                ap_item_id,
                document_type=classification,
                _actor_type="user",
                _actor_id=actor["canonical_actor"],
                _source="manually_classify_invoice_intent",
                _decision_reason=f"manual_classification_to_{classification}",
                _correlation_id=correlation_id,
            )
        except Exception as exc:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "error",
                "reason": str(exc),
                "email_id": email_id,
                "ap_item_id": ap_item_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_manual_classify_failed",
                reason=str(exc),
                metadata={"intent": self.intent, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        # Enqueue MANUAL_CLASSIFICATION event so the planning engine picks
        # up the new classification and re-routes downstream.
        try:
            from clearledgr.core.events import AgentEvent, AgentEventType
            from clearledgr.core.event_queue import get_event_queue
            get_event_queue().enqueue(AgentEvent(
                type=AgentEventType.MANUAL_CLASSIFICATION,
                source="ap_manager",
                payload={
                    "message_id": ap_item.get("message_id") or ap_item.get("thread_id", ""),
                    "classification": classification,
                    "classified_by": actor["canonical_actor"],
                    "ap_item_id": ap_item_id,
                },
                organization_id=runtime.organization_id,
            ))
        except Exception as exc:
            logger.debug("[manually_classify] event enqueue failed (non-fatal): %s", exc)

        if hasattr(runtime.db, "append_ap_item_timeline_entry"):
            runtime.db.append_ap_item_timeline_entry(ap_item_id, {
                "type": "human_action",
                "summary": f"Manually classified as {classification}",
                "actor": actor["canonical_actor"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "classified",
            "classification": classification,
            "ap_item_id": ap_item_id,
            "email_id": email_id,
            "policy_precheck": precheck,
            "audit_contract": skill.audit_contract(self.intent),
            "next_step": "planning_engine_reroute",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_manually_classified",
            reason=f"manual_classification_to_{classification}",
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "classification": classification,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        return response


def _import_ap_item_service():
    """Lazy import of ap_item_service to avoid circular imports at module load."""
    from clearledgr.services import ap_item_service as _svc
    return _svc


_RESUBMIT_INITIAL_STATES = frozenset({"received", "validated"})


class ResubmitInvoiceHandler(APIntentHandler):
    intent = "resubmit_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        ap_item = context["ap_item"]
        svc = _import_ap_item_service()
        source_state = svc._normalized_state_value(ap_item.get("state"))
        initial_state = svc._normalized_state_value(
            context["payload"].get("initial_state") or "received"
        )
        existing_child_id = str(ap_item.get("superseded_by_ap_item_id") or "").strip()
        reason = str(context["payload"].get("reason") or "").strip()
        reason_codes = []
        if source_state != "rejected":
            reason_codes.append("state_not_rejected")
        if initial_state not in _RESUBMIT_INITIAL_STATES:
            reason_codes.append("invalid_initial_state")
        if not reason:
            reason_codes.append("reason_required")
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "source_state": source_state,
            "initial_state": initial_state,
            "existing_child_id": existing_child_id or None,
        }
        return {**context, "policy_precheck": precheck}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        svc = _import_ap_item_service()
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        correlation_id = runtime.correlation_id_for_item(ap_item)
        db = runtime.db

        # Idempotent already-resubmitted: existing supersession surfaces same response.
        existing_child_id = precheck.get("existing_child_id")
        if existing_child_id:
            existing_child = db.get_ap_item(existing_child_id)
            if existing_child:
                response = {
                    "skill_id": skill.skill_id,
                    "intent": self.intent,
                    "status": "already_resubmitted",
                    "source_ap_item_id": ap_item_id,
                    "new_ap_item_id": existing_child_id,
                    "ap_item": svc.build_worklist_item(db, existing_child),
                    "linkage": {
                        "supersedes_ap_item_id": ap_item_id,
                        "supersedes_invoice_key": existing_child.get("supersedes_invoice_key")
                        or ap_item.get("invoice_key"),
                        "superseded_by_ap_item_id": existing_child_id,
                    },
                    "audit_contract": skill.audit_contract(self.intent),
                    "policy_precheck": precheck,
                }
                audit_row = runtime.append_runtime_audit(
                    ap_item_id=ap_item_id,
                    event_type="invoice_resubmitted",
                    reason="already_resubmitted_replay",
                    metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                response["audit_event_id"] = (audit_row or {}).get("id")
                return response

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "reason_required" if "reason_required" in reason_codes
                else ("invalid_resubmission_initial_state" if "invalid_initial_state" in reason_codes
                      else "resubmission_requires_rejected_state")
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_resubmit_blocked",
                reason=blocked_reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        actor_id = actor["canonical_actor"]
        reason = str(payload.get("reason") or "").strip()
        initial_state = precheck["initial_state"]

        source_meta = svc._parse_json(ap_item.get("metadata"))
        new_meta = dict(source_meta)
        for stale_key in (
            "merged_into",
            "merge_reason",
            "merge_status",
            "suppressed_from_worklist",
            "confidence_override",
        ):
            new_meta.pop(stale_key, None)

        # Build a request-shaped object for invoice_key derivation helpers.
        from types import SimpleNamespace
        request_like = SimpleNamespace(
            invoice_number=payload.get("invoice_number"),
            invoice_date=payload.get("invoice_date"),
            actor_id=payload.get("actor_id"),
            reason=reason,
        )
        new_meta["supersedes_ap_item_id"] = ap_item_id
        new_meta["supersedes_invoice_key"] = svc._superseded_invoice_key(ap_item, request_like)
        new_meta["resubmission_reason"] = reason
        new_meta["resubmission"] = {
            "source_ap_item_id": ap_item_id,
            "reason": reason,
            "actor_id": actor_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        requested_actor = str(payload.get("actor_id") or "").strip()
        if requested_actor and requested_actor != actor_id:
            new_meta["requested_actor_id"] = requested_actor
        extra_meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if extra_meta:
            new_meta.update(extra_meta)

        create_payload: Dict[str, Any] = {
            "invoice_key": svc._resubmission_invoice_key(ap_item, request_like),
            "thread_id": payload.get("thread_id") or ap_item.get("thread_id"),
            "message_id": payload.get("message_id") or ap_item.get("message_id"),
            "subject": payload.get("subject") or ap_item.get("subject"),
            "sender": payload.get("sender") or ap_item.get("sender"),
            "vendor_name": payload.get("vendor_name") or ap_item.get("vendor_name"),
            "amount": payload.get("amount") if payload.get("amount") is not None else ap_item.get("amount"),
            "currency": payload.get("currency") or ap_item.get("currency") or "USD",
            "invoice_number": payload.get("invoice_number") or ap_item.get("invoice_number"),
            "invoice_date": payload.get("invoice_date") or ap_item.get("invoice_date"),
            "due_date": payload.get("due_date") or ap_item.get("due_date"),
            "state": initial_state,
            "confidence": ap_item.get("confidence"),
            "approval_required": bool(ap_item.get("approval_required", True)),
            "workflow_id": ap_item.get("workflow_id"),
            "run_id": None,
            "approval_surface": ap_item.get("approval_surface") or "hybrid",
            "approval_policy_version": ap_item.get("approval_policy_version"),
            "post_attempted_at": None,
            "last_error": None,
            "organization_id": ap_item.get("organization_id"),
            "user_id": ap_item.get("user_id"),
            "po_number": ap_item.get("po_number"),
            "attachment_url": ap_item.get("attachment_url"),
            "supersedes_ap_item_id": ap_item_id,
            "supersedes_invoice_key": svc._superseded_invoice_key(ap_item, request_like),
            "superseded_by_ap_item_id": None,
            "resubmission_reason": reason,
            "metadata": new_meta,
        }

        try:
            created = db.create_ap_item(create_payload)
            db.update_ap_item(
                ap_item_id,
                superseded_by_ap_item_id=created["id"],
                _actor_type="user",
                _actor_id=actor_id,
                _decision_reason=reason,
                _correlation_id=correlation_id,
            )
            source_after = db.get_ap_item(ap_item_id) or ap_item
            source_after_meta = svc._parse_json(source_after.get("metadata"))
            source_after_meta["superseded_by_ap_item_id"] = created["id"]
            source_after_meta["resubmission_reason"] = reason
            db.update_ap_item(
                ap_item_id,
                metadata=source_after_meta,
                _actor_type="user",
                _actor_id=actor_id,
                _decision_reason=reason,
                _correlation_id=correlation_id,
            )
            copied_sources = 0
            if bool(payload.get("copy_sources", True)):
                copied_sources = svc._copy_item_sources_for_resubmission(
                    db,
                    source_ap_item_id=ap_item_id,
                    target_ap_item_id=created["id"],
                    actor_id=actor_id,
                )
        except Exception as exc:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "error",
                "reason": str(exc),
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_resubmit_failed",
                reason=str(exc),
                metadata={"intent": self.intent, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "resubmitted",
            "source_ap_item_id": ap_item_id,
            "new_ap_item_id": created["id"],
            "copied_sources": copied_sources,
            "linkage": {
                "supersedes_ap_item_id": ap_item_id,
                "supersedes_invoice_key": created.get("supersedes_invoice_key")
                or svc._superseded_invoice_key(ap_item, request_like),
                "superseded_by_ap_item_id": created["id"],
                "resubmission_reason": reason,
            },
            "ap_item": svc.build_worklist_item(db, created),
            "audit_contract": skill.audit_contract(self.intent),
            "policy_precheck": precheck,
            "next_step": "follow_new_item",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_resubmitted",
            reason=reason,
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "source_ap_item_id": ap_item_id,
                "new_ap_item_id": created["id"],
                "copied_sources": copied_sources,
                "response": response,
            },
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
        # Also write a parallel audit on the new item so its timeline
        # records its own creation context.
        try:
            runtime.append_runtime_audit(
                ap_item_id=created["id"],
                event_type="invoice_resubmitted",
                reason=reason,
                metadata={
                    "intent": self.intent,
                    "source_ap_item_id": ap_item_id,
                    "new_ap_item_id": created["id"],
                    "creation_context": True,
                },
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:new" if idempotency_key else None,
                skill_id=skill.skill_id,
            )
        except Exception as audit_exc:
            logger.warning("[resubmit_invoice] audit on new item failed: %s", audit_exc)
        return response


class SplitInvoiceHandler(APIntentHandler):
    intent = "split_invoice"

    def policy_precheck(self, skill, runtime, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = _base_context(self.intent, runtime, payload)
        sources = context["payload"].get("sources")
        reason_codes = []
        if not isinstance(sources, list) or not sources:
            reason_codes.append("sources_required")
        else:
            for entry in sources:
                if not isinstance(entry, dict):
                    reason_codes.append("source_entry_invalid")
                    break
                if not str(entry.get("source_type") or "").strip():
                    reason_codes.append("source_type_required")
                    break
                if not str(entry.get("source_ref") or "").strip():
                    reason_codes.append("source_ref_required")
                    break
        precheck = {
            "eligible": not reason_codes,
            "reason_codes": reason_codes,
            "source_count_requested": len(sources) if isinstance(sources, list) else 0,
        }
        return {**context, "policy_precheck": precheck}

    async def execute(self, skill, runtime, context: Dict[str, Any], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        svc = _import_ap_item_service()
        ap_item = context["ap_item"]
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        payload = context["payload"]
        correlation_id = runtime.correlation_id_for_item(ap_item)
        db = runtime.db

        if not precheck.get("eligible"):
            reason_codes = set(precheck.get("reason_codes") or [])
            blocked_reason = (
                "sources_required" if "sources_required" in reason_codes
                else "source_entry_invalid"
            )
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": blocked_reason,
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_split_blocked",
                reason=blocked_reason,
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        actor = _resolve_actor_fields(runtime, payload, fallback="workspace_spa")
        actor_id = actor["canonical_actor"]
        reason = str(payload.get("reason") or "").strip() or "manual_split"
        sources = payload["sources"]
        parent_meta = svc._parse_json(ap_item.get("metadata"))
        now_iso = datetime.now(timezone.utc).isoformat()
        created_items: list = []

        for source_entry in sources:
            source_type = str(source_entry.get("source_type") or "").strip()
            source_ref = str(source_entry.get("source_ref") or "").strip()
            current_sources = db.list_ap_item_sources(ap_item_id, source_type=source_type)
            current = next(
                (row for row in current_sources if row.get("source_ref") == source_ref),
                None,
            )
            if not current:
                continue

            split_payload = {
                "invoice_key": f"{ap_item.get('invoice_key') or ap_item_id}#split#{source_type}:{source_ref}",
                "thread_id": ap_item.get("thread_id"),
                "message_id": ap_item.get("message_id"),
                "subject": current.get("subject") or ap_item.get("subject"),
                "sender": current.get("sender") or ap_item.get("sender"),
                "vendor_name": ap_item.get("vendor_name"),
                "amount": ap_item.get("amount"),
                "currency": ap_item.get("currency") or "USD",
                "invoice_number": ap_item.get("invoice_number"),
                "invoice_date": ap_item.get("invoice_date"),
                "due_date": ap_item.get("due_date"),
                "state": "needs_info",
                "confidence": ap_item.get("confidence") or 0,
                "approval_required": bool(ap_item.get("approval_required", True)),
                "organization_id": ap_item.get("organization_id") or "default",
                "user_id": ap_item.get("user_id"),
                "metadata": {
                    **parent_meta,
                    "split_from_ap_item_id": ap_item_id,
                    "split_reason": reason,
                    "split_actor_id": actor_id,
                    "split_source": {"source_type": source_type, "source_ref": source_ref},
                    "split_at": now_iso,
                },
            }
            try:
                child = db.create_ap_item(split_payload)
                db.move_ap_item_source(
                    from_ap_item_id=ap_item_id,
                    to_ap_item_id=child["id"],
                    source_type=source_type,
                    source_ref=source_ref,
                )
                if source_type == "gmail_thread":
                    db.update_ap_item(child["id"], thread_id=source_ref)
                if source_type == "gmail_message":
                    db.update_ap_item(child["id"], message_id=source_ref)
            except Exception as exc:
                logger.exception("[split_invoice] child creation failed for %s/%s: %s", source_type, source_ref, exc)
                continue

            try:
                runtime.append_runtime_audit(
                    ap_item_id=child["id"],
                    event_type="invoice_split",
                    reason=reason,
                    metadata={
                        "intent": self.intent,
                        "parent_ap_item_id": ap_item_id,
                        "source_type": source_type,
                        "source_ref": source_ref,
                        "creation_context": True,
                    },
                    correlation_id=correlation_id,
                    idempotency_key=(
                        f"{idempotency_key}:{child['id']}" if idempotency_key else None
                    ),
                    skill_id=skill.skill_id,
                )
            except Exception as audit_exc:
                logger.warning("[split_invoice] child audit failed: %s", audit_exc)

            created_items.append(child)

        if not created_items:
            response = {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "blocked",
                "reason": "no_sources_split",
                "ap_item_id": ap_item_id,
                "email_id": email_id,
                "policy_precheck": precheck,
                "audit_contract": skill.audit_contract(self.intent),
            }
            audit_row = runtime.append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="invoice_split_blocked",
                reason="no_sources_split",
                metadata={"intent": self.intent, "policy_precheck": precheck, "response": response},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
            return response

        # Update parent metadata source_count.
        try:
            parent_meta["source_count"] = len(db.list_ap_item_sources(ap_item_id))
            db.update_ap_item(
                ap_item_id,
                metadata=parent_meta,
                _actor_type="user",
                _actor_id=actor_id,
                _decision_reason=reason,
                _correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.warning("[split_invoice] parent metadata refresh failed for %s: %s", ap_item_id, exc)

        # Track split items against subscription quota (best-effort).
        try:
            from clearledgr.services.subscription import get_subscription_service
            split_org_id = ap_item.get("organization_id") or "default"
            get_subscription_service().increment_usage(
                split_org_id, "invoices_this_month", amount=len(created_items),
            )
        except Exception:
            pass

        response = {
            "skill_id": skill.skill_id,
            "intent": self.intent,
            "status": "split",
            "parent_ap_item_id": ap_item_id,
            "email_id": email_id,
            "created_items": [
                svc.build_worklist_item(
                    db,
                    item,
                    approval_policy=svc._approval_followup_policy(
                        str(item.get("organization_id") or ap_item.get("organization_id") or "default")
                    ),
                )
                for item in created_items
            ],
            "audit_contract": skill.audit_contract(self.intent),
            "policy_precheck": precheck,
            "next_step": "review_children",
        }
        audit_row = runtime.append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="invoice_split",
            reason=reason,
            metadata={
                "intent": self.intent,
                "policy_precheck": precheck,
                "child_ids": [child["id"] for child in created_items],
                "child_count": len(created_items),
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
        SnoozeInvoiceHandler(),
        UnsnoozeInvoiceHandler(),
        ReverseInvoicePostHandler(),
        ManuallyClassifyInvoiceHandler(),
        ResubmitInvoiceHandler(),
        SplitInvoiceHandler(),
    )
}


def get_ap_intent_handler(intent: str) -> APIntentHandler:
    normalized_intent = str(intent or "").strip().lower()
    handler = _HANDLERS.get(normalized_intent)
    if handler is None:
        raise ValueError(f"unsupported_intent:{normalized_intent or 'missing'}")
    return handler
