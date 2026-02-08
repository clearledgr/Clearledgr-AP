"""
ERP OAuth Integration

Handles OAuth 2.0 authorization flows for:
- QuickBooks Online
- Xero
- NetSuite (Token-Based Auth setup)
- SAP (API key or OAuth)

This is the entry point for connecting ERPs. Users click "Connect",
get redirected to the ERP's authorization page, and return with tokens.
"""

import os
import secrets
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
import httpx

logger = logging.getLogger(__name__)


# ==================== CONFIGURATION ====================

@dataclass
class OAuthConfig:
    """OAuth configuration for an ERP."""
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str
    authorize_url: str
    token_url: str


def get_quickbooks_config() -> OAuthConfig:
    """Get QuickBooks OAuth config from environment."""
    return OAuthConfig(
        client_id=os.getenv("QUICKBOOKS_CLIENT_ID", ""),
        client_secret=os.getenv("QUICKBOOKS_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("QUICKBOOKS_REDIRECT_URI", "http://localhost:8000/oauth/quickbooks/callback"),
        scope="com.intuit.quickbooks.accounting",
        authorize_url="https://appcenter.intuit.com/connect/oauth2",
        token_url="https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    )


def get_xero_config() -> OAuthConfig:
    """Get Xero OAuth config from environment."""
    return OAuthConfig(
        client_id=os.getenv("XERO_CLIENT_ID", ""),
        client_secret=os.getenv("XERO_CLIENT_SECRET", ""),
        redirect_uri=os.getenv("XERO_REDIRECT_URI", "http://localhost:8000/oauth/xero/callback"),
        scope="openid profile email accounting.transactions accounting.settings",
        authorize_url="https://login.xero.com/identity/connect/authorize",
        token_url="https://identity.xero.com/connect/token",
    )


# ==================== STATE MANAGEMENT ====================

# In-memory state store for OAuth flow (use Redis in production)
_oauth_states: Dict[str, Dict[str, Any]] = {}


def create_oauth_state(organization_id: str, erp_type: str) -> str:
    """Create a secure state parameter for OAuth."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "organization_id": organization_id,
        "erp_type": erp_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return state


def validate_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Validate and consume OAuth state."""
    if state not in _oauth_states:
        return None
    
    data = _oauth_states.pop(state)
    
    # Check expiry (10 minutes)
    created = datetime.fromisoformat(data["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(minutes=10):
        return None
    
    return data


# ==================== AUTHORIZATION URLS ====================

def get_quickbooks_auth_url(organization_id: str) -> str:
    """Generate QuickBooks authorization URL."""
    config = get_quickbooks_config()
    state = create_oauth_state(organization_id, "quickbooks")
    
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": config.scope,
        "response_type": "code",
        "state": state,
    }
    
    param_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{config.authorize_url}?{param_string}"


def get_xero_auth_url(organization_id: str) -> str:
    """Generate Xero authorization URL."""
    config = get_xero_config()
    state = create_oauth_state(organization_id, "xero")
    
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": config.scope,
        "response_type": "code",
        "state": state,
    }
    
    param_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{config.authorize_url}?{param_string}"


# ==================== TOKEN EXCHANGE ====================

async def exchange_quickbooks_code(code: str, realm_id: str) -> Dict[str, Any]:
    """Exchange QuickBooks authorization code for tokens."""
    config = get_quickbooks_config()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            auth=(config.client_id, config.client_secret),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        tokens = response.json()
    
    return {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "realm_id": realm_id,
        "token_type": tokens.get("token_type"),
    }


async def exchange_xero_code(code: str) -> Dict[str, Any]:
    """Exchange Xero authorization code for tokens."""
    config = get_xero_config()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            auth=(config.client_id, config.client_secret),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        tokens = response.json()
    
    # Get tenant ID (Xero organization)
    tenant_id = None
    async with httpx.AsyncClient() as client:
        conn_response = await client.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {tokens.get('access_token')}"},
        )
        if conn_response.status_code == 200:
            connections = conn_response.json()
            if connections:
                tenant_id = connections[0].get("tenantId")
    
    return {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "tenant_id": tenant_id,
        "id_token": tokens.get("id_token"),
    }


async def refresh_quickbooks_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh QuickBooks OAuth token."""
    config = get_quickbooks_config()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(config.client_id, config.client_secret),
        )
        response.raise_for_status()
        return response.json()


async def refresh_xero_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh Xero OAuth token."""
    config = get_xero_config()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(config.client_id, config.client_secret),
        )
        response.raise_for_status()
        return response.json()


