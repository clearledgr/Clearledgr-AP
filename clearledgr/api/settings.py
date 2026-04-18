"""
Organization Settings API

Manages configuration for:
- Approval thresholds
- Slack channels
- GL account mappings
- Auto-approve rules
- Multi-level approval routing
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from clearledgr.core.database import get_db
from clearledgr.core.auth import get_current_user, TokenData

logger = logging.getLogger(__name__)


def _enforce_deployment_window():
    """§7.7: Block policy/model changes outside deployment window (Tue-Thu 10am-2pm UK).

    This is a soft guard — returns a warning if outside the window.
    Callers can choose to enforce or log.
    """
    try:
        from clearledgr.core.deployment_window import is_deployment_allowed
        result = is_deployment_allowed()
        if not result.get("allowed"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "deployment_window_closed",
                    "reason": result.get("reason"),
                    "uk_time": result.get("uk_time"),
                },
            )
    except ImportError:
        pass  # deployment_window module not available

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


# ==================== DATA MODELS ====================

class ApprovalTarget(BaseModel):
    """Structured approver identity for Slack-routed rules."""
    email: EmailStr
    display_name: Optional[str] = None
    slack_user_id: Optional[str] = None
    slack_resolution: Optional[str] = None


class ApprovalThreshold(BaseModel):
    """Approval threshold configuration."""
    min_amount: float = 0
    max_amount: Optional[float] = None  # None = unlimited
    approver_channel: str  # Slack channel
    approvers: List[str] = Field(default_factory=list)  # Explicit approver emails
    approver_targets: List[ApprovalTarget] = Field(default_factory=list)
    approver_role: Optional[str] = None  # e.g., "manager", "vp", "cfo"
    auto_approve: bool = False
    confidence_threshold: float = 0.95
    gl_codes: List[str] = Field(default_factory=list)  # GL code filter
    departments: List[str] = Field(default_factory=list)  # Department/cost center filter
    vendors: List[str] = Field(default_factory=list)  # Vendor name filter
    entities: List[str] = Field(default_factory=list)  # Legal entity filter
    approval_type: str = "any"  # "any" = first approver wins, "all" = unanimous


class SlackChannelConfig(BaseModel):
    """Slack channel configuration."""
    invoices: str = "#finance-approvals"
    expenses: str = "#expense-approvals"
    exceptions: str = "#finance-exceptions"
    notifications: str = "#finance-notifications"


class GLAccountMapping(BaseModel):
    """GL account mapping for a vendor or category."""
    vendor_pattern: Optional[str] = None  # Regex pattern for vendor name
    category: Optional[str] = None  # e.g., "software", "travel"
    expense_account_id: str
    expense_account_name: str


class AutoApproveRule(BaseModel):
    """Auto-approve rule."""
    name: str
    enabled: bool = True
    conditions: Dict[str, Any]  # e.g., {"vendor": "Stripe", "max_amount": 1000}
    confidence_threshold: float = 0.90


class OrganizationSettings(BaseModel):
    """Complete organization settings."""
    organization_id: str
    
    # Approval settings
    auto_approve_threshold: float = Field(default=0.95, ge=0, le=1)
    require_receipt_above: float = Field(default=25.0, ge=0)
    
    # Slack channels
    slack_channels: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    
    # Approval thresholds (amount-based routing)
    approval_thresholds: List[ApprovalThreshold] = Field(default_factory=list)
    
    # GL account mappings
    gl_mappings: List[GLAccountMapping] = Field(default_factory=list)
    
    # Auto-approve rules
    auto_approve_rules: List[AutoApproveRule] = Field(default_factory=list)
    
    # Default accounts
    default_expense_account: Optional[str] = None
    default_ap_account: Optional[str] = None
    
    # Notifications
    notify_on_auto_approve: bool = True
    notify_on_exception: bool = True
    daily_digest: bool = True
    daily_digest_time: str = "09:00"


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    auto_approve_threshold: Optional[float] = None
    require_receipt_above: Optional[float] = None
    slack_channels: Optional[SlackChannelConfig] = None
    default_expense_account: Optional[str] = None
    default_ap_account: Optional[str] = None
    notify_on_auto_approve: Optional[bool] = None
    daily_digest: Optional[bool] = None


# ==================== DEFAULT SETTINGS ====================

DEFAULT_APPROVAL_THRESHOLDS = [
    ApprovalThreshold(
        min_amount=0,
        max_amount=500,
        approver_channel="#finance-approvals",
        approver_role="accountant",
        auto_approve=True,
        confidence_threshold=0.95,
    ),
    ApprovalThreshold(
        min_amount=500,
        max_amount=5000,
        approver_channel="#finance-approvals",
        approver_role="manager",
        auto_approve=False,
        confidence_threshold=0.95,
    ),
    ApprovalThreshold(
        min_amount=5000,
        max_amount=25000,
        approver_channel="#finance-leadership",
        approver_role="director",
        auto_approve=False,
        confidence_threshold=0.98,
    ),
    ApprovalThreshold(
        min_amount=25000,
        max_amount=None,
        approver_channel="#executive-approvals",
        approver_role="cfo",
        auto_approve=False,
        confidence_threshold=0.99,
    ),
]

DEFAULT_AUTO_APPROVE_RULES = [
    AutoApproveRule(
        name="recurring_subscriptions",
        enabled=True,
        conditions={"is_recurring": True, "amount_variance_pct": 5},
        confidence_threshold=0.90,
    ),
    AutoApproveRule(
        name="known_vendors",
        enabled=True,
        conditions={"vendor_history_count": 3, "max_amount": 1000},
        confidence_threshold=0.85,
    ),
]


# ==================== ENDPOINTS ====================

@router.get("/{organization_id}", response_model=OrganizationSettings)
async def get_settings(organization_id: str):
    """
    Get organization settings.
    
    Returns current configuration or defaults if not set.
    """
    db = get_db()
    org = db.get_organization(organization_id) or db.ensure_organization(
        organization_id,
        organization_name=organization_id,
    )
    
    # Parse stored settings
    stored_settings = org.get("settings", {})
    if isinstance(stored_settings, str):
        stored_settings = json.loads(stored_settings) if stored_settings else {}
    
    # Merge with defaults
    settings = OrganizationSettings(
        organization_id=organization_id,
        **stored_settings,
    )
    
    # Apply defaults if not set
    if not settings.approval_thresholds:
        settings.approval_thresholds = DEFAULT_APPROVAL_THRESHOLDS
    
    if not settings.auto_approve_rules:
        settings.auto_approve_rules = DEFAULT_AUTO_APPROVE_RULES
    
    return settings


@router.put("/{organization_id}")
async def update_settings(organization_id: str, request: UpdateSettingsRequest):
    """
    Update organization settings.

    §7.7: Blocked outside deployment window (Tue-Thu 10am-2pm UK).
    """
    _enforce_deployment_window()
    db = get_db()
    org = db.get_organization(organization_id) or db.ensure_organization(
        organization_id,
        organization_name=organization_id,
    )
    
    # Get current settings
    current = org.get("settings", {})
    if isinstance(current, str):
        current = json.loads(current) if current else {}
    
    # Update with new values
    updates = request.model_dump(exclude_none=True)
    
    # Handle nested SlackChannelConfig
    if "slack_channels" in updates and updates["slack_channels"]:
        updates["slack_channels"] = updates["slack_channels"].model_dump()
    
    current.update(updates)
    
    # Save
    db.update_organization(organization_id, settings=current)
    
    return {"success": True, "updated": list(updates.keys())}


@router.put("/{organization_id}/approval-thresholds")
async def update_approval_thresholds(
    organization_id: str,
    request: Request,
):
    """
    Update approval thresholds for amount-based routing.

    Accepts either a list of ApprovalThreshold objects (full format)
    or a simplified dict with auto_approve_limit / manager_approval_limit /
    executive_approval_limit keys (shorthand format).
    """
    body = await request.json()

    db = get_db()
    org = db.get_organization(organization_id)

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get current settings
    current = org.get("settings", {})
    if isinstance(current, str):
        current = json.loads(current) if current else {}

    if isinstance(body, list):
        # Full ApprovalThreshold list format
        try:
            thresholds = [ApprovalThreshold(**item) for item in body]
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        sorted_thresholds = sorted(thresholds, key=lambda t: t.min_amount)
        for i, threshold in enumerate(sorted_thresholds):
            if i > 0:
                prev = sorted_thresholds[i - 1]
                if prev.max_amount and prev.max_amount != threshold.min_amount:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Gap between thresholds: {prev.max_amount} to {threshold.min_amount}",
                    )
        current["approval_thresholds"] = [t.model_dump() for t in thresholds]
        db.update_organization(organization_id, settings=current)
        return {"success": True, "thresholds": len(thresholds)}
    elif isinstance(body, dict):
        # Simplified shorthand dict format — merge directly into settings
        for key, value in body.items():
            current[key] = value
        db.update_organization(organization_id, settings=current)
        return {"success": True, "updated": list(body.keys())}
    else:
        raise HTTPException(status_code=422, detail="Invalid body format")


@router.get("/{organization_id}/gl-mappings")
async def get_gl_mappings(organization_id: str):
    """Get GL account mappings."""
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    return {
        "organization_id": organization_id,
        "mappings": settings.get("gl_mappings", []),
    }


@router.post("/{organization_id}/gl-mappings")
async def add_gl_mapping(organization_id: str, mapping: GLAccountMapping):
    """Add a GL account mapping."""
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    mappings = settings.get("gl_mappings", [])
    mappings.append(mapping.model_dump())
    settings["gl_mappings"] = mappings
    
    db.update_organization(organization_id, settings=settings)
    
    return {"success": True, "mappings": len(mappings)}


@router.delete("/{organization_id}/gl-mappings/{index}")
async def delete_gl_mapping(organization_id: str, index: int):
    """Delete a GL account mapping by index."""
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    mappings = settings.get("gl_mappings", [])
    
    if index < 0 or index >= len(mappings):
        raise HTTPException(status_code=404, detail="Mapping not found")
    
    mappings.pop(index)
    settings["gl_mappings"] = mappings
    
    db.update_organization(organization_id, settings=settings)
    
    return {"success": True, "mappings": len(mappings)}


@router.get("/{organization_id}/auto-approve-rules")
async def get_auto_approve_rules(organization_id: str):
    """Get auto-approve rules."""
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    rules = settings.get("auto_approve_rules", DEFAULT_AUTO_APPROVE_RULES)
    
    return {
        "organization_id": organization_id,
        "rules": rules,
    }


@router.put("/{organization_id}/auto-approve-rules")
async def update_auto_approve_rules(
    organization_id: str,
    rules: List[AutoApproveRule],
):
    """Update auto-approve rules."""
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    settings["auto_approve_rules"] = [r.model_dump() for r in rules]
    
    db.update_organization(organization_id, settings=settings)
    
    return {"success": True, "rules": len(rules)}


# ==================== HELPER FUNCTIONS ====================

def get_approval_channel(organization_id: str, amount: float) -> str:
    """
    Get the appropriate Slack channel for an approval based on amount.
    
    Used by invoice workflow to route approvals.
    """
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        return "#finance-approvals"  # Default
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    thresholds = settings.get("approval_thresholds", [])
    
    if not thresholds:
        thresholds = [t.model_dump() for t in DEFAULT_APPROVAL_THRESHOLDS]
    
    for threshold in thresholds:
        min_amt = threshold.get("min_amount", 0)
        max_amt = threshold.get("max_amount")
        
        if amount >= min_amt and (max_amt is None or amount < max_amt):
            return threshold.get("approver_channel", "#finance-approvals")
    
    return "#finance-approvals"


def should_auto_approve(
    organization_id: str,
    amount: float,
    confidence: float,
    is_recurring: bool = False,
    vendor_history_count: int = 0,
) -> bool:
    """
    Check if an invoice should be auto-approved based on settings.
    
    Used by invoice workflow.
    """
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        return confidence >= 0.95  # Default threshold
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    # Check approval thresholds first
    thresholds = settings.get("approval_thresholds", [])
    for threshold in thresholds:
        min_amt = threshold.get("min_amount", 0)
        max_amt = threshold.get("max_amount")
        
        if amount >= min_amt and (max_amt is None or amount < max_amt):
            if not threshold.get("auto_approve", False):
                return False
            if confidence < threshold.get("confidence_threshold", 0.95):
                return False
            break
    
    # Check auto-approve rules
    rules = settings.get("auto_approve_rules", [])
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        
        conditions = rule.get("conditions", {})
        rule_threshold = rule.get("confidence_threshold", 0.95)
        
        # Check recurring rule
        if conditions.get("is_recurring") and is_recurring:
            if confidence >= rule_threshold:
                return True
        
        # Check known vendor rule
        if conditions.get("vendor_history_count"):
            if vendor_history_count >= conditions["vendor_history_count"]:
                if amount <= conditions.get("max_amount", float("inf")):
                    if confidence >= rule_threshold:
                        return True
    
    # Default: check base threshold
    base_threshold = settings.get("auto_approve_threshold", 0.95)
    return confidence >= base_threshold


def get_gl_account_for_vendor(organization_id: str, vendor: str, category: str = None) -> Optional[str]:
    """
    Get mapped GL account for a vendor or category.
    
    Used when posting bills to ERP.
    """
    import re
    
    db = get_db()
    org = db.get_organization(organization_id)
    
    if not org:
        return None
    
    settings = org.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    
    mappings = settings.get("gl_mappings", [])
    
    for mapping in mappings:
        # Check vendor pattern
        if mapping.get("vendor_pattern"):
            pattern = mapping["vendor_pattern"]
            if re.search(pattern, vendor, re.IGNORECASE):
                return mapping.get("expense_account_id")
        
        # Check category
        if mapping.get("category") and category:
            if mapping["category"].lower() == category.lower():
                return mapping.get("expense_account_id")
    
    # Return default
    return settings.get("default_expense_account")


# ==================== MIGRATION FROM EXISTING TOOLS (§3) ====================


@router.post("/{organization_id}/migration/start-parallel")
async def start_parallel_mode(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """§3 Migration: Start parallel running mode.

    Clearledgr runs alongside the existing AP system. Autonomous actions
    are suppressed — the agent provides suggestions only. The AP Manager
    compares results for a minimum of 2 weeks before deciding to go live.
    """
    from clearledgr.core.auth import has_financial_controller
    if not has_financial_controller(user):
        raise HTTPException(status_code=403, detail="financial_controller_required")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")

    org = db.get_organization(organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")

    db.update_organization(organization_id, migration_status="parallel", parallel_start_date=now)

    db.append_audit_event({
        "event_type": "migration_parallel_started",
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "source": "settings_api",
        "payload_json": {"parallel_start_date": now},
    })

    return {
        "status": "parallel",
        "parallel_start_date": now,
        "message": "Parallel mode active. Autonomous actions suppressed. Compare results with your existing system.",
    }


@router.post("/{organization_id}/migration/cutover")
async def migration_cutover(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """§3 Migration: Go live — Clearledgr becomes the primary AP workflow.

    "The cutover decision is logged in Settings. It is timestamped,
    attributed to the AP Manager who made it, and recorded in the audit trail."
    """
    from clearledgr.core.auth import has_financial_controller
    if not has_financial_controller(user):
        raise HTTPException(status_code=403, detail="financial_controller_required")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")

    org = db.get_organization(organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")

    db.update_organization(
        organization_id,
        migration_status="live",
        cutover_decision_at=now,
        cutover_decision_by=actor_id,
    )

    db.append_audit_event({
        "event_type": "migration_cutover_decision",
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "source": "settings_api",
        "payload_json": {
            "cutover_decision_at": now,
            "cutover_decision_by": actor_id,
            "previous_status": org.get("migration_status", "parallel"),
        },
    })

    return {
        "status": "live",
        "cutover_decision_at": now,
        "cutover_decision_by": actor_id,
        "message": "Clearledgr is now the primary AP workflow. Autonomous actions enabled.",
    }


@router.get("/{organization_id}/migration/status")
async def get_migration_status(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Get current migration status for an organization."""
    db = get_db()
    org = db.get_organization(organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")

    status = org.get("migration_status", "live")
    parallel_start = org.get("parallel_start_date")
    cutover_at = org.get("cutover_decision_at")
    cutover_by = org.get("cutover_decision_by")

    days_in_parallel = 0
    if status == "parallel" and parallel_start:
        try:
            start = datetime.fromisoformat(parallel_start.replace("Z", "+00:00"))
            days_in_parallel = (datetime.now(timezone.utc) - start).days
        except (ValueError, TypeError):
            pass

    return {
        "migration_status": status,
        "parallel_start_date": parallel_start,
        "days_in_parallel": days_in_parallel,
        "cutover_decision_at": cutover_at,
        "cutover_decision_by": cutover_by,
        "minimum_parallel_days": 14,
        "can_cutover": status == "parallel" and days_in_parallel >= 14,
    }


# ==================== SHADOW MODE REPORT — §7.7 ====================

@router.get("/{organization_id}/shadow-mode-report")
async def get_shadow_mode_report_endpoint(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Get shadow model promotion readiness report (§7.7)."""
    from clearledgr.services.shadow_mode import get_shadow_mode_report

    db = get_db()
    return get_shadow_mode_report(db, organization_id=organization_id)
