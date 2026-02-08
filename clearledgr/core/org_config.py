"""
Organization Configuration

Per-organization settings for:
- GL account mappings (how to categorize transactions)
- Confidence thresholds (when to auto-approve vs. flag for review)
- Currency and locale settings
- Feature flags

This is what makes Clearledgr work for different businesses.
A Nigerian fintech using Paystack needs different GL mappings than
a European SaaS using Stripe.
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class GLAccountMapping:
    """
    Mapping from transaction type to GL account.
    
    Example:
        cash -> 1000 (Cash and Cash Equivalents)
        payment_fees -> 6800 (Bank Service Charges)
        accounts_receivable -> 1200 (AR)
    """
    account_type: str  # cash, payment_fees, revenue, accounts_receivable, etc.
    account_code: str  # GL account code
    account_name: str  # Human-readable name
    erp_type: Optional[str] = None  # If ERP-specific


@dataclass
class ConfidenceThresholds:
    """
    Thresholds for automated decision-making.
    
    - auto_match: Score above this = automatic match (no review needed)
    - review_required: Score between review_required and auto_match = needs review
    - reject: Score below this = automatic exception
    """
    auto_match: float = 90.0  # 90% confidence = auto-match
    review_required: float = 70.0  # 70-90% = needs human review
    reject: float = 50.0  # Below 50% = reject as exception
    
    # Journal entry thresholds
    auto_approve_je: float = 95.0  # Auto-post JE above this confidence
    
    # Amount thresholds for escalation
    critical_amount: float = 10000.0  # Amounts above this need senior review
    high_amount: float = 5000.0
    
    def __post_init__(self):
        if not (self.reject <= self.review_required <= self.auto_match):
            raise ValueError("Thresholds must be: reject <= review_required <= auto_match")


@dataclass
class LocaleSettings:
    """Currency and formatting preferences."""
    default_currency: str = "EUR"  # ISO 4217
    secondary_currencies: List[str] = field(default_factory=list)  # ["NGN", "GBP"]
    date_format: str = "DD/MM/YYYY"  # European default
    number_format: str = "european"  # european = 1.234,56 | us = 1,234.56
    timezone: str = "Europe/London"


@dataclass
class FeatureFlags:
    """Enable/disable features per org."""
    auto_reconciliation: bool = True
    auto_categorization: bool = True
    slack_notifications: bool = True
    email_detection: bool = True
    three_way_matching: bool = False  # Gateway + Bank + Internal
    erp_auto_posting: bool = False  # Auto-post JE to ERP
    ai_explanations: bool = True


@dataclass
class PaymentGatewayConfig:
    """Payment gateway specific settings."""
    gateway_type: str  # stripe, paystack, flutterwave
    api_key: Optional[str] = None  # Encrypted in production
    webhook_secret: Optional[str] = None
    enabled: bool = True
    fee_account: Optional[str] = None  # GL account for gateway fees


@dataclass
class DataResidencyConfig:
    """
    GDPR and data residency configuration.
    
    Allows organizations to specify where their data should be stored
    and processed to comply with GDPR and other data protection regulations.
    """
    # Data region - where data is stored
    data_region: str = "eu"  # eu, us, uk, africa, asia-pacific
    
    # Specific country if required (ISO 3166-1 alpha-2)
    data_country: Optional[str] = None  # e.g., "DE" for Germany-only
    
    # GDPR compliance settings
    gdpr_compliant: bool = True  # Enable GDPR compliance features
    data_retention_days: int = 2555  # 7 years default (financial records)
    pii_encryption_enabled: bool = True  # Encrypt PII at rest
    
    # Data processing agreements
    dpa_signed: bool = False  # Data Processing Agreement signed
    dpa_signed_date: Optional[str] = None
    
    # Right to be forgotten
    deletion_request_enabled: bool = True  # Allow data deletion requests
    
    # Data export
    data_portability_enabled: bool = True  # Allow data export (GDPR Art. 20)
    
    # Consent management
    consent_required: bool = True  # Require explicit consent for processing
    consent_version: str = "1.0"
    
    # Sub-processors (third parties that process data)
    approved_sub_processors: List[str] = field(default_factory=lambda: [
        "google_cloud",  # Cloud infrastructure
        "anthropic",     # AI processing
        "slack",         # Notifications
    ])
    
    # Audit and logging
    audit_log_enabled: bool = True
    audit_log_retention_days: int = 365  # 1 year for audit logs
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_region": self.data_region,
            "data_country": self.data_country,
            "gdpr_compliant": self.gdpr_compliant,
            "data_retention_days": self.data_retention_days,
            "pii_encryption_enabled": self.pii_encryption_enabled,
            "dpa_signed": self.dpa_signed,
            "dpa_signed_date": self.dpa_signed_date,
            "deletion_request_enabled": self.deletion_request_enabled,
            "data_portability_enabled": self.data_portability_enabled,
            "consent_required": self.consent_required,
            "consent_version": self.consent_version,
            "approved_sub_processors": self.approved_sub_processors,
            "audit_log_enabled": self.audit_log_enabled,
            "audit_log_retention_days": self.audit_log_retention_days,
        }
    
    def get_storage_location(self) -> str:
        """Get the storage location string for logging/display."""
        if self.data_country:
            return f"{self.data_region}-{self.data_country}"
        return self.data_region
    
    def is_eu_data_resident(self) -> bool:
        """Check if data is stored in EU."""
        return self.data_region == "eu" or self.data_country in [
            "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
            "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
            "PL", "PT", "RO", "SK", "SI", "ES", "SE"
        ]


# Data region options with descriptions
DATA_REGIONS = {
    "eu": {
        "name": "European Union",
        "description": "Data stored in EU data centers (GDPR compliant)",
        "locations": ["Frankfurt", "Amsterdam", "Dublin", "Paris"],
        "regulations": ["GDPR"],
    },
    "uk": {
        "name": "United Kingdom",
        "description": "Data stored in UK data centers (UK GDPR compliant)",
        "locations": ["London"],
        "regulations": ["UK GDPR", "Data Protection Act 2018"],
    },
    "us": {
        "name": "United States",
        "description": "Data stored in US data centers",
        "locations": ["Virginia", "Oregon", "Ohio"],
        "regulations": ["SOC 2", "CCPA (California)"],
    },
    "africa": {
        "name": "Africa",
        "description": "Data stored in African data centers",
        "locations": ["Johannesburg", "Cape Town", "Lagos"],
        "regulations": ["POPIA (South Africa)", "NDPR (Nigeria)"],
    },
    "asia-pacific": {
        "name": "Asia Pacific",
        "description": "Data stored in APAC data centers",
        "locations": ["Singapore", "Sydney", "Tokyo"],
        "regulations": ["PDPA (Singapore)", "Privacy Act (Australia)"],
    },
}


@dataclass
class OrganizationConfig:
    """
    Complete configuration for an organization.
    
    This is the single source of truth for how Clearledgr behaves
    for a specific organization.
    """
    organization_id: str
    organization_name: str
    
    # GL Account Mappings
    gl_mappings: Dict[str, GLAccountMapping] = field(default_factory=dict)
    
    # Thresholds
    thresholds: ConfidenceThresholds = field(default_factory=ConfidenceThresholds)
    
    # Locale
    locale: LocaleSettings = field(default_factory=LocaleSettings)
    
    # Features
    features: FeatureFlags = field(default_factory=FeatureFlags)
    
    # Payment Gateways
    payment_gateways: Dict[str, PaymentGatewayConfig] = field(default_factory=dict)
    
    # Data Residency & GDPR
    data_residency: DataResidencyConfig = field(default_factory=DataResidencyConfig)
    
    # Metadata
    created_at: str = ""
    updated_at: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "gl_mappings": {
                k: asdict(v) for k, v in self.gl_mappings.items()
            },
            "thresholds": asdict(self.thresholds),
            "locale": asdict(self.locale),
            "features": asdict(self.features),
            "payment_gateways": {
                k: asdict(v) for k, v in self.payment_gateways.items()
            },
            "data_residency": self.data_residency.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    def get_gl_account(self, account_type: str) -> Optional[str]:
        """Get GL account code for a transaction type."""
        mapping = self.gl_mappings.get(account_type)
        if mapping:
            return mapping.account_code
        return None
    
    def should_auto_match(self, score: float) -> bool:
        """Should this match be auto-approved?"""
        return score >= self.thresholds.auto_match
    
    def needs_review(self, score: float) -> bool:
        """Does this match need human review?"""
        return self.thresholds.review_required <= score < self.thresholds.auto_match
    
    def is_exception(self, score: float) -> bool:
        """Is this an exception (low confidence)?"""
        return score < self.thresholds.review_required
    
    def is_critical_amount(self, amount: float) -> bool:
        """Is this a critical amount requiring senior review?"""
        return abs(amount) >= self.thresholds.critical_amount


# ==================== DEFAULT CONFIGURATIONS ====================

def get_default_gl_mappings() -> Dict[str, GLAccountMapping]:
    """Default GL account mappings (can be customized per org)."""
    return {
        "cash": GLAccountMapping(
            account_type="cash",
            account_code="1000",
            account_name="Cash and Cash Equivalents",
        ),
        "accounts_receivable": GLAccountMapping(
            account_type="accounts_receivable",
            account_code="1200",
            account_name="Accounts Receivable",
        ),
        "revenue": GLAccountMapping(
            account_type="revenue",
            account_code="4000",
            account_name="Sales Revenue",
        ),
        "payment_fees": GLAccountMapping(
            account_type="payment_fees",
            account_code="6800",
            account_name="Payment Processing Fees",
        ),
        "bank_charges": GLAccountMapping(
            account_type="bank_charges",
            account_code="6810",
            account_name="Bank Service Charges",
        ),
        "fx_gain_loss": GLAccountMapping(
            account_type="fx_gain_loss",
            account_code="7000",
            account_name="Foreign Exchange Gain/Loss",
        ),
    }


def create_default_config(
    organization_id: str,
    organization_name: str,
    currency: str = "EUR",
) -> OrganizationConfig:
    """Create a new organization with default settings."""
    return OrganizationConfig(
        organization_id=organization_id,
        organization_name=organization_name,
        gl_mappings=get_default_gl_mappings(),
        thresholds=ConfidenceThresholds(),
        locale=LocaleSettings(default_currency=currency),
        features=FeatureFlags(),
    )


# ==================== STORAGE ====================

# In-memory storage (replace with database in production)
_org_configs: Dict[str, OrganizationConfig] = {}


def get_org_config(organization_id: str) -> Optional[OrganizationConfig]:
    """Get organization configuration."""
    return _org_configs.get(organization_id)


def save_org_config(config: OrganizationConfig):
    """Save organization configuration."""
    config.updated_at = datetime.now(timezone.utc).isoformat()
    _org_configs[config.organization_id] = config
    logger.info(f"Saved config for organization {config.organization_id}")


def delete_org_config(organization_id: str):
    """Delete organization configuration."""
    if organization_id in _org_configs:
        del _org_configs[organization_id]
        logger.info(f"Deleted config for organization {organization_id}")


def get_or_create_config(
    organization_id: str,
    organization_name: str = "",
    currency: str = "EUR",
) -> OrganizationConfig:
    """Get existing config or create default."""
    config = get_org_config(organization_id)
    if not config:
        config = create_default_config(
            organization_id=organization_id,
            organization_name=organization_name or organization_id,
            currency=currency,
        )
        save_org_config(config)
    return config


# ==================== PRESETS ====================

def get_preset_configs() -> Dict[str, OrganizationConfig]:
    """
    Preset configurations for common use cases.
    
    Organizations can start with a preset and customize.
    """
    return {
        "african_fintech": OrganizationConfig(
            organization_id="preset_african_fintech",
            organization_name="African Fintech Preset",
            gl_mappings=get_default_gl_mappings(),
            thresholds=ConfidenceThresholds(
                auto_match=85.0,  # Lower threshold for African markets
                critical_amount=50000.0,  # NGN 50k is different from EUR 50k
            ),
            locale=LocaleSettings(
                default_currency="NGN",
                secondary_currencies=["USD", "GBP"],
                timezone="Africa/Lagos",
            ),
            features=FeatureFlags(
                three_way_matching=True,  # Common for African fintechs
            ),
            payment_gateways={
                "paystack": PaymentGatewayConfig(
                    gateway_type="paystack",
                    fee_account="6800",
                ),
                "flutterwave": PaymentGatewayConfig(
                    gateway_type="flutterwave",
                    fee_account="6800",
                ),
            },
        ),
        "european_saas": OrganizationConfig(
            organization_id="preset_european_saas",
            organization_name="European SaaS Preset",
            gl_mappings=get_default_gl_mappings(),
            thresholds=ConfidenceThresholds(
                auto_match=90.0,
                critical_amount=10000.0,
            ),
            locale=LocaleSettings(
                default_currency="EUR",
                secondary_currencies=["GBP", "USD"],
                date_format="DD/MM/YYYY",
                number_format="european",
                timezone="Europe/London",
            ),
            features=FeatureFlags(
                erp_auto_posting=True,
            ),
            payment_gateways={
                "stripe": PaymentGatewayConfig(
                    gateway_type="stripe",
                    fee_account="6800",
                ),
            },
        ),
        "uk_business": OrganizationConfig(
            organization_id="preset_uk_business",
            organization_name="UK Business Preset",
            gl_mappings=get_default_gl_mappings(),
            thresholds=ConfidenceThresholds(),
            locale=LocaleSettings(
                default_currency="GBP",
                secondary_currencies=["EUR", "USD"],
                date_format="DD/MM/YYYY",
                timezone="Europe/London",
            ),
            features=FeatureFlags(),
            payment_gateways={
                "stripe": PaymentGatewayConfig(
                    gateway_type="stripe",
                    fee_account="6800",
                ),
            },
        ),
    }
