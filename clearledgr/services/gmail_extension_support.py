"""Support functions for Gmail extension adapters."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from clearledgr.core.ap_confidence import (
    evaluate_critical_field_confidence,
    extract_field_confidences,
)


logger = logging.getLogger(__name__)


def apply_intelligence(result: Dict[str, Any], org_id: str, email_id: str) -> Dict[str, Any]:
    """Apply vendor, policy, and priority enrichment to a triage result."""
    from clearledgr.services.policy_compliance import get_policy_compliance
    from clearledgr.services.priority_detection import get_priority_detection
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence

    extraction = result.get("extraction", {})

    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info

    policy_service = get_policy_compliance(org_id)
    policy_result = policy_service.check(
        {
            "vendor": extraction.get("vendor"),
            "amount": extraction.get("amount", 0),
            "vendor_intelligence": vendor_info or {},
        }
    )
    extraction["policy_compliance"] = policy_result.to_dict()

    priority_service = get_priority_detection(org_id)
    priority = priority_service.assess(
        {
            "id": email_id,
            "vendor": extraction.get("vendor"),
            "amount": extraction.get("amount", 0),
            "due_date": extraction.get("due_date"),
        }
    )
    extraction["priority"] = priority.to_dict()

    result["extraction"] = extraction
    result["intelligence"] = {
        "vendor_known": vendor_info is not None,
        "policy_compliant": policy_result.compliant,
        "priority": priority.priority.value,
        "priority_label": priority.priority.label,
    }
    return result


def merge_agent_extraction(
    extraction: Dict[str, Any],
    agent_extraction: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing extraction fields from agent reasoning output."""
    if not agent_extraction:
        return extraction

    merged = dict(extraction or {})

    def _set_if_missing(key: str, value: Any) -> None:
        if value is None or value == "":
            return
        if merged.get(key) in (None, "", 0):
            merged[key] = value

    _set_if_missing("vendor", agent_extraction.get("vendor"))
    _set_if_missing("amount", agent_extraction.get("total_amount"))
    _set_if_missing("currency", agent_extraction.get("currency"))
    _set_if_missing("invoice_number", agent_extraction.get("invoice_number"))
    _set_if_missing("invoice_date", agent_extraction.get("invoice_date"))
    _set_if_missing("due_date", agent_extraction.get("due_date"))

    if not merged.get("line_items") and agent_extraction.get("line_items"):
        merged["line_items"] = agent_extraction.get("line_items")

    return merged


