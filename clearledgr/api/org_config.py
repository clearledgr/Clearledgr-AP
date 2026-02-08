"""
Organization Configuration API

Manage per-organization settings:
- GL account mappings
- Confidence thresholds
- Currency/locale settings
- Feature flags
- Payment gateway configuration
"""

import logging
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from clearledgr.core.org_config import (
    OrganizationConfig,
    GLAccountMapping,
    ConfidenceThresholds,
    LocaleSettings,
    FeatureFlags,
    PaymentGatewayConfig,
    get_org_config,
    save_org_config,
    delete_org_config,
    get_or_create_config,
    get_preset_configs,
    create_default_config,
)
from clearledgr.core.auth import get_current_user, TokenData

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["Organization Configuration"])


# ==================== REQUEST MODELS ====================

class GLMappingRequest(BaseModel):
    """Request to update GL account mapping."""
    account_type: str = Field(..., description="Type: cash, revenue, payment_fees, etc.")
    account_code: str = Field(..., description="GL account code")
    account_name: str = Field(..., description="Human-readable name")


class ThresholdsRequest(BaseModel):
    """Request to update confidence thresholds."""
    auto_match: Optional[float] = Field(None, ge=0, le=100)
    review_required: Optional[float] = Field(None, ge=0, le=100)
    reject: Optional[float] = Field(None, ge=0, le=100)
    auto_approve_je: Optional[float] = Field(None, ge=0, le=100)
    critical_amount: Optional[float] = Field(None, ge=0)
    high_amount: Optional[float] = Field(None, ge=0)


class LocaleRequest(BaseModel):
    """Request to update locale settings."""
    default_currency: Optional[str] = Field(None, pattern=r"^[A-Z]{3}$")
    secondary_currencies: Optional[List[str]] = None
    date_format: Optional[str] = None
    number_format: Optional[str] = Field(None, pattern=r"^(european|us)$")
    timezone: Optional[str] = None


class FeaturesRequest(BaseModel):
    """Request to update feature flags."""
    auto_reconciliation: Optional[bool] = None
    auto_categorization: Optional[bool] = None
    slack_notifications: Optional[bool] = None
    email_detection: Optional[bool] = None
    three_way_matching: Optional[bool] = None
    erp_auto_posting: Optional[bool] = None
    ai_explanations: Optional[bool] = None


class PaymentGatewayRequest(BaseModel):
    """Request to configure payment gateway."""
    gateway_type: str = Field(..., pattern=r"^(stripe|paystack|flutterwave)$")
    api_key: Optional[str] = None
    webhook_secret: Optional[str] = None
    enabled: bool = True
    fee_account: Optional[str] = None


class CreateOrgRequest(BaseModel):
    """Request to create a new organization."""
    organization_id: str = Field(..., min_length=1, max_length=50)
    organization_name: str = Field(..., min_length=1, max_length=200)
    currency: str = Field("EUR", pattern=r"^[A-Z]{3}$")
    preset: Optional[str] = Field(None, description="Use preset: african_fintech, european_saas, uk_business")


# ==================== ENDPOINTS ====================

@router.post("/organizations")
async def create_organization(request: CreateOrgRequest):
    """
    Create a new organization with default or preset configuration.
    
    Available presets:
    - african_fintech: NGN currency, Paystack/Flutterwave, 3-way matching
    - european_saas: EUR currency, Stripe, auto-posting enabled
    - uk_business: GBP currency, Stripe
    """
    # Check if already exists
    existing = get_org_config(request.organization_id)
    if existing:
        raise HTTPException(status_code=400, detail="Organization already exists")
    
    # Use preset if specified
    if request.preset:
        presets = get_preset_configs()
        if request.preset not in presets:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown preset. Available: {list(presets.keys())}",
            )
        config = presets[request.preset]
        config.organization_id = request.organization_id
        config.organization_name = request.organization_name
    else:
        config = create_default_config(
            organization_id=request.organization_id,
            organization_name=request.organization_name,
            currency=request.currency,
        )
    
    save_org_config(config)
    
    return {
        "status": "success",
        "message": f"Organization {request.organization_id} created",
        "config": config.to_dict(),
    }


@router.get("/organizations/{organization_id}")
async def get_organization_config(organization_id: str):
    """Get complete configuration for an organization."""
    config = get_org_config(organization_id)
    
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    return {"config": config.to_dict()}


