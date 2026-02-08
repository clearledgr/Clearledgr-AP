"""
ERP Connection API

Handles OAuth flows for connecting to:
- QuickBooks Online (OAuth 2.0)
- Xero (OAuth 2.0)
- NetSuite (Token-Based Authentication)

Each flow:
1. Generate auth URL
2. Handle callback
3. Store tokens securely
4. Provide connection status
"""

import os
import json
import secrets
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, parse_qs, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from clearledgr.core.database import get_db
from clearledgr.integrations.erp_router import ERPConnection, set_erp_connection, get_erp_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/erp", tags=["erp-connections"])


# ==================== CONFIGURATION ====================

# QuickBooks OAuth
QUICKBOOKS_CLIENT_ID = os.getenv("QUICKBOOKS_CLIENT_ID", "")
QUICKBOOKS_CLIENT_SECRET = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
QUICKBOOKS_REDIRECT_URI = os.getenv("QUICKBOOKS_REDIRECT_URI", "http://localhost:8000/erp/quickbooks/callback")
QUICKBOOKS_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QUICKBOOKS_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Xero OAuth
XERO_CLIENT_ID = os.getenv("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET", "")
XERO_REDIRECT_URI = os.getenv("XERO_REDIRECT_URI", "http://localhost:8000/erp/xero/callback")
XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"

# NetSuite (TBA - Token Based Auth, no OAuth flow needed)
NETSUITE_ACCOUNT_ID = os.getenv("NETSUITE_ACCOUNT_ID", "")
NETSUITE_CONSUMER_KEY = os.getenv("NETSUITE_CONSUMER_KEY", "")
NETSUITE_CONSUMER_SECRET = os.getenv("NETSUITE_CONSUMER_SECRET", "")
NETSUITE_TOKEN_ID = os.getenv("NETSUITE_TOKEN_ID", "")
NETSUITE_TOKEN_SECRET = os.getenv("NETSUITE_TOKEN_SECRET", "")

# State storage for OAuth (in production, use Redis)
_oauth_states: Dict[str, Dict[str, Any]] = {}

# Frontend URL for redirects after OAuth
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


# ==================== REQUEST MODELS ====================

class ConnectRequest(BaseModel):
    """Request to start ERP connection."""
    organization_id: str
    return_url: Optional[str] = None


class NetSuiteCredentials(BaseModel):
    """NetSuite TBA credentials."""
    organization_id: str
    account_id: str
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str


class DisconnectRequest(BaseModel):
    """Request to disconnect ERP."""
    organization_id: str


# ==================== CONNECTION STATUS ====================

@router.get("/status/{organization_id}")
async def get_connection_status(organization_id: str):
    """
    Get ERP connection status for an organization.
    
    Returns connected ERPs and their status.
    """
    db = get_db()
    connections = db.get_erp_connections(organization_id)
    
    result = {
        "organization_id": organization_id,
        "connections": {},
        "available_erps": ["quickbooks", "xero", "netsuite"],
    }
    
    for conn in connections:
        erp_type = conn.get("erp_type")
        result["connections"][erp_type] = {
            "connected": conn.get("is_active", False),
            "last_sync": conn.get("last_sync_at"),
            "realm_id": conn.get("realm_id"),  # QuickBooks
            "tenant_id": conn.get("tenant_id"),  # Xero
        }
    
    return result


# ==================== QUICKBOOKS OAUTH ====================

@router.post("/quickbooks/connect")
async def quickbooks_connect(request: ConnectRequest):
    """
    Start QuickBooks OAuth flow.
    
    Returns URL to redirect user to for authorization.
    """
    if not QUICKBOOKS_CLIENT_ID:
        raise HTTPException(status_code=500, detail="QuickBooks not configured")
    
    # Generate state token
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "organization_id": request.organization_id,
        "return_url": request.return_url or f"{FRONTEND_URL}/settings/erp",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Build auth URL
    params = {
        "client_id": QUICKBOOKS_CLIENT_ID,
        "redirect_uri": QUICKBOOKS_REDIRECT_URI,
        "response_type": "code",
        "scope": "com.intuit.quickbooks.accounting",
        "state": state,
    }
    
    auth_url = f"{QUICKBOOKS_AUTH_URL}?{urlencode(params)}"
    
    return {
        "auth_url": auth_url,
        "state": state,
    }


