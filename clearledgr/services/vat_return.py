"""VAT return computation (Wave 3 / E2).

Rolls bills into the periodic 9-box VAT return shape (HMRC + EU
domestic returns share a near-identical structure).

This is the AP-side projection. Box 1 (output VAT) and Box 6 (sales)
are populated from the AR/sales side (out of scope for Clearledgr).
We populate:

  Box 1 += vat on reverse_charge bills (RC self-assessed output is
                                        an "other output" per HMRC)
  Box 4 += vat on domestic + reverse_charge bills (input reclaim)
  Box 7 += net on every bill except out_of_scope
  Box 9 += net on reverse_charge bills (intra-EU acquisitions)

Box 3 / Box 5 are derived (Box1+Box2 = Box3; Box3-Box4 = Box5).

Status flow:
  draft       — computed but not submitted to HMRC / tax authority
  submitted   — operator marked as filed; submission_reference holds
                the receipt id
  superseded  — a later draft for the same period replaced this one
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VAT_RETURN_BOX_FIELDS = (
    "box1_vat_due_on_sales",
    "box2_vat_due_on_acquisitions",
    "box3_total_vat_due",
    "box4_vat_reclaimed",
    "box5_net_vat_payable",
    "box6_total_sales_ex_vat",
    "box7_total_purchases_ex_vat",
    "box8_total_eu_sales",
    "box9_total_eu_purchases",
)


def compute_vat_return_boxes(
    db,
    *,
    organization_id: str,
    period_start: str,
    period_end: str,
) -> Dict[str, Decimal]:
    """Walk ap_items in (created_at) the period and roll boxes 4/7/9.

    Box1 contributions from RC self-assessed VAT also come from AP.
    Boxes 2/6/8 stay at zero in this AP-only projection.

    Returns Decimals keyed by the canonical box-field names.
    """
    boxes: Dict[str, Decimal] = {f: Decimal("0.00") for f in _VAT_RETURN_BOX_FIELDS}

    sql = (
        "SELECT tax_treatment, vat_amount, net_amount, vat_code "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND state IN ('posted_to_erp', 'awaiting_payment', 'payment_in_flight', 'payment_executed', 'closed') "
        "  AND COALESCE(invoice_date, created_at) >= %s "
        "  AND COALESCE(invoice_date, created_at) <= %s"
    )
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, period_start, period_end))
        rows = cur.fetchall()

    for r in rows:
        row = dict(r)
        treatment = (row.get("tax_treatment") or "").strip().lower()
        vat = Decimal(str(row.get("vat_amount") or "0"))
        net = Decimal(str(row.get("net_amount") or "0"))

        if treatment == "domestic":
            boxes["box4_vat_reclaimed"] += vat
            boxes["box7_total_purchases_ex_vat"] += net
        elif treatment == "reverse_charge":
            # RC: self-assessed output (Box 1) AND input reclaim (Box 4).
            # Net cash impact = zero, but both sides hit the boxes.
            boxes["box1_vat_due_on_sales"] += vat
            boxes["box4_vat_reclaimed"] += vat
            boxes["box7_total_purchases_ex_vat"] += net
            boxes["box9_total_eu_purchases"] += net
        elif treatment in ("zero_rated", "exempt"):
            boxes["box7_total_purchases_ex_vat"] += net
        elif treatment == "out_of_scope":
            # No box contribution.
            pass
        else:
            # Untreated bill: most likely a legacy AP item from before
            # the v64 migration. Don't include in any box — operator
            # has to retro-classify before filing.
            pass

    boxes["box3_total_vat_due"] = (
        boxes["box1_vat_due_on_sales"]
        + boxes["box2_vat_due_on_acquisitions"]
    )
    boxes["box5_net_vat_payable"] = (
        boxes["box3_total_vat_due"] - boxes["box4_vat_reclaimed"]
    )
    return boxes


def compute_and_persist_vat_return(
    db,
    *,
    organization_id: str,
    period_start: str,
    period_end: str,
    jurisdiction: str = "GB",
    currency: str = "GBP",
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the boxes and persist a draft VAT return row.

    A subsequent recompute for the same (org, jurisdiction,
    period_start, period_end) supersedes the prior draft (the
    UNIQUE INDEX permits one non-superseded row per period).
    """
    boxes = compute_vat_return_boxes(
        db,
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
    )

    db.initialize()
    # Mark prior drafts for this period as superseded.
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE vat_returns SET status = 'superseded' "
            "WHERE organization_id = %s AND jurisdiction = %s "
            "  AND period_start = %s AND period_end = %s "
            "  AND status = 'draft'",
            (organization_id, jurisdiction, period_start, period_end),
        )
        conn.commit()

    return_id = f"VR-{uuid.uuid4().hex[:24]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    cols = [
        "id", "organization_id", "period_start", "period_end",
        "jurisdiction", *list(_VAT_RETURN_BOX_FIELDS),
        "currency", "status", "computed_at", "computed_by",
    ]
    vals: List[Any] = [
        return_id, organization_id, period_start, period_end,
        jurisdiction,
    ]
    vals.extend(boxes[f] for f in _VAT_RETURN_BOX_FIELDS)
    vals.extend([currency, "draft", now_iso, actor])
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO vat_returns ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, vals)
        conn.commit()
    return get_vat_return(db, return_id) or {
        "id": return_id,
        "organization_id": organization_id,
        "period_start": period_start,
        "period_end": period_end,
        "jurisdiction": jurisdiction,
        **{k: float(v) for k, v in boxes.items()},
        "status": "draft",
        "computed_at": now_iso,
    }


def get_vat_return(db, return_id: str) -> Optional[Dict[str, Any]]:
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM vat_returns WHERE id = %s",
            (return_id,),
        )
        row = cur.fetchone()
    return _decode(row)


def list_vat_returns(
    db,
    *,
    organization_id: str,
    jurisdiction: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    db.initialize()
    clauses = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    if jurisdiction:
        clauses.append("jurisdiction = %s")
        params.append(jurisdiction)
    if status:
        clauses.append("status = %s")
        params.append(status)
    safe_limit = max(1, min(int(limit or 50), 500))
    params.append(safe_limit)
    sql = (
        "SELECT * FROM vat_returns "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY period_end DESC, computed_at DESC LIMIT %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [d for d in (_decode(r) for r in rows) if d is not None]


def mark_vat_return_submitted(
    db,
    return_id: str,
    *,
    submission_reference: str,
    submitted_by: Optional[str] = None,
) -> Dict[str, Any]:
    db.initialize()
    submitted_at = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE vat_returns "
            "SET status = 'submitted', submitted_at = %s, "
            "    submission_reference = %s, computed_by = COALESCE(%s, computed_by) "
            "WHERE id = %s",
            (submitted_at, submission_reference, submitted_by, return_id),
        )
        conn.commit()
    return get_vat_return(db, return_id) or {}


def _decode(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    raw_meta = out.pop("metadata_json", None)
    if raw_meta:
        try:
            out["metadata"] = (
                json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            )
        except Exception:
            out["metadata"] = {}
    else:
        out["metadata"] = {}
    for box in _VAT_RETURN_BOX_FIELDS:
        if out.get(box) is not None and not isinstance(out[box], Decimal):
            try:
                out[box] = Decimal(str(out[box]))
            except Exception:
                out[box] = Decimal("0.00")
    return out
