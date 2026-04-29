"""Auto-post accrual JE for received-not-billed (Wave 5 / G5).

At month-end, goods or services have been received (a GRN exists)
but the corresponding vendor invoice hasn't arrived yet. The
period-close process accrues these liabilities so the closing
balance includes the obligation; when the invoice lands next
period, the accrual auto-reverses.

This module computes the canonical accrual JE for one period:

  * Source: ``goods_receipts`` rows where the linked PO has open
    quantity AND no matching ap_items.erp_journal_entry_id has been
    posted in the period.
  * Per GRN line, accrual amount = ``quantity_received × unit_price``
    drawn from the PO line. The credit lands on
    "Accrued Expenses / Accrued Liabilities" (GL account
    ``accrued_expenses``); the debit on the expense account from
    ``DEFAULT_ACCOUNT_MAP``.

Output: an :class:`AccrualJEProposal` ready to:
  * Render as a JE preview in the workspace (operator review).
  * Post via the org's ERP using the canonical ``erp_router.post_to_*``.
  * Schedule a reversal entry on the first day of the next period.

This is **proposal-only** in v1 — operator triggers via the API, the
actual post is the next-pass integration. The reversal scheduling is
a metadata field on the JE; the period-close runner picks it up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_TWO_PLACES)
    return Decimal(str(value)).quantize(_TWO_PLACES)


@dataclass
class AccrualLine:
    """One row of the accrual JE proposal."""

    po_id: str
    po_number: str
    gr_id: str
    gr_number: str
    vendor_name: str
    item_number: str
    description: str
    quantity_received: Decimal
    unit_price: Decimal
    accrual_amount: Decimal
    expense_account: str
    accrual_account: str
    currency: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "po_id": self.po_id,
            "po_number": self.po_number,
            "gr_id": self.gr_id,
            "gr_number": self.gr_number,
            "vendor_name": self.vendor_name,
            "item_number": self.item_number,
            "description": self.description,
            "quantity_received": float(self.quantity_received),
            "unit_price": float(self.unit_price),
            "accrual_amount": float(self.accrual_amount),
            "expense_account": self.expense_account,
            "accrual_account": self.accrual_account,
            "currency": self.currency,
        }


@dataclass
class JELineEntry:
    """A Dr or Cr line on the proposed JE."""

    direction: str           # "debit" | "credit"
    account_code: str
    account_label: str
    amount: Decimal
    currency: str
    description: str


@dataclass
class AccrualJEProposal:
    """Full month-end accrual JE proposal for one period."""

    organization_id: str
    period_start: str
    period_end: str
    accrual_date: str        # = period_end
    reversal_date: str       # first day of next period
    erp_type: str
    currency: str
    lines: List[AccrualLine] = field(default_factory=list)
    je_lines: List[JELineEntry] = field(default_factory=list)
    debit_total: Decimal = Decimal("0.00")
    credit_total: Decimal = Decimal("0.00")
    balanced: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "accrual_date": self.accrual_date,
            "reversal_date": self.reversal_date,
            "erp_type": self.erp_type,
            "currency": self.currency,
            "lines": [ln.to_dict() for ln in self.lines],
            "je_lines": [
                {
                    "direction": je.direction,
                    "account_code": je.account_code,
                    "account_label": je.account_label,
                    "amount": float(je.amount),
                    "currency": je.currency,
                    "description": je.description,
                }
                for je in self.je_lines
            ],
            "debit_total": float(self.debit_total),
            "credit_total": float(self.credit_total),
            "balanced": self.balanced,
            "notes": list(self.notes),
            "accrual_count": len(self.lines),
        }


# ── Eligibility ────────────────────────────────────────────────────


def _next_period_start(period_end: str) -> str:
    """Return the first day of the period after ``period_end`` (a
    YYYY-MM-DD or YYYY-MM-DDTHH:MM... string)."""
    try:
        d = datetime.fromisoformat(period_end[:10]).date()
    except ValueError:
        try:
            d = datetime.strptime(period_end[:10], "%Y-%m-%d").date()
        except ValueError:
            d = date.today()
    return (d + timedelta(days=1)).isoformat()


def _has_invoice_posted_for_po(
    db,
    *,
    organization_id: str,
    po_number: str,
    period_end: str,
) -> bool:
    """Check whether any AP item with this po_number has been posted
    to the ERP within the period."""
    if not po_number:
        return False
    db.initialize()
    sql = (
        "SELECT 1 FROM ap_items "
        "WHERE organization_id = %s "
        "  AND po_number = %s "
        "  AND state IN ('posted_to_erp', 'awaiting_payment', "
        "                'payment_in_flight', 'payment_executed', 'closed') "
        "  AND COALESCE(erp_posted_at, created_at) <= %s "
        "LIMIT 1"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            sql, (organization_id, po_number, period_end + "T23:59:59"),
        )
        return cur.fetchone() is not None


# ── Builder ────────────────────────────────────────────────────────


def build_accrual_je_proposal(
    db,
    *,
    organization_id: str,
    period_start: str,
    period_end: str,
    erp_type: str = "xero",
    currency: str = "GBP",
) -> AccrualJEProposal:
    """Walk goods_receipts in the period, identify ones without a
    matching posted invoice, build the accrual JE proposal."""
    from clearledgr.integrations.erp_router import (
        _get_org_gl_map,
        get_account_code,
    )
    from clearledgr.services.purchase_orders import (
        get_purchase_order_service,
    )

    proposal = AccrualJEProposal(
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
        accrual_date=period_end,
        reversal_date=_next_period_start(period_end),
        erp_type=erp_type,
        currency=currency,
    )

    gl_map = _get_org_gl_map(organization_id) or {}
    expense_acct = get_account_code(erp_type, "expenses", gl_map)
    # accrued_expenses isn't always in DEFAULT_ACCOUNT_MAP; fall back
    # to accounts_payable if missing — both are liability accounts and
    # the operator can re-map after preview. Note this in proposal.
    accrual_acct = (
        gl_map.get("accrued_expenses")
        or get_account_code(erp_type, "accrued_expenses", gl_map)
        or get_account_code(erp_type, "accounts_payable", gl_map)
    )
    if not gl_map.get("accrued_expenses"):
        proposal.notes.append(
            "No accrued_expenses GL code in org settings; defaulting "
            "to accounts_payable. Set settings_json[gl_account_map]"
            "[accrued_expenses] for a dedicated accrual account."
        )

    svc = get_purchase_order_service(organization_id)

    # Enumerate POs for the period — we look up GRs per PO since
    # there's no list_goods_receipts function org-wide.
    db.initialize()
    sql = (
        "SELECT * FROM purchase_orders "
        "WHERE organization_id = %s "
        "  AND status IN ('partially_received', 'fully_received', 'approved') "
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id,))
        po_rows = cur.fetchall()

    seen_grns: set = set()
    for po_row in po_rows:
        po_dict = dict(po_row)
        po_id = po_dict.get("po_id") or po_dict.get("id")
        po_number = po_dict.get("po_number") or ""
        if not po_id:
            continue

        # Skip POs whose invoice has already posted in the period.
        if _has_invoice_posted_for_po(
            db,
            organization_id=organization_id,
            po_number=po_number,
            period_end=period_end,
        ):
            continue

        po = svc.get_po(po_id)
        if not po:
            continue
        po_currency = (po.currency or currency or "GBP").upper()

        for gr in svc.get_goods_receipts_for_po(po_id):
            gr_received_at = (
                gr.created_at.isoformat() if hasattr(gr.created_at, "isoformat")
                else str(gr.created_at or "")
            )[:10]
            if gr_received_at and (
                gr_received_at < period_start[:10]
                or gr_received_at > period_end[:10]
            ):
                continue
            if gr.gr_id in seen_grns:
                continue
            seen_grns.add(gr.gr_id)

            for gr_line in gr.line_items:
                # Find the matching PO line for the unit price.
                po_unit_price = Decimal("0.00")
                po_item_no = gr_line.item_number
                po_description = gr_line.description
                for pl in po.line_items:
                    if pl.line_id == gr_line.po_line_id:
                        po_unit_price = _money(pl.unit_price)
                        po_item_no = pl.item_number or po_item_no
                        po_description = pl.description or po_description
                        break
                qty = _money(gr_line.quantity_received)
                if qty <= 0:
                    continue
                accrual_amt = (qty * po_unit_price).quantize(_TWO_PLACES)
                if accrual_amt <= 0:
                    continue
                proposal.lines.append(AccrualLine(
                    po_id=po.po_id,
                    po_number=po.po_number,
                    gr_id=gr.gr_id,
                    gr_number=gr.gr_number,
                    vendor_name=po.vendor_name,
                    item_number=po_item_no,
                    description=po_description,
                    quantity_received=qty,
                    unit_price=po_unit_price,
                    accrual_amount=accrual_amt,
                    expense_account=expense_acct,
                    accrual_account=accrual_acct,
                    currency=po_currency,
                ))

    # Aggregate JE lines by account (one Dr per expense GL, one
    # rolled-up Cr to accrued expenses).
    expense_total: Decimal = Decimal("0.00")
    for ln in proposal.lines:
        expense_total += ln.accrual_amount

    if expense_total > 0:
        proposal.je_lines.append(JELineEntry(
            direction="debit",
            account_code=expense_acct,
            account_label="Expense (accrued)",
            amount=expense_total,
            currency=currency,
            description=f"Month-end accrual {period_end} (received-not-billed)",
        ))
        proposal.je_lines.append(JELineEntry(
            direction="credit",
            account_code=accrual_acct,
            account_label="Accrued expenses",
            amount=expense_total,
            currency=currency,
            description=f"Reverse on {proposal.reversal_date}",
        ))
        proposal.debit_total = expense_total
        proposal.credit_total = expense_total
        proposal.balanced = True
    else:
        proposal.notes.append(
            "No received-not-billed liability identified for the period."
        )

    return proposal


def render_accrual_proposal_text(proposal: AccrualJEProposal) -> str:
    """Plain-text rendering for embedding in approval surfaces."""
    width = 78
    lines: List[str] = []
    lines.append(
        f"Month-end accrual JE preview — period {proposal.period_start} "
        f"to {proposal.period_end}"
    )
    lines.append(
        f"Accrual date: {proposal.accrual_date}  |  "
        f"Reversal date: {proposal.reversal_date}  |  "
        f"ERP: {proposal.erp_type}  |  Currency: {proposal.currency}"
    )
    lines.append("-" * width)
    lines.append(f"{len(proposal.lines)} line(s) accrued from received-not-billed GRNs:")
    for ln in proposal.lines[:25]:
        lines.append(
            f"  {ln.po_number} {ln.gr_number} {ln.vendor_name[:18]:<18} "
            f"{ln.item_number[:8]:<8} qty={float(ln.quantity_received):>6.2f} "
            f"× {float(ln.unit_price):>8.2f} = "
            f"{float(ln.accrual_amount):>10.2f} {ln.currency}"
        )
    if len(proposal.lines) > 25:
        lines.append(f"  ... +{len(proposal.lines) - 25} more")
    lines.append("-" * width)
    for je in proposal.je_lines:
        prefix = "Dr  " if je.direction == "debit" else "Cr  "
        lines.append(
            f"{prefix}{je.account_label} ({je.account_code})    "
            f"{float(je.amount):>10.2f} {je.currency}"
        )
    flag = "balanced" if proposal.balanced else "unbalanced"
    lines.append(
        f"Debit: {float(proposal.debit_total):,.2f}    "
        f"Credit: {float(proposal.credit_total):,.2f}    {flag}"
    )
    if proposal.notes:
        lines.append("")
        for note in proposal.notes:
            lines.append(f"• {note}")
    return "\n".join(lines)
