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
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


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
    
    return ERPConnection(
        type=conn['erp_type'],
        access_token=conn.get('access_token'),
        refresh_token=conn.get('refresh_token'),
        realm_id=conn.get('realm_id'),
        tenant_id=conn.get('tenant_id'),
        base_url=conn.get('base_url'),
        client_id=creds.get('client_id'),
        client_secret=creds.get('client_secret'),
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
        return {"status": "error", "reason": "QuickBooks not properly configured"}
    
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
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Token expired", "needs_reauth": True}
            
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
        logger.error(f"QuickBooks API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"QuickBooks error: {e}")
        return {"status": "error", "reason": str(e)}


async def refresh_quickbooks_token(connection: ERPConnection) -> Optional[str]:
    """Refresh QuickBooks OAuth token."""
    if not connection.refresh_token or not connection.client_id or not connection.client_secret:
        return None
    
    try:
        async with httpx.AsyncClient() as client:
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
        logger.error(f"Failed to refresh QuickBooks token: {e}")
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
        return {"status": "error", "reason": "Xero not properly configured"}
    
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
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Token expired", "needs_reauth": True}
            
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
            
            return {"status": "error", "reason": "No journal returned"}
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Xero API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"Xero error: {e}")
        return {"status": "error", "reason": str(e)}


async def refresh_xero_token(connection: ERPConnection) -> Optional[str]:
    """Refresh Xero OAuth token."""
    if not connection.refresh_token or not connection.client_id or not connection.client_secret:
        return None
    
    try:
        async with httpx.AsyncClient() as client:
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
        logger.error(f"Failed to refresh Xero token: {e}")
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
        return {"status": "error", "reason": "NetSuite account ID not configured"}
    
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
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Authentication failed", "needs_reauth": True}
            
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
        logger.error(f"NetSuite API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"NetSuite error: {e}")
        return {"status": "error", "reason": str(e)}


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
        async with httpx.AsyncClient() as client:
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
        return {"status": "error", "reason": "SAP not properly configured"}
    
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
        async with httpx.AsyncClient() as client:
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
        logger.error(f"SAP OData error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"SAP error: {e}")
        return {"status": "error", "reason": str(e)}


# ==================== ACCOUNT MAPPING ====================

# Default GL account mappings - can be customized per organization
DEFAULT_ACCOUNT_MAP = {
    "quickbooks": {
        "cash": "1",  # Default checking account
        "accounts_receivable": "4",
        "payment_fees": "74",  # Bank Service Charges
        "revenue": "1",
    },
    "xero": {
        "cash": "090",  # Business Bank Account
        "accounts_receivable": "610",  # Accounts Receivable
        "payment_fees": "404",  # Bank Fees
        "revenue": "200",  # Sales
    },
    "netsuite": {
        "cash": "1000",  # Cash and Cash Equivalents
        "accounts_receivable": "1200",  # Accounts Receivable
        "payment_fees": "6800",  # Bank Service Charges
        "revenue": "4000",  # Sales Revenue
    },
    "sap": {
        "cash": "1000",  # Cash
        "accounts_receivable": "1100",  # AR
        "payment_fees": "6200",  # Bank Charges
        "revenue": "4000",  # Revenue
    },
}


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
) -> Dict[str, Any]:
    """
    Post a vendor bill to the organization's ERP.
    
    This is the primary function for invoice processing - posts as AP Bill, not journal entry.
    """
    connection = get_erp_connection(organization_id)
    
    if not connection:
        logger.warning(f"No ERP connected for {organization_id}")
        return {"status": "skipped", "reason": "No ERP connected"}
    
    if connection.type == "quickbooks":
        return await post_bill_to_quickbooks(connection, bill)
    elif connection.type == "xero":
        return await post_bill_to_xero(connection, bill)
    elif connection.type == "netsuite":
        return await post_bill_to_netsuite(connection, bill)
    elif connection.type == "sap":
        return await post_bill_to_sap(connection, bill)
    else:
        return {"status": "error", "reason": f"Unknown ERP type: {connection.type}"}


async def post_bill_to_quickbooks(
    connection: ERPConnection,
    bill: Bill,
) -> Dict[str, Any]:
    """
    Post vendor bill to QuickBooks Online.
    
    API: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/bill
    """
    if not connection.access_token or not connection.realm_id:
        return {"status": "error", "reason": "QuickBooks not properly configured"}
    
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
                    "AccountRef": {"value": item.get("account_id", "7")},  # Default: Expenses
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
                "AccountRef": {"value": "7"},  # Expenses
            }
        })
    
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/bill"
    
    try:
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Token expired", "needs_reauth": True}
            
            response.raise_for_status()
            result = response.json()
            
            bill_data = result.get("Bill", {})
            logger.info(f"Posted Bill to QuickBooks: {bill_data.get('Id')}")
            return {
                "status": "success",
                "erp": "quickbooks",
                "bill_id": bill_data.get("Id"),
                "doc_number": bill_data.get("DocNumber"),
                "sync_token": bill_data.get("SyncToken"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error(f"QuickBooks Bill API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"QuickBooks Bill error: {e}")
        return {"status": "error", "reason": str(e)}


async def post_bill_to_xero(
    connection: ERPConnection,
    bill: Bill,
) -> Dict[str, Any]:
    """
    Post vendor bill to Xero.
    
    API: https://developer.xero.com/documentation/api/accounting/invoices
    Type: ACCPAY (Accounts Payable / Bill)
    """
    if not connection.access_token or not connection.tenant_id:
        return {"status": "error", "reason": "Xero not properly configured"}
    
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
                "AccountCode": item.get("account_code", "400"),  # Default: Advertising
                "TaxType": item.get("tax_type", "NONE"),
            })
    else:
        xero_bill["LineItems"].append({
            "Description": bill.description or f"Invoice {bill.invoice_number}",
            "Quantity": 1,
            "UnitAmount": bill.amount,
            "AccountCode": "400",
            "TaxType": "NONE",
        })
    
    url = "https://api.xero.com/api.xro/2.0/Invoices"
    
    try:
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Token expired", "needs_reauth": True}
            
            response.raise_for_status()
            result = response.json()
            
            invoices = result.get("Invoices", [])
            if invoices:
                inv = invoices[0]
                logger.info(f"Posted Bill to Xero: {inv.get('InvoiceID')}")
                return {
                    "status": "success",
                    "erp": "xero",
                    "bill_id": inv.get("InvoiceID"),
                    "invoice_number": inv.get("InvoiceNumber"),
                }
            
            return {"status": "error", "reason": "No invoice returned"}
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Xero Bill API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"Xero Bill error: {e}")
        return {"status": "error", "reason": str(e)}


