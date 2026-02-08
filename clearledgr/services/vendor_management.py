"""
Vendor Management Service

Comprehensive vendor management including:
- Vendor master data
- Payment terms management
- 1099/W-9 tracking
- Vendor onboarding workflow
- Vendor performance metrics
"""

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid
import re

logger = logging.getLogger(__name__)


class VendorStatus(Enum):
    """Vendor status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"        # Pending onboarding
    BLOCKED = "blocked"        # Blocked from payments
    ARCHIVED = "archived"


class VendorType(Enum):
    """Type of vendor."""
    SUPPLIER = "supplier"
    CONTRACTOR = "contractor"
    SERVICE_PROVIDER = "service"
    UTILITY = "utility"
    GOVERNMENT = "government"
    EMPLOYEE = "employee"       # For reimbursements
    OTHER = "other"


class PaymentMethod(Enum):
    """Preferred payment method."""
    ACH = "ach"
    WIRE = "wire"
    CHECK = "check"
    VIRTUAL_CARD = "virtual_card"
    CREDIT_CARD = "credit_card"


class TaxClassification(Enum):
    """IRS tax classification."""
    INDIVIDUAL = "individual"
    SOLE_PROPRIETOR = "sole_proprietor"
    LLC_SINGLE = "llc_single"
    LLC_PARTNERSHIP = "llc_partnership"
    LLC_CORPORATION = "llc_corporation"
    C_CORPORATION = "c_corp"
    S_CORPORATION = "s_corp"
    PARTNERSHIP = "partnership"
    TRUST = "trust"
    EXEMPT = "exempt"
    GOVERNMENT = "government"
    FOREIGN = "foreign"


@dataclass
class PaymentTerms:
    """Vendor payment terms."""
    net_days: int = 30                       # Standard payment due in X days
    discount_percent: float = 0.0            # Early payment discount %
    discount_days: int = 0                   # Days to get discount
    payment_method: PaymentMethod = PaymentMethod.ACH
    payment_frequency: str = "per_invoice"   # per_invoice, weekly, monthly
    auto_pay: bool = False                   # Auto-pay approved invoices
    hold_payments: bool = False              # Hold all payments
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "net_days": self.net_days,
            "discount_percent": self.discount_percent,
            "discount_days": self.discount_days,
            "payment_method": self.payment_method.value,
            "payment_frequency": self.payment_frequency,
            "auto_pay": self.auto_pay,
            "hold_payments": self.hold_payments,
        }


@dataclass
class BankAccount:
    """Vendor bank account for payments."""
    account_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    bank_name: str = ""
    account_type: str = "checking"  # checking, savings
    routing_number: str = ""
    account_number: str = ""        # Stored encrypted in production
    account_name: str = ""          # Name on account
    is_verified: bool = False
    verified_at: Optional[datetime] = None
    is_primary: bool = True
    
    def masked_account(self) -> str:
        """Return masked account number."""
        if len(self.account_number) > 4:
            return "****" + self.account_number[-4:]
        return "****"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "bank_name": self.bank_name,
            "account_type": self.account_type,
            "routing_number": self.routing_number[:4] + "****" if self.routing_number else "",
            "account_number_masked": self.masked_account(),
            "account_name": self.account_name,
            "is_verified": self.is_verified,
            "is_primary": self.is_primary,
        }


@dataclass
class TaxInfo:
    """Vendor tax information (W-9/1099)."""
    tax_id: str = ""                # EIN or SSN (encrypted in production)
    tax_id_type: str = "ein"        # ein, ssn
    tax_classification: TaxClassification = TaxClassification.C_CORPORATION
    legal_name: str = ""
    dba_name: str = ""
    
    # W-9 Information
    w9_on_file: bool = False
    w9_received_date: Optional[date] = None
    w9_expiry_date: Optional[date] = None
    w9_document_id: str = ""
    
    # 1099 Reporting
    is_1099_eligible: bool = False
    ytd_payments: float = 0.0
    prior_year_1099_sent: bool = False
    prior_year_1099_amount: float = 0.0
    
    # Backup withholding
    backup_withholding: bool = False
    withholding_rate: float = 0.24   # 24% default federal rate
    
    def needs_1099(self, threshold: float = 600) -> bool:
        """Check if vendor needs 1099 based on YTD payments."""
        if not self.is_1099_eligible:
            return False
        # Corporations typically don't get 1099s
        if self.tax_classification in [
            TaxClassification.C_CORPORATION,
            TaxClassification.S_CORPORATION,
        ]:
            return False
        return self.ytd_payments >= threshold
    
    def masked_tax_id(self) -> str:
        """Return masked tax ID."""
        if len(self.tax_id) >= 4:
            if self.tax_id_type == "ssn":
                return "***-**-" + self.tax_id[-4:]
            return "**-***" + self.tax_id[-4:]
        return "****"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tax_id_masked": self.masked_tax_id(),
            "tax_id_type": self.tax_id_type,
            "tax_classification": self.tax_classification.value,
            "legal_name": self.legal_name,
            "dba_name": self.dba_name,
            "w9_on_file": self.w9_on_file,
            "w9_received_date": self.w9_received_date.isoformat() if self.w9_received_date else None,
            "is_1099_eligible": self.is_1099_eligible,
            "ytd_payments": self.ytd_payments,
            "needs_1099": self.needs_1099(),
            "backup_withholding": self.backup_withholding,
        }


@dataclass
class Vendor:
    """Complete vendor record."""
    vendor_id: str = field(default_factory=lambda: f"VND-{uuid.uuid4().hex[:8].upper()}")
    
    # Basic info
    name: str = ""
    display_name: str = ""
    vendor_type: VendorType = VendorType.SUPPLIER
    status: VendorStatus = VendorStatus.ACTIVE
    
    # Contact info
    email: str = ""
    phone: str = ""
    website: str = ""
    
    # Address
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "US"
    
    # Financial
    payment_terms: PaymentTerms = field(default_factory=PaymentTerms)
    bank_accounts: List[BankAccount] = field(default_factory=list)
    default_gl_code: str = ""
    default_cost_center: str = ""
    currency: str = "USD"
    credit_limit: float = 0.0
    
    # Tax
    tax_info: TaxInfo = field(default_factory=TaxInfo)
    
    # Aliases (for matching)
    aliases: List[str] = field(default_factory=list)
    email_domains: List[str] = field(default_factory=list)
    
    # ERP Integration
    erp_vendor_id: str = ""
    quickbooks_id: str = ""
    netsuite_id: str = ""
    xero_id: str = ""
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    organization_id: str = "default"
    
    # Statistics
    total_invoices: int = 0
    total_paid: float = 0.0
    average_days_to_pay: float = 0.0
    last_invoice_date: Optional[datetime] = None
    last_payment_date: Optional[datetime] = None
    
    def get_primary_bank_account(self) -> Optional[BankAccount]:
        """Get primary bank account."""
        for account in self.bank_accounts:
            if account.is_primary:
                return account
        return self.bank_accounts[0] if self.bank_accounts else None
    
    def matches_name(self, name: str) -> bool:
        """Check if vendor matches a name (including aliases)."""
        name_lower = name.lower()
        
        if name_lower in self.name.lower():
            return True
        if self.display_name and name_lower in self.display_name.lower():
            return True
        
        for alias in self.aliases:
            if name_lower in alias.lower() or alias.lower() in name_lower:
                return True
        
        return False
    
    def matches_email(self, email: str) -> bool:
        """Check if vendor matches an email address."""
        if not email:
            return False
        
        email_lower = email.lower()
        
        if self.email and email_lower == self.email.lower():
            return True
        
        # Check email domain
        domain = email_lower.split("@")[-1] if "@" in email_lower else ""
        if domain in self.email_domains:
            return True
        
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_id": self.vendor_id,
            "name": self.name,
            "display_name": self.display_name or self.name,
            "vendor_type": self.vendor_type.value,
            "status": self.status.value,
            "email": self.email,
            "phone": self.phone,
            "website": self.website,
            "address": {
                "line1": self.address_line1,
                "line2": self.address_line2,
                "city": self.city,
                "state": self.state,
                "postal_code": self.postal_code,
                "country": self.country,
            },
            "payment_terms": self.payment_terms.to_dict(),
            "bank_accounts": [b.to_dict() for b in self.bank_accounts],
            "default_gl_code": self.default_gl_code,
            "currency": self.currency,
            "tax_info": self.tax_info.to_dict(),
            "aliases": self.aliases,
            "erp_ids": {
                "quickbooks": self.quickbooks_id,
                "netsuite": self.netsuite_id,
                "xero": self.xero_id,
            },
            "statistics": {
                "total_invoices": self.total_invoices,
                "total_paid": self.total_paid,
                "average_days_to_pay": self.average_days_to_pay,
            },
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "tags": self.tags,
        }


@dataclass
class OnboardingTask:
    """Task in vendor onboarding workflow."""
    task_id: str
    name: str
    required: bool = True
    completed: bool = False
    completed_at: Optional[datetime] = None
    completed_by: str = ""
    notes: str = ""


class VendorManagementService:
    """
    Service for managing vendors.
    """
    
    # Standard onboarding tasks
    ONBOARDING_TASKS = [
        ("collect_w9", "Collect W-9 Form", True),
        ("verify_bank", "Verify Bank Account", True),
        ("approve_terms", "Approve Payment Terms", True),
        ("setup_erp", "Create in ERP System", True),
        ("send_welcome", "Send Welcome Communication", False),
    ]
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._vendors: Dict[str, Vendor] = {}
        self._onboarding: Dict[str, List[OnboardingTask]] = {}
    
    def create_vendor(
        self,
        name: str,
        vendor_type: VendorType = VendorType.SUPPLIER,
        email: str = "",
        created_by: str = "",
        **kwargs
    ) -> Vendor:
        """Create a new vendor."""
        vendor = Vendor(
            name=name,
            vendor_type=vendor_type,
            email=email,
            status=VendorStatus.PENDING,
            created_by=created_by,
            organization_id=self.organization_id,
            **kwargs
        )
        
        # Auto-populate email domains
        if email and "@" in email:
            domain = email.split("@")[-1].lower()
            if domain not in ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"]:
                vendor.email_domains.append(domain)
        
        self._vendors[vendor.vendor_id] = vendor
        
        # Initialize onboarding tasks
        self._init_onboarding(vendor.vendor_id)
        
        logger.info(f"Created vendor: {vendor.vendor_id} - {name}")
        return vendor
    
    def _init_onboarding(self, vendor_id: str):
        """Initialize onboarding workflow for a vendor."""
        tasks = []
        for task_id, name, required in self.ONBOARDING_TASKS:
            tasks.append(OnboardingTask(
                task_id=task_id,
                name=name,
                required=required,
            ))
        self._onboarding[vendor_id] = tasks
    
    def update_vendor(self, vendor_id: str, updates: Dict[str, Any]) -> Optional[Vendor]:
        """Update vendor information."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        for key, value in updates.items():
            if hasattr(vendor, key):
                setattr(vendor, key, value)
        
        vendor.updated_at = datetime.now()
        logger.info(f"Updated vendor: {vendor_id}")
        return vendor
    
    def get_vendor(self, vendor_id: str) -> Optional[Vendor]:
        """Get vendor by ID."""
        return self._vendors.get(vendor_id)
    
    def find_vendor_by_name(self, name: str) -> Optional[Vendor]:
        """Find vendor by name or alias."""
        for vendor in self._vendors.values():
            if vendor.matches_name(name):
                return vendor
        return None
    
    def find_vendor_by_email(self, email: str) -> Optional[Vendor]:
        """Find vendor by email or email domain."""
        for vendor in self._vendors.values():
            if vendor.matches_email(email):
                return vendor
        return None
    
    def search_vendors(
        self,
        query: str = "",
        status: VendorStatus = None,
        vendor_type: VendorType = None,
        needs_w9: bool = None,
        needs_1099: bool = None,
    ) -> List[Vendor]:
        """Search vendors with filters."""
        results = list(self._vendors.values())
        
        if query:
            query_lower = query.lower()
            results = [v for v in results if 
                query_lower in v.name.lower() or
                query_lower in (v.display_name or "").lower() or
                query_lower in v.email.lower()
            ]
        
        if status:
            results = [v for v in results if v.status == status]
        
        if vendor_type:
            results = [v for v in results if v.vendor_type == vendor_type]
        
        if needs_w9 is not None:
            results = [v for v in results if (not v.tax_info.w9_on_file) == needs_w9]
        
        if needs_1099 is not None:
            results = [v for v in results if v.tax_info.needs_1099() == needs_1099]
        
        return results
    
    def activate_vendor(self, vendor_id: str) -> Optional[Vendor]:
        """Activate a vendor (mark as ready for payments)."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        # Check required onboarding tasks
        tasks = self._onboarding.get(vendor_id, [])
        incomplete = [t for t in tasks if t.required and not t.completed]
        
        if incomplete:
            raise ValueError(f"Cannot activate: {len(incomplete)} required tasks incomplete")
        
        vendor.status = VendorStatus.ACTIVE
        vendor.updated_at = datetime.now()
        logger.info(f"Activated vendor: {vendor_id}")
        return vendor
    
    def deactivate_vendor(self, vendor_id: str, reason: str = "") -> Optional[Vendor]:
        """Deactivate a vendor."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        vendor.status = VendorStatus.INACTIVE
        vendor.notes = f"{vendor.notes}\nDeactivated: {reason}" if vendor.notes else f"Deactivated: {reason}"
        vendor.updated_at = datetime.now()
        logger.info(f"Deactivated vendor: {vendor_id} - {reason}")
        return vendor
    
    def block_vendor(self, vendor_id: str, reason: str) -> Optional[Vendor]:
        """Block a vendor from payments."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        vendor.status = VendorStatus.BLOCKED
        vendor.payment_terms.hold_payments = True
        vendor.notes = f"{vendor.notes}\nBlocked: {reason}" if vendor.notes else f"Blocked: {reason}"
        vendor.updated_at = datetime.now()
        logger.info(f"Blocked vendor: {vendor_id} - {reason}")
        return vendor
    
    # =========================================================================
    # Bank Account Management
    # =========================================================================
    
    def add_bank_account(self, vendor_id: str, account: BankAccount) -> Optional[Vendor]:
        """Add bank account to vendor."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        # If this is the first account or marked primary, set as primary
        if not vendor.bank_accounts or account.is_primary:
            for existing in vendor.bank_accounts:
                existing.is_primary = False
            account.is_primary = True
        
        vendor.bank_accounts.append(account)
        vendor.updated_at = datetime.now()
        logger.info(f"Added bank account to vendor: {vendor_id}")
        return vendor
    
    def verify_bank_account(self, vendor_id: str, account_id: str) -> bool:
        """Mark bank account as verified."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return False
        
        for account in vendor.bank_accounts:
            if account.account_id == account_id:
                account.is_verified = True
                account.verified_at = datetime.now()
                logger.info(f"Verified bank account for vendor: {vendor_id}")
                return True
        
        return False
    
    # =========================================================================
    # Tax Information (W-9/1099)
    # =========================================================================
    
    def update_tax_info(self, vendor_id: str, tax_info: TaxInfo) -> Optional[Vendor]:
        """Update vendor tax information."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        vendor.tax_info = tax_info
        vendor.updated_at = datetime.now()
        logger.info(f"Updated tax info for vendor: {vendor_id}")
        return vendor
    
    def record_w9(
        self,
        vendor_id: str,
        document_id: str,
        received_date: date = None,
    ) -> Optional[Vendor]:
        """Record W-9 receipt."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return None
        
        vendor.tax_info.w9_on_file = True
        vendor.tax_info.w9_received_date = received_date or date.today()
        vendor.tax_info.w9_document_id = document_id
        vendor.updated_at = datetime.now()
        
        # Complete onboarding task
        self.complete_onboarding_task(vendor_id, "collect_w9")
        
        logger.info(f"Recorded W-9 for vendor: {vendor_id}")
        return vendor
    
    def record_payment(self, vendor_id: str, amount: float):
        """Record a payment for 1099 tracking."""
        vendor = self._vendors.get(vendor_id)
        if not vendor:
            return
        
        vendor.tax_info.ytd_payments += amount
        vendor.total_paid += amount
        vendor.total_invoices += 1
        vendor.last_payment_date = datetime.now()
        vendor.updated_at = datetime.now()
    
    def get_1099_vendors(self, year: int = None) -> List[Vendor]:
        """Get vendors that need 1099s."""
        return [v for v in self._vendors.values() if v.tax_info.needs_1099()]
    
    def get_missing_w9_vendors(self) -> List[Vendor]:
        """Get active vendors missing W-9."""
        return [
            v for v in self._vendors.values()
            if v.status == VendorStatus.ACTIVE and 
               v.tax_info.is_1099_eligible and
               not v.tax_info.w9_on_file
        ]
    
    # =========================================================================
    # Onboarding Workflow
    # =========================================================================
    
    def get_onboarding_status(self, vendor_id: str) -> Dict[str, Any]:
        """Get onboarding status for a vendor."""
        tasks = self._onboarding.get(vendor_id, [])
        completed = sum(1 for t in tasks if t.completed)
        required_completed = sum(1 for t in tasks if t.required and t.completed)
        required_total = sum(1 for t in tasks if t.required)
        
        return {
            "vendor_id": vendor_id,
            "total_tasks": len(tasks),
            "completed_tasks": completed,
            "required_completed": required_completed,
            "required_total": required_total,
            "is_ready": required_completed >= required_total,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "name": t.name,
                    "required": t.required,
                    "completed": t.completed,
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                }
                for t in tasks
            ],
        }
    
    def complete_onboarding_task(
        self,
        vendor_id: str,
        task_id: str,
        completed_by: str = "",
        notes: str = "",
    ) -> bool:
        """Mark an onboarding task as complete."""
        tasks = self._onboarding.get(vendor_id, [])
        for task in tasks:
            if task.task_id == task_id:
                task.completed = True
                task.completed_at = datetime.now()
                task.completed_by = completed_by
                task.notes = notes
                logger.info(f"Completed onboarding task {task_id} for vendor {vendor_id}")
                return True
        return False
    
    # =========================================================================
    # Statistics and Reporting
    # =========================================================================
    
    def get_vendor_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        vendors = list(self._vendors.values())
        
        return {
            "total_vendors": len(vendors),
            "by_status": {
                status.value: len([v for v in vendors if v.status == status])
                for status in VendorStatus
            },
            "by_type": {
                vtype.value: len([v for v in vendors if v.vendor_type == vtype])
                for vtype in VendorType
            },
            "missing_w9": len(self.get_missing_w9_vendors()),
            "needs_1099": len(self.get_1099_vendors()),
            "total_ytd_payments": sum(v.tax_info.ytd_payments for v in vendors),
            "pending_onboarding": len([
                v for v in vendors if v.status == VendorStatus.PENDING
            ]),
        }
    
    def get_all_vendors(self) -> List[Vendor]:
        """Get all vendors."""
        return list(self._vendors.values())


# Singleton instance cache
_instances: Dict[str, VendorManagementService] = {}


def get_vendor_management_service(organization_id: str = "default") -> VendorManagementService:
    """Get or create vendor management service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = VendorManagementService(organization_id)
    return _instances[organization_id]