def apply_agent_reasoning(
    result: Dict[str, Any],
    org_id: str,
    combined_text: str,
    attachments: List[Dict[str, Any]],
    reasoning_agent_factory: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Run agent reasoning and merge decision + extraction."""
    if not combined_text and not attachments:
        return result

    try:
        if reasoning_agent_factory is None:
            from clearledgr.services.agent_reasoning import get_agent as get_reasoning_agent

            reasoning_agent_factory = get_reasoning_agent
        agent = reasoning_agent_factory(org_id)
        decision = agent.reason_about_invoice(combined_text, attachments)
    except Exception as exc:  # noqa: BLE001
        result.setdefault("agent_decision_error", str(exc))
        return result

    extraction = result.get("extraction") or {}
    extraction = merge_agent_extraction(extraction, decision.extraction or {})

    try:
        extraction_confidence = float(extraction.get("confidence") or 0.0)
        extraction["confidence"] = max(extraction_confidence, float(decision.confidence))
    except Exception:
        pass

    result["extraction"] = extraction
    result["agent_decision"] = decision.to_dict()
    return result


def pipeline_bucket_for_state(state: Any) -> str:
    """Map AP state to the Gmail extension pipeline bucket."""
    normalized = str(state or "").strip().lower()
    if normalized in {"new", "received", "validated"}:
        return "new"
    if normalized in {"needs_info", "needs_approval", "pending_approval"}:
        return "pending_approval"
    if normalized in {"approved", "ready_to_post"}:
        return "approved"
    if normalized in {"posted", "posted_to_erp", "closed"}:
        return "posted"
    if normalized in {"rejected"}:
        return "rejected"
    return "pending_approval"


def build_extension_pipeline(
    db: Any,
    organization_id: str,
    *,
    limit: int = 1000,
    build_item_fn: Optional[Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return extension pipeline grouped by Gmail-facing status buckets."""
    from clearledgr.services.ap_projection import build_worklist_items

    if build_item_fn is None:
        from clearledgr.services.ap_item_service import build_worklist_item

        build_item_fn = build_worklist_item
    items = db.list_ap_items(organization_id, limit=limit, prioritized=True)
    normalized_items = build_worklist_items(
        db,
        items,
        build_item=build_item_fn,
    )
    groups: Dict[str, List[Dict[str, Any]]] = {
        "new": [],
        "pending_approval": [],
        "approved": [],
        "posted": [],
        "rejected": [],
    }
    for normalized in normalized_items:
        bucket = pipeline_bucket_for_state(normalized.get("state"))
        groups.setdefault(bucket, []).append(normalized)
    return groups


def build_verify_confidence_payload(
    *,
    email_id: str,
    ap_item: Optional[Dict[str, Any]],
    extraction: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the field-confidence verification payload for the extension."""
    extraction_payload = extraction or {}
    metadata_payload = metadata or {}
    confidence_pct = 0
    mismatches: List[Dict[str, Any]] = []
    confidence_gate: Dict[str, Any] = {
        "threshold": 0.95,
        "threshold_pct": 95,
        "confidence_blockers": [],
        "requires_field_review": True,
    }

    if ap_item:
        confidence_pct = round((ap_item.get("confidence") or 0) * 100)
        request_field_confidences = extract_field_confidences(extraction_payload)
        stored_vendor = ap_item.get("vendor_name") or ""
        extracted_vendor = extraction_payload.get("vendor") or ""
        if extracted_vendor and stored_vendor and extracted_vendor.lower() != stored_vendor.lower():
            mismatches.append(
                {
                    "field": "vendor",
                    "extracted": extracted_vendor,
                    "expected": stored_vendor,
                    "severity": "medium",
                }
            )

        stored_amount = ap_item.get("amount")
        extracted_amount = extraction_payload.get("amount")
        if extracted_amount is not None and stored_amount is not None:
            try:
                if abs(float(extracted_amount) - float(stored_amount)) > 0.01:
                    mismatches.append(
                        {
                            "field": "amount",
                            "extracted": str(extracted_amount),
                            "expected": str(stored_amount),
                            "severity": "high",
                        }
                    )
            except (TypeError, ValueError):
                pass

        exception_code = ap_item.get("exception_code") or metadata_payload.get("exception_code")
        if exception_code:
            mismatches.append(
                {
                    "field": "exception",
                    "extracted": exception_code,
                    "expected": "none",
                    "severity": metadata_payload.get("exception_severity", "medium"),
                }
            )

        learned_threshold_overrides = None
        learned_profile_id = None
        learned_signal_count = 0
        organization_id = ap_item.get("organization_id") or metadata_payload.get("organization_id")
        vendor_name = extraction_payload.get("vendor") or ap_item.get("vendor_name")
        if organization_id and vendor_name:
            try:
                from clearledgr.services.finance_learning import get_finance_learning_service

                learned_adjustments = get_finance_learning_service(str(organization_id)).get_extraction_confidence_adjustments(
                    vendor_name=vendor_name,
                    sender_domain=metadata_payload.get("source_sender_domain") or ap_item.get("sender"),
                    document_type=(
                        extraction_payload.get("document_type")
                        or ap_item.get("document_type")
                        or metadata_payload.get("document_type")
                        or metadata_payload.get("email_type")
                    ),
                )
                learned_threshold_overrides = learned_adjustments.get("threshold_overrides") or None
                learned_profile_id = learned_adjustments.get("profile_id")
                learned_signal_count = int(learned_adjustments.get("signal_count") or 0)
            except Exception:
                learned_threshold_overrides = None
                learned_profile_id = None
                learned_signal_count = 0

        confidence_gate = evaluate_critical_field_confidence(
            overall_confidence=ap_item.get("confidence"),
            field_values={
                "vendor": extraction_payload.get("vendor") or ap_item.get("vendor_name"),
                "amount": extraction_payload.get("amount")
                if extraction_payload.get("amount") is not None else ap_item.get("amount"),
                "invoice_number": extraction_payload.get("invoice_number") or ap_item.get("invoice_number"),
                "due_date": extraction_payload.get("due_date") or ap_item.get("due_date"),
            },
            field_confidences=request_field_confidences or metadata_payload.get("field_confidences"),
            vendor_name=extraction_payload.get("vendor") or ap_item.get("vendor_name"),
            sender=ap_item.get("sender"),
            document_type=(
                extraction_payload.get("document_type")
                or ap_item.get("document_type")
                or metadata_payload.get("document_type")
                or metadata_payload.get("email_type")
            ),
            primary_source=extraction_payload.get("primary_source") or metadata_payload.get("primary_source"),
            has_attachment=bool(
                extraction_payload.get("has_invoice_attachment")
                or extraction_payload.get("attachment_url")
                or ap_item.get("has_attachment")
                or metadata_payload.get("has_attachment")
            ),
            sender_domain=metadata_payload.get("source_sender_domain"),
            learned_threshold_overrides=learned_threshold_overrides,
            learned_profile_id=learned_profile_id,
            learned_signal_count=learned_signal_count,
        )
    else:
        confidence_gate = evaluate_critical_field_confidence(
            overall_confidence=0,
            field_values=extraction_payload,
            field_confidences=extract_field_confidences(extraction_payload),
            document_type=extraction_payload.get("document_type"),
            primary_source=extraction_payload.get("primary_source"),
            has_attachment=bool(
                extraction_payload.get("has_invoice_attachment")
                or extraction_payload.get("attachment_url")
            ),
        )

    return {
        "email_id": email_id,
        "confidence_pct": confidence_pct,
        "can_post": confidence_pct >= 95 and len(mismatches) == 0 and not confidence_gate.get("requires_field_review"),
        "mismatches": mismatches,
        "threshold": confidence_gate.get("threshold_pct", 95),
        "requires_field_review": bool(confidence_gate.get("requires_field_review")),
        "confidence_blockers": confidence_gate.get("confidence_blockers") or [],
        "confidence_gate": confidence_gate,
    }


def render_ap_item_explanation(
    *,
    vendor: str,
    amount: Any,
    state: str,
    exception_code: Optional[str],
    confidence: Any,
    subject: str,
    audit_events: List[Dict[str, Any]],
    vendor_profile: Optional[Dict[str, Any]],
    vendor_history: List[Dict[str, Any]],
    prior_reasoning: str,
    needs_info_question: str,
) -> Dict[str, Any]:
    """Render a natural-language AP item explanation via Claude or fallback."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return _explain_with_claude(
            api_key=api_key,
            vendor=vendor,
            amount=amount,
            state=state,
            exception_code=exception_code,
            confidence=confidence,
            subject=subject,
            audit_events=audit_events,
            vendor_profile=vendor_profile,
            vendor_history=vendor_history,
            prior_reasoning=prior_reasoning,
            needs_info_question=needs_info_question,
        )
    return _explain_fallback(
        vendor=vendor,
        amount=amount,
        state=state,
        exception_code=exception_code,
        confidence=confidence,
        audit_events=audit_events,
        prior_reasoning=prior_reasoning,
        needs_info_question=needs_info_question,
    )


def _explain_with_claude(
    *,
    api_key: str,
    vendor: str,
    amount: Any,
    state: str,
    exception_code: Optional[str],
    confidence: Any,
    subject: str,
    audit_events: List[Dict[str, Any]],
    vendor_profile: Optional[Dict[str, Any]],
    vendor_history: List[Dict[str, Any]],
    prior_reasoning: str,
    needs_info_question: str,
) -> Dict[str, Any]:
    """Ask Claude to explain an AP item's current state in plain English."""
    import json as _json
    import re as _re

    import requests as _requests

    vendor_lines = [f"Vendor: {vendor}"]
    vendor_context = {"vendor": vendor}
    if vendor_profile:
        count = vendor_profile.get("invoice_count", 0)
        avg = vendor_profile.get("avg_invoice_amount")
        always_ok = bool(vendor_profile.get("always_approved"))
        bank_chg = vendor_profile.get("bank_details_changed_at")
        if count:
            avg_str = f"${avg:.2f}" if avg else "unknown"
            vendor_lines.append(f"  History: {count} invoice(s), avg {avg_str}")
            vendor_context.update({"invoice_count": count, "avg_amount": avg})
        if always_ok and count >= 3:
            vendor_lines.append("  Pattern: always approved in history")
            vendor_context["always_approved"] = True
        if bank_chg:
            vendor_lines.append(f"  Bank details changed: {bank_chg[:10]}")
            vendor_context["bank_details_changed_at"] = bank_chg
    if vendor_history:
        rows = []
        for history in vendor_history[:4]:
            created = (history.get("invoice_date") or history.get("created_at") or "")[:10]
            history_amount = history.get("amount")
            final_state = history.get("final_state") or "?"
            rows.append(
                f"  {created} | ${history_amount:.2f} | {final_state}"
                if history_amount else f"  {created} | {final_state}"
            )
        vendor_lines.append("  Recent invoices:\n" + "\n".join(rows))

    audit_lines = []
    for event in audit_events:
        timestamp = str(event.get("ts") or event.get("created_at") or "")[:16]
        event_type = str(event.get("event_type") or "event")
        actor = str(event.get("actor_type") or "system")
        reason = str(event.get("reason") or "")
        line = f"  {timestamp} [{actor}] {event_type}"
        if reason:
            line += f" - {reason}"
        audit_lines.append(line)

    amount_str = f"${amount:.2f}" if amount else "unknown"
    conf_str = f"{float(confidence):.0%}" if confidence else "unknown"

    prompt = f"""You are Clearledgr, an AP agent embedded in Gmail.

An operator is asking: "Why is this invoice in its current state?"

INVOICE:
  Vendor: {vendor}
  Amount: {amount_str}
  State: {state}
  Exception: {exception_code or "none"}
  Extraction confidence: {conf_str}
  Subject: {subject}

{chr(10).join(vendor_lines)}

AUDIT TRAIL (oldest -> newest):
{chr(10).join(audit_lines) if audit_lines else "  (no audit events recorded)"}

{f"PRIOR AGENT REASONING:{chr(10)}{prior_reasoning}" if prior_reasoning else ""}
{f"INFO NEEDED FROM VENDOR:{chr(10)}{needs_info_question}" if needs_info_question else ""}

---
Write a plain-English explanation (3-6 sentences) that answers:
1. What is this invoice and where did it come from?
2. Why is it in state '{state}'? (reference specific audit events or confidence scores)
3. What happens next, and is there anything the operator should do?

Speak as the AP agent. Be direct and specific. Do not use bullet points.
End with one sentence starting "Suggested next step:" if action is needed.

Return ONLY valid JSON:
{{"text": "...", "suggested_action": "..or null if no action needed"}}"""

    try:
        response = _requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                "max_tokens": 512,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json()
        content = raw.get("content", [])
        text = "\n".join(chunk.get("text", "") for chunk in content if isinstance(chunk, dict)).strip()
        fenced = _re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if fenced:
            text = fenced.group(1)
        parsed = _json.loads(text)
        return {
            "text": str(parsed.get("text") or ""),
            "suggested_action": parsed.get("suggested_action"),
            "vendor_context": vendor_context,
            "method": "llm",
        }
    except Exception as exc:
        logger.warning("[Explain] Claude call failed: %s - using fallback", exc)
        return _explain_fallback(
            vendor=vendor,
            amount=amount,
            state=state,
            exception_code=exception_code,
            confidence=confidence,
            audit_events=audit_events,
            prior_reasoning=prior_reasoning,
            needs_info_question=needs_info_question,
        )


def _explain_fallback(
    *,
    vendor: str,
    amount: Any,
    state: str,
    exception_code: Optional[str],
    confidence: Any,
    audit_events: List[Dict[str, Any]],
    prior_reasoning: str,
    needs_info_question: str,
) -> Dict[str, Any]:
    """Plain-text fallback explanation built from structured fields."""
    amount_str = f"${amount:.2f}" if amount else "an unknown amount"
    conf_str = f"{float(confidence):.0%}" if confidence else "unknown"

    parts = [f"Invoice from {vendor} for {amount_str} is currently in state '{state}'."]
    if prior_reasoning:
        parts.append(f"Agent reasoning: {prior_reasoning}")
    elif exception_code:
        parts.append(f"Blocked by: {exception_code}.")
    if confidence:
        parts.append(f"Extraction confidence: {conf_str}.")
    if audit_events:
        last = audit_events[-1]
        parts.append(f"Last recorded event: {str(last.get('event_type') or 'event')}.")

    suggested_action = None
    if state == "needs_info":
        if needs_info_question:
            parts.append(f"Waiting for information: {needs_info_question}")
        suggested_action = "Use 'Draft vendor reply' to request the missing information."
    elif state in ("failed_post", "posting"):
        suggested_action = "Retry ERP posting or use browser fallback."
    elif state in ("needs_approval", "pending_review"):
        suggested_action = "Review and approve or reject this invoice."

    return {
        "text": " ".join(parts),
        "suggested_action": suggested_action,
        "vendor_context": {},
        "method": "fallback",
    }


def build_gl_suggestion_payload(
    *,
    organization_id: str,
    vendor_name: str,
) -> Dict[str, Any]:
    """Build GL code suggestions from learning and vendor history."""
    from clearledgr.services.finance_learning import get_finance_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence

    learning = get_finance_learning_service(organization_id)
    vendor_intel = get_vendor_intelligence()

    learned = learning.suggest_gl_code(vendor_name)
    vendor_profile = vendor_intel.get_suggestion(vendor_name)

    suggestions: List[Dict[str, Any]] = []
    if learned and learned.get("gl_code"):
        suggestions.append(
            {
                "gl_code": learned["gl_code"],
                "gl_name": learned.get("gl_description", ""),
                "confidence": learned.get("confidence", 0.5),
                "source": "learning",
                "reason": f"Used {learned.get('occurrence_count', 0)} times for this vendor",
            }
        )
    if vendor_profile and vendor_profile.get("suggested_gl"):
        if not suggestions or suggestions[0]["gl_code"] != vendor_profile["suggested_gl"]:
            suggestions.append(
                {
                    "gl_code": vendor_profile["suggested_gl"],
                    "gl_name": vendor_profile.get("gl_description", ""),
                    "confidence": 0.7 if vendor_profile.get("known_vendor") else 0.4,
                    "source": "vendor_profile",
                    "reason": f"Typical for {vendor_profile.get('category', 'this vendor type')}",
                }
            )
    if learned and learned.get("alternatives"):
        for alt in learned["alternatives"][:2]:
            if not any(s["gl_code"] == alt["gl_code"] for s in suggestions):
                suggestions.append(
                    {
                        "gl_code": alt["gl_code"],
                        "gl_name": alt.get("gl_description", ""),
                        "confidence": alt.get("confidence", 0.3),
                        "source": "alternative",
                        "reason": "Also used for similar vendors",
                    }
                )
    suggestions.sort(key=lambda entry: entry["confidence"], reverse=True)
    return {
        "vendor_name": vendor_name,
        "primary": suggestions[0] if suggestions else None,
        "alternatives": suggestions[1:3] if len(suggestions) > 1 else [],
        "has_suggestion": len(suggestions) > 0,
    }


def build_vendor_suggestion_payload(
    *,
    organization_id: str,
    sender_email: Optional[str] = None,
    extracted_vendor: Optional[str] = None,
) -> Dict[str, Any]:
    """Build vendor-match suggestions from extraction and sender domain.

    Phase 3.1.a — rewritten to read directly from the DB-backed
    ``vendor_profiles`` table via :class:`VendorStore`. The previous
    implementation depended on
    ``clearledgr.services.vendor_management.VendorManagementService``,
    which carried an in-memory ``_vendors`` dict that was never
    populated in production and on a stale fuzzy-matcher API that no
    longer exists. Both have been removed in Phase 3.1.a.

    Match strategy:
      1. Score the extracted vendor name against every vendor profile
         in the org using :func:`vendor_similarity`. Top hits above the
         confidence floor are returned as ``"extraction"`` matches.
      2. Match the sender's email domain against any vendor profile
         whose ``sender_domains`` list contains the registrable domain.
         These are returned as ``"email_domain"`` matches.

    The two paths can both produce a hit for the same vendor; we
    deduplicate by vendor_name and keep the higher-confidence source.
    """
    from clearledgr.core.database import get_db
    from clearledgr.services.fuzzy_matching import vendor_similarity

    db = get_db()
    profiles = db.list_vendor_profiles(organization_id)

    candidates: List[Dict[str, Any]] = []
    seen_names: set = set()

    if extracted_vendor:
        scored: List[Dict[str, Any]] = []
        for profile in profiles:
            vendor_name = str(profile.get("vendor_name") or "").strip()
            if not vendor_name:
                continue
            score = vendor_similarity(extracted_vendor, vendor_name)
            if score >= 0.6:
                scored.append(
                    {
                        "vendor_name": vendor_name,
                        "confidence": round(score, 4),
                        "source": "extraction",
                        "matched_from": extracted_vendor,
                    }
                )
        scored.sort(key=lambda entry: entry["confidence"], reverse=True)
        for entry in scored:
            if entry["vendor_name"] in seen_names:
                continue
            seen_names.add(entry["vendor_name"])
            candidates.append(entry)

    if sender_email and "@" in sender_email:
        domain = sender_email.split("@", 1)[-1].strip().lower()
        if domain:
            for profile in profiles:
                vendor_name = str(profile.get("vendor_name") or "").strip()
                if not vendor_name or vendor_name in seen_names:
                    continue
                sender_domains = profile.get("sender_domains") or []
                if not isinstance(sender_domains, list):
                    continue
                if domain in {str(d).strip().lower() for d in sender_domains}:
                    seen_names.add(vendor_name)
                    candidates.append(
                        {
                            "vendor_name": vendor_name,
                            "confidence": 0.85,
                            "source": "email_domain",
                            "matched_from": domain,
                        }
                    )

    candidates.sort(key=lambda entry: entry["confidence"], reverse=True)
    return {
        "extracted_vendor": extracted_vendor,
        "primary": candidates[0] if candidates else None,
        "alternatives": candidates[1:3] if len(candidates) > 1 else [],
        "has_suggestion": len(candidates) > 0,
        "is_new_vendor": len(candidates) == 0,
    }


def build_amount_validation_payload(vendor_name: str, amount: float) -> Dict[str, Any]:
    """Validate amount against vendor history."""
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence

    vendor_intel = get_vendor_intelligence()
    validation = vendor_intel.validate_amount(vendor_name, amount)
    return {
        "vendor_name": vendor_name,
        "amount": amount,
        "is_reasonable": validation.get("seems_reasonable", True),
        "expected_range": validation.get("expected_range"),
        "concern": validation.get("concern"),
        "message": validation.get("message"),
    }


def build_form_prefill_payload(
    *,
    email_id: str,
    organization_id: str,
    invoice: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build all AI suggestions used to pre-fill Gmail form surfaces."""
    from clearledgr.services.finance_learning import get_finance_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence

    if not invoice:
        return {
            "email_id": email_id,
            "has_data": False,
            "message": "No extraction data found for this email",
        }

    invoice_org = str(invoice.get("organization_id") or organization_id)
    if invoice_org != organization_id:
        raise PermissionError("org_mismatch")

    learning = get_finance_learning_service(organization_id)
    vendor_intel = get_vendor_intelligence()
    vendor_name = invoice.get("vendor") or invoice.get("vendor_name", "")
    amount = invoice.get("amount", 0)

    gl_suggestion = learning.suggest_gl_code(vendor_name) if vendor_name else None
    vendor_profile = vendor_intel.get_suggestion(vendor_name) if vendor_name else None
    amount_validation = vendor_intel.validate_amount(vendor_name, amount) if vendor_name and amount else None

    return {
        "email_id": email_id,
        "has_data": True,
        "prefill": {
            "vendor": {
                "name": vendor_name,
                "confidence": invoice.get("confidence", 0.5),
            },
            "amount": {
                "value": amount,
                "is_reasonable": amount_validation.get("seems_reasonable", True) if amount_validation else True,
                "expected_range": amount_validation.get("expected_range") if amount_validation else None,
                "concern": amount_validation.get("concern") if amount_validation else None,
            },
            "gl_code": {
                "suggested": gl_suggestion.get("gl_code") if gl_suggestion else (vendor_profile.get("suggested_gl") if vendor_profile else None),
                "name": gl_suggestion.get("gl_description") if gl_suggestion else (vendor_profile.get("gl_description") if vendor_profile else None),
                "confidence": gl_suggestion.get("confidence", 0.5) if gl_suggestion else 0.4,
                "source": "learning" if gl_suggestion else ("vendor_profile" if vendor_profile else None),
            },
            "invoice_number": invoice.get("invoice_number"),
            "invoice_date": invoice.get("invoice_date"),
            "due_date": invoice.get("due_date"),
        },
    }


_EXCEPTION_REASON_MAP = {
    "po_reference_required": "Please provide a valid Purchase Order (PO) number for this invoice. Our system requires a PO reference before we can process payment.",
    "missing_po": "Please provide a valid Purchase Order (PO) number for this invoice.",
    "missing_invoice_number": "Please provide a valid invoice number. The invoice number was missing or could not be read from your submission.",
    "invalid_invoice_number": "The invoice number on your submission appears to be invalid. Please re-send with a clearly formatted invoice number.",
    "amount_mismatch": "The invoice amount does not match our purchase order or approval records. Please confirm the correct total and any line-item breakdown.",
    "duplicate_invoice": "This invoice appears to be a duplicate of a previous submission. Please confirm the invoice number and date, or advise if this is a revised invoice.",
    "vendor_not_recognized": "We were unable to match your company to our vendor records. Please confirm your registered company name, VAT/tax ID, and remittance address.",
    "currency_mismatch": "The invoice currency does not match the currency on our purchase order. Please re-issue in the agreed contract currency.",
    "missing_line_items": "Please re-send the invoice with itemised line items (description, quantity, unit price) so we can match it against our purchase order.",
    "policy_attribute_failure": "Additional details are required to process this invoice under our accounting policy. Please confirm the PO number, cost centre, and project code associated with this charge.",
    "approval_limit_exceeded": "This invoice exceeds the approval limit for automatic processing. We are escalating internally - no action is needed from you at this time.",
    "tax_id_required": "Please include your VAT/tax identification number on the invoice. This is required for our accounts payable records.",
}


def build_needs_info_draft_payload(
    *,
    ap_item_id: str,
    ap_item: Optional[Dict[str, Any]],
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Gmail compose payload for needs-info vendor follow-ups."""
    if not ap_item:
        raise LookupError("ap_item_not_found")
    if ap_item.get("state") != "needs_info":
        raise ValueError("item_not_in_needs_info_state")

    vendor = ap_item.get("vendor_name") or "Vendor"
    invoice_number = ap_item.get("invoice_number") or "your recent invoice"
    sender_email = ap_item.get("sender") or ""
    original_subject = ap_item.get("subject") or f"Invoice {invoice_number}"
    exception_code = str(ap_item.get("exception_code") or "").strip()
    reason_text = (
        str(reason).strip()
        if reason and str(reason).strip()
        else _EXCEPTION_REASON_MAP.get(exception_code)
        or str(ap_item.get("last_error") or "").strip()
        or "additional information is required before we can process this invoice"
    )

    body = (
        f"Dear {vendor},\n\n"
        f"Thank you for submitting invoice {invoice_number}.\n\n"
        f"We need the following before we can complete processing:\n\n"
        f"    {reason_text}\n\n"
        f"Please reply to this email with the requested information and we will "
        f"process your invoice promptly.\n\n"
        f"Best regards"
    )

    return {
        "ap_item_id": ap_item_id,
        "to": sender_email,
        "subject": f"Re: {original_subject}",
        "body": body,
    }