async def post_bill_to_netsuite(
    connection: ERPConnection,
    bill: Bill,
) -> Dict[str, Any]:
    """
    Post vendor bill to NetSuite.
    
    API: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/record_vendorbill.html
    """
    if not connection.account_id:
        return {"status": "error", "reason": "NetSuite account ID not configured"}
    
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
                "account": {"id": item.get("account_id", "67")},  # Default expense account
                "amount": item.get("amount", 0),
                "memo": item.get("description", ""),
            })
    else:
        ns_bill["expense"]["items"].append({
            "line": 1,
            "account": {"id": "67"},  # Expenses
            "amount": bill.amount,
            "memo": bill.description or f"Invoice {bill.invoice_number}",
        })
    
    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/vendorBill"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)
    
    try:
        async with httpx.AsyncClient() as client:
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
                return {"status": "error", "reason": "Authentication failed", "needs_reauth": True}
            
            response.raise_for_status()
            result = response.json()
            
            bill_id = result.get("id") or result.get("internalId")
            logger.info(f"Posted Vendor Bill to NetSuite: {bill_id}")
            return {
                "status": "success",
                "erp": "netsuite",
                "bill_id": bill_id,
                "tran_id": result.get("tranId"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error(f"NetSuite Vendor Bill API error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"NetSuite Vendor Bill error: {e}")
        return {"status": "error", "reason": str(e)}


async def post_bill_to_sap(
    connection: ERPConnection,
    bill: Bill,
) -> Dict[str, Any]:
    """
    Post vendor bill to SAP (A/P Invoice).
    
    SAP B1: https://help.sap.com/docs/SAP_BUSINESS_ONE
    """
    if not connection.access_token or not connection.base_url:
        return {"status": "error", "reason": "SAP not properly configured"}
    
    sap_bill = {
        "CardCode": bill.vendor_id,  # Vendor code
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
                "AccountCode": item.get("account_code", ""),
                "LineTotal": item.get("amount", 0),
            })
    else:
        sap_bill["DocumentLines"].append({
            "LineNum": 0,
            "ItemDescription": bill.description or f"Invoice {bill.invoice_number}",
            "LineTotal": bill.amount,
        })
    
    url = f"{connection.base_url}/PurchaseInvoices"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=sap_bill,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            
            response.raise_for_status()
            result = response.json()
            
            doc_entry = result.get("DocEntry")
            logger.info(f"Posted A/P Invoice to SAP: {doc_entry}")
            return {
                "status": "success",
                "erp": "sap",
                "bill_id": doc_entry,
                "doc_num": result.get("DocNum"),
            }
            
    except httpx.HTTPStatusError as e:
        logger.error(f"SAP A/P Invoice error: {e.response.text}")
        return {"status": "error", "reason": str(e), "details": e.response.text}
    except Exception as e:
        logger.error(f"SAP A/P Invoice error: {e}")
        return {"status": "error", "reason": str(e)}


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
        return {"status": "error", "reason": "QuickBooks not configured"}
    
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
        async with httpx.AsyncClient() as client:
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
        logger.error(f"QuickBooks vendor creation error: {e}")
        return {"status": "error", "reason": str(e)}


async def find_vendor_quickbooks(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in QuickBooks by name or email."""
    if not connection.access_token or not connection.realm_id:
        return None
    
    # Build query
    if name:
        query = f"SELECT * FROM Vendor WHERE DisplayName LIKE '%{name}%'"
    elif email:
        query = f"SELECT * FROM Vendor WHERE PrimaryEmailAddr LIKE '%{email}%'"
    else:
        return None
    
    url = f"https://quickbooks.api.intuit.com/v3/company/{connection.realm_id}/query"
    
    try:
        async with httpx.AsyncClient() as client:
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
        return {"status": "error", "reason": "Xero not configured"}
    
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
        async with httpx.AsyncClient() as client:
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
            return {"status": "error", "reason": "No contact returned"}
    except Exception as e:
        logger.error(f"Xero vendor creation error: {e}")
        return {"status": "error", "reason": str(e)}


async def find_vendor_xero(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in Xero."""
    if not connection.access_token or not connection.tenant_id:
        return None
    
    url = "https://api.xero.com/api.xro/2.0/Contacts"
    params = {"where": f'IsSupplier==true'}
    
    if name:
        params["where"] += f' AND Name.Contains("{name}")'
    
    try:
        async with httpx.AsyncClient() as client:
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
        return {"status": "error", "reason": "NetSuite not configured"}
    
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
        async with httpx.AsyncClient() as client:
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
        logger.error(f"NetSuite vendor creation error: {e}")
        return {"status": "error", "reason": str(e)}


async def find_vendor_netsuite(
    connection: ERPConnection,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find vendor in NetSuite."""
    if not connection.account_id:
        return None
    
    # Build SuiteQL query
    conditions = []
    if name:
        conditions.append(f"companyName LIKE '%{name}%'")
    if email:
        conditions.append(f"email = '{email}'")
    
    if not conditions:
        return None
    
    query = f"SELECT id, companyName, email FROM vendor WHERE {' OR '.join(conditions)} FETCH FIRST 1 ROWS ONLY"
    
    url = f"https://{connection.account_id}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
    auth_header = build_netsuite_oauth_header(connection, "POST", url)
    
    try:
        async with httpx.AsyncClient() as client:
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
        return {"status": "error", "reason": "SAP not configured"}
    
    sap_bp = {
        "CardName": vendor.name,
        "CardType": "cSupplier",
        "EmailAddress": vendor.email,
        "Phone1": vendor.phone,
    }
    
    url = f"{connection.base_url}/BusinessPartners"
    
    try:
        async with httpx.AsyncClient() as client:
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
        logger.error(f"SAP vendor creation error: {e}")
        return {"status": "error", "reason": str(e)}


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
        filters.append(f"contains(CardName, '{name}')")
    if email:
        filters.append(f"EmailAddress eq '{email}'")
    
    url = f"{connection.base_url}/BusinessPartners"
    params = {"$filter": " and ".join(filters), "$top": 1}
    
    try:
        async with httpx.AsyncClient() as client:
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
