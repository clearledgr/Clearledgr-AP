"""Phase 3.3 — synthesize a vendor-reply draft from an AP item's
exception state.

Deterministic-first: most "the AP machine is blocked, ask the vendor
for X" cases map cleanly to a fixed template in
``vendor_communication_templates.VENDOR_TEMPLATES``. The template
gets rendered with the AP item's vendor + invoice context (always the
same shape — see :func:`_render_context_for_item`) and returned to
the extension, which pre-fills a Gmail Compose via InboxSDK.

When no template matches the exception code, the service falls back
to the ``general_inquiry`` template with a derived question line — so
every call returns a usable draft. LLM-driven synthesis (the
``DRAFT_VENDOR_RESPONSE`` action on the LLM gateway) is not invoked
here; that's a deliberate Phase 3.3.a constraint to keep latency,
cost, and review surface bounded for the first ship.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from clearledgr.services.vendor_communication_templates import (
    VENDOR_TEMPLATES,
    render_template,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception code → template mapping
# ---------------------------------------------------------------------------
# Conservative table: only codes whose template clearly fits get mapped.
# Anything not in this table falls through to ``general_inquiry`` with
# a derived question line. The audit trail records the resolved
# template_id so a future LLM upgrade can target only the cases where
# the deterministic path wasn't precise enough.
_EXCEPTION_CODE_TO_TEMPLATE: Dict[str, str] = {
    "po_missing":               "missing_po",
    "missing_po":               "missing_po",
    "po_required":              "missing_po",
    "amount_mismatch":          "missing_amount",
    "missing_amount":           "missing_amount",
    "amount_required":          "missing_amount",
    "missing_due_date":         "missing_due_date",
    "due_date_required":        "missing_due_date",
    "bank_details_changed":     "bank_details_verification",
    "iban_change_pending":      "bank_details_verification",
    "iban_change_required":     "bank_details_verification",
    "bank_verification_needed": "bank_details_verification",
}


# Field-review blocker field names that map to a known template. When
# `exception_code` is unset but the AP item has a field_review_blocker
# entry, we use the `field_name` to pick a template — same precision,
# different signal.
_BLOCKER_FIELD_TO_TEMPLATE: Dict[str, str] = {
    "po_number":      "missing_po",
    "po_reference":   "missing_po",
    "amount":         "missing_amount",
    "primary_amount": "missing_amount",
    "total":          "missing_amount",
    "due_date":       "missing_due_date",
    "iban":           "bank_details_verification",
    "bank_account":   "bank_details_verification",
    "account_number": "bank_details_verification",
}


def _resolve_template_id(item: Dict[str, Any]) -> str:
    """Pick the template that best matches the AP item's exception state."""
    code = str(item.get("exception_code") or "").strip().lower()
    if code and code in _EXCEPTION_CODE_TO_TEMPLATE:
        return _EXCEPTION_CODE_TO_TEMPLATE[code]

    blockers = item.get("field_review_blockers") or []
    if isinstance(blockers, list):
        for blocker in blockers:
            if not isinstance(blocker, dict):
                continue
            field = str(blocker.get("field_name") or blocker.get("field") or "").strip().lower()
            if field in _BLOCKER_FIELD_TO_TEMPLATE:
                return _BLOCKER_FIELD_TO_TEMPLATE[field]

    return "general_inquiry"


def _derive_general_question(item: Dict[str, Any]) -> str:
    """Build the {question} placeholder for the general_inquiry template
    when no specific template matches. Reads the AP item's
    field-review-blocker reasons and the exception code so the resulting
    question is concrete enough to send.
    """
    blockers = item.get("field_review_blockers") or []
    if isinstance(blockers, list):
        parts: list[str] = []
        for blocker in blockers[:3]:
            if not isinstance(blocker, dict):
                continue
            field = str(blocker.get("field_name") or blocker.get("field") or "").replace("_", " ").strip()
            reason = str(blocker.get("reason") or blocker.get("message") or "").replace("_", " ").strip()
            if field and reason:
                parts.append(f"{field} ({reason})")
            elif field:
                parts.append(field)
        if parts:
            joined = ", ".join(parts)
            return (
                "Before we can process this invoice, could you please confirm "
                f"the following details: {joined}?"
            )

    code = str(item.get("exception_code") or "").strip().replace("_", " ")
    if code:
        return (
            f"Before we can process this invoice, we need to clarify: {code}. "
            "Could you please send the corrected information?"
        )

    return (
        "Before we can process this invoice, we need a couple of details "
        "clarified. Could you please get back to us?"
    )


def _render_context_for_item(
    item: Dict[str, Any],
    *,
    company_name: str,
    original_subject: str,
) -> Dict[str, Any]:
    """The context dict every template gets rendered against.

    Templates use a default-empty-string semantic via ``render_template``,
    so missing fields render cleanly rather than raising. Callers don't
    have to defend against partial AP item payloads.
    """
    return {
        "original_subject": original_subject or item.get("subject") or "Invoice",
        "invoice_number": item.get("invoice_number") or "",
        "currency": item.get("currency") or "",
        "amount": item.get("amount") or "",
        "vendor_name": item.get("vendor_name") or item.get("vendor") or "",
        "company_name": company_name or "Accounts Payable",
        "question": _derive_general_question(item),
    }


def synthesize_reply_for_item(
    item: Dict[str, Any],
    *,
    company_name: str,
    original_subject: str = "",
) -> Dict[str, Any]:
    """Produce a vendor-reply draft for an AP item.

    Returns a dict with keys ``subject``, ``body``, ``to``,
    ``template_id``, ``source``. ``to`` is the original sender if
    known, empty otherwise (the user can always retype). ``source`` is
    ``"template"`` for now — the ``"llm"`` value is reserved for a
    future commit that wires the gateway-driven fallback.
    """
    template_id = _resolve_template_id(item)
    if template_id not in VENDOR_TEMPLATES:
        logger.warning(
            "draft_reply: resolved template_id=%r is not registered, "
            "falling back to general_inquiry",
            template_id,
        )
        template_id = "general_inquiry"

    context = _render_context_for_item(
        item,
        company_name=company_name,
        original_subject=original_subject or item.get("subject") or "",
    )
    rendered = render_template(template_id, context)

    sender = str(item.get("sender") or item.get("source_email_sender") or "").strip()

    return {
        "subject": rendered["subject"],
        "body": rendered["body"],
        "to": sender,
        "template_id": template_id,
        "source": "template",
    }


__all__ = ["synthesize_reply_for_item"]
