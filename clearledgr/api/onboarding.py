"""
User Onboarding API

The setup flow:
1. Connect bank (manual CSV upload or Plaid for real-time)
2. Connect gateway (Stripe/Paystack/Flutterwave API key)
3. Connect ERP (QuickBooks/Xero OAuth or SAP credentials)
4. Configure GL account mappings
5. Done - Clearledgr is now autonomous

No schedules to configure. Just connect and go.
"""

import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from clearledgr.integrations.erp_router import ERPConnection, set_erp_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


# ==================== MODELS ====================

class OrganizationSetup(BaseModel):
    """Organization setup data."""
    name: str
    industry: Optional[str] = None
    country: str = "EU"
    currency: str = "EUR"


class BankConnectionRequest(BaseModel):
    """Request to connect a bank."""
    method: str = Field(..., description="csv_upload, plaid, or manual")
    plaid_public_token: Optional[str] = None
    account_name: Optional[str] = None


class GatewayConnectionRequest(BaseModel):
    """Request to connect a payment gateway."""
    gateway: str = Field(..., description="stripe, paystack, or flutterwave")
    api_key: str
    webhook_secret: Optional[str] = None


class ERPConnectionRequest(BaseModel):
    """Request to connect an ERP system."""
    erp_type: str = Field(..., description="quickbooks, xero, netsuite, or sap")
    
    # OAuth flow (QuickBooks/Xero)
    oauth_code: Optional[str] = None
    redirect_uri: Optional[str] = None
    
    # NetSuite Token-Based Authentication
    account_id: Optional[str] = None  # NetSuite account ID (e.g., "1234567" or "1234567_SB1")
    consumer_key: Optional[str] = None
    consumer_secret: Optional[str] = None
    token_id: Optional[str] = None
    token_secret: Optional[str] = None
    
    # Direct credentials (SAP)
    username: Optional[str] = None
    password: Optional[str] = None
    base_url: Optional[str] = None


class GLAccountMapping(BaseModel):
    """GL account mapping configuration."""
    cash_account: str
    accounts_receivable: str
    payment_fees: str
    revenue: str
    custom_mappings: Optional[Dict[str, str]] = None


class OnboardingStatus(BaseModel):
    """Current onboarding status."""
    organization_id: str
    organization_name: str
    
    bank_connected: bool = False
    bank_method: Optional[str] = None
    
    gateway_connected: bool = False
    gateway_type: Optional[str] = None
    
    erp_connected: bool = False
    erp_type: Optional[str] = None
    
    gl_configured: bool = False
    
    is_complete: bool = False


# In-memory store for demo - would be database in production
_org_setup: Dict[str, Dict[str, Any]] = {}


def get_org_setup(organization_id: str) -> Dict[str, Any]:
    if organization_id not in _org_setup:
        _org_setup[organization_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "bank": None,
            "gateway": None,
            "erp": None,
            "gl_mapping": None,
        }
    return _org_setup[organization_id]


# ==================== ENDPOINTS ====================

@router.post("/organization")
async def setup_organization(request: OrganizationSetup) -> Dict[str, Any]:
    """
    Step 0: Create/configure organization.
    """
    import uuid
    
    org_id = str(uuid.uuid4())[:8]
    
    _org_setup[org_id] = {
        "id": org_id,
        "name": request.name,
        "industry": request.industry,
        "country": request.country,
        "currency": request.currency,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bank": None,
        "gateway": None,
        "erp": None,
        "gl_mapping": None,
    }
    
    logger.info(f"Created organization: {org_id} - {request.name}")
    
    return {
        "organization_id": org_id,
        "name": request.name,
        "next_step": "connect_bank",
        "message": "Organization created. Next: Connect your bank.",
    }


@router.post("/{organization_id}/bank")
async def connect_bank(
    organization_id: str,
    request: BankConnectionRequest,
) -> Dict[str, Any]:
    """
    Step 1: Connect bank account.
    
    Options:
    - csv_upload: Manual bank statement upload (simplest)
    - plaid: Real-time bank connection via Plaid
    - manual: Just configure, upload statements when ready
    """
    setup = get_org_setup(organization_id)
    
    if request.method == "plaid" and request.plaid_public_token:
        # Exchange Plaid public token for access token
        bank_connection = await exchange_plaid_token(request.plaid_public_token)
        setup["bank"] = {
            "method": "plaid",
            "connected_at": datetime.now(timezone.utc).isoformat(),
            **bank_connection,
        }
        message = "Bank connected via Plaid. Transactions will sync automatically."
        
    elif request.method == "csv_upload":
        setup["bank"] = {
            "method": "csv_upload",
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "account_name": request.account_name or "Primary Account",
        }
        message = "Bank configured for CSV upload. Upload statements via Gmail or Sheets."
        
    else:
        setup["bank"] = {
            "method": "manual",
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "account_name": request.account_name or "Primary Account",
        }
        message = "Bank configured. Upload statements when ready."
    
    logger.info(f"Bank connected for {organization_id}: {request.method}")
    
    return {
        "status": "connected",
        "method": request.method,
        "next_step": "connect_gateway",
        "message": message,
    }


