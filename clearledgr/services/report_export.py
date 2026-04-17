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
REPORT_TYPES = {
    "ap_aging", "vendor_spend", "posting_status",
    "invoice_volume", "agent_action_log", "match_accuracy", "onboarding_duration",
}


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
        elif report_type == "invoice_volume":
            return _generate_invoice_volume(organization_id, period_days)
        elif report_type == "agent_action_log":
            return _generate_agent_action_log(organization_id, period_days)
        elif report_type == "match_accuracy":
            return _generate_match_accuracy(organization_id, period_days)
        elif report_type == "onboarding_duration":
            return _generate_onboarding_duration(organization_id)
        else:
            logger.warning("[ReportExport] Unknown report type: %s", report_type)
            return [], []
    except Exception as exc:
        logger.error("[ReportExport] %s failed for org %s: %s", report_type, organization_id, exc)
        return [], []


def rows_to_csv(rows: List[Dict[str, Any]], columns: List[str]) -> str:
    """Serialize rows to a CSV string.

    Prepends a UTF-8 BOM (``\\ufeff``) so Excel on Windows opens the
    file using UTF-8 decoding. Without the BOM, Excel falls back to
    the OS code page (CP-1252 on en-US Windows) and mangles every
    non-ASCII character — "Café Paris" becomes "CafÃ© Paris",
    "Société Générale" becomes unreadable. Finance teams export
    these CSVs regularly and "why are the vendor names broken?" is
    not a support ticket we want. UTF-8-aware tools (macOS Numbers,
    Google Sheets, modern LibreOffice, Excel on Mac) ignore the BOM
    and see clean UTF-8, so the fix is free for them.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in columns})
    return "\ufeff" + output.getvalue()


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


# ── §6.8 Google Sheets Export: thesis-required report types ──

_INVOICE_VOLUME_COLUMNS = ["period", "total_invoices", "auto_processed", "manual_reviewed", "exceptions", "touchless_rate_pct"]

def _generate_invoice_volume(organization_id: str, period_days: int = 30) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Invoice volume report — count and breakdown by processing path."""
    db = get_db()
    items = db.list_ap_items(organization_id=organization_id, limit=5000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)

    recent = [i for i in items if _parse_date(i.get("created_at")) and _parse_date(i.get("created_at")) >= cutoff]
    auto = [i for i in recent if i.get("state") in ("posted_to_erp", "closed", "approved") and not i.get("approved_by")]
    manual = [i for i in recent if i.get("approved_by")]
    exceptions = [i for i in recent if i.get("state") in ("needs_info", "failed_post")]
    touchless = (len(auto) / len(recent) * 100) if recent else 0

    rows = [{
        "period": f"Last {period_days} days",
        "total_invoices": len(recent),
        "auto_processed": len(auto),
        "manual_reviewed": len(manual),
        "exceptions": len(exceptions),
        "touchless_rate_pct": f"{touchless:.1f}",
    }]
    return rows, _INVOICE_VOLUME_COLUMNS


_ACTION_LOG_COLUMNS = ["timestamp", "event_type", "actor", "vendor", "invoice", "summary"]

def _generate_agent_action_log(organization_id: str, period_days: int = 30) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Agent action log — every autonomous action with timestamp."""
    db = get_db()
    events = []
    try:
        if hasattr(db, "list_recent_audit_events"):
            events = db.list_recent_audit_events(organization_id, limit=500)
        elif hasattr(db, "list_ap_audit_events"):
            events = db.list_ap_audit_events(organization_id, limit=500)
    except Exception:
        pass

    rows = []
    for e in events:
        rows.append({
            "timestamp": (e.get("ts") or e.get("timestamp") or e.get("created_at") or "")[:19],
            "event_type": e.get("event_type") or "",
            "actor": e.get("actor_id") or e.get("actor") or "",
            "vendor": e.get("vendor_name") or "",
            "invoice": e.get("ap_item_id") or "",
            "summary": (e.get("summary") or e.get("reason") or "")[:200],
        })
    return rows, _ACTION_LOG_COLUMNS


_MATCH_ACCURACY_COLUMNS = ["period", "total_matched", "auto_correct", "overridden", "accuracy_pct", "override_rate_pct"]

def _generate_match_accuracy(organization_id: str, period_days: int = 30) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Match accuracy report — how often the agent's match was correct."""
    db = get_db()
    items = db.list_ap_items(organization_id=organization_id, limit=5000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)

    recent = [i for i in items if _parse_date(i.get("created_at")) and _parse_date(i.get("created_at")) >= cutoff]
    matched = [i for i in recent if i.get("match_status")]
    overridden = [i for i in matched if i.get("approved_by") and i.get("match_status") != "passed"]
    auto_correct = len(matched) - len(overridden)
    accuracy = (auto_correct / len(matched) * 100) if matched else 100
    override_rate = (len(overridden) / len(matched) * 100) if matched else 0

    rows = [{
        "period": f"Last {period_days} days",
        "total_matched": len(matched),
        "auto_correct": auto_correct,
        "overridden": len(overridden),
        "accuracy_pct": f"{accuracy:.1f}",
        "override_rate_pct": f"{override_rate:.1f}",
    }]
    return rows, _MATCH_ACCURACY_COLUMNS


