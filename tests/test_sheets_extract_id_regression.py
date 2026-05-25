"""extract_spreadsheet_id is a module function, not a SheetsAPIClient method.

Two call sites (scheduled_reports, workspace_shell export-to-sheets) called
SheetsAPIClient.extract_spreadsheet_id -> AttributeError (one swallowed, one
500). Guard against re-introducing the wrong call.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.services.sheets_api import SheetsAPIClient, extract_spreadsheet_id  # noqa: E402


def test_extract_spreadsheet_id_module_function():
    assert extract_spreadsheet_id(
        "https://docs.google.com/spreadsheets/d/ABC123xyz/edit#gid=0"
    ) == "ABC123xyz"
    # It is NOT a classmethod/method — call sites must use the module function.
    assert not hasattr(SheetsAPIClient, "extract_spreadsheet_id")
