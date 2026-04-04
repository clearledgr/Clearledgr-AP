"""
Subscription & Plan Management Service

Handles:
- Plan tiers (Free, Starter, Professional, Enterprise)
- Trial management (14-day Professional trial)
- Feature gating based on plan
- Usage tracking and limits
- AI credit consumption
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
    STARTER = "starter"
    PROFESSIONAL = "professional"
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
    ai_credits_per_month: int

    @classmethod
    def for_tier(cls, tier: PlanTier) -> "PlanLimits":
        """Get limits for a specific plan tier."""
        limits = {
            PlanTier.FREE: cls(
                invoices_per_month=10,
                vendors=5,
                users=1,
                erp_connections=0,
                api_calls_per_day=50,
                storage_gb=0.25,
                ai_credits_per_month=5,
            ),
            PlanTier.STARTER: cls(
                invoices_per_month=-1,
                vendors=-1,
                users=3,
                erp_connections=1,
                api_calls_per_day=1000,
                storage_gb=5.0,
                ai_credits_per_month=150,
            ),
            PlanTier.PROFESSIONAL: cls(
                invoices_per_month=-1,
                vendors=-1,
                users=15,
                erp_connections=3,
                api_calls_per_day=10000,
                storage_gb=25.0,
                ai_credits_per_month=3000,
            ),
            PlanTier.ENTERPRISE: cls(
                invoices_per_month=-1,
                vendors=-1,
                users=-1,
                erp_connections=-1,
                api_calls_per_day=-1,
                storage_gb=100.0,
                ai_credits_per_month=-1,
            ),
        }
        return limits.get(tier, limits[PlanTier.FREE])


@dataclass
class PlanFeatures:
    """Features available per plan tier."""
    # Core
    gmail_sidebar: bool = True
    invoice_extraction: bool = True
    vendor_management: bool = False

    # ERP & routing
    erp_posting: bool = False
    approval_routing: bool = False

    # AI / Intelligence
    ai_categorization: bool = False
    three_way_matching: bool = False
    custom_gl_rules: bool = False
    recurring_detection: bool = False

    # Premium
    multi_currency: bool = False
    advanced_analytics: bool = False
    api_access: bool = False
    slack_integration: bool = False
    teams_integration: bool = False
    custom_policies: bool = False
    approval_chains: bool = False
    vendor_intelligence: bool = False
    audit_logs: bool = False
    gl_auto_coding: bool = False
    pipeline_saved_views: bool = False
    custom_workflows: bool = False
    priority_support: bool = False
    sso: bool = False
    data_residency: bool = False

    @classmethod
    def for_tier(cls, tier: PlanTier) -> "PlanFeatures":
        """Get features for a specific plan tier."""
        features = {
            PlanTier.FREE: cls(
                gmail_sidebar=True,
                invoice_extraction=True,
                vendor_management=False,
                erp_posting=False,
                approval_routing=False,
                ai_categorization=False,
                three_way_matching=False,
                custom_gl_rules=False,
                recurring_detection=False,
                multi_currency=False,
                advanced_analytics=False,
                api_access=False,
                slack_integration=False,
                teams_integration=False,
                custom_policies=False,
                approval_chains=False,
                vendor_intelligence=False,
                audit_logs=False,
                gl_auto_coding=False,
                pipeline_saved_views=False,
                custom_workflows=False,
                priority_support=False,
                sso=False,
                data_residency=False,
            ),
            PlanTier.STARTER: cls(
                gmail_sidebar=True,
                invoice_extraction=True,
                vendor_management=True,
                erp_posting=True,
                approval_routing=True,
                ai_categorization=True,
                three_way_matching=False,
                custom_gl_rules=False,
                recurring_detection=False,
                multi_currency=False,
                advanced_analytics=False,
                api_access=False,
                slack_integration=True,
                teams_integration=True,
                custom_policies=False,
                approval_chains=True,
                vendor_intelligence=True,
                audit_logs=True,
                gl_auto_coding=True,
                pipeline_saved_views=True,
                custom_workflows=False,
                priority_support=False,
                sso=False,
                data_residency=False,
            ),
            PlanTier.PROFESSIONAL: cls(
                gmail_sidebar=True,
                invoice_extraction=True,
                vendor_management=True,
                erp_posting=True,
                approval_routing=True,
                ai_categorization=True,
                three_way_matching=True,
                custom_gl_rules=True,
                recurring_detection=True,
                multi_currency=True,
                advanced_analytics=True,
                api_access=True,
                slack_integration=True,
                teams_integration=True,
                custom_policies=True,
                approval_chains=True,
                vendor_intelligence=True,
                audit_logs=True,
                gl_auto_coding=True,
                pipeline_saved_views=True,
                custom_workflows=True,
                priority_support=True,
                sso=False,
                data_residency=False,
            ),
            PlanTier.ENTERPRISE: cls(
                gmail_sidebar=True,
                invoice_extraction=True,
                vendor_management=True,
                erp_posting=True,
                approval_routing=True,
                ai_categorization=True,
                three_way_matching=True,
                custom_gl_rules=True,
                recurring_detection=True,
                multi_currency=True,
                advanced_analytics=True,
                api_access=True,
                slack_integration=True,
                teams_integration=True,
                custom_policies=True,
                approval_chains=True,
                vendor_intelligence=True,
                audit_logs=True,
                gl_auto_coding=True,
                pipeline_saved_views=True,
                custom_workflows=True,
                priority_support=True,
                sso=True,
                data_residency=True,
            ),
        }
        return features.get(tier, features[PlanTier.FREE])


# ---------------------------------------------------------------------------
# Pricing Constants (display / billing reference -- not enforcement)
# ---------------------------------------------------------------------------
PLAN_PRICING: Dict[PlanTier, Dict[str, int]] = {
    PlanTier.FREE:         {"monthly": 0,   "annual": 0},
    PlanTier.STARTER:      {"monthly": 79,  "annual": 65},
    PlanTier.PROFESSIONAL: {"monthly": 149, "annual": 125},
    PlanTier.ENTERPRISE:   {"monthly": 299, "annual": 249},
}

AI_CREDIT_COSTS: Dict[str, int] = {
    "invoice_extraction": 1,
    "ap_decision": 1,
    "vendor_outreach_draft": 1,
    "gl_auto_coding": 1,
    "vendor_intelligence_enrichment": 2,
    "cross_invoice_analysis": 1,
    "agent_planning_loop": 5,
}

AI_CREDIT_ADDON_PRICING: Dict[int, int] = {
    100: 75,
    250: 150,
    500: 250,
    1000: 400,
    5000: 1500,
}


# ---------------------------------------------------------------------------
# Usage Stats
# ---------------------------------------------------------------------------
@dataclass
class UsageStats:
    """Current usage statistics for an organization."""
    invoices_this_month: int = 0
    vendors_count: int = 0
    users_count: int = 1
    api_calls_today: int = 0
    storage_used_gb: float = 0.0
    ai_credits_this_month: int = 0
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


# ---------------------------------------------------------------------------
# Subscription Service
# ---------------------------------------------------------------------------
class SubscriptionService:
    """
    Manages subscriptions, trials, and feature access.
    """

    TRIAL_DAYS = 14

    def __init__(self):
        from clearledgr.core.database import get_db

        self.db = get_db()

    def _subscription_from_row(self, row: Optional[Dict[str, Any]], organization_id: str) -> Subscription:
        if not row:
            return Subscription(organization_id=organization_id)

        plan_value = str(row.get("plan") or PlanTier.FREE.value).lower()
        try:
            plan = PlanTier(plan_value)
        except ValueError:
            plan = PlanTier.FREE

        usage_raw = row.get("usage_json") or {}
        usage = UsageStats(**usage_raw) if isinstance(usage_raw, dict) else UsageStats()

        limits_raw = row.get("limits_json") or {}
        if isinstance(limits_raw, dict) and limits_raw:
            try:
                limits = PlanLimits(**limits_raw)
            except TypeError:
                limits = PlanLimits.for_tier(plan)
        else:
            limits = PlanLimits.for_tier(plan)

        features_raw = row.get("features_json") or {}
        if isinstance(features_raw, dict) and features_raw:
            try:
                features = PlanFeatures(**features_raw)
            except TypeError:
                features = PlanFeatures.for_tier(plan)
        else:
            features = PlanFeatures.for_tier(plan)

        return Subscription(
            organization_id=organization_id,
            plan=plan,
            status=str(row.get("status") or "active"),
            trial_started_at=row.get("trial_started_at"),
            trial_ends_at=row.get("trial_ends_at"),
            trial_days_remaining=int(row.get("trial_days_remaining") or 0),
            billing_cycle=str(row.get("billing_cycle") or "monthly"),
            current_period_start=row.get("current_period_start"),
            current_period_end=row.get("current_period_end"),
            stripe_customer_id=row.get("stripe_customer_id"),
            stripe_subscription_id=row.get("stripe_subscription_id"),
            limits=limits,
            features=features,
            usage=usage,
            onboarding_completed=bool(row.get("onboarding_completed")),
            onboarding_step=int(row.get("onboarding_step") or 0),
            created_at=str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(row.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )

    def _save_subscription(self, sub: Subscription) -> Subscription:
        sub.updated_at = datetime.now(timezone.utc).isoformat()
        row = self.db.upsert_subscription_record(
            sub.organization_id,
            {
                "plan": sub.plan.value,
                "status": sub.status,
                "trial_started_at": sub.trial_started_at,
                "trial_ends_at": sub.trial_ends_at,
                "trial_days_remaining": sub.trial_days_remaining,
                "billing_cycle": sub.billing_cycle,
                "current_period_start": sub.current_period_start,
                "current_period_end": sub.current_period_end,
                "stripe_customer_id": sub.stripe_customer_id,
                "stripe_subscription_id": sub.stripe_subscription_id,
                "limits": asdict(sub.limits) if sub.limits else {},
                "features": asdict(sub.features) if sub.features else {},
                "usage": sub.usage.to_dict() if sub.usage else {},
                "onboarding_completed": sub.onboarding_completed,
                "onboarding_step": sub.onboarding_step,
                "created_at": sub.created_at,
                "updated_at": sub.updated_at,
            },
        )
        return self._subscription_from_row(row, sub.organization_id)

    def get_subscription(self, organization_id: str) -> Subscription:
        """Get or create subscription for an organization."""
        self.db.ensure_organization(organization_id, organization_name=organization_id)
        row = self.db.get_subscription_record(organization_id)
        sub = self._subscription_from_row(row, organization_id)
        self._update_trial_status(sub)
        sub = self._save_subscription(sub)
        return sub

    def start_trial(self, organization_id: str) -> Subscription:
        """Start a 14-day Professional trial for an organization."""
        sub = self.get_subscription(organization_id)

        if sub.trial_started_at:
            logger.warning(f"Organization {organization_id} already had a trial")
            return sub

        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(days=self.TRIAL_DAYS)

        sub.plan = PlanTier.PROFESSIONAL
        sub.status = "trialing"
        sub.trial_started_at = now.isoformat()
        sub.trial_ends_at = trial_end.isoformat()
        sub.trial_days_remaining = self.TRIAL_DAYS
        sub.limits = PlanLimits.for_tier(PlanTier.PROFESSIONAL)
        sub.features = PlanFeatures.for_tier(PlanTier.PROFESSIONAL)
        sub.updated_at = now.isoformat()

        logger.info(f"Started Professional trial for organization {organization_id}")
        return self._save_subscription(sub)

    def upgrade_plan(
        self,
        organization_id: str,
        tier: PlanTier,
        stripe_customer_id: str = None,
        stripe_subscription_id: str = None,
    ) -> Subscription:
        """Upgrade organization to the specified plan tier."""
        sub = self.get_subscription(organization_id)

        now = datetime.now(timezone.utc)
        period_end = now + timedelta(days=30)

        sub.plan = tier
        sub.status = "active"
        sub.stripe_customer_id = stripe_customer_id or sub.stripe_customer_id
        sub.stripe_subscription_id = stripe_subscription_id or sub.stripe_subscription_id
        sub.current_period_start = now.isoformat()
        sub.current_period_end = period_end.isoformat()
        sub.limits = PlanLimits.for_tier(tier)
        sub.features = PlanFeatures.for_tier(tier)
        sub.updated_at = now.isoformat()

        logger.info(f"Upgraded organization {organization_id} to {tier.value}")
        return self._save_subscription(sub)

    def downgrade_plan(self, organization_id: str, tier: PlanTier = PlanTier.FREE) -> Subscription:
        """Downgrade organization to the specified plan tier (defaults to Free)."""
        sub = self.get_subscription(organization_id)

        sub.plan = tier
        sub.status = "active"
        sub.limits = PlanLimits.for_tier(tier)
        sub.features = PlanFeatures.for_tier(tier)
        sub.updated_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"Downgraded organization {organization_id} to {tier.value}")
        return self._save_subscription(sub)

    def complete_onboarding_step(self, organization_id: str, step: int) -> Subscription:
        """Mark an onboarding step as complete."""
        sub = self.get_subscription(organization_id)

        if step > sub.onboarding_step:
            sub.onboarding_step = step

        # Steps: 1=Welcome, 2=Connect ERP, 3=Configure GL, 4=Quick Tour, 5=Complete
        if step >= 5:
            sub.onboarding_completed = True

        sub.updated_at = datetime.now(timezone.utc).isoformat()
        return self._save_subscription(sub)

    def skip_onboarding(self, organization_id: str) -> Subscription:
        """Skip onboarding flow."""
        sub = self.get_subscription(organization_id)
        sub.onboarding_completed = True
        sub.onboarding_step = 5
        sub.updated_at = datetime.now(timezone.utc).isoformat()
        return self._save_subscription(sub)

    def check_feature_access(self, organization_id: str, feature: str) -> bool:
        """Check if organization has access to a feature."""
        sub = self.get_subscription(organization_id)

        if sub.features is None:
            return False

        return getattr(sub.features, feature, False)

    def _reset_monthly_counters(self, sub: Subscription) -> None:
        """Reset monthly usage counters and update last_reset timestamp."""
        if sub.usage is None:
            sub.usage = UsageStats()
        sub.usage.invoices_this_month = 0
        sub.usage.ai_credits_this_month = 0
        sub.usage.api_calls_today = 0
        sub.usage.last_reset = datetime.now(timezone.utc).isoformat()
        self._save_subscription(sub)

    def check_limit(self, organization_id: str, limit_type: str, current_value: int) -> Dict[str, Any]:
        """Check if organization is within a usage limit."""
        sub = self.get_subscription(organization_id)

        # D10: Reset counters if month has changed
        if sub.usage:
            now = datetime.now(timezone.utc)
            last_reset = sub.usage.last_reset
            if last_reset:
                try:
                    last_dt = datetime.fromisoformat(last_reset.replace("Z", "+00:00"))
                    if last_dt.month != now.month or last_dt.year != now.year:
                        self._reset_monthly_counters(sub)
                        sub = self.get_subscription(organization_id)
                        current_value = 0
                except Exception as exc:
                    logger.error("Monthly usage counter reset failed for org %s: %s", organization_id, exc)

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
        saved = self._save_subscription(sub)
        return saved.usage or UsageStats()

    def consume_ai_credits(
        self, organization_id: str, action: str, amount: int = None
    ) -> Dict[str, Any]:
        """
        Check and consume AI credits for an action.

        Args:
            organization_id: The org consuming credits.
            action: Key from AI_CREDIT_COSTS (e.g. "invoice_extraction").
            amount: Override credit cost (defaults to AI_CREDIT_COSTS lookup).

        Returns:
            {"allowed": bool, "credits_used": int, "credits_remaining": int, ...}
        """
        if amount is None:
            amount = AI_CREDIT_COSTS.get(action, 1)

        sub = self.get_subscription(organization_id)
        if sub.usage is None:
            sub.usage = UsageStats()

        current_credits = sub.usage.ai_credits_this_month
        limit = sub.limits.ai_credits_per_month if sub.limits else 0

        # Unlimited
        if limit == -1:
            sub.usage.ai_credits_this_month = current_credits + amount
            self._save_subscription(sub)
            return {
                "allowed": True,
                "credits_used": amount,
                "credits_remaining": -1,
                "unlimited": True,
                "action": action,
            }

        # Check budget
        if current_credits + amount > limit:
            return {
                "allowed": False,
                "credits_used": 0,
                "credits_remaining": max(0, limit - current_credits),
                "limit": limit,
                "action": action,
                "reason": "ai_credit_limit_reached",
            }

        sub.usage.ai_credits_this_month = current_credits + amount
        self._save_subscription(sub)
        remaining = max(0, limit - (current_credits + amount))

        return {
            "allowed": True,
            "credits_used": amount,
            "credits_remaining": remaining,
            "limit": limit,
            "action": action,
        }

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
