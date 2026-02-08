"""
GL Code Correction Service

Allows users to correct GL codes on invoices and teaches the system
to apply better mappings in the future.

The correction → learning loop:
1. User sees suggested GL code
2. User corrects it in sidebar
3. System records correction
4. Learning service uses corrections to improve future suggestions
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import uuid

from clearledgr.core.database import get_db
from clearledgr.services.correction_learning import get_correction_learning, CorrectionType

logger = logging.getLogger(__name__)


@dataclass
class GLCorrection:
    """A GL code correction."""
    correction_id: str
    invoice_id: str
    vendor: str
    
    # The correction
    original_gl: str
    original_gl_description: str
    corrected_gl: str
    corrected_gl_description: str
    
    # Context
    amount: Optional[float] = None
    category: Optional[str] = None
    reason: Optional[str] = None
    
    # Metadata
    corrected_by: str = "user"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    applied_to_invoice: bool = False
    learned: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "invoice_id": self.invoice_id,
            "vendor": self.vendor,
            "original_gl": self.original_gl,
            "original_gl_description": self.original_gl_description,
            "corrected_gl": self.corrected_gl,
            "corrected_gl_description": self.corrected_gl_description,
            "amount": self.amount,
            "category": self.category,
            "reason": self.reason,
            "corrected_by": self.corrected_by,
            "timestamp": self.timestamp,
            "applied_to_invoice": self.applied_to_invoice,
            "learned": self.learned,
        }


@dataclass
class GLAccount:
    """A GL account in the chart of accounts."""
    code: str
    name: str
    account_type: str  # expense, asset, liability, equity, revenue
    category: Optional[str] = None
    parent_code: Optional[str] = None
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "account_type": self.account_type,
            "category": self.category,
            "parent_code": self.parent_code,
            "is_active": self.is_active,
        }


# Default chart of accounts for demo
DEFAULT_GL_ACCOUNTS = [
    GLAccount("5000", "Operating Expenses", "expense", "Operations"),
    GLAccount("5100", "Office Supplies", "expense", "Operations"),
    GLAccount("5200", "Software & Subscriptions", "expense", "Technology"),
    GLAccount("5210", "Cloud Infrastructure", "expense", "Technology"),
    GLAccount("5220", "SaaS Tools", "expense", "Technology"),
    GLAccount("5300", "Professional Services", "expense", "Professional"),
    GLAccount("5310", "Legal Fees", "expense", "Professional"),
    GLAccount("5320", "Accounting Fees", "expense", "Professional"),
    GLAccount("5330", "Consulting Fees", "expense", "Professional"),
    GLAccount("5400", "Marketing & Advertising", "expense", "Marketing"),
    GLAccount("5410", "Digital Advertising", "expense", "Marketing"),
    GLAccount("5420", "Events & Sponsorships", "expense", "Marketing"),
    GLAccount("5500", "Travel & Entertainment", "expense", "T&E"),
    GLAccount("5510", "Airfare", "expense", "T&E"),
    GLAccount("5520", "Hotels", "expense", "T&E"),
    GLAccount("5530", "Meals", "expense", "T&E"),
    GLAccount("5600", "Utilities", "expense", "Facilities"),
    GLAccount("5700", "Rent & Occupancy", "expense", "Facilities"),
    GLAccount("5800", "Insurance", "expense", "Risk"),
    GLAccount("5900", "Depreciation", "expense", "Non-Cash"),
    GLAccount("6000", "Cost of Goods Sold", "expense", "COGS"),
    GLAccount("6100", "Contractor Payments", "expense", "Payroll"),
    GLAccount("6200", "Employee Benefits", "expense", "Payroll"),
    GLAccount("6250", "Payment Processing Fees", "expense", "Operations"),
    GLAccount("7000", "Other Expenses", "expense", "Other"),
]


class GLCorrectionService:
    """
    Manage GL code corrections with learning feedback.
    
    Features:
    - Accept corrections from UI
    - Feed corrections to learning service
    - Track correction history
    - Provide GL account suggestions
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self._corrections: Dict[str, GLCorrection] = {}
        self._gl_accounts: List[GLAccount] = list(DEFAULT_GL_ACCOUNTS)
        self._load_custom_accounts()
    
    def _load_custom_accounts(self):
        """Load any custom GL accounts from database."""
        try:
            custom = self.db.get_gl_accounts(self.organization_id)
            if custom:
                for acc in custom:
                    self._gl_accounts.append(GLAccount(**acc))
        except Exception:
            pass
    
    def correct_gl_code(
        self,
        invoice_id: str,
        vendor: str,
        original_gl: str,
        corrected_gl: str,
        corrected_by: str = "user",
        amount: Optional[float] = None,
        category: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> GLCorrection:
        """
        Record a GL code correction and feed to learning.
        
        Args:
            invoice_id: The invoice being corrected
            vendor: Vendor name (for learning)
            original_gl: The GL code that was suggested/wrong
            corrected_gl: The correct GL code
            corrected_by: User who made the correction
            amount: Invoice amount (for context)
            category: Category if known
            reason: Optional reason for correction
        
        Returns:
            GLCorrection object
        """
        correction_id = f"GLC-{uuid.uuid4().hex[:8]}"
        
        # Get descriptions
        original_desc = self._get_gl_description(original_gl)
        corrected_desc = self._get_gl_description(corrected_gl)
        
        correction = GLCorrection(
            correction_id=correction_id,
            invoice_id=invoice_id,
            vendor=vendor,
            original_gl=original_gl,
            original_gl_description=original_desc,
            corrected_gl=corrected_gl,
            corrected_gl_description=corrected_desc,
            amount=amount,
            category=category,
            reason=reason,
            corrected_by=corrected_by,
        )
        
        self._corrections[correction_id] = correction
        
        # Feed to learning service
        try:
            learning = get_correction_learning(self.organization_id)
            learning.record_correction(
                correction_type=CorrectionType.GL_CODE.value,
                original_value=original_gl,
                corrected_value=corrected_gl,
                context={
                    "vendor": vendor,
                    "amount": amount,
                    "category": category,
                    "original_description": original_desc,
                    "corrected_description": corrected_desc,
                },
                user_id=corrected_by,
            )
            correction.learned = True
            logger.info(f"GL correction recorded and learned: {vendor} {original_gl} → {corrected_gl}")
        except Exception as e:
            logger.warning(f"Failed to record GL correction for learning: {e}")
        
        # Save to database
        self._save_correction(correction)
        
        return correction
    
    def get_gl_accounts(
        self,
        account_type: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[GLAccount]:
        """
        Get available GL accounts with optional filtering.
        
        Args:
            account_type: Filter by type (expense, asset, etc.)
            category: Filter by category
            search: Search in code or name
        
        Returns:
            List of matching GL accounts
        """
        accounts = [a for a in self._gl_accounts if a.is_active]
        
        if account_type:
            accounts = [a for a in accounts if a.account_type == account_type]
        
        if category:
            accounts = [a for a in accounts if a.category == category]
        
        if search:
            search_lower = search.lower()
            accounts = [
                a for a in accounts
                if search_lower in a.code.lower() or search_lower in a.name.lower()
            ]
        
        return sorted(accounts, key=lambda a: a.code)
    
    def get_suggested_gl(
        self,
        vendor: str,
        amount: Optional[float] = None,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a suggested GL code based on vendor and context.
        Uses learning service if available.
        """
        try:
            from clearledgr.services.learning import get_learning_service
            learning = get_learning_service(self.organization_id)
            suggestion = learning.suggest_gl_code(vendor=vendor, amount=amount)
            if suggestion and suggestion.get("confidence", 0) > 0.5:
                return {
                    "gl_code": suggestion["gl_code"],
                    "gl_description": self._get_gl_description(suggestion["gl_code"]),
                    "confidence": suggestion["confidence"],
                    "source": "learning",
                }
        except Exception:
            pass
        
        # Fallback to vendor intelligence
        try:
            from clearledgr.services.vendor_intelligence import get_vendor_intelligence
            vi = get_vendor_intelligence()
            vendor_info = vi.get_suggestion(vendor)
            if vendor_info and vendor_info.get("suggested_gl"):
                return {
                    "gl_code": vendor_info["suggested_gl"],
                    "gl_description": vendor_info.get("gl_description", ""),
                    "confidence": 0.7,
                    "source": "vendor_intelligence",
                }
        except Exception:
            pass
        
        # Default to general operating expenses
        return {
            "gl_code": "5000",
            "gl_description": "Operating Expenses",
            "confidence": 0.3,
            "source": "default",
        }
    
    def get_recent_corrections(
        self,
        vendor: Optional[str] = None,
        limit: int = 20,
    ) -> List[GLCorrection]:
        """Get recent GL corrections."""
        corrections = list(self._corrections.values())
        
        if vendor:
            vendor_lower = vendor.lower()
            corrections = [c for c in corrections if vendor_lower in c.vendor.lower()]
        
        # Sort by timestamp descending
        corrections.sort(key=lambda c: c.timestamp, reverse=True)
        
        return corrections[:limit]
    
    def get_correction_stats(self) -> Dict[str, Any]:
        """Get statistics about GL corrections."""
        corrections = list(self._corrections.values())
        
        if not corrections:
            return {
                "total_corrections": 0,
                "unique_vendors": 0,
                "top_corrected_gl": [],
                "learned_count": 0,
            }
        
        # Count corrections by GL
        from_gl_counts: Dict[str, int] = {}
        to_gl_counts: Dict[str, int] = {}
        vendors = set()
        
        for c in corrections:
            vendors.add(c.vendor)
            from_gl_counts[c.original_gl] = from_gl_counts.get(c.original_gl, 0) + 1
            to_gl_counts[c.corrected_gl] = to_gl_counts.get(c.corrected_gl, 0) + 1
        
        return {
            "total_corrections": len(corrections),
            "unique_vendors": len(vendors),
            "top_corrected_from": sorted(
                from_gl_counts.items(), key=lambda x: x[1], reverse=True
            )[:5],
            "top_corrected_to": sorted(
                to_gl_counts.items(), key=lambda x: x[1], reverse=True
            )[:5],
            "learned_count": len([c for c in corrections if c.learned]),
        }
    
    def add_gl_account(
        self,
        code: str,
        name: str,
        account_type: str = "expense",
        category: Optional[str] = None,
    ) -> GLAccount:
        """Add a custom GL account."""
        # Check for duplicate
        if any(a.code == code for a in self._gl_accounts):
            raise ValueError(f"GL account {code} already exists")
        
        account = GLAccount(
            code=code,
            name=name,
            account_type=account_type,
            category=category,
        )
        
        self._gl_accounts.append(account)
        
        # Save to database
        try:
            self.db.save_gl_account(self.organization_id, account.to_dict())
        except Exception as e:
            logger.warning(f"Failed to save GL account: {e}")
        
        return account
    
    def _get_gl_description(self, gl_code: str) -> str:
        """Get description for a GL code."""
        for account in self._gl_accounts:
            if account.code == gl_code:
                return account.name
        return "Unknown Account"
    
    def _save_correction(self, correction: GLCorrection) -> None:
        """Save correction to database."""
        try:
            self.db.save_gl_correction(self.organization_id, correction.to_dict())
        except Exception as e:
            logger.warning(f"Failed to save GL correction: {e}")


# Singleton
_gl_correction_services: Dict[str, GLCorrectionService] = {}


def get_gl_correction(organization_id: str = "default") -> GLCorrectionService:
    """Get GL correction service for an organization."""
    if organization_id not in _gl_correction_services:
        _gl_correction_services[organization_id] = GLCorrectionService(organization_id)
    return _gl_correction_services[organization_id]
