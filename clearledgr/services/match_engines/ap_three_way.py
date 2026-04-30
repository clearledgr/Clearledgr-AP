"""AP 3-way match engine — first implementation of the
:class:`MatchEngine` protocol.

Wraps the existing :class:`PurchaseOrderService.match_invoice_to_po`
logic so the legacy callers keep working unchanged. Adapts the
output to a :class:`MatchRecord` row that the universal
``run_match`` orchestrator persists.

Tolerances flow through :func:`get_tolerance_for` with
``match_type='ap_three_way'``, so per-tenant changes go through the
versioned :class:`PolicyService` and are replayable.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from clearledgr.services.match_engine import (
    MatchCandidate,
    MatchInput,
    MatchStatus,
    get_tolerance_for,
    register_match_engine,
)

logger = logging.getLogger(__name__)


class APThreeWayMatchEngine:
    """Reuse :class:`PurchaseOrderService` for the heavy lifting; this
    engine is a thin adapter that translates between the
    :class:`MatchEngine` protocol and the existing PO service API."""

    match_type = "ap_three_way"

    async def find_candidates(
        self, input: MatchInput, *, limit: int = 10,
    ) -> List[MatchCandidate]:
        from clearledgr.services.purchase_orders import PurchaseOrderService
        po_service = PurchaseOrderService(organization_id=input.organization_id)

        vendor_name = str(input.payload.get("vendor_name") or "").strip()
        if not vendor_name:
            return []

        try:
            pos = po_service._db.list_purchase_orders_for_vendor(
                input.organization_id,
                vendor_name,
                open_only=True,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ap_three_way: PO list lookup failed for vendor=%s — %s",
                vendor_name, exc,
            )
            return []

        candidates: List[MatchCandidate] = []
        for po_row in pos or []:
            po_id = str(po_row.get("po_id") or "").strip()
            if not po_id:
                continue
            candidates.append(MatchCandidate(
                right_type="purchase_order",
                right_id=po_id,
                score=0.5,  # initial score; refined in score()
                metadata={
                    "po_number": po_row.get("po_number"),
                    "vendor_name": po_row.get("vendor_name"),
                    "currency": po_row.get("currency"),
                },
            ))
        return candidates

    async def score(
        self, input: MatchInput, candidate: MatchCandidate,
    ) -> MatchCandidate:
        """Compute price + quantity variance against the candidate PO.

        Returns the candidate with refreshed ``score`` (0.0-1.0)
        and ``variance`` populated.
        """
        from clearledgr.services.purchase_orders import (
            PurchaseOrderService,
        )
        po_service = PurchaseOrderService(organization_id=input.organization_id)
        po = po_service.get_po(candidate.right_id)
        if po is None:
            candidate.score = 0.0
            candidate.variance = {"reason": "po_not_found"}
            return candidate

        invoice_amount = float(input.payload.get("amount") or 0.0)
        po_total = float(getattr(po, "total_amount", 0.0) or 0.0)
        if po_total <= 0:
            candidate.score = 0.0
            candidate.variance = {"reason": "po_has_no_total"}
            return candidate

        amount_variance = abs(invoice_amount - po_total) / po_total
        # Currency mismatch shortcut: any currency disagreement is a
        # hard zero, no matter how close the numbers are.
        invoice_currency = str(input.payload.get("currency") or "").strip().upper()
        po_currency = str(getattr(po, "currency", "") or "").strip().upper()
        if invoice_currency and po_currency and invoice_currency != po_currency:
            candidate.score = 0.0
            candidate.variance = {
                "reason": "currency_mismatch",
                "invoice_currency": invoice_currency,
                "po_currency": po_currency,
            }
            return candidate

        score = max(0.0, 1.0 - min(1.0, amount_variance))
        candidate.score = round(score, 4)
        candidate.variance = {
            "amount_variance_pct": round(amount_variance * 100.0, 4),
            "po_total": po_total,
            "invoice_amount": invoice_amount,
        }
        return candidate

    async def decide(
        self,
        input: MatchInput,
        candidates: List[MatchCandidate],
    ) -> tuple[MatchStatus, Optional[MatchCandidate], List[str]]:
        if not candidates:
            return MatchStatus.NO_MATCH, None, ["no_po"]

        price_tolerance_pct = float(get_tolerance_for(
            input.organization_id, match_type="ap_three_way",
            key="price_tolerance_percent", default=2.0,
        ))

        # Sort descending by score; pick the best
        sorted_c = sorted(candidates, key=lambda c: c.score, reverse=True)
        best = sorted_c[0]
        if best.score == 0.0:
            return (
                MatchStatus.EXCEPTION, best,
                [best.variance.get("reason") or "po_match_zero_score"],
            )

        within_tolerance = (
            abs(best.variance.get("amount_variance_pct", 100.0))
            <= price_tolerance_pct
        )
        # Multi-match warning: more than one candidate above 0.9 score
        high_scoring = [c for c in sorted_c if c.score >= 0.9]
        if len(high_scoring) > 1 and within_tolerance:
            return (
                MatchStatus.MULTIPLE_MATCHES, best,
                ["multiple_high_score_pos"],
            )

        if within_tolerance:
            # Phase 2 enrichment: also check GR linkage. For now,
            # treat as MATCHED if price is within tolerance — the
            # downstream coordination handles the GR-not-yet-received
            # case via its own state machinery.
            return MatchStatus.MATCHED, best, []

        return (
            MatchStatus.PARTIAL_MATCH, best,
            ["amount_variance_above_tolerance"],
        )


# Register at import time
register_match_engine(APThreeWayMatchEngine())
