"""Helpers to load reconciliation inputs from Sheets."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from clearledgr.models.reconciliation import ReconciliationConfig
from clearledgr.models.transactions import BankTransaction, GLTransaction, Money
from clearledgr.services.csv_parser import parse_csv

# Lazy import to avoid gspread/google-auth dependency chain at startup
def _get_sheets_integration():
    from clearledgr.services.sheets_integration import read_config_from_sheets, read_tab_as_csv_bytes
    return read_config_from_sheets, read_tab_as_csv_bytes


DEFAULT_BANK_TAB = "BANK"
DEFAULT_GL_TAB = "INTERNAL"


def load_reconciliation_inputs_from_sheets(
    sheet_id: str,
    bank_tab: str = DEFAULT_BANK_TAB,
    gl_tab: str = DEFAULT_GL_TAB,
    schedule_config: Dict[str, Any] | None = None,
    sap_gl: List[Dict[str, Any]] | None = None,
) -> Tuple[List[BankTransaction], List[GLTransaction], ReconciliationConfig]:
    read_config_from_sheets, read_tab_as_csv_bytes = _get_sheets_integration()
    
    config = _resolve_config(sheet_id, schedule_config)
    default_config = _default_config()
    mappings = config.get("mappings", {})
    bank_mapping = mappings.get("bank") or default_config["mappings"]["bank"]
    gl_mapping = mappings.get("internal") or mappings.get("gl") or default_config["mappings"]["internal"]

    bank_rows = parse_csv(read_tab_as_csv_bytes(sheet_id, bank_tab), bank_mapping)
    gl_rows = parse_csv(read_tab_as_csv_bytes(sheet_id, gl_tab), gl_mapping)
    if sap_gl:
        gl_rows.extend(_map_sap_gl(sap_gl, gl_mapping))

    bank_transactions = _rows_to_bank_transactions(bank_rows)
    gl_transactions = _rows_to_gl_transactions(gl_rows)

    recon_config = ReconciliationConfig(
        amount_tolerance_pct=config.get("amount_tolerance_pct", default_config["amount_tolerance_pct"]),
        date_window_days=config.get("date_window_days", default_config["date_window_days"]),
    )
    return bank_transactions, gl_transactions, recon_config


def _resolve_config(sheet_id: str, schedule_config: Dict[str, Any] | None) -> Dict[str, Any]:
    config = _default_config()

    if sheet_id:
        try:
            read_config_from_sheets, _ = _get_sheets_integration()
            config = _merge_config(config, read_config_from_sheets(sheet_id))
        except Exception:
            pass

    if schedule_config:
        if schedule_config.get("config"):
            config = _merge_config(config, schedule_config.get("config"))
        else:
            config = _merge_config(config, schedule_config)

    return config


def _merge_config(base: Dict[str, Any], updates: Dict[str, Any] | None) -> Dict[str, Any]:
    if not updates:
        return base

    merged = {
        "mappings": dict(base.get("mappings", {})),
        "amount_tolerance_pct": base.get("amount_tolerance_pct"),
        "date_window_days": base.get("date_window_days"),
    }

    mappings = updates.get("mappings") or {}
    if mappings:
        merged_mappings = dict(merged.get("mappings", {}))
        for source, mapping in mappings.items():
            if mapping:
                merged_mappings[source] = mapping
        merged["mappings"] = merged_mappings

    if "amount_tolerance_pct" in updates and updates.get("amount_tolerance_pct") is not None:
        merged["amount_tolerance_pct"] = updates.get("amount_tolerance_pct")

    if "date_window_days" in updates and updates.get("date_window_days") is not None:
        merged["date_window_days"] = updates.get("date_window_days")

    return merged


def _rows_to_bank_transactions(rows: List[Dict[str, Any]]) -> List[BankTransaction]:
    transactions: List[BankTransaction] = []
    for row in rows:
        txn_id = _first_value(row, ["transaction_id", "bank_txn_id", "txn_id", "id"])
        txn_date = _first_value(row, ["transaction_date", "date"])
        if not txn_id or not txn_date:
            continue

        amount_value = row.get("amount")
        amount_value = abs(amount_value) if amount_value is not None else 0.0

        transactions.append(
            BankTransaction(
                transaction_id=str(txn_id),
                transaction_date=txn_date,
                amount=Money(amount=amount_value, currency="EUR"),
                description=row.get("description"),
                counterparty=row.get("counterparty") or row.get("vendor"),
                metadata=row,
            )
        )
    return transactions


def _rows_to_gl_transactions(rows: List[Dict[str, Any]]) -> List[GLTransaction]:
    transactions: List[GLTransaction] = []
    for row in rows:
        txn_id = _first_value(row, ["transaction_id", "internal_id", "txn_id", "id"])
        txn_date = _first_value(row, ["transaction_date", "date"])
        if not txn_id or not txn_date:
            continue

        amount_value = row.get("amount")
        amount_value = abs(amount_value) if amount_value is not None else 0.0

        transactions.append(
            GLTransaction(
                transaction_id=str(txn_id),
                transaction_date=txn_date,
                amount=Money(amount=amount_value, currency="EUR"),
                description=row.get("description"),
                counterparty=row.get("counterparty") or row.get("vendor"),
                gl_account_code=row.get("gl_account_code") or row.get("gl_code"),
                gl_account_name=row.get("gl_account_name") or row.get("gl_account"),
                metadata=row,
            )
        )
    return transactions


def _map_sap_gl(sap_rows: List[Dict[str, Any]], gl_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Map SAP GL OData fields into the internal GL schema.
    Expected SAP fields: DocumentNumber, PostingDate, AmountInCompanyCodeCurrency, CompanyCode, GLAcount, GLAcountName, Text
    """
    mapped: List[Dict[str, Any]] = []
    for row in sap_rows:
        mapped.append(
            {
                "transaction_id": row.get("DocumentNumber") or row.get("AccountingDocument"),
                "date": row.get("PostingDate") or row.get("DocumentDate"),
                "amount": row.get("AmountInCompanyCodeCurrency") or row.get("AmountInCompanyCodeCrcy"),
                "gl_account_code": row.get("GLAccount") or row.get("GLAccountNumber"),
                "gl_account_name": row.get("GLAccountName") or row.get("GLAccountText"),
                "description": row.get("Text") or row.get("DocumentHeaderText"),
                "company_code": row.get("CompanyCode"),
                "metadata": row,
            }
        )
    return mapped


def _first_value(row: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _default_config() -> Dict[str, Any]:
    return {
        "mappings": {
            "bank": {
                "Bank Transaction ID": "bank_txn_id",
                "Booking Date": "date",
                "Amount": "amount",
            },
            "internal": {
                "Internal ID": "internal_id",
                "Date": "date",
                "Amount": "amount",
            },
        },
        "amount_tolerance_pct": 0.5,
        "date_window_days": 3,
    }
