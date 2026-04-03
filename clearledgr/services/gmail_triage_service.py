"""Inline Gmail triage orchestration for the extension adapter."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from clearledgr.services.agent_reflection import get_agent_reflection
from clearledgr.services.audit_trail import AuditEventType, get_audit_trail
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer
from clearledgr.services.policy_compliance import get_policy_compliance
from clearledgr.services.priority_detection import get_priority_detection
from clearledgr.services.proactive_insights import get_proactive_insights
from clearledgr.services.vendor_intelligence import get_vendor_intelligence
from clearledgr.workflows.gmail_activities import (
    classify_email_activity,
    extract_email_data_activity,
)


async def run_inline_gmail_triage(
    *,
    payload: Dict[str, Any],
    org_id: str,
    combined_text: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    agent_reasoning_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the non-Temporal Gmail triage flow and return the triage payload."""
    request_attachments = list(attachments or [])
    trail = get_audit_trail(org_id)
    trail.log(
        invoice_id=payload.get("email_id"),
        event_type=AuditEventType.RECEIVED,
        summary=f"Email received from {payload.get('sender') or 'unknown'}",
        details={"subject": payload.get("subject"), "sender": payload.get("sender")},
    )

    classification = await classify_email_activity(payload)
    trail.log(
        invoice_id=payload.get("email_id"),
        event_type=AuditEventType.CLASSIFIED,
        summary=f"Classified as {classification.get('type', 'UNKNOWN')}",
        confidence=classification.get("confidence", 0),
        reasoning=classification.get("reason", "AI classification"),
    )

    if classification.get("type") == "NOISE":
        return {
            "email_id": payload.get("email_id"),
            "classification": classification,
            "action": "skipped",
        }

    extraction = await extract_email_data_activity({**payload, "classification": classification})

    # --- Multi-invoice handling ---
    # When the parser detects multiple distinct invoices in the same email
    # (e.g. separate PDF attachments), run the triage pipeline once per
    # sub-invoice and return a combined result so the caller can create
    # one AP item per invoice, all linked to the same source thread.
    if extraction.get("multiple_invoices") and isinstance(extraction.get("invoices"), list):
        sub_invoices = extraction["invoices"]
        if len(sub_invoices) > 1:
            logger.info(
                "Multi-invoice email detected for email_id=%s: %d invoices",
                payload.get("email_id"),
                len(sub_invoices),
            )
            trail.log(
                invoice_id=payload.get("email_id"),
                event_type=AuditEventType.EXTRACTED,
                summary=f"Multi-invoice email: {len(sub_invoices)} distinct invoices detected",
                details={"invoice_count": len(sub_invoices)},
            )

            sub_results = []
            for idx, sub_inv in enumerate(sub_invoices):
                # Build a per-invoice extraction by overlaying sub-invoice
                # fields onto the shared extraction base.
                sub_extraction = dict(extraction)
                sub_extraction.pop("invoices", None)
                sub_extraction.pop("multiple_invoices", None)
                sub_extraction["vendor"] = sub_inv.get("vendor") or extraction.get("vendor")
                sub_extraction["amount"] = sub_inv.get("amount") if sub_inv.get("amount") is not None else extraction.get("amount")
                sub_extraction["total_amount"] = sub_extraction["amount"]
                sub_extraction["currency"] = sub_inv.get("currency") or extraction.get("currency")
                sub_extraction["invoice_number"] = sub_inv.get("invoice_number") or extraction.get("invoice_number")
                sub_extraction["due_date"] = sub_inv.get("due_date") or extraction.get("due_date")
                sub_extraction["invoice_date"] = sub_inv.get("invoice_date") or extraction.get("invoice_date")
                sub_extraction["confidence"] = sub_inv.get("confidence", extraction.get("confidence", 0))
                sub_extraction["attachment_name"] = sub_inv.get("attachment_name")
                sub_extraction["sub_invoice_index"] = idx

                sub_results.append({
                    "email_id": payload.get("email_id"),
                    "classification": classification,
                    "extraction": sub_extraction,
                    "action": "triaged",
                    "ai_powered": True,
                    "sub_invoice_index": idx,
                })

            return {
                "email_id": payload.get("email_id"),
                "classification": classification,
                "extraction": extraction,
                "action": "triaged",
                "ai_powered": True,
                "multiple_invoices": True,
                "invoice_count": len(sub_invoices),
                "attachment_count": extraction.get("attachment_count", 0),
                "invoices": sub_results,
            }

    # C7: Validate that critical extraction fields are present
    if not extraction.get("vendor") or extraction.get("amount") is None:
        extraction["extraction_incomplete"] = True
        missing = []
        if not extraction.get("vendor"):
            missing.append("vendor_name")
        if extraction.get("amount") is None:
            missing.append("amount")
        logger.warning(
            "Extraction incomplete for email_id=%s: missing %s",
            payload.get("email_id"), ", ".join(missing),
        )

    extracted_amount = extraction.get("amount")
    amount_display = (
        f"{float(extracted_amount):,.2f}"
        if isinstance(extracted_amount, (int, float))
        else "Unknown"
    )
    trail.log(
        invoice_id=payload.get("email_id"),
        event_type=AuditEventType.EXTRACTED,
        summary=f"Extracted: {extraction.get('vendor', 'Unknown')} ${amount_display}",
        confidence=extraction.get("confidence", 0),
        vendor=extraction.get("vendor"),
        amount=extraction.get("amount"),
    )

    reflection = get_agent_reflection()
    original_text = f"{payload.get('subject') or ''} {payload.get('snippet') or ''} {payload.get('body') or ''}"
    reflection_result = reflection.reflect_on_extraction(extraction, original_text)
    if reflection_result.corrections_made:
        extraction = reflection_result.final_extraction
        trail.log(
            invoice_id=payload.get("email_id"),
            event_type=AuditEventType.VALIDATED,
            summary=f"Self-corrected {len(reflection_result.corrections_made)} field(s)",
            reasoning="; ".join(reflection_result.reflection_notes),
        )

    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info
        if not extraction.get("gl_code") and vendor_info.get("suggested_gl"):
            extraction["gl_code"] = vendor_info["suggested_gl"]
            extraction["gl_source"] = "vendor_intelligence"

    # C13: Wrap policy compliance in try/except to prevent cascade failures
    invoice_for_policy = {
        "vendor": extraction.get("vendor") or "",
        "amount": extraction.get("amount", 0),
        "category": extraction.get("category") or "",
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    policy_result = None
    try:
        policy_service = get_policy_compliance(org_id)
        policy_result = policy_service.check(invoice_for_policy)
        extraction["policy_compliance"] = policy_result.to_dict()
        if not policy_result.compliant:
            trail.log(
                invoice_id=payload.get("email_id"),
                event_type=AuditEventType.POLICY_CHECK,
                summary=f"Policy: {len(policy_result.violations)} requirement(s)",
                details={"violations": [v.message for v in policy_result.violations]},
            )
    except Exception as policy_exc:
        logger.warning("Policy compliance check failed for email_id=%s: %s", payload.get("email_id"), policy_exc)
        extraction["policy_compliance"] = {"compliant": True, "violations": [], "error": "check_failed"}

    priority_service = get_priority_detection(org_id)
    invoice_for_priority = {
        "id": payload.get("email_id"),
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "due_date": extraction.get("due_date"),
        "created_at": extraction.get("created_at"),
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    priority = priority_service.assess(invoice_for_priority)
    extraction["priority"] = priority.to_dict()

    analyzer = get_cross_invoice_analyzer(org_id)
    analysis = analyzer.analyze(
        vendor=extraction.get("vendor", ""),
        amount=extraction.get("amount", 0),
        invoice_number=extraction.get("invoice_number"),
        invoice_date=extraction.get("invoice_date"),
        gmail_id=payload.get("email_id"),
    )
    extraction["cross_invoice_analysis"] = analysis.to_dict()
    duplicate_alerts = getattr(analysis, "duplicates", []) or []
    if duplicate_alerts:
        trail.log(
            invoice_id=payload.get("email_id"),
            event_type=AuditEventType.DUPLICATE_CHECK,
            summary="Potential duplicate detected",
            details={"duplicates": [getattr(d, "invoice_id", None) for d in duplicate_alerts]},
        )

    # C13: Wrap budget check in try/except to prevent cascade failures
    budget_checks = []
    try:
        budget_service = get_budget_awareness(org_id)
        budget_checks = budget_service.check_invoice(invoice_for_policy)
        if budget_checks:
            extraction["budget_impact"] = [b.to_dict() for b in budget_checks]
            for check in budget_checks:
                if check.after_approval_status.value in ["critical", "exceeded"]:
                    trail.log(
                        invoice_id=payload.get("email_id"),
                        event_type=AuditEventType.ANALYZED,
                        summary=f"Budget alert: {check.budget.name} at {check.after_approval_percent:.0f}%",
                    )
    except Exception as budget_exc:
        logger.warning("Budget check failed for email_id=%s: %s", payload.get("email_id"), budget_exc)
        budget_checks = []

    insights_service = get_proactive_insights(org_id)
    insights = insights_service.analyze_after_invoice(invoice_for_priority)
    if insights:
        extraction["insights"] = [
            {"title": insight.title, "description": insight.description, "severity": insight.severity}
            for insight in insights
        ]

    trail.log(
        invoice_id=payload.get("email_id"),
        event_type=AuditEventType.DECISION_MADE,
        summary=f"Ready for processing - Priority: {priority.priority.label}",
        confidence=extraction.get("confidence", 0),
        reasoning=(
            f"Vendor: {'known' if vendor_info else 'new'}, "
            f"Policy: {'compliant' if (policy_result and policy_result.compliant) else 'requirements'}, "
            f"Duplicates: {len(duplicate_alerts)}"
        ),
    )

    result = {
        "email_id": payload.get("email_id"),
        "classification": classification,
        "extraction": extraction,
        "action": "triaged",
        "ai_powered": True,
        "intelligence": {
            "vendor_known": vendor_info is not None,
            "vendor_info": vendor_info,
            "policy_compliant": policy_result.compliant if policy_result else True,
            "policy_requirements": [v.message for v in policy_result.violations] if policy_result else [],
            "required_approvers": policy_result.required_approvers if policy_result else [],
            "priority": priority.priority.value,
            "priority_label": priority.priority.label,
            "days_until_due": priority.days_until_due,
            "alerts": priority.alerts,
            "potential_duplicates": len(duplicate_alerts),
            "anomalies": [getattr(a, "anomaly_type", None) for a in (getattr(analysis, "anomalies", []) or [])],
            "budget_warnings": [
                check.warning_message for check in budget_checks if check.warning_message
            ] if budget_checks else [],
            "insights": [insight.title for insight in insights] if insights else [],
            "self_verified": reflection_result.self_verified,
        },
    }

    # C13: Wrap agent reasoning in try/except to prevent cascade failures
    if callable(agent_reasoning_fn):
        try:
            result = agent_reasoning_fn(
                result=result,
                org_id=org_id,
                combined_text=combined_text,
                attachments=request_attachments,
            )
        except Exception as reasoning_exc:
            logger.warning("Agent reasoning failed for email_id=%s: %s", payload.get("email_id"), reasoning_exc)
            result["intelligence"]["agent_reasoning_error"] = str(reasoning_exc)

    return result
