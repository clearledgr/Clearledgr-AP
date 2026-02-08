"""
Vendor Intelligence Service

Agent knows about vendors before you tell it:
- Common vendor database (SaaS, utilities, services)
- Typical pricing ranges
- Standard GL mappings
- Industry categorization

Architecture: Part of the MEMORY LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VendorProfile:
    """Known information about a vendor."""
    name: str
    aliases: List[str]
    category: str
    subcategory: str
    typical_pricing: Dict[str, Any]
    suggested_gl: str
    gl_description: str
    website: Optional[str] = None
    description: Optional[str] = None
    billing_frequency: str = "monthly"
    notes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "aliases": self.aliases,
            "category": self.category,
            "subcategory": self.subcategory,
            "typical_pricing": self.typical_pricing,
            "suggested_gl": self.suggested_gl,
            "gl_description": self.gl_description,
            "website": self.website,
            "description": self.description,
            "billing_frequency": self.billing_frequency,
        }


# Pre-loaded vendor database
# In production, this would be a database or API
KNOWN_VENDORS: Dict[str, VendorProfile] = {
    # === CLOUD & INFRASTRUCTURE ===
    "aws": VendorProfile(
        name="Amazon Web Services",
        aliases=["aws", "amazon web services", "amazonaws"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "usage", "range": "$50-$50,000+/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        website="aws.amazon.com",
        description="Cloud computing platform",
        billing_frequency="monthly",
    ),
    "gcp": VendorProfile(
        name="Google Cloud Platform",
        aliases=["gcp", "google cloud", "google cloud platform"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "usage", "range": "$50-$50,000+/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        website="cloud.google.com",
        billing_frequency="monthly",
    ),
    "azure": VendorProfile(
        name="Microsoft Azure",
        aliases=["azure", "microsoft azure"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "usage", "range": "$50-$50,000+/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        billing_frequency="monthly",
    ),
    "digitalocean": VendorProfile(
        name="DigitalOcean",
        aliases=["digitalocean", "digital ocean"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "usage", "range": "$5-$5,000/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        billing_frequency="monthly",
    ),
    "heroku": VendorProfile(
        name="Heroku",
        aliases=["heroku", "salesforce heroku"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "per_dyno", "range": "$7-$500/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        billing_frequency="monthly",
    ),
    "vercel": VendorProfile(
        name="Vercel",
        aliases=["vercel"],
        category="Technology",
        subcategory="Cloud Infrastructure",
        typical_pricing={"model": "per_seat", "per_user": "$20/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        billing_frequency="monthly",
    ),
    
    # === SOFTWARE SUBSCRIPTIONS ===
    "stripe": VendorProfile(
        name="Stripe",
        aliases=["stripe", "stripe inc", "stripe payments"],
        category="Technology",
        subcategory="Payment Processing",
        typical_pricing={"model": "percentage", "rate": "2.9% + $0.30"},
        suggested_gl="6250",
        gl_description="Payment Processing Fees",
        website="stripe.com",
        description="Payment processing platform",
        billing_frequency="monthly",
        notes=["Fees based on transaction volume"],
    ),
    "slack": VendorProfile(
        name="Slack",
        aliases=["slack", "slack technologies"],
        category="Technology",
        subcategory="Collaboration Software",
        typical_pricing={"model": "per_seat", "per_user": "$8.75-$15/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="slack.com",
        billing_frequency="monthly",
    ),
    "notion": VendorProfile(
        name="Notion",
        aliases=["notion", "notion labs", "notion labs inc"],
        category="Technology",
        subcategory="Productivity Software",
        typical_pricing={"model": "per_seat", "per_user": "$8-$15/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="notion.so",
        billing_frequency="monthly",
    ),
    "github": VendorProfile(
        name="GitHub",
        aliases=["github", "github inc"],
        category="Technology",
        subcategory="Developer Tools",
        typical_pricing={"model": "per_seat", "per_user": "$4-$21/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="github.com",
        billing_frequency="monthly",
    ),
    "atlassian": VendorProfile(
        name="Atlassian",
        aliases=["atlassian", "jira", "confluence", "atlassian inc"],
        category="Technology",
        subcategory="Project Management",
        typical_pricing={"model": "per_seat", "per_user": "$7.75-$15.25/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="atlassian.com",
        billing_frequency="monthly",
    ),
    "figma": VendorProfile(
        name="Figma",
        aliases=["figma", "figma inc"],
        category="Technology",
        subcategory="Design Tools",
        typical_pricing={"model": "per_seat", "per_user": "$12-$45/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="figma.com",
        billing_frequency="monthly",
    ),
    "adobe": VendorProfile(
        name="Adobe",
        aliases=["adobe", "adobe inc", "adobe systems", "adobe creative cloud"],
        category="Technology",
        subcategory="Design Tools",
        typical_pricing={"model": "per_seat", "per_user": "$35-$85/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="adobe.com",
        billing_frequency="monthly",
    ),
    "salesforce": VendorProfile(
        name="Salesforce",
        aliases=["salesforce", "salesforce.com", "salesforce inc"],
        category="Technology",
        subcategory="CRM",
        typical_pricing={"model": "per_seat", "per_user": "$25-$300/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="salesforce.com",
        billing_frequency="monthly",
    ),
    "hubspot": VendorProfile(
        name="HubSpot",
        aliases=["hubspot", "hubspot inc"],
        category="Technology",
        subcategory="Marketing/CRM",
        typical_pricing={"model": "tiered", "range": "$50-$3,200/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="hubspot.com",
        billing_frequency="monthly",
    ),
    "zoom": VendorProfile(
        name="Zoom",
        aliases=["zoom", "zoom video", "zoom communications"],
        category="Technology",
        subcategory="Video Conferencing",
        typical_pricing={"model": "per_seat", "per_user": "$15.99-$21.99/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="zoom.us",
        billing_frequency="monthly",
    ),
    "intercom": VendorProfile(
        name="Intercom",
        aliases=["intercom", "intercom inc"],
        category="Technology",
        subcategory="Customer Support",
        typical_pricing={"model": "tiered", "range": "$74-$999/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="intercom.com",
        billing_frequency="monthly",
    ),
    "zendesk": VendorProfile(
        name="Zendesk",
        aliases=["zendesk", "zendesk inc"],
        category="Technology",
        subcategory="Customer Support",
        typical_pricing={"model": "per_seat", "per_user": "$19-$115/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="zendesk.com",
        billing_frequency="monthly",
    ),
    
    # === MONITORING & DEVOPS ===
    "datadog": VendorProfile(
        name="Datadog",
        aliases=["datadog", "datadog inc"],
        category="Technology",
        subcategory="Monitoring",
        typical_pricing={"model": "per_host", "per_host": "$15-$23/mo"},
        suggested_gl="6200",
        gl_description="Cloud & Hosting",
        website="datadoghq.com",
        billing_frequency="monthly",
    ),
    "sentry": VendorProfile(
        name="Sentry",
        aliases=["sentry", "sentry.io", "functional software"],
        category="Technology",
        subcategory="Error Tracking",
        typical_pricing={"model": "tiered", "range": "$26-$80/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="sentry.io",
        billing_frequency="monthly",
    ),
    "pagerduty": VendorProfile(
        name="PagerDuty",
        aliases=["pagerduty", "pagerduty inc"],
        category="Technology",
        subcategory="Incident Management",
        typical_pricing={"model": "per_seat", "per_user": "$21-$41/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="pagerduty.com",
        billing_frequency="monthly",
    ),
    
    # === PROFESSIONAL SERVICES ===
    "quickbooks": VendorProfile(
        name="QuickBooks",
        aliases=["quickbooks", "intuit quickbooks", "intuit"],
        category="Technology",
        subcategory="Accounting Software",
        typical_pricing={"model": "tiered", "range": "$30-$200/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="quickbooks.intuit.com",
        billing_frequency="monthly",
    ),
    "xero": VendorProfile(
        name="Xero",
        aliases=["xero", "xero limited"],
        category="Technology",
        subcategory="Accounting Software",
        typical_pricing={"model": "tiered", "range": "$13-$70/mo"},
        suggested_gl="6150",
        gl_description="Software Subscriptions",
        website="xero.com",
        billing_frequency="monthly",
    ),
    "gusto": VendorProfile(
        name="Gusto",
        aliases=["gusto", "gusto inc"],
        category="Professional Services",
        subcategory="Payroll",
        typical_pricing={"model": "base_plus_per_person", "base": "$40/mo", "per_person": "$6/mo"},
        suggested_gl="6300",
        gl_description="Payroll Services",
        website="gusto.com",
        billing_frequency="monthly",
    ),
    
    # === UTILITIES & OFFICE ===
    "wework": VendorProfile(
        name="WeWork",
        aliases=["wework", "the we company"],
        category="Facilities",
        subcategory="Office Space",
        typical_pricing={"model": "per_desk", "range": "$300-$800/desk/mo"},
        suggested_gl="6400",
        gl_description="Rent & Facilities",
        website="wework.com",
        billing_frequency="monthly",
    ),
    
    # === MARKETING ===
    "google_ads": VendorProfile(
        name="Google Ads",
        aliases=["google ads", "google adwords", "google advertising"],
        category="Marketing",
        subcategory="Digital Advertising",
        typical_pricing={"model": "usage", "range": "varies"},
        suggested_gl="6100",
        gl_description="Advertising & Marketing",
        website="ads.google.com",
        billing_frequency="monthly",
        notes=["Spend-based billing"],
    ),
    "meta_ads": VendorProfile(
        name="Meta Ads",
        aliases=["meta ads", "facebook ads", "instagram ads", "meta platforms"],
        category="Marketing",
        subcategory="Digital Advertising",
        typical_pricing={"model": "usage", "range": "varies"},
        suggested_gl="6100",
        gl_description="Advertising & Marketing",
        billing_frequency="monthly",
    ),
    "linkedin": VendorProfile(
        name="LinkedIn",
        aliases=["linkedin", "linkedin corporation", "linkedin ads"],
        category="Marketing",
        subcategory="Digital Advertising/Recruiting",
        typical_pricing={"model": "usage", "range": "varies"},
        suggested_gl="6100",
        gl_description="Advertising & Marketing",
        website="linkedin.com",
        billing_frequency="monthly",
    ),
}


class VendorIntelligenceService:
    """
    Provides intelligence about vendors without being told.
    
    Usage:
        service = VendorIntelligenceService()
        
        # Look up a vendor
        profile = service.identify("Stripe Inc")
        if profile:
            print(f"Known vendor: {profile.name}")
            print(f"Suggested GL: {profile.suggested_gl}")
            print(f"Typical pricing: {profile.typical_pricing}")
        
        # Validate an invoice amount
        validation = service.validate_amount("Stripe", 299.00, user_count=10)
        if validation["seems_reasonable"]:
            print("Amount looks correct")
        else:
            print(f"Warning: {validation['concern']}")
    """
    
    def __init__(self):
        self.vendors = KNOWN_VENDORS
        # Build alias index for fast lookup
        self._alias_index: Dict[str, str] = {}
        for key, profile in self.vendors.items():
            for alias in profile.aliases:
                self._alias_index[alias.lower()] = key
    
    def identify(self, vendor_name: str) -> Optional[VendorProfile]:
        """
        Identify a vendor from various name formats.
        
        Handles:
        - Exact matches
        - Partial matches
        - Common variations
        """
        if not vendor_name:
            return None
        
        normalized = vendor_name.lower().strip()
        
        # Remove common suffixes
        normalized = re.sub(r'\s*(inc\.?|llc|ltd\.?|corp\.?|co\.?)$', '', normalized, flags=re.IGNORECASE)
        normalized = normalized.strip()
        
        # Direct alias match
        if normalized in self._alias_index:
            return self.vendors[self._alias_index[normalized]]
        
        # Partial match (vendor name contains known alias)
        for alias, key in self._alias_index.items():
            if alias in normalized or normalized in alias:
                return self.vendors[key]
        
        # Word-by-word match
        words = normalized.split()
        for word in words:
            if len(word) > 3 and word in self._alias_index:
                return self.vendors[self._alias_index[word]]
        
        return None
    
    def validate_amount(
        self,
        vendor_name: str,
        amount: float,
        user_count: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Validate if an invoice amount seems reasonable for the vendor.
        """
        profile = self.identify(vendor_name)
        
        if not profile:
            return {
                "known_vendor": False,
                "seems_reasonable": True,  # Can't validate unknown vendor
                "message": "Unknown vendor - cannot validate typical pricing",
            }
        
        pricing = profile.typical_pricing
        model = pricing.get("model", "unknown")
        
        result = {
            "known_vendor": True,
            "vendor_profile": profile.name,
            "pricing_model": model,
            "seems_reasonable": True,
            "message": "",
            "expected_range": None,
        }
        
        # Per-seat pricing validation
        if model == "per_seat" and "per_user" in pricing:
            per_user_str = pricing["per_user"]
            # Parse "$8-$15/mo" format
            match = re.search(r'\$?([\d.]+)(?:-\$?([\d.]+))?', per_user_str)
            if match:
                min_price = float(match.group(1))
                max_price = float(match.group(2)) if match.group(2) else min_price * 2
                
                if user_count:
                    expected_min = min_price * user_count
                    expected_max = max_price * user_count
                    result["expected_range"] = f"${expected_min:.0f}-${expected_max:.0f}/mo for {user_count} users"
                    
                    if amount < expected_min * 0.5:
                        result["seems_reasonable"] = False
                        result["concern"] = f"Amount ${amount:.2f} seems low for {user_count} users"
                    elif amount > expected_max * 2:
                        result["seems_reasonable"] = False
                        result["concern"] = f"Amount ${amount:.2f} seems high for {user_count} users"
                    else:
                        result["message"] = f"Amount aligns with typical pricing for {user_count} users"
        
        # Tiered pricing validation
        elif model == "tiered" and "range" in pricing:
            range_str = pricing["range"]
            match = re.search(r'\$?([\d,]+)(?:-\$?([\d,]+))?', range_str)
            if match:
                min_price = float(match.group(1).replace(",", ""))
                max_price = float(match.group(2).replace(",", "")) if match.group(2) else min_price * 10
                result["expected_range"] = range_str
                
                if amount > max_price * 1.5:
                    result["seems_reasonable"] = False
                    result["concern"] = f"Amount ${amount:.2f} exceeds typical range {range_str}"
        
        return result
    
    def get_suggestion(self, vendor_name: str) -> Optional[Dict[str, Any]]:
        """
        Get suggestions for a vendor (GL code, category, etc.).
        """
        profile = self.identify(vendor_name)
        
        if not profile:
            return None
        
        return {
            "vendor_name": profile.name,
            "suggested_gl": profile.suggested_gl,
            "gl_description": profile.gl_description,
            "category": profile.category,
            "subcategory": profile.subcategory,
            "billing_frequency": profile.billing_frequency,
            "typical_pricing": profile.typical_pricing,
            "notes": profile.notes,
        }
    
    def enrich_invoice(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich invoice data with vendor intelligence.
        """
        vendor_name = invoice.get("vendor", "")
        amount = invoice.get("amount", 0)
        
        enriched = invoice.copy()
        
        profile = self.identify(vendor_name)
        if profile:
            enriched["vendor_intelligence"] = {
                "known_vendor": True,
                "canonical_name": profile.name,
                "category": profile.category,
                "subcategory": profile.subcategory,
                "suggested_gl": profile.suggested_gl,
                "gl_description": profile.gl_description,
                "typical_pricing": profile.typical_pricing,
                "website": profile.website,
            }
            
            # Validate amount
            validation = self.validate_amount(vendor_name, amount)
            enriched["vendor_intelligence"]["amount_validation"] = validation
        else:
            enriched["vendor_intelligence"] = {
                "known_vendor": False,
                "message": "New vendor - no prior intelligence available",
            }
        
        return enriched
    
    def format_for_slack(self, vendor_name: str) -> Optional[Dict[str, Any]]:
        """Format vendor intelligence for Slack display."""
        profile = self.identify(vendor_name)
        
        if not profile:
            return None
        
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Vendor Intelligence: {profile.name}*\n"
                    f"• Category: {profile.category} > {profile.subcategory}\n"
                    f"• Typical pricing: {profile.typical_pricing.get('range', profile.typical_pricing.get('per_user', 'varies'))}\n"
                    f"• Suggested GL: {profile.suggested_gl} ({profile.gl_description})\n"
                    f"• Billing: {profile.billing_frequency}"
                )
            }
        }


# Convenience function
def get_vendor_intelligence() -> VendorIntelligenceService:
    """Get a vendor intelligence service instance."""
    return VendorIntelligenceService()
