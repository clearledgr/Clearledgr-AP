"""
ERP Integration Router

Routes journal entries to the appropriate ERP system:
- QuickBooks Online (for small/medium businesses)
- Xero (popular in Europe/Africa/Australia)
- NetSuite (mid-market to enterprise, very popular in Africa)
- SAP (enterprise)

This is REAL integration, not mocked.

This module is the dispatch layer. ERP-specific implementations live in:
- erp_quickbooks.py
- erp_xero.py
- erp_netsuite.py
- erp_sap.py
- erp_sanitization.py (shared helpers)

All public names are re-exported here so existing callers do not break.
"""

import logging
import httpx
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime

from clearledgr.core.database import get_db as _canonical_get_db

logger = logging.getLogger(__name__)

_ERP_TIMEOUT = 30  # seconds — applied to all outbound ERP HTTP calls

# ---------------------------------------------------------------------------
# Re-export sanitization helpers (used directly by some callers/tests)
# ---------------------------------------------------------------------------
from clearledgr.integrations.erp_sanitization import (  # noqa: F401, E402
    _QB_QUERY_VALUE_ALLOWED_CHARS,
    _NS_LIKE_VALUE_ALLOWED_CHARS,
    _NS_EMAIL_VALUE_ALLOWED_CHARS,
    _XERO_WHERE_VALUE_ALLOWED_CHARS,
    _SAP_ODATA_VALUE_ALLOWED_CHARS,
    _sanitize_quickbooks_like_operand,
    _sanitize_netsuite_like_operand,
    _sanitize_netsuite_email_operand,
    _sanitize_xero_where_operand,
    _sanitize_odata_value,
    _escape_query_literal,
    _build_quickbooks_vendor_lookup_query,
    _build_quickbooks_vendor_credit_lookup_query,
    _build_netsuite_vendor_lookup_query,
    _build_xero_vendor_lookup_where,
)

# ---------------------------------------------------------------------------
# Re-export QuickBooks functions
# ---------------------------------------------------------------------------
from clearledgr.integrations.erp_quickbooks import (  # noqa: F401, E402
    _quickbooks_headers,
    _extract_quickbooks_fault_message,
    post_to_quickbooks,
    refresh_quickbooks_token,
    post_bill_to_quickbooks,
    get_bill_quickbooks,
    find_vendor_credit_quickbooks,
    apply_credit_note_to_quickbooks,
    apply_settlement_to_quickbooks,
    create_vendor_quickbooks,
    find_vendor_quickbooks,
    find_bill_quickbooks,
    _attach_to_quickbooks,
    get_payment_status_quickbooks,
)

# ---------------------------------------------------------------------------
# Re-export Xero functions
# ---------------------------------------------------------------------------
from clearledgr.integrations.erp_xero import (  # noqa: F401, E402
    _xero_headers,
    _extract_xero_validation_message,
    post_to_xero,
    refresh_xero_token,
    post_bill_to_xero,
    find_credit_note_xero,
    apply_credit_note_to_xero,
    apply_settlement_to_xero,
    create_vendor_xero,
    find_vendor_xero,
    find_bill_xero,
    _attach_to_xero,
    get_payment_status_xero,
)

# ---------------------------------------------------------------------------
# Re-export NetSuite functions
# ---------------------------------------------------------------------------
from clearledgr.integrations.erp_netsuite import (  # noqa: F401, E402
    _extract_netsuite_validation_message,
    build_netsuite_oauth_header,
    post_to_netsuite,
    get_netsuite_accounts,
    _poll_netsuite_async_result,
    post_bill_to_netsuite,
    get_vendor_bill_netsuite,
    find_credit_note_netsuite,
    apply_credit_note_to_netsuite,
    apply_settlement_to_netsuite,
    create_vendor_netsuite,
    find_vendor_netsuite,
    find_bill_netsuite,
    _attach_to_netsuite,
    get_payment_status_netsuite,
)

