"""Deterministic Planning Engine — Agent Design Specification §4.

Given an event and the current Box state, produces a Plan — an ordered
sequence of Actions. The planning engine is pure deterministic code.
No Claude calls. Claude is only called WITHIN specific Actions during
execution (classify_email, extract_invoice_fields, etc.), never during
planning itself.

"Rules decide. Claude describes."

Usage:
    from clearledgr.core.planning_engine import get_planning_engine
    from clearledgr.core.events import AgentEvent, AgentEventType

    engine = get_planning_engine()
    plan = engine.plan(event, box_state)
    # plan.actions is the ordered list of Actions to execute
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.core.events import AgentEvent, AgentEventType
from clearledgr.core.plan import Action, Plan

logger = logging.getLogger(__name__)


class DeterministicPlanningEngine:
    """§4: Deterministic event→plan dispatch.

    The planning engine is invoked once per event. It does not execute
    anything — that is the execution engine's job.
    """

    def __init__(self, db: Any = None):
        self._db = db

    def _get_db(self) -> Any:
        if self._db is not None:
            return self._db
        from clearledgr.core.database import get_db
        self._db = get_db()
        return self._db

    def plan(self, event: AgentEvent, box_state: Optional[Dict[str, Any]] = None) -> Plan:
        """Produce a Plan from event + Box state. No LLM calls."""
        box_state = box_state or {}
        dispatcher = {
            AgentEventType.EMAIL_RECEIVED: self._plan_email_received,
            AgentEventType.APPROVAL_RECEIVED: self._plan_approval_received,
            AgentEventType.TIMER_FIRED: self._plan_timer_fired,
            AgentEventType.OVERRIDE_WINDOW_EXPIRED: self._plan_override_expired,
            AgentEventType.VENDOR_RESPONSE_RECEIVED: self._plan_vendor_response,
            AgentEventType.KYC_DOCUMENT_RECEIVED: self._plan_kyc_document,
            AgentEventType.IBAN_CHANGE_SUBMITTED: self._plan_iban_change,
            AgentEventType.PAYMENT_CONFIRMED: self._plan_payment_confirmed,
            AgentEventType.ERP_GRN_CONFIRMED: self._plan_grn_confirmed,
            AgentEventType.MANUAL_CLASSIFICATION: self._plan_manual_classification,
            AgentEventType.LABEL_CHANGED: self._plan_label_changed,
        }
        handler = dispatcher.get(event.type)
        if not handler:
            logger.warning("[PlanningEngine] No handler for event type: %s", event.type.value)
            return Plan(event_type=event.type.value, actions=[], organization_id=event.organization_id)

        plan = handler(event, box_state)
        plan.organization_id = event.organization_id
        logger.info(
            "[PlanningEngine] %s → %d-step plan for org=%s",
            event.type.value, plan.step_count, event.organization_id,
        )
        return plan

    # ------------------------------------------------------------------
    # §4.1: Planning for email_received (the 19-step invoice plan)
    # ------------------------------------------------------------------

    def _plan_email_received(self, event: AgentEvent, box_state: dict) -> Plan:
        """§4.1: The most complex planning path — handles all incoming emails.

        Steps 1-2 (read + classify) always run. Step 3 branches on
        classification type to produce the correct plan per §4.1.3:
          invoice      → 19-step invoice plan
          credit_note  → credit note plan
          payment_query → query plan
          vendor_statement → statement plan
          onboarding_response → onboarding plan
          irrelevant   → apply_label(Not Finance), archive
          unclassifiable → apply_label(Review Required), flag
        """
        payload = event.payload
        message_id = payload.get("message_id", "")
        mailbox = payload.get("mailbox", "")
        user_id = payload.get("user_id", "")

        # Check if thread is already watched (linked to existing Box)
        thread_id = payload.get("thread_id", "")
        if thread_id and box_state.get("thread_id") == thread_id:
            return self._plan_thread_continuation(event, box_state)

        # Steps 1-2: always read + classify. The execution engine's
        # classify_email handler returns _stop_plan=True for non-invoice
        # types, so the invoice steps below only execute for invoices.
        #
        # For non-invoice types, the classify handler also sets
        # ctx["classification"]["type"] which the execution engine uses
        # to select the right follow-up plan.
        actions = [
            # Step 1: Fetch and check
            Action("read_email", "DET",
                   {"message_id": message_id, "user_id": user_id},
                   "Fetch full email content from Gmail API"),
            # Step 2: Classify
            Action("classify_email", "LLM",
                   {"message_id": message_id},
                   "Classify email as invoice, credit note, query, or irrelevant"),
            # Step 3: Flag internal instruction (fraud control)
            Action("flag_internal_instruction", "DET",
                   {},
                   "Detect emails from internal senders instructing payment actions"),
            # Step 4: Apply received label
            Action("apply_label", "DET",
                   {"label": "Clearledgr/Invoice/Received"},
                   "Apply Received stage label to Gmail thread"),
            # Step 5: Create Box
            Action("create_box", "DET",
                   {"pipeline": "ap_invoices", "mailbox": mailbox, "user_id": user_id},
                   "Create AP item in Received stage"),
            # Step 6: Vendor check (spec: lookup_vendor_master)
            Action("lookup_vendor_master", "DET",
                   {},
                   "Query ERP vendor master for match by domain and name"),
            Action("check_domain_match", "DET",
                   {},
                   "Validate sender domain matches vendor master"),
            # Step 7: Duplicate check (pre-extraction)
            Action("check_duplicate", "DET",
                   {"window_days": 90, "phase": "pre_extraction"},
                   "Check for duplicate invoice (pre-extraction, partial)"),
            # Step 8: Extraction
            Action("extract_invoice_fields", "LLM",
                   {"message_id": message_id},
                   "Extract structured invoice fields via Claude"),
            # Step 9: Guardrails
            Action("run_extraction_guardrails", "DET",
                   {},
                   "Apply 5 deterministic extraction guardrails"),
            # Step 10: Duplicate check (post-extraction, full)
            Action("check_duplicate_full", "DET",
                   {"window_days": 90, "phase": "post_extraction"},
                   "Check for duplicate invoice (post-extraction, full)"),
            # Step 11: Fraud checks
            Action("check_amount_ceiling", "DET",
                   {},
                   "Validate amount does not exceed per-vendor ceiling"),
            Action("check_velocity", "DET",
                   {"window_days": 7},
                   "Check invoice velocity for this vendor"),
            # Step 12: Update fields
            Action("update_box_fields", "DET",
                   {},
                   "Persist extracted fields to Box record"),
            Action("link_vendor_to_box", "DET",
                   {},
                   "Associate Vendor record with invoice Box"),
            # Step 13: ERP lookups
            Action("lookup_po", "DET",
                   {},
                   "Fetch Purchase Order from ERP"),
            # Step 14: GRN lookup
            Action("lookup_grn", "DET",
                   {},
                   "Fetch Goods Receipt Notes from ERP"),
            # Step 15: 3-way match
            Action("run_three_way_match", "DET",
                   {},
                   "Execute deterministic 3-way match algorithm"),
            # Step 16: Apply matched label
            Action("apply_label", "DET",
                   {"label": "Clearledgr/Invoice/Matched"},
                   "Apply Matched stage label"),
            # Step 17: Move stage
            Action("move_box_stage", "DET",
                   {"target": "awaiting_approval"},
                   "Advance Box to awaiting_approval stage"),
            # Step 18: Send approval (spec: send_slack_approval)
            Action("send_slack_approval", "DET",
                   {},
                   "Send structured approval message to Slack"),
            # Step 19: Set waiting condition
            Action("set_waiting_condition", "DET",
                   {"type": "approval_response", "timeout_hours": 4},
                   "Record that agent is waiting for approval decision"),
        ]
        return Plan(event_type="email_received", actions=actions)

    def _plan_thread_continuation(self, event: AgentEvent, box_state: dict) -> Plan:
        """Handle a new message on an already-watched thread."""
        return Plan(
            event_type="thread_continuation",
            actions=[
                Action("read_email", "DET",
                       {"message_id": event.payload.get("message_id", "")},
                       "Fetch reply content"),
                Action("classify_vendor_response", "LLM",
                       {},
                       "Classify vendor reply (document, question, OOO, etc.)"),
                Action("route_vendor_response", "DET",
                       {},
                       "Route response to appropriate handler"),
            ],
            box_id=box_state.get("id"),
        )

    # ------------------------------------------------------------------
    # §4.2: Planning for approval_received
    # ------------------------------------------------------------------

    def _plan_approval_received(self, event: AgentEvent, box_state: dict) -> Plan:
        """§4.2: AP Manager clicks Approve or Reject in Slack/Teams."""
        decision = event.payload.get("decision", "approved")
        box_id = event.payload.get("box_id", "")

        if decision in ("approved", "approve", "override_approved"):
            override_reason = event.payload.get("override_reason", "")
            actions = [
                Action("clear_waiting_condition", "DET", {},
                       "Clear approval waiting condition"),
                Action("pre_post_validate", "DET", {},
                       "Re-validate against current ERP state before posting"),
                Action("post_bill", "DET", {},
                       "Write invoice as bill to ERP"),
                Action("move_box_stage", "DET",
                       {"target": "approved"},
                       "Advance Box to approved stage"),
                Action("apply_label", "DET",
                       {"label": "Clearledgr/Invoice/Approved"},
                       "Apply Approved stage label"),
                Action("schedule_payment", "DET", {},
                       "Create payment schedule entry in ERP"),
                Action("post_timeline_entry", "DET",
                       {"format": "DID-WHY-NEXT", "override_reason": override_reason},
                       "Record DID-WHY-NEXT timeline entry"),
                Action("send_slack_override_window", "DET", {},
                       "Post override window notification with Undo button"),
                Action("watch_thread", "DET", {},
                       "Register thread for monitoring"),
            ]
        else:  # rejected
            actions = [
                Action("clear_waiting_condition", "DET", {},
                       "Clear approval waiting condition"),
                Action("move_box_stage", "DET",
                       {"target": "exception"},
                       "Move Box to exception stage"),
                Action("apply_label", "DET",
                       {"label": "Clearledgr/Invoice/Exception"},
                       "Apply Exception stage label"),
                Action("send_vendor_email", "DET",
                       {"template": "payment_query_response"},
                       "Notify vendor of rejection"),
                Action("post_timeline_entry", "DET",
                       {"reason": event.payload.get("override_reason", "Rejected")},
                       "Record rejection to timeline"),
            ]

        return Plan(event_type="approval_received", actions=actions, box_id=box_id)

    # ------------------------------------------------------------------
    # §4.3: Planning for timer_fired
    # ------------------------------------------------------------------

    def _plan_timer_fired(self, event: AgentEvent, box_state: dict) -> Plan:
        """§4.3: A scheduled job's time condition has been met."""
        timer_type = event.payload.get("timer_type", "unknown")
        box_id = event.payload.get("box_id", "")

        if timer_type == "grn_check":
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("lookup_grn", "DET", {},
                           "Re-check GRN status in ERP"),
                    Action("evaluate_grn_result", "DET",
                           {"max_retries": 10, "check_interval_hours": 4},
                           "Clear waiting if confirmed, reschedule if pending, escalate if overdue"),
                ],
                box_id=box_id,
            )
        elif timer_type == "vendor_chase":
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("check_vendor_response", "DET", {},
                           "Check if vendor has responded"),
                    Action("send_vendor_email", "DET",
                           {"template": "chase"},
                           "Send chase email if no response"),
                ],
                box_id=box_id,
            )
        elif timer_type == "approval_timeout":
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("escalate_approval", "DET", {},
                           "Escalate approval to next tier in hierarchy"),
                    Action("post_timeline_entry", "DET",
                           {"summary": "Approval escalated to next tier due to timeout"},
                           "Log escalation to Box timeline"),
                ],
                box_id=box_id,
            )
        elif timer_type == "override_window_close":
            return self._plan_override_expired(event, box_state)
        elif timer_type == "snooze_expired":
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("unsnooze", "DET", {},
                           "Restore snoozed item to pre-snooze state"),
                ],
                box_id=box_id,
            )
        elif timer_type == "iban_verification_deadline":
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("freeze_vendor_payments", "DET",
                           {"reason": "iban_verification_timeout"},
                           "Freeze vendor payments — IBAN verification deadline exceeded"),
                    Action("send_slack_exception", "DET",
                           {"reason": "IBAN verification not complete within 5 business days"},
                           "Alert AP Manager about IBAN verification deadline"),
                ],
                box_id=box_id,
            )
        elif timer_type in ("erp_recheck", "external_dependency_unavailable"):
            # §12.2: ERP connectivity check — if ERP still unavailable,
            # reschedule for another 15 min. If restored, clear_waiting
            # and resume from pending_plan.
            return Plan(
                event_type="timer_fired",
                actions=[
                    Action("check_erp_connectivity", "DET", {},
                           "Check if ERP is back online"),
                    Action("evaluate_erp_recheck", "DET", {},
                           "Clear waiting + resume, or reschedule 15-min check"),
                ],
                box_id=box_id,
            )

        logger.warning("[PlanningEngine] Unknown timer type: %s", timer_type)
        return Plan(event_type="timer_fired", actions=[], box_id=box_id)

    # ------------------------------------------------------------------
    # Other event handlers
    # ------------------------------------------------------------------

    def _plan_override_expired(self, event: AgentEvent, box_state: dict) -> Plan:
        """Override window closed — action is now irreversible."""
        return Plan(
            event_type="override_window_expired",
            actions=[
                Action("close_override_window", "DET", {},
                       "Mark override window as closed — action confirmed"),
                Action("post_timeline_entry", "DET",
                       {"summary": "Override window closed — action confirmed"},
                       "Record closure to timeline"),
            ],
            box_id=event.payload.get("box_id", ""),
        )

    def _plan_vendor_response(self, event: AgentEvent, box_state: dict) -> Plan:
        """Vendor replies to an onboarding or chase email."""
        return Plan(
            event_type="vendor_response_received",
            actions=[
                Action("read_email", "DET",
                       {"message_id": event.payload.get("message_id", "")},
                       "Fetch vendor reply content"),
                Action("classify_vendor_response", "LLM",
                       {"vendor_id": event.payload.get("vendor_id", "")},
                       "Classify vendor response type"),
                Action("route_vendor_response", "DET", {},
                       "Route to appropriate onboarding step"),
            ],
        )

    def _plan_kyc_document(self, event: AgentEvent, box_state: dict) -> Plan:
        """Vendor submits a KYC document via the portal."""
        return Plan(
            event_type="kyc_document_received",
            actions=[
                Action("validate_kyc_document", "DET",
                       {"document_type": event.payload.get("document_type", ""),
                        "vendor_id": event.payload.get("vendor_id", "")},
                       "Validate document against requirements checklist"),
                Action("update_onboarding_progress", "DET", {},
                       "Update onboarding stage if all documents received"),
            ],
        )

    def _plan_iban_change(self, event: AgentEvent, box_state: dict) -> Plan:
        """Bank details submitted — kick off three-factor verification.

        Fired from the vendor portal when a vendor saves new bank details
        (fresh onboarding OR a change to an already-verified IBAN).
        session_id is carried through every action so the execution
        engine can initiate micro-deposits against the right session
        without re-resolving.
        """
        vendor_id = event.payload.get("vendor_id", "")
        session_id = event.payload.get("session_id", "")
        new_iban = event.payload.get("new_iban", "")
        return Plan(
            event_type="iban_change_submitted",
            actions=[
                Action(
                    "check_iban_change", "DET",
                    {
                        "vendor_id": vendor_id,
                        "session_id": session_id,
                        "new_iban": new_iban,
                    },
                    "Detect IBAN change and flag payment hold if needed",
                ),
                Action(
                    "freeze_vendor_payments", "DET",
                    {"vendor_id": vendor_id, "reason": "iban_change_detected"},
                    "Apply payment hold on vendor",
                ),
                Action(
                    "initiate_iban_verification", "DET",
                    {"vendor_id": vendor_id, "session_id": session_id},
                    "Start micro-deposit IBAN verification",
                ),
            ],
        )

    def _plan_payment_confirmed(self, event: AgentEvent, box_state: dict) -> Plan:
        """ERP confirms payment has settled."""
        return Plan(
            event_type="payment_confirmed",
            actions=[
                Action("move_box_stage", "DET",
                       {"target": "paid"},
                       "Advance Box to paid (terminal) stage"),
                Action("apply_label", "DET",
                       {"label": "Clearledgr/Invoice/Paid"},
                       "Apply Paid stage label"),
                Action("post_timeline_entry", "DET",
                       {"summary": f"Payment settled. Ref: {event.payload.get('payment_reference', '')}"},
                       "Record payment confirmation to timeline"),
            ],
            box_id=event.payload.get("box_id", ""),
        )

    def _plan_grn_confirmed(self, event: AgentEvent, box_state: dict) -> Plan:
        """GRN confirmed in ERP — clear waiting condition, resume matching."""
        return Plan(
            event_type="erp_grn_confirmed",
            actions=[
                Action("clear_waiting_condition", "DET", {},
                       "Clear GRN waiting condition"),
                Action("resume_from_pending_plan", "DET", {},
                       "Resume the paused plan from pending_plan column"),
            ],
            box_id=event.payload.get("box_id", ""),
        )

    def _plan_label_changed(self, event: AgentEvent, box_state: dict) -> Plan:
        """Phase 2: user applies a Clearledgr/* label in Gmail → drive workflow.

        Payload: {box_id, label_name, intent, actor_email, thread_id}.

        The webhook has already:
          - filtered to labels in LABEL_TO_INTENT,
          - confirmed the actor is a workspace member (not the agent),
          - resolved the thread to an AP box.

        So by the time we plan, we just translate the intent into the
        same action sequence that the approval/rejection flows use.
        """
        from clearledgr.services.gmail_labels import intent_for_label

        box_id = event.payload.get("box_id")
        label_name = event.payload.get("label_name") or ""
        intent = event.payload.get("intent") or intent_for_label(label_name)
        actor_email = event.payload.get("actor_email") or "unknown"

        if not intent or not box_id:
            return Plan(event_type="label_changed", actions=[], box_id=box_id)

        via_label_ctx = {
            "source": "gmail_label",
            "label_name": label_name,
            "actor_email": actor_email,
        }

        if intent == "approve_invoice":
            return Plan(
                event_type="label_changed",
                box_id=box_id,
                actions=[
                    Action("clear_waiting_condition", "DET", {}, "Clear waiting condition if any"),
                    Action("pre_post_validate", "DET", {}, "Pre-post validation before ERP"),
                    Action("post_bill", "DET", {"via_label": True}, "Post bill to ERP"),
                    Action("move_box_stage", "DET", {"target": "approved"}, "Move to approved"),
                    Action("apply_label", "DET", {"label": "Clearledgr/Invoice/Approved"},
                           "Confirm approval label"),
                    Action("schedule_payment", "DET", {}, "Schedule payment"),
                    Action("post_timeline_entry", "DET",
                           {"format": "DID-WHY-NEXT", "summary": f"Approved via Gmail label by {actor_email}",
                            "context": via_label_ctx},
                           "Record approval via label"),
                    Action("send_override_window", "DET", {}, "Open override window"),
                ],
            )

        if intent == "reject_invoice":
            return Plan(
                event_type="label_changed",
                box_id=box_id,
                actions=[
                    Action("clear_waiting_condition", "DET", {}, "Clear waiting condition"),
                    Action("move_box_stage", "DET", {"target": "exception"}, "Move to exception"),
                    Action("apply_label", "DET", {"label": "Clearledgr/Invoice/Exception"},
                           "Apply Exception label"),
                    Action("post_timeline_entry", "DET",
                           {"summary": f"Rejected via Gmail label by {actor_email}",
                            "context": via_label_ctx},
                           "Record rejection via label"),
                ],
            )

        if intent == "needs_info":
            return Plan(
                event_type="label_changed",
                box_id=box_id,
                actions=[
                    Action("move_box_stage", "DET", {"target": "needs_info"}, "Move to needs_info"),
                    Action("apply_label", "DET",
                           {"label": label_name or "Clearledgr/Review Required"},
                           "Apply Review Required label"),
                    Action("post_timeline_entry", "DET",
                           {"summary": f"Flagged for review via Gmail label by {actor_email}",
                            "context": via_label_ctx},
                           "Record review flag via label"),
                ],
            )

        # Unknown intent — return empty plan so the webhook can log and move on.
        return Plan(event_type="label_changed", actions=[], box_id=box_id)

    def _plan_manual_classification(self, event: AgentEvent, box_state: dict) -> Plan:
        """AP Manager manually classifies an email."""
        classification = event.payload.get("classification", "invoice")
        if classification == "invoice":
            # Re-enter the invoice plan from step 3 (already classified)
            return Plan(
                event_type="manual_classification",
                actions=[
                    Action("apply_label", "DET",
                           {"label": "Clearledgr/Invoice/Received"},
                           "Apply Received label after manual classification"),
                    Action("create_box", "DET",
                           {"pipeline": "ap_invoices"},
                           "Create AP item"),
                    Action("extract_invoice_fields", "LLM", {},
                           "Extract structured fields"),
                    Action("run_extraction_guardrails", "DET", {},
                           "Apply extraction guardrails"),
                    Action("update_box_fields", "DET", {},
                           "Persist fields to Box"),
                ],
            )
        else:
            return Plan(
                event_type="manual_classification",
                actions=[
                    Action("apply_label", "DET",
                           {"label": f"Clearledgr/{classification.replace('_', ' ').title()}"},
                           f"Apply {classification} label"),
                ],
            )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[DeterministicPlanningEngine] = None


def get_planning_engine(db: Any = None) -> DeterministicPlanningEngine:
    global _engine
    if _engine is None:
        _engine = DeterministicPlanningEngine(db)
    return _engine