@router.get("/quickbooks/callback")
async def quickbooks_callback(
    code: str = Query(None),
    state: str = Query(None),
    realmId: str = Query(None),
    error: str = Query(None),
):
    """
    Handle QuickBooks OAuth callback.
    
    Exchanges code for tokens and stores connection.
    """
    if error:
        logger.error(f"QuickBooks OAuth error: {error}")
        return RedirectResponse(f"{FRONTEND_URL}/settings/erp?error={error}")
    
    if not state or state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state")
    
    state_data = _oauth_states.pop(state)
    organization_id = state_data["organization_id"]
    return_url = state_data["return_url"]
    
    if not code or not realmId:
        return RedirectResponse(f"{return_url}?error=missing_params")
    
    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                QUICKBOOKS_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": QUICKBOOKS_REDIRECT_URI,
                },
                auth=(QUICKBOOKS_CLIENT_ID, QUICKBOOKS_CLIENT_SECRET),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            tokens = response.json()
    except Exception as e:
        logger.error(f"QuickBooks token exchange failed: {e}")
        return RedirectResponse(f"{return_url}?error=token_exchange_failed")
    
    # Store connection
    connection = ERPConnection(
        type="quickbooks",
        client_id=QUICKBOOKS_CLIENT_ID,
        client_secret=QUICKBOOKS_CLIENT_SECRET,
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        realm_id=realmId,
    )
    
    set_erp_connection(organization_id, connection)
    
    logger.info(f"QuickBooks connected for org {organization_id}, realm {realmId}")
    
    return RedirectResponse(f"{return_url}?connected=quickbooks")


@router.post("/quickbooks/disconnect")
async def quickbooks_disconnect(request: DisconnectRequest):
    """Disconnect QuickBooks from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection
    
    success = delete_erp_connection(request.organization_id, "quickbooks")
    
    return {"success": success, "erp": "quickbooks"}


# ==================== XERO OAUTH ====================

@router.post("/xero/connect")
async def xero_connect(request: ConnectRequest):
    """
    Start Xero OAuth flow.
    
    Returns URL to redirect user to for authorization.
    """
    if not XERO_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Xero not configured")
    
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "organization_id": request.organization_id,
        "return_url": request.return_url or f"{FRONTEND_URL}/settings/erp",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    params = {
        "client_id": XERO_CLIENT_ID,
        "redirect_uri": XERO_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid profile email accounting.transactions accounting.contacts offline_access",
        "state": state,
    }
    
    auth_url = f"{XERO_AUTH_URL}?{urlencode(params)}"
    
    return {
        "auth_url": auth_url,
        "state": state,
    }


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
        logger.error(f"Xero OAuth error: {error}")
        return RedirectResponse(f"{FRONTEND_URL}/settings/erp?error={error}")
    
    if not state or state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state")
    
    state_data = _oauth_states.pop(state)
    organization_id = state_data["organization_id"]
    return_url = state_data["return_url"]
    
    if not code:
        return RedirectResponse(f"{return_url}?error=missing_code")
    
    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                XERO_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": XERO_REDIRECT_URI,
                },
                auth=(XERO_CLIENT_ID, XERO_CLIENT_SECRET),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            tokens = response.json()
    except Exception as e:
        logger.error(f"Xero token exchange failed: {e}")
        return RedirectResponse(f"{return_url}?error=token_exchange_failed")
    
    # Get tenant ID (Xero organization)
    tenant_id = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/connections",
                headers={"Authorization": f"Bearer {tokens.get('access_token')}"},
            )
            response.raise_for_status()
            connections = response.json()
            if connections:
                tenant_id = connections[0].get("tenantId")
    except Exception as e:
        logger.warning(f"Failed to get Xero tenant: {e}")
    
    # Store connection
    connection = ERPConnection(
        type="xero",
        client_id=XERO_CLIENT_ID,
        client_secret=XERO_CLIENT_SECRET,
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        tenant_id=tenant_id,
    )
    
    set_erp_connection(organization_id, connection)
    
    logger.info(f"Xero connected for org {organization_id}, tenant {tenant_id}")
    
    return RedirectResponse(f"{return_url}?connected=xero")


@router.post("/xero/disconnect")
async def xero_disconnect(request: DisconnectRequest):
    """Disconnect Xero from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection
    
    success = delete_erp_connection(request.organization_id, "xero")
    
    return {"success": success, "erp": "xero"}


# ==================== NETSUITE TBA ====================