@router.delete("/organizations/{organization_id}")
async def delete_organization(
    organization_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Delete an organization (requires owner role)."""
    if current_user.role not in ["owner", "admin"]:
        raise HTTPException(status_code=403, detail="Only owners can delete organizations")
    
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    delete_org_config(organization_id)
    
    return {"status": "success", "message": f"Organization {organization_id} deleted"}


# ==================== GL MAPPINGS ====================

@router.get("/organizations/{organization_id}/gl-mappings")
async def get_gl_mappings(organization_id: str):
    """Get all GL account mappings for an organization."""
    config = get_or_create_config(organization_id)
    
    return {
        "mappings": {
            k: {
                "account_type": v.account_type,
                "account_code": v.account_code,
                "account_name": v.account_name,
            }
            for k, v in config.gl_mappings.items()
        }
    }


@router.put("/organizations/{organization_id}/gl-mappings/{account_type}")
async def update_gl_mapping(
    organization_id: str,
    account_type: str,
    request: GLMappingRequest,
):
    """Update a GL account mapping."""
    config = get_or_create_config(organization_id)
    
    config.gl_mappings[account_type] = GLAccountMapping(
        account_type=request.account_type,
        account_code=request.account_code,
        account_name=request.account_name,
    )
    
    save_org_config(config)
    
    return {
        "status": "success",
        "mapping": {
            "account_type": request.account_type,
            "account_code": request.account_code,
            "account_name": request.account_name,
        },
    }


@router.delete("/organizations/{organization_id}/gl-mappings/{account_type}")
async def delete_gl_mapping(organization_id: str, account_type: str):
    """Delete a GL account mapping."""
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    if account_type in config.gl_mappings:
        del config.gl_mappings[account_type]
        save_org_config(config)
    
    return {"status": "success"}


# ==================== THRESHOLDS ====================

@router.get("/organizations/{organization_id}/thresholds")
async def get_thresholds(organization_id: str):
    """Get confidence thresholds for an organization."""
    config = get_or_create_config(organization_id)
    
    return {
        "thresholds": {
            "auto_match": config.thresholds.auto_match,
            "review_required": config.thresholds.review_required,
            "reject": config.thresholds.reject,
            "auto_approve_je": config.thresholds.auto_approve_je,
            "critical_amount": config.thresholds.critical_amount,
            "high_amount": config.thresholds.high_amount,
        }
    }


@router.patch("/organizations/{organization_id}/thresholds")
async def update_thresholds(organization_id: str, request: ThresholdsRequest):
    """Update confidence thresholds."""
    config = get_or_create_config(organization_id)
    
    # Update only provided fields
    if request.auto_match is not None:
        config.thresholds.auto_match = request.auto_match
    if request.review_required is not None:
        config.thresholds.review_required = request.review_required
    if request.reject is not None:
        config.thresholds.reject = request.reject
    if request.auto_approve_je is not None:
        config.thresholds.auto_approve_je = request.auto_approve_je
    if request.critical_amount is not None:
        config.thresholds.critical_amount = request.critical_amount
    if request.high_amount is not None:
        config.thresholds.high_amount = request.high_amount
    
    save_org_config(config)
    
    return {"status": "success", "thresholds": get_thresholds(organization_id)}


# ==================== LOCALE ====================

@router.get("/organizations/{organization_id}/locale")
async def get_locale(organization_id: str):
    """Get locale settings for an organization."""
    config = get_or_create_config(organization_id)
    
    return {
        "locale": {
            "default_currency": config.locale.default_currency,
            "secondary_currencies": config.locale.secondary_currencies,
            "date_format": config.locale.date_format,
            "number_format": config.locale.number_format,
            "timezone": config.locale.timezone,
        }
    }


@router.patch("/organizations/{organization_id}/locale")
async def update_locale(organization_id: str, request: LocaleRequest):
    """Update locale settings."""
    config = get_or_create_config(organization_id)
    
    if request.default_currency is not None:
        config.locale.default_currency = request.default_currency
    if request.secondary_currencies is not None:
        config.locale.secondary_currencies = request.secondary_currencies
    if request.date_format is not None:
        config.locale.date_format = request.date_format
    if request.number_format is not None:
        config.locale.number_format = request.number_format
    if request.timezone is not None:
        config.locale.timezone = request.timezone
    
    save_org_config(config)
    
    return {"status": "success", "locale": (await get_locale(organization_id))["locale"]}


# ==================== FEATURES ====================

@router.get("/organizations/{organization_id}/features")
async def get_features(organization_id: str):
    """Get feature flags for an organization."""
    config = get_or_create_config(organization_id)
    
    return {
        "features": {
            "auto_reconciliation": config.features.auto_reconciliation,
            "auto_categorization": config.features.auto_categorization,
            "slack_notifications": config.features.slack_notifications,
            "email_detection": config.features.email_detection,
            "three_way_matching": config.features.three_way_matching,
            "erp_auto_posting": config.features.erp_auto_posting,
            "ai_explanations": config.features.ai_explanations,
        }
    }


@router.patch("/organizations/{organization_id}/features")
async def update_features(organization_id: str, request: FeaturesRequest):
    """Update feature flags."""
    config = get_or_create_config(organization_id)
    
    if request.auto_reconciliation is not None:
        config.features.auto_reconciliation = request.auto_reconciliation
    if request.auto_categorization is not None:
        config.features.auto_categorization = request.auto_categorization
    if request.slack_notifications is not None:
        config.features.slack_notifications = request.slack_notifications
    if request.email_detection is not None:
        config.features.email_detection = request.email_detection
    if request.three_way_matching is not None:
        config.features.three_way_matching = request.three_way_matching
    if request.erp_auto_posting is not None:
        config.features.erp_auto_posting = request.erp_auto_posting
    if request.ai_explanations is not None:
        config.features.ai_explanations = request.ai_explanations
    
    save_org_config(config)
    
    return {"status": "success", "features": (await get_features(organization_id))["features"]}


# ==================== PAYMENT GATEWAYS ====================

@router.get("/organizations/{organization_id}/gateways")
async def get_payment_gateways(organization_id: str):
    """Get payment gateway configurations."""
    config = get_or_create_config(organization_id)
    
    # Don't expose API keys
    return {
        "gateways": {
            k: {
                "gateway_type": v.gateway_type,
                "enabled": v.enabled,
                "fee_account": v.fee_account,
                "has_api_key": bool(v.api_key),
                "has_webhook_secret": bool(v.webhook_secret),
            }
            for k, v in config.payment_gateways.items()
        }
    }


@router.put("/organizations/{organization_id}/gateways/{gateway_type}")
async def configure_payment_gateway(
    organization_id: str,
    gateway_type: str,
    request: PaymentGatewayRequest,
):
    """Configure a payment gateway."""
    if gateway_type not in ["stripe", "paystack", "flutterwave"]:
        raise HTTPException(status_code=400, detail="Invalid gateway type")
    
    config = get_or_create_config(organization_id)
    
    config.payment_gateways[gateway_type] = PaymentGatewayConfig(
        gateway_type=request.gateway_type,
        api_key=request.api_key,
        webhook_secret=request.webhook_secret,
        enabled=request.enabled,
        fee_account=request.fee_account,
    )
    
    save_org_config(config)
    
    return {
        "status": "success",
        "gateway": {
            "gateway_type": gateway_type,
            "enabled": request.enabled,
            "fee_account": request.fee_account,
        },
    }


@router.delete("/organizations/{organization_id}/gateways/{gateway_type}")
async def remove_payment_gateway(organization_id: str, gateway_type: str):
    """Remove a payment gateway configuration."""
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    if gateway_type in config.payment_gateways:
        del config.payment_gateways[gateway_type]
        save_org_config(config)
    
    return {"status": "success"}


# ==================== PRESETS ====================

@router.get("/presets")
async def list_presets():
    """List available configuration presets."""
    presets = get_preset_configs()
    
    return {
        "presets": [
            {
                "id": k,
                "name": v.organization_name,
                "currency": v.locale.default_currency,
                "gateways": list(v.payment_gateways.keys()),
                "features": {
                    "three_way_matching": v.features.three_way_matching,
                    "erp_auto_posting": v.features.erp_auto_posting,
                },
            }
            for k, v in presets.items()
        ]
    }


# ==================== DATA RESIDENCY & GDPR ====================

from clearledgr.core.org_config import DataResidencyConfig, DATA_REGIONS


class DataResidencyRequest(BaseModel):
    """Request to update data residency settings."""
    data_region: Optional[str] = Field(None, description="Data region: eu, uk, us, africa, asia-pacific")
    data_country: Optional[str] = Field(None, pattern=r"^[A-Z]{2}$", description="Specific country code (ISO 3166-1 alpha-2)")
    gdpr_compliant: Optional[bool] = None
    data_retention_days: Optional[int] = Field(None, ge=365, le=3650)
    pii_encryption_enabled: Optional[bool] = None
    dpa_signed: Optional[bool] = None
    deletion_request_enabled: Optional[bool] = None
    data_portability_enabled: Optional[bool] = None
    consent_required: Optional[bool] = None


@router.get("/organizations/{organization_id}/data-residency")
async def get_data_residency(organization_id: str):
    """
    Get data residency and GDPR settings for an organization.
    
    Returns current data storage location, GDPR compliance status,
    and related privacy settings.
    """
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    return {
        "organization_id": organization_id,
        "data_residency": config.data_residency.to_dict(),
        "storage_location": config.data_residency.get_storage_location(),
        "is_eu_resident": config.data_residency.is_eu_data_resident(),
    }


@router.patch("/organizations/{organization_id}/data-residency")
async def update_data_residency(organization_id: str, request: DataResidencyRequest):
    """
    Update data residency and GDPR settings.
    
    **Important:** Changing data_region may require data migration.
    Contact support for region changes on production data.
    
    Available regions:
    - eu: European Union (GDPR compliant)
    - uk: United Kingdom (UK GDPR compliant)
    - us: United States
    - africa: Africa (South Africa, Nigeria, Kenya)
    - asia-pacific: Asia Pacific (Singapore, Australia, Japan)
    """
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    # Validate data region
    if request.data_region and request.data_region not in DATA_REGIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid data region. Must be one of: {', '.join(DATA_REGIONS.keys())}"
        )
    
    # Update fields
    if request.data_region is not None:
        config.data_residency.data_region = request.data_region
    if request.data_country is not None:
        config.data_residency.data_country = request.data_country
    if request.gdpr_compliant is not None:
        config.data_residency.gdpr_compliant = request.gdpr_compliant
    if request.data_retention_days is not None:
        config.data_residency.data_retention_days = request.data_retention_days
    if request.pii_encryption_enabled is not None:
        config.data_residency.pii_encryption_enabled = request.pii_encryption_enabled
    if request.dpa_signed is not None:
        config.data_residency.dpa_signed = request.dpa_signed
        if request.dpa_signed:
            from datetime import datetime
            config.data_residency.dpa_signed_date = datetime.utcnow().isoformat()
    if request.deletion_request_enabled is not None:
        config.data_residency.deletion_request_enabled = request.deletion_request_enabled
    if request.data_portability_enabled is not None:
        config.data_residency.data_portability_enabled = request.data_portability_enabled
    if request.consent_required is not None:
        config.data_residency.consent_required = request.consent_required
    
    save_org_config(config)
    
    return {
        "status": "success",
        "data_residency": config.data_residency.to_dict(),
        "storage_location": config.data_residency.get_storage_location(),
    }


@router.get("/data-regions")
async def list_data_regions():
    """
    List available data regions for data residency.
    
    Returns all supported regions with their compliance information.
    """
    return {
        "regions": [
            {
                "id": region_id,
                **region_info
            }
            for region_id, region_info in DATA_REGIONS.items()
        ]
    }


@router.post("/organizations/{organization_id}/gdpr/data-export-request")
async def request_data_export(organization_id: str):
    """
    Request a data export (GDPR Article 20 - Right to Data Portability).
    
    Initiates an export of all personal data for the organization.
    Export will be available for download within 24 hours.
    """
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    if not config.data_residency.data_portability_enabled:
        raise HTTPException(
            status_code=403,
            detail="Data portability is not enabled for this organization"
        )
    
    # In production, this would queue a background job
    import uuid
    request_id = f"EXPORT-{uuid.uuid4().hex[:8].upper()}"
    
    return {
        "request_id": request_id,
        "status": "queued",
        "estimated_completion": "24 hours",
        "message": "Your data export request has been queued. You will receive a notification when the export is ready.",
    }


@router.post("/organizations/{organization_id}/gdpr/deletion-request")
async def request_data_deletion(
    organization_id: str,
    confirm: bool = Query(False, description="Confirm deletion request")
):
    """
    Request data deletion (GDPR Article 17 - Right to Erasure).
    
    **Warning:** This action is irreversible. All organization data will be
    permanently deleted within 30 days.
    
    Set confirm=true to confirm the deletion request.
    """
    config = get_org_config(organization_id)
    if not config:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    if not config.data_residency.deletion_request_enabled:
        raise HTTPException(
            status_code=403,
            detail="Deletion requests are not enabled for this organization"
        )
    
    if not confirm:
        return {
            "status": "confirmation_required",
            "message": "Data deletion is irreversible. Set confirm=true to proceed.",
            "warning": "All organization data will be permanently deleted within 30 days.",
        }
    
    # In production, this would queue a background job with a 30-day grace period
    import uuid
    request_id = f"DELETE-{uuid.uuid4().hex[:8].upper()}"
    
    return {
        "request_id": request_id,
        "status": "queued",
        "grace_period": "30 days",
        "message": "Your deletion request has been queued. Data will be permanently deleted after a 30-day grace period. Contact support to cancel.",
    }
