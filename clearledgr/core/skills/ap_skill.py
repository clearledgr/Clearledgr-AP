"""AP (Accounts Payable) skill — five tools that wrap the existing AP pipeline.

Claude calls these tools during the planning loop instead of following the
hardcoded 8-step sequence in AgentOrchestrator._process_invoice_legacy().

Tool catalogue (in typical execution order):
  1. enrich_with_context    — fetch vendor history, correction suggestions, priority
  2. run_validation_gate    — deterministic confidence/PO/budget checks
  3. get_ap_decision        — call APDecisionService (Claude Sonnet) with full context
  4. execute_routing        — route based on recommendation; auto-approve or HITL pause
  5. request_vendor_info    — draft a Gmail follow-up when info is missing

Each handler:
  - Accepts **kwargs (extra args from Claude are silently ignored).
  - Accepts organization_id injected by the runtime.
  - NEVER raises — returns {"ok": False, "error": "..."} on failure.
  - Sets {"is_awaiting_human": True} when a human decision is required (runtime pauses for HITL).
"""
from __future__ import annotations

import json
import logging
import inspect
from typing import Any, Dict, List, Optional

from clearledgr.core.skills.base import AgentTool, AgentTask, FinanceSkill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_invoice(invoice_payload: Dict[str, Any]):
    """Reconstruct InvoiceData from a serialised dict (best-effort)."""
    from clearledgr.services.invoice_workflow import InvoiceData
    valid_fields = InvoiceData.__dataclass_fields__.keys()
    filtered = {k: v for k, v in invoice_payload.items() if k in valid_fields}
    return InvoiceData(**filtered)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_enrich_with_context(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Fetch vendor history, correction learning, reflection, and reasoning for the invoice."""
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.correction_learning import get_correction_learning_service

        invoice = _build_invoice(invoice_payload)
        db = get_db()

        # --- Resolve vendor aliases (dedup integration) ---
        try:
            from clearledgr.services.vendor_dedup import get_vendor_dedup_service
            resolved_name = get_vendor_dedup_service(organization_id).resolve_vendor_name(invoice.vendor_name or "")
            if resolved_name and resolved_name != invoice.vendor_name:
                invoice.vendor_name = resolved_name
        except Exception as exc:
            logger.debug("Vendor dedup resolution failed: %s", exc)

        # --- Gap 3: Self-reflection validates extraction before enrichment ---
        reflection_data: Dict[str, Any] = {}
        invoice_text = getattr(invoice, "invoice_text", "") or ""
        try:
            from clearledgr.services.agent_reflection import get_agent_reflection

            if invoice_text:
                reflection = get_agent_reflection()
                reflection_result = reflection.reflect_on_extraction(
                    extraction={
                        "vendor": invoice.vendor_name,
                        "total_amount": invoice.amount,
                        "due_date": getattr(invoice, "due_date", None),
                        "invoice_number": getattr(invoice, "invoice_number", None),
                        "currency": getattr(invoice, "currency", "USD"),
                    },
                    original_text=invoice_text,
                )
                reflection_data = {
                    "self_verified": reflection_result.self_verified,
                    "corrections_made": reflection_result.corrections_made,
                    "issues_found": reflection_result.issues_found,
                    "confidence_adjustment": reflection_result.confidence_adjustment,
                    "reflection_notes": reflection_result.reflection_notes,
                }
        except Exception as refl_exc:
            logger.debug("[APSkill] reflection skipped: %s", refl_exc)

        # --- Vendor enrichment (existing) ---
        vendor_profile = db.get_vendor_profile(organization_id, invoice.vendor_name) or {}
        vendor_history = db.get_vendor_invoice_history(organization_id, invoice.vendor_name, limit=6) or []
        decision_feedback = db.get_vendor_decision_feedback(organization_id, invoice.vendor_name) or []

        correction_svc = get_correction_learning_service(organization_id)
        correction_suggestions = correction_svc.suggest(invoice.vendor_name, invoice.amount)

        # --- Cross-invoice analysis (duplicate detection, anomalies, vendor stats) ---
        cross_invoice_data: Dict[str, Any] = {}
        try:
            from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer

            analyzer = get_cross_invoice_analyzer(organization_id)
            analysis = analyzer.analyze(
                vendor=invoice.vendor_name,
                amount=invoice.amount,
                invoice_number=getattr(invoice, "invoice_number", None),
                invoice_date=getattr(invoice, "due_date", None),
                currency=getattr(invoice, "currency", "USD"),
                gmail_id=getattr(invoice, "gmail_id", None),
            )
            cross_invoice_data = analysis.to_dict()
        except Exception as ci_exc:
            logger.debug("[APSkill] cross-invoice analysis skipped: %s", ci_exc)

        # --- Gap 2: Reasoning factors provide contextual enrichment ---
        reasoning_data: Dict[str, Any] = {}
        try:
            from clearledgr.services.agent_reasoning import get_reasoning_agent

            if invoice_text:
                agent = get_reasoning_agent(organization_id)
                decision = agent.reason_about_invoice(invoice_text)
                reasoning_data = {
                    "reasoning_factors": [
                        {"name": f.name, "score": f.score, "explanation": f.explanation}
                        for f in (decision.factors or [])
                    ],
                    "reasoning_risks": decision.risks or [],
                    "reasoning_confidence": decision.confidence,
                }
        except Exception as reason_exc:
            logger.debug("[APSkill] reasoning skipped: %s", reason_exc)

        return {
            "ok": True,
            "vendor_profile": vendor_profile,
            "vendor_history": vendor_history[:6],
            "decision_feedback": decision_feedback[:10],
            "correction_suggestions": correction_suggestions,
            "cross_invoice_analysis": cross_invoice_data,
            "vendor_name": invoice.vendor_name,
            "amount": invoice.amount,
            "reflection": reflection_data,
            "reasoning": reasoning_data,
        }
    except Exception as exc:
        logger.warning("[APSkill] enrich_with_context failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_run_validation_gate(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Run deterministic validation: confidence threshold, PO check, budget gate."""
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow

        invoice = _build_invoice(invoice_payload)
        workflow = get_invoice_workflow(organization_id)
        gate = await workflow._evaluate_deterministic_validation(invoice)
        passed = not gate.get("failed", False)
        return {
            "ok": True,
            "passed": passed,
            "gate_result": gate,
            "failures": gate.get("failures", []),
            "override_needed": not passed,
        }
    except Exception as exc:
        logger.warning("[APSkill] run_validation_gate failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_get_ap_decision(
    invoice_payload: Dict[str, Any],
    vendor_context: Optional[Dict[str, Any]] = None,
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Call APDecisionService (Claude Sonnet) with full vendor context."""
    try:
        from clearledgr.services.ap_decision import APDecisionService
        from clearledgr.core.database import get_db

        invoice = _build_invoice(invoice_payload)
        db = get_db()
        ctx = vendor_context or {}

        vendor_profile = ctx.get("vendor_profile") or db.get_vendor_profile(organization_id, invoice.vendor_name) or {}
        vendor_history = ctx.get("vendor_history") or db.get_vendor_invoice_history(organization_id, invoice.vendor_name, limit=6) or []
        decision_feedback = ctx.get("decision_feedback") or db.get_vendor_decision_feedback(organization_id, invoice.vendor_name) or []
        correction_suggestions = ctx.get("correction_suggestions") or []

        service = APDecisionService()
        decision_or_awaitable = service.decide(
            invoice=invoice,
            vendor_profile=vendor_profile,
            vendor_history=vendor_history,
            decision_feedback=decision_feedback,
            correction_suggestions=correction_suggestions,
            org_config={"organization_id": organization_id},
        )
        decision = (
            await decision_or_awaitable
            if inspect.isawaitable(decision_or_awaitable)
            else decision_or_awaitable
        )
        return {
            "ok": True,
            "recommendation": decision.recommendation,
            "reasoning": decision.reasoning,
            "confidence": decision.confidence,
            "risk_flags": decision.risk_flags,
            "info_needed": decision.info_needed,
        }
    except Exception as exc:
        logger.warning("[APSkill] get_ap_decision failed: %s", exc)
        return {"ok": False, "error": str(exc), "recommendation": "escalate"}


async def _handle_execute_routing(
    invoice_payload: Dict[str, Any],
    recommendation: str = "escalate",
    confidence: float = 0.0,
    reason: str = "",
    risk_flags: Optional[list] = None,
    info_needed: Optional[str] = None,
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Route invoice: auto-approve or request human review (HITL pause).

    Passes the pre-computed AP decision from the planning loop into
    process_new_invoice so that Claude Sonnet is NOT called a second time.
    """
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow
        from clearledgr.services.ap_decision import APDecision

        invoice = _build_invoice(invoice_payload)
        workflow = get_invoice_workflow(organization_id)

        # Map recommendation → confidence that controls workflow routing
        APPROVAL_THRESHOLD = float(
            __import__("os").getenv("INVOICE_AUTO_APPROVE_THRESHOLD", "0.95")
        )
        if recommendation == "approve" and confidence >= APPROVAL_THRESHOLD:
            invoice.confidence = max(confidence, APPROVAL_THRESHOLD)
        else:
            # Anything below threshold → workflow sends for human review
            invoice.confidence = min(confidence, APPROVAL_THRESHOLD - 0.01)

        # Build pre-computed APDecision so process_new_invoice skips its
        # internal _get_ap_decision() call (avoids double Sonnet invocation).
        pre_computed_decision = APDecision(
            recommendation=recommendation,
            reasoning=reason or f"Agent planning loop decided: {recommendation}",
            confidence=confidence,
            info_needed=info_needed,
            risk_flags=risk_flags or [],
            vendor_context_used={},
            model="agent_planning_loop",
            fallback=False,
        )

        result = await workflow.process_new_invoice(invoice, ap_decision=pre_computed_decision)
        needs_human = result.get("status") in (
            "pending_approval", "needs_info", "escalated", "failed"
        )
        return {
            "ok": True,
            "status": result.get("status"),
            "invoice_id": result.get("invoice_id"),
            "recommendation": recommendation,
            "is_awaiting_human": needs_human,
            "hitl_context": {
                "invoice_id": result.get("invoice_id"),
                "recommendation": recommendation,
                "reason": reason or result.get("reason", ""),
                "status": result.get("status"),
            } if needs_human else None,
        }
    except Exception as exc:
        logger.warning("[APSkill] execute_routing failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_verify_erp_posting(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Verify a posted invoice exists in the connected ERP."""
    try:
        from clearledgr.integrations.erp_router import verify_bill_posted

        erp_reference = invoice_payload.get("erp_reference")
        invoice_number = invoice_payload.get("invoice_number")
        amount = invoice_payload.get("amount")

        # Need at least an invoice number to look up
        lookup_key = invoice_number or erp_reference
        if not lookup_key:
            return {"ok": True, "verified": False, "reason": "no_erp_reference"}

        result = await verify_bill_posted(
            organization_id=organization_id,
            invoice_number=str(lookup_key),
            expected_amount=float(amount) if amount is not None else None,
        )
        return {"ok": True, **result}
    except Exception as exc:
        logger.warning("[APSkill] verify_erp_posting failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_request_vendor_info(
    invoice_payload: Dict[str, Any],
    question: Optional[str] = None,
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Send (or draft) a Gmail follow-up when the invoice is missing information.

    Attempts to send the email directly using ``gmail_client.send_message()``.
    If sending fails (e.g. missing gmail.send scope), falls back to creating
    a draft.  All sends are recorded in AP item metadata for audit trail.

    NEVER raises.
    """
    try:
        from clearledgr.services.auto_followup import get_auto_followup_service

        followup_svc = get_auto_followup_service(organization_id)
        missing_info = followup_svc.detect_missing_info(invoice_payload)

        # If nothing is missing and no explicit question, skip
        if not missing_info and not question:
            return {
                "ok": True,
                "draft_created": False,
                "sent": False,
                "reason": "no_missing_info",
                "missing_info": [],
            }

        thread_id = (
            invoice_payload.get("gmail_thread_id")
            or invoice_payload.get("thread_id")
            or invoice_payload.get("gmail_id")
        )
        to_email = (
            invoice_payload.get("sender")
            or invoice_payload.get("sender_email")
            or invoice_payload.get("from_email")
        )

        if not thread_id or not to_email:
            return {
                "ok": False,
                "draft_created": False,
                "sent": False,
                "error": "Missing thread_id or sender_email — cannot create draft",
                "missing_info": [m.value for m in missing_info],
            }

        # Obtain Gmail client for this org
        from clearledgr.services.gmail_client import get_gmail_client

        gmail_client = get_gmail_client(organization_id)

        ap_item_id = invoice_payload.get("id") or invoice_payload.get("ap_item_id") or ""

        # --- Build subject & body ---
        original_subject = invoice_payload.get("subject") or "Invoice follow-up"
        vendor = invoice_payload.get("vendor_name") or invoice_payload.get("vendor") or "Unknown vendor"
        amount = invoice_payload.get("amount") or 0
        invoice_number = invoice_payload.get("invoice_number") or "N/A"

        # Pick template or use Claude-generated question
        template_id: Optional[str] = None
        if question:
            subject = f"Re: {original_subject} — Clarification Needed"
            body = (
                f"Hi,\n\n"
                f"We are reviewing invoice #{invoice_number} from {vendor} "
                f"(${amount:,.2f}) and need the following information before we can process payment:\n\n"
                f"{question}\n\n"
                f"Please reply at your earliest convenience.\n\n"
                f"Best regards"
            )
            template_id = "general_inquiry"
        else:
            # Try template-based rendering
            try:
                from clearledgr.services.vendor_communication_templates import render_template

                # Map missing info to template ID
                _MISSING_TO_TEMPLATE = {
                    "po_number": "missing_po",
                    "amount": "missing_amount",
                    "due_date": "missing_due_date",
                    "bank_details": "bank_details_verification",
                }
                primary_type = missing_info[0].value if missing_info else "general_inquiry"
                template_id = _MISSING_TO_TEMPLATE.get(primary_type, "general_inquiry")
                rendered = render_template(template_id, {
                    "original_subject": original_subject,
                    "invoice_number": invoice_number,
                    "vendor_name": vendor,
                    "amount": f"{amount:,.2f}" if amount else "N/A",
                    "currency": invoice_payload.get("currency", "USD"),
                    "company_name": "Clearledgr",
                    "question": question or "",
                })
                subject = rendered["subject"]
                body = rendered["body"]
            except Exception:
                # Fall back to simple subject/body
                subject = f"Re: {original_subject} — Clarification Needed"
                body = (
                    f"Hi,\n\n"
                    f"We need additional information to process invoice #{invoice_number} "
                    f"from {vendor} (${amount:,.2f}).\n\n"
                    f"Please reply at your earliest convenience.\n\n"
                    f"Best regards"
                )

        # --- Attempt to send directly ---
        sent = False
        sent_message_id: Optional[str] = None
        draft_id: Optional[str] = None

        try:
            result = await gmail_client.send_message(
                to=to_email,
                subject=subject,
                body=body,
                thread_id=thread_id,
            )
            sent = True
            sent_message_id = result.get("id")
            logger.info(
                "Vendor follow-up SENT for ap_item_id=%s thread=%s message_id=%s",
                ap_item_id, thread_id, sent_message_id,
            )
        except Exception as send_exc:
            # Detect scope issue so operator knows to re-authenticate
            exc_str = str(send_exc).lower()
            if "insufficient" in exc_str or "403" in exc_str or "scope" in exc_str:
                logger.warning(
                    "Gmail send failed for ap_item_id=%s — likely missing gmail.send scope. "
                    "User should re-authenticate Gmail to enable direct sending. "
                    "Falling back to draft. Error: %s",
                    ap_item_id, send_exc,
                )
            else:
                logger.info(
                    "Direct send failed for ap_item_id=%s (%s), falling back to draft",
                    ap_item_id, send_exc,
                )
            # Fall back to draft
            draft_id = await followup_svc.create_gmail_draft(
                gmail_client=gmail_client,
                ap_item_id=ap_item_id,
                thread_id=thread_id,
                to_email=to_email,
                invoice_data=invoice_payload,
                question=question,
            )

        # --- Record in AP item metadata (audit trail) ---
        try:
            from clearledgr.core.database import get_db
            from datetime import datetime as _dt, timezone as _tz

            db = get_db()
            now_iso = _dt.now(_tz.utc).isoformat()

            ap_item = db.get_ap_item(ap_item_id) if ap_item_id else None
            if ap_item:
                metadata = json.loads(ap_item.get("metadata") or "{}") if isinstance(ap_item.get("metadata"), str) else (ap_item.get("metadata") or {})
                prev_count = int(metadata.get("followup_attempt_count") or 0)
                metadata["followup_sent_at"] = now_iso
                metadata["followup_attempt_count"] = prev_count + 1
                metadata["followup_template"] = template_id
                metadata["followup_to"] = to_email
                metadata["followup_method"] = "sent" if sent else "draft"
                if sent:
                    metadata["followup_message_id"] = sent_message_id
                    metadata.pop("pending_followup", None)
                else:
                    metadata["pending_followup"] = {
                        "to": to_email,
                        "subject": subject,
                        "created_at": now_iso,
                        "draft_id": draft_id,
                    }
                db.update_ap_item(ap_item_id, metadata=json.dumps(metadata))
        except Exception as meta_exc:
            logger.warning("[APSkill] Could not update AP item metadata: %s", meta_exc)

        return {
            "ok": True,
            "sent": sent,
            "draft_created": bool(draft_id),
            "draft_id": draft_id,
            "message_id": sent_message_id,
            "to": to_email,
            "missing_info": [m.value for m in missing_info],
        }
    except Exception as exc:
        logger.warning("[APSkill] request_vendor_info failed: %s", exc)
        return {"ok": False, "draft_created": False, "sent": False, "error": str(exc)}


async def _handle_resolve_exception(
    invoice_payload: Dict[str, Any],
    exception_code: str = "",
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Attempt to auto-resolve a common AP exception.

    Strategies by exception type:
    - missing PO: search ERP for matching PO by vendor+amount, auto-attach
    - wrong amount / amount anomaly: calculate discrepancy, suggest correction
    - vendor mismatch: suggest correct vendor from known aliases
    - missing approval: identify correct approver, auto-route
    - duplicate invoice: link to original, suggest merge or reject
    - erp_vendor_not_found: attempt to create vendor in ERP
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.exception_resolver import get_exception_resolver

        db = get_db()
        ap_item_id = (
            invoice_payload.get("id")
            or invoice_payload.get("ap_item_id")
            or ""
        )

        # If we have an AP item ID, read fresh data to get exception_code
        ap_item: Dict[str, Any] = {}
        if ap_item_id:
            ap_item = db.get_ap_item(ap_item_id) or {}

        # Fall back to the invoice_payload if no AP item found
        if not ap_item:
            ap_item = dict(invoice_payload)

        # Determine the exception code: explicit param > AP item column > metadata
        resolved_code = exception_code or ap_item.get("exception_code") or ""
        if not resolved_code:
            try:
                meta = json.loads(ap_item.get("metadata") or "{}") if isinstance(ap_item.get("metadata"), str) else (ap_item.get("metadata") or {})
                resolved_code = meta.get("exception_code") or ""
            except Exception as exc:
                logger.debug("Metadata parse for exception_code failed: %s", exc)

        if not resolved_code:
            return {
                "ok": True,
                "resolved": False,
                "reason": "no_exception_code",
                "suggestion": "No exception to resolve.",
            }

        resolver = get_exception_resolver(organization_id)
        result = await resolver.resolve(ap_item, resolved_code)

        return {"ok": True, **result}
    except Exception as exc:
        logger.warning("[APSkill] resolve_exception failed: %s", exc)
        return {"ok": False, "resolved": False, "error": str(exc)}


async def _handle_check_payment_readiness(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Check if a posted invoice is ready for payment and surface the payment record.

    This tool NEVER triggers a payment.  It only looks up the payment tracking
    record and returns its current status.
    """
    try:
        from clearledgr.core.database import get_db

        db = get_db()
        ap_item_id = (
            invoice_payload.get("id")
            or invoice_payload.get("ap_item_id")
            or ""
        )
        gmail_id = invoice_payload.get("gmail_id") or invoice_payload.get("thread_id") or ""

        # Try to find the AP item to check state
        ap_item = None
        if ap_item_id:
            ap_item = db.get_ap_item(ap_item_id)
        if not ap_item and gmail_id:
            ap_item = db.get_invoice_status(gmail_id)

        if not ap_item:
            return {
                "ok": True,
                "payment_ready": False,
                "reason": "ap_item_not_found",
            }

        state = str(ap_item.get("state") or "").strip().lower()
        if state not in {"posted_to_erp", "closed"}:
            return {
                "ok": True,
                "payment_ready": False,
                "reason": "not_posted",
                "current_state": state,
            }

        # Look up payment record
        resolved_ap_id = ap_item.get("id") or ap_item_id
        payment = db.get_payment_by_ap_item(resolved_ap_id) if resolved_ap_id else None

        if not payment:
            return {
                "ok": True,
                "payment_ready": True,
                "reason": "posted_but_no_payment_record",
                "current_state": state,
                "vendor_name": ap_item.get("vendor_name"),
                "amount": ap_item.get("amount"),
                "currency": ap_item.get("currency") or "USD",
                "due_date": ap_item.get("due_date"),
                "erp_reference": ap_item.get("erp_reference"),
            }

        result = {
            "ok": True,
            "payment_ready": True,
            "payment_id": payment.get("id"),
            "payment_status": payment.get("status"),
            "vendor_name": payment.get("vendor_name"),
            "amount": payment.get("amount"),
            "currency": payment.get("currency") or "USD",
            "due_date": payment.get("due_date"),
            "erp_reference": payment.get("erp_reference"),
            "payment_method": payment.get("payment_method"),
            "scheduled_date": payment.get("scheduled_date"),
        }

        # Enrich with completion/partial payment details from metadata
        try:
            import json as _json
            meta = _json.loads(ap_item.get("metadata") or "{}") if isinstance(ap_item.get("metadata"), str) else (ap_item.get("metadata") or {})
        except Exception:
            meta = {}

        payment_status = payment.get("status") or ""
        if payment_status == "completed":
            result["completed_date"] = payment.get("completed_date")
            result["payment_reference"] = payment.get("payment_reference")
            result["payment_completed_at"] = meta.get("payment_completed_at")
        elif payment_status == "partial":
            result["paid_amount"] = payment.get("paid_amount")
            result["remaining_balance"] = meta.get("payment_remaining")

        return result
    except Exception as exc:
        logger.warning("[APSkill] check_payment_readiness failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Spend analysis handler
# ---------------------------------------------------------------------------

async def _handle_analyze_spending(
    period_days: int = 30,
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Run portfolio-level spend analysis for the organization."""
    try:
        from clearledgr.services.spend_analysis import get_spend_analysis_service

        service = get_spend_analysis_service(organization_id)
        result = service.analyze(period_days=period_days)
        return {"ok": True, **result}
    except Exception as exc:
        logger.warning("[APSkill] analyze_spending failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# APSkill
# ---------------------------------------------------------------------------

class APSkill(FinanceSkill):
    """AP domain skill — wraps InvoiceWorkflowService for use in the planning loop."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id

    @property
    def skill_name(self) -> str:
        return "ap_invoice_processing"

    def get_tools(self) -> List[AgentTool]:
        return [
            AgentTool(
                name="enrich_with_context",
                description=(
                    "Fetch vendor history, prior approval decisions, and GL correction "
                    "suggestions for this invoice. Call this first to understand the vendor."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        }
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_enrich_with_context,
            ),
            AgentTool(
                name="run_validation_gate",
                description=(
                    "Run deterministic validation: confidence threshold, PO number check, "
                    "and budget gate. If this fails, escalate to human — do not auto-approve."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        }
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_run_validation_gate,
            ),
            AgentTool(
                name="get_ap_decision",
                description=(
                    "Call the AP decision AI with full vendor context to get a recommendation: "
                    "approve, needs_info, escalate, or reject. Use vendor_context from "
                    "enrich_with_context if available."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        },
                        "vendor_context": {
                            "type": "object",
                            "description": "Vendor enrichment from enrich_with_context (optional).",
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_get_ap_decision,
            ),
            AgentTool(
                name="execute_routing",
                description=(
                    "Route the invoice based on the AP decision recommendation. "
                    "If recommendation is 'approve' with high confidence, auto-approves and posts to ERP. "
                    "Otherwise routes for human review (pauses for HITL). "
                    "Always call this as the final step."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        },
                        "recommendation": {
                            "type": "string",
                            "enum": ["approve", "needs_info", "escalate", "reject"],
                            "description": "The AP decision recommendation.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score 0.0–1.0 from get_ap_decision.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for the routing decision.",
                        },
                        "risk_flags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Risk flags from get_ap_decision.",
                        },
                        "info_needed": {
                            "type": "string",
                            "description": "If needs_info: the question to ask the vendor.",
                        },
                    },
                    "required": ["invoice_payload", "recommendation"],
                },
                handler=_handle_execute_routing,
            ),
            AgentTool(
                name="request_vendor_info",
                description=(
                    "Draft a Gmail follow-up email to the vendor when information is "
                    "missing (PO number, amount clarification, etc.). Only call this "
                    "when get_ap_decision returns 'needs_info'. Optionally pass a "
                    "'question' string for Claude-generated clarification."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        },
                        "question": {
                            "type": "string",
                            "description": "Specific question to ask the vendor (optional).",
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_request_vendor_info,
            ),
            AgentTool(
                name="verify_erp_posting",
                description=(
                    "Verify that a posted invoice actually exists in the connected ERP. "
                    "Call this after execute_routing returns a posted status to confirm "
                    "the bill landed in QuickBooks/Xero/SAP/NetSuite."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict (must include invoice_number or erp_reference).",
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_verify_erp_posting,
            ),
            AgentTool(
                name="resolve_exception",
                description=(
                    "Attempt to auto-resolve a common AP exception. Supported types: "
                    "missing PO (po_required_missing, missing_required_field_po_number), "
                    "amount anomaly (amount_anomaly_high, amount_anomaly_moderate), "
                    "ERP vendor not found (erp_vendor_not_found), "
                    "duplicate invoice (erp_duplicate_bill, duplicate_invoice), "
                    "low confidence (confidence_field_review_required), "
                    "currency mismatch, vendor mismatch, vendor unresponsive, "
                    "posting exhausted. If an exception is flagged, call this to "
                    "attempt auto-resolution before routing."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice/AP item data dict (must include id).",
                        },
                        "exception_code": {
                            "type": "string",
                            "description": (
                                "The exception code to resolve. If omitted, reads from the "
                                "AP item's exception_code column."
                            ),
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_resolve_exception,
            ),
            AgentTool(
                name="check_payment_readiness",
                description=(
                    "Check if a posted invoice is ready for payment and return the "
                    "payment tracking record. This NEVER triggers a payment — it only "
                    "surfaces the current payment status and due date. Call this after "
                    "verify_erp_posting to see if a payment record was created."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict (must include id or gmail_id).",
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_check_payment_readiness,
            ),
            AgentTool(
                name="analyze_spending",
                description=(
                    "Run portfolio-level spend analysis: top vendors, GL breakdown, "
                    "monthly trends, budget utilization, and anomaly detection. "
                    "Call this when the user asks about spend patterns, budgets, or "
                    "vendor cost trends. Does NOT modify any data."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "period_days": {
                            "type": "integer",
                            "description": "Number of days to analyze (default 30).",
                            "default": 30,
                        },
                    },
                    "required": [],
                },
                handler=_handle_analyze_spending,
            ),
        ]

    def build_system_prompt(self, task: AgentTask) -> str:
        payload = task.payload
        invoice = payload.get("invoice", {})
        vendor = invoice.get("vendor_name", "Unknown")
        amount = invoice.get("amount", 0)
        currency = invoice.get("currency", "USD")
        confidence = invoice.get("confidence", 0)

        # --- Cross-invoice warnings (duplicate / anomaly) ---
        cross_invoice_warning = ""
        try:
            from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer

            analyzer = get_cross_invoice_analyzer(task.organization_id)
            analysis = analyzer.analyze(
                vendor=vendor,
                amount=amount,
                invoice_number=invoice.get("invoice_number"),
                invoice_date=invoice.get("due_date"),
                currency=currency,
                gmail_id=invoice.get("gmail_id"),
            )
            if analysis.has_issues:
                warnings = []
                for dup in analysis.duplicates:
                    warnings.append(f"  - DUPLICATE RISK: {dup.message} (severity={dup.severity})")
                for anomaly in analysis.anomalies:
                    warnings.append(f"  - ANOMALY ({anomaly.anomaly_type}): {anomaly.message}")
                if warnings:
                    warnings.append("  Consider escalating if duplicates are present.")
                    cross_invoice_warning = (
                        "\n\nCross-invoice warnings:\n" + "\n".join(warnings)
                    )
        except Exception:
            pass  # non-critical

        return f"""You are the Clearledgr AP agent processing an invoice for approval.

Invoice summary:
- Vendor: {vendor}
- Amount: {currency} {amount:,.2f}
- Extraction confidence: {confidence:.0%}
- Organization: {task.organization_id}{cross_invoice_warning}

Your job: process this invoice through AP review using the available tools.

Recommended sequence:
1. Call enrich_with_context to understand the vendor relationship
2. Call run_validation_gate to check hard rules
3. If an exception is flagged (exception_code on the AP item), call resolve_exception to attempt auto-resolution before routing
4. Call get_ap_decision with the vendor context to get a recommendation
5. Call execute_routing with the recommendation to complete processing
6. If recommendation is "needs_info", call request_vendor_info to draft a follow-up

Rules:
- NEVER skip run_validation_gate — it is a hard guardrail
- If validation gate fails, set recommendation to "escalate" in execute_routing
- If an exception is flagged, call resolve_exception to attempt auto-resolution before routing
- NEVER auto-approve without calling get_ap_decision first
- NEVER reject without human sign-off — use "escalate" instead
- Only call request_vendor_info when the decision is "needs_info"
- After calling execute_routing (and optionally request_vendor_info), you are done — do not call more tools
- If cross-invoice warnings mention duplicates, factor that into your decision — prefer "escalate"
- Use analyze_spending when asked about spend patterns, budget status, or vendor cost trends — it is read-only

When you are finished, respond with a brief summary of what was decided and why."""
