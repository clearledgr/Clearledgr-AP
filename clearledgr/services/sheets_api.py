"""Google Sheets API client for Clearledgr.

Reuses Gmail OAuth tokens (same Google account, expanded scopes) so there
is no additional authentication step. Finance teams use spreadsheets as
the operational interface — Clearledgr reads from and writes to them.

Used by:
- Reconciliation skill: import bank statements, write matched results
- Future: FP&A aggregation, close preparation checklists
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from clearledgr.core.http_client import get_http_client

logger = logging.getLogger(__name__)

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


class SheetsAPIClient:
    """Google Sheets API client — mirrors GmailAPIClient pattern.

    Reuses the same OAuth token stored by GmailTokenStore. The token
    must have the `spreadsheets` scope (added in GMAIL_SCOPES).
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._token = None
        self._credentials = None

    async def ensure_authenticated(self) -> bool:
        """Load and validate the user's Google OAuth token."""
        from clearledgr.services.gmail_api import token_store

        token = token_store.get(self.user_id)
        if not token or not token.access_token:
            logger.warning("No Google token found for user %s", self.user_id)
            return False
        self._token = token
        return True

    def _headers(self) -> Dict[str, str]:
        """Authorization headers using the stored access token."""
        return {"Authorization": f"Bearer {self._token.access_token}"}

    async def read_sheet(
        self,
        spreadsheet_id: str,
        range_notation: str,
        value_render: str = "FORMATTED_VALUE",
    ) -> List[List[str]]:
        """Read a range from a Google Sheet.

        Args:
            spreadsheet_id: The ID from the sheet URL (between /d/ and /edit)
            range_notation: A1 notation, e.g. "Sheet1!A1:F100"
            value_render: FORMATTED_VALUE, UNFORMATTED_VALUE, or FORMULA

        Returns:
            List of rows, each row is a list of cell values.
        """
        import httpx

        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}"
        params = {"valueRenderOption": value_render}

        client = get_http_client()
        response = await client.get(url, headers=self._headers(), params=params)
        response.raise_for_status()
        data = response.json()

        return data.get("values", [])

    async def write_sheet(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: List[List[Any]],
        input_option: str = "USER_ENTERED",
    ) -> Dict[str, Any]:
        """Write values to a Google Sheet range.

        Args:
            spreadsheet_id: Sheet ID
            range_notation: A1 notation for the target range
            values: List of rows to write
            input_option: USER_ENTERED (parsed) or RAW

        Returns:
            Update response from Sheets API.
        """
        import httpx

        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}"
        params = {"valueInputOption": input_option}
        body = {"range": range_notation, "majorDimension": "ROWS", "values": values}

        client = get_http_client()
        response = await client.put(url, headers=self._headers(), params=params, json=body)
        response.raise_for_status()
        return response.json()

    async def append_rows(
        self,
        spreadsheet_id: str,
        range_notation: str,
        rows: List[List[Any]],
        input_option: str = "USER_ENTERED",
    ) -> Dict[str, Any]:
        """Append rows to a Google Sheet (adds after existing data).

        Args:
            spreadsheet_id: Sheet ID
            range_notation: A1 notation (e.g. "Sheet1!A:F")
            rows: Rows to append
            input_option: USER_ENTERED or RAW

        Returns:
            Append response from Sheets API.
        """
        import httpx

        url = f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}:append"
        params = {"valueInputOption": input_option, "insertDataOption": "INSERT_ROWS"}
        body = {"range": range_notation, "majorDimension": "ROWS", "values": rows}

        client = get_http_client()
        response = await client.post(url, headers=self._headers(), params=params, json=body)
        response.raise_for_status()
        return response.json()

    async def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        """Get spreadsheet title and sheet names."""
        import httpx

        url = f"{SHEETS_API_BASE}/{spreadsheet_id}"
        params = {"fields": "spreadsheetId,properties.title,sheets.properties"}

        client = get_http_client()
        response = await client.get(url, headers=self._headers(), params=params)
        response.raise_for_status()
        return response.json()


def extract_spreadsheet_id(url: str) -> Optional[str]:
    """Extract the spreadsheet ID from a Google Sheets URL.

    Examples:
        https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit
        → "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
    """
    import re

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url or "")
    return match.group(1) if match else None
