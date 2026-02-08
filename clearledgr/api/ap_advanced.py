"""
Advanced AP Workflow API Endpoints

Advanced/optional AP features:
- Document Retention & Compliance
- Multi-Currency Support
- Tax Calculations
- Accruals & Month-End
"""

from datetime import datetime, date
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import logging

from clearledgr.services.document_retention import (
    get_document_retention_service,
    DocumentType,
    RetentionStatus,
)
from clearledgr.services.multi_currency import get_multi_currency_service
from clearledgr.services.tax_calculations import (
    get_tax_calculation_service,
    TaxType,
)
from clearledgr.services.accruals import (
    get_accruals_service,
    AccrualType,
    AccrualStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap-advanced", tags=["ap-advanced"])


# =============================================================================
# REQUEST MODELS
# =============================================================================

class RegisterDocumentRequest(BaseModel):
    """Register a document for retention."""
    document_id: str
    document_type: str
    document_number: str = ""
    vendor_name: str = ""
    amount: float = 0.0
    document_date: Optional[str] = None
    storage_location: str = ""
    organization_id: str = "default"


class LegalHoldRequest(BaseModel):
    """Place or release legal hold."""
    record_id: str
    reason: str = ""
    placed_by: str = ""
    organization_id: str = "default"


class CurrencyConvertRequest(BaseModel):
    """Convert currency."""
    amount: float
    from_currency: str
    to_currency: str
    rate_date: Optional[str] = None
    organization_id: str = "default"


class SetManualRateRequest(BaseModel):
    """Set manual exchange rate."""
    from_currency: str
    to_currency: str
    rate: float
    rate_date: Optional[str] = None
    organization_id: str = "default"


class CalculateTaxRequest(BaseModel):
    """Calculate tax."""
    net_amount: float
    tax_code: str = ""
    country: str = ""
    vendor_type: str = ""
    is_service: bool = False
    organization_id: str = "default"


class CreateAccrualRequest(BaseModel):
    """Create an accrual."""
    accrual_type: str = "expense"
    vendor_name: str
    amount: float
    expense_account: str
    description: str
    department: str = ""
    accrual_date: Optional[str] = None
    auto_reverse: bool = True
    organization_id: str = "default"


class CreateGRNIAccrualRequest(BaseModel):
    """Create GRNI accrual."""
    po_id: str
    po_number: str
    vendor_name: str
    amount: float
    expense_account: str
    department: str = ""
    accrual_date: Optional[str] = None
    organization_id: str = "default"


class RunMonthEndRequest(BaseModel):
    """Run month-end accruals."""
    month: int
    year: int
    post_entries: bool = False
    posted_by: str = "system"
    organization_id: str = "default"


# =============================================================================
# DOCUMENT RETENTION
# =============================================================================

@router.post("/retention/register")
async def register_document(request: RegisterDocumentRequest):
    """Register a document for retention tracking."""
    service = get_document_retention_service(request.organization_id)
    
    doc_type_map = {
        "invoice": DocumentType.INVOICE,
        "credit_note": DocumentType.CREDIT_NOTE,
        "purchase_order": DocumentType.PURCHASE_ORDER,
        "receipt": DocumentType.RECEIPT,
        "contract": DocumentType.CONTRACT,
        "w9": DocumentType.W9,
        "tax_form": DocumentType.TAX_FORM,
        "bank_statement": DocumentType.BANK_STATEMENT,
    }
    
    doc_date = None
    if request.document_date:
        doc_date = date.fromisoformat(request.document_date)
    
    record = service.register_document(
        document_id=request.document_id,
        document_type=doc_type_map.get(request.document_type, DocumentType.OTHER),
        document_number=request.document_number,
        vendor_name=request.vendor_name,
        amount=request.amount,
        document_date=doc_date,
        storage_location=request.storage_location,
    )
    
    return record.to_dict()


@router.post("/retention/{record_id}/archive")
async def archive_document(record_id: str, archived_by: str = "", organization_id: str = "default"):
    """Archive a document."""
    service = get_document_retention_service(organization_id)
    
    try:
        record = service.archive_document(record_id, archived_by)
        return record.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/retention/legal-hold/place")
async def place_legal_hold(request: LegalHoldRequest):
    """Place a legal hold on a document."""
    service = get_document_retention_service(request.organization_id)
    
    try:
        record = service.place_legal_hold(
            record_id=request.record_id,
            reason=request.reason,
            placed_by=request.placed_by,
        )
        return record.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/retention/legal-hold/release")
async def release_legal_hold(request: LegalHoldRequest):
    """Release a legal hold."""
    service = get_document_retention_service(request.organization_id)
    
    try:
        record = service.release_legal_hold(request.record_id, request.placed_by)
        return record.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/retention/expiring")
async def get_expiring_documents(days: int = 90, organization_id: str = "default"):
    """Get documents with retention expiring soon."""
    service = get_document_retention_service(organization_id)
    docs = service.get_expiring_documents(days)
    return [d.to_dict() for d in docs]


@router.get("/retention/legal-holds")
async def get_legal_holds(organization_id: str = "default"):
    """Get all documents on legal hold."""
    service = get_document_retention_service(organization_id)
    docs = service.get_documents_on_legal_hold()
    return [d.to_dict() for d in docs]


@router.post("/retention/run-job")
async def run_retention_job(organization_id: str = "default"):
    """Run retention maintenance job."""
    service = get_document_retention_service(organization_id)
    return service.run_retention_job()


@router.get("/retention/compliance-report")
async def get_compliance_report(organization_id: str = "default"):
    """Get compliance report."""
    service = get_document_retention_service(organization_id)
    return service.get_compliance_report()


@router.get("/retention/summary")
async def get_retention_summary(organization_id: str = "default"):
    """Get retention summary."""
    service = get_document_retention_service(organization_id)
    return service.get_summary()


# =============================================================================
# MULTI-CURRENCY
# =============================================================================

@router.post("/currency/convert")
async def convert_currency(request: CurrencyConvertRequest):
    """Convert currency amount."""
    service = get_multi_currency_service(request.organization_id)
    
    rate_date = None
    if request.rate_date:
        rate_date = date.fromisoformat(request.rate_date)
    
    conversion = await service.convert(
        amount=request.amount,
        from_currency=request.from_currency,
        to_currency=request.to_currency,
        rate_date=rate_date,
    )
    
    return conversion.to_dict()


@router.get("/currency/rate/{from_currency}/{to_currency}")
async def get_exchange_rate(
    from_currency: str,
    to_currency: str,
    rate_date: Optional[str] = None,
    organization_id: str = "default"
):
    """Get exchange rate."""
    service = get_multi_currency_service(organization_id)
    
    r_date = date.fromisoformat(rate_date) if rate_date else None
    rate = await service.get_exchange_rate(from_currency, to_currency, r_date)
    
    return rate.to_dict()


@router.post("/currency/rate/manual")
async def set_manual_rate(request: SetManualRateRequest):
    """Set manual exchange rate."""
    service = get_multi_currency_service(request.organization_id)
    
    r_date = date.fromisoformat(request.rate_date) if request.rate_date else None
    
    rate = service.set_manual_rate(
        from_currency=request.from_currency,
        to_currency=request.to_currency,
        rate=request.rate,
        rate_date=r_date,
    )
    
    return rate.to_dict()


@router.get("/currency/summary")
async def get_currency_summary(organization_id: str = "default"):
    """Get currency service summary."""
    service = get_multi_currency_service(organization_id)
    return service.get_summary()


# =============================================================================
# TAX CALCULATIONS
# =============================================================================

@router.post("/tax/calculate")
async def calculate_tax(request: CalculateTaxRequest):
    """Calculate tax for an amount."""
    service = get_tax_calculation_service(request.organization_id)
    
    calc = service.calculate_tax(
        net_amount=request.net_amount,
        tax_code=request.tax_code,
        country=request.country,
        vendor_type=request.vendor_type,
        is_service=request.is_service,
    )
    
    return calc.to_dict()


@router.get("/tax/codes")
async def get_tax_codes(country: Optional[str] = None, organization_id: str = "default"):
    """Get tax codes."""
    service = get_tax_calculation_service(organization_id)
    
    if country:
        codes = service.get_tax_codes_for_country(country)
    else:
        codes = service.get_all_tax_codes()
    
    return [c.to_dict() for c in codes]


@router.post("/tax/exemption/{vendor_id}")
async def register_vendor_exemption(
    vendor_id: str,
    exemption_certificate: str,
    organization_id: str = "default"
):
    """Register vendor tax exemption."""
    service = get_tax_calculation_service(organization_id)
    service.register_vendor_exemption(vendor_id, exemption_certificate)
    return {"status": "success", "vendor_id": vendor_id}


@router.get("/tax/summary")
async def get_tax_summary(organization_id: str = "default"):
    """Get tax service summary."""
    service = get_tax_calculation_service(organization_id)
    return service.get_summary()


# =============================================================================
# ACCRUALS
# =============================================================================

@router.post("/accruals/expense")
async def create_expense_accrual(request: CreateAccrualRequest):
    """Create an expense accrual."""
    service = get_accruals_service(request.organization_id)
    
    accrual_date = None
    if request.accrual_date:
        accrual_date = date.fromisoformat(request.accrual_date)
    
    entry = service.create_expense_accrual(
        vendor_name=request.vendor_name,
        amount=request.amount,
        expense_account=request.expense_account,
        description=request.description,
        department=request.department,
        accrual_date=accrual_date,
    )
    
    return entry.to_dict()


@router.post("/accruals/grni")
async def create_grni_accrual(request: CreateGRNIAccrualRequest):
    """Create a GRNI (Goods Received Not Invoiced) accrual."""
    service = get_accruals_service(request.organization_id)
    
    accrual_date = None
    if request.accrual_date:
        accrual_date = date.fromisoformat(request.accrual_date)
    
    entry = service.create_grni_accrual(
        po_id=request.po_id,
        po_number=request.po_number,
        vendor_name=request.vendor_name,
        amount=request.amount,
        expense_account=request.expense_account,
        department=request.department,
        accrual_date=accrual_date,
    )
    
    return entry.to_dict()


@router.post("/accruals/{accrual_id}/post")
async def post_accrual(accrual_id: str, posted_by: str, organization_id: str = "default"):
    """Post an accrual to the ledger."""
    service = get_accruals_service(organization_id)
    
    try:
        entry = service.post_accrual(accrual_id, posted_by)
        return entry.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/accruals/{accrual_id}/reverse")
async def reverse_accrual(
    accrual_id: str,
    reversal_date: Optional[str] = None,
    organization_id: str = "default"
):
    """Reverse an accrual."""
    service = get_accruals_service(organization_id)
    
    r_date = date.fromisoformat(reversal_date) if reversal_date else None
    
    try:
        reversal = service.reverse_accrual(accrual_id, r_date)
        return reversal.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/accruals/month-end")
async def run_month_end(request: RunMonthEndRequest):
    """Run month-end accrual process."""
    service = get_accruals_service(request.organization_id)
    
    results = service.run_month_end(
        month=request.month,
        year=request.year,
        post_entries=request.post_entries,
        posted_by=request.posted_by,
    )
    
    return results


@router.get("/accruals/period/{year}/{month}")
async def get_period_accruals(
    year: int,
    month: int,
    status: Optional[str] = None,
    organization_id: str = "default"
):
    """Get accruals for a period."""
    service = get_accruals_service(organization_id)
    
    status_enum = AccrualStatus(status) if status else None
    accruals = service.get_accruals_for_period(month, year, status_enum)
    
    return [a.to_dict() for a in accruals]


@router.get("/accruals/pending-reversals")
async def get_pending_reversals(organization_id: str = "default"):
    """Get accruals pending reversal."""
    service = get_accruals_service(organization_id)
    accruals = service.get_pending_reversals()
    return [a.to_dict() for a in accruals]


@router.get("/accruals/summary")
async def get_accruals_summary(organization_id: str = "default"):
    """Get accruals summary."""
    service = get_accruals_service(organization_id)
    return service.get_summary()


# =============================================================================
# EU VAT VALIDATION ENDPOINTS
# =============================================================================

from clearledgr.services.eu_vat_validation import (
    get_vat_validation_service,
    VATValidationStatus,
)


class VATValidationRequest(BaseModel):
    """Request to validate a VAT number."""
    vat_number: str
    use_vies: bool = True  # If True, validate against VIES API


class BatchVATValidationRequest(BaseModel):
    """Request to validate multiple VAT numbers."""
    vat_numbers: List[str]
    use_vies: bool = True


@router.post("/vat/validate")
async def validate_vat_number(request: VATValidationRequest):
    """
    Validate an EU VAT number.
    
    - Format validation: Checks if the VAT number matches the country-specific format
    - VIES validation: Queries the EU VIES service for real-time validation and company details
    
    Supports all 27 EU member states + Northern Ireland (XI).
    """
    service = get_vat_validation_service()
    
    if request.use_vies:
        result = await service.validate_vies(request.vat_number)
    else:
        result = service.validate_format(request.vat_number)
    
    return result.to_dict()


@router.get("/vat/validate/{vat_number}")
async def validate_vat_number_get(vat_number: str, use_vies: bool = True):
    """
    Validate an EU VAT number (GET endpoint).
    
    Args:
        vat_number: VAT number with country prefix (e.g., DE123456789)
        use_vies: If True, validate against VIES API (default: True)
    """
    service = get_vat_validation_service()
    
    if use_vies:
        result = await service.validate_vies(vat_number)
    else:
        result = service.validate_format(vat_number)
    
    return result.to_dict()


@router.post("/vat/validate-batch")
async def validate_vat_batch(request: BatchVATValidationRequest):
    """
    Validate multiple EU VAT numbers in batch.
    
    Returns validation results for all provided VAT numbers.
    Rate-limited to avoid overloading VIES API.
    """
    service = get_vat_validation_service()
    results = await service.validate_batch(request.vat_numbers, use_vies=request.use_vies)
    return [r.to_dict() for r in results]


@router.get("/vat/format/{country_code}")
async def get_vat_format(country_code: str):
    """
    Get VAT number format for a specific country.
    
    Returns the expected format pattern and an example.
    """
    service = get_vat_validation_service()
    format_info = service.get_country_format(country_code)
    
    if not format_info:
        raise HTTPException(
            status_code=404,
            detail=f"Country code '{country_code}' not found. Use 2-letter EU country codes (e.g., DE, FR, NL)."
        )
    
    return format_info


@router.get("/vat/countries")
async def get_supported_vat_countries():
    """
    Get list of supported countries for VAT validation.
    
    Returns all EU member states plus Northern Ireland (XI).
    """
    service = get_vat_validation_service()
    countries = service.get_supported_countries()
    
    # Add country names
    country_names = {
        "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus",
        "CZ": "Czech Republic", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
        "EL": "Greece", "ES": "Spain", "FI": "Finland", "FR": "France",
        "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
        "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta",
        "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
        "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "XI": "Northern Ireland",
        "GB": "United Kingdom (historical)",
    }
    
    return [
        {"code": code, "name": country_names.get(code, code)}
        for code in countries
    ]
