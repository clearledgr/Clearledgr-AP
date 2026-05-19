"""
ERP OAuth API Endpoints

Handles OAuth authorization flows for connecting ERPs:
- GET /oauth/{erp}/authorize - Start OAuth flow
- GET /oauth/{erp}/callback - Handle OAuth callback
- DELETE /oauth/{erp}/disconnect - Disconnect ERP
- GET /oauth/status - Get connection status
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from solden.core.auth import get_current_user
from solden.core.http_client import get_http_client
from solden.integrations.erp_router import (
    ERPConnection,
)
from solden.integrations.oauth import (
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

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/oauth",
    tags=["ERP OAuth"],
    dependencies=[Depends(get_current_user)],
)


def _oauth_success_response(erp_name: str) -> HTMLResponse:
    """Return a simple success page after OAuth — no console redirect."""
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Connected</title>
<style>body{{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f8f9fa}}
.card{{text-align:center;padding:2rem;border-radius:8px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
h1{{color:#22c55e;margin:0 0 .5rem}}</style></head>
<body><div class="card"><h1>{erp_name} Connected</h1>
<p>You can close this tab and return to Slack or Gmail.</p></div></body></html>""")


# ==================== REQUEST/RESPONSE MODELS ====================

class NetSuiteConnectRequest(BaseModel):
    """Request to connect NetSuite via Token-Based Auth.

    The pre-fix shape carried ``organization_id`` in the body; we now
    derive the org from the authenticated user instead so no body field
    can attach a NetSuite credential set to a tenant the caller doesn't
    own.
    """
    account_id: str  # e.g., "1234567" or "1234567_SB1" for sandbox
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str
    # OneWorld tenants only. Internal ID of the subsidiary this
    # workspace posts bills against (NetSuite returns these from the
    # ``/record/v1/subsidiary`` endpoint). Single-subsidiary accounts
    # may omit this field.
    subsidiary_id: Optional[str] = None


class SAPConnectRequest(BaseModel):
    """Request to connect SAP.

    Org is derived from the authenticated user — see the NetSuite
    request docstring for the rationale.
    """
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
async def authorize_quickbooks(user=Depends(get_current_user)):
    """
    Start QuickBooks OAuth flow.

    Redirects user to QuickBooks authorization page.
    After authorization, QuickBooks redirects back to /oauth/quickbooks/callback.

    Pre-fix this route accepted ``organization_id`` as an unverified
    query parameter. An authenticated user from tenant A could pass
    ``?organization_id=tenantB`` and have their newly-issued
    QuickBooks credentials saved against tenant B's connection — a
    direct cross-tenant credential-attachment attack. Now the org
    is derived from the authenticated user's session and never
    accepted from the URL.
    """
    organization_id = str(getattr(user, "organization_id", "") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=403, detail="user_missing_organization_id")
    user_id = str(getattr(user, "user_id", "") or "").strip() or None
    auth_url = get_quickbooks_auth_url(organization_id, user_id=user_id)
    return RedirectResponse(url=auth_url)


@router.get("/quickbooks/callback")
async def quickbooks_callback(
    code: str = Query(None),
    state: str = Query(None),
    realmId: str = Query(None),
    error: str = Query(None),
    user=Depends(get_current_user),
):
    """
    Handle QuickBooks OAuth callback.

    QuickBooks redirects here after user authorizes.
    We exchange the code for tokens and store the connection.

    The state parameter is bound to ``(organization_id, user_id)`` from
    the authorize step. We re-check both against the session here so a
    leaked state cannot be redeemed by a different user, and the org
    on the saved connection cannot drift from the org of the user who
    initiated the flow.
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

    session_org = str(getattr(user, "organization_id", "") or "").strip()
    if not session_org or session_org != str(organization_id):
        raise HTTPException(status_code=403, detail="oauth_state_org_mismatch")

    state_user_id = state_data.get("user_id")
    if state_user_id is not None:
        session_user_id = str(getattr(user, "user_id", "") or "").strip()
        if not session_user_id or session_user_id != str(state_user_id):
            raise HTTPException(status_code=403, detail="oauth_state_user_mismatch")
    
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
    return _oauth_success_response("QuickBooks")


# ==================== XERO ====================

@router.get("/xero/authorize")
async def authorize_xero(user=Depends(get_current_user)):
    """
    Start Xero OAuth flow.

    Redirects user to Xero authorization page. Same security
    contract as the QuickBooks authorize route — org is derived
    from the authenticated user, never from the URL.
    """
    organization_id = str(getattr(user, "organization_id", "") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=403, detail="user_missing_organization_id")
    user_id = str(getattr(user, "user_id", "") or "").strip() or None
    auth_url = get_xero_auth_url(organization_id, user_id=user_id)
    return RedirectResponse(url=auth_url)


@router.get("/xero/callback")
async def xero_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    user=Depends(get_current_user),
):
    """
    Handle Xero OAuth callback. Same security contract as the
    QuickBooks callback — state is bound to (org, user) and both are
    re-checked against the session.
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

    session_org = str(getattr(user, "organization_id", "") or "").strip()
    if not session_org or session_org != str(organization_id):
        raise HTTPException(status_code=403, detail="oauth_state_org_mismatch")

    state_user_id = state_data.get("user_id")
    if state_user_id is not None:
        session_user_id = str(getattr(user, "user_id", "") or "").strip()
        if not session_user_id or session_user_id != str(state_user_id):
            raise HTTPException(status_code=403, detail="oauth_state_user_mismatch")
    
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
    
    return _oauth_success_response("Xero")


