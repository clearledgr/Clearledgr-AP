"""Google Sheets export — push reports to a Google Sheet.

Uses the existing SheetsAPIClient and report_export service to write
AP aging, vendor spend, and posting status reports to a user's
Google Sheet. Creates or updates a named tab per report type.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.http_client import get_http_client

logger = logging.getLogger(__name__)

# Sheet tab names per report type
REPORT_TAB_NAMES = {
    "ap_aging": "AP Aging",
    "vendor_spend": "Vendor Spend",
    "posting_status": "Posting Status",
    "invoice_volume": "Invoice Volume",
    "agent_action_log": "Agent Action Log",
    "match_accuracy": "Match Accuracy",
    "onboarding_duration": "Onboarding Duration",
}


async def export_report_to_sheets(
    user_id: str,
    spreadsheet_id: str,
    report_type: str,
    organization_id: str = "default",
    period_days: int = 30,
) -> Dict[str, Any]:
    """Generate a report and write it to a Google Sheet tab.

    Creates/overwrites a tab named after the report type.
    Returns {ok, sheet_name, rows_written, spreadsheet_id}.
    """
    from clearledgr.services.sheets_api import SheetsAPIClient
    from clearledgr.services.report_export import generate_report

    client = SheetsAPIClient(user_id)
    if not await client.ensure_authenticated():
        return {"ok": False, "error": "sheets_auth_failed"}

    rows, columns = generate_report(
        report_type=report_type,
        organization_id=organization_id,
        period_days=period_days,
    )

    sheet_name = REPORT_TAB_NAMES.get(report_type, report_type)

    # Ensure the tab exists
    try:
        await _ensure_tab(client, spreadsheet_id, sheet_name)
    except Exception as exc:
        logger.warning("[SheetsExport] Could not ensure tab %s: %s", sheet_name, exc)

    # Build values: header row + data rows
    header = columns
    data_rows = []
    for row in rows:
        data_rows.append([str(row.get(col, "")) for col in columns])

    # Add metadata row
    meta_row = [f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", f"Org: {organization_id}", f"Report: {report_type}"]
    all_values = [meta_row, header] + data_rows

    # Write to sheet (overwrite from A1)
    range_notation = f"'{sheet_name}'!A1"
    try:
        result = await client.write_sheet(spreadsheet_id, range_notation, all_values)
        return {
            "ok": True,
            "sheet_name": sheet_name,
            "rows_written": len(data_rows),
            "spreadsheet_id": spreadsheet_id,
            "updated_cells": result.get("updatedCells", 0),
        }
    except Exception as exc:
        logger.error("[SheetsExport] write failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _ensure_tab(client, spreadsheet_id: str, sheet_name: str) -> None:
    """Create a tab if it doesn't exist. Silently ignores if it already exists."""
    import httpx

    meta = await client.get_spreadsheet_metadata(spreadsheet_id)
    existing_sheets = [s.get("properties", {}).get("title", "") for s in meta.get("sheets", [])]
    if sheet_name in existing_sheets:
        return

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
    body = {
        "requests": [{
            "addSheet": {
                "properties": {"title": sheet_name}
            }
        }]
    }
    http_client = get_http_client()
    resp = await http_client.post(url, headers=client._headers(), json=body)
    if resp.status_code == 400 and "already exists" in resp.text.lower():
        return
    resp.raise_for_status()
