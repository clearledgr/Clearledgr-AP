"""
ERP OAuth API Endpoints

Handles OAuth authorization flows for connecting ERPs:
- GET /oauth/{erp}/authorize - Start OAuth flow
- GET /oauth/{erp}/callback - Handle OAuth callback
- DELETE /oauth/{erp}/disconnect - Disconnect ERP
- GET /oauth/status - Get connection status
"""

import uuid
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from clearledgr.integrations.oauth import (
    get_quickbooks_auth_url,
    get_xero_auth_url,
    validate_oauth_state,
    exchange_quickbooks_code,
    exchange_xero_code,
    save_erp_connection,
    get_erp_connection_record,
    delete_erp_connection,
    ERPConnectionRecord,
    ensure_valid_token,
)
from clearledgr.integrations.erp_router import (
    set_erp_connection,
    get_erp_connection,
    ERPConnection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oauth", tags=["ERP OAuth"])


# ==================== REQUEST/RESPONSE MODELS ====================

class AuthorizeRequest(BaseModel):
    """Request to start OAuth flow."""
    organization_id: str


class NetSuiteConnectRequest(BaseModel):
    """Request to connect NetSuite via Token-Based Auth."""
    organization_id: str
    account_id: str  # e.g., "1234567" or "1234567_SB1" for sandbox
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str


class SAPConnectRequest(BaseModel):
    """Request to connect SAP."""
    organization_id: str
    base_url: str  # e.g., "https://mycompany.sapbydesign.com/sap/byd/odata/v1/financials"
    username: str
    password: str  # Will be base64 encoded for Basic auth


class ConnectionStatus(BaseModel):
    """ERP connection status."""
    connected: bool
    erp_type: Optional[str] = None
    organization_id: Optional[str] = None
    expires_at: Optional[str] = None
    needs_reauth: bool = False


# ==================== QUICKBOOKS ====================

@router.get("/quickbooks/authorize")
async def authorize_quickbooks(organization_id: str = Query(...)):
    """
    Start QuickBooks OAuth flow.
    
    Redirects user to QuickBooks authorization page.
    After authorization, QuickBooks redirects back to /oauth/quickbooks/callback.
    """
    auth_url = get_quickbooks_auth_url(organization_id)
    return RedirectResponse(url=auth_url)


@router.get("/quickbooks/callback")
async def quickbooks_callback(
    code: str = Query(None),
    state: str = Query(None),
    realmId: str = Query(None),
    error: str = Query(None),
):
    """
    Handle QuickBooks OAuth callback.
    
    QuickBooks redirects here after user authorizes.
    We exchange the code for tokens and store the connection.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"Authorization failed: {error}")
    
    if not code or not state or not realmId:
        raise HTTPException(status_code=400, detail="Missing required parameters")
    
    # Validate state
    state_data = validate_oauth_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    
    organization_id = state_data["organization_id"]
    
    # Exchange code for tokens
    try:
        tokens = await exchange_quickbooks_code(code, realmId)
    except Exception as e:
        logger.error(f"Failed to exchange QuickBooks code: {e}")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")
    
    # Save connection
    record = ERPConnectionRecord(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        erp_type="quickbooks",
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat(),
        realm_id=realmId,
    )
    save_erp_connection(record)
    
    logger.info(f"Connected QuickBooks for organization {organization_id}")
    
    # Redirect to success page (frontend should handle this)
    return RedirectResponse(url=f"/settings/integrations?success=quickbooks&org={organization_id}")


# ==================== XERO ====================

@router.get("/xero/authorize")
async def authorize_xero(organization_id: str = Query(...)):
    """
    Start Xero OAuth flow.
    
    Redirects user to Xero authorization page.
    """
    auth_url = get_xero_auth_url(organization_id)
    return RedirectResponse(url=auth_url)


@router.get("/xero/callback")
async def xero_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """
    Handle Xero OAuth callback.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"Authorization failed: {error}")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required parameters")
    
    # Validate state
    state_data = validate_oauth_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    
    organization_id = state_data["organization_id"]
    
    # Exchange code for tokens
    try:
        tokens = await exchange_xero_code(code)
    except Exception as e:
        logger.error(f"Failed to exchange Xero code: {e}")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")
    
    # Save connection
    record = ERPConnectionRecord(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        erp_type="xero",
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 1800))).isoformat(),
        tenant_id=tokens.get("tenant_id"),
    )
    save_erp_connection(record)
    
    logger.info(f"Connected Xero for organization {organization_id}")
    
    return RedirectResponse(url=f"/settings/integrations?success=xero&org={organization_id}")


# ==================== NETSUITE (Token-Based Auth) ====================

