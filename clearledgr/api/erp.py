"""ERP integration endpoints (SAP S/4HANA)."""
from fastapi import APIRouter, Depends

from clearledgr.api.deps import get_sap_adapter
from clearledgr.models.erp import (
    ERPDocumentResult,
    ParkedAPInvoiceRequest,
    ParkedJournalEntryRequest,
    SAPAPIEndpoints,
    SAPVendorList,
    SAPGLAccountList,
    SAPOpenInvoiceList,
    SAPGLLineItemList,
    SAPSyncPayload,
    SAPValidationRequest,
    SAPValidationResult,
)
from clearledgr.services.erp.sap import SAPAdapter

router = APIRouter(prefix="/erp/sap", tags=["ERP"])


@router.get("/endpoints", response_model=SAPAPIEndpoints)
def get_sap_endpoints(
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.get_endpoints()


@router.get("/vendors", response_model=SAPVendorList)
def list_vendors(
    query: str | None = None,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.list_vendors(query)


@router.get("/gl-accounts", response_model=SAPGLAccountList)
def list_gl_accounts(
    query: str | None = None,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.list_gl_accounts(query)


@router.get("/open-invoices", response_model=SAPOpenInvoiceList)
def list_open_invoices(
    query: str | None = None,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.list_open_invoices(query)


@router.get("/gl-line-items", response_model=SAPGLLineItemList)
def list_gl_line_items(
    query: str | None = None,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.list_gl_line_items(query)


@router.get("/sync", response_model=SAPSyncPayload)
def sync_sap_payload(
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.sync_payload()


@router.post("/validate", response_model=SAPValidationResult)
def validate_document(
    payload: SAPValidationRequest,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.validate_document(payload)


@router.post("/park-ap-invoice", response_model=ERPDocumentResult)
def park_ap_invoice(
    payload: ParkedAPInvoiceRequest,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.park_ap_invoice(payload)


@router.post("/park-journal-entry", response_model=ERPDocumentResult)
def park_journal_entry(
    payload: ParkedJournalEntryRequest,
    sap: SAPAdapter = Depends(get_sap_adapter),
):
    return sap.park_journal_entry(payload)
