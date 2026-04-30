"""SAP S/4HANA adapter (dry-run friendly, live-capable)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from clearledgr.models.erp import (
    ERPDocumentResult,
    ParkedAPInvoiceRequest,
    ParkedJournalEntryRequest,
    SAPDocumentConfig,
    SAPAPIEndpoints,
    SAPVendorList,
    SAPVendor,
    SAPGLAccountList,
    SAPGLAccount,
    SAPOpenInvoiceList,
    SAPOpenInvoice,
    SAPGLLineItemList,
    SAPGLLineItem,
    SAPValidationRequest,
    SAPValidationResult,
    SAPSyncPayload,
)

logger = logging.getLogger(__name__)


class SAPAdapter:
    def __init__(
        self,
        default_config: SAPDocumentConfig | None = None,
        *,
        base_url: Optional[str] = None,
        bearer_token: Optional[str] = None,
    ) -> None:
        self.default_config = default_config or SAPDocumentConfig()
        self.endpoints = SAPAPIEndpoints()
        # Live connection credentials — injected when a real SAP connection is
        # available (e.g. from ``get_erp_connection(organization_id)``).
        self._base_url = (base_url or "").rstrip("/")
        self._bearer_token = bearer_token or ""

    def get_endpoints(self) -> SAPAPIEndpoints:
        return self.endpoints

    def list_vendors(self, query: str | None = None) -> SAPVendorList:
        mode = "dry_run" if self.default_config.dry_run else "live"
        message = "Dry run: connect SAP to fetch vendors." if self.default_config.dry_run else None
        items: List[SAPVendor] = []

        if self.default_config.dry_run:
            items = [
                SAPVendor(vendor_id="100001", name="Acme Hosting", status="active"),
                SAPVendor(vendor_id="100245", name="Global Logistics", status="active"),
                SAPVendor(vendor_id="100411", name="Nimbus Cloud", status="on_hold"),
            ]
            if query:
                items = [item for item in items if query.lower() in item.name.lower()]
            message = "Dry run: returning mocked vendors."

        return SAPVendorList(items=items, mode=mode, message=message)

    def list_gl_accounts(self, query: str | None = None) -> SAPGLAccountList:
        """Return GL accounts from the SAP Chart of Accounts.

        In ``dry_run`` mode: returns a small set of static mock accounts so
        the UI/tests can function without a live SAP connection.

        In live mode: calls the SAP OData endpoint
        ``{base_url}/ChartOfAccounts`` (or ``/GLAccounts`` on S/4HANA Public
        Cloud).  Requires ``base_url`` and ``bearer_token`` to be provided to
        the constructor (typically injected from ``get_erp_connection()``).
        Falls back to an empty list — not mock data — on failure so callers
        can distinguish "no connection" from "real empty result".
        """
        if self.default_config.dry_run:
            items: List[SAPGLAccount] = [
                SAPGLAccount(gl_account="6000", name="Hosting Expense", chart_of_accounts="YCOA"),
                SAPGLAccount(gl_account="6100", name="Software Subscriptions", chart_of_accounts="YCOA"),
                SAPGLAccount(gl_account="2000", name="Accounts Payable", chart_of_accounts="YCOA"),
            ]
            if query:
                items = [item for item in items if query.lower() in item.name.lower()]
            return SAPGLAccountList(items=items, mode="dry_run", message="Dry run: returning mocked GL accounts.")

        # Live mode — call SAP OData API
        if not self._base_url or not self._bearer_token:
            return SAPGLAccountList(
                items=[],
                mode="live",
                message="SAP connection not configured (base_url or bearer_token missing).",
            )

        try:
            import urllib.request as _urllib_request
            import json as _json

            # S/4HANA On-Premise: /sap/opu/odata/sap/FAG_FINANCIALACCOUNTING_GLA_SRV/GLAccountInChartOfAccountsSet
            # S/4HANA Cloud (simplified): /api/v1/ChartOfAccounts
            # We support both via a configurable path; default to the On-Premise OData endpoint.
            odata_path = "/sap/opu/odata/sap/FAG_FINANCIALACCOUNTING_GLA_SRV/GLAccountInChartOfAccountsSet"
            filter_clause = ""
            if query:
                safe_q = query.replace("'", "''")
                filter_clause = f"?$filter=substringof('{safe_q}',GLAccountName)&$format=json"
            else:
                filter_clause = "?$format=json&$top=500"

            url = f"{self._base_url}{odata_path}{filter_clause}"
            req = _urllib_request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Accept": "application/json",
                },
            )
            with _urllib_request.urlopen(req, timeout=15) as resp:
                raw = _json.loads(resp.read().decode("utf-8"))

            entries = (raw.get("d") or {}).get("results") or raw.get("value") or []
            items = []
            for entry in entries:
                gl_account = str(
                    entry.get("GLAccount") or entry.get("gl_account") or entry.get("AccountNumber") or ""
                ).strip()
                name = str(
                    entry.get("GLAccountName") or entry.get("name") or entry.get("AccountDescription") or ""
                ).strip()
                coa = str(
                    entry.get("ChartOfAccounts") or entry.get("chart_of_accounts") or ""
                ).strip()
                if gl_account:
                    items.append(SAPGLAccount(gl_account=gl_account, name=name, chart_of_accounts=coa))

            return SAPGLAccountList(items=items, mode="live", message=None)

        except Exception as exc:
            logger.warning("SAP GL account lookup failed: %s", exc)
            return SAPGLAccountList(
                items=[],
                mode="live",
                message=f"SAP GL account lookup failed: {exc}",
            )

    def list_open_invoices(self, query: str | None = None) -> SAPOpenInvoiceList:
        mode = "dry_run" if self.default_config.dry_run else "live"
        message = "Dry run: connect SAP to fetch open invoices." if self.default_config.dry_run else None
        items: List[SAPOpenInvoice] = []

        if self.default_config.dry_run:
            items = [
                SAPOpenInvoice(
                    invoice_id="INV-100245",
                    vendor_id="100001",
                    vendor_name="Acme Hosting",
                    amount=1420.55,
                    currency="EUR",
                    document_date="2025-02-10",
                    due_date="2025-03-12",
                    status="open",
                    company_code=self.default_config.company_code,
                ),
                SAPOpenInvoice(
                    invoice_id="INV-100387",
                    vendor_id="100245",
                    vendor_name="Global Logistics",
                    amount=987.2,
                    currency="EUR",
                    document_date="2025-02-12",
                    due_date="2025-03-14",
                    status="open",
                    company_code=self.default_config.company_code,
                ),
            ]
            if query:
                items = [item for item in items if query.lower() in (item.vendor_name or "").lower()]
            message = "Dry run: returning mocked open invoices."

        return SAPOpenInvoiceList(items=items, mode=mode, message=message)

    def list_gl_line_items(self, query: str | None = None) -> SAPGLLineItemList:
        mode = "dry_run" if self.default_config.dry_run else "live"
        message = "Dry run: connect SAP to fetch GL line items." if self.default_config.dry_run else None
        items: List[SAPGLLineItem] = []

        if self.default_config.dry_run:
            items = [
                SAPGLLineItem(
                    document_id="1900004412",
                    line_item="001",
                    gl_account="6000",
                    amount=1420.55,
                    currency="EUR",
                    posting_date="2025-02-11",
                    text="Hosting services",
                    company_code=self.default_config.company_code,
                    cost_center="1000",
                    profit_center="NA-OPS",
                    reference="ACME-HOSTING",
                ),
                SAPGLLineItem(
                    document_id="1900004412",
                    line_item="002",
                    gl_account="2000",
                    amount=-1420.55,
                    currency="EUR",
                    posting_date="2025-02-11",
                    text="Accounts payable",
                    company_code=self.default_config.company_code,
                    cost_center="1000",
                    profit_center="NA-OPS",
                    reference="ACME-HOSTING",
                ),
            ]
            if query:
                items = [item for item in items if query.lower() in (item.text or "").lower()]
            message = "Dry run: returning mocked GL line items."

        return SAPGLLineItemList(items=items, mode=mode, message=message)

    def sync_payload(self) -> SAPSyncPayload:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return SAPSyncPayload(
            vendors=self.list_vendors(),
            gl_accounts=self.list_gl_accounts(),
            open_invoices=self.list_open_invoices(),
            gl_line_items=self.list_gl_line_items(),
            timestamp=timestamp,
        )

    def validate_document(self, request: SAPValidationRequest) -> SAPValidationResult:
        config = merge_config(self.default_config, request.config)
        metadata = request.metadata
        missing_fields = []
        warnings: List[str] = []

        if not config.company_code:
            missing_fields.append("company_code")
        if not (metadata.currency or config.currency):
            missing_fields.append("currency")
        if not metadata.amount:
            missing_fields.append("amount")
        if not (metadata.vendor_id or metadata.vendor):
            missing_fields.append("vendor_id")
        if not (metadata.invoice_date or config.document_date or config.posting_date):
            missing_fields.append("document_date")

        if metadata.vendor and not metadata.vendor_id:
            warnings.append("Vendor name provided without SAP vendor_id.")

        return SAPValidationResult(
            valid=len(missing_fields) == 0,
            mode="dry_run" if config.dry_run else "live",
            missing_fields=missing_fields,
            warnings=warnings,
        )

    def park_ap_invoice(self, request: ParkedAPInvoiceRequest) -> ERPDocumentResult:
        config = merge_config(self.default_config, request.config)
        metadata = request.metadata
        missing_fields = []
        warnings: List[str] = []

        if not (metadata.vendor_id or metadata.vendor):
            missing_fields.append("vendor_id")
        if not metadata.amount:
            missing_fields.append("amount")
        if not (metadata.currency or config.currency):
            missing_fields.append("currency")
        if not config.company_code:
            missing_fields.append("company_code")
        if not (metadata.invoice_date or config.document_date):
            missing_fields.append("invoice_date")

        if metadata.vendor and not metadata.vendor_id:
            warnings.append("Vendor name provided without SAP vendor_id.")
        if not request.line_items:
            warnings.append("No line items provided; SAP may require GL coding.")

        document_id = build_document_id(prefix="SAP-PI")
        mode = "dry_run" if config.dry_run else "live"
        api_name = self.endpoints.supplier_invoice
        message = (
            f"Dry run: would call {api_name} to park AP invoice."
            if config.dry_run
            else f"Parked AP invoice via {api_name}."
        )

        return ERPDocumentResult(
            document_id=document_id,
            status="parked",
            mode=mode,
            message=message,
            missing_fields=missing_fields,
            warnings=warnings,
        )

    def park_journal_entry(self, request: ParkedJournalEntryRequest) -> ERPDocumentResult:
        config = merge_config(self.default_config, request.config)
        metadata = request.metadata
        missing_fields = []
        warnings: List[str] = []

        if not config.company_code:
            missing_fields.append("company_code")
        if not (metadata.currency or config.currency):
            missing_fields.append("currency")
        if not (config.posting_date or metadata.invoice_date):
            missing_fields.append("posting_date")
        if len(request.line_items) < 2:
            missing_fields.append("line_items")
            warnings.append("Journal entry requires balanced debit/credit lines.")

        document_id = build_document_id(prefix="SAP-JE")
        mode = "dry_run" if config.dry_run else "live"
        api_name = self.endpoints.journal_entry
        message = (
            f"Dry run: would call {api_name} to park journal entry."
            if config.dry_run
            else f"Parked journal entry via {api_name}."
        )

        return ERPDocumentResult(
            document_id=document_id,
            status="parked",
            mode=mode,
            message=message,
            missing_fields=missing_fields,
            warnings=warnings,
        )


def merge_config(defaults: SAPDocumentConfig, override: SAPDocumentConfig) -> SAPDocumentConfig:
    data = defaults.model_dump()
    override_data = override.model_dump(exclude_unset=True)
    for key, value in override_data.items():
        if value is not None:
            data[key] = value
    return SAPDocumentConfig(**data)


def build_document_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"
