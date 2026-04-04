"""Report export service — generates CSV and JSON exports from existing data sources.

Supported report types:
- ap_aging:       Open payables bucketed by days past due
- vendor_spend:   Spend analysis by vendor, GL category, trends
- posting_status: AP items grouped by posting state with timing
- audit_trail:    Audit events (delegates to existing export)

All generators return (rows, columns) where rows is a list of flat dicts
suitable for CSV serialization.  Never raises — returns empty on error.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Supported report types
REPORT_TYPES = {"ap_aging", "vendor_spend", "posting_status"}


def generate_report(
    report_type: str,
    organization_id: str,
    period_days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Generate report rows and column headers.

    Returns (rows, columns) where rows is a list of flat dicts.
    Never raises — returns ([], []) on error.
    """
    try:
        if report_type == "ap_aging":
            return _generate_ap_aging(organization_id)
        elif report_type == "vendor_spend":
            return _generate_vendor_spend(organization_id, period_days)
        elif report_type == "posting_status":
            return _generate_posting_status(
                organization_id, start_date=start_date, end_date=end_date, vendor=vendor,
            )
        else:
            logger.warning("[ReportExport] Unknown report type: %s", report_type)
            return [], []
    except Exception as exc:
        logger.error("[ReportExport] %s failed for org %s: %s", report_type, organization_id, exc)
        return [], []


def rows_to_csv(rows: List[Dict[str, Any]], columns: List[str]) -> str:
    """Serialize rows to a CSV string."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in columns})
    return output.getvalue()


# ------------------------------------------------------------------
# AP Aging
# ------------------------------------------------------------------

_AP_AGING_COLUMNS = [
    "vendor_name", "currency", "total", "current", "1_30", "31_60", "61_90", "90_plus",
]


def _generate_ap_aging(organization_id: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """AP aging vendor breakdown — one row per vendor per currency."""
    from clearledgr.services.ap_aging_report import get_ap_aging_report

    report = get_ap_aging_report(organization_id)
    data = report.generate()

    rows = data.get("vendor_breakdown", [])
    return rows, _AP_AGING_COLUMNS


# ------------------------------------------------------------------
# Vendor Spend
# ------------------------------------------------------------------

_VENDOR_SPEND_COLUMNS = [
    "vendor_name", "total_spend", "invoice_count",
]

_SPEND_GL_COLUMNS = [
    "gl_code", "total_spend",
]

_SPEND_TREND_COLUMNS = [
    "month", "total_spend", "invoice_count", "mom_change_pct",
]


def _generate_vendor_spend(
    organization_id: str, period_days: int = 30,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Vendor spend — top vendors, GL breakdown, and monthly trends in one export.

    Each row has a 'section' column to distinguish vendor/gl/trend rows.
    """
    from clearledgr.services.spend_analysis import get_spend_analysis_service

    service = get_spend_analysis_service(organization_id)
    data = service.analyze(period_days)

    columns = ["section", "vendor_name", "gl_code", "month", "total_spend", "invoice_count", "mom_change_pct"]
    rows: List[Dict[str, Any]] = []

    for v in data.get("top_vendors", []):
        rows.append({
            "section": "vendor",
            "vendor_name": v.get("vendor_name", ""),
            "total_spend": v.get("total_spend", 0),
            "invoice_count": v.get("invoice_count", 0),
        })

    for gl in data.get("spend_by_gl_category", []):
        rows.append({
            "section": "gl_category",
            "gl_code": gl.get("gl_code", ""),
            "total_spend": gl.get("total_spend", 0),
        })

    for t in data.get("monthly_trends", []):
        rows.append({
            "section": "monthly_trend",
            "month": t.get("month", ""),
            "total_spend": t.get("total_spend", 0),
            "invoice_count": t.get("invoice_count", 0),
            "mom_change_pct": t.get("mom_change_pct"),
        })

    return rows, columns


# ------------------------------------------------------------------
# Posting Status
# ------------------------------------------------------------------

_POSTING_STATUS_COLUMNS = [
    "id", "invoice_number", "vendor_name", "amount", "currency", "state",
    "created_at", "erp_posted_at", "days_to_post", "erp_reference",
]


def _generate_posting_status(
    organization_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """AP items with posting timing — useful for month-end reconciliation."""
    from clearledgr.core.database import get_db

    db = get_db()
    db.initialize()

    # Build query with optional filters
    conditions = ["organization_id = ?"]
    params: list = [organization_id]

    if start_date:
        conditions.append("created_at >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= ?")
        params.append(end_date)
    if vendor:
        conditions.append("vendor_name LIKE ?")
        params.append(f"%{vendor}%")

    where = " AND ".join(conditions)
    sql = db._prepare_sql(
        f"SELECT id, invoice_number, vendor_name, amount, currency, state, "
        f"created_at, erp_posted_at, erp_reference "
        f"FROM ap_items WHERE {where} "
        f"ORDER BY created_at DESC LIMIT 10000"
    )

    try:
        with db.connect() as conn:
            if db.use_postgres:
                cur = conn.cursor()
                cur.execute(sql, params)
                items = [dict(r) for r in cur.fetchall()]
            else:
                conn.row_factory = __import__("sqlite3").Row
                cur = conn.cursor()
                cur.execute(sql, params)
                items = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("[ReportExport] posting_status query failed: %s", exc)
        return [], _POSTING_STATUS_COLUMNS

    rows: List[Dict[str, Any]] = []
    for item in items:
        created = item.get("created_at") or ""
        posted = item.get("erp_posted_at") or ""
        days_to_post = ""
        if created and posted:
            try:
                c = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                p = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
                days_to_post = round((p - c).total_seconds() / 86400.0, 1)
            except (ValueError, TypeError):
                pass

        rows.append({
            "id": item.get("id", ""),
            "invoice_number": item.get("invoice_number", ""),
            "vendor_name": item.get("vendor_name", ""),
            "amount": item.get("amount", ""),
            "currency": item.get("currency", "USD"),
            "state": item.get("state", ""),
            "created_at": created,
            "erp_posted_at": posted,
            "days_to_post": days_to_post,
            "erp_reference": item.get("erp_reference", ""),
        })

    return rows, _POSTING_STATUS_COLUMNS
