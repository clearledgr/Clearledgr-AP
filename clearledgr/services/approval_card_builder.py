"""
Slack / Teams Approval Card Builder

Pure functions for constructing approval surface UI (Slack Block Kit blocks,
approval copy text, budget summaries). Extracted from InvoiceWorkflowService
to separate presentation concerns from workflow orchestration.

All functions are stateless — they take invoice data and context dicts,
return UI structures. No database or network access.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source-aware deeplink builder
# ---------------------------------------------------------------------------


def _build_source_link(invoice: Any) -> str:
    """Return the right "open this invoice in its native UI" URL based
    on the intake channel.

    * Gmail → ``https://mail.google.com/mail/u/0/#search/<gmail_id>``
    * NetSuite (ERP-native) → NetSuite Vendor Bill record URL using
      the account_id + ns_internal_id captured in ``erp_metadata``
    * SAP S/4HANA (ERP-native) → Fiori Launchpad cross-nav intent
      ``#SupplierInvoice-display?CompanyCode=...&SupplierInvoice=...&FiscalYear=...``
      against the customer's Fiori host (read from ``erp_metadata``;
      falls back to a Clearledgr deeplink if the Fiori host isn't
      known)
    * Anything else → Clearledgr web app deeplink

    Each ERP-native branch falls through to the Clearledgr deeplink
    when the metadata it needs is missing — so a Slack card never has
    a dead "Open" button.
    """
    source_type = str(getattr(invoice, "source_type", "gmail") or "gmail").lower()
    erp_metadata = getattr(invoice, "erp_metadata", None) or {}
    ap_item_id = (
        getattr(invoice, "ap_item_id", None)
        or getattr(invoice, "source_id", None)
        or getattr(invoice, "gmail_id", "")
    )
    import os
    base = os.getenv("APP_BASE_URL", "https://workspace.clearledgr.com").rstrip("/")
    # The web-app routes ap items at /items/:id (see ui/web-app/src/App.js);
    # /ap-items/:id is the legacy extension path and would 404 in the SPA.
    clearledgr_fallback = f"{base}/items/{ap_item_id}" if ap_item_id else base

    if source_type == "gmail":
        gid = str(getattr(invoice, "gmail_id", "") or "").strip()
        if gid:
            return f"https://mail.google.com/mail/u/0/#search/{gid}"
        return clearledgr_fallback

    if source_type == "netsuite":
        account_id = str(erp_metadata.get("ns_account_id") or "").strip()
        ns_internal_id = str(erp_metadata.get("ns_internal_id") or "").strip()
        if account_id and ns_internal_id:
            account_segment = account_id.replace("_", "-").lower()
            return (
                f"https://{account_segment}.app.netsuite.com"
                f"/app/accounting/transactions/vendbill.nl?id={ns_internal_id}"
            )
        return clearledgr_fallback

    if source_type == "sap_s4hana":
        fiori_host = str(erp_metadata.get("sap_fiori_host") or "").strip()
        cc = str(erp_metadata.get("company_code") or "").strip()
        doc = str(erp_metadata.get("supplier_invoice") or "").strip()
        fy = str(erp_metadata.get("fiscal_year") or "").strip()
        if fiori_host and cc and doc and fy:
            return (
                f"https://{fiori_host}/sap/bc/ui2/flp"
                f"#SupplierInvoice-display"
                f"?CompanyCode={cc}&SupplierInvoice={doc}&FiscalYear={fy}"
            )
        return clearledgr_fallback

    return clearledgr_fallback


def _source_link_label(invoice: Any) -> str:
    """The button text for the deeplink built by ``_build_source_link``.
    Matches the destination so operators see "Open in NetSuite" /
    "Open in SAP" / "Open in Gmail" / "Open in Clearledgr"."""
    source_type = str(getattr(invoice, "source_type", "gmail") or "gmail").lower()
    erp_metadata = getattr(invoice, "erp_metadata", None) or {}
    if source_type == "gmail" and getattr(invoice, "gmail_id", ""):
        return "Open in Gmail"
    if source_type == "netsuite" and erp_metadata.get("ns_internal_id") and erp_metadata.get("ns_account_id"):
        return "Open in NetSuite"
    if source_type == "sap_s4hana" and erp_metadata.get("sap_fiori_host") and erp_metadata.get("supplier_invoice"):
        return "Open in SAP"
    return "Open in Clearledgr"


# ---------------------------------------------------------------------------
# Budget helpers (pure data transforms)
# ---------------------------------------------------------------------------


def budget_status_rank(status: str) -> int:
    value = str(status or "").strip().lower()
    if value == "exceeded":
        return 4
    if value == "critical":
        return 3
    if value == "warning":
        return 2
    if value == "healthy":
        return 1
    return 0


def normalize_budget_checks(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        for key in ("checks", "budgets", "budget_impact"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [entry for entry in nested if isinstance(entry, dict)]
        if raw.get("budget_name") or raw.get("after_approval_status"):
            return [raw]
    return []


def compute_budget_summary(budget_checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "status": "healthy",
        "requires_decision": False,
        "critical_count": 0,
        "exceeded_count": 0,
        "warning_count": 0,
        "checks": budget_checks,
    }
    highest_rank = 0
    highest_status = "healthy"
    for check in budget_checks:
        status = str(check.get("after_approval_status") or check.get("status") or "healthy").lower()
        rank = budget_status_rank(status)
        if rank > highest_rank:
            highest_rank = rank
            highest_status = status
        if status == "critical":
            summary["critical_count"] += 1
        elif status == "exceeded":
            summary["exceeded_count"] += 1
        elif status == "warning":
            summary["warning_count"] += 1

    summary["status"] = highest_status
    summary["requires_decision"] = highest_status in {"critical", "exceeded"}
    summary["hard_block"] = highest_status == "exceeded"
    return summary


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def humanize_reason_code(code: Any) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    return raw.replace("_", " ")


def dedupe_reason_lines(lines: List[str], limit: int = 3) -> List[str]:
    deduped: List[str] = []
    seen: set = set()
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
        if len(deduped) >= max(1, int(limit)):
            break
    return deduped


# ---------------------------------------------------------------------------
# Approval surface copy (AX7 parity text)
# ---------------------------------------------------------------------------


def build_approval_surface_copy(
    invoice: Any,
    extra_context: Optional[Dict[str, Any]] = None,
    budget_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build parity copy for Slack/Teams approval cards (AX7)."""
    extra_context = extra_context or {}
    budget_summary = budget_summary or {}
    gmail_url = _build_source_link(invoice)

    why_scored: List[tuple] = []
    budget_status = str((budget_summary or {}).get("status") or "").strip().lower()
    if bool((budget_summary or {}).get("requires_decision")) or budget_status in {"critical", "exceeded"}:
        if budget_status in {"critical", "exceeded"}:
            why_scored.append(
                (120, f"Budget check is {budget_status.replace('_', ' ')} and requires an approval decision.")
            )
        else:
            why_scored.append((105, "Budget check requires an approval decision before posting."))

    confidence_gate = extra_context.get("confidence_gate")
    confidence_gate = confidence_gate if isinstance(confidence_gate, dict) else {}
    confidence_blockers = confidence_gate.get("blockers")
    if not isinstance(confidence_blockers, list):
        confidence_blockers = []
    if bool(confidence_gate.get("requires_field_review")) or confidence_blockers:
        blocker = confidence_blockers[0] if confidence_blockers else {}
        if isinstance(blocker, dict):
            field = str(blocker.get("field") or blocker.get("code") or "critical field").replace("_", " ")
            why_scored.append((95, f"Extraction confidence is low for {field}; human review is required."))
        else:
            why_scored.append((90, "Extraction confidence is low for a critical field; human review is required."))
    elif float(invoice.confidence or 0.0) < 0.95:
        why_scored.append(
            (70, f"Extraction confidence is {invoice.confidence * 100:.0f}%, so the agent is asking for a review before posting.")
        )

    validation_gate = extra_context.get("validation_gate")
    validation_gate = validation_gate if isinstance(validation_gate, dict) else {}
    validation_reasons = validation_gate.get("reasons")
    if not isinstance(validation_reasons, list):
        validation_reasons = []
    for reason in validation_reasons[:2]:
        if not isinstance(reason, dict):
            continue
        message = str(reason.get("message") or "").strip()
        code = humanize_reason_code(reason.get("code"))
        if message:
            why_scored.append((85, message))
        elif code:
            why_scored.append((80, f"Validation flagged: {code}."))
    if not why_scored:
        reason_codes = validation_gate.get("reason_codes")
        if isinstance(reason_codes, list):
            for code in reason_codes[:2]:
                text = humanize_reason_code(code)
                if text:
                    why_scored.append((72, f"Validation flagged: {text}."))

    po_match = extra_context.get("po_match_result")
    po_match = po_match if isinstance(po_match, dict) else {}
    po_exceptions = po_match.get("exceptions") if isinstance(po_match.get("exceptions"), list) else []
    if po_exceptions:
        first_po_exception = po_exceptions[0]
        if isinstance(first_po_exception, dict):
            po_type = str(first_po_exception.get("type") or first_po_exception.get("code") or "").strip().lower()
            if po_type:
                why_scored.append((88, f"PO/receipt exception detected: {po_type.replace('_', ' ')}."))

    erp_preflight = extra_context.get("erp_preflight")
    if isinstance(erp_preflight, dict) and erp_preflight.get("erp_available"):
        if erp_preflight.get("bill_exists") is True:
            why_scored.append((130, f"Duplicate bill already exists in {erp_preflight.get('erp_type', 'ERP')}."))
        if erp_preflight.get("vendor_exists") is False:
            why_scored.append((75, f"Vendor not found in {erp_preflight.get('erp_type', 'ERP')}."))

    approval_context = extra_context.get("approval_context")
    approval_context = approval_context if isinstance(approval_context, dict) else {}
    open_vendor_items = int(approval_context.get("vendor_open_invoices") or 0)
    if open_vendor_items > 1:
        why_scored.append((60, f"Vendor has {open_vendor_items} other open invoice(s), so this decision affects related payables already in flight."))

    if int(invoice.potential_duplicates or 0) > 0:
        why_scored.append(
            (92, f"Potential duplicate risk detected ({int(invoice.potential_duplicates)} similar invoice(s)).")
        )

    if not why_scored:
        why_scored.append((50, "This invoice needs approval before Clearledgr can post it."))

    why_candidates = [line for _score, line in sorted(why_scored, key=lambda entry: entry[0], reverse=True)]
    why_summary = " ".join(dedupe_reason_lines(why_candidates, limit=2)).strip()

    requires_budget_decision = bool((budget_summary or {}).get("requires_decision"))
    hard_budget_block = bool((budget_summary or {}).get("hard_block")) or budget_status == "exceeded"
    confidence_requires_review = bool(confidence_gate.get("requires_field_review")) or bool(confidence_blockers)
    has_validation_blockers = bool(validation_reasons) or bool(validation_gate.get("reason_codes"))
    has_duplicate_risk = int(invoice.potential_duplicates or 0) > 0
    recommended_action_text = (
        "Request budget adjustment unless this invoice is business-critical and an override is justified."
        if requires_budget_decision and hard_budget_block
        else "Approve the override only if the business need is clear and documented."
        if requires_budget_decision
        else "Request more information before posting if any detail still looks wrong."
        if has_validation_blockers or confidence_requires_review
        else "Only reject if the duplicate risk is confirmed; otherwise ask for clarification."
        if has_duplicate_risk
        else "Approve and let Clearledgr post it if the details look correct."
    )

    if requires_budget_decision:
        approve_line = (
            "Approve override: Clearledgr records the justification and then posts this invoice to ERP."
            if hard_budget_block
            else "Approve override: Clearledgr records the justification and then posts this invoice to ERP."
        )
        request_info_line = (
            "Request info: Clearledgr sends this back for budget or policy clarification."
            if has_validation_blockers
            else "Request info: Clearledgr sends this back for clarification."
        )
        reject_line = "Reject: Clearledgr records the rejection and stops any further posting."
        if has_duplicate_risk:
            reject_line = (
                "Reject: use this if the duplicate risk is confirmed. Clearledgr records the rejection and stops posting."
            )
        next_lines = [approve_line, request_info_line, reject_line]
    else:
        approve_line = (
            "Approve / Post to ERP: Clearledgr records the approval and posts this invoice automatically."
            if confidence_requires_review
            else "Approve / Post to ERP: Clearledgr records the approval and posts this invoice automatically."
        )
        request_info_line = (
            "Request info: Clearledgr sends this back for the missing policy or evidence details."
            if has_validation_blockers
            else "Request info: Clearledgr sends this back for the missing details."
        )
        reject_line = "Reject: Clearledgr records the rejection and stops any further posting."
        if has_duplicate_risk:
            reject_line = (
                "Reject: use this if the duplicate risk is confirmed. Clearledgr records the rejection and stops posting."
            )
        next_lines = [approve_line, request_info_line, reject_line]

    return {
        "why_summary": why_summary,
        "what_happens_next": next_lines,
        "recommended_action_text": recommended_action_text,
        "requested_by_text": "Raised by Clearledgr from this Gmail thread.",
        "source_of_truth_text": "Open in Gmail if you want to review the original email and attachment.",
        "gmail_url": gmail_url,
    }


