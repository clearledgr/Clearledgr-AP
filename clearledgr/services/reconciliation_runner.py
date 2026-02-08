"""Reconciliation helpers to reduce API duplication."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Tuple

from clearledgr.reconciliation_engine import load_sources, reconcile_data, build_outputs, send_summary_notification

# Lazy import to avoid gspread/google-auth dependency chain at startup
def _get_sheets_integration():
    from clearledgr.services.sheets_integration import read_config_from_sheets, read_tab_as_csv_bytes, write_outputs_to_sheets
    return read_config_from_sheets, read_tab_as_csv_bytes, write_outputs_to_sheets


REQUIRED_SOURCES = ("payment_gateway", "bank", "internal")


def validate_period_dates(period_start: str, period_end: str) -> None:
    try:
        datetime.strptime(period_start, "%Y-%m-%d")
        datetime.strptime(period_end, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Invalid date format. Use YYYY-MM-DD") from exc


def validate_reconciliation_config(config: Dict[str, Any]) -> None:
    if "mappings" not in config:
        raise ValueError("Config must contain 'mappings' key")

    mappings = config.get("mappings", {})
    for source in REQUIRED_SOURCES:
        if source not in mappings:
            raise ValueError(f"Config mappings must include '{source}'")


def run_reconciliation_pipeline(
    config: Dict[str, Any],
    period_start: str,
    period_end: str,
    gateway_bytes: bytes,
    bank_bytes: bytes,
    internal_bytes: bytes,
) -> Dict[str, Any]:
    sources = load_sources(config, gateway_bytes, bank_bytes, internal_bytes)
    reconciliation_result = reconcile_data(config, sources)
    outputs = build_outputs(period_start, period_end, reconciliation_result)
    try:
        send_summary_notification(config, period_start, period_end, outputs)
    except Exception as exc:
        print(f"Notification error (non-fatal): {exc}")
    return outputs


def run_reconciliation_from_sheets(
    sheet_id: str,
    period_start: str,
    period_end: str,
    gateway_tab: str,
    bank_tab: str,
    internal_tab: str,
) -> Tuple[Dict[str, Any], str]:
    read_config_from_sheets, read_tab_as_csv_bytes, write_outputs_to_sheets = _get_sheets_integration()
    
    config = read_config_from_sheets(sheet_id)

    gateway_bytes = read_tab_as_csv_bytes(sheet_id, gateway_tab)
    bank_bytes = read_tab_as_csv_bytes(sheet_id, bank_tab)
    internal_bytes = read_tab_as_csv_bytes(sheet_id, internal_tab)

    outputs = run_reconciliation_pipeline(
        config,
        period_start,
        period_end,
        gateway_bytes,
        bank_bytes,
        internal_bytes,
    )

    sheet_url = write_outputs_to_sheets(
        sheet_id,
        period_start,
        period_end,
        outputs,
    )
    return outputs, sheet_url
