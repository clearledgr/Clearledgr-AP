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
from urllib.parse import urlparse
from datetime import datetime, timezone
from urllib.parse import urlencode

from clearledgr.core.http_client import get_http_client
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.integrations.erp_router import ERPConnection, set_erp_connection, get_erp_connection

logger = logging.getLogger(__name__)


# §15 Onboarding Design Rule: "If the ERP connection fails, the
# error message names the specific permission that is missing and
# links directly to where to grant it in the ERP. Generic
# 'connection failed' errors are not acceptable."
#
# _classify_erp_connect_error maps the exception the ERP SDK raised
# into a structured payload the extension can render with a named
# permission and a direct remediation link into the ERP's own admin
# console. Shared across QB / Xero / NetSuite / SAP handlers so the
# contract is consistent — the extension only has to handle the
# canonical error codes, not per-ERP exception shapes.

_ERP_REMEDIATION_LINKS = {
    "quickbooks": "https://app.qbo.intuit.com/app/apps/myapps",
    "xero": "https://go.xero.com/Settings/ConnectedApps",
    "netsuite": "https://system.netsuite.com/app/login/secure/enterpriseselectrole.nl",
    "sap": "https://help.sap.com/docs/SAP_S4HANA_CLOUD/authorization",
}


def _classify_erp_connect_error(erp_type: str, exc: Exception) -> Dict[str, Any]:
    """Map an ERP connection exception into a structured error
    payload per §15.

    Returns a dict with:
      code:              canonical error code the extension uses to
                         select copy + remediation link
      missing_permission: human name of the missing permission, or
                         None if the failure is not a permission issue
      remediation_link:  deep link into the ERP's admin console, or
                         None when the category doesn't map to a
                         single fix location
      message:           human-readable one-liner for the extension
      detail:            the raw exception text (for the audit log)
    """
    text = str(exc or "").lower()
    erp = (erp_type or "").lower().strip()
    remediation_link = _ERP_REMEDIATION_LINKS.get(erp)

    # Permission-specific classifiers, checked in order of
    # specificity.
    if any(tok in text for tok in ("unauthorized", "unauthoris", "401")):
        return {
            "code": "erp_unauthorized",
            "missing_permission": "AP read + write",
            "remediation_link": remediation_link,
            "message": (
                f"{erp_type} rejected the credentials. The signed-in user "
                "needs AP read + write access. Ask your admin to grant "
                "that role in the ERP and reconnect."
            ),
            "detail": str(exc),
        }
    if any(tok in text for tok in ("forbidden", "permission denied", "insufficient", "403")):
        return {
            "code": "erp_missing_permission",
            "missing_permission": "Vendor master + bill posting",
            "remediation_link": remediation_link,
            "message": (
                f"The connected {erp_type} user is missing write access "
                "to the vendor master and the bill-posting endpoint. "
                "Grant those permissions in the ERP admin console and "
                "reconnect."
            ),
            "detail": str(exc),
        }
    if any(tok in text for tok in ("scope", "oauth scope", "invalid_scope")):
        return {
            "code": "erp_missing_scope",
            "missing_permission": "OAuth scope (com.intuit.quickbooks.accounting / accounting.transactions / bills.read bills.write)",
            "remediation_link": remediation_link,
            "message": (
                f"The {erp_type} OAuth consent did not grant all the "
                "scopes Clearledgr needs (read POs + GRNs + vendor "
                "master, write bills). Retry the connect flow and "
                "accept all requested scopes."
            ),
            "detail": str(exc),
        }
    if any(tok in text for tok in ("not found", "404", "account_id", "realm")):
        return {
            "code": "erp_account_not_found",
            "missing_permission": None,
            "remediation_link": remediation_link,
            "message": (
                f"The {erp_type} account identifier we received does not "
                "resolve to an active company. Verify the account ID and "
                "retry."
            ),
            "detail": str(exc),
        }
    if any(tok in text for tok in ("timeout", "timed out", "connection reset", "502", "503", "504")):
        return {
            "code": "erp_transient",
            "missing_permission": None,
            "remediation_link": None,
            "message": (
                f"{erp_type} did not respond in time. This is usually "
                "transient — retry in a minute. If it persists, check "
                "your ERP's status page."
            ),
            "detail": str(exc),
        }

    # Fallback — still structured, but names the unknown category so
    # the extension can surface "something we didn't anticipate"
    # rather than bare exception text.
    return {
        "code": "erp_connection_failed",
        "missing_permission": None,
        "remediation_link": remediation_link,
        "message": (
            f"Could not connect to {erp_type}. Your admin can check the "
            "permissions and OAuth app registration; the specific error "
            "detail is in the logs."
        ),
        "detail": str(exc),
    }

