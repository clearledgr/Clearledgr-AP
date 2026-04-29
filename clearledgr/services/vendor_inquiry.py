"""Vendor inquiry status surface (Wave 6 / H2).

Vendors regularly email AP teams asking "where's my payment for
invoice INV-9001?". Today the AP team digs through the inbox and
ERP to answer; H2 makes the answer a one-click (or auto-reply)
operation. The response is intentionally minimal — only the
canonical status the vendor would already see on their own bank
statement / portal — so we don't leak internal data.

Two layers:

  * **Lookup**: given a (sender_email, invoice_number), find the
    AP item if any matches the vendor's stored sender_domains and
    the invoice_number agrees. Returns a sanitized status block.
  * **Reply formatting**: produces a plain-text email body that
    the AP team can review + send (or that the auto-reply hook can
    fire after a confidence check).

What the vendor sees:

  * Status bucket: received / under_review / awaiting_approval /
    approved / scheduled_for_payment / paid / on_hold / rejected.
  * Last update timestamp.
  * Payment reference + settlement date IF status=paid.

What the vendor does NOT see:

  * Internal AP item id.
  * Approver names / decision_reason fields.
  * Internal note metadata.
  * Other invoices for the same vendor.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Sanitized status mapping ───────────────────────────────────────


_AP_STATE_TO_VENDOR_STATUS: Dict[str, str] = {
    "received": "received",
    "validated": "under_review",
    "needs_info": "under_review",
    "needs_approval": "awaiting_approval",
    "needs_second_approval": "awaiting_approval",
    "approved": "approved",
    "ready_to_post": "approved",
    "posted_to_erp": "scheduled_for_payment",
    "awaiting_payment": "scheduled_for_payment",
    "payment_in_flight": "scheduled_for_payment",
    "payment_executed": "paid",
    "payment_failed": "on_hold",
    "failed_post": "on_hold",
    "snoozed": "on_hold",
    "rejected": "rejected",
    "reversed": "rejected",
    "closed": "paid",
}


_STATUS_NARRATIVES: Dict[str, str] = {
    "received": (
        "We have received your invoice and it is queued for "
        "processing. You can expect an update within a few business days."
    ),
    "under_review": (
        "Your invoice is currently being reviewed by our team. "
        "We will follow up if any clarifying information is needed."
    ),
    "awaiting_approval": (
        "Your invoice has been validated and is awaiting approval "
        "from the assigned approver(s)."
    ),
    "approved": (
        "Your invoice has been approved and is queued to be posted "
        "to our ledger."
    ),
    "scheduled_for_payment": (
        "Your invoice has been posted to our ledger and is "
        "scheduled for payment per our standard payment terms."
    ),
    "paid": (
        "This invoice has been paid. The payment reference and "
        "settlement details are listed below."
    ),
    "on_hold": (
        "There is an issue with this invoice that requires "
        "attention. Our team will reach out shortly."
    ),
    "rejected": (
        "This invoice has not been processed for payment. Our "
        "team will reach out with more details."
    ),
}


# ── Output shapes ──────────────────────────────────────────────────


@dataclass
class VendorInquiryResult:
    found: bool
    status: Optional[str] = None
    last_updated_at: Optional[str] = None
    invoice_number: Optional[str] = None
    payment_reference: Optional[str] = None
    settlement_at: Optional[str] = None
    narrative: Optional[str] = None
    no_match_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "status": self.status,
            "last_updated_at": self.last_updated_at,
            "invoice_number": self.invoice_number,
            "payment_reference": self.payment_reference,
            "settlement_at": self.settlement_at,
            "narrative": self.narrative,
            "no_match_reason": self.no_match_reason,
        }


# ── Helpers ────────────────────────────────────────────────────────


def _norm_invoice(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value)).strip().upper()


def _domain_from_email(value: Optional[str]) -> str:
    if not value:
        return ""
    s = str(value).lower().strip()
    if "@" in s:
        s = s.rsplit("@", 1)[-1]
    return s.strip().strip(".")


def _vendor_profile_for_domain(
    db, organization_id: str, domain: str,
) -> Optional[Dict[str, Any]]:
    """Walk vendor_profiles for the org, return the first whose
    sender_domains list contains the domain."""
    if not domain:
        return None
    # Build the set of candidate parent domains the inbound email
    # could legitimately match: ['billing.vendor-x.com', 'vendor-x.com', 'com']
    # so the SQL pre-filter catches sub-domain registrations.
    parts = domain.split(".")
    candidates = {
        ".".join(parts[i:]) for i in range(len(parts))
    }
    db.initialize()
    sql = (
        "SELECT vendor_name, sender_domains FROM vendor_profiles "
        "WHERE organization_id = %s "
        "  AND sender_domains IS NOT NULL "
        "  AND sender_domains != %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, "[]"))
        rows = cur.fetchall()
    for r in rows:
        row = dict(r)
        raw_list = row.get("sender_domains")
        if isinstance(raw_list, str):
            try:
                raw_list = json.loads(raw_list)
            except Exception:
                raw_list = []
        if not isinstance(raw_list, list):
            continue
        normalized = {str(d).lower().strip().strip(".") for d in raw_list}
        # Match if any registered domain equals the inbound domain or
        # is a parent of it (sub-domain match).
        if normalized & candidates:
            return row
    return None


# ── Lookup ─────────────────────────────────────────────────────────


def lookup_vendor_inquiry(
    db,
    *,
    organization_id: str,
    sender_email: str,
    invoice_number: str,
) -> VendorInquiryResult:
    """Find the AP item matching (sender_domain, invoice_number).

    Returns a sanitized result the AP team can show or auto-send.
    """
    domain = _domain_from_email(sender_email)
    needle = _norm_invoice(invoice_number)
    if not domain:
        return VendorInquiryResult(
            found=False, no_match_reason="missing_sender_domain",
        )
    if not needle:
        return VendorInquiryResult(
            found=False, no_match_reason="missing_invoice_number",
        )

    profile = _vendor_profile_for_domain(db, organization_id, domain)
    if profile is None:
        return VendorInquiryResult(
            found=False, no_match_reason="sender_domain_not_recognised",
        )
    vendor_name = profile.get("vendor_name")
    if not vendor_name:
        return VendorInquiryResult(
            found=False, no_match_reason="vendor_profile_invalid",
        )

    db.initialize()
    sql = (
        "SELECT id, state, invoice_number, updated_at, "
        "       payment_reference, metadata, currency "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND vendor_name = %s "
        "  AND UPPER(REPLACE(COALESCE(invoice_number, ''), ' ', '')) = %s "
        "ORDER BY updated_at DESC LIMIT 1"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, vendor_name, needle))
        row = cur.fetchone()

    if row is None:
        return VendorInquiryResult(
            found=False,
            no_match_reason="invoice_not_found_for_vendor",
            invoice_number=invoice_number,
        )

    row_dict = dict(row)
    state = (row_dict.get("state") or "").lower()
    sanitized_status = _AP_STATE_TO_VENDOR_STATUS.get(state, "under_review")
    payment_reference = row_dict.get("payment_reference")
    settlement_at: Optional[str] = None

    # If paid, look up the most-recent confirmed payment_confirmation.
    if sanitized_status == "paid":
        try:
            confirmations = db.list_payment_confirmations_for_ap_item(
                organization_id, row_dict["id"],
            )
            for c in confirmations or []:
                if (c.get("status") or "").lower() == "confirmed":
                    settlement_at = c.get("settlement_at")
                    payment_reference = (
                        c.get("payment_reference") or payment_reference
                        or c.get("payment_id")
                    )
                    break
        except Exception:
            logger.exception(
                "vendor_inquiry: payment_confirmation lookup failed",
            )

    return VendorInquiryResult(
        found=True,
        status=sanitized_status,
        last_updated_at=row_dict.get("updated_at"),
        invoice_number=row_dict.get("invoice_number"),
        payment_reference=payment_reference,
        settlement_at=settlement_at,
        narrative=_STATUS_NARRATIVES.get(sanitized_status),
    )


# ── Reply formatting ──────────────────────────────────────────────


def render_inquiry_reply(
    *,
    organization_name: str,
    vendor_name: Optional[str],
    invoice_number: Optional[str],
    result: VendorInquiryResult,
) -> Dict[str, str]:
    """Plain-text reply ready to send (or attach as a Gmail draft).

    Returns ``{"subject": str, "body": str}``.
    """
    inv_label = invoice_number or "your invoice"
    subject = (
        f"Status of invoice {inv_label}"
        if result.found
        else f"Status of invoice {inv_label} - more information needed"
    )

    if not result.found:
        body = (
            f"Hello{(' ' + vendor_name) if vendor_name else ''},\n\n"
            f"Thank you for your inquiry about invoice {inv_label}. "
            f"We could not locate this invoice in our records under "
            f"the email address you contacted us from.\n\n"
            f"Could you confirm the invoice number and the email "
            f"address it was originally sent from? We'll follow up "
            f"as soon as we can match it.\n\n"
            f"Regards,\n{organization_name} Accounts Payable\n"
        )
        return {"subject": subject, "body": body}

    status = result.status or "under_review"
    narrative = result.narrative or _STATUS_NARRATIVES.get(status, "")
    payment_block = ""
    if status == "paid":
        ref = result.payment_reference or "—"
        date = result.settlement_at or "—"
        payment_block = (
            f"\n  Payment reference: {ref}"
            f"\n  Settlement date:   {date}\n"
        )

    body = (
        f"Hello{(' ' + vendor_name) if vendor_name else ''},\n\n"
        f"Thank you for your inquiry about invoice "
        f"{result.invoice_number or inv_label}.\n\n"
        f"Current status: {status.replace('_', ' ').title()}\n"
        f"{narrative}\n"
        f"{payment_block}"
        f"\n"
        f"If you need anything further, please reply to this thread.\n\n"
        f"Regards,\n{organization_name} Accounts Payable\n"
    )
    return {"subject": subject, "body": body}