# ---------------------------------------------------------------------------
# Re-export SAP functions
# ---------------------------------------------------------------------------
from clearledgr.integrations.erp_sap import (  # noqa: F401, E402
    _extract_sap_validation_message,
    _decode_sap_login_credentials,
    _normalize_sap_doc_entry,
    _sap_session_headers,
    _open_sap_service_layer_session,
    post_to_sap,
    post_bill_to_sap,
    get_purchase_invoice_sap,
    find_credit_note_sap,
    _build_sap_credit_note_lines,
    apply_credit_note_to_sap,
    apply_settlement_to_sap,
    create_vendor_sap,
    find_vendor_sap,
    find_bill_sap,
    _attach_to_sap,
    get_payment_status_sap,
)


# ==================== Shared Dataclasses ====================

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
    """Get database instance via canonical get_db()."""
    return _canonical_get_db()


def _erp_connection_from_row(conn: Dict[str, Any]) -> ERPConnection:
    """Convert a raw DB row into an ERPConnection dataclass."""
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


def get_erp_connection(
    organization_id: str,
    entity_id: Optional[str] = None,
) -> Optional[ERPConnection]:
    """Get ERP connection for an organization from database.

    When *entity_id* is provided, the function first tries to resolve an
    entity-specific ERP connection (via the entity's ``erp_connection_id``).
    If the entity has no dedicated connection, or if no entity_id is
    provided, the org-level default connection is returned.

    This keeps everything backward-compatible: orgs without entities
    continue to work exactly as before.
    """
    db = _get_db()

    # Try entity-specific connection first
    if entity_id:
        try:
            entity = db.get_entity(entity_id)
            if entity and entity.get("erp_connection_id"):
                entity_conn = db.get_erp_connection_by_id(entity["erp_connection_id"])
                if entity_conn:
                    return _erp_connection_from_row(entity_conn)
        except Exception:
            logger.debug("Entity ERP lookup failed for %s, falling back to org default", entity_id)

    # Fall back to org-level default
    connections = db.get_erp_connections(organization_id)
    if not connections:
        return None

    # Return the first active connection
    return _erp_connection_from_row(connections[0])


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


# ==================== Journal Entry Dispatcher ====================

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


def _get_entity_gl_map(organization_id: str, entity_id: Optional[str]) -> Dict[str, str]:
    """Load entity-specific GL account mapping from the entity's gl_mapping_json."""
    if not entity_id:
        return {}
    try:
        db = _get_db()
        entity = db.get_entity(entity_id)
        if not entity:
            return {}
        gl_mapping = entity.get("gl_mapping") or {}
        if isinstance(gl_mapping, str):
            import json as _json
            try:
                gl_mapping = _json.loads(gl_mapping)
            except Exception:
                return {}
        return dict(gl_mapping) if isinstance(gl_mapping, dict) else {}
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


@dataclass
class CreditApplication:
    """Represents a vendor credit application against an ERP payable."""

    target_erp_reference: str
    amount: float
    currency: str = "USD"
    credit_note_number: Optional[str] = None
    target_invoice_number: Optional[str] = None
    note: Optional[str] = None
    source_ap_item_id: Optional[str] = None
    related_ap_item_id: Optional[str] = None


@dataclass
class SettlementApplication:
    """Represents a cash settlement application against an ERP payable."""

    target_erp_reference: str
    amount: float
    currency: str = "USD"
    source_reference: Optional[str] = None
    source_document_type: Optional[str] = None
    target_invoice_number: Optional[str] = None
    note: Optional[str] = None
    source_ap_item_id: Optional[str] = None
    related_ap_item_id: Optional[str] = None


# ==================== Bill Dispatch ====================