# ==================== CONNECTION STORAGE ====================

@dataclass
class ERPConnectionRecord:
    """Database record for ERP connection."""
    id: str
    organization_id: str
    erp_type: str
    access_token: str
    refresh_token: str
    expires_at: str
    # ERP-specific
    realm_id: Optional[str] = None  # QuickBooks
    tenant_id: Optional[str] = None  # Xero
    account_id: Optional[str] = None  # NetSuite
    consumer_key: Optional[str] = None  # NetSuite TBA
    consumer_secret: Optional[str] = None  # NetSuite TBA
    token_id: Optional[str] = None  # NetSuite TBA
    token_secret: Optional[str] = None  # NetSuite TBA
    base_url: Optional[str] = None  # SAP
    created_at: str = ""
    updated_at: str = ""


# In-memory storage (replace with database in production)
_erp_connections: Dict[str, ERPConnectionRecord] = {}


def save_erp_connection(record: ERPConnectionRecord):
    """Save ERP connection to storage."""
    record.updated_at = datetime.now(timezone.utc).isoformat()
    if not record.created_at:
        record.created_at = record.updated_at
    _erp_connections[record.organization_id] = record
    
    # Also update the erp_router's in-memory store for immediate use
    from clearledgr.integrations.erp_router import set_erp_connection, ERPConnection
    
    connection = ERPConnection(
        type=record.erp_type,
        access_token=record.access_token,
        refresh_token=record.refresh_token,
        realm_id=record.realm_id,
        tenant_id=record.tenant_id,
        account_id=record.account_id,
        consumer_key=record.consumer_key,
        consumer_secret=record.consumer_secret,
        token_id=record.token_id,
        token_secret=record.token_secret,
        base_url=record.base_url,
    )
    set_erp_connection(record.organization_id, connection)


def get_erp_connection_record(organization_id: str) -> Optional[ERPConnectionRecord]:
    """Get ERP connection record."""
    return _erp_connections.get(organization_id)


def delete_erp_connection(organization_id: str):
    """Delete ERP connection."""
    if organization_id in _erp_connections:
        del _erp_connections[organization_id]


# ==================== AUTO-REFRESH ====================

async def ensure_valid_token(organization_id: str) -> bool:
    """
    Ensure the organization's ERP token is valid.
    Refreshes if expired or about to expire.
    
    Returns True if token is valid/refreshed, False otherwise.
    """
    record = get_erp_connection_record(organization_id)
    if not record:
        return False
    
    # Check if token is expired or about to expire (within 5 minutes)
    expires_at = datetime.fromisoformat(record.expires_at)
    if expires_at - timedelta(minutes=5) > datetime.now(timezone.utc):
        return True  # Still valid
    
    # Need to refresh
    try:
        if record.erp_type == "quickbooks":
            new_tokens = await refresh_quickbooks_token(record.refresh_token)
        elif record.erp_type == "xero":
            new_tokens = await refresh_xero_token(record.refresh_token)
        else:
            # NetSuite and SAP don't use OAuth refresh the same way
            return True
        
        # Update record
        record.access_token = new_tokens.get("access_token", record.access_token)
        record.refresh_token = new_tokens.get("refresh_token", record.refresh_token)
        record.expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get("expires_in", 3600))
        ).isoformat()
        
        save_erp_connection(record)
        logger.info(f"Refreshed {record.erp_type} token for {organization_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to refresh token for {organization_id}: {e}")
        return False
