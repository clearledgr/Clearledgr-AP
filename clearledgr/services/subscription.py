"""
Subscription & Plan Management Service

Handles:
- Plan tiers (Free, Trial, Pro, Enterprise)
- Trial management (14-day trial)
- Feature gating based on plan
- Usage tracking and limits
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class PlanTier(str, Enum):
    """Available subscription plans."""
    FREE = "free"
    TRIAL = "trial"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class PlanLimits:
    """Usage limits per plan tier."""
    invoices_per_month: int
    vendors: int
    users: int
    erp_connections: int
    api_calls_per_day: int
    storage_gb: float
    ai_extractions_per_month: int
    
    @classmethod
    def for_tier(cls, tier: PlanTier) -> "PlanLimits":
        """Get limits for a specific plan tier."""
        limits = {
            PlanTier.FREE: cls(
                invoices_per_month=25,
                vendors=10,
                users=1,
                erp_connections=1,
                api_calls_per_day=100,
                storage_gb=0.5,
                ai_extractions_per_month=50,
            ),
            PlanTier.TRIAL: cls(
                invoices_per_month=500,
                vendors=100,
                users=5,
                erp_connections=3,
                api_calls_per_day=5000,
                storage_gb=5.0,
                ai_extractions_per_month=1000,
            ),
            PlanTier.PRO: cls(
                invoices_per_month=500,
                vendors=100,
                users=5,
                erp_connections=3,
                api_calls_per_day=5000,
                storage_gb=5.0,
                ai_extractions_per_month=1000,
            ),
            PlanTier.ENTERPRISE: cls(
                invoices_per_month=-1,  # Unlimited
                vendors=-1,
                users=-1,
                erp_connections=-1,
                api_calls_per_day=-1,
                storage_gb=100.0,
                ai_extractions_per_month=-1,
            ),
        }
        return limits.get(tier, limits[PlanTier.FREE])


@dataclass
class PlanFeatures:
    """Features available per plan tier."""
    # Core features
    email_scanning: bool = True
    invoice_extraction: bool = True
    vendor_management: bool = True
    
    # Advanced features
    ai_categorization: bool = False
    three_way_matching: bool = False
    erp_auto_posting: bool = False
    custom_gl_rules: bool = False
    recurring_detection: bool = False
    
    # Premium features
    multi_currency: bool = False
    advanced_analytics: bool = False
    api_access: bool = False
    slack_integration: bool = False
    custom_workflows: bool = False
    priority_support: bool = False
    sso: bool = False
    audit_logs: bool = False
    
    @classmethod
    def for_tier(cls, tier: PlanTier) -> "PlanFeatures":
        """Get features for a specific plan tier."""
        features = {
            PlanTier.FREE: cls(
                email_scanning=True,
                invoice_extraction=True,
                vendor_management=True,
                ai_categorization=False,
                three_way_matching=False,
                erp_auto_posting=False,
                custom_gl_rules=False,
                recurring_detection=False,
                multi_currency=False,
                advanced_analytics=False,
                api_access=False,
                slack_integration=False,
                custom_workflows=False,
                priority_support=False,
                sso=False,
                audit_logs=False,
            ),
            PlanTier.TRIAL: cls(
                email_scanning=True,
                invoice_extraction=True,
                vendor_management=True,
                ai_categorization=True,
                three_way_matching=True,
                erp_auto_posting=True,
                custom_gl_rules=True,
                recurring_detection=True,
                multi_currency=True,
                advanced_analytics=True,
                api_access=True,
                slack_integration=True,
                custom_workflows=True,
                priority_support=False,
                sso=False,
                audit_logs=True,
            ),
            PlanTier.PRO: cls(
                email_scanning=True,
                invoice_extraction=True,
                vendor_management=True,
                ai_categorization=True,
                three_way_matching=True,
                erp_auto_posting=True,
                custom_gl_rules=True,
                recurring_detection=True,
                multi_currency=True,
                advanced_analytics=True,
                api_access=True,
                slack_integration=True,
                custom_workflows=True,
                priority_support=True,
                sso=False,
                audit_logs=True,
            ),
            PlanTier.ENTERPRISE: cls(
                email_scanning=True,
                invoice_extraction=True,
                vendor_management=True,
                ai_categorization=True,
                three_way_matching=True,
                erp_auto_posting=True,
                custom_gl_rules=True,
                recurring_detection=True,
                multi_currency=True,
                advanced_analytics=True,
                api_access=True,
                slack_integration=True,
                custom_workflows=True,
                priority_support=True,
                sso=True,
                audit_logs=True,
            ),
        }
        return features.get(tier, features[PlanTier.FREE])


@dataclass
class UsageStats:
    """Current usage statistics for an organization."""
    invoices_this_month: int = 0
    vendors_count: int = 0
    users_count: int = 1
    api_calls_today: int = 0
    storage_used_gb: float = 0.0
    ai_extractions_this_month: int = 0
    last_reset: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Subscription:
    """Organization subscription details."""
    organization_id: str
    plan: PlanTier = PlanTier.FREE
    status: str = "active"  # active, cancelled, past_due, trialing
    
    # Trial info
    trial_started_at: Optional[str] = None
    trial_ends_at: Optional[str] = None
    trial_days_remaining: int = 0
    
    # Billing info
    billing_cycle: str = "monthly"  # monthly, yearly
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    
    # Payment provider
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    
    # Limits and features
    limits: Optional[PlanLimits] = None
    features: Optional[PlanFeatures] = None
    usage: Optional[UsageStats] = None
    
    # Onboarding
    onboarding_completed: bool = False
    onboarding_step: int = 0
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def __post_init__(self):
        if self.limits is None:
            self.limits = PlanLimits.for_tier(self.plan)
        if self.features is None:
            self.features = PlanFeatures.for_tier(self.plan)
        if self.usage is None:
            self.usage = UsageStats()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "plan": self.plan.value,
            "status": self.status,
            "trial_started_at": self.trial_started_at,
            "trial_ends_at": self.trial_ends_at,
            "trial_days_remaining": self.trial_days_remaining,
            "billing_cycle": self.billing_cycle,
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "limits": asdict(self.limits) if self.limits else None,
            "features": asdict(self.features) if self.features else None,
            "usage": self.usage.to_dict() if self.usage else None,
            "onboarding_completed": self.onboarding_completed,
            "onboarding_step": self.onboarding_step,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SubscriptionService:
    """
    Manages subscriptions, trials, and feature access.
    """
    
    TRIAL_DAYS = 14
    
    def __init__(self):
        self._subscriptions: Dict[str, Subscription] = {}
    
    def get_subscription(self, organization_id: str) -> Subscription:
        """Get or create subscription for an organization."""
        if organization_id not in self._subscriptions:
            self._subscriptions[organization_id] = Subscription(
                organization_id=organization_id
            )
        
        sub = self._subscriptions[organization_id]
        self._update_trial_status(sub)
        return sub
    
    def start_trial(self, organization_id: str) -> Subscription:
        """Start a 14-day trial for an organization."""
        sub = self.get_subscription(organization_id)
        
        if sub.trial_started_at:
            logger.warning(f"Organization {organization_id} already had a trial")
            return sub
        
        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(days=self.TRIAL_DAYS)
        
        sub.plan = PlanTier.TRIAL
        sub.status = "trialing"
        sub.trial_started_at = now.isoformat()
        sub.trial_ends_at = trial_end.isoformat()
        sub.trial_days_remaining = self.TRIAL_DAYS
        sub.limits = PlanLimits.for_tier(PlanTier.TRIAL)
        sub.features = PlanFeatures.for_tier(PlanTier.TRIAL)
        sub.updated_at = now.isoformat()
        
        logger.info(f"Started trial for organization {organization_id}")
        return sub
    
    def upgrade_to_pro(self, organization_id: str, stripe_customer_id: str = None, stripe_subscription_id: str = None) -> Subscription:
        """Upgrade organization to Pro plan."""
        sub = self.get_subscription(organization_id)
        
        now = datetime.now(timezone.utc)
        period_end = now + timedelta(days=30)  # Monthly billing
        
        sub.plan = PlanTier.PRO
        sub.status = "active"
        sub.stripe_customer_id = stripe_customer_id
        sub.stripe_subscription_id = stripe_subscription_id
        sub.current_period_start = now.isoformat()
        sub.current_period_end = period_end.isoformat()
        sub.limits = PlanLimits.for_tier(PlanTier.PRO)
        sub.features = PlanFeatures.for_tier(PlanTier.PRO)
        sub.updated_at = now.isoformat()
        
        logger.info(f"Upgraded organization {organization_id} to Pro")
        return sub
    
    def downgrade_to_free(self, organization_id: str) -> Subscription:
        """Downgrade organization to Free plan."""
        sub = self.get_subscription(organization_id)
        
        sub.plan = PlanTier.FREE
        sub.status = "active"
        sub.limits = PlanLimits.for_tier(PlanTier.FREE)
        sub.features = PlanFeatures.for_tier(PlanTier.FREE)
        sub.updated_at = datetime.now(timezone.utc).isoformat()
        
        logger.info(f"Downgraded organization {organization_id} to Free")
        return sub
    
    def complete_onboarding_step(self, organization_id: str, step: int) -> Subscription:
        """Mark an onboarding step as complete."""
        sub = self.get_subscription(organization_id)
        
        if step > sub.onboarding_step:
            sub.onboarding_step = step
        
        # Steps: 1=Welcome, 2=Connect ERP, 3=Configure GL, 4=Quick Tour, 5=Complete
        if step >= 5:
            sub.onboarding_completed = True
        
        sub.updated_at = datetime.now(timezone.utc).isoformat()
        return sub
    
    def skip_onboarding(self, organization_id: str) -> Subscription:
        """Skip onboarding flow."""
        sub = self.get_subscription(organization_id)
        sub.onboarding_completed = True
        sub.onboarding_step = 5
        sub.updated_at = datetime.now(timezone.utc).isoformat()
        return sub
    
    def check_feature_access(self, organization_id: str, feature: str) -> bool:
        """Check if organization has access to a feature."""
        sub = self.get_subscription(organization_id)
        
        if sub.features is None:
            return False
        
        return getattr(sub.features, feature, False)
    
    def check_limit(self, organization_id: str, limit_type: str, current_value: int) -> Dict[str, Any]:
        """Check if organization is within a usage limit."""
        sub = self.get_subscription(organization_id)
        
        if sub.limits is None:
            return {"allowed": False, "limit": 0, "current": current_value}
        
        limit = getattr(sub.limits, limit_type, 0)
        
        # -1 means unlimited
        if limit == -1:
            return {"allowed": True, "limit": -1, "current": current_value, "unlimited": True}
        
        return {
            "allowed": current_value < limit,
            "limit": limit,
            "current": current_value,
            "remaining": max(0, limit - current_value),
            "percentage_used": round((current_value / limit) * 100, 1) if limit > 0 else 0,
        }
    
    def increment_usage(self, organization_id: str, usage_type: str, amount: int = 1) -> UsageStats:
        """Increment a usage counter."""
        sub = self.get_subscription(organization_id)
        
        if sub.usage is None:
            sub.usage = UsageStats()
        
        current = getattr(sub.usage, usage_type, 0)
        setattr(sub.usage, usage_type, current + amount)
        
        return sub.usage
    
    def _update_trial_status(self, sub: Subscription) -> None:
        """Update trial status and days remaining."""
        if sub.status != "trialing" or not sub.trial_ends_at:
            return
        
        try:
            trial_end = datetime.fromisoformat(sub.trial_ends_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            
            if now >= trial_end:
                # Trial expired - downgrade to free
                sub.plan = PlanTier.FREE
                sub.status = "active"
                sub.trial_days_remaining = 0
                sub.limits = PlanLimits.for_tier(PlanTier.FREE)
                sub.features = PlanFeatures.for_tier(PlanTier.FREE)
                logger.info(f"Trial expired for organization {sub.organization_id}")
            else:
                # Calculate days remaining
                delta = trial_end - now
                sub.trial_days_remaining = max(0, delta.days)
        except Exception as e:
            logger.error(f"Error updating trial status: {e}")


# Singleton instance
_subscription_service: Optional[SubscriptionService] = None


def get_subscription_service() -> SubscriptionService:
    """Get the subscription service singleton."""
    global _subscription_service
    if _subscription_service is None:
        _subscription_service = SubscriptionService()
    return _subscription_service
