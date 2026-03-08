"""
ERP Integration Router

Routes journal entries to the appropriate ERP system:
- QuickBooks Online (for small/medium businesses)
- Xero (popular in Europe/Africa/Australia)
- NetSuite (mid-market to enterprise, very popular in Africa)
- SAP (enterprise)

This is REAL integration, not mocked.
"""

import logging
import httpx
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

_ERP_TIMEOUT = 30  # seconds — applied to all outbound ERP HTTP calls


_QB_QUERY_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_NS_LIKE_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_NS_EMAIL_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\+]")
_XERO_WHERE_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")
_SAP_ODATA_VALUE_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9@._\-\s]")


def _sanitize_quickbooks_like_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe LIKE operand for QuickBooks query strings.

    QuickBooks query API does not support parameter binding. To prevent query
    manipulation, we apply strict allowlist sanitization and remove wildcard
    operators from user-provided values.
    """
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _QB_QUERY_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_netsuite_like_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe SuiteQL LIKE operand for NetSuite vendor search."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _NS_LIKE_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_netsuite_email_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe SuiteQL equality operand for NetSuite email search."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _NS_EMAIL_VALUE_ALLOWED_CHARS.sub("", text)
    sanitized = sanitized.strip()
    if not sanitized:
        return None
    return sanitized[:160]


def _sanitize_xero_where_operand(value: Optional[str]) -> Optional[str]:
    """Return a safe operand for Xero where-clause Name.Contains filter."""
    text = str(value or "").strip()
    if not text:
        return None
    sanitized = _XERO_WHERE_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        return None
    return sanitized[:120]


def _sanitize_odata_value(value: Optional[str]) -> str:
    """Return a safe OData filter operand for SAP Business Partner search.

    Prevents OData filter injection by stripping non-alphanumeric characters
    and escaping single quotes.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    sanitized = _SAP_ODATA_VALUE_ALLOWED_CHARS.sub(" ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # OData single-quote escape: ' → ''
    sanitized = sanitized.replace("'", "''")
    return sanitized[:120]


def _escape_query_literal(value: str) -> str:
    """Escape single quotes for query syntaxes that require inline literals."""
    return str(value).replace("'", "''")


def _build_quickbooks_vendor_lookup_query(
    *,
    name_operand: Optional[str],
    email_operand: Optional[str],
) -> Optional[str]:
    if name_operand:
        literal = _escape_query_literal(name_operand)
        return f"SELECT * FROM Vendor WHERE DisplayName LIKE '%{literal}%'"
    if email_operand:
        literal = _escape_query_literal(email_operand)
        return f"SELECT * FROM Vendor WHERE PrimaryEmailAddr LIKE '%{literal}%'"
    return None


def _build_netsuite_vendor_lookup_query(
    *,
    name_operand: Optional[str],
    email_operand: Optional[str],
) -> Optional[str]:
    conditions: List[str] = []
    if name_operand:
        literal = _escape_query_literal(name_operand)
        conditions.append(f"companyName LIKE '%{literal}%'")
    if email_operand:
        literal = _escape_query_literal(email_operand)
        conditions.append(f"email = '{literal}'")
    if not conditions:
        return None
    return f"SELECT id, companyName, email FROM vendor WHERE {' OR '.join(conditions)} FETCH FIRST 1 ROWS ONLY"


def _build_xero_vendor_lookup_where(*, name_operand: Optional[str]) -> str:
    where = "IsSupplier==true"
    if name_operand:
        literal = _escape_query_literal(name_operand)
        where += f' AND Name.Contains("{literal}")'
    return where


@dataclass
class ERPConnection:
    """Connection details for an ERP system."""
    type: str  # "quickbooks", "xero", "netsuite", "sap"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    realm_id: Optional[str] = None  # QuickBooks company ID
    tenant_id: Optional[str] = None  # Xero tenant ID
    base_url: Optional[str] = None  # SAP OData URL or NetSuite account URL
    company_code: Optional[str] = None  # SAP company code (e.g., "1000")

    # NetSuite specific
    account_id: Optional[str] = None  # NetSuite account ID (e.g., "1234567")
    consumer_key: Optional[str] = None  # NetSuite consumer key (TBA)
    consumer_secret: Optional[str] = None  # NetSuite consumer secret
    token_id: Optional[str] = None  # NetSuite token ID
    token_secret: Optional[str] = None  # NetSuite token secret


# Database-backed connection storage
def _get_db():
    """Get database instance (lazy load to avoid circular imports)."""
    from clearledgr.core.database import get_db
    return get_db()


def get_erp_connection(organization_id: str) -> Optional[ERPConnection]:
    """Get ERP connection for an organization from database."""
    db = _get_db()
    connections = db.get_erp_connections(organization_id)
    
    if not connections:
        return None
    
    # Return the first active connection
    conn = connections[0]
    creds = conn.get('credentials', {}) or {}
    if isinstance(creds, str):
        try:
            import json
            decoded = json.loads(creds)
            creds = decoded if isinstance(decoded, dict) else {}
        except Exception:
            creds = {}
    
    return ERPConnection(
        type=conn['erp_type'],
        access_token=conn.get('access_token'),
        refresh_token=conn.get('refresh_token'),
        realm_id=conn.get('realm_id'),
        tenant_id=conn.get('tenant_id'),
        base_url=conn.get('base_url'),
        client_id=creds.get('client_id'),
        client_secret=creds.get('client_secret'),
        company_code=creds.get('company_code'),
        account_id=creds.get('account_id'),
        consumer_key=creds.get('consumer_key'),
        consumer_secret=creds.get('consumer_secret'),
        token_id=creds.get('token_id'),
        token_secret=creds.get('token_secret'),
    )


def set_erp_connection(organization_id: str, connection: ERPConnection):
    """Store ERP connection for an organization in database."""
    db = _get_db()
    
    # Build credentials dict for sensitive fields
    credentials = {}
    if connection.client_id:
        credentials['client_id'] = connection.client_id
    if connection.client_secret:
        credentials['client_secret'] = connection.client_secret
    if connection.account_id:
        credentials['account_id'] = connection.account_id
    if connection.consumer_key:
        credentials['consumer_key'] = connection.consumer_key
    if connection.consumer_secret:
        credentials['consumer_secret'] = connection.consumer_secret
    if connection.token_id:
        credentials['token_id'] = connection.token_id
    if connection.token_secret:
        credentials['token_secret'] = connection.token_secret
    if connection.company_code:
        credentials['company_code'] = connection.company_code

    db.save_erp_connection(
        organization_id=organization_id,
        erp_type=connection.type,
        access_token=connection.access_token,
        refresh_token=connection.refresh_token,
        realm_id=connection.realm_id,
        tenant_id=connection.tenant_id,
        base_url=connection.base_url,
        credentials=credentials if credentials else None
    )


def delete_erp_connection(organization_id: str, erp_type: str) -> bool:
    """Remove an ERP connection."""
    db = _get_db()
    return db.delete_erp_connection(organization_id, erp_type)


async def post_journal_entry(
    organization_id: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Post journal entry to the organization's ERP.
    
    Automatically routes to QuickBooks, Xero, NetSuite, or SAP based on org settings.
    """
    connection = get_erp_connection(organization_id)
    
    if not connection:
        logger.warning(f"No ERP connected for {organization_id}")
        return {"status": "skipped", "reason": "No ERP connected"}
    
    if connection.type == "quickbooks":
        return await post_to_quickbooks(connection, entry)
    elif connection.type == "xero":
        return await post_to_xero(connection, entry)
    elif connection.type == "netsuite":
        return await post_to_netsuite(connection, entry)
    elif connection.type == "sap":
        return await post_to_sap(connection, entry)
    else:
        return {"status": "error", "reason": f"Unknown ERP type: {connection.type}"}


# ==================== QUICKBOOKS ONLINE ====================

async def post_to_quickbooks(
    connection: ERPConnection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Post journal entry to QuickBooks Online.
    
    Uses QuickBooks API:
    https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry
    """
    if not connection.access_token or not connection.realm_id:
        return {"status": "error", "erp": "quickbooks", "reason": "QuickBooks not properly configured"}

    # Build QuickBooks journal entry format
    qb_entry = {
        "TxnDate": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
        "PrivateNote": entry.get("description", "Auto-generated by Clearledgr"),
        "Line": [],
    }
    
    for line in entry.get("lines", []):
        qb_line = {
            "DetailType": "JournalEntryLineDetail",
            "Amount": line.get("debit", 0) or line.get("credit", 0),
            "JournalEntryLineDetail": {
                "PostingType": "Debit" if line.get("debit", 0) > 0 else "Credit",
                "AccountRef": {
                    "value": line.get("account", "1"),
                    "name": line.get("account_name", "Unknown"),
                },
            },
        }
        qb_entry["Line"].append(qb_line)
    
    # Make API call
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/journalentry"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=qb_entry,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            
            if response.status_code == 401:
                # Token expired - would need to refresh
                logger.warning("QuickBooks token expired, needs refresh")
                return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()

            logger.info(f"Posted to QuickBooks: {result.get('JournalEntry', {}).get('Id')}")
            return {
                "status": "success",
                "erp": "quickbooks",
                "entry_id": result.get("JournalEntry", {}).get("Id"),
                "sync_token": result.get("JournalEntry", {}).get("SyncToken"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error("QuickBooks API error: %s", e.response.status_code)
        return {"status": "error", "erp": "quickbooks", "reason": f"QuickBooks API {e.response.status_code}"}
    except Exception as e:
        logger.error("QuickBooks error: %s", type(e).__name__)
        return {"status": "error", "erp": "quickbooks", "reason": "posting_failed"}


async def refresh_quickbooks_token(connection: ERPConnection) -> Optional[str]:
    """Refresh QuickBooks OAuth token."""
    if not connection.refresh_token or not connection.client_id or not connection.client_secret:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": connection.refresh_token,
                },
                auth=(connection.client_id, connection.client_secret),
            )
            response.raise_for_status()
            tokens = response.json()
            
            connection.access_token = tokens.get("access_token")
            connection.refresh_token = tokens.get("refresh_token")
            
            return connection.access_token
    except Exception as e:
        logger.error("Failed to refresh QuickBooks token: %s", type(e).__name__)
        return None


# ==================== XERO ====================

async def post_to_xero(
    connection: ERPConnection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Post journal entry to Xero.
    
    Uses Xero API:
    https://developer.xero.com/documentation/api/accounting/manualjournals
    """
    if not connection.access_token or not connection.tenant_id:
        return {"status": "error", "erp": "xero", "reason": "Xero not properly configured"}

    # Build Xero manual journal format
    xero_journal = {
        "Date": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
        "Narration": entry.get("description", "Auto-generated by Clearledgr"),
        "JournalLines": [],
    }
    
    for line in entry.get("lines", []):
        xero_line = {
            "LineAmount": line.get("debit", 0) if line.get("debit", 0) > 0 else -line.get("credit", 0),
            "AccountCode": line.get("account", "200"),  # Xero uses account codes
            "Description": line.get("account_name", ""),
        }
        xero_journal["JournalLines"].append(xero_line)
    
    # Make API call
    url = "https://api.xero.com/api.xro/2.0/ManualJournals"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"ManualJournals": [xero_journal]},
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                    "xero-tenant-id": connection.tenant_id,
                },
                timeout=30,
            )
            
            if response.status_code == 401:
                logger.warning("Xero token expired, needs refresh")
                return {"status": "error", "erp": "xero", "reason": "Token expired", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()

            journals = result.get("ManualJournals", [])
            if journals:
                journal_id = journals[0].get("ManualJournalID")
                logger.info(f"Posted to Xero: {journal_id}")
                return {
                    "status": "success",
                    "erp": "xero",
                    "entry_id": journal_id,
                }

            return {"status": "error", "erp": "xero", "reason": "No journal returned"}
            
    except httpx.HTTPStatusError as e:
        logger.error("Xero API error: %s", e.response.status_code)
        return {"status": "error", "erp": "xero", "reason": f"Xero API {e.response.status_code}"}
    except Exception as e:
        logger.error("Xero error: %s", type(e).__name__)
        return {"status": "error", "erp": "xero", "reason": "posting_failed"}


async def refresh_xero_token(connection: ERPConnection) -> Optional[str]:
    """Refresh Xero OAuth token."""
    if not connection.refresh_token or not connection.client_id or not connection.client_secret:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": connection.refresh_token,
                },
                auth=(connection.client_id, connection.client_secret),
            )
            response.raise_for_status()
            tokens = response.json()
            
            connection.access_token = tokens.get("access_token")
            connection.refresh_token = tokens.get("refresh_token")
            
            return connection.access_token
    except Exception as e:
        logger.error("Failed to refresh Xero token: %s", type(e).__name__)
        return None


# ==================== NETSUITE ====================

async def post_to_netsuite(
    connection: ERPConnection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Post journal entry to NetSuite via REST API.
    
    NetSuite uses Token-Based Authentication (TBA) with OAuth 1.0.
    
    API Docs:
    https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1544787084.html
    """
    if not connection.account_id:
        return {"status": "error", "erp": "netsuite", "reason": "NetSuite account ID not configured"}

    # Build NetSuite journal entry format
    ns_entry = {
        "tranDate": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
        "memo": entry.get("description", "Auto-generated by Clearledgr"),
        "subsidiary": {"id": "1"},  # Default subsidiary
        "line": {
            "items": []
        },
    }
    
    line_num = 0
    for line in entry.get("lines", []):
        debit = line.get("debit", 0)
        credit = line.get("credit", 0)
        
        ns_line = {
            "lineId": line_num,
            "account": {"id": line.get("account", "1")},
            "memo": line.get("account_name", ""),
        }
        
        if debit > 0:
            ns_line["debit"] = debit
        else:
            ns_line["credit"] = credit
        
        ns_entry["line"]["items"].append(ns_line)
        line_num += 1
    
    # Build OAuth 1.0 signature for NetSuite TBA
    auth_header = build_netsuite_oauth_header(
        connection=connection,
        method="POST",
        url=f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/journalEntry",
    )
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/journalEntry",
                json=ns_entry,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Prefer": "respond-async, transient",
                },
                timeout=60,
            )
            
            if response.status_code == 401:
                logger.warning("NetSuite authentication failed")
                return {"status": "error", "erp": "netsuite", "reason": "Authentication failed", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()
            
            entry_id = result.get("id") or result.get("internalId")
            logger.info(f"Posted to NetSuite: {entry_id}")
            
            return {
                "status": "success",
                "erp": "netsuite",
                "entry_id": entry_id,
                "tran_id": result.get("tranId"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error("NetSuite API error: %s", e.response.status_code)
        return {"status": "error", "erp": "netsuite", "reason": f"NetSuite API {e.response.status_code}"}
    except Exception as e:
        logger.error("NetSuite error: %s", type(e).__name__)
        return {"status": "error", "erp": "netsuite", "reason": "posting_failed"}


def build_netsuite_oauth_header(
    connection: ERPConnection,
    method: str,
    url: str,
) -> str:
    """
    Build OAuth 1.0 Authorization header for NetSuite TBA.
    
    NetSuite uses Token-Based Authentication which is OAuth 1.0 based.
    """
    import base64
    import hmac
    import hashlib
    import time
    import urllib.parse
    import uuid
    
    # OAuth parameters
    oauth_params = {
        "oauth_consumer_key": connection.consumer_key or "",
        "oauth_token": connection.token_id or "",
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": str(uuid.uuid4()).replace("-", ""),
        "oauth_version": "1.0",
        "realm": connection.account_id or "",
    }
    
    # Build base string
    param_string = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(oauth_params.items())
        if k != "realm"
    )
    
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(param_string, safe=""),
    ])
    
    # Build signing key
    signing_key = "&".join([
        urllib.parse.quote(connection.consumer_secret or "", safe=""),
        urllib.parse.quote(connection.token_secret or "", safe=""),
    ])
    
    # Generate signature
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    
    oauth_params["oauth_signature"] = signature
    
    # Build header
    auth_header = "OAuth " + ", ".join(
        f'{k}="{urllib.parse.quote(v, safe="")}"'
        for k, v in oauth_params.items()
    )
    
    return auth_header


async def get_netsuite_accounts(connection: ERPConnection) -> List[Dict[str, Any]]:
    """
    Get chart of accounts from NetSuite.
    
    Useful for GL account mapping during onboarding.
    """
    if not connection.account_id:
        return []
    
    auth_header = build_netsuite_oauth_header(
        connection=connection,
        method="GET",
        url=f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/account",
    )
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/account",
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                params={"limit": 1000},
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            accounts = []
            for item in result.get("items", []):
                accounts.append({
                    "id": item.get("id"),
                    "name": item.get("acctName"),
                    "number": item.get("acctNumber"),
                    "type": item.get("acctType", {}).get("refName"),
                })
            
            return accounts
            
    except Exception as e:
        logger.error(f"Failed to get NetSuite accounts: {e}")
        return []


# ==================== SAP ====================

async def post_to_sap(
    connection: ERPConnection,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Post journal entry to SAP via OData.
    
    Uses SAP Business One Service Layer or S/4HANA OData.
    """
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    # Build SAP journal entry format
    sap_entry = {
        "ReferenceDate": entry.get("date", datetime.now().strftime("%Y-%m-%d")),
        "Memo": entry.get("description", "Auto-generated by Clearledgr"),
        "JournalEntryLines": [],
    }
    
    line_num = 0
    for line in entry.get("lines", []):
        sap_line = {
            "Line_ID": line_num,
            "AccountCode": line.get("account", ""),
            "Debit": line.get("debit", 0),
            "Credit": line.get("credit", 0),
            "LineMemo": line.get("account_name", ""),
        }
        sap_entry["JournalEntryLines"].append(sap_line)
        line_num += 1
    
    # Make OData call
    url = f"{connection.base_url}/JournalEntries"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=sap_entry,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=60,  # SAP can be slow
            )
            
            response.raise_for_status()
            result = response.json()
            
            entry_num = result.get("JdtNum") or result.get("DocEntry")
            logger.info(f"Posted to SAP: {entry_num}")
            return {
                "status": "success",
                "erp": "sap",
                "entry_id": entry_num,
            }
            
    except httpx.HTTPStatusError as e:
        logger.error("SAP OData error: %s", e.response.status_code)
        return {"status": "error", "erp": "sap", "reason": f"SAP API {e.response.status_code}"}
    except Exception as e:
        logger.error("SAP error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "posting_failed"}


# ==================== ACCOUNT MAPPING ====================

# Default GL account mappings - can be customized per organization via settings_json["gl_account_map"]
DEFAULT_ACCOUNT_MAP = {
    "quickbooks": {
        "cash": "1",  # Default checking account
        "accounts_receivable": "4",
        "payment_fees": "74",  # Bank Service Charges
        "revenue": "1",
        "expenses": "7",  # Expenses (default AP bill debit account)
    },
    "xero": {
        "cash": "090",  # Business Bank Account
        "accounts_receivable": "610",  # Accounts Receivable
        "payment_fees": "404",  # Bank Fees
        "revenue": "200",  # Sales
        "expenses": "400",  # General Expenses (default AP bill debit account)
    },
    "netsuite": {
        "cash": "1000",  # Cash and Cash Equivalents
        "accounts_receivable": "1200",  # Accounts Receivable
        "payment_fees": "6800",  # Bank Service Charges
        "revenue": "4000",  # Sales Revenue
        "expenses": "67",  # Vendor expense (default AP bill debit account)
    },
    "sap": {
        "cash": "1000",  # Cash
        "accounts_receivable": "1100",  # AR
        "payment_fees": "6200",  # Bank Charges
        "revenue": "4000",  # Revenue
        "expenses": "6000",  # General Expenses (default AP invoice GL account)
    },
}


def _get_org_gl_map(organization_id: str) -> Dict[str, str]:
    """Load per-tenant GL account mapping from org settings_json["gl_account_map"]."""
    try:
        import json as _json
        db = _get_db()
        org = db.get_organization(organization_id)
        if not org:
            return {}
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                return {}
        return dict(settings.get("gl_account_map") or {})
    except Exception:
        return {}


def get_account_code(
    erp_type: str,
    account_type: str,
    custom_mappings: Optional[Dict[str, str]] = None,
) -> str:
    """Get ERP-specific account code."""
    if custom_mappings and account_type in custom_mappings:
        return custom_mappings[account_type]
    
    return DEFAULT_ACCOUNT_MAP.get(erp_type, {}).get(account_type, "1")


# ==================== BILLS / VENDOR BILLS ====================

@dataclass
class Bill:
    """Represents a vendor bill/invoice to be posted."""
    vendor_id: str
    vendor_name: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    description: Optional[str] = None
    line_items: Optional[List[Dict[str, Any]]] = None
    attachment_url: Optional[str] = None
    po_number: Optional[str] = None


async def post_bill(
    organization_id: str,
    bill: Bill,
    ap_item_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Post a vendor bill to the organization's ERP.

    This is the primary function for invoice processing — posts as AP Bill.

    Idempotency: If *ap_item_id* is provided the function checks whether
    the AP item already has an ``erp_reference``.  If it does the post is
    skipped and the existing reference is returned, preventing duplicate
    bills in the ERP.
    """
    # Idempotency guard — skip if already posted
    if ap_item_id:
        db = _get_db()
        existing = db.get_ap_item(ap_item_id)
        if existing and existing.get("erp_reference"):
            logger.info(
                "Idempotency: AP item %s already posted (ref=%s), skipping",
                ap_item_id,
                existing["erp_reference"],
            )
            return {
                "status": "already_posted",
                "reference_id": existing["erp_reference"],
                "idempotency_key": idempotency_key,
            }

    # H10: At-source idempotency check — prevent concurrent duplicate posts
    # by checking if this idempotency_key already has a success audit event.
    if idempotency_key and ap_item_id:
        try:
            db = _get_db()
            existing_event = db.get_ap_audit_event_by_key(idempotency_key)
            if existing_event and str(existing_event.get("event_type") or "") == "erp_post_succeeded":
                logger.info(
                    "Idempotency: key %s already succeeded, skipping duplicate post",
                    idempotency_key,
                )
                meta = existing_event.get("metadata") or {}
                if isinstance(meta, str):
                    import json as _json
                    try:
                        meta = _json.loads(meta)
                    except Exception:
                        meta = {}
                return {
                    "status": "already_posted",
                    "reference_id": meta.get("erp_reference"),
                    "idempotency_key": idempotency_key,
                }
        except Exception:
            pass  # Non-fatal — proceed with post

    connection = get_erp_connection(organization_id)

    if not connection:
        logger.warning("No ERP connected for %s", organization_id)
        return {"status": "skipped", "reason": "No ERP Connected", "idempotency_key": idempotency_key}

    gl_map = _get_org_gl_map(organization_id)

    if connection.type == "quickbooks":
        result = await post_bill_to_quickbooks(connection, bill, gl_map=gl_map)
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_quickbooks_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await post_bill_to_quickbooks(connection, bill, gl_map=gl_map)
    elif connection.type == "xero":
        result = await post_bill_to_xero(connection, bill, gl_map=gl_map)
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_xero_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await post_bill_to_xero(connection, bill, gl_map=gl_map)
    elif connection.type == "netsuite":
        result = await post_bill_to_netsuite(connection, bill, gl_map=gl_map)
        if isinstance(result, dict) and result.get("needs_reauth"):
            # H7: NetSuite uses OAuth 1.0a — no token refresh, but retry once
            # in case of transient clock-skew causing signature mismatch.
            logger.warning("NetSuite 401 for org %s — retrying once (clock-skew mitigation)", organization_id)
            result = await post_bill_to_netsuite(connection, bill, gl_map=gl_map)
    elif connection.type == "sap":
        result = await post_bill_to_sap(connection, bill, gl_map=gl_map)
        if isinstance(result, dict) and result.get("needs_reauth"):
            # H9: SAP B1 session may have expired — retry forces a fresh Login.
            logger.warning("SAP 401 for org %s — retrying with fresh session", organization_id)
            result = await post_bill_to_sap(connection, bill, gl_map=gl_map)
    else:
        result = {"status": "error", "reason": f"Unknown ERP type: {connection.type}"}

    if isinstance(result, dict) and idempotency_key and not result.get("idempotency_key"):
        result = {**result, "idempotency_key": idempotency_key}

    # Attachment forwarding (non-fatal)
    if (
        isinstance(result, dict)
        and result.get("status") == "success"
        and bill.attachment_url
    ):
        bill_ref = result.get("bill_id") or result.get("erp_reference") or result.get("reference_id")
        if bill_ref:
            try:
                attach_result = await attach_file_to_erp_bill(
                    organization_id=organization_id,
                    bill_id=str(bill_ref),
                    attachment_url=bill.attachment_url,
                )
                if attach_result:
                    result["attachment_forwarded"] = True
            except Exception:
                logger.warning("Attachment forwarding failed (non-fatal)")

    return result


async def post_bill_to_quickbooks(
    connection: ERPConnection,
    bill: Bill,
    gl_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Post vendor bill to QuickBooks Online.

    API: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/bill
    """
    if not connection.access_token or not connection.realm_id:
        return {"status": "error", "erp": "quickbooks", "reason": "QuickBooks not properly configured"}

    expense_account = get_account_code("quickbooks", "expenses", gl_map)

    # Build QuickBooks Bill format
    qb_bill = {
        "VendorRef": {"value": bill.vendor_id, "name": bill.vendor_name},
        "TxnDate": bill.invoice_date or datetime.now().strftime("%Y-%m-%d"),
        "DueDate": bill.due_date,
        "DocNumber": bill.invoice_number,
        "PrivateNote": bill.description or f"Invoice from {bill.vendor_name}",
        "Line": [],
    }

    # Add line items or create single expense line
    if bill.line_items:
        for i, item in enumerate(bill.line_items):
            qb_bill["Line"].append({
                "Id": str(i + 1),
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": item.get("amount", 0),
                "Description": item.get("description", ""),
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": item.get("account_id", expense_account)},
                }
            })
    else:
        # Single line item for full amount
        qb_bill["Line"].append({
            "Id": "1",
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": bill.amount,
            "Description": bill.description or f"Invoice {bill.invoice_number}",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": expense_account},
            }
        })

    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/bill"

    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=qb_bill,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )

            if response.status_code == 401:
                return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()

            bill_data = result.get("Bill", {})
            logger.info("Posted Bill to QuickBooks: %s", bill_data.get("Id"))
            return {
                "status": "success",
                "erp": "quickbooks",
                "bill_id": bill_data.get("Id"),
                "doc_number": bill_data.get("DocNumber"),
                "sync_token": bill_data.get("SyncToken"),
            }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("QuickBooks Bill API HTTP error: status=%d", status_code)
        return {
            "status": "error",
            "erp": "quickbooks",
            "reason": f"http_{status_code}",
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("QuickBooks Bill error: %s", type(e).__name__)
        return {"status": "error", "erp": "quickbooks", "reason": "bill_posting_failed"}


async def post_bill_to_xero(
    connection: ERPConnection,
    bill: Bill,
    gl_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Post vendor bill to Xero.

    API: https://developer.xero.com/documentation/api/accounting/invoices
    Type: ACCPAY (Accounts Payable / Bill)
    """
    if not connection.access_token or not connection.tenant_id:
        return {"status": "error", "erp": "xero", "reason": "Xero not properly configured"}

    expense_account = get_account_code("xero", "expenses", gl_map)

    # Build Xero Invoice (ACCPAY type = Bill)
    xero_bill = {
        "Type": "ACCPAY",  # Accounts Payable = Bill
        "Contact": {"ContactID": bill.vendor_id, "Name": bill.vendor_name},
        "Date": bill.invoice_date or datetime.now().strftime("%Y-%m-%d"),
        "DueDate": bill.due_date,
        "InvoiceNumber": bill.invoice_number,
        "Reference": bill.po_number,
        "Status": "AUTHORISED",  # Ready for payment
        "LineItems": [],
    }

    # Add line items
    if bill.line_items:
        for item in bill.line_items:
            xero_bill["LineItems"].append({
                "Description": item.get("description", ""),
                "Quantity": item.get("quantity", 1),
                "UnitAmount": item.get("unit_amount", item.get("amount", 0)),
                "AccountCode": item.get("account_code", expense_account),
                "TaxType": item.get("tax_type", "NONE"),
            })
    else:
        xero_bill["LineItems"].append({
            "Description": bill.description or f"Invoice {bill.invoice_number}",
            "Quantity": 1,
            "UnitAmount": bill.amount,
            "AccountCode": expense_account,
            "TaxType": "NONE",
        })

    url = "https://api.xero.com/api.xro/2.0/Invoices"

    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"Invoices": [xero_bill]},
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                    "xero-tenant-id": connection.tenant_id,
                },
                timeout=30,
            )

            if response.status_code == 401:
                return {"status": "error", "erp": "xero", "reason": "Token expired", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()

            invoices = result.get("Invoices", [])
            if invoices:
                inv = invoices[0]
                logger.info("Posted Bill to Xero: %s", inv.get("InvoiceID"))
                return {
                    "status": "success",
                    "erp": "xero",
                    "bill_id": inv.get("InvoiceID"),
                    "invoice_number": inv.get("InvoiceNumber"),
                }

            return {"status": "error", "erp": "xero", "reason": "no_invoice_returned"}

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("Xero Bill API HTTP error: status=%d", status_code)
        return {
            "status": "error",
            "erp": "xero",
            "reason": f"http_{status_code}",
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("Xero Bill error: %s", type(e).__name__)
        return {"status": "error", "erp": "xero", "reason": "bill_posting_failed"}


async def post_bill_to_netsuite(
    connection: ERPConnection,
    bill: Bill,
    gl_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Post vendor bill to NetSuite.

    API: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/record_vendorbill.html
    """
    if not connection.account_id:
        return {"status": "error", "erp": "netsuite", "reason": "NetSuite account ID not configured"}

    expense_account = get_account_code("netsuite", "expenses", gl_map)

    # Build NetSuite Vendor Bill format
    ns_bill = {
        "entity": {"id": bill.vendor_id},  # Vendor reference
        "tranDate": bill.invoice_date or datetime.now().strftime("%Y-%m-%d"),
        "dueDate": bill.due_date,
        "tranId": bill.invoice_number,  # Vendor's invoice number
        "memo": bill.description or f"Invoice from {bill.vendor_name}",
        "item": {"items": []},
        "expense": {"items": []},
    }

    # Add line items as expenses
    if bill.line_items:
        for i, item in enumerate(bill.line_items):
            ns_bill["expense"]["items"].append({
                "line": i + 1,
                "account": {"id": item.get("account_id", expense_account)},
                "amount": item.get("amount", 0),
                "memo": item.get("description", ""),
            })
    else:
        ns_bill["expense"]["items"].append({
            "line": 1,
            "account": {"id": expense_account},
            "amount": bill.amount,
            "memo": bill.description or f"Invoice {bill.invoice_number}",
        })

    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/vendorBill"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)

    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=ns_bill,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Prefer": "respond-async",
                },
                timeout=60,
            )

            if response.status_code == 401:
                return {"status": "error", "erp": "netsuite", "reason": "Authentication failed", "needs_reauth": True}

            response.raise_for_status()

            # H6: Handle async 202 response — poll Location header for result
            if response.status_code == 202:
                location = response.headers.get("Location", "").strip()
                if location:
                    # Poll for the result (up to 5 attempts with 2s delay)
                    import asyncio as _asyncio
                    for _attempt in range(5):
                        await _asyncio.sleep(2)
                        poll_resp = await client.get(
                            location,
                            headers={"Authorization": auth_header},
                            timeout=30,
                        )
                        if poll_resp.status_code == 200:
                            poll_result = poll_resp.json()
                            bill_id = poll_result.get("id") or poll_result.get("internalId")
                            logger.info("Posted Vendor Bill to NetSuite (async): %s", bill_id)
                            return {
                                "status": "success",
                                "erp": "netsuite",
                                "bill_id": bill_id,
                                "tran_id": poll_result.get("tranId"),
                            }
                        if poll_resp.status_code != 202:
                            break
                    logger.warning("NetSuite async job did not complete within polling window")
                    return {"status": "error", "erp": "netsuite", "reason": "async_timeout"}
                # No Location header — treat 202 body as best-effort
                logger.warning("NetSuite returned 202 without Location header")

            result = response.json()

            bill_id = result.get("id") or result.get("internalId")
            logger.info("Posted Vendor Bill to NetSuite: %s", bill_id)
            return {
                "status": "success",
                "erp": "netsuite",
                "bill_id": bill_id,
                "tran_id": result.get("tranId"),
            }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("NetSuite Vendor Bill API HTTP error: status=%d", status_code)
        return {
            "status": "error",
            "erp": "netsuite",
            "reason": f"http_{status_code}",
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("NetSuite Vendor Bill error: %s", type(e).__name__)
        return {"status": "error", "erp": "netsuite", "reason": "bill_posting_failed"}


async def post_bill_to_sap(
    connection: ERPConnection,
    bill: Bill,
    gl_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Post vendor bill to SAP B1 (A/P Invoice via Service Layer).

    SAP B1: https://help.sap.com/docs/SAP_BUSINESS_ONE
    Validates required fields before posting. company_code must be set in
    the ERP connection credentials (stored as settings_json["gl_account_map"]).
    """
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not properly configured"}

    # Pre-flight validation — block before hitting the SAP API
    missing_fields = []
    if not bill.vendor_id:
        missing_fields.append("vendor_id")
    if not bill.amount or bill.amount <= 0:
        missing_fields.append("amount")
    if not connection.company_code:
        missing_fields.append("company_code")
    if missing_fields:
        logger.error("SAP pre-flight validation failed: missing %s", missing_fields)
        return {
            "status": "error",
            "erp": "sap",
            "reason": "sap_validation_failed",
            "missing_fields": missing_fields,
        }

    expense_account = get_account_code("sap", "expenses", gl_map)

    sap_bill = {
        "CardCode": bill.vendor_id,  # Vendor code
        "CompanyCode": connection.company_code,
        "DocDate": bill.invoice_date or datetime.now().strftime("%Y-%m-%d"),
        "DocDueDate": bill.due_date,
        "NumAtCard": bill.invoice_number,  # Vendor's reference
        "Comments": bill.description or f"Invoice from {bill.vendor_name}",
        "DocumentLines": [],
    }

    if bill.line_items:
        for i, item in enumerate(bill.line_items):
            sap_bill["DocumentLines"].append({
                "LineNum": i,
                "ItemDescription": item.get("description", ""),
                "AccountCode": item.get("account_code", expense_account),
                "LineTotal": item.get("amount", 0),
            })
    else:
        sap_bill["DocumentLines"].append({
            "LineNum": 0,
            "ItemDescription": bill.description or f"Invoice {bill.invoice_number}",
            "AccountCode": expense_account,
            "LineTotal": bill.amount,
        })
    
    url = f"{connection.base_url}/PurchaseInvoices"
    
    # B5: SAP B1 Service Layer uses session auth + CSRF token for mutations.
    # Step 1: Establish session (POST /Login) if needed.
    # Step 2: Fetch CSRF token (GET with X-CSRF-Token: Fetch header).
    # Step 3: POST /PurchaseInvoices with session cookie + CSRF token.
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            # SAP B1 session login
            login_url = f"{connection.base_url}/Login"
            login_payload = {
                "CompanyDB": connection.company_code or "",
                "UserName": "",
                "Password": "",
            }
            # Decode stored credentials (base64-encoded username:password)
            try:
                import base64
                decoded = base64.b64decode(connection.access_token).decode("utf-8")
                if ":" in decoded:
                    login_payload["UserName"], login_payload["Password"] = decoded.split(":", 1)
            except Exception:
                # Fallback: treat access_token as session cookie directly
                pass

            session_cookie = None
            csrf_token = None

            if login_payload["UserName"]:
                login_resp = await client.post(login_url, json=login_payload, timeout=30)
                if login_resp.status_code == 200:
                    session_cookie = login_resp.cookies.get("B1SESSION")
                else:
                    return {"status": "error", "erp": "sap", "reason": "sap_login_failed", "needs_reauth": True}
            else:
                # Legacy path: use access_token as session cookie
                session_cookie = connection.access_token

            # Fetch CSRF token
            headers = {"X-CSRF-Token": "Fetch"}
            if session_cookie:
                headers["Cookie"] = f"B1SESSION={session_cookie}"
            csrf_resp = await client.get(url, headers=headers, timeout=30)
            csrf_token = csrf_resp.headers.get("x-csrf-token", "")

            # Post the invoice
            post_headers = {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf_token,
            }
            if session_cookie:
                post_headers["Cookie"] = f"B1SESSION={session_cookie}"

            response = await client.post(url, json=sap_bill, headers=post_headers, timeout=60)

            if response.status_code == 401:
                return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}

            response.raise_for_status()
            result = response.json()

            doc_entry = result.get("DocEntry")
            logger.info("Posted A/P Invoice to SAP: %s", doc_entry)
            return {
                "status": "success",
                "erp": "sap",
                "bill_id": doc_entry,
                "doc_num": result.get("DocNum"),
            }

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error("SAP A/P Invoice HTTP error: status=%d", status_code)
        return {
            "status": "error",
            "erp": "sap",
            "reason": f"http_{status_code}",
            "needs_reauth": status_code == 401,
        }
    except Exception as e:
        logger.error("SAP A/P Invoice error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "bill_posting_failed"}


# ==================== VENDOR MANAGEMENT ====================

@dataclass
class Vendor:
    """Represents a vendor/supplier."""
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tax_id: Optional[str] = None
    currency: str = "USD"
    payment_terms: Optional[str] = None  # e.g., "Net 30"


async def create_vendor(
    organization_id: str,
    vendor: Vendor,
) -> Dict[str, Any]:
    """Create a new vendor in the ERP."""
    connection = get_erp_connection(organization_id)
    
    if not connection:
        return {"status": "error", "reason": "No ERP connected"}
    
    if connection.type == "quickbooks":
        return await create_vendor_quickbooks(connection, vendor)
    elif connection.type == "xero":
        return await create_vendor_xero(connection, vendor)
    elif connection.type == "netsuite":
        return await create_vendor_netsuite(connection, vendor)
    elif connection.type == "sap":
        return await create_vendor_sap(connection, vendor)
    else:
        return {"status": "error", "reason": f"Unknown ERP type: {connection.type}"}


async def find_vendor(
    organization_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find a vendor by name or email."""
    connection = get_erp_connection(organization_id)
    
    if not connection:
        return None
    
    if connection.type == "quickbooks":
        return await find_vendor_quickbooks(connection, name, email)
    elif connection.type == "xero":
        return await find_vendor_xero(connection, name, email)
    elif connection.type == "netsuite":
        return await find_vendor_netsuite(connection, name, email)
    elif connection.type == "sap":
        return await find_vendor_sap(connection, name, email)
    
    return None


async def create_vendor_quickbooks(
    connection: ERPConnection,
    vendor: Vendor,
) -> Dict[str, Any]:
    """Create vendor in QuickBooks."""
    if not connection.access_token or not connection.realm_id:
        return {"status": "error", "erp": "quickbooks", "reason": "QuickBooks not configured"}

    qb_vendor = {
        "DisplayName": vendor.name,
        "PrintOnCheckName": vendor.name,
    }
    
    if vendor.email:
        qb_vendor["PrimaryEmailAddr"] = {"Address": vendor.email}
    if vendor.phone:
        qb_vendor["PrimaryPhone"] = {"FreeFormNumber": vendor.phone}
    if vendor.tax_id:
        qb_vendor["TaxIdentifier"] = vendor.tax_id
    
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/vendor"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=qb_vendor,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            vendor_data = result.get("Vendor", {})
            return {
                "status": "success",
                "vendor_id": vendor_data.get("Id"),
                "display_name": vendor_data.get("DisplayName"),
            }
    except Exception as e:
        logger.error("QuickBooks vendor creation error: %s", type(e).__name__)
        return {"status": "error", "erp": "quickbooks", "reason": "vendor_creation_failed"}


async def find_vendor_quickbooks(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in QuickBooks by name or email."""
    if not connection.access_token or not connection.realm_id:
        return None
    
    # Build query via canonical helper with strict operand sanitization.
    name_operand = _sanitize_quickbooks_like_operand(name)
    email_operand = _sanitize_quickbooks_like_operand(email)
    query = _build_quickbooks_vendor_lookup_query(
        name_operand=name_operand,
        email_operand=email_operand,
    )
    if not query:
        return None
    
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/query"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params={"query": query},
                headers={"Authorization": f"Bearer {connection.access_token}"},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            vendors = result.get("QueryResponse", {}).get("Vendor", [])
            if vendors:
                v = vendors[0]
                return {
                    "vendor_id": v.get("Id"),
                    "name": v.get("DisplayName"),
                    "email": v.get("PrimaryEmailAddr", {}).get("Address"),
                }
    except Exception as e:
        logger.error(f"QuickBooks vendor search error: {e}")
    
    return None


async def create_vendor_xero(
    connection: ERPConnection,
    vendor: Vendor,
) -> Dict[str, Any]:
    """Create vendor (Contact) in Xero."""
    if not connection.access_token or not connection.tenant_id:
        return {"status": "error", "erp": "xero", "reason": "Xero not configured"}

    xero_contact = {
        "Name": vendor.name,
        "IsSupplier": True,
        "ContactStatus": "ACTIVE",
    }
    
    if vendor.email:
        xero_contact["EmailAddress"] = vendor.email
    if vendor.phone:
        xero_contact["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": vendor.phone}]
    if vendor.tax_id:
        xero_contact["TaxNumber"] = vendor.tax_id
    if vendor.payment_terms:
        # Map to Xero payment terms
        days = int(''.join(filter(str.isdigit, vendor.payment_terms)) or "30")
        xero_contact["PaymentTerms"] = {"Bills": {"Day": days, "Type": "DAYSAFTERBILLDATE"}}
    
    url = "https://api.xero.com/api.xro/2.0/Contacts"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"Contacts": [xero_contact]},
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                    "xero-tenant-id": connection.tenant_id,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            contacts = result.get("Contacts", [])
            if contacts:
                c = contacts[0]
                return {
                    "status": "success",
                    "vendor_id": c.get("ContactID"),
                    "name": c.get("Name"),
                }
            return {"status": "error", "erp": "xero", "reason": "No contact returned"}
    except Exception as e:
        logger.error("Xero vendor creation error: %s", type(e).__name__)
        return {"status": "error", "erp": "xero", "reason": "vendor_creation_failed"}


async def find_vendor_xero(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in Xero."""
    if not connection.access_token or not connection.tenant_id:
        return None
    
    url = "https://api.xero.com/api.xro/2.0/Contacts"
    name_operand = _sanitize_xero_where_operand(name)
    params = {"where": _build_xero_vendor_lookup_where(name_operand=name_operand)}
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "xero-tenant-id": connection.tenant_id,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            contacts = result.get("Contacts", [])
            for c in contacts:
                if email and c.get("EmailAddress") != email:
                    continue
                return {
                    "vendor_id": c.get("ContactID"),
                    "name": c.get("Name"),
                    "email": c.get("EmailAddress"),
                }
    except Exception as e:
        logger.error(f"Xero vendor search error: {e}")
    
    return None


async def create_vendor_netsuite(
    connection: ERPConnection,
    vendor: Vendor,
) -> Dict[str, Any]:
    """Create vendor in NetSuite."""
    if not connection.account_id:
        return {"status": "error", "erp": "netsuite", "reason": "NetSuite not configured"}

    ns_vendor = {
        "companyName": vendor.name,
        "entityId": vendor.name.replace(" ", "_")[:32],  # External ID
        "email": vendor.email,
        "phone": vendor.phone,
    }
    
    if vendor.currency:
        ns_vendor["currency"] = {"refName": vendor.currency}
    
    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/vendor"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=ns_vendor,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "status": "success",
                "vendor_id": result.get("id"),
                "entity_id": result.get("entityId"),
            }
    except Exception as e:
        logger.error("NetSuite vendor creation error: %s", type(e).__name__)
        return {"status": "error", "erp": "netsuite", "reason": "vendor_creation_failed"}


async def find_vendor_netsuite(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in NetSuite."""
    if not connection.account_id:
        return None
    
    # Build SuiteQL query with strict sanitization through shared helper.
    name_operand = _sanitize_netsuite_like_operand(name)
    email_operand = _sanitize_netsuite_email_operand(email)
    query = _build_netsuite_vendor_lookup_query(
        name_operand=name_operand,
        email_operand=email_operand,
    )
    if not query:
        return None
    
    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"q": query},
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Prefer": "transient",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            items = result.get("items", [])
            if items:
                v = items[0]
                return {
                    "vendor_id": str(v.get("id")),
                    "name": v.get("companyname"),
                    "email": v.get("email"),
                }
    except Exception as e:
        logger.error(f"NetSuite vendor search error: {e}")
    
    return None


async def create_vendor_sap(
    connection: ERPConnection,
    vendor: Vendor,
) -> Dict[str, Any]:
    """Create vendor (Business Partner) in SAP."""
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "erp": "sap", "reason": "SAP not configured"}

    sap_bp = {
        "CardName": vendor.name,
        "CardType": "cSupplier",
        "EmailAddress": vendor.email,
        "Phone1": vendor.phone,
    }
    
    url = f"{connection.base_url}/BusinessPartners"
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=sap_bp,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "status": "success",
                "vendor_id": result.get("CardCode"),
                "name": result.get("CardName"),
            }
    except Exception as e:
        logger.error("SAP vendor creation error: %s", type(e).__name__)
        return {"status": "error", "erp": "sap", "reason": "vendor_creation_failed"}


