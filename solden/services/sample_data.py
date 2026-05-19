"""Sample data loader — Module 10 (onboarding sample data mode).

Synthesises a curated set of AP items so a new customer can practice
the workflow end-to-end before going live with real data. Sample
rows are tagged ``is_sample = true``; production reads filter them
out so they never contaminate live dashboards or reports.

What the sample set covers:

  - A clean low-amount auto-approval candidate (under the Module 3
    "Auto-approve <$1K USD" template) so the leader sees the
    fast-path.
  - A mid-amount needs-approval invoice that should route to the AP
    Manager.
  - A high-amount invoice that should require dual approval.
  - A vendor-not-in-ERP-master gate trigger so the operator sees
    needs_info routing.
  - A field-conflict / extraction-review case for the field-review
    flow.
  - A failed-post case to exercise the retry surface.
  - A multi-currency case (EUR) so the FX panel shows up in
    Reports → Volume.
  - A few cleanly-posted historical rows to show the timeline view
    populated.

Loader is idempotent — calling it twice doesn't double-load. Clearer
deletes only sample rows for the calling org, never production.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# Sample-vendor naming convention: every name carries the "SAMPLE"
# prefix so even if a sample row leaks into a search, the operator
# recognises it on sight as practice data, not a real vendor they
# need to chase.
_SAMPLE_PREFIX = "SAMPLE — "


@dataclass
class SampleSpec:
    suffix: str
    vendor_name: str
    amount: float
    currency: str
    days_ago: int
    state: str
    exception_code: str = ""
    note: str = ""


def _spec_set() -> List[SampleSpec]:
    """The curated 10-row sample set. Deterministic so re-loads always
    produce the same shape (idempotency check below dedupes by
    invoice_number)."""
    return [
        SampleSpec(
            suffix="approve-fast",
            vendor_name=f"{_SAMPLE_PREFIX}Acme Coffee Supplies",
            amount=240.00,
            currency="USD",
            days_ago=2,
            state="closed",
            note="Auto-approved under the <$1K rule.",
        ),
        SampleSpec(
            suffix="manager-review",
            vendor_name=f"{_SAMPLE_PREFIX}Cisco Systems",
            amount=4_500.00,
            currency="USD",
            days_ago=5,
            state="needs_approval",
            note="Mid-amount; routes to AP Manager.",
        ),
        SampleSpec(
            suffix="dual-approval",
            vendor_name=f"{_SAMPLE_PREFIX}Booking Holdings BV",
            amount=78_000.00,
            currency="USD",
            days_ago=4,
            state="needs_second_approval",
            note="Above the dual-approval threshold.",
        ),
        SampleSpec(
            suffix="vendor-master-miss",
            vendor_name=f"{_SAMPLE_PREFIX}Mystery Office Supplies LLC",
            amount=820.00,
            currency="USD",
            days_ago=3,
            state="needs_info",
            exception_code="vendor_not_in_erp_master",
            note="Vendor not yet in ERP master — needs info.",
        ),
        SampleSpec(
            suffix="field-conflict",
            vendor_name=f"{_SAMPLE_PREFIX}AWS Cloud Services",
            amount=1_240.50,
            currency="USD",
            days_ago=6,
            state="validated",
            exception_code="field_conflict",
            note="Extraction confidence below the field-review floor.",
        ),
        SampleSpec(
            suffix="po-required",
            vendor_name=f"{_SAMPLE_PREFIX}Cisco Systems",
            amount=12_400.00,
            currency="USD",
            days_ago=8,
            state="needs_info",
            exception_code="po_required_missing",
            note="PO required for this vendor; not on invoice.",
        ),
        SampleSpec(
            suffix="failed-post",
            vendor_name=f"{_SAMPLE_PREFIX}Verizon Communications",
            amount=890.00,
            currency="USD",
            days_ago=1,
            state="failed_post",
            exception_code="erp_post_failed",
            note="ERP rejected the post — recoverable retry.",
        ),
        SampleSpec(
            suffix="eur-cross-currency",
            vendor_name=f"{_SAMPLE_PREFIX}Café Paris",
            amount=320.00,
            currency="EUR",
            days_ago=10,
            state="closed",
            note="EUR invoice — exercises Module 9 FX conversion.",
        ),
        SampleSpec(
            suffix="historic-clean-1",
            vendor_name=f"{_SAMPLE_PREFIX}Slack Technologies",
            amount=180.00,
            currency="USD",
            days_ago=15,
            state="closed",
        ),
        SampleSpec(
            suffix="historic-clean-2",
            vendor_name=f"{_SAMPLE_PREFIX}GitHub Enterprise",
            amount=2_100.00,
            currency="USD",
            days_ago=22,
            state="closed",
        ),
    ]


def load_sample_data(db: Any, organization_id: str) -> Dict[str, Any]:
    """Insert the curated sample set for an org. Idempotent: re-running
    against an org that already has samples returns the existing
    count without creating duplicates.

    Returns ``{loaded: N, already_present: M}`` so the API layer
    can surface either "10 samples loaded" or "you already have 10
    samples in the org" without ambiguity.
    """
    existing = count_sample_data(db, organization_id)
    if existing > 0:
        return {"loaded": 0, "already_present": existing, "total": existing}

    specs = _spec_set()
    loaded = 0
    now = datetime.now(timezone.utc)
    for spec in specs:
        item_id = f"sample-{organization_id}-{spec.suffix}-{uuid.uuid4().hex[:8]}"
        invoice_number = f"SAMPLE-{spec.suffix.upper()}"
        try:
            payload = {
                "id": item_id,
                "organization_id": organization_id,
                "vendor_name": spec.vendor_name,
                "amount": spec.amount,
                "currency": spec.currency,
                "invoice_number": invoice_number,
                "state": spec.state,
                "exception_code": spec.exception_code,
                "is_sample": True,
                "metadata": {"sample_note": spec.note},
            }
            db.create_ap_item(payload)
            # Back-date created_at + (where applicable) erp_posted_at so
            # the sample rows show up across time-series reports rather
            # than all clustered at "now". Same UPDATE flips is_sample
            # to true — the existing create_ap_item INSERT doesn't
            # include is_sample in its column list (it predates this
            # migration), so we set it here in the same trip.
            backdated = (now - timedelta(days=spec.days_ago)).isoformat()
            with db.connect() as conn:
                cur = conn.cursor()
                if spec.state in ("closed", "posted_to_erp"):
                    cur.execute(
                        "UPDATE ap_items SET created_at = %s, "
                        "erp_posted_at = %s, is_sample = TRUE "
                        "WHERE id = %s",
                        (backdated, backdated, item_id),
                    )
                else:
                    cur.execute(
                        "UPDATE ap_items SET created_at = %s, "
                        "is_sample = TRUE WHERE id = %s",
                        (backdated, item_id),
                    )
                conn.commit()
            loaded += 1
        except Exception as exc:
            logger.warning(
                "[sample_data] failed to load %s: %s", spec.suffix, exc,
            )

    return {"loaded": loaded, "already_present": 0, "total": loaded}


def clear_sample_data(db: Any, organization_id: str) -> Dict[str, Any]:
    """Delete every sample row for an org. Production rows
    (``is_sample = false``) are untouched — this is the contract
    that makes "sample data does not contaminate production"
    enforceable at SQL level."""
    deleted = 0
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM ap_items "
                "WHERE organization_id = %s AND is_sample = TRUE",
                (organization_id,),
            )
            deleted = cur.rowcount or 0
            conn.commit()
    except Exception as exc:
        logger.warning(
            "[sample_data] clear failed for org=%s: %s", organization_id, exc,
        )
    return {"deleted": int(deleted)}


def count_sample_data(db: Any, organization_id: str) -> int:
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*)::bigint FROM ap_items "
                "WHERE organization_id = %s AND is_sample = TRUE",
                (organization_id,),
            )
            row = cur.fetchone()
            return int((row[0] if row else 0) or 0)
    except Exception as exc:
        logger.debug(
            "[sample_data] count failed for org=%s: %s", organization_id, exc,
        )
        return 0


def list_sample_items(
    db: Any, organization_id: str, limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return the sample AP items for an org so the dashboard can
    render the practice-data preview without exposing them to
    production reads."""
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, vendor_name, amount, currency, invoice_number, "
                "       state, exception_code, created_at "
                "FROM ap_items "
                "WHERE organization_id = %s AND is_sample = TRUE "
                "ORDER BY created_at DESC "
                "LIMIT %s",
                (organization_id, int(limit)),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug(
            "[sample_data] list failed for org=%s: %s", organization_id, exc,
        )
        return []
