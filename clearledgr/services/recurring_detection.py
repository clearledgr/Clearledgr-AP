"""
Recurring Invoice Detection Service

Automatically recognizes recurring subscriptions and bills:
- Software subscriptions (Stripe, AWS, GitHub, etc.)
- Utilities
- Monthly services

Behavior:
- Auto-approves if amount matches previous month
- Alerts if amount changed significantly
- Alerts if unexpected timing
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class RecurringPattern:
    """Detected recurring pattern for a vendor."""
    vendor: str
    typical_amount: float
    currency: str
    frequency_days: int  # e.g., 30 for monthly
    last_invoice_date: str
    invoice_count: int
    variance_threshold: float = 0.05  # 5% variance allowed


class RecurringDetectionService:
    """
    Detects and handles recurring invoices.
    
    Known recurring vendors are auto-processed if amount matches.
    """
    
    # Known SaaS/subscription vendors (expand as needed)
    KNOWN_RECURRING_VENDORS = {
        # Cloud & Infrastructure
        "aws", "amazon web services", "google cloud", "gcp", "azure", "microsoft azure",
        "digitalocean", "heroku", "vercel", "netlify", "cloudflare",
        
        # Developer Tools
        "github", "gitlab", "bitbucket", "atlassian", "jira", "confluence",
        "slack", "notion", "figma", "miro", "linear",
        
        # Business Software
        "salesforce", "hubspot", "zendesk", "intercom", "freshdesk",
        "quickbooks", "xero", "gusto", "rippling", "deel",
        
        # Communication
        "zoom", "twilio", "sendgrid", "mailchimp", "mailgun",
        
        # Analytics & Monitoring
        "datadog", "new relic", "sentry", "mixpanel", "amplitude", "segment",
        
        # Payments
        "stripe", "braintree", "adyen", "square",
        
        # Security
        "1password", "okta", "auth0",
        
        # Other common
        "dropbox", "box", "google workspace", "microsoft 365", "office 365",
        "adobe", "canva", "grammarly",
    }
    
    def __init__(self, organization_id: str):
        self.organization_id = organization_id
        self.db = get_db()
    
    def is_likely_recurring(self, vendor: str, sender_email: str = "") -> bool:
        """Check if a vendor is likely a recurring subscription."""
        vendor_lower = vendor.lower()
        email_lower = sender_email.lower()
        
        # Check against known vendors
        for known in self.KNOWN_RECURRING_VENDORS:
            if known in vendor_lower or known in email_lower:
                return True
        
        # Check for subscription-related keywords
        subscription_keywords = [
            "subscription", "invoice", "billing", "monthly", "annual",
            "renewal", "recurring", "payment due",
        ]
        for keyword in subscription_keywords:
            if keyword in vendor_lower:
                return True
        
        return False
    
    def get_vendor_history(self, vendor: str) -> List[Dict[str, Any]]:
        """Get invoice history for a vendor."""
        # Query past invoices for this vendor
        invoices = self.db.get_invoices_by_status(
            organization_id=self.organization_id,
            status="posted",
            limit=12,  # Last year of monthly invoices
        )
        
        vendor_lower = vendor.lower()
        return [
            inv for inv in invoices
            if inv.get("vendor", "").lower() == vendor_lower
        ]
    
    def detect_pattern(self, vendor: str) -> Optional[RecurringPattern]:
        """Detect recurring pattern for a vendor based on history."""
        history = self.get_vendor_history(vendor)
        
        if len(history) < 2:
            return None  # Not enough data
        
        # Calculate typical amount (median)
        amounts = [inv.get("amount", 0) for inv in history if inv.get("amount")]
        if not amounts:
            return None
        
        amounts.sort()
        median_idx = len(amounts) // 2
        typical_amount = amounts[median_idx]
        
        # Calculate frequency (days between invoices)
        dates = []
        for inv in history:
            date_str = inv.get("created_at")
            if date_str:
                try:
                    dates.append(datetime.fromisoformat(date_str.replace("Z", "+00:00")))
                except:
                    pass
        
        if len(dates) < 2:
            return None
        
        dates.sort(reverse=True)
        intervals = []
        for i in range(len(dates) - 1):
            delta = (dates[i] - dates[i + 1]).days
            intervals.append(delta)
        
        # Typical frequency
        avg_interval = sum(intervals) / len(intervals) if intervals else 30
        
        return RecurringPattern(
            vendor=vendor,
            typical_amount=typical_amount,
            currency=history[0].get("currency", "USD"),
            frequency_days=round(avg_interval),
            last_invoice_date=history[0].get("created_at", ""),
            invoice_count=len(history),
        )
    
    def analyze_invoice(
        self,
        vendor: str,
        amount: float,
        currency: str = "USD",
        sender_email: str = "",
    ) -> Dict[str, Any]:
        """
        Analyze an invoice for recurring pattern.
        
        Returns:
            Dict with:
            - is_recurring: bool
            - auto_approve: bool
            - alerts: List of any issues
            - pattern: RecurringPattern if detected
        """
        result = {
            "is_recurring": False,
            "auto_approve": False,
            "alerts": [],
            "pattern": None,
            "confidence": 0.0,
        }
        
        # Check if likely recurring
        if not self.is_likely_recurring(vendor, sender_email):
            return result
        
        result["is_recurring"] = True
        
        # Get pattern from history
        pattern = self.detect_pattern(vendor)
        
        if not pattern:
            # First time seeing this vendor as recurring
            result["alerts"].append({
                "type": "new_recurring",
                "message": f"First invoice from {vendor} - establishing baseline",
            })
            result["confidence"] = 0.5
            return result
        
        result["pattern"] = {
            "vendor": pattern.vendor,
            "typical_amount": pattern.typical_amount,
            "frequency_days": pattern.frequency_days,
            "invoice_count": pattern.invoice_count,
        }
        
        # Check amount variance
        if pattern.typical_amount > 0:
            variance = abs(amount - pattern.typical_amount) / pattern.typical_amount
            
            if variance <= pattern.variance_threshold:
                # Amount matches - safe to auto-approve
                result["auto_approve"] = True
                result["confidence"] = 0.95
            elif variance <= 0.20:  # Up to 20% variance
                result["alerts"].append({
                    "type": "amount_changed",
                    "message": f"Amount changed from {currency} {pattern.typical_amount:,.2f} to {currency} {amount:,.2f} ({variance*100:.1f}% change)",
                    "severity": "warning",
                })
                result["confidence"] = 0.7
            else:
                result["alerts"].append({
                    "type": "significant_change",
                    "message": f"Significant amount change: was {currency} {pattern.typical_amount:,.2f}, now {currency} {amount:,.2f}",
                    "severity": "high",
                })
                result["auto_approve"] = False
                result["confidence"] = 0.4
        
        # Check timing (if we have last invoice date)
        if pattern.last_invoice_date:
            try:
                last_date = datetime.fromisoformat(pattern.last_invoice_date.replace("Z", "+00:00"))
                days_since = (datetime.now(last_date.tzinfo) - last_date).days
                expected_days = pattern.frequency_days
                
                # Allow 5-day window around expected date
                if abs(days_since - expected_days) > 5:
                    if days_since < expected_days - 5:
                        result["alerts"].append({
                            "type": "early_invoice",
                            "message": f"Invoice arrived {expected_days - days_since} days earlier than expected",
                            "severity": "info",
                        })
                    else:
                        result["alerts"].append({
                            "type": "late_invoice",
                            "message": f"Invoice arrived {days_since - expected_days} days later than expected",
                            "severity": "info",
                        })
            except:
                pass
        
        return result


def get_recurring_detector(organization_id: str) -> RecurringDetectionService:
    """Get recurring detection service instance."""
    return RecurringDetectionService(organization_id=organization_id)
