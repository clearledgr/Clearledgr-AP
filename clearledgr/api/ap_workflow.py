"""
AP Workflow API Endpoints

Unified API for complete AP workflow features:
- Early Payment Discounts
- Multi-level Approval Chains
- AP Aging Reports
- Vendor Management
- Audit Trail Query
"""

from datetime import datetime, date
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query, Body, Depends
from pydantic import BaseModel
import logging

from clearledgr.services.early_payment_discounts import (
    get_discount_service,
    EarlyPaymentDiscount,
    DiscountStatus,
)
from clearledgr.services.approval_chains import (
    get_approval_chain_service,
    ApprovalLevel,
    ApprovalType,
    Approver,
    ApprovalRule,
)
from clearledgr.services.ap_aging import (
    get_ap_aging_service,
    Invoice,
    AgingBucket,
)
from clearledgr.services.vendor_management import (
    get_vendor_management_service,
    VendorType,
    VendorStatus,
    PaymentTerms,
    PaymentMethod,
    BankAccount,
    TaxInfo,
    TaxClassification,
)
from clearledgr.services.audit_trail import (
    get_audit_trail,
    AuditEventType,
)
from clearledgr.services.purchase_orders import (
    get_purchase_order_service,
    POStatus,
    PurchaseOrder,
    POLineItem,
    GoodsReceiptLine,
)
from clearledgr.services.credit_notes import (
    get_credit_note_service,
    CreditType,
    CreditStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap", tags=["ap-workflow"])


# =============================================================================
# REQUEST MODELS
# =============================================================================

class DetectDiscountRequest(BaseModel):
    """Request to detect early payment discount."""
    invoice_id: str
    invoice_text: str
    vendor_name: str = ""
    amount: float = 0.0
    invoice_date: Optional[str] = None
    organization_id: str = "default"


class CaptureDiscountRequest(BaseModel):
    """Request to capture or skip a discount."""
    invoice_id: str
    action: str  # "capture" or "skip"
    reason: str = ""
    organization_id: str = "default"


class SetVendorTermsRequest(BaseModel):
    """Set vendor payment terms for discounts."""
    vendor_pattern: str
    discount_percent: float
    discount_days: int
    net_days: int
    organization_id: str = "default"


class CreateApprovalChainRequest(BaseModel):
    """Create an approval chain for an invoice."""
    invoice_id: str
    vendor_name: str
    amount: float
    gl_code: str = ""
    department: str = ""
    requester_id: Optional[str] = None
    requester_name: Optional[str] = None
    organization_id: str = "default"


class ApproveStepRequest(BaseModel):
    """Approve or reject an approval step."""
    chain_id: str
    user_id: str
    action: str  # "approve" or "reject"
    comments: str = ""
    rejection_reason: str = ""
    organization_id: str = "default"


class RegisterApproverRequest(BaseModel):
    """Register an approver."""
    user_id: str
    email: str
    name: str
    level: str  # level_1, level_2, level_3, level_4
    department: Optional[str] = None
    max_amount: float = 0.0
    organization_id: str = "default"


class SetDelegationRequest(BaseModel):
    """Set approval delegation."""
    from_user_id: str
    to_user_id: str
    organization_id: str = "default"


class LoadInvoicesRequest(BaseModel):
    """Load invoices for aging analysis."""
    invoices: List[Dict[str, Any]]
    organization_id: str = "default"


class CreateVendorRequest(BaseModel):
    """Create a new vendor."""
    name: str
    vendor_type: str = "supplier"
    email: str = ""
    phone: str = ""
    address_line1: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "US"
    default_gl_code: str = ""
    organization_id: str = "default"


class UpdateVendorRequest(BaseModel):
    """Update vendor information."""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    payment_method: Optional[str] = None
    net_days: Optional[int] = None
    auto_pay: Optional[bool] = None


class AddBankAccountRequest(BaseModel):
    """Add bank account to vendor."""
    vendor_id: str
    bank_name: str
    account_type: str = "checking"
    routing_number: str
    account_number: str
    account_name: str
    organization_id: str = "default"


class RecordW9Request(BaseModel):
    """Record W-9 receipt."""
    vendor_id: str
    document_id: str
    received_date: Optional[str] = None
    tax_id: str = ""
    tax_id_type: str = "ein"
    tax_classification: str = "c_corp"
    legal_name: str = ""
    is_1099_eligible: bool = False
    organization_id: str = "default"


# =============================================================================
# EARLY PAYMENT DISCOUNTS
# =============================================================================

@router.post("/discounts/detect")
async def detect_discount(request: DetectDiscountRequest):
    """Detect early payment discount from invoice."""
    service = get_discount_service(request.organization_id)
    
    invoice_date = None
    if request.invoice_date:
        invoice_date = datetime.fromisoformat(request.invoice_date)
    
    discount = service.detect_discount_terms(
        invoice_text=request.invoice_text,
        vendor_name=request.vendor_name,
        invoice_amount=request.amount,
        invoice_id=request.invoice_id,
        invoice_date=invoice_date,
    )
    
    if discount:
        return discount.to_dict()
    return {"detected": False, "message": "No discount terms found"}


@router.get("/discounts/available")
async def get_available_discounts(organization_id: str = "default"):
    """Get all available (uncaptured) discounts."""
    service = get_discount_service(organization_id)
    discounts = service.get_available_discounts()
    return [d.to_dict() for d in discounts]


@router.get("/discounts/expiring")
async def get_expiring_discounts(
    days: int = 3,
    organization_id: str = "default"
):
    """Get discounts expiring within X days."""
    service = get_discount_service(organization_id)
    discounts = service.get_expiring_discounts(days)
    return [d.to_dict() for d in discounts]


@router.post("/discounts/action")
async def discount_action(request: CaptureDiscountRequest):
    """Capture or skip a discount."""
    service = get_discount_service(request.organization_id)
    
    if request.action == "capture":
        discount = service.capture_discount(request.invoice_id)
    elif request.action == "skip":
        discount = service.skip_discount(request.invoice_id, request.reason)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    if discount:
        return discount.to_dict()
    raise HTTPException(status_code=404, detail="Discount not found")


@router.get("/discounts/summary")
async def get_discount_summary(organization_id: str = "default"):
    """Get discount summary statistics."""
    service = get_discount_service(organization_id)
    return service.get_discount_summary()


@router.get("/discounts/recommendations")
async def get_payment_recommendations(organization_id: str = "default"):
    """Get payment priority recommendations based on discounts."""
    service = get_discount_service(organization_id)
    return service.recommend_payment_priority()


@router.post("/discounts/vendor-terms")
async def set_vendor_discount_terms(request: SetVendorTermsRequest):
    """Set vendor-specific discount terms."""
    service = get_discount_service(request.organization_id)
    service.set_vendor_terms(
        vendor_pattern=request.vendor_pattern,
        discount_percent=request.discount_percent,
        discount_days=request.discount_days,
        net_days=request.net_days,
    )
    return {"status": "success", "vendor_pattern": request.vendor_pattern}


# =============================================================================
# MULTI-LEVEL APPROVAL CHAINS
# =============================================================================

@router.post("/approvals/chain/create")
async def create_approval_chain(request: CreateApprovalChainRequest):
    """Create an approval chain for an invoice."""
    service = get_approval_chain_service(request.organization_id)
    
    chain = service.create_approval_chain(
        invoice_id=request.invoice_id,
        vendor_name=request.vendor_name,
        amount=request.amount,
        gl_code=request.gl_code,
        department=request.department,
        requester_id=request.requester_id,
        requester_name=request.requester_name,
    )
    
    return chain.to_dict()


@router.post("/approvals/action")
async def approval_action(request: ApproveStepRequest):
    """Approve or reject an approval step."""
    service = get_approval_chain_service(request.organization_id)
    
    try:
        if request.action == "approve":
            chain = service.approve_step(
                chain_id=request.chain_id,
                user_id=request.user_id,
                comments=request.comments,
            )
        elif request.action == "reject":
            chain = service.reject_step(
                chain_id=request.chain_id,
                user_id=request.user_id,
                reason=request.rejection_reason,
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid action")
        
        return chain.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/approvals/chain/{chain_id}")
async def get_approval_chain(chain_id: str, organization_id: str = "default"):
    """Get approval chain details."""
    service = get_approval_chain_service(organization_id)
    chain = service.get_chain(chain_id)
    
    if not chain:
        raise HTTPException(status_code=404, detail="Chain not found")
    
    return chain.to_dict()


@router.get("/approvals/pending/{user_id}")
async def get_pending_approvals(user_id: str, organization_id: str = "default"):
    """Get pending approvals for a user."""
    service = get_approval_chain_service(organization_id)
    chains = service.get_pending_approvals(user_id)
    return [c.to_dict() for c in chains]


@router.post("/approvals/approver/register")
async def register_approver(request: RegisterApproverRequest):
    """Register an approver."""
    service = get_approval_chain_service(request.organization_id)
    
    level_map = {
        "level_1": ApprovalLevel.LEVEL_1,
        "level_2": ApprovalLevel.LEVEL_2,
        "level_3": ApprovalLevel.LEVEL_3,
        "level_4": ApprovalLevel.LEVEL_4,
    }
    
    level = level_map.get(request.level)
    if not level:
        raise HTTPException(status_code=400, detail="Invalid approval level")
    
    approver = Approver(
        user_id=request.user_id,
        email=request.email,
        name=request.name,
        level=level,
        department=request.department,
        max_amount=request.max_amount,
    )
    
    service.register_approver(approver)
    return {"status": "success", "user_id": request.user_id}


@router.post("/approvals/delegation")
async def set_delegation(request: SetDelegationRequest):
    """Set approval delegation."""
    service = get_approval_chain_service(request.organization_id)
    service.set_delegation(request.from_user_id, request.to_user_id)
    return {"status": "success"}


@router.get("/approvals/summary")
async def get_approval_summary(organization_id: str = "default"):
    """Get approval summary statistics."""
    service = get_approval_chain_service(organization_id)
    return service.get_approval_summary()


# =============================================================================
# AP AGING REPORTS
# =============================================================================

@router.post("/aging/load")
async def load_invoices_for_aging(request: LoadInvoicesRequest):
    """Load invoices for aging analysis."""
    service = get_ap_aging_service(request.organization_id)
    count = service.load_from_queue(request.invoices)
    return {"loaded": count}


@router.get("/aging/summary")
async def get_aging_summary(organization_id: str = "default"):
    """Get AP aging summary report."""
    service = get_ap_aging_service(organization_id)
    return service.get_aging_summary()


@router.get("/aging/by-vendor")
async def get_vendor_aging(
    min_balance: float = 0,
    organization_id: str = "default"
):
    """Get aging summary by vendor."""
    service = get_ap_aging_service(organization_id)
    summaries = service.get_vendor_aging(min_balance)
    return [s.to_dict() for s in summaries]


@router.get("/aging/by-department")
async def get_department_aging(organization_id: str = "default"):
    """Get aging summary by department."""
    service = get_ap_aging_service(organization_id)
    return service.get_department_aging()


@router.get("/aging/by-gl-code")
async def get_gl_aging(organization_id: str = "default"):
    """Get aging summary by GL code."""
    service = get_ap_aging_service(organization_id)
    return service.get_gl_code_aging()


@router.get("/aging/overdue")
async def get_overdue_invoices(
    min_days: int = 1,
    organization_id: str = "default"
):
    """Get overdue invoices."""
    service = get_ap_aging_service(organization_id)
    invoices = service.get_overdue_invoices(min_days)
    return [i.to_dict() for i in invoices]


@router.get("/aging/critical")
async def get_critical_invoices(
    days_threshold: int = 60,
    organization_id: str = "default"
):
    """Get critically overdue invoices."""
    service = get_ap_aging_service(organization_id)
    invoices = service.get_critical_invoices(days_threshold)
    return [i.to_dict() for i in invoices]


@router.get("/aging/export/csv")
async def export_aging_csv(
    include_details: bool = True,
    organization_id: str = "default"
):
    """Export aging report as CSV."""
    service = get_ap_aging_service(organization_id)
    csv_content = service.export_to_csv(include_details)
    return {"format": "csv", "content": csv_content}


@router.get("/aging/export/json")
async def export_aging_json(organization_id: str = "default"):
    """Export aging report as JSON."""
    service = get_ap_aging_service(organization_id)
    return {"format": "json", "content": service.export_to_json()}


# =============================================================================
# VENDOR MANAGEMENT
# =============================================================================

@router.post("/vendors/create")
async def create_vendor(request: CreateVendorRequest):
    """Create a new vendor."""
    service = get_vendor_management_service(request.organization_id)
    
    vendor_type_map = {
        "supplier": VendorType.SUPPLIER,
        "contractor": VendorType.CONTRACTOR,
        "service": VendorType.SERVICE_PROVIDER,
        "utility": VendorType.UTILITY,
        "government": VendorType.GOVERNMENT,
        "employee": VendorType.EMPLOYEE,
        "other": VendorType.OTHER,
    }
    
    vendor = service.create_vendor(
        name=request.name,
        vendor_type=vendor_type_map.get(request.vendor_type, VendorType.SUPPLIER),
        email=request.email,
        phone=request.phone,
        address_line1=request.address_line1,
        city=request.city,
        state=request.state,
        postal_code=request.postal_code,
        country=request.country,
        default_gl_code=request.default_gl_code,
    )
    
    return vendor.to_dict()


@router.get("/vendors/{vendor_id}")
async def get_vendor(vendor_id: str, organization_id: str = "default"):
    """Get vendor by ID."""
    service = get_vendor_management_service(organization_id)
    vendor = service.get_vendor(vendor_id)
    
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    return vendor.to_dict()


@router.put("/vendors/{vendor_id}")
async def update_vendor(vendor_id: str, request: UpdateVendorRequest, organization_id: str = "default"):
    """Update vendor information."""
    service = get_vendor_management_service(organization_id)
    
    updates = {k: v for k, v in request.dict().items() if v is not None}
    vendor = service.update_vendor(vendor_id, updates)
    
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    return vendor.to_dict()


@router.get("/vendors")
async def search_vendors(
    query: str = "",
    status: str = None,
    vendor_type: str = None,
    needs_w9: bool = None,
    needs_1099: bool = None,
    organization_id: str = "default"
):
    """Search vendors."""
    service = get_vendor_management_service(organization_id)
    
    status_enum = VendorStatus(status) if status else None
    type_enum = VendorType(vendor_type) if vendor_type else None
    
    vendors = service.search_vendors(
        query=query,
        status=status_enum,
        vendor_type=type_enum,
        needs_w9=needs_w9,
        needs_1099=needs_1099,
    )
    
    return [v.to_dict() for v in vendors]


@router.post("/vendors/{vendor_id}/activate")
async def activate_vendor(vendor_id: str, organization_id: str = "default"):
    """Activate a vendor."""
    service = get_vendor_management_service(organization_id)
    
    try:
        vendor = service.activate_vendor(vendor_id)
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vendors/{vendor_id}/deactivate")
async def deactivate_vendor(vendor_id: str, reason: str = "", organization_id: str = "default"):
    """Deactivate a vendor."""
    service = get_vendor_management_service(organization_id)
    vendor = service.deactivate_vendor(vendor_id, reason)
    
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    return vendor.to_dict()


@router.post("/vendors/{vendor_id}/block")
async def block_vendor(vendor_id: str, reason: str, organization_id: str = "default"):
    """Block a vendor from payments."""
    service = get_vendor_management_service(organization_id)
    vendor = service.block_vendor(vendor_id, reason)
    
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    return vendor.to_dict()


@router.post("/vendors/bank-account")
async def add_bank_account(request: AddBankAccountRequest):
    """Add bank account to vendor."""
    service = get_vendor_management_service(request.organization_id)
    
    account = BankAccount(
        bank_name=request.bank_name,
        account_type=request.account_type,
        routing_number=request.routing_number,
        account_number=request.account_number,
        account_name=request.account_name,
    )
    
    vendor = service.add_bank_account(request.vendor_id, account)
    
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    return vendor.to_dict()


@router.post("/vendors/{vendor_id}/verify-bank/{account_id}")
async def verify_bank_account(vendor_id: str, account_id: str, organization_id: str = "default"):
    """Verify a bank account."""
    service = get_vendor_management_service(organization_id)
    success = service.verify_bank_account(vendor_id, account_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Vendor or account not found")
    
    return {"status": "verified"}


@router.post("/vendors/w9")
async def record_w9(request: RecordW9Request):
    """Record W-9 receipt for vendor."""
    service = get_vendor_management_service(request.organization_id)
    
    # Update tax info first
    vendor = service.get_vendor(request.vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    tax_class_map = {
        "c_corp": TaxClassification.C_CORPORATION,
        "s_corp": TaxClassification.S_CORPORATION,
        "llc_single": TaxClassification.LLC_SINGLE,
        "llc_partnership": TaxClassification.LLC_PARTNERSHIP,
        "partnership": TaxClassification.PARTNERSHIP,
        "sole_proprietor": TaxClassification.SOLE_PROPRIETOR,
        "individual": TaxClassification.INDIVIDUAL,
    }
    
    vendor.tax_info.tax_id = request.tax_id
    vendor.tax_info.tax_id_type = request.tax_id_type
    vendor.tax_info.tax_classification = tax_class_map.get(
        request.tax_classification, TaxClassification.C_CORPORATION
    )
    vendor.tax_info.legal_name = request.legal_name
    vendor.tax_info.is_1099_eligible = request.is_1099_eligible
    
    received_date = None
    if request.received_date:
        received_date = date.fromisoformat(request.received_date)
    
    vendor = service.record_w9(request.vendor_id, request.document_id, received_date)
    
    return vendor.to_dict()


@router.get("/vendors/1099-report")
async def get_1099_vendors(organization_id: str = "default"):
    """Get vendors that need 1099s."""
    service = get_vendor_management_service(organization_id)
    vendors = service.get_1099_vendors()
    return [v.to_dict() for v in vendors]


@router.get("/vendors/missing-w9")
async def get_missing_w9(organization_id: str = "default"):
    """Get active vendors missing W-9."""
    service = get_vendor_management_service(organization_id)
    vendors = service.get_missing_w9_vendors()
    return [v.to_dict() for v in vendors]


@router.get("/vendors/{vendor_id}/onboarding")
async def get_onboarding_status(vendor_id: str, organization_id: str = "default"):
    """Get vendor onboarding status."""
    service = get_vendor_management_service(organization_id)
    return service.get_onboarding_status(vendor_id)


@router.get("/vendors/summary")
async def get_vendor_summary(organization_id: str = "default"):
    """Get vendor summary statistics."""
    service = get_vendor_management_service(organization_id)
    return service.get_vendor_summary()


# =============================================================================
# AUDIT TRAIL QUERY & EXPORT
# =============================================================================

@router.get("/audit/trail/{invoice_id}")
async def get_audit_trail_by_invoice(invoice_id: str, organization_id: str = "default"):
    """Get complete audit trail for an invoice."""
    service = get_audit_trail(organization_id)
    trail = service.get_trail(invoice_id)
    
    if not trail:
        raise HTTPException(status_code=404, detail="Audit trail not found")
    
    return trail.to_dict()


@router.get("/audit/query")
async def query_audit_trails(
    vendor: Optional[str] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    actor: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    organization_id: str = "default"
):
    """Query audit trails with filters."""
    service = get_audit_trail(organization_id)
    
    event_type_enum = None
    if event_type:
        try:
            event_type_enum = AuditEventType(event_type)
        except ValueError:
            pass
    
    trails = service.query_trails(
        vendor=vendor,
        status=status,
        event_type=event_type_enum,
        actor=actor,
        min_amount=min_amount,
        max_amount=max_amount,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    
    return [t.to_dict() for t in trails]


@router.get("/audit/recent")
async def get_recent_audit_activity(
    limit: int = 50,
    organization_id: str = "default"
):
    """Get recent audit activity across all invoices."""
    service = get_audit_trail(organization_id)
    return service.get_recent_activity(limit)


@router.get("/audit/events/by-type/{event_type}")
async def get_events_by_type(
    event_type: str,
    limit: int = 100,
    organization_id: str = "default"
):
    """Get all events of a specific type."""
    service = get_audit_trail(organization_id)
    
    try:
        event_type_enum = AuditEventType(event_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid event type: {event_type}")
    
    return service.get_events_by_type(event_type_enum, limit)


@router.get("/audit/events/by-actor/{actor}")
async def get_events_by_actor(
    actor: str,
    limit: int = 100,
    organization_id: str = "default"
):
    """Get all events by a specific actor."""
    service = get_audit_trail(organization_id)
    return service.get_events_by_actor(actor, limit)


@router.get("/audit/stats")
async def get_audit_stats(organization_id: str = "default"):
    """Get audit trail summary statistics."""
    service = get_audit_trail(organization_id)
    return service.get_summary_stats()


@router.get("/audit/export/csv")
async def export_audit_csv(
    invoice_ids: Optional[str] = None,
    organization_id: str = "default"
):
    """Export audit trails as CSV."""
    service = get_audit_trail(organization_id)
    
    ids = invoice_ids.split(",") if invoice_ids else None
    csv_content = service.export_to_csv(ids)
    
    return {"format": "csv", "content": csv_content}


@router.get("/audit/export/json")
async def export_audit_json(
    invoice_ids: Optional[str] = None,
    organization_id: str = "default"
):
    """Export audit trails as JSON."""
    service = get_audit_trail(organization_id)
    
    ids = invoice_ids.split(",") if invoice_ids else None
    json_content = service.export_to_json(ids)
    
    return {"format": "json", "content": json_content}


@router.get("/audit/compliance-report")
async def get_compliance_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    organization_id: str = "default"
):
    """Generate compliance report for audit purposes."""
    service = get_audit_trail(organization_id)
    return service.get_compliance_report(start_date, end_date)


# ==================== GL ACCOUNTS MANAGEMENT ====================
# 
# GL accounts are synced from the connected ERP (QuickBooks, Xero, NetSuite, SAP).
# Users select from ERP accounts - manual entry is only for edge cases.
# ================================================================

# In-memory cache for GL accounts (in production, use Redis or database)
_gl_accounts_cache: Dict[str, Dict[str, Any]] = {}


class GLAccountResponse(BaseModel):
    """GL account from ERP."""
    id: str
    code: str
    name: str
    type: str
    subtype: Optional[str] = None
    is_custom: bool = False  # True if manually added (not from ERP)


class GLAccountsResponse(BaseModel):
    """Response with GL accounts list."""
    organization_id: str
    erp_type: Optional[str] = None
    erp_connected: bool = False
    accounts: List[GLAccountResponse]
    last_synced: Optional[str] = None


class CustomGLAccountRequest(BaseModel):
    """Request to add a custom GL account (edge case)."""
    code: str
    name: str
    type: str = "Expense"


@router.get("/gl/accounts", response_model=GLAccountsResponse)
async def get_gl_accounts(organization_id: str = "default"):
    """
    Get GL accounts for the organization.
    
    Returns accounts synced from connected ERP. If no ERP is connected,
    returns any custom accounts that were manually added.
    
    Accounts are cached and auto-synced from ERP on first call.
    """
    # Check cache first
    if organization_id in _gl_accounts_cache:
        cached = _gl_accounts_cache[organization_id]
        return GLAccountsResponse(
            organization_id=organization_id,
            erp_type=cached.get("erp_type"),
            erp_connected=cached.get("erp_connected", False),
            accounts=cached.get("accounts", []),
            last_synced=cached.get("last_synced"),
        )
    
    # Try to fetch from ERP
    try:
        from clearledgr.integrations.erp_router import get_erp_connection
        connection = get_erp_connection(organization_id)
        
        if connection:
            # Fetch from ERP
            accounts = await _sync_gl_accounts_from_erp(organization_id, connection)
            return GLAccountsResponse(
                organization_id=organization_id,
                erp_type=connection.type,
                erp_connected=True,
                accounts=accounts,
                last_synced=datetime.utcnow().isoformat(),
            )
    except Exception as e:
        logger.warning(f"Failed to fetch GL accounts from ERP: {e}")
    
    # No ERP connected - return empty or custom accounts
    custom_accounts = _gl_accounts_cache.get(organization_id, {}).get("custom_accounts", [])
    return GLAccountsResponse(
        organization_id=organization_id,
        erp_type=None,
        erp_connected=False,
        accounts=custom_accounts,
        last_synced=None,
    )


@router.post("/gl/accounts/sync")
async def sync_gl_accounts(organization_id: str = "default"):
    """
    Force sync GL accounts from connected ERP.
    
    Call this after connecting an ERP or when accounts might have changed.
    """
    try:
        from clearledgr.integrations.erp_router import get_erp_connection
        connection = get_erp_connection(organization_id)
        
        if not connection:
            raise HTTPException(status_code=404, detail="No ERP connected. Connect an ERP first.")
        
        accounts = await _sync_gl_accounts_from_erp(organization_id, connection)
        
        return {
            "success": True,
            "organization_id": organization_id,
            "erp_type": connection.type,
            "accounts_synced": len(accounts),
            "synced_at": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GL account sync failed: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@router.post("/gl/accounts/custom")
async def add_custom_gl_account(
    request: CustomGLAccountRequest,
    organization_id: str = "default"
):
    """
    Add a custom GL account (edge case).
    
    Use this only when:
    - ERP is not yet connected
    - A new account was just created in ERP and sync hasn't run
    - Special override needed
    
    Custom accounts are marked as is_custom=True.
    """
    # Initialize cache if needed
    if organization_id not in _gl_accounts_cache:
        _gl_accounts_cache[organization_id] = {
            "accounts": [],
            "custom_accounts": [],
            "erp_type": None,
            "erp_connected": False,
        }
    
    # Check for duplicate
    existing = _gl_accounts_cache[organization_id].get("accounts", [])
    custom = _gl_accounts_cache[organization_id].get("custom_accounts", [])
    
    all_codes = [a.get("code") or a.get("number") for a in existing + custom]
    if request.code in all_codes:
        raise HTTPException(status_code=400, detail=f"Account {request.code} already exists")
    
    # Add custom account
    new_account = GLAccountResponse(
        id=f"custom_{request.code}",
        code=request.code,
        name=request.name,
        type=request.type,
        is_custom=True,
    )
    
    _gl_accounts_cache[organization_id]["custom_accounts"].append(new_account.model_dump())
    
    # Also add to main accounts list for easy access
    _gl_accounts_cache[organization_id]["accounts"].append(new_account.model_dump())
    
    return {
        "success": True,
        "account": new_account.model_dump(),
        "message": "Custom account added. This will be replaced when ERP sync runs.",
    }


@router.delete("/gl/accounts/custom/{code}")
async def delete_custom_gl_account(code: str, organization_id: str = "default"):
    """Delete a custom GL account."""
    if organization_id not in _gl_accounts_cache:
        raise HTTPException(status_code=404, detail="Account not found")
    
    custom = _gl_accounts_cache[organization_id].get("custom_accounts", [])
    updated = [a for a in custom if a.get("code") != code]
    
    if len(updated) == len(custom):
        raise HTTPException(status_code=404, detail="Custom account not found")
    
    _gl_accounts_cache[organization_id]["custom_accounts"] = updated
    
    # Also remove from main accounts
    accounts = _gl_accounts_cache[organization_id].get("accounts", [])
    _gl_accounts_cache[organization_id]["accounts"] = [
        a for a in accounts if a.get("code") != code or not a.get("is_custom")
    ]
    
    return {"success": True, "deleted": code}


async def _sync_gl_accounts_from_erp(organization_id: str, connection) -> List[Dict]:
    """Sync GL accounts from ERP and cache them."""
    from clearledgr.api.erp_connections import (
        _get_quickbooks_accounts,
        _get_xero_accounts,
    )
    
    accounts = []
    
    if connection.type == "quickbooks":
        raw_accounts = await _get_quickbooks_accounts(connection)
        accounts = [
            GLAccountResponse(
                id=str(a.get("id", "")),
                code=a.get("number") or a.get("id", ""),
                name=a.get("name", "Unknown"),
                type=a.get("type", "Expense"),
                subtype=a.get("subtype"),
                is_custom=False,
            ).model_dump()
            for a in raw_accounts
        ]
    elif connection.type == "xero":
        raw_accounts = await _get_xero_accounts(connection)
        accounts = [
            GLAccountResponse(
                id=str(a.get("id", "")),
                code=a.get("number") or a.get("code", ""),
                name=a.get("name", "Unknown"),
                type=a.get("type", "Expense"),
                subtype=a.get("class"),
                is_custom=False,
            ).model_dump()
            for a in raw_accounts
        ]
    elif connection.type == "netsuite":
        from clearledgr.integrations.erp_router import get_netsuite_accounts
        raw_accounts = await get_netsuite_accounts(connection)
        accounts = [
            GLAccountResponse(
                id=str(a.get("id", "")),
                code=a.get("number") or a.get("acctNumber", ""),
                name=a.get("name", "Unknown"),
                type=a.get("type", "Expense"),
                is_custom=False,
            ).model_dump()
            for a in (raw_accounts or [])
        ]
    elif connection.type == "sap":
        from clearledgr.services.erp.sap import SAPAdapter
        sap = SAPAdapter()
        result = sap.list_gl_accounts()
        accounts = [
            GLAccountResponse(
                id=a.gl_account,
                code=a.gl_account,
                name=a.name,
                type="Expense",
                is_custom=False,
            ).model_dump()
            for a in result.accounts
        ]
    
    # Preserve custom accounts
    custom_accounts = _gl_accounts_cache.get(organization_id, {}).get("custom_accounts", [])
    
    # Cache the results
    _gl_accounts_cache[organization_id] = {
        "accounts": accounts + custom_accounts,
        "custom_accounts": custom_accounts,
        "erp_type": connection.type,
        "erp_connected": True,
        "last_synced": datetime.utcnow().isoformat(),
    }
    
    return accounts + custom_accounts


# ==================== INVOICES ENDPOINTS ====================

from clearledgr.core.database import get_db


PENDING_AP_STATUSES = {"pending", "review", "needs_approval", "needs_review", "new", "pending_approval"}


def _normalize_pipeline_invoice(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize invoice_status rows into AP invoice shape used by AP endpoints/UI."""
    raw_status = (invoice.get("status") or "new").strip().lower()
    status_map = {
        "new": "pending",
        "pending_approval": "pending",
        "approved": "approved",
        "posted": "posted",
        "rejected": "rejected",
    }
    status = status_map.get(raw_status, raw_status)
    email_id = invoice.get("gmail_id") or invoice.get("email_id") or invoice.get("id")
    vendor = invoice.get("vendor") or "Unknown"
    amount = invoice.get("amount", 0) or 0
    currency = invoice.get("currency") or "USD"
    created_at = invoice.get("created_at")
    updated_at = invoice.get("updated_at")

    return {
        "id": email_id or invoice.get("id"),
        "invoice_number": invoice.get("invoice_number"),
        "vendor_name": vendor,
        "vendor": vendor,
        "vendor_id": invoice.get("erp_vendor_id"),
        "amount": amount,
        "currency": currency,
        "due_date": invoice.get("due_date"),
        "gl_code": invoice.get("gl_code"),
        "description": invoice.get("email_subject"),
        "status": status,
        "raw_status": raw_status,
        "email_id": email_id,
        "thread_id": invoice.get("thread_id"),
        "po_number": invoice.get("po_number"),
        "confidence": invoice.get("confidence", 0),
        "organization_id": invoice.get("organization_id"),
        "created_at": created_at,
        "updated_at": updated_at,
        "source": "invoice_status",
    }


def _dedupe_and_merge_invoices(ap_rows: List[Dict[str, Any]], pipeline_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge AP table rows + invoice pipeline rows without duplicates."""
    merged: Dict[str, Dict[str, Any]] = {}

    def make_key(inv: Dict[str, Any]) -> str:
        email_id = inv.get("email_id") or inv.get("gmail_id")
        if email_id:
            return f"email:{email_id}"
        inv_id = inv.get("id")
        if inv_id:
            return f"id:{inv_id}"
        invoice_number = inv.get("invoice_number") or ""
        vendor = inv.get("vendor_name") or inv.get("vendor") or ""
        amount = inv.get("amount") or 0
        return f"fallback:{invoice_number}:{vendor}:{amount}"

    for row in pipeline_rows:
        key = make_key(row)
        merged[key] = row

    for row in ap_rows:
        normalized = dict(row)
        vendor = normalized.get("vendor_name") or normalized.get("vendor") or "Unknown"
        normalized["vendor_name"] = vendor
        normalized["vendor"] = vendor
        normalized.setdefault("source", "ap_invoices")
        key = make_key(normalized)
        merged[key] = normalized

    combined = list(merged.values())
    combined.sort(key=lambda inv: (inv.get("updated_at") or inv.get("created_at") or ""), reverse=True)
    return combined


def _pipeline_rows_for_status(pipeline: Dict[str, List[Dict[str, Any]]], status: Optional[str]) -> List[Dict[str, Any]]:
    status_key = (status or "").strip().lower()
    if not status_key:
        return sum(pipeline.values(), [])
    if status_key in {"pending", "review", "needs_approval", "needs_review"}:
        return pipeline.get("new", []) + pipeline.get("pending_approval", [])
    if status_key == "approved":
        return pipeline.get("approved", [])
    if status_key == "posted":
        return pipeline.get("posted", [])
    if status_key == "rejected":
        return pipeline.get("rejected", [])
    if status_key in pipeline:
        return pipeline.get(status_key, [])
    return []


@router.get("/invoices/pending")
async def get_pending_invoices(organization_id: str = "default"):
    """Get list of pending invoices."""
    db = get_db()
    ap_rows = db.get_ap_invoices(organization_id)
    pending_ap = [inv for inv in ap_rows if (inv.get("status") or "").strip().lower() in PENDING_AP_STATUSES]

    pipeline = db.get_invoice_pipeline(organization_id)
    pending_pipeline_rows = _pipeline_rows_for_status(pipeline, "pending")
    pending_pipeline = [_normalize_pipeline_invoice(inv) for inv in pending_pipeline_rows]

    invoices = _dedupe_and_merge_invoices(pending_ap, pending_pipeline)
    return {"invoices": invoices, "count": len(invoices)}


@router.get("/invoices/all")
async def get_all_invoices(
    status: Optional[str] = None,
    limit: int = 100,
    organization_id: str = "default"
):
    """Get all invoices with optional status filter."""
    db = get_db()
    ap_rows = db.get_ap_invoices(organization_id, status=status, limit=limit)
    pipeline = db.get_invoice_pipeline(organization_id)
    pipeline_rows = _pipeline_rows_for_status(pipeline, status)
    normalized_pipeline = [_normalize_pipeline_invoice(inv) for inv in pipeline_rows]
    merged = _dedupe_and_merge_invoices(ap_rows, normalized_pipeline)
    return merged[:limit]


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, organization_id: str = "default"):
    """Get invoice by ID."""
    db = get_db()
    invoice = db.get_ap_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/invoices/create")
async def create_invoice(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    invoice_number: str = Body(""),
    due_date: Optional[str] = Body(None),
    gl_code: str = Body(""),
    description: str = Body(""),
    organization_id: str = Body("default"),
):
    """Create a new invoice record."""
    db = get_db()
    return db.save_ap_invoice(
        vendor_name=vendor_name,
        amount=amount,
        invoice_number=invoice_number,
        due_date=due_date,
        gl_code=gl_code,
        description=description,
        organization_id=organization_id,
    )


@router.put("/invoices/{invoice_id}/status")
async def update_invoice_status(
    invoice_id: str,
    status: str = Body(...),
    organization_id: str = Body("default"),
):
    """Update invoice status."""
    db = get_db()
    success = db.update_ap_invoice_status(invoice_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return db.get_ap_invoice(invoice_id)


# ==================== PAYMENTS ENDPOINTS ====================


@router.get("/payments/summary")
async def get_payments_summary(organization_id: str = "default"):
    """Get payments summary statistics."""
    db = get_db()
    return db.get_ap_payments_summary(organization_id)


@router.get("/payments/pending")
async def get_pending_payments(organization_id: str = "default"):
    """Get list of pending payments."""
    db = get_db()
    all_payments = db.get_ap_payments(organization_id)
    return [p for p in all_payments if p.get("status") in ["pending", "scheduled", "processing"]]


@router.post("/payments/create")
async def create_payment(
    invoice_id: str = Body(...),
    vendor_id: str = Body(...),
    vendor_name: str = Body(...),
    amount: float = Body(...),
    currency: str = Body("USD"),
    method: str = Body("ach"),
    organization_id: str = Body("default"),
):
    """Create a new payment."""
    db = get_db()
    return db.save_ap_payment(
        invoice_id=invoice_id,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        amount=amount,
        currency=currency,
        method=method,
        organization_id=organization_id,
    )


@router.post("/payments/{payment_id}/mark-sent")
async def mark_payment_sent(
    payment_id: str,
    organization_id: str = Body("default"),
):
    """Mark a payment as sent/processing."""
    db = get_db()
    success = db.update_ap_payment(payment_id, status="processing", sent_at=datetime.utcnow().isoformat())
    if not success:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment = db.get_ap_payment(payment_id)
    return {"success": True, "payment": payment}


@router.post("/payments/batch")
async def create_payment_batch(organization_id: str = Body("default")):
    """Create a batch of payments and generate NACHA file."""
    db = get_db()
    all_payments = db.get_ap_payments(organization_id)
    pending = [p for p in all_payments if p.get("status") == "pending"]
    
    batch_id = f"BATCH-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    batch_date = datetime.utcnow().isoformat()
    
    # Generate simple NACHA-like content
    nacha_lines = [
        "101 CLEARLEDGR BATCH FILE",
        f"5220 CLEARLEDGR PAYMENTS BATCH {datetime.utcnow().strftime('%Y%m%d')}",
    ]
    
    for p in pending:
        db.update_ap_payment(p['id'], status="scheduled", batch_id=batch_id, scheduled_date=batch_date)
        nacha_lines.append(f"6220 {p.get('vendor_name', '')[:22]:22} {p.get('amount', 0):>12.2f}")
    
    nacha_lines.append(f"8220 BATCH TOTAL {len(pending)} ENTRIES")
    nacha_lines.append("9999 END OF FILE")
    
    return {
        "batch_id": batch_id,
        "payment_count": len(pending),
        "total_amount": sum(p.get("amount", 0) for p in pending),
        "nacha_file": "\n".join(nacha_lines),
    }


# ==================== GL CORRECTIONS ENDPOINTS ====================


@router.get("/gl/stats")
async def get_gl_stats(organization_id: str = "default"):
    """Get GL correction statistics."""
    db = get_db()
    return db.get_gl_stats(organization_id)


@router.get("/gl/corrections")
async def get_gl_corrections(
    limit: int = 20,
    organization_id: str = "default"
):
    """Get recent GL corrections."""
    db = get_db()
    return db.get_gl_corrections(organization_id, limit=limit)


@router.post("/gl/corrections")
async def record_gl_correction(
    invoice_id: str = Body(...),
    vendor: str = Body(...),
    original_gl: str = Body(...),
    corrected_gl: str = Body(...),
    reason: str = Body(""),
    organization_id: str = Body("default"),
):
    """Record a GL code correction (for learning)."""
    db = get_db()
    correction = db.save_gl_correction(
        invoice_id=invoice_id,
        vendor=vendor,
        original_gl=original_gl,
        corrected_gl=corrected_gl,
        reason=reason,
        organization_id=organization_id,
    )
    
    # Update learning service
    try:
        from clearledgr.services.learning import get_learning_service
        learning = get_learning_service(organization_id)
        learning.learn_gl_code(vendor, corrected_gl, "gl_correction")
    except Exception as e:
        logger.warning(f"Failed to update learning service: {e}")
    
    return correction


# ==================== RECURRING INVOICES ENDPOINTS ====================


@router.get("/recurring/rules")
async def get_recurring_rules(organization_id: str = "default"):
    """Get recurring invoice rules."""
    db = get_db()
    return db.get_recurring_rules(organization_id)


@router.get("/recurring/summary")
async def get_recurring_summary(organization_id: str = "default"):
    """Get recurring invoices summary."""
    db = get_db()
    return db.get_recurring_summary(organization_id)


@router.post("/recurring/rules")
async def create_recurring_rule(
    vendor_name: str = Body(...),
    expected_amount: Optional[float] = Body(None),
    expected_frequency: str = Body("monthly"),
    amount_tolerance_pct: int = Body(5),
    action: str = Body("flag"),
    gl_code: str = Body(""),
    aliases: List[str] = Body([]),
    notes: str = Body(""),
    organization_id: str = Body("default"),
):
    """Create a recurring invoice rule."""
    db = get_db()
    return db.save_recurring_rule(
        vendor_name=vendor_name,
        frequency=expected_frequency,
        expected_amount=expected_amount or 0.0,
        amount_tolerance=amount_tolerance_pct / 100.0,
        gl_code=gl_code,
        auto_approve=(action == "auto_approve"),
        organization_id=organization_id,
    )


@router.delete("/recurring/rules/{rule_id}")
async def delete_recurring_rule(rule_id: str, organization_id: str = "default"):
    """Delete a recurring rule."""
    db = get_db()
    success = db.delete_recurring_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"success": True, "deleted": rule_id}


# ==================== PURCHASE ORDERS & 3-WAY MATCHING ====================

class CreatePORequest(BaseModel):
    """Create a purchase order."""
    vendor_id: str
    vendor_name: str
    requested_by: str
    line_items: List[Dict[str, Any]] = []
    expected_delivery: Optional[str] = None
    department: str = ""
    notes: str = ""
    organization_id: str = "default"


class CreateGoodsReceiptRequest(BaseModel):
    """Create a goods receipt."""
    po_id: str
    received_by: str
    line_items: List[Dict[str, Any]]
    delivery_note: str = ""
    carrier: str = ""
    notes: str = ""
    organization_id: str = "default"


class ThreeWayMatchRequest(BaseModel):
    """Match invoice to PO and GR."""
    invoice_id: str
    invoice_amount: float
    invoice_vendor: str
    invoice_po_number: str = ""
    invoice_lines: List[Dict[str, Any]] = []
    organization_id: str = "default"


class OverrideMatchRequest(BaseModel):
    """Override match exception."""
    match_id: str
    override_by: str
    reason: str
    organization_id: str = "default"


@router.post("/po/create")
async def create_purchase_order(request: CreatePORequest):
    """Create a new purchase order."""
    service = get_purchase_order_service(request.organization_id)
    
    expected_delivery = None
    if request.expected_delivery:
        expected_delivery = date.fromisoformat(request.expected_delivery)
    
    po = service.create_po(
        vendor_id=request.vendor_id,
        vendor_name=request.vendor_name,
        requested_by=request.requested_by,
        line_items=request.line_items,
        expected_delivery=expected_delivery,
        department=request.department,
        notes=request.notes,
    )
    
    return po.to_dict()


@router.post("/po/{po_id}/approve")
async def approve_purchase_order(po_id: str, approved_by: str, organization_id: str = "default"):
    """Approve a purchase order."""
    service = get_purchase_order_service(organization_id)
    
    try:
        po = service.approve_po(po_id, approved_by)
        return po.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/po/summary")
async def get_po_summary(organization_id: str = "default"):
    """Get PO/matching summary."""
    service = get_purchase_order_service(organization_id)
    return service.get_summary()


@router.get("/po/{po_id}")
async def get_purchase_order(po_id: str, organization_id: str = "default"):
    """Get a purchase order."""
    service = get_purchase_order_service(organization_id)
    po = service.get_po(po_id)
    
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    
    return po.to_dict()


@router.get("/po/by-number/{po_number}")
async def get_po_by_number(po_number: str, organization_id: str = "default"):
    """Get a PO by number."""
    service = get_purchase_order_service(organization_id)
    po = service.get_po_by_number(po_number)
    
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    
    return po.to_dict()


@router.get("/po/vendor/{vendor_id}/open")
async def get_open_pos_for_vendor(vendor_id: str, organization_id: str = "default"):
    """Get open POs for a vendor."""
    service = get_purchase_order_service(organization_id)
    pos = service.get_open_pos_for_vendor(vendor_id)
    return [po.to_dict() for po in pos]


@router.post("/gr/create")
async def create_goods_receipt(request: CreateGoodsReceiptRequest):
    """Create a goods receipt."""
    service = get_purchase_order_service(request.organization_id)
    
    try:
        gr = service.create_goods_receipt(
            po_id=request.po_id,
            received_by=request.received_by,
            line_items=request.line_items,
            delivery_note=request.delivery_note,
            carrier=request.carrier,
            notes=request.notes,
        )
        return gr.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/match/3way")
async def three_way_match(request: ThreeWayMatchRequest):
    """Perform 3-way matching: PO + Goods Receipt + Invoice."""
    service = get_purchase_order_service(request.organization_id)
    
    match = service.match_invoice_to_po(
        invoice_id=request.invoice_id,
        invoice_amount=request.invoice_amount,
        invoice_vendor=request.invoice_vendor,
        invoice_po_number=request.invoice_po_number,
        invoice_lines=request.invoice_lines,
    )
    
    return match.to_dict()


@router.post("/match/override")
async def override_match(request: OverrideMatchRequest):
    """Override a match exception with management approval."""
    service = get_purchase_order_service(request.organization_id)
    
    try:
        match = service.override_match_exception(
            match_id=request.match_id,
            override_by=request.override_by,
            reason=request.reason,
        )
        return match.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/match/exceptions")
async def get_match_exceptions(organization_id: str = "default"):
    """Get all match exceptions requiring attention."""
    service = get_purchase_order_service(organization_id)
    exceptions = service.get_match_exceptions()
    return [m.to_dict() for m in exceptions]


# ==================== CREDIT NOTES & DEBIT MEMOS ====================

class CreateCreditNoteRequest(BaseModel):
    """Create a credit note."""
    vendor_id: str
    vendor_name: str
    total_amount: float
    credit_type: str = "credit_note"
    original_invoice_id: str = ""
    reason_code: str = "OTHER"
    line_items: List[Dict[str, Any]] = []
    notes: str = ""
    organization_id: str = "default"


class ApplyCreditRequest(BaseModel):
    """Apply credit to invoice."""
    credit_id: str
    invoice_id: str
    amount: float
    applied_by: str
    organization_id: str = "default"


@router.post("/credit/create")
async def create_credit_note(request: CreateCreditNoteRequest):
    """Create a credit note or debit memo."""
    service = get_credit_note_service(request.organization_id)
    
    credit_type_map = {
        "credit_note": CreditType.CREDIT_NOTE,
        "debit_memo": CreditType.DEBIT_MEMO,
        "return": CreditType.RETURN_CREDIT,
        "rebate": CreditType.REBATE,
        "price_adj": CreditType.PRICE_ADJUSTMENT,
    }
    
    credit = service.create_credit_note(
        vendor_id=request.vendor_id,
        vendor_name=request.vendor_name,
        total_amount=request.total_amount,
        credit_type=credit_type_map.get(request.credit_type, CreditType.CREDIT_NOTE),
        original_invoice_id=request.original_invoice_id,
        reason_code=request.reason_code,
        line_items=request.line_items,
        notes=request.notes,
    )
    
    return credit.to_dict()


@router.get("/credit/summary")
async def get_credit_summary(organization_id: str = "default"):
    """Get credit notes summary."""
    service = get_credit_note_service(organization_id)
    return service.get_summary()


@router.get("/credit/{credit_id}")
async def get_credit_note(credit_id: str, organization_id: str = "default"):
    """Get a credit note by ID."""
    service = get_credit_note_service(organization_id)
    credit = service.get_credit(credit_id)
    
    if not credit:
        raise HTTPException(status_code=404, detail="Credit not found")
    
    return credit.to_dict()


@router.post("/credit/{credit_id}/verify")
async def verify_credit(credit_id: str, verified_by: str, organization_id: str = "default"):
    """Verify a credit note."""
    service = get_credit_note_service(organization_id)
    
    try:
        credit = service.verify_credit(credit_id, verified_by)
        return credit.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/credit/apply")
async def apply_credit(request: ApplyCreditRequest):
    """Apply credit to an invoice."""
    service = get_credit_note_service(request.organization_id)
    
    try:
        application = service.apply_credit_to_invoice(
            credit_id=request.credit_id,
            invoice_id=request.invoice_id,
            amount=request.amount,
            applied_by=request.applied_by,
        )
        return application.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/credit/vendor/{vendor_id}")
async def get_vendor_credits(vendor_id: str, organization_id: str = "default"):
    """Get all credits for a vendor."""
    service = get_credit_note_service(organization_id)
    credits = service.get_credits_for_vendor(vendor_id)
    return [c.to_dict() for c in credits]


@router.get("/credit/vendor/{vendor_id}/balance")
async def get_vendor_credit_balance(vendor_id: str, organization_id: str = "default"):
    """Get credit balance for a vendor."""
    service = get_credit_note_service(organization_id)
    balance = service.get_vendor_credit_balance(vendor_id)
    return {"vendor_id": vendor_id, "credit_balance": balance}


# ==================== EXTRACTION & FOLLOWUP ====================

@router.post("/extract")
async def extract_invoice_data(
    email_id: str = Body(...),
    subject: str = Body(""),
    body: str = Body(""),
    sender: str = Body(""),
    attachments: List[Dict[str, Any]] = Body([]),
    organization_id: str = Body("default"),
):
    """Extract invoice data from email content."""
    # Use the email parser service
    try:
        from clearledgr.services.email_parser import EmailParserService
        parser = EmailParserService()
        
        result = parser.parse_email({
            "id": email_id,
            "subject": subject,
            "body": body,
            "sender": sender,
            "attachments": attachments,
        })
        
        return {
            "email_id": email_id,
            "extracted": result,
            "confidence": result.get("confidence", 0.0),
        }
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {
            "email_id": email_id,
            "extracted": {},
            "error": str(e),
        }


@router.post("/followup/create")
async def create_followup(
    email_id: str = Body(...),
    thread_id: str = Body(""),
    vendor_name: str = Body(""),
    invoice_number: str = Body(""),
    amount: float = Body(0.0),
    followup_type: str = Body("reminder"),
    message: str = Body(""),
    scheduled_date: Optional[str] = Body(None),
    organization_id: str = Body("default"),
):
    """Create a followup reminder for an invoice."""
    followup = {
        "followup_id": f"FU-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "email_id": email_id,
        "thread_id": thread_id,
        "vendor_name": vendor_name,
        "invoice_number": invoice_number,
        "amount": amount,
        "followup_type": followup_type,
        "message": message,
        "scheduled_date": scheduled_date,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    
    return followup


# ==================== ERP SYNC TRACKING ====================


@router.get("/erp-sync/by-thread/{thread_id}")
async def get_erp_sync_status(thread_id: str, organization_id: str = "default"):
    """Get ERP sync status for a thread."""
    db = get_db()
    return db.get_erp_sync_by_thread(thread_id)


@router.post("/erp-sync/track")
async def track_erp_sync(
    thread_id: str = Body(...),
    email_id: str = Body(""),
    erp_type: str = Body(...),
    erp_id: str = Body(...),
    erp_status: str = Body("synced"),
    organization_id: str = Body("default"),
):
    """Track ERP sync status for a thread/email."""
    db = get_db()
    return db.save_erp_sync_tracking(
        thread_id=thread_id,
        email_id=email_id,
        erp_type=erp_type,
        erp_id=erp_id,
        erp_status=erp_status,
        synced=True,
        organization_id=organization_id,
    )


# ==================== RECURRING UPCOMING ====================

@router.get("/recurring/upcoming")
async def get_upcoming_recurring(days: int = 30, organization_id: str = "default"):
    """Get upcoming recurring invoices within X days."""
    db = get_db()
    rules = db.get_recurring_rules(organization_id)
    
    # Calculate upcoming based on frequency
    upcoming = []
    for rule in rules:
        upcoming.append({
            "rule_id": rule.get("id"),
            "vendor_name": rule.get("vendor_name"),
            "expected_amount": rule.get("expected_amount"),
            "frequency": rule.get("frequency", "monthly"),
            "gl_code": rule.get("gl_code"),
            "next_expected": None,  # Would calculate based on last_matched_at
        })
    
    return upcoming