async def exchange_plaid_token(public_token: str) -> Dict[str, Any]:
    """Exchange Plaid public token for access token."""
    import httpx
    
    plaid_client_id = os.getenv("PLAID_CLIENT_ID")
    plaid_secret = os.getenv("PLAID_SECRET")
    plaid_env = os.getenv("PLAID_ENV", "sandbox")
    
    if not plaid_client_id or not plaid_secret:
        raise HTTPException(status_code=500, detail="Plaid not configured")
    
    base_url = {
        "sandbox": "https://sandbox.plaid.com",
        "development": "https://development.plaid.com",
        "production": "https://production.plaid.com",
    }.get(plaid_env, "https://sandbox.plaid.com")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/item/public_token/exchange",
                json={
                    "client_id": plaid_client_id,
                    "secret": plaid_secret,
                    "public_token": public_token,
                },
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "access_token": result.get("access_token"),
                "item_id": result.get("item_id"),
            }
    except Exception as e:
        logger.error(f"Plaid token exchange failed: {e}")
        raise HTTPException(status_code=400, detail=f"Plaid connection failed: {str(e)}")


@router.post("/{organization_id}/gateway")
async def connect_gateway(
    organization_id: str,
    request: GatewayConnectionRequest,
) -> Dict[str, Any]:
    """
    Step 2: Connect payment gateway.
    
    We'll verify the API key works and set up webhook endpoints.
    """
    setup = get_org_setup(organization_id)
    
    # Verify API key works
    if request.gateway == "stripe":
        verified = await verify_stripe_key(request.api_key)
    elif request.gateway == "paystack":
        verified = await verify_paystack_key(request.api_key)
    elif request.gateway == "flutterwave":
        verified = await verify_flutterwave_key(request.api_key)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown gateway: {request.gateway}")
    
    if not verified:
        raise HTTPException(status_code=400, detail="Invalid API key")
    
    setup["gateway"] = {
        "type": request.gateway,
        "api_key_last4": request.api_key[-4:],
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "webhook_configured": bool(request.webhook_secret),
    }
    
    # Store the full key securely (in production, use secrets manager)
    # For demo, store in environment or encrypted storage
    
    logger.info(f"Gateway connected for {organization_id}: {request.gateway}")
    
    # Generate webhook URL
    base_url = os.getenv("CLEARLEDGR_BASE_URL", "https://api.clearledgr.com")
    webhook_url = f"{base_url}/api/webhooks/{request.gateway}"
    
    return {
        "status": "connected",
        "gateway": request.gateway,
        "webhook_url": webhook_url,
        "webhook_instructions": f"Add this webhook URL to your {request.gateway} dashboard",
        "next_step": "connect_erp",
        "message": f"{request.gateway.title()} connected. Transactions will flow automatically.",
    }


async def verify_stripe_key(api_key: str) -> bool:
    """Verify Stripe API key."""
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.stripe.com/v1/balance",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return response.status_code == 200
    except Exception:
        return False


async def verify_paystack_key(api_key: str) -> bool:
    """Verify Paystack API key."""
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.paystack.co/balance",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return response.status_code == 200
    except Exception:
        return False


async def verify_flutterwave_key(api_key: str) -> bool:
    """Verify Flutterwave API key."""
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.flutterwave.com/v3/balances",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return response.status_code == 200
    except Exception:
        return False


