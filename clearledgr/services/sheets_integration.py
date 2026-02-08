"""
Google Sheets Integration for Clearledgr Reconciliation v1

Provides functions to read from and write to Google Sheets for the Reconciliation reconciliation system.
"""
import os
import csv
import io
import json
import gspread
from typing import Dict, List, Optional
from google.oauth2.service_account import Credentials

# Google Sheets API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _sheets_client():
    """Get authenticated Google Sheets client."""
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON env var")
    info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _read_tab(sheet_id: str, tab_name: str) -> List[List[str]]:
    """Read a Google Sheets tab and return all values."""
    gc = _sheets_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab_name)
    values = ws.get_all_values()
    return values  # includes header row


def _ensure_tab(sheet_id: str, tab_name: str, rows: int = 1000, cols: int = 20):
    """Ensure a tab exists, create it if it doesn't."""
    gc = _sheets_client()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab_name)
        return ws
    except Exception:
        return sh.add_worksheet(title=tab_name, rows=str(rows), cols=str(cols))


def read_config_from_sheets(sheet_id: str) -> Dict:
    """
    Read CL_CONFIG tab from Google Sheets and convert to our config format.
    
    Args:
        sheet_id: Google Sheets ID
    
    Returns:
        Config dict with mappings, tolerance, date_window
    """
    try:
        values = _read_tab(sheet_id, "CL_CONFIG")
    except Exception as e:
        raise ValueError(f"Failed to read CL_CONFIG tab: {str(e)}")
    
    # Parse key/value pairs from CL_CONFIG
    # Expected format: 2 columns, first row is header, subsequent rows are key/value
    config_dict: Dict[str, str] = {}
    
    if len(values) < 2:
        raise ValueError("CL_CONFIG tab must have at least a header row and one config row")
    
    # Skip header row, process key/value pairs
    for row in values[1:]:
        if len(row) < 2:
            continue
        key = str(row[0]).strip()
        value = str(row[1]).strip()
        if key:
            config_dict[key] = value
    
    # Build mappings from config
    # Default column names if not specified
    gateway_date_col = config_dict.get("gateway_date_col", "Date")
    gateway_amount_col = config_dict.get("gateway_amount_col", "Net Amount")
    gateway_id_col = config_dict.get("gateway_id_col", "Transaction ID")
    gateway_status_col = config_dict.get("gateway_status_col", "Status")
    
    bank_date_col = config_dict.get("bank_date_col", "Booking Date")
    bank_amount_col = config_dict.get("bank_amount_col", "Amount")
    bank_id_col = config_dict.get("bank_id_col", "Bank Transaction ID")
    
    internal_date_col = config_dict.get("internal_date_col", "Date")
    internal_amount_col = config_dict.get("internal_amount_col", "Amount")
    internal_id_col = config_dict.get("internal_id_col", "Internal ID")
    
    # Build our config format
    config = {
        "mappings": {
            "payment_gateway": {
                gateway_id_col: "txn_id",
                gateway_date_col: "date",
                gateway_amount_col: "net_amount",
                gateway_status_col: "status"
            },
            "bank": {
                bank_id_col: "bank_txn_id",
                bank_date_col: "date",
                bank_amount_col: "amount"
            },
            "internal": {
                internal_id_col: "internal_id",
                internal_date_col: "date",
                internal_amount_col: "amount"
            }
        },
        "amount_tolerance_pct": float(config_dict.get("amount_tolerance_pct", "0.5")),
        "date_window_days": int(config_dict.get("date_window_days", "3"))
        # Note: Slack/Teams notifications handled via installed apps
    }
    
    return config


def read_tab_as_csv_bytes(sheet_id: str, tab_name: str) -> bytes:
    """
    Read a Google Sheets tab and return as CSV bytes.
    
    Args:
        sheet_id: Google Sheets ID
        tab_name: Name of the tab to read
    
    Returns:
        CSV file as bytes
    """
    try:
        values = _read_tab(sheet_id, tab_name)
    except Exception as e:
        raise ValueError(f"Failed to read {tab_name} tab: {str(e)}")
    
    if not values:
        # Return empty CSV with just headers if tab is empty
        return b""
    
    # Convert to CSV bytes
    output = io.StringIO()
    writer = csv.writer(output)
    
    for row in values:
        writer.writerow(row)
    
    return output.getvalue().encode('utf-8')