# ---------------------------------------------------------------------------
# Slack Block Kit approval card
# ---------------------------------------------------------------------------


def build_approval_blocks(
    invoice: Any,
    extra_context: Optional[Dict[str, Any]] = None,
) -> list:
    """Build compact Slack Block Kit blocks for approval request.

    Structure:
    1. Header (1 block)
    2. Invoice details (1 block - 4 fields)
    3. Flags - only if something needs attention (0-2 blocks)
    4. Actions (1 block)
    5. Footer context (1 block)
    """
    from datetime import datetime, timezone

    # ========== CONFIDENCE ==========
    if invoice.confidence >= 0.9:
        confidence_text = f"High ({invoice.confidence*100:.0f}%)"
    elif invoice.confidence >= 0.7:
        confidence_text = f"Medium ({invoice.confidence*100:.0f}%)"
    else:
        confidence_text = f"Low ({invoice.confidence*100:.0f}%)"

    # ========== DUE DATE WARNING ==========
    due_warning = ""
    days_until = invoice.priority.get("days_until_due") if invoice.priority else None
    if days_until is not None:
        if days_until < 0:
            due_warning = f" *OVERDUE {abs(days_until)}d*"
        elif days_until == 0:
            due_warning = " *DUE TODAY*"
        elif days_until <= 3:
            due_warning = f" _{days_until}d left_"
    elif invoice.due_date:
        try:
            # Parse the date-only string as midnight UTC so we can compare
            # against an aware `now()` without raising TypeError. Day-level
            # arithmetic stays correct because both sides are in the same tz.
            due = datetime.strptime(invoice.due_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_until = (due - datetime.now(timezone.utc)).days
            if days_until < 0:
                due_warning = f" *OVERDUE {abs(days_until)}d*"
            elif days_until <= 3:
                due_warning = f" _{days_until}d left_"
        except Exception:
            pass

    # ========== PO MATCH STATUS ==========
    po_text = "Not provided"
    po_match = getattr(invoice, "po_match_result", None)
    if not po_match and extra_context:
        po_match = (extra_context or {}).get("po_match_result")
    if po_match:
        po_num = po_match.get("po_number") or po_match.get("po_id")
        match_status = po_match.get("match_status", "").lower()
        if po_num and "match" in match_status and "exception" not in match_status:
            po_text = f"#{po_num} matched"
        elif po_num:
            po_text = f"#{po_num} (exceptions)"
        else:
            po_text = "No match"
    elif invoice.po_number:
        po_text = f"#{invoice.po_number}"

    # ========== HEADER ==========
    priority_level = invoice.priority.get("priority", "") if invoice.priority else ""
    priority_text = invoice.priority.get("priority_label", "") if invoice.priority else ""
    if priority_level == "CRITICAL":
        header_text = "CRITICAL: Invoice Approval"
    elif priority_level == "HIGH":
        header_text = "HIGH: Invoice Approval"
    elif priority_text == "URGENT":
        header_text = "URGENT: Invoice Approval"
    else:
        header_text = "Invoice Approval"

    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
    ]

    # ========== §6.8 MATCH ICON ROW — scannable in under two seconds =========
    # Thesis: "Three icons in a row: Purchase Order ✓, Goods Receipt ✓,
    # Invoice ✓. Or with discrepancy: GRN ⚠ — £422 delta. Visual at a
    # glance. No text required to understand the status."
    def _match_icon(status: Any) -> str:
        s = str(status or "").lower().strip()
        if s in {"matched", "passed", "confirmed", "verified", "ok", "linked"}:
            return "✓"
        if s in {"exception", "warning", "mismatch", "partial", "delta"}:
            return "⚠"
        if s in {"failed", "missing", "not_linked", "not_provided", "na"}:
            return "✗"
        return "—"

    _po_match = getattr(invoice, "po_match_result", None) or (extra_context or {}).get("po_match_result") or {}
    _grn_match = (extra_context or {}).get("grn_match_result") or {}
    _three_way = (extra_context or {}).get("three_way_match") or {}

    # PO icon: linked + matched > linked > not linked
    if _po_match:
        _po_icon = _match_icon(_po_match.get("match_status") or ("matched" if _po_match.get("po_number") else ""))
    elif getattr(invoice, "po_number", None):
        _po_icon = "✓"  # PO number extracted but not yet matched in ERP
    else:
        _po_icon = "✗"

    # GRN icon: from dedicated match result or three_way_match
    _grn_status_raw = (
        _grn_match.get("status")
        or _three_way.get("grn_status")
        or _three_way.get("grn_match")
        or ""
    )
    _grn_icon = _match_icon(_grn_status_raw) if _grn_status_raw else "—"

    # Invoice match: usually from three-way match or the invoice's own
    # extraction confidence + exception state
    _inv_status_raw = (
        _three_way.get("invoice_status")
        or _three_way.get("overall_status")
        or _three_way.get("match_status")
        or ""
    )
    if _inv_status_raw:
        _inv_icon = _match_icon(_inv_status_raw)
    elif invoice.policy_compliance and not invoice.policy_compliance.get("compliant", True):
        _inv_icon = "⚠"
    elif invoice.confidence and invoice.confidence >= 0.9:
        _inv_icon = "✓"
    else:
        _inv_icon = "—"

    # Variance line (if we know the delta)
    _variance_bits = []
    _po_delta = _po_match.get("delta") or _po_match.get("variance_amount")
    if _po_delta:
        try:
            _variance_bits.append(f"PO delta {invoice.currency} {abs(float(_po_delta)):,.2f}")
        except (TypeError, ValueError):
            pass
    _grn_delta = _grn_match.get("delta") or _grn_match.get("variance_amount")
    if _grn_delta:
        try:
            _variance_bits.append(f"GRN delta {invoice.currency} {abs(float(_grn_delta)):,.2f}")
        except (TypeError, ValueError):
            pass

    _icon_row = f"PO {_po_icon}    GRN {_grn_icon}    Invoice {_inv_icon}"
    if _variance_bits:
        _icon_row += "  ·  " + "  ·  ".join(_variance_bits)

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"`{_icon_row}`"},
    })

    # ========== EXTRACTION CONTEXT ==========
    missing_fields = []
    field_labels = {
        "vendor": "vendor",
        "amount": "amount",
        "invoice #": "invoice number",
        "due date": "due date",
        "PO #": "PO number",
    }
    for field_name, value in [("vendor", invoice.vendor_name), ("amount", invoice.amount), ("invoice #", invoice.invoice_number), ("due date", invoice.due_date), ("PO #", invoice.po_number)]:
        if not value or str(value) in ("N/A", "0", "0.0", "None", ""):
            missing_fields.append(field_labels.get(field_name, field_name))

    source_parts = []
    if missing_fields:
        source_parts.append(f"Missing: {', '.join(missing_fields)}")
    if invoice.confidence < 0.95:
        source_parts.append(f"Extraction confidence: {confidence_text}")

    if source_parts:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " · ".join(source_parts)}]
        })

    # ========== ERP PRE-FLIGHT ==========
    erp_preflight = (extra_context or {}).get("erp_preflight")
    if isinstance(erp_preflight, dict) and erp_preflight.get("erp_available"):
        pf_parts = []
        if erp_preflight.get("vendor_exists") is False:
            pf_parts.append("Vendor not in ERP")
        if erp_preflight.get("bill_exists") is True:
            pf_parts.append("Duplicate bill found in ERP")
        if erp_preflight.get("gl_valid") is False:
            pf_parts.append(f"Invalid GL: {', '.join(erp_preflight.get('invalid_gl_codes', []))}")
        if pf_parts:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*ERP pre-check:*\n{' | '.join(pf_parts)}"},
            })

    # ========== AGENT REASONING (only when populated) ==========
    if invoice.reasoning_summary:
        reasoning_parts = [f"*Agent:* {invoice.reasoning_summary}"]

        if invoice.reasoning_factors:
            factor_strs = []
            for f in invoice.reasoning_factors[:4]:
                name = str(f.get("factor", "")).replace("_", " ").title()
                score = f.get("score", 0)
                detail = f.get("detail", "")
                factor_strs.append(f"{name}: {score:.1f}" + (f" — {detail}" if detail else ""))
            if factor_strs:
                reasoning_parts.append("*Factors:* " + " | ".join(factor_strs))

        if invoice.reasoning_risks:
            risk_text = " | ".join(str(r) for r in invoice.reasoning_risks[:3])
            reasoning_parts.append(f"*Risks:* {risk_text}")

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(reasoning_parts)},
        })

    # ========== MAIN DETAILS (4 fields) ==========
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Vendor:*\n{invoice.vendor_name}"},
            {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
            {"type": "mrkdwn", "text": f"*Invoice #:*\n{invoice.invoice_number or 'N/A'}"},
            {"type": "mrkdwn", "text": f"*Due:*\n{invoice.due_date or 'N/A'}{due_warning}"},
        ]
    })

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*PO:*\n{po_text}"},
            {
                "type": "mrkdwn",
                "text": f"*GL:*\n{'Suggested automatically' if str((invoice.vendor_intelligence or {}).get('suggested_gl') or '').strip().lower() in {'', 'auto'} else (invoice.vendor_intelligence or {}).get('suggested_gl')}",
            },
        ]
    })

    # ========== LINE ITEMS (show when present) ==========
    _card_line_items = getattr(invoice, "line_items", None)
    if not _card_line_items and extra_context:
        _card_line_items = (extra_context or {}).get("line_items")
    if isinstance(_card_line_items, list) and _card_line_items:
        _max_display = 5
        _li_lines = []
        for _li in _card_line_items[:_max_display]:
            if not isinstance(_li, dict):
                continue
            desc = str(_li.get("description") or "Item")[:40]
            amt = _li.get("amount")
            try:
                amt_str = f"${float(amt):,.2f}" if amt is not None else ""
            except (TypeError, ValueError):
                amt_str = ""
            _li_lines.append(f"- {desc}  {amt_str}".rstrip())
        if _li_lines:
            _li_text = "\n".join(_li_lines)
            if len(_card_line_items) > _max_display:
                _li_text += f"\n_...and {len(_card_line_items) - _max_display} more_"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Line items ({len(_card_line_items)}):*\n{_li_text}"},
            })

    # ========== FLAGS (only when something needs attention) ==========

    # Budget impact — only show if warning/critical/exceeded
    budget_checks = normalize_budget_checks(invoice.budget_impact)
    if not budget_checks and extra_context:
        budget_checks = normalize_budget_checks(extra_context.get("budget_impact"))
    budget_summary = compute_budget_summary(budget_checks) if budget_checks else {
        "status": "healthy",
        "requires_decision": False,
    }
    approval_copy = build_approval_surface_copy(
        invoice=invoice,
        extra_context=extra_context or {},
        budget_summary=budget_summary,
    )

    flagged_budgets = [b for b in (budget_checks or []) if str(b.get("after_approval_status") or b.get("status") or "").lower() in ("warning", "critical", "exceeded")]
    if flagged_budgets:
        budget_lines = []
        for budget in flagged_budgets[:2]:
            status = str(budget.get("after_approval_status") or budget.get("status") or "").lower()
            try:
                pct = float(budget.get("after_approval_percent") or budget.get("percent_used") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            name = str(budget.get("budget_name") or "Budget")
            marker = "RED" if status == "exceeded" else "AMBER"
            budget_lines.append(f"• *{name}*  {marker} {pct:.0f}% used")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Budget:* " + " | ".join(budget_lines)}
        })

    # Policy violations — only show if non-compliant
    if invoice.policy_compliance and not invoice.policy_compliance.get("compliant", True):
        violations = invoice.policy_compliance.get("violations", [])[:2]
        if violations:
            viol_text = " | ".join(v.get("message", "") for v in violations if v.get("message"))
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Policy:* {viol_text}"}
            })

    # Duplicate warning
    if invoice.potential_duplicates and invoice.potential_duplicates > 0:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Duplicate:* {invoice.potential_duplicates} similar invoice(s) found"}
        })

    # Validation gate issues
    validation_gate = (extra_context or {}).get("validation_gate") if extra_context else None
    if validation_gate and validation_gate.get("reason_codes"):
        reasons = validation_gate.get("reasons") or []
        gate_msgs = [str(r.get("message") or r.get("code", "")) for r in reasons[:2] if isinstance(r, dict)]
        if gate_msgs:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Validation:* " + " | ".join(gate_msgs)}
            })

    why_summary = str(approval_copy.get("why_summary") or "").strip()
    if why_summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Why this needs your decision:*\n{why_summary}"},
            }
        )
    recommended_action_text = str(approval_copy.get("recommended_action_text") or "").strip()
    if recommended_action_text:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recommended decision:*\n{recommended_action_text}"},
            }
        )

    what_happens_next = approval_copy.get("what_happens_next")
    if isinstance(what_happens_next, list) and what_happens_next:
        next_lines = [f"• {str(line).strip()}" for line in what_happens_next[:3] if str(line).strip()]
        if next_lines:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*What happens next:*\n" + "\n".join(next_lines)},
                }
            )

    approval_mentions = [
        str(value).strip()
        for value in ((extra_context or {}).get("approval_mentions") or [])
        if str(value).strip()
    ]
    approval_assignees = [
        str(value).strip()
        for value in ((extra_context or {}).get("approval_assignee_labels") or [])
        if str(value).strip()
    ]
    approver_display: list[str] = []
    seen_approvers = set()
    for value in [*approval_mentions, *approval_assignees]:
        if not value or value in seen_approvers:
            continue
        seen_approvers.add(value)
        approver_display.append(value)
    if approver_display:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Approvers for this request:*\n" + ", ".join(approver_display),
                },
            }
        )

    # ========== ACTIONS ==========
    requires_budget_decision = bool(budget_summary.get("requires_decision"))
    approval_override_value = json.dumps({
        "gmail_id": invoice.gmail_id,
        "justification": "Approved over budget in Slack",
        "decision": "approve_override",
    })

    gmail_link = _build_source_link(invoice)
    gmail_link_label = _source_link_label(invoice)

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": (
            [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve override"},
                    "style": "primary",
                    "action_id": f"approve_budget_override_{invoice.gmail_id}",
                    "value": approval_override_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request info"},
                    "action_id": f"request_info_{invoice.gmail_id}",
                    "value": invoice.gmail_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_budget_{invoice.gmail_id}",
                    "value": invoice.gmail_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": gmail_link_label},
                    "action_id": f"view_invoice_{invoice.gmail_id}",
                    "url": gmail_link,
                },
            ]
            if requires_budget_decision
            else [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Post to ERP"},
                    "style": "primary",
                    "action_id": f"post_to_erp_{invoice.gmail_id}",
                    "value": invoice.gmail_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_invoice_{invoice.gmail_id}",
                    "value": invoice.gmail_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request info"},
                    "action_id": f"request_info_{invoice.gmail_id}",
                    "value": invoice.gmail_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": gmail_link_label},
                    "action_id": f"view_invoice_{invoice.gmail_id}",
                    "url": gmail_link,
                },
            ]
        )
    })

    # Footer
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"From: {invoice.sender}"},
            {"type": "mrkdwn", "text": str(approval_copy.get("requested_by_text") or "Raised by Clearledgr from this Gmail thread.")},
            {"type": "mrkdwn", "text": str(approval_copy.get("source_of_truth_text") or "Open in Gmail if you want to review the original email and attachment.")},
        ]
    })

    # Validate all action_id strings — Slack silently rejects blocks with
    # empty or None action_ids (e.g. when gmail_id is None).
    for block in blocks:
        for element in block.get("elements", []):
            aid = element.get("action_id")
            if aid is not None and (not isinstance(aid, str) or not aid.strip() or "None" in aid):
                logger.error(
                    "Invalid action_id detected in approval blocks: %r (invoice gmail_id=%s); replacing with fallback",
                    aid, getattr(invoice, "gmail_id", None),
                )
                element["action_id"] = f"fallback_action_{id(element)}"

    return blocks