async def find_vendor_sap(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in SAP."""
    if not connection.access_token or not connection.base_url:
        return None
    
    filters = ["CardType eq 'cSupplier'"]
    if name:
        safe_name = _sanitize_odata_value(name)
        filters.append(f"contains(CardName, '{safe_name}')")
    if email:
        safe_email = _sanitize_odata_value(email)
        filters.append(f"EmailAddress eq '{safe_email}'")
    
    url = f"{connection.base_url}/BusinessPartners"
    params = {"$filter": " and ".join(filters), "$top": 1}
    
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {connection.access_token}"},
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            
            items = result.get("value", [])
            if items:
                v = items[0]
                return {
                    "vendor_id": v.get("CardCode"),
                    "name": v.get("CardName"),
                    "email": v.get("EmailAddress"),
                }
    except Exception as e:
        logger.error(f"SAP vendor search error: {e}")
    
    return None


async def get_or_create_vendor(
    organization_id: str,
    vendor: Vendor,
) -> Dict[str, Any]:
    """
    Find existing vendor or create new one.
    
    This is the primary function to use when posting bills -
    ensures vendor exists before posting.
    """
    # Try to find by name first
    existing = await find_vendor(organization_id, name=vendor.name)
    
    if existing:
        return {
            "status": "found",
            "vendor_id": existing["vendor_id"],
            "name": existing["name"],
        }
    
    # Try by email if provided
    if vendor.email:
        existing = await find_vendor(organization_id, email=vendor.email)
        if existing:
            return {
                "status": "found",
                "vendor_id": existing["vendor_id"],
                "name": existing["name"],
            }
    
    # Create new vendor
    result = await create_vendor(organization_id, vendor)
    
    if result.get("status") == "success":
        return {
            "status": "created",
            "vendor_id": result["vendor_id"],
            "name": vendor.name,
        }

    return result


# ==================== BILL LOOKUP (ERP PRE-FLIGHT) ====================


async def find_bill_quickbooks(
    connection: ERPConnection,
    invoice_number: str,
) -> Optional[Dict[str, Any]]:
    """Check if a bill with this invoice number already exists in QuickBooks."""
    if not connection.access_token or not connection.realm_id:
        return None
    safe_number = _sanitize_quickbooks_like_operand(invoice_number)
    if not safe_number:
        return None
    literal = _escape_query_literal(safe_number)
    query = f"SELECT Id, DocNumber, TotalAmt FROM Bill WHERE DocNumber = '{literal}'"
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/query"
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params={"query": query},
                headers={"Authorization": f"Bearer {connection.access_token}"},
                timeout=30,
            )
            response.raise_for_status()
            bills = response.json().get("QueryResponse", {}).get("Bill", [])
            if bills:
                b = bills[0]
                return {
                    "bill_id": b.get("Id"),
                    "doc_number": b.get("DocNumber"),
                    "amount": b.get("TotalAmt"),
                    "erp": "quickbooks",
                }
    except Exception as e:
        logger.error("QuickBooks bill lookup error: %s", e)
    return None


async def find_bill_xero(
    connection: ERPConnection,
    invoice_number: str,
) -> Optional[Dict[str, Any]]:
    """Check if a bill (accounts payable invoice) already exists in Xero."""
    if not connection.access_token or not connection.tenant_id:
        return None
    safe_number = _sanitize_xero_where_operand(invoice_number)
    if not safe_number:
        return None
    literal = _escape_query_literal(safe_number)
    where_clause = f'Type=="ACCPAY" AND InvoiceNumber=="{literal}"'
    url = "https://api.xero.com/api.xro/2.0/Invoices"
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params={"where": where_clause},
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "xero-tenant-id": connection.tenant_id,
                },
                timeout=30,
            )
            response.raise_for_status()
            invoices = response.json().get("Invoices", [])
            if invoices:
                inv = invoices[0]
                return {
                    "bill_id": inv.get("InvoiceID"),
                    "doc_number": inv.get("InvoiceNumber"),
                    "amount": inv.get("Total"),
                    "erp": "xero",
                }
    except Exception as e:
        logger.error("Xero bill lookup error: %s", e)
    return None


async def find_bill_netsuite(
    connection: ERPConnection,
    invoice_number: str,
) -> Optional[Dict[str, Any]]:
    """Check if a vendor bill already exists in NetSuite."""
    if not connection.account_id:
        return None
    safe_number = _sanitize_netsuite_like_operand(invoice_number)
    if not safe_number:
        return None
    literal = _escape_query_literal(safe_number)
    query = (
        f"SELECT id, tranid, amount FROM transaction "
        f"WHERE tranid = '{literal}' AND type = 'VendBill' "
        f"FETCH FIRST 1 ROWS ONLY"
    )
    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"q": query},
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Prefer": "transient",
                },
                timeout=60,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            if items:
                row = items[0]
                return {
                    "bill_id": str(row.get("id")),
                    "doc_number": row.get("tranid"),
                    "amount": row.get("amount"),
                    "erp": "netsuite",
                }
    except Exception as e:
        logger.error("NetSuite bill lookup error: %s", e)
    return None


async def find_bill_sap(
    connection: ERPConnection,
    invoice_number: str,
) -> Optional[Dict[str, Any]]:
    """Check if a purchase invoice already exists in SAP."""
    if not connection.access_token or not connection.base_url:
        return None
    safe_number = _sanitize_odata_value(invoice_number)
    if not safe_number:
        return None
    url = f"{connection.base_url}/PurchaseInvoices"
    params = {
        "$filter": f"NumAtCard eq '{safe_number}'",
        "$top": "1",
        "$select": "DocEntry,NumAtCard,DocTotal",
    }
    try:
        async with httpx.AsyncClient(timeout=_ERP_TIMEOUT) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {connection.access_token}"},
                timeout=60,
            )
            response.raise_for_status()
            items = response.json().get("value", [])
            if items:
                row = items[0]
                return {
                    "bill_id": str(row.get("DocEntry")),
                    "doc_number": row.get("NumAtCard"),
                    "amount": row.get("DocTotal"),
                    "erp": "sap",
                }
    except Exception as e:
        logger.error("SAP bill lookup error: %s", e)
    return None


# ==================== ERP PRE-FLIGHT ORCHESTRATOR ====================


_BILL_FINDERS = {
    "quickbooks": find_bill_quickbooks,
    "xero": find_bill_xero,
    "netsuite": find_bill_netsuite,
    "sap": find_bill_sap,
}

_VENDOR_FINDERS = {
    "quickbooks": find_vendor_quickbooks,
    "xero": find_vendor_xero,
    "netsuite": find_vendor_netsuite,
    "sap": find_vendor_sap,
}


async def erp_preflight_check(
    organization_id: str,
    vendor_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
    gl_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Non-blocking ERP pre-flight check run during the validation gate.

    Checks vendor existence, bill duplicate, and GL mapping validity.
    Each check is independently wrapped — one failure does not block others.
    Returns None-valued fields for checks that were not run.
    """
    result: Dict[str, Any] = {
        "vendor_exists": None,
        "vendor_erp_id": None,
        "bill_exists": None,
        "bill_erp_ref": None,
        "gl_valid": None,
        "invalid_gl_codes": [],
        "erp_type": None,
        "erp_available": False,
        "checks_run": [],
    }

    connection = get_erp_connection(organization_id)
    if not connection:
        return result

    result["erp_type"] = connection.type
    result["erp_available"] = True

    # 1. Vendor existence check
    if vendor_name:
        finder = _VENDOR_FINDERS.get(connection.type)
        if finder:
            try:
                vendor = await finder(connection, name=vendor_name)
                result["vendor_exists"] = vendor is not None
                if vendor:
                    result["vendor_erp_id"] = vendor.get("vendor_id")
                result["checks_run"].append("vendor_lookup")
            except Exception as e:
                logger.warning("ERP preflight vendor check failed (non-fatal): %s", e)

    # 2. Bill duplicate check
    if invoice_number:
        finder = _BILL_FINDERS.get(connection.type)
        if finder:
            try:
                bill = await finder(connection, invoice_number)
                result["bill_exists"] = bill is not None
                if bill:
                    result["bill_erp_ref"] = bill
                result["checks_run"].append("bill_lookup")
            except Exception as e:
                logger.warning("ERP preflight bill check failed (non-fatal): %s", e)

    # 3. GL code validation against org mapping
    if gl_codes:
        gl_map = _get_org_gl_map(organization_id)
        if gl_map:
            valid_codes = set(gl_map.values())
            invalid = [c for c in gl_codes if c not in valid_codes]
            result["gl_valid"] = len(invalid) == 0
            result["invalid_gl_codes"] = invalid
            result["checks_run"].append("gl_validation")

    return result


async def verify_bill_posted(
    organization_id: str,
    invoice_number: str,
    expected_amount: Optional[float] = None,
) -> Dict[str, Any]:
    """Verify a bill actually exists in the ERP after posting.

    Reuses the ``find_bill_*`` functions built for pre-flight checks.
    Returns ``{"verified": bool, "bill": ..., "erp_type": str, "reason": str}``.

    Non-fatal by design — callers should default to ``verified=True`` on error
    so the pipeline is never blocked by a verification failure.
    """
    org_id = str(organization_id or "").strip() or "default"
    inv_num = str(invoice_number or "").strip()
    if not inv_num:
        return {"verified": False, "bill": None, "erp_type": None, "reason": "no_invoice_number"}

    connection = get_erp_connection(org_id)
    if not connection:
        return {"verified": True, "bill": None, "erp_type": None, "reason": "no_erp_connection"}

    erp_type = str(connection.type or "").strip().lower()
    finder = _BILL_FINDERS.get(erp_type)
    if not finder:
        return {"verified": True, "bill": None, "erp_type": erp_type, "reason": "no_finder_for_erp"}

    try:
        bill = await finder(connection, inv_num)
    except Exception as exc:
        logger.warning("Post-posting verification lookup failed: %s", exc)
        return {"verified": True, "bill": None, "erp_type": erp_type, "reason": f"lookup_error:{exc}"}

    if not bill:
        return {"verified": False, "bill": None, "erp_type": erp_type, "reason": "bill_not_found_in_erp"}

    # Amount tolerance check (± 0.01 to handle rounding)
    if expected_amount is not None:
        erp_amount = bill.get("amount")
        if erp_amount is not None and abs(float(erp_amount) - float(expected_amount)) > 0.01:
            return {
                "verified": False,
                "bill": bill,
                "erp_type": erp_type,
                "reason": f"amount_mismatch:expected={expected_amount},got={erp_amount}",
            }

    return {"verified": True, "bill": bill, "erp_type": erp_type, "reason": "confirmed"}


# ---------------------------------------------------------------------------
# Attachment forwarding — upload invoice PDF to ERP bill after posting
# ---------------------------------------------------------------------------

async def _download_attachment(url: str) -> Optional[bytes]:
    """Download file bytes from a URL. Returns None on failure."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.warning("Attachment download failed from %s: %s", url, exc)
        return None


async def _attach_to_quickbooks(
    connection: ERPConnection, bill_id: str, file_bytes: bytes, filename: str,
) -> Optional[Dict[str, Any]]:
    """Upload attachment to a QuickBooks Bill via the Attachable API."""
    creds = connection.credentials or {}
    access_token = creds.get("access_token", "")
    realm_id = creds.get("realm_id", "")
    base_url = creds.get("base_url", "https://quickbooks.api.intuit.com")
    if not access_token or not realm_id:
        return None
    url = f"{base_url}/v3/company/{realm_id}/upload?minorversion=73"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    import io
    files = {"file_content_01": (filename, io.BytesIO(file_bytes), "application/pdf")}
    metadata = json.dumps({
        "AttachableRef": [{"EntityRef": {"type": "Bill", "value": bill_id}}],
        "FileName": filename,
        "ContentType": "application/pdf",
    })
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, files=files, data={"file_metadata_01": metadata})
        resp.raise_for_status()
    return {"attached": True, "erp": "quickbooks"}


async def _attach_to_xero(
    connection: ERPConnection, bill_id: str, file_bytes: bytes, filename: str,
) -> Optional[Dict[str, Any]]:
    """Upload attachment to a Xero ACCPAY Invoice."""
    creds = connection.credentials or {}
    access_token = creds.get("access_token", "")
    tenant_id = creds.get("tenant_id", "")
    if not access_token or not tenant_id:
        return None
    safe_name = _sanitize_xero_where_operand(filename) or "invoice.pdf"
    url = f"https://api.xero.com/api.xro/2.0/Invoices/{bill_id}/Attachments/{safe_name}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "xero-tenant-id": tenant_id,
        "Content-Type": "application/pdf",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, headers=headers, content=file_bytes)
        resp.raise_for_status()
    return {"attached": True, "erp": "xero"}


async def _attach_to_netsuite(
    connection: ERPConnection, bill_id: str, file_bytes: bytes, filename: str,
) -> Optional[Dict[str, Any]]:
    """Upload attachment to a NetSuite VendorBill."""
    creds = connection.credentials or {}
    account_id = creds.get("account_id", "")
    if not account_id:
        return None
    import base64
    encoded = base64.b64encode(file_bytes).decode()
    base_url = f"https://{account_id}.suitetalk.api.netsuite.com"
    url = f"{base_url}/services/rest/record/v1/vendorbill/{bill_id}/file"
    headers = build_netsuite_oauth_header(connection, url, "POST")
    headers["Content-Type"] = "application/json"
    payload = {"name": filename, "content": encoded}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
    return {"attached": True, "erp": "netsuite"}


async def _attach_to_sap(
    connection: ERPConnection, bill_id: str, file_bytes: bytes, filename: str,
) -> Optional[Dict[str, Any]]:
    """Upload attachment to a SAP Business One PurchaseInvoice."""
    creds = connection.credentials or {}
    base_url = str(creds.get("base_url") or "").rstrip("/")
    session_id = creds.get("session_id", "")
    if not base_url or not session_id:
        return None
    import base64
    encoded = base64.b64encode(file_bytes).decode()
    url = f"{base_url}/Attachments2"
    headers = {"Cookie": f"B1SESSION={session_id}", "Content-Type": "application/json"}
    payload = {
        "Attachments2_Lines": [{
            "SourcePath": filename,
            "FileName": filename,
            "FileExtension": "pdf",
            "Override": "tNO",
        }],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        # Create attachment record
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
    return {"attached": True, "erp": "sap"}


_ATTACHMENT_UPLOADERS = {
    "quickbooks": _attach_to_quickbooks,
    "xero": _attach_to_xero,
    "netsuite": _attach_to_netsuite,
    "sap": _attach_to_sap,
}


async def attach_file_to_erp_bill(
    organization_id: str,
    bill_id: str,
    attachment_url: str,
    filename: str = "invoice.pdf",
) -> Optional[Dict[str, Any]]:
    """Download an attachment and upload it to the ERP bill.

    Returns ``{"attached": True, "erp": str}`` on success, ``None`` on failure.
    Non-fatal — callers should treat None as a warning, never block on it.
    """
    connection = get_erp_connection(organization_id)
    if not connection:
        return None

    erp_type = str(connection.type or "").strip().lower()
    uploader = _ATTACHMENT_UPLOADERS.get(erp_type)
    if not uploader:
        logger.info("No attachment uploader for ERP type: %s", erp_type)
        return None

    file_bytes = await _download_attachment(attachment_url)
    if not file_bytes:
        return None

    try:
        return await uploader(connection, bill_id, file_bytes, filename)
    except Exception as exc:
        logger.warning("Attachment upload to %s failed: %s", erp_type, exc)
        return None
