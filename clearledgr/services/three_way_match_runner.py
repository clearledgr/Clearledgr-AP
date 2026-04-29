"""Three-way match runner (Wave 5 / G1).

Operator/agent-facing wrapper around the existing
:class:`PurchaseOrderService.match_invoice_to_po`. Where the underlying
service produces the match record and exception list, this module:

  * Takes an ap_items row (no kwargs juggling at the call site).
  * Runs the match against the org's stored POs + GRs.
  * Persists the rolled-up status on ``ap_items.match_status`` so
    the box-summary surfaces it in one query.
  * Emits an audit event with a stable idempotency key so re-running
    is a no-op end-to-end.
  * Returns a structured per-line breakdown suitable for the
    approval card / Slack block / Gmail sidebar.

The AP cycle reference (Stage 4) requires PO + GR + Invoice match
for goods receipts. This is the canonical entry point — every other
surface (bulk approve, agent decision, JE preview hook) calls
through here so the variance numbers stay consistent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_AP_MATCH_STATUSES = (
    "matched", "partial_match", "exception", "no_po",
)


@dataclass
class ThreeWayMatchSummary:
    """Operator-facing summary built from the underlying ThreeWayMatch."""

    ap_item_id: str
    organization_id: str
    match_status: str           # matched | partial_match | exception | no_po
    po_id: Optional[str] = None
    po_number: Optional[str] = None
    gr_id: Optional[str] = None
    invoice_amount: float = 0.0
    po_amount: Optional[float] = None
    gr_amount: Optional[float] = None
    price_variance: Optional[float] = None
    price_variance_pct: Optional[float] = None
    quantity_variance: Optional[float] = None
    currency: Optional[str] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    line_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ap_item_id": self.ap_item_id,
            "organization_id": self.organization_id,
            "match_status": self.match_status,
            "po_id": self.po_id,
            "po_number": self.po_number,
            "gr_id": self.gr_id,
            "invoice_amount": self.invoice_amount,
            "po_amount": self.po_amount,
            "gr_amount": self.gr_amount,
            "price_variance": self.price_variance,
            "price_variance_pct": self.price_variance_pct,
            "quantity_variance": self.quantity_variance,
            "currency": self.currency,
            "exceptions": list(self.exceptions),
            "line_breakdown": list(self.line_breakdown),
            "note": self.note,
        }


# ── Status mapping ──────────────────────────────────────────────────


def _to_ap_match_status(match_status_value: str, has_po: bool) -> str:
    raw = (match_status_value or "").strip().lower()
    if raw == "matched":
        return "matched"
    if raw in ("partial_match", "partial-match"):
        return "partial_match"
    if not has_po:
        return "no_po"
    return "exception"


# ── Line breakdown helper ───────────────────────────────────────────


def _build_line_breakdown(
    *,
    invoice_lines: Optional[List[Dict[str, Any]]],
    po,
    gr,
    price_tolerance_pct: float,
    quantity_tolerance_pct: float,
) -> List[Dict[str, Any]]:
    """One row per invoice line × matching PO line × GR line.

    Each row carries the per-line price + quantity variance so the
    operator can see exactly which line(s) are out of tolerance,
    not just the aggregate."""
    out: List[Dict[str, Any]] = []
    if not invoice_lines:
        return out
    po_lines = list(getattr(po, "line_items", None) or []) if po else []
    gr_lines = list(getattr(gr, "line_items", None) or []) if gr else []

    for inv in invoice_lines:
        inv_qty = float(inv.get("quantity") or 0)
        inv_price = float(inv.get("unit_price") or 0)
        inv_amount = float(inv.get("amount") or (inv_qty * inv_price) or 0)
        inv_desc = str(inv.get("description") or "")
        inv_item_code = str(inv.get("item_code") or inv.get("sku") or "")

        po_match_line = None
        for pl in po_lines:
            pl_item = str(getattr(pl, "item_number", "") or "")
            pl_desc = str(getattr(pl, "description", "") or "")
            if (
                inv_item_code and pl_item
                and inv_item_code == pl_item
            ) or (
                inv_desc and pl_desc
                and inv_desc.strip().lower() == pl_desc.strip().lower()
            ):
                po_match_line = pl
                break

        gr_match_qty = 0.0
        if po_match_line is not None and gr_lines:
            for gl in gr_lines:
                if getattr(gl, "po_line_id", None) == getattr(po_match_line, "line_id", None):
                    gr_match_qty += float(getattr(gl, "quantity_received", 0) or 0)

        po_qty = float(getattr(po_match_line, "quantity", 0) or 0) if po_match_line else None
        po_unit = float(getattr(po_match_line, "unit_price", 0) or 0) if po_match_line else None
        price_var = (
            (inv_price - po_unit) if (po_unit is not None and po_unit > 0) else None
        )
        price_var_pct = (
            (abs(price_var) / po_unit * 100) if (price_var is not None and po_unit and po_unit > 0)
            else None
        )
        qty_var = (inv_qty - po_qty) if (po_qty is not None) else None
        qty_var_pct = (
            (abs(qty_var) / po_qty * 100) if (qty_var is not None and po_qty and po_qty > 0)
            else None
        )

        match_flag = "matched"
        if po_match_line is None:
            match_flag = "no_po_line"
        else:
            if (
                price_var_pct is not None
                and price_var_pct > price_tolerance_pct
            ):
                match_flag = "price_variance"
            elif (
                qty_var_pct is not None
                and qty_var_pct > quantity_tolerance_pct
            ):
                match_flag = "quantity_variance"
            elif (
                gr_lines
                and gr_match_qty + 0.0001 < inv_qty
            ):
                match_flag = "over_invoiced_vs_grn"

        out.append({
            "description": inv_desc,
            "item_code": inv_item_code or None,
            "invoice_quantity": inv_qty,
            "invoice_unit_price": inv_price,
            "invoice_amount": inv_amount,
            "po_quantity": po_qty,
            "po_unit_price": po_unit,
            "gr_quantity_received": (
                gr_match_qty if gr_lines else None
            ),
            "price_variance": price_var,
            "price_variance_pct": price_var_pct,
            "quantity_variance": qty_var,
            "quantity_variance_pct": qty_var_pct,
            "match_flag": match_flag,
        })
    return out


# ── Entry point ─────────────────────────────────────────────────────


def run_three_way_match(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    actor: Optional[str] = None,
) -> Optional[ThreeWayMatchSummary]:
    """Run + persist the 3-way match for one AP item.

    Returns ``None`` when the AP item is not found or doesn't belong
    to the organization. Always-best-effort — exceptions during the
    match itself bubble up; an "no PO" outcome is a *successful*
    match call that returns status='no_po'.
    """
    from clearledgr.services.purchase_orders import (
        get_purchase_order_service,
    )

    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        return None

    invoice_lines: List[Dict[str, Any]] = []
    raw_meta = item.get("metadata")
    if isinstance(raw_meta, str):
        try:
            import json as _json
            raw_meta = _json.loads(raw_meta) if raw_meta else {}
        except Exception:
            raw_meta = {}
    if isinstance(raw_meta, dict):
        candidate = raw_meta.get("line_items")
        if isinstance(candidate, list):
            invoice_lines = list(candidate)

    service = get_purchase_order_service(organization_id)

    match = service.match_invoice_to_po(
        invoice_id=ap_item_id,
        invoice_amount=float(item.get("amount") or 0),
        invoice_vendor=str(item.get("vendor_name") or ""),
        invoice_po_number=str(item.get("po_number") or ""),
        invoice_lines=invoice_lines or None,
        invoice_currency=str(item.get("currency") or ""),
    )

    raw_po_id = getattr(match, "po_id", None) or None
    if raw_po_id == "":
        raw_po_id = None
    has_po = bool(raw_po_id)
    rolled_up_status = _to_ap_match_status(
        getattr(match.status, "value", None) or "",
        has_po=has_po,
    )

    # Resolve PO + GR full objects so the line-breakdown helper has
    # access to po.line_items / gr.line_items.
    po_obj = None
    gr_obj = None
    if raw_po_id:
        po_obj = service.get_po(raw_po_id)
        if getattr(match, "gr_id", None):
            for gr in service.get_goods_receipts_for_po(raw_po_id):
                if gr.gr_id == match.gr_id:
                    gr_obj = gr
                    break

    line_breakdown = _build_line_breakdown(
        invoice_lines=invoice_lines,
        po=po_obj,
        gr=gr_obj,
        price_tolerance_pct=service.PRICE_TOLERANCE_PERCENT,
        quantity_tolerance_pct=service.QUANTITY_TOLERANCE_PERCENT,
    )

    invoice_amount = float(item.get("amount") or 0)
    po_amount = (
        float(po_obj.total_amount) if po_obj is not None else None
    )
    price_variance = (
        invoice_amount - po_amount if po_amount is not None else None
    )
    price_variance_pct = (
        (abs(price_variance) / po_amount * 100)
        if (price_variance is not None and po_amount and po_amount > 0)
        else None
    )

    raw_gr_id = getattr(match, "gr_id", None) or None
    if raw_gr_id == "":
        raw_gr_id = None
    summary = ThreeWayMatchSummary(
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        match_status=rolled_up_status,
        po_id=raw_po_id,
        po_number=(getattr(po_obj, "po_number", None) if po_obj is not None else None),
        gr_id=raw_gr_id,
        invoice_amount=invoice_amount,
        po_amount=po_amount,
        gr_amount=getattr(match, "gr_amount", None),
        price_variance=price_variance,
        price_variance_pct=price_variance_pct,
        quantity_variance=getattr(match, "quantity_variance", None),
        currency=str(item.get("currency") or "") or None,
        exceptions=list(getattr(match, "exceptions", None) or []),
        line_breakdown=line_breakdown,
        note=(
            f"Resolved PO {po_obj.po_number}" if po_obj is not None
            else "No PO matched"
        ),
    )

    # Persist match_status on the AP item so the box summary + the
    # match-icon row in the Slack block stay one query away.
    persist_kwargs: Dict[str, Any] = {
        "match_status": summary.match_status,
        "_actor_type": "user" if actor else "system",
        "_actor_id": actor or "three_way_match_runner",
        "_source": "three_way_match",
    }
    if summary.po_number and not item.get("po_number"):
        persist_kwargs["po_number"] = summary.po_number
    if summary.gr_id and not item.get("grn_reference"):
        persist_kwargs["grn_reference"] = summary.gr_id
    try:
        db.update_ap_item(ap_item_id, **persist_kwargs)
    except Exception:
        logger.exception(
            "three_way_match: persist match_status failed ap_item=%s",
            ap_item_id,
        )

    # Audit emit — keyed so re-runs are idempotent at the audit layer.
    try:
        db.append_audit_event({
            "ap_item_id": ap_item_id,
            "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": "three_way_match_evaluated",
            "actor_type": "user" if actor else "system",
            "actor_id": actor or "three_way_match_runner",
            "organization_id": organization_id,
            "source": "three_way_match",
            "idempotency_key": (
                f"three_way_match:{organization_id}:{ap_item_id}:"
                f"{summary.po_id or 'no_po'}:{summary.gr_id or 'no_gr'}:"
                f"{summary.match_status}"
            ),
            "metadata": {
                "match_status": summary.match_status,
                "po_id": summary.po_id,
                "po_number": summary.po_number,
                "gr_id": summary.gr_id,
                "invoice_amount": summary.invoice_amount,
                "po_amount": summary.po_amount,
                "gr_amount": summary.gr_amount,
                "price_variance": summary.price_variance,
                "price_variance_pct": summary.price_variance_pct,
                "quantity_variance": summary.quantity_variance,
                "exception_count": len(summary.exceptions),
            },
        })
    except Exception:
        logger.exception(
            "three_way_match: audit emit failed ap_item=%s", ap_item_id,
        )

    return summary
