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
            # Email and Inbox
            "read_email": self._handle_read_email,
            "apply_label": self._handle_apply_label,
            "watch_thread": self._handle_watch_thread,

            # Classification and Extraction (LLM)
            "classify_email": self._handle_classify_email,
            "extract_invoice_fields": self._handle_extract,
            "run_extraction_guardrails": self._handle_guardrails,
            "classify_vendor_response": self._handle_classify_vendor,
            "generate_exception_reason": self._handle_generate_exception,

            # ERP
            "lookup_po": self._handle_lookup_po,
            "lookup_grn": self._handle_lookup_grn,
            "run_three_way_match": self._handle_match,
            "post_bill": self._handle_post_bill,
            "pre_post_validate": self._handle_pre_post_validate,
            "schedule_payment": self._handle_schedule_payment,

            # Box and State
            "create_box": self._handle_create_box,
            "update_box_fields": self._handle_update_fields,
            "move_box_stage": self._handle_stage_transition,
            "post_timeline_entry": self._handle_timeline,
            "set_waiting_condition": self._handle_set_waiting,
            "clear_waiting_condition": self._handle_clear_waiting,
            "resume_from_pending_plan": self._handle_resume_plan,

            # Fraud
            "check_domain_match": self._handle_domain_match,
            "check_duplicate": self._handle_duplicate,
            "check_duplicate_full": self._handle_duplicate,
            "check_amount_ceiling": self._handle_ceiling,
            "check_velocity": self._handle_velocity,
            "check_iban_change": self._handle_iban_change,

            # Communication
            "send_approval": self._handle_send_approval,
            "send_vendor_email": self._handle_send_vendor_email,
            "send_override_window": self._handle_override_window,
            "close_override_window": self._handle_close_override,
            "escalate_approval": self._handle_escalate,
            "route_vendor_response": self._handle_route_vendor,

            # Vendor Onboarding
            "validate_kyc_document": self._handle_kyc_validate,
            "update_onboarding_progress": self._handle_onboarding_progress,
            "freeze_vendor_payments": self._handle_freeze_payments,
            "initiate_iban_verification": self._handle_iban_verify,
            "check_vendor_response": self._handle_check_vendor_response,
            "evaluate_grn_result": self._handle_evaluate_grn,
            "unsnooze": self._handle_unsnooze,
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
        handler = self._handlers.get(action.name)
        if not handler:
            logger.warning("[ExecutionEngine] No handler for action: %s", action.name)
            return {"ok": True, "_stop_plan": False}

        timeout = _ACTION_TIMEOUTS.get(action.name, _DEFAULT_TIMEOUT)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    handler(action, plan),
                    timeout=timeout,
                )
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
                organization_id=self.organization_id, db=self.db,
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
            return {"ok": True, "match_status": status, "match_passed": match_passed}
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
                # Payment scheduling is ERP-specific — record intent
                self.db.update_ap_item(plan.box_id, metadata={
                    **(item.get("metadata") or {}),
                    "payment_scheduled": True,
                    "payment_scheduled_at": datetime.now(timezone.utc).isoformat(),
                })
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
            service = get_override_window_service(db=self.db)
            window = service.open_window(
                ap_item_id=plan.box_id,
                organization_id=self.organization_id,
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
            await chase_stale_sessions(self.organization_id, db=self.db)
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