# ==================== NETSUITE (Token-Based Auth) ====================

@router.post("/netsuite/connect")
async def connect_netsuite(
    request: NetSuiteConnectRequest,
    user=Depends(get_current_user),
):
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
    organization_id = _require_session_org(user)
    # Validate by running the full preflight.
    connection = ERPConnection(
        type="netsuite",
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
        subsidiary_id=request.subsidiary_id,
    )
    
    # Full preflight: authenticate + validate AP permissions + sanity-check the
    # customer's chart of accounts. Bare auth success is not enough — a token
    # with wrong scopes will pass OAuth but fail on the first bill post weeks
    # later. Fail fast at connect time instead.
    from solden.integrations.erp_netsuite import preflight_netsuite
    try:
        preflight = await preflight_netsuite(connection)
    except Exception as e:
        logger.error(f"NetSuite preflight failed: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

    # Any failed critical check (vendor read, vendor-bill read, chart read)
    # blocks the connection. The customer sees exactly which permission is
    # missing so they can fix it in NetSuite before retrying.
    if not preflight.get("critical_ok"):
        failed = {
            name: check
            for name, check in preflight.get("checks", {}).items()
            if not check.get("ok")
        }
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "netsuite_preflight_failed",
                "failed_checks": failed,
                "fix": (
                    "Update the NetSuite Integration Record + Role to grant "
                    "read access to Vendors, Vendor Bills, and Lists. See the "
                    "'NetSuite permissions' section of your onboarding guide."
                ),
            },
        )

    # Save connection
    record = ERPConnectionRecord(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        erp_type="netsuite",
        access_token="",  # Not used for TBA
        refresh_token="",  # Not used for TBA
        expires_at=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),  # TBA tokens don't expire
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
        subsidiary_id=request.subsidiary_id,
    )
    save_erp_connection(record)

    logger.info(f"Connected NetSuite for organization {organization_id}")

    # Return the preflight detail alongside the success so the UI can
    # show the customer exactly what was validated and warn about any
    # soft issues (e.g., chart has no expense accounts yet).
    return {
        "status": "success",
        "erp": "netsuite",
        "organization_id": organization_id,
        "account_id": request.account_id,
        "preflight": {
            "checks": preflight.get("checks", {}),
            "chart_summary": preflight.get("chart_summary", {}),
            "warnings": preflight.get("warnings", []),
        },
    }


# ==================== SAP ====================

@router.post("/sap/connect")
async def connect_sap(
    request: SAPConnectRequest,
    user=Depends(get_current_user),
):
    """
    Connect SAP Business One or S/4HANA.

    Uses Basic Auth or OAuth depending on SAP configuration.
    """
    organization_id = _require_session_org(user)
    import base64
    
    # Create Basic Auth header
    credentials = base64.b64encode(f"{request.username}:{request.password}".encode()).decode()
    
    # Test connection
    try:
        client = get_http_client()
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
        organization_id=organization_id,
        erp_type="sap",
        access_token=credentials,  # Store Basic Auth as "token"
        refresh_token="",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        base_url=request.base_url,
    )
    save_erp_connection(record)
    
    logger.info(f"Connected SAP for organization {organization_id}")

    return {
        "status": "success",
        "erp": "sap",
        "organization_id": organization_id,
        "base_url": request.base_url,
    }


# ==================== DISCONNECT & STATUS ====================

def _require_session_org(user) -> str:
    """Derive the caller's org from the authenticated session.

    These routes used to accept ``organization_id`` from the URL — any
    user from tenant A could pass ``?organization_id=tenantB`` and
    operate on tenant B's ERP connection (delete/refresh/inspect).
    Now the org comes solely from the session and the URL parameter
    is gone.
    """
    organization_id = str(getattr(user, "organization_id", "") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=403, detail="user_missing_organization_id")
    return organization_id


@router.delete("/{erp}/disconnect")
async def disconnect_erp(erp: str, user=Depends(get_current_user)):
    """Disconnect an ERP integration for the caller's organization."""
    organization_id = _require_session_org(user)
    record = get_erp_connection_record(organization_id)

    if not record or record.erp_type != erp:
        raise HTTPException(status_code=404, detail=f"{erp} not connected for this organization")

    delete_erp_connection(organization_id)

    logger.info(f"Disconnected {erp} for organization {organization_id}")

    return {"status": "success", "message": f"{erp} disconnected"}


@router.get("/status")
async def get_connection_status(user=Depends(get_current_user)) -> ConnectionStatus:
    """Get ERP connection status for the caller's organization."""
    organization_id = _require_session_org(user)
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
async def refresh_token(user=Depends(get_current_user)):
    """Manually trigger token refresh for the caller's organization."""
    organization_id = _require_session_org(user)
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
