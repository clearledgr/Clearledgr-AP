"""
Early Payment Discount Service

Handles detection, calculation, and capture of early payment discounts.
Common discount terms:
- 2/10 Net 30 (2% discount if paid within 10 days, otherwise due in 30)
- 1/10 Net 30
- 2/15 Net 45
- 3/10 Net 60
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class DiscountStatus(Enum):
    """Status of early payment discount."""
    AVAILABLE = "available"      # Discount still claimable
    EXPIRING_SOON = "expiring"   # Within 2 days of expiry
    EXPIRED = "expired"          # Discount period passed
    CAPTURED = "captured"        # Discount was taken
    SKIPPED = "skipped"          # Chose not to take discount


@dataclass
class EarlyPaymentDiscount:
    """Represents an early payment discount offer."""
    invoice_id: str
    vendor_name: str
    invoice_amount: float
    
    # Discount terms
    discount_percent: float        # e.g., 2.0 for 2%
    discount_days: int             # Days to pay to get discount
    net_days: int                  # Standard payment terms
    
    # Calculated values
    discount_amount: float = 0.0   # Dollar amount of discount
    discounted_total: float = 0.0  # Amount if discount taken
    
    # Dates
    invoice_date: datetime = field(default_factory=datetime.now)
    discount_deadline: datetime = None
    net_due_date: datetime = None
    
    # Status
    status: DiscountStatus = DiscountStatus.AVAILABLE
    
    # Metadata
    detected_from: str = ""        # "invoice_text", "vendor_terms", "manual"
    confidence: float = 1.0
    
    def __post_init__(self):
        """Calculate derived values."""
        self.discount_amount = round(self.invoice_amount * (self.discount_percent / 100), 2)
        self.discounted_total = round(self.invoice_amount - self.discount_amount, 2)
        
        if self.discount_deadline is None:
            self.discount_deadline = self.invoice_date + timedelta(days=self.discount_days)
        if self.net_due_date is None:
            self.net_due_date = self.invoice_date + timedelta(days=self.net_days)
    
    def days_until_discount_expires(self) -> int:
        """Calculate days remaining to capture discount."""
        delta = self.discount_deadline - datetime.now()
        return max(0, delta.days)
    
    def is_discount_available(self) -> bool:
        """Check if discount can still be captured."""
        return datetime.now() < self.discount_deadline
    
    def annualized_return(self) -> float:
        """
        Calculate annualized return of taking the discount.
        Formula: (discount% / (100 - discount%)) * (365 / (net_days - discount_days))
        
        For 2/10 Net 30: (2/98) * (365/20) = 37.2% annualized return
        """
        if self.net_days <= self.discount_days:
            return 0.0
        
        days_accelerated = self.net_days - self.discount_days
        effective_rate = self.discount_percent / (100 - self.discount_percent)
        annualized = effective_rate * (365 / days_accelerated) * 100
        return round(annualized, 2)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "vendor_name": self.vendor_name,
            "invoice_amount": self.invoice_amount,
            "discount_percent": self.discount_percent,
            "discount_days": self.discount_days,
            "net_days": self.net_days,
            "discount_amount": self.discount_amount,
            "discounted_total": self.discounted_total,
            "invoice_date": self.invoice_date.isoformat(),
            "discount_deadline": self.discount_deadline.isoformat(),
            "net_due_date": self.net_due_date.isoformat(),
            "days_until_expiry": self.days_until_discount_expires(),
            "is_available": self.is_discount_available(),
            "annualized_return": self.annualized_return(),
            "status": self.status.value,
            "detected_from": self.detected_from,
            "confidence": self.confidence,
        }


class EarlyPaymentDiscountService:
    """
    Service for detecting and managing early payment discounts.
    """
    
    # Common discount term patterns
    DISCOUNT_PATTERNS = [
        # "2/10 Net 30" format
        r'(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*[,\s]*[Nn]et\s*(\d+)',
        # "2% 10 days, Net 30" format
        r'(\d+(?:\.\d+)?)\s*%?\s*(?:if paid )?(?:within\s*)?(\d+)\s*days?\s*[,;]?\s*[Nn]et\s*(\d+)',
        # "2% discount if paid within 10 days" format
        r'(\d+(?:\.\d+)?)\s*%?\s*discount\s*(?:if\s*)?(?:paid\s*)?(?:within\s*)?(\d+)\s*days?',
        # "Pay within 10 days for 2% discount"
        r'(?:pay\s*)?(?:within\s*)?(\d+)\s*days?\s*(?:for\s*)?(\d+(?:\.\d+)?)\s*%?\s*(?:discount|off)',
        # "Net 30, 2% 10"
        r'[Nn]et\s*(\d+)\s*[,;]\s*(\d+(?:\.\d+)?)\s*%?\s*(\d+)',
    ]
    
    # Known vendor payment terms (can be loaded from config)
    VENDOR_TERMS: Dict[str, Tuple[float, int, int]] = {
        # vendor_pattern: (discount%, discount_days, net_days)
        "grainger": (2.0, 10, 30),
        "mcmaster": (1.0, 10, 30),
        "office depot": (2.0, 10, 30),
        "staples": (1.5, 15, 45),
        "uline": (2.0, 10, 30),
        "fastenal": (1.0, 15, 30),
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._discounts: Dict[str, EarlyPaymentDiscount] = {}
        self._vendor_terms: Dict[str, Tuple[float, int, int]] = dict(self.VENDOR_TERMS)
    
    def detect_discount_terms(
        self,
        invoice_text: str,
        vendor_name: str = "",
        invoice_amount: float = 0.0,
        invoice_id: str = "",
        invoice_date: datetime = None,
    ) -> Optional[EarlyPaymentDiscount]:
        """
        Detect early payment discount terms from invoice text or vendor.
        
        Returns EarlyPaymentDiscount if terms found, None otherwise.
        """
        invoice_date = invoice_date or datetime.now()
        
        # First, try to detect from invoice text
        terms = self._parse_discount_from_text(invoice_text)
        if terms:
            discount_percent, discount_days, net_days = terms
            discount = EarlyPaymentDiscount(
                invoice_id=invoice_id,
                vendor_name=vendor_name,
                invoice_amount=invoice_amount,
                discount_percent=discount_percent,
                discount_days=discount_days,
                net_days=net_days,
                invoice_date=invoice_date,
                detected_from="invoice_text",
                confidence=0.95,
            )
            self._discounts[invoice_id] = discount
            logger.info(f"Detected discount from invoice: {discount_percent}%/{discount_days} Net {net_days}")
            return discount
        
        # Second, check vendor-specific terms
        vendor_terms = self._get_vendor_terms(vendor_name)
        if vendor_terms:
            discount_percent, discount_days, net_days = vendor_terms
            discount = EarlyPaymentDiscount(
                invoice_id=invoice_id,
                vendor_name=vendor_name,
                invoice_amount=invoice_amount,
                discount_percent=discount_percent,
                discount_days=discount_days,
                net_days=net_days,
                invoice_date=invoice_date,
                detected_from="vendor_terms",
                confidence=0.85,
            )
            self._discounts[invoice_id] = discount
            logger.info(f"Applied vendor terms for {vendor_name}: {discount_percent}%/{discount_days} Net {net_days}")
            return discount
        
        return None
    
    def _parse_discount_from_text(self, text: str) -> Optional[Tuple[float, int, int]]:
        """Parse discount terms from invoice text."""
        if not text:
            return None
        
        text_lower = text.lower()
        
        for pattern in self.DISCOUNT_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                groups = match.groups()
                try:
                    if len(groups) == 3:
                        # Standard format: discount%, discount_days, net_days
                        discount_percent = float(groups[0])
                        discount_days = int(groups[1])
                        net_days = int(groups[2])
                    elif len(groups) == 2:
                        # Simplified format without net days
                        discount_percent = float(groups[0])
                        discount_days = int(groups[1])
                        net_days = 30  # Default assumption
                    else:
                        continue
                    
                    # Validate reasonable values
                    if 0 < discount_percent <= 10 and 0 < discount_days < net_days <= 120:
                        return (discount_percent, discount_days, net_days)
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def _get_vendor_terms(self, vendor_name: str) -> Optional[Tuple[float, int, int]]:
        """Get known payment terms for a vendor."""
        if not vendor_name:
            return None
        
        vendor_lower = vendor_name.lower()
        
        for vendor_pattern, terms in self._vendor_terms.items():
            if vendor_pattern in vendor_lower:
                return terms
        
        return None
    
    def set_vendor_terms(
        self,
        vendor_pattern: str,
        discount_percent: float,
        discount_days: int,
        net_days: int,
    ):
        """Set or update vendor payment terms."""
        self._vendor_terms[vendor_pattern.lower()] = (discount_percent, discount_days, net_days)
        logger.info(f"Set vendor terms for {vendor_pattern}: {discount_percent}%/{discount_days} Net {net_days}")
    
    def get_available_discounts(self) -> List[EarlyPaymentDiscount]:
        """Get all discounts that can still be captured."""
        available = []
        now = datetime.now()
        
        for discount in self._discounts.values():
            if discount.status == DiscountStatus.AVAILABLE:
                if discount.discount_deadline > now:
                    # Update status if expiring soon
                    if discount.days_until_discount_expires() <= 2:
                        discount.status = DiscountStatus.EXPIRING_SOON
                    available.append(discount)
                else:
                    discount.status = DiscountStatus.EXPIRED
        
        # Sort by days until expiry (most urgent first), then by annualized return
        available.sort(key=lambda d: (d.days_until_discount_expires(), -d.annualized_return()))
        return available
    
    def get_expiring_discounts(self, days: int = 3) -> List[EarlyPaymentDiscount]:
        """Get discounts expiring within the specified number of days."""
        return [
            d for d in self.get_available_discounts()
            if d.days_until_discount_expires() <= days
        ]
    
    def capture_discount(self, invoice_id: str) -> Optional[EarlyPaymentDiscount]:
        """Mark a discount as captured (payment made early)."""
        discount = self._discounts.get(invoice_id)
        if discount and discount.status in [DiscountStatus.AVAILABLE, DiscountStatus.EXPIRING_SOON]:
            discount.status = DiscountStatus.CAPTURED
            logger.info(f"Captured discount for {invoice_id}: saved ${discount.discount_amount}")
            return discount
        return None
    
    def skip_discount(self, invoice_id: str, reason: str = "") -> Optional[EarlyPaymentDiscount]:
        """Mark a discount as intentionally skipped."""
        discount = self._discounts.get(invoice_id)
        if discount:
            discount.status = DiscountStatus.SKIPPED
            logger.info(f"Skipped discount for {invoice_id}: {reason}")
            return discount
        return None
    
    def get_discount_summary(self) -> Dict[str, Any]:
        """Get summary statistics for discounts."""
        discounts = list(self._discounts.values())
        
        available = [d for d in discounts if d.status in [DiscountStatus.AVAILABLE, DiscountStatus.EXPIRING_SOON]]
        captured = [d for d in discounts if d.status == DiscountStatus.CAPTURED]
        expired = [d for d in discounts if d.status == DiscountStatus.EXPIRED]
        skipped = [d for d in discounts if d.status == DiscountStatus.SKIPPED]
        
        return {
            "total_discounts": len(discounts),
            "available": {
                "count": len(available),
                "total_savings": sum(d.discount_amount for d in available),
                "expiring_soon": len([d for d in available if d.status == DiscountStatus.EXPIRING_SOON]),
            },
            "captured": {
                "count": len(captured),
                "total_saved": sum(d.discount_amount for d in captured),
            },
            "expired": {
                "count": len(expired),
                "missed_savings": sum(d.discount_amount for d in expired),
            },
            "skipped": {
                "count": len(skipped),
                "foregone_savings": sum(d.discount_amount for d in skipped),
            },
        }
    
    def get_discount(self, invoice_id: str) -> Optional[EarlyPaymentDiscount]:
        """Get discount info for a specific invoice."""
        return self._discounts.get(invoice_id)
    
    def recommend_payment_priority(self) -> List[Dict[str, Any]]:
        """
        Recommend which invoices to pay first based on discount value.
        Returns sorted list with recommendations.
        """
        available = self.get_available_discounts()
        
        recommendations = []
        for discount in available:
            roi = discount.annualized_return()
            urgency = "high" if discount.days_until_discount_expires() <= 2 else \
                     "medium" if discount.days_until_discount_expires() <= 5 else "low"
            
            recommendations.append({
                "invoice_id": discount.invoice_id,
                "vendor": discount.vendor_name,
                "amount": discount.invoice_amount,
                "discount_amount": discount.discount_amount,
                "pay_by": discount.discount_deadline.isoformat(),
                "days_remaining": discount.days_until_discount_expires(),
                "annualized_return": roi,
                "urgency": urgency,
                "recommendation": "pay_early" if roi > 20 else "consider",
            })
        
        return recommendations


# Singleton instance cache
_instances: Dict[str, EarlyPaymentDiscountService] = {}


def get_discount_service(organization_id: str = "default") -> EarlyPaymentDiscountService:
    """Get or create discount service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = EarlyPaymentDiscountService(organization_id)
    return _instances[organization_id]