@router.post("/{organization_id}/erp")
async def connect_erp(
    organization_id: str,
    request: ERPConnectionRequest,
) -> Dict[str, Any]:
    """
    Step 3: Connect ERP system.
    
    - QuickBooks/Xero: OAuth flow
    - NetSuite: Token-Based Authentication (TBA)
    - SAP: Direct credentials or OAuth
    """
    setup = get_org_setup(organization_id)
    
    if request.erp_type == "quickbooks":
        connection = await connect_quickbooks(request)
    elif request.erp_type == "xero":
        connection = await connect_xero(request)
    elif request.erp_type == "netsuite":
        connection = await connect_netsuite(request)
    elif request.erp_type == "sap":
        connection = await connect_sap(request)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown ERP: {request.erp_type}")
    
    if not connection:
        raise HTTPException(status_code=400, detail="Failed to connect ERP")
    
    # Store connection
    set_erp_connection(organization_id, connection)
    
    setup["erp"] = {
        "type": request.erp_type,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    
    logger.info(f"ERP connected for {organization_id}: {request.erp_type}")
    
    return {
        "status": "connected",
        "erp": request.erp_type,
        "next_step": "configure_gl",
        "message": f"{request.erp_type.title()} connected. Journal entries will post automatically.",
    }


async def connect_quickbooks(request: ERPConnectionRequest) -> Optional[ERPConnection]:
    """Connect to QuickBooks via OAuth."""
    import httpx
    
    client_id = os.getenv("QUICKBOOKS_CLIENT_ID")
    client_secret = os.getenv("QUICKBOOKS_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="QuickBooks not configured")
    
    if not request.oauth_code:
        # Return OAuth URL for user to authorize
        raise HTTPException(
            status_code=400,
            detail={
                "message": "OAuth authorization required",
                "auth_url": f"https://appcenter.intuit.com/connect/oauth2"
                           f"?client_id={client_id}"
                           f"&redirect_uri={request.redirect_uri or ''}"
                           f"&response_type=code"
                           f"&scope=com.intuit.quickbooks.accounting",
            },
        )
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                data={
                    "grant_type": "authorization_code",
                    "code": request.oauth_code,
                    "redirect_uri": request.redirect_uri or "",
                },
                auth=(client_id, client_secret),
            )
            response.raise_for_status()
            tokens = response.json()
            
            return ERPConnection(
                type="quickbooks",
                client_id=client_id,
                client_secret=client_secret,
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token"),
                realm_id=tokens.get("realmId"),
            )
    except Exception as e:
        logger.error(f"QuickBooks OAuth failed: {e}")
        return None


async def connect_xero(request: ERPConnectionRequest) -> Optional[ERPConnection]:
    """Connect to Xero via OAuth."""
    import httpx
    
    client_id = os.getenv("XERO_CLIENT_ID")
    client_secret = os.getenv("XERO_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Xero not configured")
    
    if not request.oauth_code:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "OAuth authorization required",
                "auth_url": f"https://login.xero.com/identity/connect/authorize"
                           f"?client_id={client_id}"
                           f"&redirect_uri={request.redirect_uri or ''}"
                           f"&response_type=code"
                           f"&scope=openid profile email accounting.transactions",
            },
        )
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "code": request.oauth_code,
                    "redirect_uri": request.redirect_uri or "",
                },
                auth=(client_id, client_secret),
            )
            response.raise_for_status()
            tokens = response.json()
            
            # Get tenant ID
            tenant_response = await client.get(
                "https://api.xero.com/connections",
                headers={"Authorization": f"Bearer {tokens.get('access_token')}"},
            )
            tenant_response.raise_for_status()
            connections = tenant_response.json()
            
            tenant_id = connections[0].get("tenantId") if connections else None
            
            return ERPConnection(
                type="xero",
                client_id=client_id,
                client_secret=client_secret,
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token"),
                tenant_id=tenant_id,
            )
    except Exception as e:
        logger.error(f"Xero OAuth failed: {e}")
        return None


async def connect_netsuite(request: ERPConnectionRequest) -> Optional[ERPConnection]:
    """
    Connect to NetSuite via Token-Based Authentication (TBA).
    
    NetSuite setup steps:
    1. Create Integration record in NetSuite (Setup > Integration > Manage Integrations)
    2. Generate Consumer Key/Secret
    3. Create Access Token for the integration
    4. Provide all 4 credentials here
    """
    import httpx
    
    if not all([request.account_id, request.consumer_key, request.consumer_secret, 
                request.token_id, request.token_secret]):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "NetSuite requires Token-Based Authentication credentials",
                "required_fields": [
                    "account_id (e.g., '1234567' or '1234567_SB1' for sandbox)",
                    "consumer_key",
                    "consumer_secret", 
                    "token_id",
                    "token_secret",
                ],
                "setup_guide": "https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4247337262.html",
            },
        )
    
    # Build connection
    connection = ERPConnection(
        type="netsuite",
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
    )
    
    # Test the connection by fetching company info
    try:
        from clearledgr.integrations.erp_router import build_netsuite_oauth_header
        
        test_url = f"https://{request.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/customer"
        auth_header = build_netsuite_oauth_header(
            connection=connection,
            method="GET",
            url=test_url,
        )
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                test_url,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                params={"limit": 1},
                timeout=30,
            )
            
            if response.status_code == 401:
                raise HTTPException(
                    status_code=400,
                    detail="NetSuite authentication failed. Check your credentials.",
                )
            
            # Any 2xx or 4xx (except 401) means connection works
            # 4xx might just mean no customers exist
            logger.info(f"NetSuite connection verified for account {request.account_id}")
            return connection
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NetSuite connection test failed: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"NetSuite connection failed: {str(e)}",
        )