@router.post("/netsuite/connect")
async def connect_netsuite(request: NetSuiteConnectRequest):
    """
    Connect NetSuite using Token-Based Authentication.
    
    NetSuite uses OAuth 1.0 TBA instead of OAuth 2.0.
    User provides credentials from NetSuite's "Manage Access Tokens" page.
    
    Steps to get credentials:
    1. In NetSuite, go to Setup > Company > Enable Features > SuiteCloud > Manage Authentication
    2. Enable Token-Based Authentication
    3. Create an Integration record
    4. Create a Token for the Integration
    """
    # Validate by attempting to fetch accounts
    connection = ERPConnection(
        type="netsuite",
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
    )
    
    # Test the connection
    from clearledgr.integrations.erp_router import get_netsuite_accounts
    try:
        accounts = await get_netsuite_accounts(connection)
        if not accounts:
            # Connection worked but no accounts returned - still valid
            logger.warning("NetSuite connected but no accounts found")
    except Exception as e:
        logger.error(f"NetSuite connection test failed: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    
    # Save connection
    record = ERPConnectionRecord(
        id=str(uuid.uuid4()),
        organization_id=request.organization_id,
        erp_type="netsuite",
        access_token="",  # Not used for TBA
        refresh_token="",  # Not used for TBA
        expires_at=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),  # TBA tokens don't expire
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
    )
    save_erp_connection(record)
    
    logger.info(f"Connected NetSuite for organization {request.organization_id}")
    
    return {
        "status": "success",
        "erp": "netsuite",
        "organization_id": request.organization_id,
        "account_id": request.account_id,
    }


# ==================== SAP ====================

@router.post("/sap/connect")
async def connect_sap(request: SAPConnectRequest):
    """
    Connect SAP Business One or S/4HANA.
    
    Uses Basic Auth or OAuth depending on SAP configuration.
    """
    import base64
    
    # Create Basic Auth header
    credentials = base64.b64encode(f"{request.username}:{request.password}".encode()).decode()
    
    # Test connection
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{request.base_url}/$metadata",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            if response.status_code not in [200, 401]:  # 401 might mean auth is different
                response.raise_for_status()
    except Exception as e:
        logger.error(f"SAP connection test failed: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    
    # Save connection
    record = ERPConnectionRecord(
        id=str(uuid.uuid4()),
        organization_id=request.organization_id,
        erp_type="sap",
        access_token=credentials,  # Store Basic Auth as "token"
        refresh_token="",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        base_url=request.base_url,
    )
    save_erp_connection(record)
    
    logger.info(f"Connected SAP for organization {request.organization_id}")
    
    return {
        "status": "success",
        "erp": "sap",
        "organization_id": request.organization_id,
        "base_url": request.base_url,
    }


# ==================== DISCONNECT & STATUS ====================

@router.delete("/{erp}/disconnect")
async def disconnect_erp(erp: str, organization_id: str = Query(...)):
    """
    Disconnect an ERP integration.
    """
    record = get_erp_connection_record(organization_id)
    
    if not record or record.erp_type != erp:
        raise HTTPException(status_code=404, detail=f"{erp} not connected for this organization")
    
    delete_erp_connection(organization_id)
    
    logger.info(f"Disconnected {erp} for organization {organization_id}")
    
    return {"status": "success", "message": f"{erp} disconnected"}


@router.get("/status")
async def get_connection_status(organization_id: str = Query(...)) -> ConnectionStatus:
    """
    Get ERP connection status for an organization.
    """
    record = get_erp_connection_record(organization_id)
    
    if not record:
        return ConnectionStatus(connected=False)
    
    # Check if token needs refresh
    needs_reauth = False
    expires_at = datetime.fromisoformat(record.expires_at)
    if expires_at < datetime.now(timezone.utc):
        # Try to refresh
        if not await ensure_valid_token(organization_id):
            needs_reauth = True
    
    return ConnectionStatus(
        connected=True,
        erp_type=record.erp_type,
        organization_id=record.organization_id,
        expires_at=record.expires_at,
        needs_reauth=needs_reauth,
    )


@router.post("/refresh")
async def refresh_token(organization_id: str = Query(...)):
    """
    Manually trigger token refresh.
    """
    record = get_erp_connection_record(organization_id)
    
    if not record:
        raise HTTPException(status_code=404, detail="No ERP connected")
    
    if record.erp_type in ["netsuite", "sap"]:
        return {"status": "success", "message": f"{record.erp_type} doesn't use OAuth refresh tokens"}
    
    success = await ensure_valid_token(organization_id)
    
    if success:
        return {"status": "success", "message": "Token refreshed"}
    else:
        raise HTTPException(status_code=400, detail="Token refresh failed. Re-authorization required.")
