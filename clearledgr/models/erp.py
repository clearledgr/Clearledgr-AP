"""ERP request/response models."""
from typing import List, Optional, Literal

from pydantic import Field

from clearledgr.models.base import CLBaseModel


class ERPMetadata(CLBaseModel):
    vendor: Optional[str] = None
    vendor_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    gmail_message_id: Optional[str] = None
    subject: Optional[str] = None
    sender_email: Optional[str] = None
    attachment_names: List[str] = Field(default_factory=list)


class ERPLineItem(CLBaseModel):
    gl_account: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    tax_code: Optional[str] = None
    cost_center: Optional[str] = None
    profit_center: Optional[str] = None
    description: Optional[str] = None
    debit_credit: Optional[Literal["debit", "credit"]] = None


class SAPDocumentConfig(CLBaseModel):
    company_code: Optional[str] = None
    document_type: Optional[str] = None
    currency: Optional[str] = None
    posting_date: Optional[str] = None
    document_date: Optional[str] = None
    tax_code: Optional[str] = None
    dry_run: bool = True


class ParkedAPInvoiceRequest(CLBaseModel):
    metadata: ERPMetadata
    config: SAPDocumentConfig = Field(default_factory=SAPDocumentConfig)
    line_items: List[ERPLineItem] = Field(default_factory=list)


class ParkedJournalEntryRequest(CLBaseModel):
    metadata: ERPMetadata
    config: SAPDocumentConfig = Field(default_factory=SAPDocumentConfig)
    line_items: List[ERPLineItem] = Field(default_factory=list)


class ERPDocumentResult(CLBaseModel):
    document_id: str
    status: Literal["parked", "posted", "failed"]
    mode: Literal["dry_run", "live"]
    message: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SAPAPIEndpoints(CLBaseModel):
    supplier_invoice: str = "/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV"
    journal_entry: str = "/sap/opu/odata/sap/API_JOURNALENTRY_SRV"
    business_partner: str = "/sap/opu/odata/sap/API_BUSINESS_PARTNER"
    supplier: str = "/sap/opu/odata/sap/API_SUPPLIER"
    gl_accounts: str = "/sap/opu/odata/sap/API_GLACCOUNTINCHARTOFACCOUNTS_SRV"
    open_invoices: str = "/sap/opu/odata/sap/C_PURCHASEINVOICE_SRV"
    gl_line_items: str = "/sap/opu/odata/sap/C_GLACCOUNTLINEITEM_SRV"


class SAPVendor(CLBaseModel):
    vendor_id: str
    name: str
    status: Optional[str] = None


class SAPVendorList(CLBaseModel):
    items: List[SAPVendor] = Field(default_factory=list)
    mode: Literal["dry_run", "live"]
    message: Optional[str] = None


class SAPGLAccount(CLBaseModel):
    gl_account: str
    name: str
    chart_of_accounts: Optional[str] = None


class SAPGLAccountList(CLBaseModel):
    items: List[SAPGLAccount] = Field(default_factory=list)
    mode: Literal["dry_run", "live"]
    message: Optional[str] = None


class SAPOpenInvoice(CLBaseModel):
    invoice_id: str
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    document_date: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    company_code: Optional[str] = None


class SAPOpenInvoiceList(CLBaseModel):
    items: List[SAPOpenInvoice] = Field(default_factory=list)
    mode: Literal["dry_run", "live"]
    message: Optional[str] = None


class SAPGLLineItem(CLBaseModel):
    document_id: str
    line_item: Optional[str] = None
    gl_account: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    posting_date: Optional[str] = None
    text: Optional[str] = None
    company_code: Optional[str] = None
    cost_center: Optional[str] = None
    profit_center: Optional[str] = None
    reference: Optional[str] = None


class SAPGLLineItemList(CLBaseModel):
    items: List[SAPGLLineItem] = Field(default_factory=list)
    mode: Literal["dry_run", "live"]
    message: Optional[str] = None


class SAPSyncPayload(CLBaseModel):
    vendors: SAPVendorList
    gl_accounts: SAPGLAccountList
    open_invoices: SAPOpenInvoiceList
    gl_line_items: SAPGLLineItemList
    timestamp: str


class SAPValidationRequest(CLBaseModel):
    metadata: ERPMetadata
    config: SAPDocumentConfig = Field(default_factory=SAPDocumentConfig)


class SAPValidationResult(CLBaseModel):
    valid: bool
    mode: Literal["dry_run", "live"]
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