def write_outputs_to_sheets(
    sheet_id: str,
    period_start: str,
    period_end: str,
    outputs: Dict[str, List[Dict]]
) -> str:
    """
    Write Reconciliation reconciliation outputs to Google Sheets tabs.
    
    Args:
        sheet_id: Google Sheets ID
        period_start: Period start date (YYYY-MM-DD)
        period_end: Period end date (YYYY-MM-DD)
        outputs: Output from build_reconciliation_outputs with summary, reconciled, exceptions
    
    Returns:
        URL of the Google Sheet
    """
    # Write CL_SUMMARY tab
    _write_summary_tab(sheet_id, period_start, period_end, outputs.get("summary", []))
    
    # Write CL_RECONCILED tab
    _write_reconciled_tab(sheet_id, period_start, period_end, outputs.get("reconciled", []))
    
    # Write CL_EXCEPTIONS tab
    _write_exceptions_tab(sheet_id, period_start, period_end, outputs.get("exceptions", []))
    
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def _write_summary_tab(sheet_id: str, period_start: str, period_end: str, summary_rows: List[Dict]):
    """Write summary rows to CL_SUMMARY tab."""
    tab_name = "CL_SUMMARY"
    # Updated headers to match new schema
    headers = [
        "period_start", "period_end", "total_gateway_volume", "total_bank_volume",
        "total_internal_volume", "matched_volume", "matched_pct", "exception_count", "run_timestamp"
    ]
    
    ws = _ensure_tab(sheet_id, tab_name, rows=1000, cols=len(headers))
    
    # Clear existing data and write headers
    ws.clear()
    ws.append_row(headers)
    
    # Write summary rows
    for summary in summary_rows:
        row = [
            summary.get("period_start", period_start),
            summary.get("period_end", period_end),
            summary.get("total_gateway_volume", 0),
            summary.get("total_bank_volume", 0),
            summary.get("total_internal_volume", 0),
            summary.get("matched_volume", 0),
            summary.get("matched_pct", 0),
            summary.get("exception_count", 0),
            summary.get("run_timestamp", "")
        ]
        ws.append_row(row)


def _write_reconciled_tab(sheet_id: str, period_start: str, period_end: str, reconciled_rows: List[Dict]):
    """Write reconciled rows to CL_RECONCILED tab."""
    tab_name = "CL_RECONCILED"
    # Updated headers to match new schema
    headers = [
        "group_id", "gateway_tx_ids", "bank_tx_ids", "internal_tx_ids",
        "amount_gateway", "amount_bank", "amount_internal",
        "date_gateway", "date_bank", "date_internal", "status"
    ]
    
    ws = _ensure_tab(sheet_id, tab_name, rows=5000, cols=len(headers))
    
    # Clear existing data and write headers
    ws.clear()
    ws.append_row(headers)
    
    # Write reconciled rows
    rows_to_write = []
    for reconciled in reconciled_rows:
        # Convert arrays to comma-separated strings
        gateway_tx_ids = ", ".join(reconciled.get("gateway_tx_ids", []))
        bank_tx_ids = ", ".join(reconciled.get("bank_tx_ids", []))
        internal_tx_ids = ", ".join(reconciled.get("internal_tx_ids", []))
        
        row = [
            reconciled.get("group_id", ""),
            gateway_tx_ids,
            bank_tx_ids,
            internal_tx_ids,
            reconciled.get("amount_gateway", 0),
            reconciled.get("amount_bank", 0),
            reconciled.get("amount_internal", 0),
            reconciled.get("date_gateway", ""),
            reconciled.get("date_bank", ""),
            reconciled.get("date_internal", ""),
            reconciled.get("status", "")
        ]
        rows_to_write.append(row)
    
    if rows_to_write:
        ws.append_rows(rows_to_write)


