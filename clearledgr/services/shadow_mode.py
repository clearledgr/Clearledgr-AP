"""
Shadow Mode Deployment — DESIGN_THESIS.md §7.7

"New model versions run in shadow mode on live production traffic for
a minimum of 48 hours before any customer-facing change. In shadow mode,
the new model processes every incoming invoice but its output is never
shown to users and never used to make any decision."

Shadow mode:
1. Runs the candidate model's extraction/decision alongside production
2. Stores shadow output in metadata (never shown to users)
3. Compares shadow vs production decision
4. Reports agreement rate after 48 hours
5. Blocks promotion to production if agreement rate < threshold
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SHADOW_MIN_HOURS = 48
SHADOW_MIN_ITEMS = 20
SHADOW_AGREEMENT_THRESHOLD = 0.95


async def run_shadow_decision(
    *,
    invoice_data: Dict[str, Any],
    production_decision: Dict[str, Any],
    shadow_model: str = "",
    organization_id: str = "default",
    db: Any = None,
) -> Dict[str, Any]:
    """Run the shadow model's decision alongside production.

    Called from the invoice workflow AFTER the production decision is made.
    Shadow output is stored in AP item metadata but never used for routing.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    shadow_result = {
        "shadow_model": shadow_model,
        "shadow_recommendation": None,
        "shadow_confidence": None,
        "agrees_with_production": None,
        "shadow_run_at": datetime.now(timezone.utc).isoformat(),
    }

    # Run the candidate model (if configured)
    try:
        import os
        candidate_model = shadow_model or os.environ.get("SHADOW_MODEL", "")
        if not candidate_model:
            return shadow_result

        from clearledgr.services.ap_decision import APDecisionService
        shadow_service = APDecisionService(
            organization_id=organization_id,
            db=db,
            model_override=candidate_model,
        )

        shadow_decision = await shadow_service.decide(
            invoice=invoice_data.get("invoice"),
            validation_gate=invoice_data.get("validation_gate"),
            vendor_profile=invoice_data.get("vendor_profile"),
        )

        shadow_result["shadow_recommendation"] = shadow_decision.recommendation
        shadow_result["shadow_confidence"] = shadow_decision.confidence
        shadow_result["shadow_reasoning"] = (shadow_decision.reasoning or "")[:256]

        # Compare with production
        prod_rec = production_decision.get("recommendation") or ""
        shadow_rec = shadow_decision.recommendation or ""
        shadow_result["agrees_with_production"] = (prod_rec == shadow_rec)

    except Exception as exc:
        shadow_result["shadow_error"] = str(exc)
        logger.debug("[shadow_mode] shadow decision failed: %s", exc)

    # Store shadow result in AP item metadata
    ap_item_id = invoice_data.get("ap_item_id")
    if ap_item_id and hasattr(db, "update_ap_item"):
        try:
            item = db.get_ap_item(ap_item_id)
            if item:
                metadata = dict(item.get("metadata") or {})
                shadow_log = metadata.get("shadow_decisions") or []
                shadow_log.append(shadow_result)
                metadata["shadow_decisions"] = shadow_log[-10:]  # Keep last 10
                db.update_ap_item(ap_item_id, metadata=metadata)
        except Exception:
            pass

    return shadow_result


def get_shadow_mode_report(
    db: Any,
    organization_id: str = "default",
) -> Dict[str, Any]:
    """Generate shadow mode agreement report.

    Called to check if shadow model is ready for promotion.
    """
    items = db.list_ap_items(organization_id=organization_id, limit=1000)

    shadow_items = []
    agreements = 0
    disagreements = 0

    for item in items:
        metadata = item.get("metadata") or {}
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except Exception:
                continue
        shadow_log = metadata.get("shadow_decisions") or []
        for entry in shadow_log:
            shadow_items.append(entry)
            if entry.get("agrees_with_production") is True:
                agreements += 1
            elif entry.get("agrees_with_production") is False:
                disagreements += 1

    total = agreements + disagreements
    rate = agreements / total if total > 0 else 0.0

    # Check promotion readiness
    first_shadow_at = None
    if shadow_items:
        dates = [s.get("shadow_run_at") for s in shadow_items if s.get("shadow_run_at")]
        if dates:
            try:
                first_shadow_at = min(dates)
            except Exception:
                pass

    hours_elapsed = 0
    if first_shadow_at:
        try:
            first_dt = datetime.fromisoformat(first_shadow_at.replace("Z", "+00:00"))
            hours_elapsed = (datetime.now(timezone.utc) - first_dt).total_seconds() / 3600
        except Exception:
            pass

    can_promote = (
        total >= SHADOW_MIN_ITEMS
        and hours_elapsed >= SHADOW_MIN_HOURS
        and rate >= SHADOW_AGREEMENT_THRESHOLD
    )

    return {
        "total_shadow_decisions": total,
        "agreements": agreements,
        "disagreements": disagreements,
        "agreement_rate": round(rate, 4),
        "hours_elapsed": round(hours_elapsed, 1),
        "min_hours_required": SHADOW_MIN_HOURS,
        "min_items_required": SHADOW_MIN_ITEMS,
        "agreement_threshold": SHADOW_AGREEMENT_THRESHOLD,
        "can_promote": can_promote,
        "blocking_reasons": _get_blocking_reasons(total, hours_elapsed, rate),
    }


def _get_blocking_reasons(total: int, hours: float, rate: float) -> List[str]:
    reasons = []
    if total < SHADOW_MIN_ITEMS:
        reasons.append(f"Need {SHADOW_MIN_ITEMS} shadow decisions, have {total}")
    if hours < SHADOW_MIN_HOURS:
        reasons.append(f"Need {SHADOW_MIN_HOURS}h shadow runtime, have {hours:.1f}h")
    if rate < SHADOW_AGREEMENT_THRESHOLD:
        reasons.append(f"Agreement rate {rate:.1%} below {SHADOW_AGREEMENT_THRESHOLD:.0%} threshold")
    return reasons
