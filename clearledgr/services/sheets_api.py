"""
Google Sheets API Client for Clearledgr

Provides server-side access to Google Sheets for:
- Reading bank/gateway transactions
- Writing reconciliation results
- Updating exception lists
- Dashboard data sync

Uses OAuth 2.0 for authorization via the same token store as Gmail.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import httpx

# Configuration
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3/files"

# Scopes for Sheets access (added to Gmail scopes)
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class SheetRange:
    """Represents a range of cells."""
    sheet_name: str
    start_row: int
    start_col: str
    end_row: Optional[int] = None
    end_col: Optional[str] = None
    
    def to_a1(self) -> str:
        """Convert to A1 notation."""
        if self.end_row and self.end_col:
            return f"'{self.sheet_name}'!{self.start_col}{self.start_row}:{self.end_col}{self.end_row}"
        return f"'{self.sheet_name}'!{self.start_col}{self.start_row}"


class SheetsAPIClient:
    """
    Google Sheets API client for server-side spreadsheet operations.
    
    Usage:
        client = SheetsAPIClient(user_id="user123")
        await client.ensure_authenticated()
        data = await client.read_range(spreadsheet_id, "Sheet1!A1:D100")
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._access_token: Optional[str] = None
    
    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token."""
        from clearledgr.services.gmail_api import token_store, GmailAPIClient
        
        token = token_store.get(self.user_id)
        if not token:
            return False
        
        if token.is_expired():
            # Refresh token using Gmail client
            gmail_client = GmailAPIClient(self.user_id)
            if not await gmail_client._refresh_token():
                return False
            token = token_store.get(self.user_id)
        
        self._access_token = token.access_token
        return True
    
    async def _request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated API request."""
        if not self._access_token:
            await self.ensure_authenticated()
        
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        
        async with httpx.AsyncClient() as client:
            if method == "GET":
                response = await client.get(url, headers=headers, params=params, timeout=30)
            elif method == "POST":
                response = await client.post(url, headers=headers, json=json_data, timeout=30)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=json_data, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            if response.status_code == 401:
                # Token expired, try refresh
                await self.ensure_authenticated()
                headers["Authorization"] = f"Bearer {self._access_token}"
                
                if method == "GET":
                    response = await client.get(url, headers=headers, params=params, timeout=30)
                elif method == "POST":
                    response = await client.post(url, headers=headers, json=json_data, timeout=30)
                elif method == "PUT":
                    response = await client.put(url, headers=headers, json=json_data, timeout=30)
            
            response.raise_for_status()
            return response.json() if response.content else {}
    
    # ==================== READ OPERATIONS ====================
    
    async def get_spreadsheet(self, spreadsheet_id: str) -> Dict[str, Any]:
        """Get spreadsheet metadata."""
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}"
        return await self._request("GET", url)
    
    async def read_range(
        self,
        spreadsheet_id: str,
        range_notation: str,
        value_render_option: str = "FORMATTED_VALUE"
    ) -> List[List[Any]]:
        """
        Read values from a range.
        
        Args:
            spreadsheet_id: The spreadsheet ID
            range_notation: A1 notation (e.g., "Sheet1!A1:D100")
            value_render_option: FORMATTED_VALUE, UNFORMATTED_VALUE, or FORMULA
        
        Returns:
            2D list of cell values
        """
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}"
        params = {"valueRenderOption": value_render_option}
        
        result = await self._request("GET", url, params=params)
        return result.get("values", [])
    
    async def read_named_range(
        self,
        spreadsheet_id: str,
        named_range: str
    ) -> List[List[Any]]:
        """Read values from a named range."""
        return await self.read_range(spreadsheet_id, named_range)
    
    async def get_sheet_names(self, spreadsheet_id: str) -> List[str]:
        """Get all sheet names in a spreadsheet."""
        metadata = await self.get_spreadsheet(spreadsheet_id)
        sheets = metadata.get("sheets", [])
        return [s.get("properties", {}).get("title", "") for s in sheets]
    
    # ==================== WRITE OPERATIONS ====================
    
    async def write_range(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: List[List[Any]],
        value_input_option: str = "USER_ENTERED"
    ) -> Dict[str, Any]:
        """
        Write values to a range.
        
        Args:
            spreadsheet_id: The spreadsheet ID
            range_notation: A1 notation (e.g., "Sheet1!A1")
            values: 2D list of values to write
            value_input_option: USER_ENTERED or RAW
        
        Returns:
            API response with updatedCells count
        """
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}"
        params = {"valueInputOption": value_input_option}
        
        return await self._request("PUT", url, json_data={"values": values}, params=params)
    
    async def append_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        values: List[List[Any]],
        value_input_option: str = "USER_ENTERED"
    ) -> Dict[str, Any]:
        """
        Append rows to the end of a sheet.
        
        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: The sheet name
            values: 2D list of values to append
        
        Returns:
            API response with updates info
        """
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{sheet_name}!A1:append"
        params = {
            "valueInputOption": value_input_option,
            "insertDataOption": "INSERT_ROWS"
        }
        
        return await self._request("POST", url, json_data={"values": values}, params=params)
    
    async def clear_range(
        self,
        spreadsheet_id: str,
        range_notation: str
    ) -> Dict[str, Any]:
        """Clear values from a range (keeps formatting)."""
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}:clear"
        return await self._request("POST", url)
    
    # ==================== BATCH OPERATIONS ====================
    
    async def batch_read(
        self,
        spreadsheet_id: str,
        ranges: List[str]
    ) -> Dict[str, List[List[Any]]]:
        """
        Read multiple ranges in a single request.
        
        Returns:
            Dict mapping range notation to values
        """
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values:batchGet"
        params = {"ranges": ranges}
        
        result = await self._request("GET", url, params=params)
        
        values_dict = {}
        for vr in result.get("valueRanges", []):
            range_notation = vr.get("range", "")
            values_dict[range_notation] = vr.get("values", [])
        
        return values_dict
    
    async def batch_write(
        self,
        spreadsheet_id: str,
        data: Dict[str, List[List[Any]]],
        value_input_option: str = "USER_ENTERED"
    ) -> Dict[str, Any]:
        """
        Write to multiple ranges in a single request.
        
        Args:
            spreadsheet_id: The spreadsheet ID
            data: Dict mapping range notation to values
        """
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values:batchUpdate"
        
        body = {
            "valueInputOption": value_input_option,
            "data": [
                {"range": range_notation, "values": values}
                for range_notation, values in data.items()
            ]
        }
        
        return await self._request("POST", url, json_data=body)
    
    # ==================== SHEET MANAGEMENT ====================
    
    async def create_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: int = 1000,
        cols: int = 26
    ) -> Dict[str, Any]:
        """Add a new sheet to a spreadsheet."""
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}:batchUpdate"
        
        body = {
            "requests": [{
                "addSheet": {
                    "properties": {
                        "title": sheet_name,
                        "gridProperties": {
                            "rowCount": rows,
                            "columnCount": cols
                        }
                    }
                }
            }]
        }
        
        return await self._request("POST", url, json_data=body)
    
    async def delete_sheet(
        self,
        spreadsheet_id: str,
        sheet_id: int
    ) -> Dict[str, Any]:
        """Delete a sheet from a spreadsheet."""
        url = f"{SHEETS_API_BASE}/{spreadsheet_id}:batchUpdate"
        
        body = {
            "requests": [{
                "deleteSheet": {"sheetId": sheet_id}
            }]
        }
        
        return await self._request("POST", url, json_data=body)
    
    # ==================== CLEARLEDGR-SPECIFIC ====================
    
    async def read_transactions(
        self,
        spreadsheet_id: str,
        sheet_name: str = "Transactions",
        has_header: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Read transactions from a sheet with standard column format.
        
        Expected columns: Date, Description, Amount, Reference, Vendor
        """
        values = await self.read_range(spreadsheet_id, f"{sheet_name}!A:F")
        
        if not values:
            return []
        
        if has_header:
            headers = [str(h).lower().strip() for h in values[0]]
            data_rows = values[1:]
        else:
            headers = ["date", "description", "amount", "reference", "vendor", "currency"]
            data_rows = values
        
        transactions = []
        for row in data_rows:
            if not row or not any(row):
                continue
            
            tx = {}
            for i, header in enumerate(headers):
                if i < len(row):
                    tx[header] = row[i]
            transactions.append(tx)
        
        return transactions
    
    async def write_reconciliation_results(
        self,
        spreadsheet_id: str,
        matches: List[Dict[str, Any]],
        exceptions: List[Dict[str, Any]],
        sheet_name: str = "Reconciliation Results"
    ) -> Dict[str, Any]:
        """
        Write reconciliation results to a sheet.
        
        Creates/updates two sections: Matches and Exceptions
        """
        # Ensure sheet exists
        try:
            await self.create_sheet(spreadsheet_id, sheet_name)
        except Exception:
            pass  # Sheet might already exist
        
        # Clear existing data
        await self.clear_range(spreadsheet_id, f"{sheet_name}!A:Z")
        
        # Write matches
        match_data = [
            ["=== MATCHES ===", "", "", "", "", ""],
            ["Gateway ID", "Bank ID", "Amount", "Score", "Confidence", "Date"],
        ]
        for m in matches:
            match_data.append([
                m.get("gateway_id", ""),
                m.get("bank_id", ""),
                m.get("amount", 0),
                m.get("score", 0),
                f"{m.get('confidence', 0) * 100:.1f}%",
                m.get("date", ""),
            ])
        
        # Add spacing
        match_data.append([""])
        match_data.append([""])
        
        # Write exceptions
        match_data.append(["=== EXCEPTIONS ===", "", "", "", "", ""])
        match_data.append(["ID", "Type", "Amount", "Vendor", "Priority", "Status"])
        for e in exceptions:
            match_data.append([
                e.get("id", ""),
                e.get("type", ""),
                e.get("amount", 0),
                e.get("vendor", ""),
                e.get("priority", ""),
                e.get("status", "open"),
            ])
        
        return await self.write_range(
            spreadsheet_id,
            f"{sheet_name}!A1",
            match_data
        )
    
    async def update_dashboard(
        self,
        spreadsheet_id: str,
        stats: Dict[str, Any],
        sheet_name: str = "Dashboard"
    ) -> Dict[str, Any]:
        """Update dashboard sheet with current stats."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        dashboard_data = [
            ["Clearledgr Dashboard", "", f"Last Updated: {now}"],
            [""],
            ["Metric", "Value"],
            ["Total Transactions", stats.get("total_transactions", 0)],
            ["Matched", stats.get("matched", 0)],
            ["Match Rate", f"{stats.get('match_rate', 0):.1f}%"],
            ["Open Exceptions", stats.get("open_exceptions", 0)],
            ["Pending Approvals", stats.get("pending_approvals", 0)],
            [""],
            ["By Status", "Count"],
        ]
        
        for status, count in stats.get("transactions", {}).items():
            dashboard_data.append([status.title(), count])
        
        # Ensure sheet exists
        try:
            await self.create_sheet(spreadsheet_id, sheet_name)
        except Exception:
            pass
        
        await self.clear_range(spreadsheet_id, f"{sheet_name}!A:C")
        return await self.write_range(spreadsheet_id, f"{sheet_name}!A1", dashboard_data)


# Helper function
async def get_sheets_client(user_id: str) -> SheetsAPIClient:
    """Get an authenticated Sheets client."""
    client = SheetsAPIClient(user_id)
    await client.ensure_authenticated()
    return client
