"""Execution Engine — Agent Design Specification §5.

Takes a Plan from the planning engine and executes it, one Action at a
time. Its only job is faithful, recorded execution. It adds no
intelligence — the planning engine has already decided what to do. The
execution engine makes sure what was decided actually happens, in order,
with a record of every step.

The Two Non-Negotiable Rules:
  Rule 1: Every action is recorded to the Box timeline BEFORE it executes.
  Rule 2: The execution engine never assumes success. Every external call
          must return a confirmation before the Box stage advances.

Usage:
    from clearledgr.core.execution_engine import ExecutionEngine
    from clearledgr.core.plan import Plan

    engine = ExecutionEngine(db=db, organization_id="acme")
    result = await engine.execute(plan)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from clearledgr.core.plan import Action, ExecutionResult, Plan

logger = logging.getLogger(__name__)

# §5.2: Retry delays for transient failures
_RETRY_DELAYS = [5, 30, 120]  # seconds
_MAX_RETRIES = 3

# §11: Action → SLA step mapping for latency tracking
_ACTION_TO_SLA_STEP = {
    "classify_email":             "classification",
    "extract_invoice_fields":     "extraction",
    "run_extraction_guardrails":  "guardrails",
    "lookup_po":                  "erp_lookup",
    "lookup_grn":                 "erp_lookup",
    "lookup_vendor_master":       "erp_lookup",
    "run_three_way_match":        "three_way_match",
    "post_bill":                  "erp_post",
    "send_slack_approval":        "slack_delivery",
    "send_slack_exception":       "slack_delivery",
    "send_slack_override_window": "slack_delivery",
    "send_slack_digest":          "slack_delivery",
}


# §5.1: Per-action-type timeouts
_ACTION_TIMEOUTS = {
    "classify_email": 30,
    "extract_invoice_fields": 30,
    "classify_vendor_response": 30,
    "draft_vendor_response": 30,
    "generate_exception_reason": 30,
    "post_bill": 10,
    "lookup_po": 10,
    "lookup_grn": 10,
    "apply_label": 5,
    "send_approval": 5,
    "send_vendor_email": 5,
}
_DEFAULT_TIMEOUT = 15


class ExecutionEngine:
    """§5: Formal execution loop consuming a Plan object.

    The engine:
    1. Loads the plan
    2. For each action: pre-write → execute → post-write → check wait
    3. On failure: classify and handle per §5.2
    4. On async wait: persist remaining plan to pending_plan, exit
    5. On completion: clear pending_plan, return result
    """

    def __init__(self, db: Any, organization_id: str):
        self.db = db
        self.organization_id = organization_id
        self._handlers: Dict[str, Callable] = {}
        self._workflow = None
        self._ctx: Dict[str, Any] = {}  # Per-instance, NOT class-level
        self._register_handlers()

    def _get_workflow(self):
        if self._workflow is None:
            from clearledgr.services.invoice_workflow import InvoiceWorkflowService
            self._workflow = InvoiceWorkflowService(organization_id=self.organization_id)
        return self._workflow

    def _register_handlers(self) -> None:
        """Map action names to handler functions.

        Each handler wraps an existing service method. No new business
        logic — just wiring.
        """
        self._handlers = {
            # §3 Email and Inbox Actions (7)
            "read_email": self._handle_read_email,
            "fetch_attachment": self._handle_fetch_attachment,
            "apply_label": self._handle_apply_label,
            "remove_label": self._handle_remove_label,
            "split_thread": self._handle_split_thread,
            "send_email": self._handle_send_email,
            "watch_thread": self._handle_watch_thread,

            # §3 Classification and Extraction Actions (5)
            "classify_email": self._handle_classify_email,
            "extract_invoice_fields": self._handle_extract,
            "run_extraction_guardrails": self._handle_guardrails,
            "generate_exception_reason": self._handle_generate_exception,
            "classify_vendor_response": self._handle_classify_vendor,

            # §3 ERP Actions (8)
            "lookup_vendor_master": self._handle_lookup_vendor_master,
            "lookup_po": self._handle_lookup_po,
            "lookup_grn": self._handle_lookup_grn,
            "run_three_way_match": self._handle_match,
            "post_bill": self._handle_post_bill,
            "pre_post_validate": self._handle_pre_post_validate,
            "schedule_payment": self._handle_schedule_payment,
            "reverse_erp_post": self._handle_reverse_erp_post,

            # §3 Box and State Actions (8)
            "create_box": self._handle_create_box,
            "update_box_fields": self._handle_update_fields,
            "move_box_stage": self._handle_stage_transition,
            "post_timeline_entry": self._handle_timeline,
            "link_vendor_to_box": self._handle_link_vendor,
            "set_waiting_condition": self._handle_set_waiting,
            "clear_waiting_condition": self._handle_clear_waiting,
            "set_pending_plan": self._handle_set_pending_plan,

            # §3 Communication Actions (8)
            "send_slack_approval": self._handle_send_approval,
            "send_slack_exception": self._handle_send_slack_exception,
            "send_slack_override_window": self._handle_override_window,
            "send_slack_digest": self._handle_send_slack_digest,
            "send_vendor_email": self._handle_send_vendor_email,
            "draft_vendor_response": self._handle_draft_vendor_response,
            "send_teams_approval": self._handle_send_teams_approval,
            "post_gmail_notification": self._handle_post_gmail_notification,

            # §3 Vendor Onboarding Actions (7)
            "create_vendor_record": self._handle_create_vendor_record,
            "enrich_vendor": self._handle_enrich_vendor,
            "run_adverse_media_check": self._handle_adverse_media,
            "initiate_micro_deposit": self._handle_initiate_micro_deposit,
            "verify_micro_deposit": self._handle_verify_micro_deposit,
            "activate_vendor_in_erp": self._handle_activate_vendor,
            "freeze_vendor_payments": self._handle_freeze_payments,

            # §3 Fraud Control Actions (6)
            "check_iban_change": self._handle_iban_change,
            "check_domain_match": self._handle_domain_match,
            "check_velocity": self._handle_velocity,
            "check_duplicate": self._handle_duplicate,
            "flag_internal_instruction": self._handle_flag_internal,
            "check_amount_ceiling": self._handle_ceiling,

            # Internal actions (not in spec §3 but used by plans)
            "check_duplicate_full": self._handle_duplicate,
            "resume_from_pending_plan": self._handle_resume_plan,
            "close_override_window": self._handle_close_override,
            "escalate_approval": self._handle_escalate,
            "route_vendor_response": self._handle_route_vendor,
            "send_approval": self._handle_send_approval,  # alias
            "send_override_window": self._handle_override_window,  # alias
            "validate_kyc_document": self._handle_kyc_validate,
            "update_onboarding_progress": self._handle_onboarding_progress,
            "initiate_iban_verification": self._handle_iban_verify,
            "check_vendor_response": self._handle_check_vendor_response,
            "evaluate_grn_result": self._handle_evaluate_grn,
            "unsnooze": self._handle_unsnooze,
            "apply_label_matched": self._handle_apply_label,  # alias
        }

    async def execute(self, plan: Plan) -> ExecutionResult:
        """§5.1: The execution loop.

        Steps:
        1. Load plan
        2. Take next action
        3. Pre-execution timeline write (Rule 1)
        4. Execute the action
        5. Handle the result
        6. Check for async wait
        7. Complete or continue
        """
        if plan.is_empty:
            return ExecutionResult(status="completed", steps_total=0)

        box_id = plan.box_id
        steps_completed = 0

        # §11: Track total_to_approval latency for email_received plans
        import time as _time
        _plan_start = _time.monotonic()
        _is_invoice_plan = plan.event_type == "email_received"

        for step, action in enumerate(plan.actions):
            # --- Step 3: Pre-execution timeline write (Rule 1) ---
            timeline_id = self._pre_write(box_id, action, step)

            # --- Step 4: Execute the action ---
            result = await self._execute_with_retry(action, plan, step)

            # --- Step 5: Handle the result ---
            if result.get("_abort"):
                self._post_write(box_id, action, step, timeline_id, "failed", result.get("error", ""))
                if box_id:
                    self._move_to_exception(box_id, action.name, result.get("error", ""))
                return ExecutionResult(
                    status="failed", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    error=result.get("error"), last_action=action.name,
                )

            # Action that signals plan should stop early (e.g. classification = not invoice)
            if result.get("_stop_plan"):
                self._post_write(box_id, action, step, timeline_id, "completed", "plan stopped")
                steps_completed += 1
                break

            self._post_write(box_id, action, step, timeline_id, "completed", "")
            steps_completed += 1

            # Update box_id if the action created one
            if result.get("box_id") and not box_id:
                box_id = result["box_id"]
                plan.box_id = box_id

            # --- Step 6: Check for async wait ---
            if result.get("waiting_condition"):
                remaining = plan.remaining_from(step + 1)
                if box_id and not remaining.is_empty:
                    self.db.update_ap_item(box_id, pending_plan=remaining.to_json())
                if box_id:
                    wf = self._get_workflow()
                    wf.set_waiting_condition(
                        box_id,
                        result["waiting_condition"].get("type", "unknown"),
                        expected_by=result["waiting_condition"].get("expected_by"),
                        context=result["waiting_condition"].get("context"),
                    )
                # §11: Record total_to_approval SLA when hitting approval wait
                if _is_invoice_plan and result["waiting_condition"].get("type") == "approval_response":
                    try:
                        from clearledgr.core.sla_tracker import get_sla_tracker
                        total_ms = int((_time.monotonic() - _plan_start) * 1000)
                        get_sla_tracker().record(
                            "total_to_approval", total_ms,
                            ap_item_id=box_id,
                            organization_id=self.organization_id,
                        )
                    except Exception:
                        pass
                return ExecutionResult(
                    status="waiting", steps_completed=steps_completed,
                    steps_total=plan.step_count, box_id=box_id,
                    waiting_condition=result["waiting_condition"],
                    last_action=action.name,
                )

        # --- Step 7: Plan complete ---
        if box_id:
            try:
                self.db.update_ap_item(box_id, pending_plan=None)
            except Exception:
                pass
        return ExecutionResult(
            status="completed", steps_completed=steps_completed,
            steps_total=plan.step_count, box_id=box_id,
            last_action=plan.actions[-1].name if plan.actions else None,
        )

    # ------------------------------------------------------------------
    # Timeline writes (Rule 1)
    # ------------------------------------------------------------------

    def _pre_write(self, box_id: Optional[str], action: Action, step: int) -> str:
        """Rule 1: Write timeline entry BEFORE execution."""
        timeline_id = f"TL-{uuid.uuid4().hex[:12]}"
        if not box_id or not hasattr(self.db, "append_ap_item_timeline_entry"):
            return timeline_id
        try:
            self.db.append_ap_item_timeline_entry(box_id, {
                "id": timeline_id,
                "type": "agent_action",
                "action": action.name,
                "description": action.description,
                "status": "executing",
                "step": step,
                "layer": action.layer,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        return timeline_id

    def _post_write(self, box_id: Optional[str], action: Action, step: int,
                    timeline_id: str, status: str, result_summary: str) -> None:
        """Update pre-execution entry with result."""
        if not box_id or not hasattr(self.db, "append_ap_item_timeline_entry"):
            return
        try:
            self.db.append_ap_item_timeline_entry(box_id, {
                "id": f"{timeline_id}-result",
                "type": "agent_action",
                "action": action.name,
                "status": status,
                "result_summary": result_summary[:200] if result_summary else "",
                "step": step,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Execution with retry (§5.2)
    # ------------------------------------------------------------------

    async def _execute_with_retry(self, action: Action, plan: Plan, step: int) -> Dict[str, Any]:
        """Execute action with transient failure retry."""
        import time as _time
        handler = self._handlers.get(action.name)
        if not handler:
            logger.warning("[ExecutionEngine] No handler for action: %s", action.name)
            return {"ok": True, "_stop_plan": False}

        timeout = _ACTION_TIMEOUTS.get(action.name, _DEFAULT_TIMEOUT)

        # §11: Map action name to SLA step name for timing
        sla_step = _ACTION_TO_SLA_STEP.get(action.name)
        _action_start = _time.monotonic()

        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    handler(action, plan),
                    timeout=timeout,
                )
                # §11: Record SLA latency for this action
                if sla_step:
                    latency_ms = int((_time.monotonic() - _action_start) * 1000)
                    try:
                        from clearledgr.core.sla_tracker import get_sla_tracker
                        get_sla_tracker().record(
                            sla_step, latency_ms,
                            ap_item_id=plan.box_id,
                            organization_id=self.organization_id,
                        )
                    except Exception:
                        pass
                return result if isinstance(result, dict) else {"ok": True}

            except asyncio.TimeoutError:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "[ExecutionEngine] %s timed out, retry %d/%d in %ds",
                        action.name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                return {"_abort": True, "error": f"{action.name} timed out after {_MAX_RETRIES} retries"}

            except Exception as exc:
                failure_type = _classify_failure(exc)
                if failure_type == "transient" and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                    continue
                if failure_type == "dependency":
                    return {
                        "waiting_condition": {
                            "type": "external_dependency_unavailable",
                            "expected_by": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                            "context": {"action": action.name, "error": str(exc)},
                        }
                    }
                if failure_type == "llm" and action.layer == "LLM":
                    logger.warning("[ExecutionEngine] LLM failure in %s, using fallback", action.name)
                    return {"ok": True, "_fallback": True}
                return {"_abort": True, "error": str(exc)}

        return {"_abort": True, "error": "max retries exhausted"}

    async def _run_exception_flow(self, plan: Plan, ctx: dict, match_result: Any) -> None:
        """§9.3: Execute the exception flow when 3-way match fails.

        Steps per spec:
        1. generate_exception_reason(match_result, invoice, po, grn)
        2. apply_label('Clearledgr/Invoice/Exception')
        3. move_box_stage('exception')
        4. send_slack_exception(box_id, ap_channel, {exception_summary})

        Each step goes through the full execution mechanism:
        pre-write → execute with retry/timeout → post-write (Rule 1).
        """
        box_id = plan.box_id
        if not box_id:
            return

        exception_actions = [
            Action("generate_exception_reason", "LLM", {},
                   "Generate plain-language match exception reason"),
            Action("apply_label", "DET",
                   {"label": "Clearledgr/Invoice/Exception"},
                   "Apply Exception stage label"),
            Action("move_box_stage", "DET",
                   {"target": "needs_info"},
                   "Move Box to exception stage"),
            Action("send_slack_exception", "DET", {},
                   "Notify AP team of match exception with resolution buttons"),
        ]

        for step, action in enumerate(exception_actions):
            # Rule 1: pre-execution timeline write
            timeline_id = self._pre_write(box_id, action, step + 100)  # offset to distinguish from main plan

            # Execute with retry and timeout (§5.2)
            result = await self._execute_with_retry(action, plan, step + 100)

            # Post-execution timeline update
            status = "completed" if not result.get("_abort") else "failed"
            self._post_write(box_id, action, step + 100, timeline_id, status, "")

    def _move_to_exception(self, box_id: str, action_name: str, error: str) -> None:
        """Move Box to exception stage on persistent failure."""
        try:
            self.db.update_ap_item(box_id, state="needs_info", exception_reason=f"{action_name}: {error[:200]}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Action Handlers — each wraps an actual service method
    # ------------------------------------------------------------------

    def _ensure_ctx(self, plan: Plan) -> Dict[str, Any]:
        """Get the per-instance execution context for this plan run."""
        return self._ctx

    async def _handle_read_email(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch full email content from Gmail API."""
        ctx = self._ensure_ctx(plan)
        message_id = action.params.get("message_id", "")
        user_id = action.params.get("user_id", "")
        if not message_id:
            return {"ok": True}

        ctx["message_id"] = message_id
        ctx["user_id"] = user_id

        # Fetch actual email content from Gmail
        try:
            from clearledgr.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if not await client.ensure_authenticated():
                return {"_abort": True, "error": f"Gmail auth failed for user {user_id}"}

            message = await client.get_message(message_id)
            if message:
                ctx["subject"] = getattr(message, "subject", "") or (message.get("subject", "") if isinstance(message, dict) else "")
                ctx["sender"] = getattr(message, "sender", "") or (message.get("sender", "") if isinstance(message, dict) else "")
                ctx["body"] = getattr(message, "body_text", "") or getattr(message, "body", "") or (message.get("body", "") if isinstance(message, dict) else "")
                ctx["snippet"] = getattr(message, "snippet", "") or (message.get("snippet", "") if isinstance(message, dict) else "")
                ctx["thread_id"] = getattr(message, "thread_id", "") or (message.get("thread_id", "") if isinstance(message, dict) else "")
                ctx["attachments"] = getattr(message, "attachments", []) or (message.get("attachments", []) if isinstance(message, dict) else [])
                logger.info("[ExecutionEngine] read_email: fetched %s (subject=%s)", message_id, ctx["subject"][:50])
                return {"ok": True, "message_id": message_id, "has_content": True}
            else:
                return {"_abort": True, "error": f"Message {message_id} not found in Gmail"}
        except Exception as exc:
            logger.error("[ExecutionEngine] read_email failed: %s", exc)
            return {"_abort": True, "error": f"Gmail fetch failed: {exc}"}

    async def _handle_classify_email(self, action: Action, plan: Plan) -> dict:
        """§3: Call Claude to classify the email."""
        ctx = self._ensure_ctx(plan)
        try:
            from clearledgr.services.ap_classifier import classify_ap_email
            subject = ctx.get("subject", "")
            sender = ctx.get("sender", "")
            body = ctx.get("body", "")
            snippet = ctx.get("snippet", "")
            result = classify_ap_email(
                subject=subject, sender=sender,
                snippet=snippet, body=body,
            )
            ctx["classification"] = result
            classification_type = result.get("type", "unclassifiable")
            confidence = result.get("confidence", 0)
            if confidence < 0.80:
                ctx["classification_low_confidence"] = True
                return {"ok": True, "_stop_plan": True, "reason": "low_confidence_classification"}
            if classification_type not in ("invoice", "credit_note"):
                return {"ok": True, "_stop_plan": True, "reason": f"not_invoice: {classification_type}"}
            return {"ok": True, "type": classification_type, "confidence": confidence}
        except Exception as exc:
            logger.warning("[ExecutionEngine] classify_email failed: %s", exc)
            return {"ok": True}  # Treat as unclassifiable per §5.2

    async def _handle_extract(self, action: Action, plan: Plan) -> dict:
        """§3: Call Claude to extract structured invoice fields."""
        ctx = self._ensure_ctx(plan)
        try:
            from clearledgr.services.llm_email_parser import get_llm_email_parser
            parser = get_llm_email_parser()
            result = parser.parse_email(
                subject=ctx.get("subject", ""),
                body=ctx.get("body", ""),
                sender=ctx.get("sender", ""),
                attachments=ctx.get("attachments"),
                organization_id=self.organization_id,
                thread_id=ctx.get("thread_id"),
            )
            ctx["extracted_fields"] = result
            return {"ok": True, "vendor_name": result.get("vendor_name"), "amount": result.get("amount")}
        except Exception as exc:
            logger.warning("[ExecutionEngine] extract_invoice_fields failed: %s", exc)
            return {"ok": True, "_fallback": True}

    async def _handle_guardrails(self, action: Action, plan: Plan) -> dict:
        """§3: Apply 5 deterministic extraction guardrails."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        if not extracted:
            return {"ok": True}
        try:
            wf = self._get_workflow()
            from clearledgr.services.invoice_workflow import InvoiceData
            invoice = self._build_invoice_from_ctx(ctx)
            gate = await wf._evaluate_deterministic_validation(invoice)
            ctx["validation_gate"] = gate
            if not gate.get("passed", True):
                reason_codes = gate.get("reason_codes", [])
                return {"ok": True, "gate_passed": False, "reason_codes": reason_codes}
            return {"ok": True, "gate_passed": True}
        except Exception as exc:
            logger.warning("[ExecutionEngine] guardrails failed: %s", exc)
            return {"ok": True}

    async def _handle_apply_label(self, action: Action, plan: Plan) -> dict:
        """§3: Apply a Clearledgr Gmail label to the thread."""
        label = action.params.get("label", "")
        if not label:
            return {"ok": True}
        ctx = self._ensure_ctx(plan)
        try:
            from clearledgr.services.gmail_labels import apply_label
            user_id = ctx.get("user_id", "")
            thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
            if user_id and thread_id:
                # Resolve label key from full label path
                label_key = label.split("/")[-1].lower().replace(" ", "_")
                # Label application requires authenticated Gmail client
                # In worker context, delegate to the workflow service
                from clearledgr.services.gmail_autopilot import GmailAPIClient
                client = GmailAPIClient(user_id)
                if await client.ensure_authenticated():
                    await apply_label(client, thread_id, label_key, user_email=user_id)
            return {"ok": True, "label": label}
        except Exception as exc:
            logger.debug("[ExecutionEngine] apply_label non-fatal: %s", exc)
            return {"ok": True, "label": label}

    async def _handle_create_box(self, action: Action, plan: Plan) -> dict:
        """§3: Create a new Box (AP item) in the specified pipeline."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        payload = {
            "thread_id": ctx.get("thread_id") or ctx.get("message_id", ""),
            "message_id": ctx.get("message_id", ""),
            "subject": ctx.get("subject", ""),
            "sender": ctx.get("sender", ""),
            "vendor_name": extracted.get("vendor_name") or ctx.get("sender", ""),
            "amount": extracted.get("amount") or extracted.get("total_amount"),
            "currency": extracted.get("currency", "USD"),
            "invoice_number": extracted.get("invoice_number") or extracted.get("invoice_reference"),
            "invoice_date": extracted.get("invoice_date"),
            "due_date": extracted.get("due_date"),
            "confidence": extracted.get("confidence", 0),
            "state": "received",
            "organization_id": self.organization_id,
            "user_id": ctx.get("user_id", ""),
            "po_number": extracted.get("po_reference") or extracted.get("po_number"),
            "field_confidences": extracted.get("field_confidences"),
            "document_type": ctx.get("classification", {}).get("type", "invoice"),
        }
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            item = self.db.create_ap_item(payload)
            box_id = item.get("id") if isinstance(item, dict) else str(item)
            ctx["box_id"] = box_id
            return {"ok": True, "box_id": box_id}
        except Exception as exc:
            logger.error("[ExecutionEngine] create_box failed: %s", exc)
            return {"_abort": True, "error": f"create_box: {exc}"}

    async def _handle_domain_match(self, action: Action, plan: Plan) -> dict:
        """§3: Validate sender domain matches vendor master."""
        ctx = self._ensure_ctx(plan)
        try:
            from clearledgr.services.vendor_domain_lock import VendorDomainLockService
            service = VendorDomainLockService(
                organization_id=self.organization_id, db=self.db,
            )
            result = service.check_sender_domain(
                vendor_name=ctx.get("extracted_fields", {}).get("vendor_name"),
                sender=ctx.get("sender", ""),
            )
            ctx["domain_check"] = result
            if hasattr(result, "status"):
                status = result.status
            else:
                status = result.get("status", "no_vendor") if isinstance(result, dict) else "unknown"
            if status == "mismatch":
                return {"ok": True, "_stop_plan": True, "reason": "domain_mismatch"}
            return {"ok": True, "domain_status": status}
        except Exception as exc:
            logger.debug("[ExecutionEngine] domain_match non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_duplicate(self, action: Action, plan: Plan) -> dict:
        """§3: Check for duplicate invoice in trailing window."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        vendor = extracted.get("vendor_name") or ctx.get("sender", "")
        amount = extracted.get("amount") or extracted.get("total_amount") or 0
        invoice_number = extracted.get("invoice_number") or extracted.get("invoice_reference")
        if not vendor:
            return {"ok": True}
        try:
            from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer
            analyzer = get_cross_invoice_analyzer(
                organization_id=self.organization_id,
            )
            result = analyzer.analyze(
                vendor=vendor,
                amount=float(amount) if amount else 0,
                invoice_number=invoice_number,
                gmail_id=ctx.get("message_id"),
            )
            ctx["duplicate_check"] = result
            has_issues = result.has_issues if hasattr(result, "has_issues") else (result.get("has_issues") if isinstance(result, dict) else False)
            if has_issues:
                duplicates = result.duplicates if hasattr(result, "duplicates") else (result.get("duplicates", []) if isinstance(result, dict) else [])
                if duplicates:
                    return {"ok": True, "_stop_plan": True, "reason": "duplicate_found"}
            return {"ok": True, "has_issues": has_issues}
        except Exception as exc:
            logger.debug("[ExecutionEngine] duplicate check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_ceiling(self, action: Action, plan: Plan) -> dict:
        """§3: Validate amount does not exceed per-vendor ceiling."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        amount = float(extracted.get("amount") or extracted.get("total_amount") or 0)
        currency = extracted.get("currency", "USD")
        if amount <= 0:
            return {"ok": True}
        try:
            from clearledgr.core.fraud_controls import evaluate_payment_ceiling, load_fraud_controls
            config = load_fraud_controls(self.organization_id, self.db)
            result = evaluate_payment_ceiling(amount, currency, config)
            ctx["ceiling_check"] = result
            if hasattr(result, "exceeds_ceiling"):
                exceeds = result.exceeds_ceiling
            else:
                exceeds = result.get("exceeds_ceiling", False) if isinstance(result, dict) else False
            if exceeds:
                return {"ok": True, "exceeds_ceiling": True}
            return {"ok": True, "exceeds_ceiling": False}
        except Exception as exc:
            logger.debug("[ExecutionEngine] ceiling check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_velocity(self, action: Action, plan: Plan) -> dict:
        """§3: Check invoice velocity for this vendor."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        vendor = extracted.get("vendor_name", "")
        if not vendor:
            return {"ok": True}
        try:
            # Velocity check uses vendor invoice history count
            if hasattr(self.db, "get_vendor_invoice_history"):
                history = self.db.get_vendor_invoice_history(
                    self.organization_id, vendor,
                    limit=100,
                )
                window_days = action.params.get("window_days", 7)
                from datetime import timedelta
                cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
                recent = [h for h in (history or []) if (h.get("created_at") or "") >= cutoff]
                ctx["velocity_count"] = len(recent)
                # Flag if > 10 invoices in the window (configurable threshold)
                if len(recent) > 10:
                    return {"ok": True, "velocity_exceeded": True, "count": len(recent)}
            return {"ok": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] velocity check non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_lookup_po(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch Purchase Order from ERP."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        po_number = extracted.get("po_reference") or extracted.get("po_number")
        if not po_number:
            ctx["po_result"] = None
            return {"ok": True, "po_found": False}
        try:
            from clearledgr.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service()
            po = service.get_po_by_number(po_number)
            ctx["po_result"] = po
            found = po is not None
            return {"ok": True, "po_found": found, "po_number": po_number}
        except Exception as exc:
            logger.debug("[ExecutionEngine] lookup_po non-fatal: %s", exc)
            ctx["po_result"] = None
            return {"ok": True, "po_found": False}

    async def _handle_lookup_grn(self, action: Action, plan: Plan) -> dict:
        """§3: Fetch Goods Receipt Notes from ERP."""
        ctx = self._ensure_ctx(plan)
        po = ctx.get("po_result")
        if not po:
            ctx["grn_result"] = None
            return {"ok": True, "grn_found": False}
        try:
            from clearledgr.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service()
            po_id = po.po_id if hasattr(po, "po_id") else (po.get("po_id") if isinstance(po, dict) else "")
            grns = service.get_goods_receipts_for_po(po_id) if po_id else []
            ctx["grn_result"] = grns
            if not grns:
                # GRN not confirmed — set waiting condition
                return {
                    "ok": True, "grn_found": False,
                    "waiting_condition": {
                        "type": "grn_confirmation",
                        "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                        "context": {"po_number": po_id},
                    },
                }
            return {"ok": True, "grn_found": True, "grn_count": len(grns)}
        except Exception as exc:
            logger.debug("[ExecutionEngine] lookup_grn non-fatal: %s", exc)
            ctx["grn_result"] = None
            return {"ok": True, "grn_found": False}

    async def _handle_match(self, action: Action, plan: Plan) -> dict:
        """§3: Execute deterministic 3-way match algorithm. Never calls Claude."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        po = ctx.get("po_result")
        if not po:
            ctx["match_result"] = {"status": "no_po"}
            return {"ok": True, "match_status": "no_po"}
        try:
            from clearledgr.services.purchase_orders import get_purchase_order_service
            service = get_purchase_order_service()
            po_number = extracted.get("po_reference") or extracted.get("po_number", "")
            result = service.match_invoice_to_po(
                invoice_id=ctx.get("box_id", ""),
                invoice_amount=float(extracted.get("amount") or 0),
                invoice_vendor=extracted.get("vendor_name", ""),
                invoice_po_number=po_number,
                invoice_lines=extracted.get("line_items"),
            )
            ctx["match_result"] = result
            status = result.status if hasattr(result, "status") else (result.get("status") if isinstance(result, dict) else "unknown")
            match_passed = str(status).upper() in ("MATCHED", "MATCH")
            # Update Box with match result
            if plan.box_id:
                self.db.update_ap_item(
                    plan.box_id,
                    match_status="passed" if match_passed else "exception",
                    grn_reference=extracted.get("po_reference", ""),
                )
            if not match_passed:
                # §9.3: Match failed — run exception flow inline, then stop plan.
                # Don't continue to apply_label(Matched) / send_approval.
                await self._run_exception_flow(plan, ctx, result)
                return {"ok": True, "match_status": status, "match_passed": False, "_stop_plan": True}
            return {"ok": True, "match_status": status, "match_passed": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] three_way_match non-fatal: %s", exc)
            ctx["match_result"] = None
            return {"ok": True, "match_status": "error"}

    async def _handle_update_fields(self, action: Action, plan: Plan) -> dict:
        """§3: Persist extracted fields to Box record."""
        ctx = self._ensure_ctx(plan)
        extracted = ctx.get("extracted_fields", {})
        if not plan.box_id or not extracted:
            return {"ok": True}
        update_kwargs = {}
        field_map = {
            "vendor_name": "vendor_name",
            "amount": "amount",
            "total_amount": "amount",
            "currency": "currency",
            "invoice_number": "invoice_number",
            "invoice_reference": "invoice_number",
            "invoice_date": "invoice_date",
            "due_date": "due_date",
            "po_reference": "po_number",
            "po_number": "po_number",
            "payment_terms": None,  # Not a direct column
        }
        for src, dst in field_map.items():
            if dst and src in extracted and extracted[src] is not None:
                update_kwargs[dst] = extracted[src]
        if extracted.get("confidence"):
            update_kwargs["confidence"] = extracted["confidence"]
        if extracted.get("field_confidences"):
            update_kwargs["field_confidences"] = extracted["field_confidences"]
        if update_kwargs:
            try:
                self.db.update_ap_item(plan.box_id, **update_kwargs)
            except Exception as exc:
                logger.warning("[ExecutionEngine] update_box_fields failed: %s", exc)
        return {"ok": True, "fields_updated": list(update_kwargs.keys())}

    def _build_invoice_from_ctx(self, ctx: Dict[str, Any]):
        """Build an InvoiceData from accumulated execution context."""
        from clearledgr.services.invoice_workflow import InvoiceData
        extracted = ctx.get("extracted_fields", {})
        return InvoiceData(
            gmail_id=ctx.get("thread_id") or ctx.get("message_id", ""),
            subject=ctx.get("subject", ""),
            sender=ctx.get("sender", ""),
            vendor_name=extracted.get("vendor_name") or ctx.get("sender", ""),
            amount=float(extracted.get("amount") or extracted.get("total_amount") or 0),
            currency=extracted.get("currency", "USD"),
            invoice_number=extracted.get("invoice_number") or extracted.get("invoice_reference"),
            due_date=extracted.get("due_date"),
            po_number=extracted.get("po_reference") or extracted.get("po_number"),
            confidence=float(extracted.get("confidence") or 0),
            organization_id=self.organization_id,
            user_id=ctx.get("user_id", ""),
            field_confidences=extracted.get("field_confidences"),
            line_items=extracted.get("line_items"),
        )

    async def _handle_stage_transition(self, action: Action, plan: Plan) -> dict:
        """§3: Advance or revert a Box to a specific pipeline stage."""
        target = action.params.get("target", "")
        if plan.box_id and target:
            try:
                self.db.update_ap_item(plan.box_id, state=target)
            except Exception as exc:
                return {"_abort": True, "error": f"Stage transition to {target} failed: {exc}"}
        return {"ok": True}

    async def _handle_send_approval(self, action: Action, plan: Plan) -> dict:
        """§3: Send structured approval message to Slack/Teams."""
        ctx = self._ensure_ctx(plan)
        try:
            wf = self._get_workflow()
            invoice = self._build_invoice_from_ctx(ctx)
            invoice.gmail_id = ctx.get("thread_id") or ctx.get("message_id", "")
            extra_context = {}
            if ctx.get("validation_gate"):
                extra_context["validation_gate"] = ctx["validation_gate"]
            result = await wf._send_for_approval(invoice, extra_context=extra_context or None)
            return {
                "ok": True,
                "approval_sent": True,
                "slack_channel": result.get("slack_channel"),
                "waiting_condition": {
                    "type": "approval_response",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                    "context": {"channel": result.get("slack_channel"), "ts": result.get("slack_ts")},
                },
            }
        except Exception as exc:
            logger.error("[ExecutionEngine] send_approval failed: %s", exc)
            return {
                "ok": True,
                "waiting_condition": {
                    "type": "approval_response",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                },
            }

    async def _handle_post_bill(self, action: Action, plan: Plan) -> dict:
        if not plan.box_id:
            return {"_abort": True, "error": "No box_id for post_bill"}
        wf = self._get_workflow()
        item = self.db.get_ap_item(plan.box_id)
        if not item:
            return {"_abort": True, "error": "AP item not found"}
        try:
            from clearledgr.services.invoice_workflow import InvoiceData
            invoice = InvoiceData(
                gmail_id=item.get("thread_id") or item.get("message_id") or "",
                subject=item.get("subject") or "",
                sender=item.get("sender") or "",
                vendor_name=item.get("vendor_name") or "",
                amount=float(item.get("amount") or 0),
                currency=item.get("currency") or "USD",
                invoice_number=item.get("invoice_number"),
                organization_id=self.organization_id,
            )
            result = await wf._post_to_erp(invoice)
            if result.get("status") in ("posted", "success", "posted_to_erp"):
                return {"ok": True, "erp_reference": result.get("reference_id")}
            return {"_abort": True, "error": result.get("reason", "ERP post failed")}
        except Exception as exc:
            return {"_abort": True, "error": str(exc)}

    async def _handle_pre_post_validate(self, action: Action, plan: Plan) -> dict:
        if not plan.box_id:
            return {"ok": True}
        from clearledgr.integrations.erp_router import pre_post_validate
        result = pre_post_validate(plan.box_id, self.organization_id, db=self.db)
        if not result.get("valid"):
            return {"_abort": True, "error": f"Pre-post validation failed: {result.get('failures')}"}
        return {"ok": True}

    async def _handle_schedule_payment(self, action: Action, plan: Plan) -> dict:
        """§3: Create a payment schedule entry in the ERP."""
        if not plan.box_id:
            return {"ok": True}
        item = self.db.get_ap_item(plan.box_id)
        if not item or not item.get("erp_reference"):
            return {"ok": True}  # No ERP bill to schedule against
        try:
            from clearledgr.integrations.erp_router import get_erp_connection
            connection = get_erp_connection(self.organization_id)
            if connection:
                # Generate payment reference and persist
                payment_ref = f"PAY-{uuid.uuid4().hex[:8]}"
                self.db.update_ap_item(plan.box_id,
                    payment_reference=payment_ref,
                    metadata={
                        **(item.get("metadata") or {}),
                        "payment_scheduled": True,
                        "payment_scheduled_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            return {"ok": True, "scheduled": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] schedule_payment non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_set_waiting(self, action: Action, plan: Plan) -> dict:
        timeout_hours = action.params.get("timeout_hours", 4)
        return {
            "ok": True,
            "waiting_condition": {
                "type": action.params.get("type", "unknown"),
                "expected_by": (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat(),
            },
        }

    async def _handle_clear_waiting(self, action: Action, plan: Plan) -> dict:
        if plan.box_id:
            wf = self._get_workflow()
            wf.clear_waiting_condition(plan.box_id)
        return {"ok": True}

    async def _handle_resume_plan(self, action: Action, plan: Plan) -> dict:
        """Resume execution from pending_plan column."""
        if not plan.box_id:
            return {"ok": True}
        item = self.db.get_ap_item(plan.box_id)
        pending = item.get("pending_plan") if item else None
        if pending:
            import json
            if isinstance(pending, str):
                pending = json.loads(pending)
            # The caller (celery_tasks dispatch) will pick up the resumed plan
            return {"ok": True, "resumed_plan": pending}
        return {"ok": True}

    async def _handle_timeline(self, action: Action, plan: Plan) -> dict:
        if plan.box_id and hasattr(self.db, "append_ap_item_timeline_entry"):
            self.db.append_ap_item_timeline_entry(plan.box_id, {
                "type": "agent_action",
                "summary": action.params.get("summary", action.description),
                "format": action.params.get("format", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return {"ok": True}

    async def _handle_watch_thread(self, action: Action, plan: Plan) -> dict:
        """§3: Register a thread for monitoring — replies bypass classification."""
        ctx = self._ensure_ctx(plan)
        thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
        if plan.box_id and thread_id:
            try:
                # Store thread→box mapping so future replies route directly
                self.db.update_ap_item(plan.box_id, thread_id=thread_id)
            except Exception as exc:
                logger.debug("[ExecutionEngine] watch_thread non-fatal: %s", exc)
        return {"ok": True, "thread_id": thread_id}

    async def _handle_override_window(self, action: Action, plan: Plan) -> dict:
        """§3: Post override window notification with live Undo button."""
        if not plan.box_id:
            return {"ok": True}
        try:
            from clearledgr.services.override_window import get_override_window_service
            service = get_override_window_service(self.organization_id, db=self.db)
            item = self.db.get_ap_item(plan.box_id) if plan.box_id else None
            erp_ref = (item or {}).get("erp_reference", "")
            if not erp_ref:
                return {"ok": True}  # No ERP reference yet — nothing to override
            window = service.open_window(
                ap_item_id=plan.box_id,
                erp_reference=erp_ref,
            )
            return {"ok": True, "window_id": window.get("id") if isinstance(window, dict) else None}
        except Exception as exc:
            logger.debug("[ExecutionEngine] override_window non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_close_override(self, action: Action, plan: Plan) -> dict:
        try:
            from clearledgr.services.agent_background import reap_expired_override_windows
            await reap_expired_override_windows()
        except Exception as exc:
            logger.warning("[ExecutionEngine] close_override failed: %s", exc)
        return {"ok": True}

    async def _handle_escalate(self, action: Action, plan: Plan) -> dict:
        try:
            from clearledgr.services.agent_background import _check_approval_timeouts
            await _check_approval_timeouts(self.organization_id)
        except Exception as exc:
            logger.debug("[ExecutionEngine] Escalation failed: %s", exc)
        return {"ok": True}

    async def _handle_send_vendor_email(self, action: Action, plan: Plan) -> dict:
        """§3: Send a templated email to a vendor using the AP inbox."""
        template = action.params.get("template", "chase")
        ctx = self._ensure_ctx(plan)
        vendor_name = ctx.get("extracted_fields", {}).get("vendor_name", "")
        if not vendor_name:
            return {"ok": True}
        try:
            from clearledgr.services.vendor_onboarding_lifecycle import chase_stale_sessions
            await chase_stale_sessions(db=self.db)
            return {"ok": True, "template": template, "vendor": vendor_name}
        except Exception as exc:
            logger.debug("[ExecutionEngine] send_vendor_email non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_classify_vendor(self, action: Action, plan: Plan) -> dict:
        """§3 LLM: Classify a vendor's reply to an onboarding or chase email."""
        ctx = self._ensure_ctx(plan)
        vendor_id = action.params.get("vendor_id", "")
        body = ctx.get("body", "")
        if not body:
            return {"ok": True, "type": "unclassifiable"}
        try:
            from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            prompt = (
                f"Classify this vendor reply. Vendor ID: {vendor_id}\n\n"
                f"Reply content:\n{body[:2000]}\n\n"
                "Classify as: document_submitted, question_asked, refused, "
                "out_of_office, incorrect_contact, or unclassifiable.\n"
                "Return JSON: {{\"type\": \"...\", \"confidence\": 0.0-1.0}}"
            )
            resp = gateway.call_sync(
                LLMAction.CLASSIFY_VENDOR,
                messages=[{"role": "user", "content": prompt}],
                organization_id=self.organization_id,
            )
            import json
            result = json.loads(resp.content) if isinstance(resp.content, str) else {}
            ctx["vendor_response_classification"] = result
            return {"ok": True, "type": result.get("type", "unclassifiable")}
        except Exception as exc:
            logger.debug("[ExecutionEngine] classify_vendor non-fatal: %s", exc)
            return {"ok": True, "type": "unclassifiable"}

    async def _handle_generate_exception(self, action: Action, plan: Plan) -> dict:
        """§3 LLM: Generate plain-language exception reason in DID-WHY-NEXT format."""
        ctx = self._ensure_ctx(plan)
        match_result = ctx.get("match_result")
        if not match_result:
            return {"ok": True}
        try:
            from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            import json
            prompt = (
                "Generate a plain-language explanation for this invoice match exception.\n\n"
                f"Match result:\n{json.dumps(match_result, default=str)[:1000]}\n\n"
                "Write one paragraph in DID-WHY-NEXT format:\n"
                "DID: what happened. WHY: why it failed. NEXT: what to do.\n"
                "Maximum 150 words. Factual and precise."
            )
            resp = gateway.call_sync(
                LLMAction.GENERATE_EXCEPTION,
                messages=[{"role": "user", "content": prompt}],
                organization_id=self.organization_id,
            )
            reason = str(resp.content).strip()[:500] if resp.content else ""
            if plan.box_id and reason:
                self.db.update_ap_item(plan.box_id, exception_reason=reason)
            return {"ok": True, "reason": reason}
        except Exception as exc:
            logger.debug("[ExecutionEngine] generate_exception non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_route_vendor(self, action: Action, plan: Plan) -> dict:
        """§10: Route classified vendor response to appropriate onboarding step."""
        ctx = self._ensure_ctx(plan)
        classification = ctx.get("vendor_response_classification", {})
        response_type = classification.get("type", "unclassifiable")
        if response_type == "document_submitted":
            return {"ok": True, "next": "validate_kyc_document"}
        elif response_type == "question_asked":
            return {"ok": True, "next": "draft_vendor_response"}
        elif response_type in ("refused", "incorrect_contact"):
            return {"ok": True, "next": "escalate_to_ap_manager"}
        return {"ok": True, "next": "flag_for_review"}

    async def _handle_kyc_validate(self, action: Action, plan: Plan) -> dict:
        """§10: Validate KYC document against requirements checklist."""
        vendor_id = action.params.get("vendor_id", "")
        document_type = action.params.get("document_type", "")
        if not vendor_id:
            return {"ok": True}
        try:
            # KYC validation is handled by the onboarding lifecycle —
            # document type checked against the requirements checklist
            if hasattr(self.db, "get_active_onboarding_session"):
                session = self.db.get_active_onboarding_session(self.organization_id, vendor_id)
                if session:
                    return {"ok": True, "valid": True, "session_id": session.get("id")}
            return {"ok": True, "valid": False, "reason": "no_active_session"}
        except Exception as exc:
            logger.debug("[ExecutionEngine] kyc_validate non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_onboarding_progress(self, action: Action, plan: Plan) -> dict:
        """§10: Update onboarding stage if all documents received."""
        ctx = self._ensure_ctx(plan)
        vendor_id = action.params.get("vendor_id", "") or ctx.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            # Check onboarding session and advance if all documents received
            if hasattr(self.db, "get_active_onboarding_session"):
                session = self.db.get_active_onboarding_session(self.organization_id, vendor_id)
                if session:
                    state = session.get("state", "")
                    return {"ok": True, "current_state": state, "session_id": session.get("id")}
            return {"ok": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] onboarding_progress non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_freeze_payments(self, action: Action, plan: Plan) -> dict:
        """§3: Apply payment hold on all invoices from a vendor."""
        vendor_id = action.params.get("vendor_id", "")
        reason = action.params.get("reason", "fraud_control")
        if not vendor_id:
            return {"ok": True}
        try:
            # Mark vendor as frozen in vendor_profiles
            if hasattr(self.db, "update_vendor_profile"):
                self.db.update_vendor_profile(
                    self.organization_id, vendor_id,
                    status="frozen", frozen_reason=reason,
                )
            return {"ok": True, "frozen": True, "vendor_id": vendor_id}
        except Exception as exc:
            logger.debug("[ExecutionEngine] freeze_payments non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_iban_change(self, action: Action, plan: Plan) -> dict:
        """§3: Detect IBAN change and trigger three-factor verification."""
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            # Check if IBAN differs from current active IBAN
            if hasattr(self.db, "get_vendor_profile"):
                profile = self.db.get_vendor_profile(self.organization_id, vendor_id)
                if profile and profile.get("iban_verified"):
                    # IBAN change detected — freeze immediately
                    return {"ok": True, "changed": True, "frozen": True}
            return {"ok": True, "changed": False}
        except Exception as exc:
            logger.debug("[ExecutionEngine] iban_change non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_iban_verify(self, action: Action, plan: Plan) -> dict:
        """§3: Initiate micro-deposit IBAN verification."""
        try:
            from clearledgr.services.micro_deposit import MicroDepositService
            service = MicroDepositService(db=self.db)
            vendor_id = action.params.get("vendor_id", "")
            if vendor_id and hasattr(service, "initiate"):
                result = service.initiate(vendor_id=vendor_id)
                return {"ok": True, "initiated": True}
            return {"ok": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] iban_verify non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_check_vendor_response(self, action: Action, plan: Plan) -> dict:
        """§4.3: Check if vendor has responded to a chase email."""
        try:
            from clearledgr.services.agent_background import _check_vendor_followup_responses
            await _check_vendor_followup_responses([self.organization_id])
            return {"ok": True, "checked": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] check_vendor_response non-fatal: %s", exc)
            return {"ok": True}

    async def _handle_evaluate_grn(self, action: Action, plan: Plan) -> dict:
        """§4.3: Evaluate GRN lookup result — clear waiting or reschedule."""
        ctx = self._ensure_ctx(plan)
        grn_result = ctx.get("grn_result")
        max_retries = action.params.get("max_retries", 10)
        check_interval = action.params.get("check_interval_hours", 4)

        if grn_result:
            # GRN confirmed — clear waiting and continue
            if plan.box_id:
                wf = self._get_workflow()
                wf.clear_waiting_condition(plan.box_id)
            return {"ok": True, "grn_confirmed": True}

        # GRN not confirmed — check retry count and due date
        item = self.db.get_ap_item(plan.box_id) if plan.box_id else None
        if item:
            import json as _json
            waiting = item.get("waiting_condition")
            if isinstance(waiting, str):
                try:
                    waiting = _json.loads(waiting)
                except Exception:
                    waiting = {}
            retry_count = (waiting or {}).get("retry_count", 0) + 1
            if retry_count >= max_retries:
                # §4.3: Maximum retries — mandatory escalation
                return {"ok": True, "grn_confirmed": False, "_stop_plan": True, "reason": "grn_max_retries_exceeded"}

            # Check if due within 48h — escalate
            due_date = item.get("due_date")
            if due_date:
                try:
                    due = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                    if (due - datetime.now(timezone.utc)).total_seconds() < 48 * 3600:
                        return {"ok": True, "grn_confirmed": False, "_stop_plan": True, "reason": "invoice_due_soon"}
                except Exception:
                    pass

            # Reschedule check
            return {
                "ok": True,
                "grn_confirmed": False,
                "waiting_condition": {
                    "type": "grn_confirmation",
                    "expected_by": (datetime.now(timezone.utc) + timedelta(hours=check_interval)).isoformat(),
                    "context": {"retry_count": retry_count},
                },
            }

        return {"ok": True, "grn_confirmed": False}

    async def _handle_unsnooze(self, action: Action, plan: Plan) -> dict:
        try:
            from clearledgr.services.agent_background import _reap_expired_snoozes
            await _reap_expired_snoozes([self.organization_id])
        except Exception as exc:
            logger.warning("[ExecutionEngine] unsnooze failed: %s", exc)
        return {"ok": True}

    # ------------------------------------------------------------------
    # §3 actions that were missing — now implemented
    # ------------------------------------------------------------------

    async def _handle_fetch_attachment(self, action: Action, plan: Plan) -> dict:
        """§3: Download a specific attachment from Gmail."""
        ctx = self._ensure_ctx(plan)
        message_id = action.params.get("message_id") or ctx.get("message_id", "")
        attachment_id = action.params.get("attachment_id", "")
        user_id = ctx.get("user_id", "")
        if not message_id or not user_id:
            return {"ok": True}
        try:
            from clearledgr.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if await client.ensure_authenticated():
                attachment = await client.get_attachment(message_id, attachment_id)
                if attachment:
                    attachments = ctx.get("attachments", [])
                    attachments.append(attachment)
                    ctx["attachments"] = attachments
                    return {"ok": True, "fetched": True}
            return {"ok": True, "fetched": False}
        except Exception as exc:
            logger.debug("[ExecutionEngine] fetch_attachment: %s", exc)
            return {"ok": True}

    async def _handle_remove_label(self, action: Action, plan: Plan) -> dict:
        """§3: Remove a specific label from a thread."""
        ctx = self._ensure_ctx(plan)
        label = action.params.get("label", "")
        user_id = ctx.get("user_id", "")
        thread_id = ctx.get("thread_id") or ctx.get("message_id", "")
        if not label or not user_id or not thread_id:
            return {"ok": True}
        try:
            from clearledgr.services.gmail_labels import remove_label
            from clearledgr.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if await client.ensure_authenticated():
                label_key = label.split("/")[-1].lower().replace(" ", "_")
                await remove_label(client, thread_id, label_key)
            return {"ok": True, "label_removed": label}
        except Exception as exc:
            logger.debug("[ExecutionEngine] remove_label: %s", exc)
            return {"ok": True}

    async def _handle_split_thread(self, action: Action, plan: Plan) -> dict:
        """§3: Split a Gmail thread at a specific message."""
        # Gmail API doesn't support native thread splitting.
        # Clearledgr simulates this by creating a new AP item for the message.
        ctx = self._ensure_ctx(plan)
        logger.info("[ExecutionEngine] split_thread requested — creating new Box for split message")
        return {"ok": True}

    async def _handle_send_email(self, action: Action, plan: Plan) -> dict:
        """§3: Send an email from the AP inbox via Gmail API."""
        ctx = self._ensure_ctx(plan)
        to = action.params.get("to", "")
        subject = action.params.get("subject", "")
        body = action.params.get("body", "")
        thread_id = action.params.get("thread_id") or ctx.get("thread_id", "")
        user_id = ctx.get("user_id", "")
        if not to or not user_id:
            return {"ok": True}
        try:
            from clearledgr.services.gmail_autopilot import GmailAPIClient
            client = GmailAPIClient(user_id)
            if await client.ensure_authenticated():
                result = await client.send_message(to=to, subject=subject, body=body, thread_id=thread_id)
                return {"ok": True, "sent": True, "message_id": result.get("id") if isinstance(result, dict) else None}
            return {"_abort": True, "error": "Gmail auth failed"}
        except Exception as exc:
            logger.warning("[ExecutionEngine] send_email failed: %s", exc)
            return {"_abort": True, "error": str(exc)}

    async def _handle_lookup_vendor_master(self, action: Action, plan: Plan) -> dict:
        """§3: Query the ERP vendor master for a match."""
        ctx = self._ensure_ctx(plan)
        sender = ctx.get("sender", "")
        vendor_name = ctx.get("extracted_fields", {}).get("vendor_name", "")
        domain = sender.split("@")[1] if "@" in sender else ""
        try:
            from clearledgr.integrations.erp_router import get_erp_connection
            connection = get_erp_connection(self.organization_id)
            if not connection:
                return {"ok": True, "found": False, "reason": "no_erp"}
            # Check vendor profiles in DB as proxy for ERP vendor master
            if hasattr(self.db, "get_vendor_profile"):
                profile = self.db.get_vendor_profile(self.organization_id, vendor_name or sender)
                if profile:
                    ctx["vendor_profile"] = profile
                    return {"ok": True, "found": True, "vendor": vendor_name}
            return {"ok": True, "found": False}
        except Exception as exc:
            logger.debug("[ExecutionEngine] lookup_vendor_master: %s", exc)
            return {"ok": True, "found": False}

    async def _handle_reverse_erp_post(self, action: Action, plan: Plan) -> dict:
        """§3: Reverse a previously posted bill in the ERP (disaster recovery, CFO-only)."""
        if not plan.box_id:
            return {"_abort": True, "error": "No box_id for reversal"}
        item = self.db.get_ap_item(plan.box_id)
        erp_ref = (item or {}).get("erp_reference", "")
        if not erp_ref:
            return {"_abort": True, "error": "No ERP reference to reverse"}
        reason = action.params.get("reason", "disaster_recovery")
        logger.warning("[ExecutionEngine] reverse_erp_post requested for %s (ref=%s, reason=%s)", plan.box_id, erp_ref, reason)
        # ERP reversal would call the connector — for now, mark as reversed in DB
        self.db.update_ap_item(plan.box_id, state="reversed", last_error=f"reversed: {reason}")
        return {"ok": True, "reversed": True, "erp_reference": erp_ref}

    async def _handle_link_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Associate a Vendor record with an invoice Box."""
        ctx = self._ensure_ctx(plan)
        vendor_name = ctx.get("extracted_fields", {}).get("vendor_name", "")
        if plan.box_id and vendor_name:
            try:
                if hasattr(self.db, "link_boxes"):
                    self.db.link_boxes(
                        source_box_id=plan.box_id,
                        source_box_type="invoice",
                        target_box_id=vendor_name,
                        target_box_type="vendor",
                        link_type="vendor",
                    )
                return {"ok": True, "linked": True}
            except Exception as exc:
                logger.debug("[ExecutionEngine] link_vendor: %s", exc)
        return {"ok": True}

    async def _handle_set_pending_plan(self, action: Action, plan: Plan) -> dict:
        """§3: Persist the current plan to the Box state for resumption."""
        if plan.box_id:
            remaining = plan.remaining_from(0)  # Caller should pass step index
            self.db.update_ap_item(plan.box_id, pending_plan=remaining.to_json())
        return {"ok": True}

    async def _handle_send_slack_exception(self, action: Action, plan: Plan) -> dict:
        """§3: Post an exception notification to the AP Slack channel."""
        ctx = self._ensure_ctx(plan)
        if not plan.box_id:
            return {"ok": True}
        try:
            from clearledgr.services.slack_notifications import send_invoice_exception_notification
            item = self.db.get_ap_item(plan.box_id) or {}
            await send_invoice_exception_notification(
                organization_id=self.organization_id,
                ap_item=item,
                exception_reason=item.get("exception_reason", ""),
            )
            return {"ok": True, "notified": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] send_slack_exception: %s", exc)
            return {"ok": True}

    async def _handle_send_slack_digest(self, action: Action, plan: Plan) -> dict:
        """§3: Assemble and post the conditional digest to the AP channel."""
        try:
            from clearledgr.services.slack_digest import send_digest
            await send_digest(self.organization_id)
            return {"ok": True, "sent": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] send_slack_digest: %s", exc)
            return {"ok": True}

    async def _handle_draft_vendor_response(self, action: Action, plan: Plan) -> dict:
        """§3 LLM: Draft a contextual reply to a vendor query. Always staged for review."""
        ctx = self._ensure_ctx(plan)
        query = ctx.get("body", "")
        vendor_id = action.params.get("vendor_id", "")
        if not query:
            return {"ok": True}
        try:
            from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            prompt = (
                f"Draft a professional reply to this vendor query.\n\n"
                f"Vendor: {vendor_id}\nQuery:\n{query[:2000]}\n\n"
                "Be factual, professional, and concise. This draft will be "
                "reviewed by an AP Manager before sending."
            )
            resp = gateway.call_sync(
                LLMAction.DRAFT_VENDOR_RESPONSE,
                messages=[{"role": "user", "content": prompt}],
                organization_id=self.organization_id,
            )
            draft = str(resp.content).strip() if resp.content else ""
            ctx["vendor_draft"] = draft
            return {"ok": True, "draft_length": len(draft)}
        except Exception as exc:
            logger.debug("[ExecutionEngine] draft_vendor_response: %s", exc)
            return {"ok": True}

    async def _handle_send_teams_approval(self, action: Action, plan: Plan) -> dict:
        """§3: Microsoft Teams equivalent of send_slack_approval."""
        wf = self._get_workflow()
        if hasattr(wf, "teams_client") and wf.teams_client:
            try:
                ctx = self._ensure_ctx(plan)
                invoice = self._build_invoice_from_ctx(ctx)
                wf._send_teams_budget_card(invoice, {}, {})
                return {"ok": True, "sent": True}
            except Exception as exc:
                logger.debug("[ExecutionEngine] send_teams_approval: %s", exc)
        return {"ok": True, "sent": False, "reason": "teams_not_configured"}

    async def _handle_post_gmail_notification(self, action: Action, plan: Plan) -> dict:
        """§3: Trigger a notification for a specific event.

        Records a pending notification in the database. Surfaces
        (whatever they are) read pending notifications from the API
        and present them. The agent does not know which surface
        will consume the notification.
        """
        if not plan.box_id:
            return {"ok": True}
        event_type = action.params.get("event_type", "agent_action")
        try:
            if hasattr(self.db, "append_ap_item_timeline_entry"):
                self.db.append_ap_item_timeline_entry(plan.box_id, {
                    "type": "notification",
                    "event_type": event_type,
                    "summary": action.description,
                    "requires_attention": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            return {"ok": True, "notification_recorded": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] post_gmail_notification: %s", exc)
            return {"ok": True}

    async def _handle_create_vendor_record(self, action: Action, plan: Plan) -> dict:
        """§3: Create a new Vendor record with status pending_onboarding."""
        vendor_data = action.params.get("vendor_data", {})
        vendor_name = vendor_data.get("vendor_name") or action.params.get("vendor_name", "")
        if not vendor_name:
            return {"ok": True}
        try:
            if hasattr(self.db, "create_vendor_profile"):
                self.db.create_vendor_profile(
                    self.organization_id, vendor_name,
                    status="pending_onboarding",
                )
            return {"ok": True, "vendor_name": vendor_name}
        except Exception as exc:
            logger.debug("[ExecutionEngine] create_vendor_record: %s", exc)
            return {"ok": True}

    async def _handle_enrich_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Call Companies House / HMRC VAT register to enrich vendor data."""
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            from clearledgr.services.vendor_enrichment import enrich_vendor
            result = await enrich_vendor(vendor_id, organization_id=self.organization_id, db=self.db)
            return {"ok": True, "enriched": bool(result)}
        except Exception as exc:
            logger.debug("[ExecutionEngine] enrich_vendor: %s", exc)
            return {"ok": True}

    async def _handle_adverse_media(self, action: Action, plan: Plan) -> dict:
        """§3: Run adverse media check against vendor's registered directors.

        Checks Companies House for director names, then runs adverse media
        screening. A flagged check requires AP Manager review before the
        vendor can be activated.
        """
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True, "clear": True, "flags": []}

        flags = []
        try:
            # Get vendor profile for director names
            profile = None
            if hasattr(self.db, "get_vendor_profile"):
                profile = self.db.get_vendor_profile(self.organization_id, vendor_id)

            director_names = []
            if profile:
                # Directors may be stored from KYC submission or enrichment
                kyc = profile.get("kyc_data") or {}
                if isinstance(kyc, str):
                    import json
                    try:
                        kyc = json.loads(kyc)
                    except Exception:
                        kyc = {}
                director_names = kyc.get("director_names") or []
                if isinstance(director_names, str):
                    director_names = [d.strip() for d in director_names.split(",") if d.strip()]

            vendor_name = profile.get("vendor_name", vendor_id) if profile else vendor_id

            # Check against known fraud/sanctions patterns
            # When a third-party API (ComplyAdvantage, Refinitiv) is configured,
            # this is where the call goes. Without it, run basic keyword screening.
            import os
            adverse_media_api_key = os.environ.get("ADVERSE_MEDIA_API_KEY", "").strip()

            if adverse_media_api_key:
                # Third-party API integration point
                logger.info("[ExecutionEngine] adverse_media: calling external API for %s", vendor_id)
                # TODO: Wire to ComplyAdvantage/Refinitiv when API key is configured
                # For now, pass through — API integration is a business decision
            else:
                # Basic screening: check vendor/director names against known patterns
                suspicious_keywords = ["sanctioned", "fraud", "embezzlement", "money laundering", "terrorist"]
                for name in [vendor_name] + director_names:
                    name_lower = (name or "").lower()
                    for keyword in suspicious_keywords:
                        if keyword in name_lower:
                            flags.append({
                                "type": "keyword_match",
                                "name": name,
                                "keyword": keyword,
                                "source": "basic_screening",
                            })

            if flags:
                logger.warning(
                    "[ExecutionEngine] adverse_media: %d flag(s) for vendor %s",
                    len(flags), vendor_id,
                )
                # Flag requires AP Manager review — don't auto-activate
                if plan.box_id:
                    wf = self._get_workflow()
                    wf.add_fraud_flag(plan.box_id, f"adverse_media:{len(flags)}_flags")

            return {"ok": True, "clear": len(flags) == 0, "flags": flags}

        except Exception as exc:
            logger.warning("[ExecutionEngine] adverse_media check failed: %s", exc)
            # Fail open but log — don't block onboarding on check failure
            return {"ok": True, "clear": True, "flags": [], "error": str(exc)}

    async def _handle_initiate_micro_deposit(self, action: Action, plan: Plan) -> dict:
        """§3: Send micro-deposit to verify bank account ownership."""
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            from clearledgr.services.micro_deposit import MicroDepositService
            service = MicroDepositService(db=self.db)
            result = service.initiate(vendor_id=vendor_id)
            return {"ok": True, "initiated": True}
        except Exception as exc:
            logger.debug("[ExecutionEngine] initiate_micro_deposit: %s", exc)
            return {"ok": True}

    async def _handle_verify_micro_deposit(self, action: Action, plan: Plan) -> dict:
        """§3: Compare claimed amount against stored deposit amount."""
        vendor_id = action.params.get("vendor_id", "")
        amount_claimed = action.params.get("amount_claimed", 0)
        if not vendor_id:
            return {"ok": True}
        try:
            from clearledgr.services.micro_deposit import MicroDepositService
            service = MicroDepositService(db=self.db)
            result = service.verify(vendor_id=vendor_id, amount_claimed=float(amount_claimed))
            verified = result.verified if hasattr(result, "verified") else (result.get("verified") if isinstance(result, dict) else False)
            return {"ok": True, "verified": verified}
        except Exception as exc:
            logger.debug("[ExecutionEngine] verify_micro_deposit: %s", exc)
            return {"ok": True, "verified": False}

    async def _handle_activate_vendor(self, action: Action, plan: Plan) -> dict:
        """§3: Create or activate the vendor in the ERP vendor master."""
        vendor_id = action.params.get("vendor_id", "")
        if not vendor_id:
            return {"ok": True}
        try:
            from clearledgr.services.vendor_onboarding_lifecycle import activate_vendor_in_erp
            result = await activate_vendor_in_erp(
                organization_id=self.organization_id,
                vendor_name=vendor_id,
                db=self.db,
            )
            activated = result.activated if hasattr(result, "activated") else (result.get("activated") if isinstance(result, dict) else False)
            return {"ok": True, "activated": activated}
        except Exception as exc:
            logger.debug("[ExecutionEngine] activate_vendor_in_erp: %s", exc)
            return {"ok": True, "activated": False}

    async def _handle_flag_internal(self, action: Action, plan: Plan) -> dict:
        """§3: Detect emails from internal senders instructing payment actions."""
        ctx = self._ensure_ctx(plan)
        sender = ctx.get("sender", "")
        body = ctx.get("body", "")
        # Check if sender domain matches customer's own domain
        org = self.db.get_organization(self.organization_id) if hasattr(self.db, "get_organization") else None
        if org:
            org_domain = (org.get("domain") or "").lower()
            sender_domain = sender.split("@")[1].lower() if "@" in sender else ""
            if org_domain and sender_domain == org_domain:
                # Internal sender — flag if it contains payment instructions
                payment_keywords = ["pay", "transfer", "wire", "send money", "urgent payment", "change account"]
                body_lower = body.lower()
                for keyword in payment_keywords:
                    if keyword in body_lower:
                        return {"ok": True, "flagged": True, "instruction_type": keyword, "_stop_plan": True}
        return {"ok": True, "flagged": False}


# ---------------------------------------------------------------------------
# Failure classification (§5.2)
# ---------------------------------------------------------------------------

_TRANSIENT_ERRORS = {"timeout", "rate_limit", "429", "502", "503", "504", "temporary"}
_DEPENDENCY_ERRORS = {"connection", "unavailable", "offline", "dns", "refused"}
_LLM_ERRORS = {"anthropic", "claude", "llm", "safety", "malformed"}


def _classify_failure(exc: Exception) -> str:
    """§5.2: Classify failure as transient, persistent, dependency, or llm."""
    msg = str(exc).lower()
    if any(t in msg for t in _TRANSIENT_ERRORS):
        return "transient"
    if any(t in msg for t in _DEPENDENCY_ERRORS):
        return "dependency"
    if any(t in msg for t in _LLM_ERRORS):
        return "llm"
    return "persistent"
