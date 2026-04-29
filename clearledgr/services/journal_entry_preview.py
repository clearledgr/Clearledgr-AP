"""Journal-entry preview generator (Wave 3 / E4).

Given an AP item (with the VAT split E2 lands on it) and the org's
GL account map (already wired through erp_router._get_org_gl_map),
produce the canonical Dr/Cr lines that the ERP will post — plus a
human-readable representation suitable for embedding on every
approval surface (Slack/Teams/Gmail sidebar/NetSuite/SAP Fiori).

The preview is **deterministic**: same AP item + same map = same
JE shape, every time. No LLM in the loop. The agent uses this
output verbatim for both the pre-approval card AND the actual
post-to-ERP payload (the integrations layer translates lines to
ERP-native verbs).

Treatments:

  domestic
      Dr  Expense        net
      Dr  VAT input      vat
      Cr  Accounts Payable  gross

  reverse_charge
      Dr  Expense          gross
      Cr  Accounts Payable gross
      Dr  VAT input        vat   (self-assessed reclaim)
      Cr  VAT output       vat   (self-assessed output)
      → net cash impact = zero; both sides land on the VAT return.

  zero_rated / exempt / out_of_scope
      Dr  Expense          gross
      Cr  Accounts Payable gross
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class JELine:
    direction: str          # "debit" | "credit"
    account_code: str
    account_label: str
    amount: Decimal
    currency: str
    line_role: str          # "expense" | "vat_input" | "vat_output" | "accounts_payable"
    description: Optional[str] = None


@dataclass
class JEPreview:
    ap_item_id: str
    erp_type: str
    treatment: str           # echoes ap_items.tax_treatment
    vat_code: str            # echoes ap_items.vat_code
    currency: str
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    vat_rate: Decimal        # in percent
    lines: List[JELine] = field(default_factory=list)
    debit_total: Decimal = Decimal("0.00")
    credit_total: Decimal = Decimal("0.00")
    balanced: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ap_item_id": self.ap_item_id,
            "erp_type": self.erp_type,
            "treatment": self.treatment,
            "vat_code": self.vat_code,
            "currency": self.currency,
            "gross_amount": float(self.gross_amount),
            "net_amount": float(self.net_amount),
            "vat_amount": float(self.vat_amount),
            "vat_rate": float(self.vat_rate),
            "debit_total": float(self.debit_total),
            "credit_total": float(self.credit_total),
            "balanced": self.balanced,
            "notes": list(self.notes),
            "lines": [
                {
                    "direction": ln.direction,
                    "account_code": ln.account_code,
                    "account_label": ln.account_label,
                    "amount": float(ln.amount),
                    "currency": ln.currency,
                    "line_role": ln.line_role,
                    "description": ln.description,
                }
                for ln in self.lines
            ],
        }


_ROLE_LABELS = {
    "expense": "Expense",
    "vat_input": "VAT — input (reclaim)",
    "vat_output": "VAT — output (self-assessed)",
    "accounts_payable": "Accounts Payable",
}


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def build_je_preview(
    *,
    ap_item: Dict[str, Any],
    erp_type: str,
    gl_account_map: Optional[Dict[str, str]] = None,
) -> JEPreview:
    """Pure compute — no DB writes."""
    from clearledgr.integrations.erp_router import get_account_code

    gross = _money(ap_item.get("amount") or 0)
    net = _money(ap_item.get("net_amount"))
    vat = _money(ap_item.get("vat_amount"))
    rate = _money(ap_item.get("vat_rate"))
    currency = str(ap_item.get("currency") or "GBP")
    treatment = str(ap_item.get("tax_treatment") or "domestic").lower()
    vat_code = str(ap_item.get("vat_code") or "T1")
    notes: List[str] = []

    # Backfill: if VAT split hasn't been computed yet, fall through to
    # net=gross + vat=0 and tag the preview as un-split. This keeps the
    # surface working for legacy AP items pre-E2.
    if net == 0 and vat == 0:
        net = gross
        vat = Decimal("0.00")
        notes.append(
            "VAT split not computed — net defaults to gross. Run "
            "/api/workspace/ap-items/{id}/vat-recalculate for a "
            "compliant preview."
        )

    expense_acct = get_account_code(erp_type, "expenses", gl_account_map)
    ap_acct = get_account_code(erp_type, "accounts_payable", gl_account_map)
    vat_in_acct = get_account_code(erp_type, "vat_input", gl_account_map)
    vat_out_acct = get_account_code(erp_type, "vat_output", gl_account_map)

    lines: List[JELine] = []

    if treatment == "domestic":
        lines.append(JELine(
            direction="debit", account_code=expense_acct,
            account_label=_ROLE_LABELS["expense"],
            amount=net, currency=currency, line_role="expense",
            description=f"Bill {ap_item.get('invoice_number') or ap_item.get('id')}",
        ))
        if vat > 0:
            lines.append(JELine(
                direction="debit", account_code=vat_in_acct,
                account_label=_ROLE_LABELS["vat_input"],
                amount=vat, currency=currency, line_role="vat_input",
                description=f"VAT input @ {float(rate)}% ({vat_code})",
            ))
        lines.append(JELine(
            direction="credit", account_code=ap_acct,
            account_label=_ROLE_LABELS["accounts_payable"],
            amount=gross, currency=currency, line_role="accounts_payable",
            description=f"Bill payable to {ap_item.get('vendor_name') or 'vendor'}",
        ))
    elif treatment == "reverse_charge":
        lines.append(JELine(
            direction="debit", account_code=expense_acct,
            account_label=_ROLE_LABELS["expense"],
            amount=gross, currency=currency, line_role="expense",
            description=f"Bill {ap_item.get('invoice_number') or ap_item.get('id')}",
        ))
        lines.append(JELine(
            direction="credit", account_code=ap_acct,
            account_label=_ROLE_LABELS["accounts_payable"],
            amount=gross, currency=currency, line_role="accounts_payable",
            description=f"Bill payable to {ap_item.get('vendor_name') or 'vendor'}",
        ))
        if vat > 0:
            # Self-assessed pair: reclaim + output, net cash zero.
            lines.append(JELine(
                direction="debit", account_code=vat_in_acct,
                account_label=_ROLE_LABELS["vat_input"],
                amount=vat, currency=currency, line_role="vat_input",
                description=f"RC input VAT @ {float(rate)}% (self-assessed)",
            ))
            lines.append(JELine(
                direction="credit", account_code=vat_out_acct,
                account_label=_ROLE_LABELS["vat_output"],
                amount=vat, currency=currency, line_role="vat_output",
                description=f"RC output VAT @ {float(rate)}% (self-assessed)",
            ))
        notes.append(
            "Intra-EU B2B reverse charge: net cash to vendor = gross. "
            "Buyer self-accounts VAT (boxes 1 + 4 of the VAT return)."
        )
    elif treatment in ("zero_rated", "exempt", "out_of_scope"):
        lines.append(JELine(
            direction="debit", account_code=expense_acct,
            account_label=_ROLE_LABELS["expense"],
            amount=gross, currency=currency, line_role="expense",
            description=f"Bill {ap_item.get('invoice_number') or ap_item.get('id')}",
        ))
        lines.append(JELine(
            direction="credit", account_code=ap_acct,
            account_label=_ROLE_LABELS["accounts_payable"],
            amount=gross, currency=currency, line_role="accounts_payable",
            description=f"Bill payable to {ap_item.get('vendor_name') or 'vendor'}",
        ))
        if treatment == "zero_rated":
            notes.append("Zero-rated supply — no VAT line.")
        elif treatment == "exempt":
            notes.append("Exempt supply — no VAT line.")
        else:
            notes.append("Out of scope of VAT — no VAT line.")
    else:
        # Unknown treatment: treat as zero-rated to fail safe (no
        # imaginary VAT).
        lines.append(JELine(
            direction="debit", account_code=expense_acct,
            account_label=_ROLE_LABELS["expense"],
            amount=gross, currency=currency, line_role="expense",
        ))
        lines.append(JELine(
            direction="credit", account_code=ap_acct,
            account_label=_ROLE_LABELS["accounts_payable"],
            amount=gross, currency=currency, line_role="accounts_payable",
        ))
        notes.append(f"Unknown tax_treatment={treatment!r}; defaulting to zero-rated.")

    debit_total = sum(
        (ln.amount for ln in lines if ln.direction == "debit"),
        Decimal("0.00"),
    )
    credit_total = sum(
        (ln.amount for ln in lines if ln.direction == "credit"),
        Decimal("0.00"),
    )
    balanced = abs(debit_total - credit_total) <= Decimal("0.01")

    return JEPreview(
        ap_item_id=str(ap_item.get("id") or ""),
        erp_type=erp_type,
        treatment=treatment,
        vat_code=vat_code,
        currency=currency,
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        vat_rate=rate,
        lines=lines,
        debit_total=debit_total,
        credit_total=credit_total,
        balanced=balanced,
        notes=notes,
    )


def render_je_preview_text(preview: JEPreview) -> str:
    """Plain-text rendering suitable for embedding in Slack message
    blocks, Gmail sidebar, Teams adaptive cards, etc.

    Format::

        Journal Entry preview (xero · GBP, treatment: reverse_charge)
        ----------------------------------------------------------------
        Dr  Expense (400)                       1,000.00
        Dr  VAT — input (820)                     190.00
        Cr  Accounts Payable (800)              1,000.00
        Cr  VAT — output (825)                    190.00
        ----------------------------------------------------------------
        Debit total:  1,190.00 GBP    Credit total: 1,190.00 GBP    ✓ balanced
    """
    width = 64
    lines: List[str] = []
    lines.append(
        f"Journal Entry preview "
        f"({preview.erp_type} · {preview.currency}, "
        f"treatment: {preview.treatment})"
    )
    lines.append("-" * width)
    for ln in preview.lines:
        prefix = "Dr  " if ln.direction == "debit" else "Cr  "
        label = f"{ln.account_label} ({ln.account_code})"
        amount = f"{float(ln.amount):,.2f}"
        # Right-align the amount; truncate label if necessary.
        max_label = width - len(prefix) - len(amount) - 2
        if len(label) > max_label:
            label = label[: max_label - 1] + "…"
        lines.append(f"{prefix}{label:<{max_label}}  {amount}")
    lines.append("-" * width)
    debit = f"{float(preview.debit_total):,.2f}"
    credit = f"{float(preview.credit_total):,.2f}"
    flag = "✓ balanced" if preview.balanced else "⚠ unbalanced"
    lines.append(
        f"Debit total: {debit} {preview.currency}    "
        f"Credit total: {credit} {preview.currency}    {flag}"
    )
    if preview.notes:
        lines.append("")
        for note in preview.notes:
            lines.append(f"• {note}")
    return "\n".join(lines)


# ── Entry points used by approval surfaces ──────────────────────────


def get_je_preview_for_ap_item(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    erp_type: Optional[str] = None,
) -> Optional[JEPreview]:
    """Convenience: fetch the AP item, resolve the org's GL account
    map, and build the preview. Returns None if the AP item doesn't
    exist or doesn't belong to the org."""
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        return None

    resolved_erp = (erp_type or "").strip().lower()
    if not resolved_erp:
        # Resolve the org's primary ERP from the connections table.
        try:
            from clearledgr.integrations.erp_router import (
                _get_db as _erp_get_db,
            )
            erp_db = _erp_get_db()
            with erp_db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT erp_type FROM erp_connections "
                    "WHERE organization_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (organization_id,),
                )
                row = cur.fetchone()
            if row:
                resolved_erp = str(dict(row).get("erp_type") or "").lower()
        except Exception:
            resolved_erp = ""
    if not resolved_erp:
        resolved_erp = "xero"  # safe default for the EU launch

    from clearledgr.integrations.erp_router import _get_org_gl_map
    gl_map = _get_org_gl_map(organization_id)

    return build_je_preview(
        ap_item=item,
        erp_type=resolved_erp,
        gl_account_map=gl_map,
    )
