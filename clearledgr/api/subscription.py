"""
Subscription API Endpoints

Handles:
- Get subscription status
- Start trial
- Complete onboarding steps
- Check feature access
- Usage tracking
"""

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from clearledgr.services.subscription import (
    get_subscription_service,
    PlanTier,
    Subscription,
)

router = APIRouter(prefix="/subscription", tags=["Subscription"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class StartTrialRequest(BaseModel):
    """Request to start a trial."""
    organization_id: Optional[str] = None


class OnboardingStepRequest(BaseModel):
    """Request to complete an onboarding step."""
    step: int = Field(..., ge=1, le=5, description="Onboarding step (1-5)")


class FeatureCheckRequest(BaseModel):
    """Request to check feature access."""
    feature: str


class UsageIncrementRequest(BaseModel):
    """Request to increment usage."""
    usage_type: str = Field(..., description="Type: invoices_this_month, api_calls_today, ai_extractions_this_month")
    amount: int = Field(1, ge=1)


class SubscriptionResponse(BaseModel):
    """Subscription status response."""
    organization_id: str
    plan: str
    plan_display: str
    status: str
    is_trial: bool
    trial_days_remaining: int
    trial_ends_at: Optional[str]
    onboarding_completed: bool
    onboarding_step: int
    limits: dict
    features: dict
    usage: dict


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/status")
async def get_subscription_status(
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """
    Get current subscription status for an organization.
    
    Returns plan details, trial status, limits, features, and usage.
    """
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    sub = service.get_subscription(org_id)
    
    plan_display = {
        PlanTier.FREE: "Free",
        PlanTier.TRIAL: "Pro Trial",
        PlanTier.PRO: "Pro",
        PlanTier.ENTERPRISE: "Enterprise",
    }
    
    return {
        "organization_id": sub.organization_id,
        "plan": sub.plan.value,
        "plan_display": plan_display.get(sub.plan, "Free"),
        "status": sub.status,
        "is_trial": sub.status == "trialing",
        "trial_days_remaining": sub.trial_days_remaining,
        "trial_started_at": sub.trial_started_at,
        "trial_ends_at": sub.trial_ends_at,
        "billing_cycle": sub.billing_cycle,
        "current_period_end": sub.current_period_end,
        "onboarding_completed": sub.onboarding_completed,
        "onboarding_step": sub.onboarding_step,
        "limits": sub.limits.__dict__ if sub.limits else {},
        "features": sub.features.__dict__ if sub.features else {},
        "usage": sub.usage.to_dict() if sub.usage else {},
        "created_at": sub.created_at,
    }


@router.post("/trial/start")
async def start_trial(
    request: StartTrialRequest = None,
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """
    Start a 14-day Pro trial for the organization.
    
    Trial includes full access to Pro features.
    """
    org_id = (request and request.organization_id) or x_organization_id or "default"
    service = get_subscription_service()
    
    sub = service.get_subscription(org_id)
    if sub.trial_started_at:
        return {
            "success": False,
            "message": "Trial already used",
            "subscription": sub.to_dict(),
        }
    
    sub = service.start_trial(org_id)
    
    return {
        "success": True,
        "message": f"Started 14-day Pro trial. Expires on {sub.trial_ends_at[:10]}",
        "subscription": sub.to_dict(),
    }


@router.post("/onboarding/step")
async def complete_onboarding_step(
    request: OnboardingStepRequest,
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """
    Mark an onboarding step as complete.
    
    Steps:
    1. Welcome & account setup
    2. Connect ERP
    3. Configure GL codes
    4. Quick product tour
    5. Complete
    """
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    sub = service.complete_onboarding_step(org_id, request.step)
    
    step_names = {
        1: "Welcome",
        2: "ERP Connection",
        3: "GL Configuration",
        4: "Product Tour",
        5: "Complete",
    }
    
    return {
        "success": True,
        "step": request.step,
        "step_name": step_names.get(request.step, "Unknown"),
        "onboarding_completed": sub.onboarding_completed,
        "onboarding_step": sub.onboarding_step,
    }


@router.post("/onboarding/skip")
async def skip_onboarding(
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """Skip the onboarding flow."""
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    sub = service.skip_onboarding(org_id)
    
    return {
        "success": True,
        "message": "Onboarding skipped",
        "onboarding_completed": sub.onboarding_completed,
    }


@router.get("/onboarding/status")
async def get_onboarding_status(
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """Get current onboarding status."""
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    sub = service.get_subscription(org_id)
    
    steps = [
        {"step": 1, "name": "Welcome", "description": "Set up your account", "completed": sub.onboarding_step >= 1},
        {"step": 2, "name": "Connect ERP", "description": "Link QuickBooks, Xero, or NetSuite", "completed": sub.onboarding_step >= 2},
        {"step": 3, "name": "GL Codes", "description": "Configure your chart of accounts", "completed": sub.onboarding_step >= 3},
        {"step": 4, "name": "Quick Tour", "description": "Learn the key features", "completed": sub.onboarding_step >= 4},
        {"step": 5, "name": "Ready!", "description": "Start processing invoices", "completed": sub.onboarding_step >= 5},
    ]
    
    return {
        "onboarding_completed": sub.onboarding_completed,
        "current_step": sub.onboarding_step,
        "steps": steps,
    }


@router.get("/features/{feature}")
async def check_feature_access(
    feature: str,
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """
    Check if the organization has access to a specific feature.
    
    Features: ai_categorization, three_way_matching, erp_auto_posting,
    multi_currency, advanced_analytics, api_access, slack_integration, etc.
    """
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    
    has_access = service.check_feature_access(org_id, feature)
    sub = service.get_subscription(org_id)
    
    return {
        "feature": feature,
        "has_access": has_access,
        "plan": sub.plan.value,
        "upgrade_required": not has_access,
    }


@router.get("/limits/{limit_type}")
async def check_limit(
    limit_type: str,
    current_value: int = Query(0),
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """
    Check if the organization is within a usage limit.
    
    Limit types: invoices_per_month, vendors, users, erp_connections,
    api_calls_per_day, ai_extractions_per_month
    """
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    
    result = service.check_limit(org_id, limit_type, current_value)
    
    return {
        "limit_type": limit_type,
        **result,
    }


@router.post("/usage/increment")
async def increment_usage(
    request: UsageIncrementRequest,
    x_organization_id: str = Header(None, alias="X-Organization-ID")
):
    """Increment a usage counter."""
    org_id = x_organization_id or "default"
    service = get_subscription_service()
    
    usage = service.increment_usage(org_id, request.usage_type, request.amount)
    
    return {
        "success": True,
        "usage_type": request.usage_type,
        "new_value": getattr(usage, request.usage_type, 0),
        "usage": usage.to_dict(),
    }


@router.get("/plans")
async def get_available_plans():
    """Get all available subscription plans with their features and limits."""
    from clearledgr.services.subscription import PlanLimits, PlanFeatures
    
    plans = []
    for tier in PlanTier:
        limits = PlanLimits.for_tier(tier)
        features = PlanFeatures.for_tier(tier)
        
        plan_info = {
            "id": tier.value,
            "name": {
                PlanTier.FREE: "Free",
                PlanTier.TRIAL: "Pro Trial",
                PlanTier.PRO: "Pro",
                PlanTier.ENTERPRISE: "Enterprise",
            }.get(tier, tier.value),
            "price": {
                PlanTier.FREE: 0,
                PlanTier.TRIAL: 0,
                PlanTier.PRO: 49,
                PlanTier.ENTERPRISE: 199,
            }.get(tier, 0),
            "billing": "monthly",
            "description": {
                PlanTier.FREE: "Get started with basic AP automation",
                PlanTier.TRIAL: "Full Pro features for 14 days",
                PlanTier.PRO: "Everything you need for growing teams",
                PlanTier.ENTERPRISE: "Advanced features for large organizations",
            }.get(tier, ""),
            "limits": limits.__dict__,
            "features": features.__dict__,
            "highlighted_features": _get_highlighted_features(tier),
        }
        plans.append(plan_info)
    
    return {"plans": plans}


def _get_highlighted_features(tier: PlanTier) -> List[str]:
    """Get highlighted features for a plan tier."""
    highlights = {
        PlanTier.FREE: [
            "25 invoices/month",
            "1 ERP connection",
            "Basic email scanning",
            "Invoice extraction",
        ],
        PlanTier.TRIAL: [
            "500 invoices/month",
            "3 ERP connections",
            "AI categorization",
            "3-way matching",
            "All Pro features for 14 days",
        ],
        PlanTier.PRO: [
            "500 invoices/month",
            "3 ERP connections",
            "AI categorization",
            "3-way matching",
            "Multi-currency support",
            "Slack integration",
            "Priority support",
        ],
        PlanTier.ENTERPRISE: [
            "Unlimited invoices",
            "Unlimited ERP connections",
            "Custom workflows",
            "SSO/SAML",
            "Dedicated support",
            "SLA guarantee",
        ],
    }
    return highlights.get(tier, [])