_ONBOARDING_DURATION_COLUMNS = [
    "vendor", "state", "invited_at", "activated_at",
    "days_elapsed", "business_days_to_active", "within_5bd_sla",
    "chase_count", "stage",
]

def _generate_onboarding_duration(organization_id: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Vendor onboarding duration report — DESIGN_THESIS §11 success metric #4.

    Includes both in-flight (pending) and completed activations so a
    CFO or Controller scanning the report can answer both "who's
    stuck" and "how fast are successful onboardings actually landing".
    Completed rows carry ``business_days_to_active`` (Mon–Fri only,
    weekend-aware) so the ≤5-business-day SLA the thesis names is
    directly readable from the column.
    """
    from clearledgr.core.business_days import business_days_from_iso

    db = get_db()
    pending: List[Dict[str, Any]] = []
    completed: List[Dict[str, Any]] = []
    try:
        if hasattr(db, "list_pending_onboarding_sessions"):
            pending = db.list_pending_onboarding_sessions(organization_id) or []
    except Exception:
        pass
    try:
        if hasattr(db, "list_completed_onboarding_sessions"):
            completed = db.list_completed_onboarding_sessions(organization_id) or []
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []

    # Completed activations first — they carry the SLA-relevant
    # business_days_to_active field. Ordered newest-first already by
    # the store; preserve that so the top of the report shows the
    # most recent activations.
    for s in completed:
        invited = s.get("invited_at") or ""
        activated = s.get("erp_activated_at") or ""
        calendar_days = 0
        bd_to_active = 0
        if invited and activated:
            try:
                start = datetime.fromisoformat(invited.replace("Z", "+00:00"))
                end = datetime.fromisoformat(activated.replace("Z", "+00:00"))
                calendar_days = max(0, (end - start).days)
            except (ValueError, TypeError):
                pass
            bd_to_active = business_days_from_iso(invited, activated)
        rows.append({
            "vendor": s.get("vendor_name") or "",
            "state": "active",
            "invited_at": invited[:10] if invited else "",
            "activated_at": activated[:10] if activated else "",
            "days_elapsed": calendar_days,
            "business_days_to_active": bd_to_active,
            "within_5bd_sla": "yes" if (bd_to_active and bd_to_active <= 5) else "no",
            "chase_count": s.get("chase_count") or 0,
            "stage": "Active",
        })

    # In-flight sessions below. business_days_to_active isn't
    # meaningful yet (no activation date) — leave blank so the column
    # aggregator doesn't mistake an empty cell for a zero-day SLA hit.
    for s in pending:
        invited = s.get("invited_at") or ""
        calendar_days = 0
        if invited:
            try:
                dt = datetime.fromisoformat(invited.replace("Z", "+00:00"))
                calendar_days = max(0, (now - dt).days)
            except (ValueError, TypeError):
                pass
        rows.append({
            "vendor": s.get("vendor_name") or "",
            "state": s.get("state") or "",
            "invited_at": invited[:10] if invited else "",
            "activated_at": "",
            "days_elapsed": calendar_days,
            "business_days_to_active": "",
            "within_5bd_sla": "",
            "chase_count": s.get("chase_count") or 0,
            "stage": (s.get("state") or "").replace("_", " "),
        })
    return rows, _ONBOARDING_DURATION_COLUMNS


def _parse_date(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
