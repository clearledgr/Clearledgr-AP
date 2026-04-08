"""Vendor intelligence bootstrap.

Populates vendor_profiles and vendor_invoice_history from existing ap_items
so the AP reasoning layer has context from day one — even before any new
invoice flows through the updated pipeline.

This is idempotent: running it multiple times is safe (history rows are keyed
by ap_item_id, profile stats are recomputed from history each time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.utils import safe_float_or_none

logger = logging.getLogger(__name__)

# AP states that represent a final approved outcome
_APPROVED_STATES = {"posted_to_erp", "approved", "closed"}
# AP states that represent a rejected/declined outcome
_REJECTED_STATES = {"rejected", "cancelled"}
# Skip ephemeral / in-flight states — they have no outcome yet
_SKIP_STATES = {
    "draft", "received", "validated", "needs_info",
    "needs_approval", "pending_review", "ready_to_post",
    "posting", "failed_post",
}


def _norm_state(state: Optional[str]) -> str:
    return str(state or "").strip().lower()


def bootstrap_vendor_intelligence(
    db: Any,
    organization_id: str,
    *,
    limit: int = 5000,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Populate vendor intelligence from all existing ap_items for an org.

    Args:
        db:              ClearledgrDB instance (must have VendorStore mixed in)
        organization_id: Org to process
        limit:           Max ap_items to scan (default 5000)
        dry_run:         If True, compute stats but don't write to DB

    Returns:
        {
            "vendors_processed": int,
            "items_processed": int,
            "items_skipped": int,
            "errors": int,
            "dry_run": bool,
            "vendor_summaries": [{vendor, count, always_approved, avg_amount}...]
        }
    """
    if not hasattr(db, "list_ap_items_all"):
        raise RuntimeError("DB does not support list_ap_items_all — VendorStore not mixed in")
    if not hasattr(db, "record_vendor_invoice"):
        raise RuntimeError("DB does not support vendor intelligence tables — VendorStore not mixed in")

    logger.info(
        "[VendorBootstrap] Starting for org=%s (limit=%d, dry_run=%s)",
        organization_id, limit, dry_run,
    )

    items = db.list_ap_items_all(organization_id, limit=limit)
    logger.info("[VendorBootstrap] Found %d ap_items", len(items))

    # Pre-fetch existing history ap_item_ids to ensure idempotency
    existing_ids: set = set()
    try:
        # Fetch all history rows for the org — we only need the ap_item_id column
        sql = db._prepare_sql(
            "SELECT ap_item_id FROM vendor_invoice_history WHERE organization_id = ?"
        )
        with db.connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            for row in cur.fetchall():
                existing_ids.add(row["ap_item_id"])
    except Exception as exc:
        logger.warning("[VendorBootstrap] Could not pre-fetch existing history: %s", exc)

    items_processed = 0
    items_skipped = 0
    errors = 0
    vendor_item_counts: Dict[str, int] = {}

    for item in items:
        item_id = str(item.get("id") or "")
        vendor = str(item.get("vendor_name") or "").strip()
        state = _norm_state(item.get("state"))

        if not vendor or not item_id:
            items_skipped += 1
            continue

        # Skip items still in flight — no meaningful outcome yet
        if state in _SKIP_STATES:
            items_skipped += 1
            continue

        # Skip already-recorded items (idempotency)
        if item_id in existing_ids:
            items_skipped += 1
            vendor_item_counts[vendor] = vendor_item_counts.get(vendor, 0) + 1
            continue

        was_approved = state in _APPROVED_STATES
        final_state = state if (state in _APPROVED_STATES or state in _REJECTED_STATES) else "unknown"
        amount = safe_float_or_none(item.get("amount"))
        invoice_date = _first_date(item.get("invoice_date"), item.get("created_at"))
        exception_code = item.get("exception_code")

        # Extract agent recommendation from metadata if stored
        agent_rec = None
        try:
            import json
            meta = item.get("metadata") or "{}"
            parsed = json.loads(meta) if isinstance(meta, str) else (meta or {})
            vi = parsed.get("vendor_intelligence") or {}
            agent_rec = str(vi.get("ap_decision") or "").strip() or None
        except Exception:
            pass

        try:
            if not dry_run:
                db.record_vendor_invoice(
                    organization_id, vendor, item_id,
                    invoice_date=invoice_date,
                    amount=amount,
                    currency=str(item.get("currency") or "USD"),
                    final_state=final_state,
                    exception_code=exception_code,
                    was_approved=was_approved,
                    agent_recommendation=agent_rec,
                )
                existing_ids.add(item_id)
            items_processed += 1
            vendor_item_counts[vendor] = vendor_item_counts.get(vendor, 0) + 1
        except Exception as exc:
            logger.warning(
                "[VendorBootstrap] Failed to record history for item %s: %s", item_id, exc
            )
            errors += 1

    # Now recompute vendor profiles from the newly populated history
    vendor_summaries: List[Dict[str, Any]] = []
    for vendor in vendor_item_counts:
        try:
            if not dry_run:
                # Trigger a profile recompute by calling update from a dummy "current" outcome.
                # We use update_vendor_profile_from_outcome with the last known item for this vendor.
                last_items = [
                    i for i in items
                    if str(i.get("vendor_name") or "").strip() == vendor
                    and _norm_state(i.get("state")) in _APPROVED_STATES | _REJECTED_STATES
                ]
                if last_items:
                    last = last_items[0]
                    db.update_vendor_profile_from_outcome(
                        organization_id, vendor,
                        ap_item_id=str(last.get("id") or ""),
                        final_state=_norm_state(last.get("state")),
                        was_approved=_norm_state(last.get("state")) in _APPROVED_STATES,
                        amount=safe_float_or_none(last.get("amount")),
                        invoice_date=_first_date(last.get("invoice_date"), last.get("created_at")),
                        exception_code=last.get("exception_code"),
                    )
            profile = db.get_vendor_profile(organization_id, vendor) if not dry_run else {}
            vendor_summaries.append({
                "vendor": vendor,
                "items_in_history": vendor_item_counts[vendor],
                "avg_invoice_amount": (profile or {}).get("avg_invoice_amount"),
                "always_approved": bool((profile or {}).get("always_approved")),
                "invoice_count": (profile or {}).get("invoice_count", vendor_item_counts[vendor]),
            })
        except Exception as exc:
            logger.warning("[VendorBootstrap] Failed to update profile for %r: %s", vendor, exc)
            errors += 1

    result = {
        "vendors_processed": len(vendor_item_counts),
        "items_processed": items_processed,
        "items_skipped": items_skipped,
        "errors": errors,
        "dry_run": dry_run,
        "vendor_summaries": sorted(
            vendor_summaries, key=lambda v: v["items_in_history"], reverse=True
        )[:50],  # cap summary to 50 vendors
    }
    logger.info(
        "[VendorBootstrap] Complete: vendors=%d items=%d skipped=%d errors=%d",
        result["vendors_processed"], result["items_processed"],
        result["items_skipped"], result["errors"],
    )
    return result


def _first_date(*candidates: Optional[str]) -> Optional[str]:
    for c in candidates:
        if c:
            return str(c)[:10]
    return None
