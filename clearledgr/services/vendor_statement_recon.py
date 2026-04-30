"""Vendor statement reconciliation — match statement line items against AP records.

Accepts a parsed vendor statement (list of line items with date, reference,
amount) and matches each against posted AP items for the same vendor.

Produces a reconciliation report with:
- Matched items (statement line ↔ AP item)
- Unmatched on statement (vendor claims we owe, we have no record)
- Unmatched in Clearledgr (we have an AP item, statement doesn't show it)
- Amount discrepancies (matched by reference but amounts differ)

Never raises — returns empty report on error.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from clearledgr.core.money import money_sum, money_to_float
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Matching tolerance: dates within N days are considered a match
DATE_TOLERANCE_DAYS = 5
# Amount tolerance: amounts within this % are considered close enough to flag (not reject)
AMOUNT_TOLERANCE_PCT = 0.01  # 1%


class VendorStatementRecon:
    """Reconcile a vendor statement against Clearledgr AP items."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def reconcile(
        self,
        vendor_name: str,
        statement_items: List[Dict[str, Any]],
        period_days: int = 180,
    ) -> Dict[str, Any]:
        """Run reconciliation.

        Args:
            vendor_name: Vendor to reconcile
            statement_items: List of dicts, each with:
                - date: str (YYYY-MM-DD)
                - reference: str (invoice number or reference)
                - amount: float
                - description: str (optional)
            period_days: How far back to look for AP items

        Returns:
            Reconciliation report dict.
        """
        try:
            # Load AP items for this vendor
            ap_items = self._load_ap_items(vendor_name, period_days)

            matched: List[Dict[str, Any]] = []
            unmatched_statement: List[Dict[str, Any]] = []
            discrepancies: List[Dict[str, Any]] = []

            # Track which AP items have been matched
            matched_ap_ids = set()

            for stmt_item in statement_items:
                stmt_ref = str(stmt_item.get("reference") or "").strip()
                stmt_amount = float(stmt_item.get("amount") or 0)
                stmt_date = str(stmt_item.get("date") or "")
                stmt_desc = str(stmt_item.get("description") or "")

                match = self._find_match(stmt_ref, stmt_amount, stmt_date, ap_items, matched_ap_ids)

                if match:
                    ap_item = match["ap_item"]
                    matched_ap_ids.add(ap_item["id"])

                    ap_amount = float(ap_item.get("amount") or 0)
                    amount_diff = abs(stmt_amount - ap_amount)
                    amounts_match = amount_diff < max(0.01, stmt_amount * AMOUNT_TOLERANCE_PCT)

                    entry = {
                        "statement_reference": stmt_ref,
                        "statement_amount": stmt_amount,
                        "statement_date": stmt_date,
                        "ap_item_id": ap_item["id"],
                        "ap_invoice_number": ap_item.get("invoice_number", ""),
                        "ap_amount": ap_amount,
                        "ap_state": ap_item.get("state", ""),
                        "match_type": match["match_type"],
                    }

                    if amounts_match:
                        matched.append(entry)
                    else:
                        entry["amount_difference"] = round(stmt_amount - ap_amount, 2)
                        discrepancies.append(entry)
                else:
                    unmatched_statement.append({
                        "reference": stmt_ref,
                        "amount": stmt_amount,
                        "date": stmt_date,
                        "description": stmt_desc,
                    })

            # AP items not on the statement
            unmatched_clearledgr = []
            for ap in ap_items:
                if ap["id"] not in matched_ap_ids:
                    unmatched_clearledgr.append({
                        "ap_item_id": ap["id"],
                        "invoice_number": ap.get("invoice_number", ""),
                        "amount": float(ap.get("amount") or 0),
                        "state": ap.get("state", ""),
                        "created_at": ap.get("created_at", ""),
                    })

            # Penny-exact totals — reconciliation compares these two.
            # Float sums drift by a cent across ~100 line items which
            # masquerades as a real discrepancy.
            total_statement = money_sum(s.get("amount") for s in statement_items)
            total_clearledgr = money_sum(a.get("amount") for a in ap_items)

            return {
                "organization_id": self.organization_id,
                "vendor_name": vendor_name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "period_days": period_days,
                "summary": {
                    "statement_line_count": len(statement_items),
                    "ap_item_count": len(ap_items),
                    "matched_count": len(matched),
                    "discrepancy_count": len(discrepancies),
                    "unmatched_on_statement": len(unmatched_statement),
                    "unmatched_in_clearledgr": len(unmatched_clearledgr),
                    "statement_total": money_to_float(total_statement),
                    "clearledgr_total": money_to_float(total_clearledgr),
                    "difference": money_to_float(total_statement - total_clearledgr),
                    "match_rate_pct": round(
                        len(matched) / len(statement_items) * 100, 1
                    ) if statement_items else 0.0,
                },
                "matched": matched,
                "discrepancies": discrepancies,
                "unmatched_on_statement": unmatched_statement,
                "unmatched_in_clearledgr": unmatched_clearledgr,
            }

        except Exception as exc:
            logger.error("[VendorRecon] reconcile failed: %s", exc)
            return {
                "organization_id": self.organization_id,
                "vendor_name": vendor_name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": {},
                "matched": [],
                "discrepancies": [],
                "unmatched_on_statement": [],
                "unmatched_in_clearledgr": [],
                "error": str(exc),
            }

    def _load_ap_items(self, vendor_name: str, period_days: int) -> List[Dict[str, Any]]:
        """Load posted/closed AP items for this vendor."""
        try:
            # Get all items for vendor, then filter to posted/closed
            items = self.db.get_ap_items_by_vendor(
                self.organization_id, vendor_name, days=period_days, limit=500,
            )
            return [
                i for i in items
                if i.get("state") in ("posted_to_erp", "closed", "approved", "ready_to_post")
            ]
        except Exception as exc:
            logger.warning("[VendorRecon] _load_ap_items failed: %s", exc)
            return []

    def _find_match(
        self,
        stmt_ref: str,
        stmt_amount: float,
        stmt_date: str,
        ap_items: List[Dict[str, Any]],
        already_matched: set,
    ) -> Optional[Dict[str, Any]]:
        """Find the best matching AP item for a statement line."""
        # Strategy 1: Exact reference match
        if stmt_ref:
            norm_ref = self._normalize_ref(stmt_ref)
            for ap in ap_items:
                if ap["id"] in already_matched:
                    continue
                ap_ref = self._normalize_ref(ap.get("invoice_number") or "")
                if norm_ref and ap_ref and norm_ref == ap_ref:
                    return {"ap_item": ap, "match_type": "reference_exact"}

            # Strategy 2: Reference contains match
            for ap in ap_items:
                if ap["id"] in already_matched:
                    continue
                ap_ref = self._normalize_ref(ap.get("invoice_number") or "")
                if norm_ref and ap_ref and (norm_ref in ap_ref or ap_ref in norm_ref):
                    return {"ap_item": ap, "match_type": "reference_partial"}

        # Strategy 3: Amount + date proximity match
        if stmt_amount and stmt_date:
            stmt_d = self._parse_date(stmt_date)
            for ap in ap_items:
                if ap["id"] in already_matched:
                    continue
                ap_amount = float(ap.get("amount") or 0)
                if abs(stmt_amount - ap_amount) < max(0.01, stmt_amount * AMOUNT_TOLERANCE_PCT):
                    ap_date = self._parse_date(ap.get("invoice_date") or ap.get("created_at") or "")
                    if stmt_d and ap_date and abs((stmt_d - ap_date).days) <= DATE_TOLERANCE_DAYS:
                        return {"ap_item": ap, "match_type": "amount_date"}

        # Strategy 4: Amount-only match (weakest)
        if stmt_amount:
            for ap in ap_items:
                if ap["id"] in already_matched:
                    continue
                ap_amount = float(ap.get("amount") or 0)
                if abs(stmt_amount - ap_amount) < 0.01:
                    return {"ap_item": ap, "match_type": "amount_only"}

        return None

    @staticmethod
    def _normalize_ref(ref: str) -> str:
        """Normalize an invoice reference for comparison."""
        import re
        return re.sub(r'[^a-zA-Z0-9]', '', ref).lower()

    @staticmethod
    def _parse_date(val: str) -> Optional[date]:
        if not val:
            return None
        try:
            return date.fromisoformat(str(val)[:10])
        except (ValueError, TypeError):
            return None


def get_vendor_statement_recon(organization_id: str = "default") -> VendorStatementRecon:
    return VendorStatementRecon(organization_id=organization_id)
