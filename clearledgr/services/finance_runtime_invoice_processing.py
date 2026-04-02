"""Invoice-processing helpers extracted from the finance runtime."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


async def execute_ap_invoice_processing(
    runtime: Any,
    invoice_payload: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    *,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run AP invoice processing through the canonical finance runtime path."""
    from clearledgr.services.invoice_workflow import InvoiceData, get_invoice_workflow

    invoice = invoice_payload if isinstance(invoice_payload, dict) else {}
    gmail_thread_id = runtime._invoice_thread_id(invoice)
    gmail_message_id = runtime._invoice_message_id(invoice)
    runtime_reference = gmail_thread_id or gmail_message_id
    resolved_idempotency_key = str(idempotency_key or "").strip() or (
        f"invoice:{runtime_reference}" if runtime_reference else None
    )
    resolved_correlation_id = (
        str(correlation_id or "").strip()
        or str(invoice.get("correlation_id") or "").strip()
        or None
    )
    invoice_org = str(invoice.get("organization_id") or runtime.organization_id or "default").strip() or "default"
    attachment_list = attachments if isinstance(attachments, list) else []
    attachment_url = ""
    attachment_names: List[str] = []
    source_conflicts = invoice.get("source_conflicts") if isinstance(invoice.get("source_conflicts"), list) else []
    blocking_conflicts = [
        conflict for conflict in source_conflicts
        if isinstance(conflict, dict) and bool(conflict.get("blocking"))
    ]
    confidence_blockers = invoice.get("confidence_blockers") if isinstance(invoice.get("confidence_blockers"), list) else []
    if not confidence_blockers:
        gate = invoice.get("confidence_gate") if isinstance(invoice.get("confidence_gate"), dict) else {}
        confidence_blockers = gate.get("confidence_blockers") if isinstance(gate.get("confidence_blockers"), list) else []
    requires_field_review = bool(
        invoice.get("requires_field_review")
        or invoice.get("requires_extraction_review")
        or confidence_blockers
        or blocking_conflicts
    )
    if attachment_list:
        first_attachment = attachment_list[0] if isinstance(attachment_list[0], dict) else {}
        attachment_url = str(
            first_attachment.get("url")
            or first_attachment.get("attachment_url")
            or ""
        ).strip()
        for attachment in attachment_list:
            if not isinstance(attachment, dict):
                continue
            name = str(attachment.get("filename") or attachment.get("name") or "").strip()
            if name:
                attachment_names.append(name)
    seeded_item = runtime._seed_ap_item_for_invoice_processing(
        {
            **invoice,
            "organization_id": invoice_org,
            "thread_id": gmail_thread_id or invoice.get("thread_id"),
            "message_id": gmail_message_id or invoice.get("message_id"),
            "attachment_url": attachment_url or invoice.get("attachment_url"),
            "attachment_count": len(attachment_list),
            "attachment_names": attachment_names,
            "has_attachment": bool(attachment_list),
        },
        correlation_id=resolved_correlation_id,
    )

    amount_value = runtime._safe_float(invoice.get("amount"))
    confidence_value = runtime._safe_float(invoice.get("confidence"))

    invoice_data = InvoiceData(
        gmail_id=runtime_reference or f"invoice-{uuid.uuid4().hex[:10]}",
        subject=str(invoice.get("subject") or "").strip() or "Invoice",
        sender=str(invoice.get("sender") or "").strip() or "unknown@unknown.local",
        vendor_name=runtime._resolved_vendor_name(
            invoice.get("vendor_name") or invoice.get("vendor"),
            invoice.get("sender"),
        ) or "Unknown vendor",
        amount=amount_value,
        currency=str(invoice.get("currency") or "USD").strip() or "USD",
        invoice_number=str(invoice.get("invoice_number") or "").strip() or None,
        due_date=str(invoice.get("due_date") or "").strip() or None,
        po_number=str(invoice.get("po_number") or "").strip() or None,
        confidence=confidence_value,
        attachment_url=attachment_url or None,
        organization_id=invoice_org,
        user_id=str(invoice.get("user_id") or runtime.actor_id or "").strip() or None,
        invoice_text=str(invoice.get("invoice_text") or "").strip() or None,
        correlation_id=resolved_correlation_id,
        field_confidences=invoice.get("field_confidences") if isinstance(invoice.get("field_confidences"), dict) else None,
    )

    autonomy_policy = runtime.ap_autonomy_policy(
        vendor_name=invoice_data.vendor_name,
        action="auto_approve_post",
        autonomous_requested=True,
        ap_item=seeded_item,
    )
    autonomy_threshold = runtime.ap_auto_approve_threshold()
    shadow_decision = runtime._build_shadow_decision_proposal(
        invoice=invoice,
        vendor_name=invoice_data.vendor_name,
        amount=invoice_data.amount,
        confidence=invoice_data.confidence,
        requires_field_review=requires_field_review,
        autonomy_policy=autonomy_policy,
        auto_post_threshold=autonomy_threshold,
    )
    autonomy_downgraded_auto_post = False
    if not autonomy_policy.get("autonomous_allowed") and invoice_data.confidence >= autonomy_threshold:
        invoice_data.confidence = max(0.0, autonomy_threshold - 0.01)
        autonomy_downgraded_auto_post = True

    if seeded_item and hasattr(runtime.db, "update_ap_item_metadata_merge"):
        try:
            runtime.db.update_ap_item_metadata_merge(
                str(seeded_item.get("id") or "").strip(),
                {
                    "autonomy_policy": autonomy_policy,
                    "autonomy_mode": autonomy_policy.get("mode"),
                    "autonomy_reason_codes": autonomy_policy.get("reason_codes") or [],
                    "autonomy_auto_post_downgraded": bool(autonomy_downgraded_auto_post),
                    "shadow_decision": shadow_decision,
                },
            )
        except Exception:
            logger.exception("Could not persist invoice autonomy metadata")

    if requires_field_review:
        ap_item_id = str(seeded_item.get("id") or "").strip() if seeded_item else ""
        review_exception_code = str(invoice.get("exception_code") or "").strip() or (
            "field_conflict" if blocking_conflicts else "field_review_required"
        )
        review_exception_severity = str(invoice.get("exception_severity") or "").strip() or (
            "high" if blocking_conflicts else "medium"
        )
        if seeded_item and hasattr(runtime.db, "update_ap_item"):
            merged_metadata = {
                **runtime._parse_json_dict(seeded_item.get("metadata")),
                "requires_field_review": True,
                "processing_status": "field_review_required",
                "confidence_blockers": confidence_blockers,
                "source_conflicts": source_conflicts,
                "conflict_actions": invoice.get("conflict_actions") if isinstance(invoice.get("conflict_actions"), list) else [],
                "exception_code": review_exception_code,
                "exception_severity": review_exception_severity,
            }
            try:
                runtime.db.update_ap_item(
                    ap_item_id,
                    exception_code=review_exception_code,
                    exception_severity=review_exception_severity,
                    field_confidences=invoice.get("field_confidences") if isinstance(invoice.get("field_confidences"), dict) else None,
                    metadata=merged_metadata,
                )
            except Exception:
                logger.exception("Could not persist field review blocker state")
        response = {
            "status": "blocked",
            "reason": "field_review_required",
            "detail": "Invoice extraction has unresolved field blockers; workflow execution was not performed.",
            "execution_mode": "finance_agent_runtime",
            "requires_field_review": True,
            "confidence_blockers": confidence_blockers,
            "source_conflicts": source_conflicts,
            "conflict_actions": invoice.get("conflict_actions") if isinstance(invoice.get("conflict_actions"), list) else [],
            "autonomy_policy": autonomy_policy,
        }
        if seeded_item:
            response.setdefault("ap_item_id", seeded_item.get("id"))
            response.setdefault("email_id", runtime_reference or seeded_item.get("thread_id"))
        if resolved_idempotency_key:
            response.setdefault("idempotency_key", resolved_idempotency_key)
        if resolved_correlation_id:
            response.setdefault("correlation_id", resolved_correlation_id)
        if ap_item_id:
            audit_row = runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="ap_invoice_processing_blocked",
                reason="ap_invoice_processing_field_review_required",
                metadata={"response": response},
                correlation_id=resolved_correlation_id,
                idempotency_key=resolved_idempotency_key,
                skill_id="ap_v1",
            )
            response["audit_event_id"] = (audit_row or {}).get("id")
        return response

    response: Dict[str, Any]
    try:
        workflow = get_invoice_workflow(invoice_org)
        response = await workflow.process_new_invoice(invoice_data)
        response = dict(response or {})
        response["execution_mode"] = "finance_agent_runtime"
        response.setdefault("agent_status", "completed")
        if response.get("status") in {"pending_approval", "needs_info"}:
            response["agent_status"] = "awaiting_human"
        elif response.get("status") in {"error", "failed"}:
            response["agent_status"] = "failed"
        if seeded_item and hasattr(runtime.db, "update_ap_item_metadata_merge"):
            runtime.db.update_ap_item_metadata_merge(
                str(seeded_item.get("id") or "").strip(),
                {
                    "processing_status": response.get("status") or "processed",
                    "last_runtime_execution_mode": "finance_agent_runtime",
                },
            )
    except Exception as workflow_exc:
        logger.error(
            "[FinanceAgentRuntime] invoice workflow execution failed closed: %s",
            workflow_exc,
        )
        if seeded_item and hasattr(runtime.db, "update_ap_item"):
            ap_item_id = str(seeded_item.get("id") or "").strip()
            merged_metadata = {
                **runtime._parse_json_dict(seeded_item.get("metadata")),
                "exception_code": "workflow_execution_failed",
                "exception_severity": "high",
                "processing_status": "workflow_execution_failed",
                "workflow_error": str(workflow_exc),
            }
            try:
                runtime.db.update_ap_item(
                    ap_item_id,
                    last_error=str(workflow_exc),
                    metadata=merged_metadata,
                )
            except Exception:
                logger.exception("Could not persist workflow execution failure state")
        response = {
            "status": "error",
            "reason": "invoice_workflow_unavailable",
            "detail": "AP workflow execution failed; no workflow execution was completed.",
            "execution_mode": "finance_agent_runtime",
            "agent_status": "failed",
            "autonomy_policy": autonomy_policy,
        }

    if seeded_item:
        response.setdefault("ap_item_id", seeded_item.get("id"))
        response.setdefault("email_id", runtime_reference or seeded_item.get("thread_id"))
    if resolved_idempotency_key:
        response.setdefault("idempotency_key", resolved_idempotency_key)
    if resolved_correlation_id:
        response.setdefault("correlation_id", resolved_correlation_id)
    response.setdefault("autonomy_policy", autonomy_policy)
    if autonomy_downgraded_auto_post:
        response.setdefault("autonomy_auto_post_downgraded", True)
    ap_item_id = str(response.get("ap_item_id") or (seeded_item or {}).get("id") or "").strip()
    if ap_item_id:
        status_token = str(response.get("status") or "unknown").strip().lower()
        event_type = "ap_invoice_processing_completed"
        if status_token in {"error", "failed"}:
            event_type = "ap_invoice_processing_failed"
        audit_row = runtime._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type=event_type,
            reason=f"ap_invoice_processing_{status_token or 'unknown'}",
            metadata={"response": response},
            correlation_id=resolved_correlation_id,
            idempotency_key=resolved_idempotency_key,
            skill_id="ap_v1",
        )
        response["audit_event_id"] = (audit_row or {}).get("id")
    return response