@router.post("/netsuite/connect")
async def netsuite_connect(credentials: NetSuiteCredentials):
    """
    Connect NetSuite using Token-Based Authentication.
    
    NetSuite uses TBA (OAuth 1.0 style) not OAuth 2.0.
    Credentials are generated in NetSuite UI and provided here.
    """
    # Validate credentials by making a test API call
    connection = ERPConnection(
        type="netsuite",
        account_id=credentials.account_id,
        consumer_key=credentials.consumer_key,
        consumer_secret=credentials.consumer_secret,
        token_id=credentials.token_id,
        token_secret=credentials.token_secret,
    )
    
    # Test connection
    from clearledgr.integrations.erp_router import get_netsuite_accounts, build_netsuite_oauth_header
    
    try:
        accounts = await get_netsuite_accounts(connection)
        if accounts is None:
            raise Exception("Failed to fetch accounts")
        
        # Store connection
        set_erp_connection(credentials.organization_id, connection)
        
        logger.info(f"NetSuite connected for org {credentials.organization_id}")
        
        return {
            "success": True,
            "erp": "netsuite",
            "account_id": credentials.account_id,
            "accounts_found": len(accounts) if accounts else 0,
        }
        
    except Exception as e:
        logger.error(f"NetSuite connection failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to connect: {str(e)}")


@router.post("/netsuite/disconnect")
async def netsuite_disconnect(request: DisconnectRequest):
    """Disconnect NetSuite from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection
    
    success = delete_erp_connection(request.organization_id, "netsuite")
    
    return {"success": success, "erp": "netsuite"}


# ==================== TOKEN REFRESH ====================

@router.post("/refresh/{organization_id}/{erp_type}")
async def refresh_tokens(organization_id: str, erp_type: str):
    """
    Manually refresh tokens for an ERP connection.
    
    Normally tokens are refreshed automatically, but this allows manual refresh.
    """
    connection = get_erp_connection(organization_id)
    
    if not connection or connection.type != erp_type:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    if erp_type == "quickbooks":
        from clearledgr.integrations.erp_router import refresh_quickbooks_token
        new_token = await refresh_quickbooks_token(connection)
    elif erp_type == "xero":
        from clearledgr.integrations.erp_router import refresh_xero_token
        new_token = await refresh_xero_token(connection)
    else:
        raise HTTPException(status_code=400, detail="Token refresh not supported for this ERP")
    
    if new_token:
        # Update stored connection
        set_erp_connection(organization_id, connection)
        return {"success": True, "erp": erp_type}
    
    return {"success": False, "error": "Token refresh failed"}


# ==================== CHART OF ACCOUNTS ====================

@router.get("/accounts/{organization_id}")
async def get_chart_of_accounts(organization_id: str):
    """
    Get chart of accounts from connected ERP.
    
    Used for GL account mapping.
    """
    connection = get_erp_connection(organization_id)
    
    if not connection:
        raise HTTPException(status_code=404, detail="No ERP connected")
    
    accounts = []
    
    if connection.type == "quickbooks":
        accounts = await _get_quickbooks_accounts(connection)
    elif connection.type == "xero":
        accounts = await _get_xero_accounts(connection)
    elif connection.type == "netsuite":
        from clearledgr.integrations.erp_router import get_netsuite_accounts
        accounts = await get_netsuite_accounts(connection)
    
    return {
        "organization_id": organization_id,
        "erp": connection.type,
        "accounts": accounts,
    }


async def _get_quickbooks_accounts(connection: ERPConnection) -> list:
    """Fetch chart of accounts from QuickBooks."""
    if not connection.access_token or not connection.realm_id:
        return []
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/query",
                params={"query": "SELECT * FROM Account MAXRESULTS 1000"},
                headers={"Authorization": f"Bearer {connection.access_token}"},
            )
            response.raise_for_status()
            result = response.json()
            
            accounts = []
            for acc in result.get("QueryResponse", {}).get("Account", []):
                accounts.append({
                    "id": acc.get("Id"),
                    "name": acc.get("Name"),
                    "number": acc.get("AcctNum"),
                    "type": acc.get("AccountType"),
                    "subtype": acc.get("AccountSubType"),
                })
            return accounts
            
    except Exception as e:
        logger.error(f"Failed to get QuickBooks accounts: {e}")
        return []


async def _get_xero_accounts(connection: ERPConnection) -> list:
    """Fetch chart of accounts from Xero."""
    if not connection.access_token or not connection.tenant_id:
        return []
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Accounts",
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "xero-tenant-id": connection.tenant_id,
                },
            )
            response.raise_for_status()
            result = response.json()
            
            accounts = []
            for acc in result.get("Accounts", []):
                accounts.append({
                    "id": acc.get("AccountID"),
                    "name": acc.get("Name"),
                    "number": acc.get("Code"),
                    "type": acc.get("Type"),
                    "class": acc.get("Class"),
                })
            return accounts
            
    except Exception as e:
        logger.error(f"Failed to get Xero accounts: {e}")
        return []