async def post_bill(
    organization_id: str,
    bill: Bill,
    ap_item_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Post a vendor bill to the organization's ERP.

    This is the primary function for invoice processing — posts as AP Bill.

    When *entity_id* is provided, the function looks up the entity's
    dedicated ERP connection and GL mapping.  If the entity has no
    dedicated connection, the org-level default is used.

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

    connection = get_erp_connection(organization_id, entity_id=entity_id)

    if not connection:
        logger.warning("No ERP connected for %s", organization_id)
        return {"status": "skipped", "reason": "No ERP Connected", "idempotency_key": idempotency_key}

    gl_map = _get_entity_gl_map(organization_id, entity_id) or _get_org_gl_map(organization_id)

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


# ==================== Credit Note Dispatch ====================

async def apply_credit_note(
    organization_id: str,
    application: CreditApplication,
    *,
    ap_item_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a credit note to an existing ERP payable.

    Current GA connectors still use browser fallback for this path. The API
    seam exists so connector-specific credit application can ship incrementally
    without changing AP-item workflow code again.
    """
    connection = get_erp_connection(organization_id)
    if not connection:
        return {
            "status": "skipped",
            "reason": "No ERP Connected",
            "idempotency_key": idempotency_key,
            "erp_reference": application.target_erp_reference,
            "ap_item_id": ap_item_id,
        }

    if connection.type == "xero":
        result = await apply_credit_note_to_xero(
            connection,
            application,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_xero_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await apply_credit_note_to_xero(
                    connection,
                    application,
                    idempotency_key=idempotency_key,
                )
    elif connection.type == "quickbooks":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_credit_note_to_quickbooks(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_quickbooks_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await apply_credit_note_to_quickbooks(
                    connection,
                    application,
                    gl_map=gl_map,
                    idempotency_key=idempotency_key,
                )
    elif connection.type == "netsuite":
        result = await apply_credit_note_to_netsuite(
            connection,
            application,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            logger.warning("NetSuite 401 during credit application for org %s; retrying once", organization_id)
            result = await apply_credit_note_to_netsuite(
                connection,
                application,
                idempotency_key=idempotency_key,
            )
    elif connection.type == "sap":
        result = await apply_credit_note_to_sap(
            connection,
            application,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            logger.warning("SAP 401 during credit application for org %s; retrying with fresh session", organization_id)
            result = await apply_credit_note_to_sap(
                connection,
                application,
                idempotency_key=idempotency_key,
            )
    else:
        result = {
            "status": "error",
            "erp": connection.type,
            "reason": "credit_application_api_not_available_for_connector",
        }

    if isinstance(result, dict) and idempotency_key and not result.get("idempotency_key"):
        result = {**result, "idempotency_key": idempotency_key}
    if isinstance(result, dict) and ap_item_id and not result.get("ap_item_id"):
        result = {**result, "ap_item_id": ap_item_id}
    return result


# ==================== Settlement Dispatch ====================

async def apply_settlement(
    organization_id: str,
    application: SettlementApplication,
    *,
    ap_item_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a payment, receipt, or refund settlement to an ERP payable."""
    connection = get_erp_connection(organization_id)
    if not connection:
        return {
            "status": "skipped",
            "reason": "No ERP Connected",
            "idempotency_key": idempotency_key,
            "erp_reference": application.target_erp_reference,
            "ap_item_id": ap_item_id,
        }

    if connection.type == "quickbooks":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_settlement_to_quickbooks(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_quickbooks_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await apply_settlement_to_quickbooks(
                    connection,
                    application,
                    gl_map=gl_map,
                    idempotency_key=idempotency_key,
                )
    elif connection.type == "xero":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_settlement_to_xero(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_xero_token(connection)
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await apply_settlement_to_xero(
                    connection,
                    application,
                    gl_map=gl_map,
                    idempotency_key=idempotency_key,
                )
    elif connection.type == "netsuite":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_settlement_to_netsuite(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            logger.warning("NetSuite 401 during settlement application for org %s; retrying once", organization_id)
            result = await apply_settlement_to_netsuite(
                connection,
                application,
                gl_map=gl_map,
                idempotency_key=idempotency_key,
            )
    elif connection.type == "sap":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_settlement_to_sap(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            logger.warning("SAP 401 during settlement application for org %s; retrying with fresh session", organization_id)
            result = await apply_settlement_to_sap(
                connection,
                application,
                gl_map=gl_map,
                idempotency_key=idempotency_key,
            )
    else:
        result = {
            "status": "error",
            "erp": connection.type,
            "reason": "settlement_application_api_not_available_for_connector",
        }

    if isinstance(result, dict) and idempotency_key and not result.get("idempotency_key"):
        result = {**result, "idempotency_key": idempotency_key}
    if isinstance(result, dict) and ap_item_id and not result.get("ap_item_id"):
        result = {**result, "ap_item_id": ap_item_id}
    return result


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


# ---------------------------------------------------------------------------
# Lookup helpers used by the agent runtime
# ---------------------------------------------------------------------------

async def lookup_purchase_order_from_erp(
    organization_id: str,
    po_number: str,
) -> Optional[Dict[str, Any]]:
    """Look up a purchase order by number across the connected ERP.

    This is a thin seam consumed by the AP validation gate / agent runtime.
    Currently delegates to the bill-finder (POs and bills share document-number
    lookup in most ERPs).  Returns ``None`` when no ERP is connected or the PO
    is not found.
    """
    connection = get_erp_connection(organization_id)
    if not connection:
        return None
    finder = _BILL_FINDERS.get(connection.type)
    if not finder:
        return None
    try:
        return await finder(connection, po_number)
    except Exception as exc:
        logger.warning("PO lookup failed (non-fatal): %s", exc)
        return None


async def find_open_payables_for_vendor(
    organization_id: str,
    vendor_name: str,
) -> List[Dict[str, Any]]:
    """Return open payables for a vendor.  Placeholder — returns []."""
    return []


# ==================== Payment Status Lookup ====================

_PAYMENT_STATUS_LOOKUPS = {
    "quickbooks": get_payment_status_quickbooks,
    "xero": get_payment_status_xero,
    "netsuite": get_payment_status_netsuite,
    "sap": get_payment_status_sap,
}


async def get_bill_payment_status(
    organization_id: str,
    erp_reference: str,
    invoice_number: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Read payment status from the ERP for a posted bill.

    This function NEVER executes payments — it only reads status via GET/query
    requests.  Returns a normalized dict:

        {"paid": bool, "payment_amount": float, "payment_date": str,
         "payment_method": str, "payment_reference": str,
         "partial": bool, "remaining_balance": float}

    Or on failure:

        {"paid": False, "reason": "not_found"}
        {"paid": False, "error": "<description>"}
    """
    connection = get_erp_connection(organization_id, entity_id=entity_id)
    if not connection:
        return {"paid": False, "reason": "no_erp_connection"}

    erp_type = str(connection.type or "").strip().lower()
    lookup = _PAYMENT_STATUS_LOOKUPS.get(erp_type)
    if not lookup:
        return {"paid": False, "reason": f"no_payment_lookup_for_{erp_type}"}

    try:
        result = await lookup(connection, erp_reference)
        # If first attempt gets a token expiry, try refresh + retry once
        if isinstance(result, dict) and result.get("needs_reauth"):
            refreshed = False
            if erp_type == "quickbooks":
                refreshed = bool(await refresh_quickbooks_token(connection))
            elif erp_type == "xero":
                refreshed = bool(await refresh_xero_token(connection))
            if refreshed:
                set_erp_connection(organization_id, connection)
                result = await lookup(connection, erp_reference)
        return result
    except Exception as exc:
        logger.warning(
            "Payment status lookup failed for org=%s ref=%s: %s",
            organization_id, erp_reference, exc,
        )
        return {"paid": False, "error": str(exc)}