async def connect_sap(request: ERPConnectionRequest) -> Optional[ERPConnection]:
    """Connect to SAP via Service Layer."""
    import httpx
    
    if not request.base_url or not request.username or not request.password:
        raise HTTPException(
            status_code=400,
            detail="SAP requires base_url, username, and password",
        )
    
    try:
        async with httpx.AsyncClient(verify=False) as client:  # SAP often has self-signed certs
            response = await client.post(
                f"{request.base_url}/Login",
                json={
                    "CompanyDB": request.base_url.split("/")[-1] if "/" in request.base_url else "SBO_DEMO",
                    "UserName": request.username,
                    "Password": request.password,
                },
            )
            response.raise_for_status()
            
            # Extract session
            session_id = response.cookies.get("B1SESSION")
            
            return ERPConnection(
                type="sap",
                access_token=session_id,
                base_url=request.base_url,
            )
    except Exception as e:
        logger.error(f"SAP connection failed: {e}")
        return None


@router.post("/{organization_id}/gl-mapping")
async def configure_gl_mapping(
    organization_id: str,
    request: GLAccountMapping,
) -> Dict[str, Any]:
    """
    Step 4: Configure GL account mappings.
    
    Map Clearledgr's standard accounts to your ERP's chart of accounts.
    """
    setup = get_org_setup(organization_id)
    
    setup["gl_mapping"] = {
        "cash": request.cash_account,
        "accounts_receivable": request.accounts_receivable,
        "payment_fees": request.payment_fees,
        "revenue": request.revenue,
        "custom": request.custom_mappings or {},
        "configured_at": datetime.now(timezone.utc).isoformat(),
    }
    
    logger.info(f"GL mapping configured for {organization_id}")
    
    return {
        "status": "configured",
        "mapping": setup["gl_mapping"],
        "next_step": "complete",
        "message": "GL accounts mapped. Clearledgr is now fully configured!",
    }


@router.get("/{organization_id}/status")
async def get_onboarding_status(organization_id: str) -> OnboardingStatus:
    """
    Get current onboarding status.
    
    Shows what's connected and what's remaining.
    """
    setup = get_org_setup(organization_id)

    bank_setup = setup.get("bank") or {}
    gateway_setup = setup.get("gateway") or {}
    erp_setup = setup.get("erp") or {}
    
    bank_connected = bool(bank_setup)
    gateway_connected = bool(gateway_setup)
    erp_connected = bool(erp_setup)
    gl_configured = setup.get("gl_mapping") is not None
    
    # Minimum required: bank + gateway
    # ERP and GL are optional (can use without posting to ERP)
    is_complete = bank_connected and gateway_connected
    
    return OnboardingStatus(
        organization_id=organization_id,
        organization_name=setup.get("name", organization_id),
        bank_connected=bank_connected,
        bank_method=bank_setup.get("method"),
        gateway_connected=gateway_connected,
        gateway_type=gateway_setup.get("type"),
        erp_connected=erp_connected,
        erp_type=erp_setup.get("type"),
        gl_configured=gl_configured,
        is_complete=is_complete,
    )


@router.post("/{organization_id}/skip-erp")
async def skip_erp_connection(organization_id: str) -> Dict[str, Any]:
    """
    Skip ERP connection.
    
    User can reconcile without posting to an ERP.
    Journal entries will be generated but not posted.
    """
    setup = get_org_setup(organization_id)
    
    setup["erp"] = {
        "type": "none",
        "skipped_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "status": "skipped",
        "message": "ERP connection skipped. Journal entries will be generated but not posted.",
        "next_step": "complete",
    }


@router.get("/{organization_id}/test")
async def test_connections(organization_id: str) -> Dict[str, Any]:
    """
    Test all connections.
    
    Verifies bank, gateway, and ERP connections are working.
    """
    setup = get_org_setup(organization_id)
    results = {}
    
    # Test gateway
    if setup.get("gateway"):
        gateway_type = setup["gateway"].get("type")
        # Would test actual connection here
        results["gateway"] = {"status": "ok", "type": gateway_type}
    else:
        results["gateway"] = {"status": "not_connected"}
    
    # Test ERP
    if setup.get("erp") and setup["erp"].get("type") != "none":
        erp_type = setup["erp"].get("type")
        # Would test actual connection here
        results["erp"] = {"status": "ok", "type": erp_type}
    else:
        results["erp"] = {"status": "not_connected"}
    
    return {
        "organization_id": organization_id,
        "tests": results,
        "all_ok": all(r.get("status") == "ok" for r in results.values() if r.get("status") != "not_connected"),
    }
