"""Lightweight SAP OData client.

This is a real HTTP client (not a stub) that will attempt to talk to SAP if
SAP_BASE_URL (and credentials) are provided. It falls back gracefully to a
no-op response when configuration is missing so the rest of the product can
run in development.
"""
from __future__ import annotations

import base64
import os
from typing import List, Dict, Any, Tuple

import httpx


class SAPService:
    """
    Minimal OData wrapper for:
    - Pulling GL line items (for matching)
    - Posting journal entries (approved drafts)
    """

    def __init__(self) -> None:
        self.base_url = os.getenv("SAP_BASE_URL", "").rstrip("/")
        self.company_code = os.getenv("SAP_COMPANY_CODE")
        self.username = os.getenv("SAP_USERNAME")
        self.password = os.getenv("SAP_PASSWORD")
        self.timeout = float(os.getenv("SAP_TIMEOUT_SECS", "8"))
        self.default_currency = os.getenv("SAP_CURRENCY", "EUR")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def pull_gl_transactions(self, company_code: str | None = None) -> List[Dict[str, Any]]:
        """
        Fetch GL line items via SAP OData.
        Returns [] if not configured.
        """
        if not self._is_configured():
            return []

        url = f"{self.base_url}/sap/opu/odata/sap/C_GLACCOUNTLINEITEM_SRV/GLAccountLineItems"
        params = {}
        if company_code or self.company_code:
            params["CompanyCode"] = company_code or self.company_code

        data, _ = self._request("GET", url, params=params)
        rows = data.get("d", {}).get("results", []) if data else []
        return [self._normalize_gl_row(r) for r in rows]

    def post_journal_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Post approved journal entries to SAP.
        Returns status payload; if not configured, returns a soft failure.
        """
        if not self._is_configured():
            return {"status": "skipped", "reason": "SAP not configured"}

        valid_entries, errors = self._validate_journal_entries(entries)
        if errors:
            return {"status": "invalid", "errors": errors}

        url = f"{self.base_url}/sap/opu/odata/sap/C_JOURNALENTRY_SRV/JournalEntries"
        payload = {"d": {"results": [self._map_journal_entry(e) for e in valid_entries]}}
        data, status = self._request("POST", url, json=payload)
        if data and isinstance(data, dict):
            doc_numbers = self._extract_doc_numbers(data)
            return {"status": status, "sap_doc_numbers": doc_numbers, "raw": data}
        return {"status": status, "sap_error": data}

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _is_configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    def _auth_headers(self) -> Dict[str, str]:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(
        self,
        method: str,
        url: str,
        params: Dict[str, Any] | None = None,
        json: Dict[str, Any] | None = None,
    ) -> Tuple[Dict[str, Any] | None, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._is_configured():
            headers.update(self._auth_headers())

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, params=params, json=json, headers=headers)
                status = f"{resp.status_code}"
                body = None
                try:
                    body = resp.json()
                except Exception:
                    body = {"text": resp.text}
                if resp.is_error:
                    return {"status": status, "sap_error": body}, status
                return body, status
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": str(exc)}, "error"

    # ------------------------------------------------------------------ #
    # Mapping helpers
    # ------------------------------------------------------------------ #
    def _map_journal_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map internal draft JE to SAP JournalEntry payload.
        Expect entry keys: date, description, debit_accounts (list), credit_accounts (list), company_code.
        """
        lines = []
        for debit in entry.get("debit_accounts", []) or entry.get("debits", []) or []:
            lines.append(
                {
                    "GLAccount": self._normalize_gl_account(debit.get("account") or debit.get("gl_account_code")),
                    "AmountInCompanyCodeCurrency": abs(debit.get("amount") or 0),
                    "DCIndicator": "D",
                    "Text": entry.get("description"),
                    "Currency": debit.get("currency") or self.default_currency,
                    "TaxCode": debit.get("tax_code"),
                    "TaxAmount": debit.get("tax_amount"),
                }
            )
        for credit in entry.get("credit_accounts", []) or entry.get("credits", []) or []:
            lines.append(
                {
                    "GLAccount": self._normalize_gl_account(credit.get("account") or credit.get("gl_account_code")),
                    "AmountInCompanyCodeCurrency": abs(credit.get("amount") or 0),
                    "DCIndicator": "C",
                    "Text": entry.get("description"),
                    "Currency": credit.get("currency") or self.default_currency,
                    "TaxCode": credit.get("tax_code"),
                    "TaxAmount": credit.get("tax_amount"),
                }
            )
        return {
            "CompanyCode": entry.get("company_code") or self.company_code,
            "DocumentDate": entry.get("date"),
            "PostingDate": entry.get("date"),
            "DocumentHeaderText": entry.get("description"),
            "Items": {"results": lines},
        }

    def _normalize_gl_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize SAP GL row fields into a consistent structure."""
        return {
            "DocumentNumber": row.get("DocumentNumber") or row.get("AccountingDocument"),
            "DocumentDate": row.get("DocumentDate"),
            "PostingDate": row.get("PostingDate"),
            "AmountInCompanyCodeCurrency": row.get("AmountInCompanyCodeCurrency") or row.get("AmountInCompanyCodeCrcy"),
            "GLAccount": self._normalize_gl_account(row.get("GLAccount") or row.get("GLAccountNumber")),
            "GLAccountName": row.get("GLAccountName") or row.get("GLAccountText"),
            "CompanyCode": row.get("CompanyCode"),
            "Text": row.get("Text") or row.get("DocumentHeaderText"),
            "metadata": row,
        }

    def _validate_journal_entries(self, entries: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Ensure required fields exist before posting to SAP."""
        valid: List[Dict[str, Any]] = []
        errors: List[str] = []
        for entry in entries:
            if not entry.get("debit_accounts") and not entry.get("debits"):
                errors.append(f"Entry {entry.get('entry_id','')} missing debits")
                continue
            if not entry.get("credit_accounts") and not entry.get("credits"):
                errors.append(f"Entry {entry.get('entry_id','')} missing credits")
                continue
            if not (entry.get("company_code") or self.company_code):
                errors.append(f"Entry {entry.get('entry_id','')} missing company_code")
                continue
            if not self._amounts_positive(entry):
                errors.append(f"Entry {entry.get('entry_id','')} has non-positive amounts")
                continue
            valid.append(entry)
        return valid, errors

    def _extract_doc_numbers(self, response: Dict[str, Any]) -> List[str]:
        doc_numbers: List[str] = []
        try:
            results = response.get("d", {}).get("results", [])
            for r in results:
                if r.get("DocumentNumber"):
                    doc_numbers.append(str(r["DocumentNumber"]))
        except Exception:
            return doc_numbers
        return doc_numbers

    def _normalize_gl_account(self, value: str | None) -> str | None:
        if value is None:
            return None
        # Strip leading zeros/spaces
        normalized = str(value).strip()
        normalized = normalized.lstrip("0") or normalized
        return normalized

    def _amounts_positive(self, entry: Dict[str, Any]) -> bool:
        def has_positive(lines: List[Dict[str, Any]]) -> bool:
            return all((ln.get("amount") or ln.get("AmountInCompanyCodeCurrency") or 0) >= 0 for ln in lines)

        debits = entry.get("debit_accounts") or entry.get("debits") or []
        credits = entry.get("credit_accounts") or entry.get("credits") or []
        return has_positive(debits) and has_positive(credits)