router = APIRouter(prefix="/erp", tags=["erp-connections"])
_ADMIN_ROLES = {"admin", "owner"}


def _audit_erp_admin_action(
    *,
    user: TokenData,
    org_id: str,
    erp: str,
    action: str,
    success: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an admin-action audit event for ERP connection mutations.

    Connect/disconnect/refresh of an ERP credential is a high-impact
    admin action — it controls where bills get posted, which means it
    controls where money moves. Compliance + incident response need a
    record of who did what, when. Best-effort: if the audit write
    fails we don't roll back the action (we'd rather complete the
    user's intent than block them on telemetry), but we do log so the
    gap is visible.
    """
    try:
        db = get_db()
        actor_id = str(getattr(user, "user_id", "") or "").strip() or "unknown"
        actor_email = str(getattr(user, "email", "") or "").strip() or None
        payload: Dict[str, Any] = {
            "erp": erp,
            "actor_email": actor_email,
            "success": bool(success),
        }
        if extra:
            payload.update(extra)
        db.append_audit_event({
            "event_type": f"erp_admin_action:{action}",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": org_id,
            "source": "erp_admin",
            "payload_json": payload,
        })
    except Exception:
        logger.warning(
            "[erp_admin_audit] failed to write audit event for org=%s erp=%s action=%s",
            org_id, erp, action,
        )


def _resolve_org_id(user: TokenData, requested_org: str) -> str:
    """Resolve + enforce tenant scope.

    Admin/owner role elevates WHAT the user can do within their org,
    never WHICH org they can access. No cross-tenant access via role.
    """
    org_id = str(requested_org or user.organization_id or "default").strip() or "default"
    if org_id != str(user.organization_id or "").strip():
        raise HTTPException(status_code=403, detail="org_mismatch")
    return org_id


# ==================== CONFIGURATION ====================

# QuickBooks OAuth
QUICKBOOKS_CLIENT_ID = os.getenv("QUICKBOOKS_CLIENT_ID", "")
QUICKBOOKS_CLIENT_SECRET = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
QUICKBOOKS_REDIRECT_URI = os.getenv("QUICKBOOKS_REDIRECT_URI", "http://localhost:8010/erp/quickbooks/callback")
QUICKBOOKS_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QUICKBOOKS_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Xero OAuth
XERO_CLIENT_ID = os.getenv("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET", "")
XERO_REDIRECT_URI = os.getenv("XERO_REDIRECT_URI", "http://localhost:8010/erp/xero/callback")
XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"

# NetSuite (TBA - Token Based Auth, no OAuth flow needed)
NETSUITE_ACCOUNT_ID = os.getenv("NETSUITE_ACCOUNT_ID", "")
NETSUITE_CONSUMER_KEY = os.getenv("NETSUITE_CONSUMER_KEY", "")
NETSUITE_CONSUMER_SECRET = os.getenv("NETSUITE_CONSUMER_SECRET", "")
NETSUITE_TOKEN_ID = os.getenv("NETSUITE_TOKEN_ID", "")
NETSUITE_TOKEN_SECRET = os.getenv("NETSUITE_TOKEN_SECRET", "")

# OAuth state is stored in the DB (erp_oauth_states table, migration v10)
# so it works across multiple workers / processes.

# Frontend URL for redirects after OAuth
FRONTEND_URL = os.getenv("FRONTEND_URL", "/")


def _postmessage_target_origin() -> str:
    """Origin to use for postMessage back to the opener window.

    Using '*' lets ANY page that opens the OAuth popup intercept the
    completion signal and spoof connection state in the opener. We lock
    it to FRONTEND_URL's origin so only our own app can receive it.

    If FRONTEND_URL is a relative path ('/' during local dev without an
    explicit FRONTEND_URL env var), there is no origin to lock to —
    fall back to '*' only in that case.
    """
    try:
        parsed = urlparse(FRONTEND_URL)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return "*"


def _popup_close_response(
    erp: str,
    success: bool,
    organization_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> HTMLResponse:
    """Return a tiny HTML page that closes the OAuth popup and notifies
    the opener window (the Gmail tab running the extension). Used by
    ERP OAuth callbacks instead of RedirectResponse, because:
      1. The callback runs on api.clearledgr.com, which is the backend
         API — redirecting here loops back into the strict route filter
         ("/" isn't allowlisted).
      2. The opener already listens for a postMessage of shape
         {type:'clearledgr_erp_oauth_complete', erp, success, ...} and
         refreshes the connections page / onboarding state.
    """
    status_line = "Connected to " + erp.title() if success else "Connection failed"
    detail_line = (
        "You can close this window."
        if success
        else f"{detail or 'Unknown error'}. Close this window and try again."
    )
    color = "#00D67E" if success else "#FCA5A5"
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Clearledgr — {erp.title()}</title>
    <style>
      body{{font-family:-apple-system,'Segoe UI',sans-serif;background:#0A1628;color:#fff;
           display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
      .card{{text-align:center;padding:32px;max-width:360px}}
      h1{{font-size:18px;font-weight:600;margin:0 0 8px;color:{color}}}
      p{{font-size:13px;opacity:0.7;margin:0}}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>{status_line}</h1>
      <p>{detail_line}</p>
    </div>
    <script>
      (function () {{
        var payload = {{
          type: 'clearledgr_erp_oauth_complete',
          erp: {json.dumps(erp)},
          success: {('true' if success else 'false')},
          organizationId: {json.dumps(organization_id or '')},
          detail: {json.dumps(detail or '')}
        }};
        var targetOrigin = {json.dumps(_postmessage_target_origin())};
        try {{
          if (window.opener && !window.opener.closed) {{
            window.opener.postMessage(payload, targetOrigin);
          }}
        }} catch (_) {{ /* opener may be gone */ }}
        setTimeout(function () {{ try {{ window.close(); }} catch (_) {{}} }}, 800);
      }})();
    </script>
  </body>
</html>"""
    return HTMLResponse(content=html)


def _validate_return_url(url: Optional[str]) -> str:
    """Validate return_url is same-origin as FRONTEND_URL to prevent open redirects."""
    default = f"{FRONTEND_URL}/settings/erp"
    if not url:
        return default
    try:
        parsed = urlparse(url)
        frontend_parsed = urlparse(FRONTEND_URL)
        # Only allow same-host redirects (or relative paths)
        if parsed.scheme and parsed.netloc:
            if parsed.netloc != frontend_parsed.netloc:
                logger.warning("Blocked open redirect attempt to %s", parsed.netloc)
                return default
        elif url.startswith("//"):
            return default
        return url
    except Exception:
        return default


def _save_oauth_state(state: str, data: Dict[str, Any]) -> None:
    """Persist an OAuth state token to the database."""
    db = get_db()
    with db.connect() as conn:
        cur = conn.cursor()
        sql = (
            "INSERT INTO erp_oauth_states (state, organization_id, return_url, erp_type, created_at) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        cur.execute(sql, (
            state,
            data["organization_id"],
            data.get("return_url"),
            data.get("erp_type"),
            data["created_at"],
        ))
        conn.commit()


def _pop_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Look up and atomically delete an OAuth state token from the database.

    Returns the state data dict, or ``None`` if not found.
    """
    db = get_db()
    with db.connect() as conn:
        cur = conn.cursor()
        sql = (
            "SELECT organization_id, return_url, erp_type, created_at "
            "FROM erp_oauth_states WHERE state = %s"
        )
        cur.execute(sql, (state,))
        row = cur.fetchone()
        if row is None:
            return None
        # Delete atomically so a replay attempt fails
        del_sql = "DELETE FROM erp_oauth_states WHERE state = %s"
        cur.execute(del_sql, (state,))
        conn.commit()
        # Normalise to plain dict (handles both sqlite3.Row and psycopg dict_row)
        if isinstance(row, dict):
            return dict(row)
        return {
            "organization_id": row[0],
            "return_url": row[1],
            "erp_type": row[2],
            "created_at": row[3],
        }


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
async def get_connection_status(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """
    Get ERP connection status for an organization.
    
    Returns connected ERPs and their status.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    connections = db.get_erp_connections(org_id)
    
    result = {
        "organization_id": org_id,
        "connections": {},
        "available_erps": ["quickbooks", "xero", "netsuite", "sap"],
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
async def quickbooks_connect(
    request: ConnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """
    Start QuickBooks OAuth flow.
    
    Returns URL to redirect user to for authorization.
    """
    if not QUICKBOOKS_CLIENT_ID:
        raise HTTPException(status_code=500, detail="QuickBooks not configured")
    
    org_id = _resolve_org_id(user, request.organization_id)
    # Generate state token
    state = secrets.token_urlsafe(32)
    _save_oauth_state(state, {
        "organization_id": org_id,
        "return_url": _validate_return_url(request.return_url),
        "erp_type": "quickbooks",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

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
        return _popup_close_response("quickbooks", success=False, detail=error)

    if not state:
        return _popup_close_response("quickbooks", success=False, detail="invalid_state")

    state_data = _pop_oauth_state(state)
    if state_data is None:
        return _popup_close_response("quickbooks", success=False, detail="invalid_state")
    # Enforce 15-minute TTL on OAuth state tokens
    created = state_data.get("created_at", "")
    if created:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds()
        if age > 900:
            return _popup_close_response("quickbooks", success=False, detail="state_expired")
    organization_id = state_data["organization_id"]

    if not code or not realmId:
        return _popup_close_response(
            "quickbooks", success=False, organization_id=organization_id, detail="missing_params"
        )

    # Exchange code for tokens. The try/except previously collapsed
    # every failure mode (missing env var, Intuit 400 with error body,
    # network timeout) to the opaque "token_exchange_failed" popup —
    # which made field debugging impossible. Log the HTTP status + body
    # when available so Sentry/Railway logs show exactly why Intuit said
    # no, and surface a sub-detail in the popup so the operator can
    # distinguish config errors from transient network issues.
    if not QUICKBOOKS_CLIENT_ID or not QUICKBOOKS_CLIENT_SECRET:
        logger.error("QuickBooks token exchange: client_id/secret not configured")
        return _popup_close_response(
            "quickbooks", success=False, organization_id=organization_id,
            detail="token_exchange_failed:missing_credentials",
        )
    try:
        client = get_http_client()
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
        if response.status_code >= 400:
            body = (response.text or "")[:500]
            logger.error(
                "QuickBooks token exchange failed: status=%s redirect_uri=%s body=%s",
                response.status_code, QUICKBOOKS_REDIRECT_URI, body,
            )
            return _popup_close_response(
                "quickbooks", success=False, organization_id=organization_id,
                detail=f"token_exchange_failed:http_{response.status_code}",
            )
        tokens = response.json()
    except Exception as e:
        logger.error(
            "QuickBooks token exchange raised %s: %s (redirect_uri=%s)",
            type(e).__name__, e, QUICKBOOKS_REDIRECT_URI,
        )
        return _popup_close_response(
            "quickbooks",
            success=False,
            organization_id=organization_id,
            detail=f"token_exchange_failed:{type(e).__name__}",
        )

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

    return _popup_close_response(
        "quickbooks", success=True, organization_id=organization_id
    )


@router.post("/quickbooks/disconnect")
async def quickbooks_disconnect(
    request: DisconnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """Disconnect QuickBooks from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection

    org_id = _resolve_org_id(user, request.organization_id)
    success = delete_erp_connection(org_id, "quickbooks")
    _audit_erp_admin_action(
        user=user, org_id=org_id, erp="quickbooks",
        action="disconnect", success=bool(success),
    )
    return {"success": success, "erp": "quickbooks"}


# ==================== XERO OAUTH ====================

@router.post("/xero/connect")
async def xero_connect(
    request: ConnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """
    Start Xero OAuth flow.
    
    Returns URL to redirect user to for authorization.
    """
    if not XERO_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Xero not configured")
    
    org_id = _resolve_org_id(user, request.organization_id)
    state = secrets.token_urlsafe(32)
    _save_oauth_state(state, {
        "organization_id": org_id,
        "return_url": _validate_return_url(request.return_url),
        "erp_type": "xero",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

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
        return _popup_close_response("xero", success=False, detail=error)

    if not state:
        return _popup_close_response("xero", success=False, detail="invalid_state")

    state_data = _pop_oauth_state(state)
    if state_data is None:
        return _popup_close_response("xero", success=False, detail="invalid_state")
    # Enforce 15-minute TTL on OAuth state tokens
    created = state_data.get("created_at", "")
    if created:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds()
        if age > 900:
            return _popup_close_response("xero", success=False, detail="state_expired")
    organization_id = state_data["organization_id"]

    if not code:
        return _popup_close_response(
            "xero", success=False, organization_id=organization_id, detail="missing_code"
        )

    # Exchange code for tokens
    try:
        client = get_http_client()
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
        return _popup_close_response(
            "xero",
            success=False,
            organization_id=organization_id,
            detail="token_exchange_failed",
        )

    # Get tenant ID (Xero organization)
    tenant_id = None
    try:
        client = get_http_client()
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

    return _popup_close_response(
        "xero", success=True, organization_id=organization_id
    )


@router.post("/xero/disconnect")
async def xero_disconnect(
    request: DisconnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """Disconnect Xero from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection

    org_id = _resolve_org_id(user, request.organization_id)
    success = delete_erp_connection(org_id, "xero")
    _audit_erp_admin_action(
        user=user, org_id=org_id, erp="xero",
        action="disconnect", success=bool(success),
    )
    return {"success": success, "erp": "xero"}


# ==================== NETSUITE TBA ====================

@router.post("/netsuite/connect")
async def netsuite_connect(
    credentials: NetSuiteCredentials,
    user: TokenData = Depends(get_current_user),
):
    """
    Connect NetSuite using Token-Based Authentication.
    
    NetSuite uses TBA (OAuth 1.0 style) not OAuth 2.0.
    Credentials are generated in NetSuite UI and provided here.
    """
    org_id = _resolve_org_id(user, credentials.organization_id)
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
    from clearledgr.integrations.erp_router import get_netsuite_accounts
    
    try:
        accounts = await get_netsuite_accounts(connection)
        if accounts is None:
            raise Exception("Failed to fetch accounts")
        
        # Store connection
        set_erp_connection(org_id, connection)

        logger.info(f"NetSuite connected for org {org_id}")
        _audit_erp_admin_action(
            user=user, org_id=org_id, erp="netsuite",
            action="connect", success=True,
            extra={"account_id": credentials.account_id},
        )

        return {
            "success": True,
            "erp": "netsuite",
            "account_id": credentials.account_id,
            "organization_id": org_id,
            "accounts_found": len(accounts) if accounts else 0,
        }
        
    except Exception as e:
        logger.error(f"NetSuite connection failed: {e}")
        # §15 onboarding design rule — return structured error with
        # named permission + remediation link instead of
        # "Failed to connect: {e}".
        classified = _classify_erp_connect_error("netsuite", e)
        raise HTTPException(status_code=400, detail=classified)


@router.post("/netsuite/disconnect")
async def netsuite_disconnect(
    request: DisconnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """Disconnect NetSuite from organization."""
    from clearledgr.integrations.erp_router import delete_erp_connection

    org_id = _resolve_org_id(user, request.organization_id)
    success = delete_erp_connection(org_id, "netsuite")
    _audit_erp_admin_action(
        user=user, org_id=org_id, erp="netsuite",
        action="disconnect", success=bool(success),
    )
    return {"success": success, "erp": "netsuite"}


# ==================== TOKEN REFRESH ====================

@router.post("/refresh/{organization_id}/{erp_type}")
async def refresh_tokens(
    organization_id: str,
    erp_type: str,
    user: TokenData = Depends(get_current_user),
):
    """
    Manually refresh tokens for an ERP connection.
    
    Normally tokens are refreshed automatically, but this allows manual refresh.
    """
    org_id = _resolve_org_id(user, organization_id)
    connection = get_erp_connection(org_id)
    
    if not connection or connection.type != erp_type:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    # Route manual refresh through the same dedupe wrapper the post
    # paths use, so a manual click that races with an in-flight auto-
    # refresh doesn't burn the refresh_token (QB invalidates the prior
    # RT on every successful refresh — concurrent refreshes lose the
    # connection entirely).
    if erp_type == "quickbooks":
        from clearledgr.integrations.erp_router import (
            refresh_quickbooks_token, refresh_with_dedupe,
        )
        new_token = await refresh_with_dedupe(
            organization_id=org_id, erp_type="quickbooks",
            connection=connection, refresh_fn=refresh_quickbooks_token,
        )
    elif erp_type == "xero":
        from clearledgr.integrations.erp_router import (
            refresh_xero_token, refresh_with_dedupe,
        )
        new_token = await refresh_with_dedupe(
            organization_id=org_id, erp_type="xero",
            connection=connection, refresh_fn=refresh_xero_token,
        )
    else:
        raise HTTPException(status_code=400, detail="Token refresh not supported for this ERP")
    
    if new_token:
        # Update stored connection
        set_erp_connection(org_id, connection)
        return {"success": True, "erp": erp_type}
    
    return {"success": False, "error": "Token refresh failed"}


# ==================== CHART OF ACCOUNTS ====================

@router.get("/accounts/{organization_id}")
async def get_chart_of_accounts(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
):
    """
    Get chart of accounts from connected ERP.
    
    Used for GL account mapping.
    """
    org_id = _resolve_org_id(user, organization_id)
    connection = get_erp_connection(org_id)
    
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
        "organization_id": org_id,
        "erp": connection.type,
        "accounts": accounts,
    }


async def _get_quickbooks_accounts(connection: ERPConnection) -> list:
    """Fetch chart of accounts from QuickBooks."""
    if not connection.access_token or not connection.realm_id:
        return []
    
    try:
        client = get_http_client()
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
        client = get_http_client()
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


# ==================== GL ACCOUNT MAP ====================

class GLAccountMapRequest(BaseModel):
    gl_account_map: Dict[str, str]


@router.get("/gl-map")
async def get_gl_account_map(
    organization_id: str = Query(default="default"),
    user: TokenData = Depends(get_current_user),
):
    """Return the per-tenant GL account code mapping stored in org settings.

    Keys are account type names (e.g. "expenses", "accounts_payable");
    values are ERP-specific account codes. An empty dict means the system
    defaults from DEFAULT_ACCOUNT_MAP are used.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return {
        "organization_id": org_id,
        "gl_account_map": dict(settings.get("gl_account_map") or {}),
    }


@router.put("/gl-map")
async def update_gl_account_map(
    body: GLAccountMapRequest,
    organization_id: str = Query(default="default"),
    user: TokenData = Depends(get_current_user),
):
    """Store a per-tenant GL account code mapping in org settings.

    Pass the full mapping dict — existing keys not in the request are removed.
    Example body: {"gl_account_map": {"expenses": "6100", "accounts_payable": "2000"}}
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    settings["gl_account_map"] = dict(body.gl_account_map)
    db.update_organization(org_id, settings_json=settings)
    return {
        "organization_id": org_id,
        "gl_account_map": settings["gl_account_map"],
    }