def _write_exceptions_tab(sheet_id: str, period_start: str, period_end: str, exception_rows: List[Dict]):
    """Write exception rows to CL_EXCEPTIONS tab."""
    tab_name = "CL_EXCEPTIONS"
    # Updated headers to match new schema
    headers = [
        "source", "tx_ids", "amounts", "dates", "description",
        "reason", "llm_explanation", "suggested_action"
    ]
    
    ws = _ensure_tab(sheet_id, tab_name, rows=5000, cols=len(headers))
    
    # Clear existing data and write headers
    ws.clear()
    ws.append_row(headers)
    
    # Write exception rows
    rows_to_write = []
    for exception in exception_rows:
        # Convert tx_ids array to comma-separated string
        tx_ids = ", ".join(exception.get("tx_ids", []))
        
        row = [
            exception.get("source", ""),
            tx_ids,
            exception.get("amounts", 0),
            exception.get("dates", ""),
            exception.get("description", ""),
            exception.get("reason", ""),
            exception.get("llm_explanation", ""),
            exception.get("suggested_action", "")
        ]
        rows_to_write.append(row)
    
    if rows_to_write:
        ws.append_rows(rows_to_write)


class SheetsService:
    """
    High-level Google Sheets service for Clearledgr.
    
    Provides simple read/write operations for workflow activities.
    """
    
    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self._client = None
        self._spreadsheet = None
    
    def _get_client(self):
        """Get authenticated gspread client."""
        if self._client is None:
            self._client = _sheets_client()
        return self._client
    
    def _get_spreadsheet(self):
        """Get spreadsheet object."""
        if self._spreadsheet is None:
            self._spreadsheet = self._get_client().open_by_key(self.spreadsheet_id)
        return self._spreadsheet
    
    def write_range(
        self,
        range_name: str,
        values: List[List],
        clear_first: bool = False,
    ) -> Dict:
        """
        Write values to a range in the spreadsheet.
        
        Args:
            range_name: A1 notation range (e.g., "Sheet1!A1:D10" or "Sheet1!A1")
            values: 2D list of values to write
            clear_first: If True, clear the sheet before writing
            
        Returns:
            Dict with update results
        """
        # Parse sheet name from range
        if "!" in range_name:
            sheet_name = range_name.split("!")[0]
        else:
            sheet_name = range_name
            range_name = f"{sheet_name}!A1"
        
        spreadsheet = self._get_spreadsheet()
        
        # Get or create worksheet
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=max(len(values) + 100, 1000),
                cols=max(len(values[0]) if values else 10, 20),
            )
        
        # Clear if requested
        if clear_first:
            worksheet.clear()
        
        # Write values
        if values:
            worksheet.update(range_name.split("!")[-1] if "!" in range_name else "A1", values)
        
        return {
            "spreadsheetId": self.spreadsheet_id,
            "updatedRange": range_name,
            "updatedRows": len(values),
            "updatedCells": sum(len(row) for row in values),
        }
    
    def read_range(self, range_name: str) -> List[List]:
        """
        Read values from a range.
        
        Args:
            range_name: A1 notation range
            
        Returns:
            2D list of values
        """
        if "!" in range_name:
            sheet_name = range_name.split("!")[0]
        else:
            sheet_name = range_name
        
        spreadsheet = self._get_spreadsheet()
        worksheet = spreadsheet.worksheet(sheet_name)
        
        return worksheet.get_all_values()
    
    def append_rows(self, sheet_name: str, rows: List[List]) -> Dict:
        """
        Append rows to a sheet.
        
        Args:
            sheet_name: Name of the worksheet
            rows: List of rows to append
            
        Returns:
            Dict with update results
        """
        spreadsheet = self._get_spreadsheet()
        
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=20,
            )
        
        if rows:
            worksheet.append_rows(rows)
        
        return {
            "spreadsheetId": self.spreadsheet_id,
            "appendedRows": len(rows),
        }

