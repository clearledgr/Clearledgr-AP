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

import asyncio as _asyncio_for_lock
import logging
import secrets as _secrets_for_lock
import time as _time_for_lock
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.database import get_db as _canonical_get_db
from clearledgr.core.http_client import get_http_client

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
    reverse_bill_from_quickbooks,
    get_bill_quickbooks,
    find_vendor_credit_quickbooks,
    apply_credit_note_to_quickbooks,
    apply_settlement_to_quickbooks,
    create_vendor_quickbooks,
    find_vendor_quickbooks,
    find_bill_quickbooks,
    _attach_to_quickbooks,
    get_payment_status_quickbooks,
    get_chart_of_accounts_quickbooks,
    list_all_purchase_orders_quickbooks,
    list_all_vendors_quickbooks,
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
    reverse_bill_from_xero,
    find_credit_note_xero,
    apply_credit_note_to_xero,
    apply_settlement_to_xero,
    create_vendor_xero,
    find_vendor_xero,
    find_bill_xero,
    _attach_to_xero,
    get_payment_status_xero,
    get_chart_of_accounts_xero,
    list_all_purchase_orders_xero,
    list_all_vendors_xero,
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
    reverse_bill_from_netsuite,
    get_vendor_bill_netsuite,
    find_credit_note_netsuite,
    apply_credit_note_to_netsuite,
    apply_settlement_to_netsuite,
    create_vendor_netsuite,
    find_vendor_netsuite,
    find_bill_netsuite,
    _attach_to_netsuite,
    get_payment_status_netsuite,
    get_chart_of_accounts_netsuite,
    list_all_vendors_netsuite,
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
    reverse_bill_from_sap,
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
    get_chart_of_accounts_sap,
    list_all_vendors_sap,
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
    subsidiary_id: Optional[str] = None  # NetSuite OneWorld subsidiary internal ID

    # Inbound webhook shared secret (per-tenant). QBO calls this the
    # "verifier token"; Xero calls it the "webhook key"; NetSuite and
    # SAP use a generic HMAC shared secret. One field regardless of
    # ERP — the webhook verifier module treats each per its protocol.
    webhook_secret: Optional[str] = None


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
        subsidiary_id=creds.get('subsidiary_id'),
        webhook_secret=creds.get('webhook_secret'),
    )


# ---------------------------------------------------------------------------
# Per-(org, erp_type) refresh-token dedupe — two-tier locking.
#
# Why this matters: QuickBooks invalidates the previous refresh_token
# on every successful refresh. Xero is similar. If N concurrent posts
# hit a stale access token, all N get 401, all N call refresh in
# parallel, the first wins, the other N-1 send a now-burned RT to
# Intuit and get invalid_grant — connection permanently broken until
# an admin re-OAuths.
#
# Two layers of protection, walked in order on every refresh attempt:
#
#   1. **Cross-process Redis SETNX lock** (clearledgr:erp_refresh_lock:
#      <org>:<erp>). 30s TTL so a crashed worker can't pin the lock
#      forever. Holders carry a unique token; release uses a Lua
#      compare-and-delete so we can't release someone else's lock.
#      If acquired: we're the only refresher across the whole fleet.
#      If not acquired: another pod or process is refreshing — we
#      poll the DB connection row briefly waiting for the new tokens
#      to land, then adopt them. On poll timeout (rare), we fail the
#      caller's request rather than refresh in parallel.
#
#   2. **In-process asyncio.Lock** (per (org, erp_type)). Inside one
#      worker pod, multiple coroutines hit 401 simultaneously; the
#      asyncio lock collapses them to one Redis-lock contender, which
#      is the cheapest possible serialization layer.
#
# When Redis is unreachable we fall back to in-process-only locking —
# same behaviour as the original ship — so dev (no Redis) and Redis
# outages still work, just without the cross-process guarantee.
# ---------------------------------------------------------------------------
_REFRESH_LOCKS: Dict[str, _asyncio_for_lock.Lock] = {}

# How long the Redis lock TTL is — bounds blast radius if the holder
# crashes. 30s comfortably covers the typical QB/Xero refresh round-
# trip (200-800ms) plus retry budget; way under any practical "stuck
# refresh" window.
_REDIS_LOCK_TTL_SECONDS = 30
# How long a non-acquiring caller waits for the holder to land new
# tokens in the DB. Tuned a touch above the typical refresh latency
# so the slow callers piggyback rather than fail. If the holder
# really is stuck, we'd rather fail this one request than block forever.
_REDIS_LOCK_WAIT_SECONDS = 8.0
_REDIS_LOCK_POLL_INTERVAL = 0.25

# Lua script: delete the lock key only if its current value matches
# the token we passed in. Atomic on the Redis side — without this we
# could DELETE a key that another holder set after our TTL expired.
_REDIS_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


def _refresh_lock_for(organization_id: str, erp_type: str) -> _asyncio_for_lock.Lock:
    key = f"{organization_id}:{erp_type}"
    lock = _REFRESH_LOCKS.get(key)
    if lock is None:
        lock = _asyncio_for_lock.Lock()
        _REFRESH_LOCKS[key] = lock
    return lock


def _redis_for_refresh_lock():
    """Return the rate_limit module's Redis client, or None if Redis
    isn't configured / reachable. Reusing rate_limit's client means
    we don't spin up a second connection pool just for refresh locks.
    """
    try:
        from clearledgr.services import rate_limit
        return rate_limit._redis_client
    except Exception:
        return None


def _try_acquire_redis_lock(redis_client, key: str) -> Optional[str]:
    """SETNX with TTL. Returns the unique token on acquire, None
    otherwise. Failures (Redis blip) return None — caller falls back
    to in-process-only locking, which is strictly weaker but better
    than failing the user's post."""
    if redis_client is None:
        return None
    token = _secrets_for_lock.token_urlsafe(16)
    try:
        ok = redis_client.set(key, token, nx=True, ex=_REDIS_LOCK_TTL_SECONDS)
        return token if ok else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[refresh_lock] SETNX failed for %s: %s", key, exc)
        return None


def _release_redis_lock(redis_client, key: str, token: str) -> None:
    if redis_client is None or not token:
        return
    try:
        redis_client.eval(_REDIS_RELEASE_SCRIPT, 1, key, token)
    except Exception as exc:  # noqa: BLE001
        # Releasing failed — the lock will TTL-expire in
        # _REDIS_LOCK_TTL_SECONDS, so the worst case is one held lock
        # per failure. Not worth raising.
        logger.warning("[refresh_lock] release failed for %s: %s", key, exc)


async def _wait_for_other_refresher(
    organization_id: str,
    erp_type: str,
    connection,
) -> Optional[str]:
    """Poll the DB connection row until the refresh_token changes
    (the holder of the lock landed new tokens) or we time out.
    Returns the new access_token on success."""
    deadline = _time_for_lock.monotonic() + _REDIS_LOCK_WAIT_SECONDS
    original_rt = connection.refresh_token
    while _time_for_lock.monotonic() < deadline:
        try:
            fresh = get_erp_connection(organization_id)
        except Exception:
            fresh = None
        if (
            fresh
            and str(fresh.type or "").lower() == str(erp_type or "").lower()
            and getattr(fresh, "refresh_token", None)
            and fresh.refresh_token != original_rt
        ):
            connection.access_token = fresh.access_token
            connection.refresh_token = fresh.refresh_token
            return connection.access_token
        await _asyncio_for_lock.sleep(_REDIS_LOCK_POLL_INTERVAL)
    logger.warning(
        "[refresh_lock] gave up waiting for cross-process refresher on %s/%s",
        organization_id, erp_type,
    )
    return None


async def refresh_with_dedupe(
    *,
    organization_id: str,
    erp_type: str,
    connection,
    refresh_fn,
) -> Optional[str]:
    """Run refresh_fn(connection) under a per-(org, erp_type) lock,
    skipping the OAuth call entirely if another caller already
    refreshed while we were waiting for the lock.

    Returns the new access token on success (whether ours or the other
    caller's), or None on failure. Caller is responsible for writing
    the (potentially mutated) connection back via set_erp_connection.
    """
    in_proc_lock = _refresh_lock_for(organization_id, erp_type)
    async with in_proc_lock:
        # Cheapest check first: did another in-process coroutine
        # refresh while we were waiting for the asyncio lock?
        try:
            fresh = get_erp_connection(organization_id)
        except Exception:
            fresh = None
        if (
            fresh
            and str(fresh.type or "").lower() == str(erp_type or "").lower()
            and getattr(fresh, "refresh_token", None)
            and fresh.refresh_token != connection.refresh_token
        ):
            connection.access_token = fresh.access_token
            connection.refresh_token = fresh.refresh_token
            return connection.access_token

        # Cross-process gate: try to claim the Redis lock. If a different
        # worker pod is refreshing the same org's token, we wait for them
        # to land new tokens in the DB rather than hammering the OAuth
        # endpoint with a now-burned RT.
        redis_client = _redis_for_refresh_lock()
        redis_key = f"clearledgr:erp_refresh_lock:{organization_id}:{erp_type}"
        redis_token = _try_acquire_redis_lock(redis_client, redis_key)

        if redis_client is not None and redis_token is None:
            # Another pod owns the lock. Poll the DB for their result.
            adopted = await _wait_for_other_refresher(
                organization_id, erp_type, connection,
            )
            if adopted:
                return adopted
            # Timed out — rather than refresh in parallel and risk
            # burning the RT, fail this request. The caller will see
            # a non-success post and the next attempt will retry under
            # a (likely-now-released) lock.
            return None

        # Either we got the Redis lock, or Redis is unavailable and
        # we're proceeding under in-process-only protection. Either
        # way, do the refresh and persist.
        try:
            return await refresh_fn(connection)
        finally:
            _release_redis_lock(redis_client, redis_key, redis_token or "")


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
    if connection.subsidiary_id:
        credentials['subsidiary_id'] = connection.subsidiary_id
    if connection.webhook_secret:
        credentials['webhook_secret'] = connection.webhook_secret

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

def _enforce_erp_rate_limit(organization_id: str, erp_type: str) -> Optional[Dict[str, Any]]:
    """§11.1: Rate-limit check before any ERP API call.

    Returns a dict with rate_limited status if over limit, None if allowed.
    Every ERP-calling function must invoke this before making the API call.
    """
    try:
        from clearledgr.integrations.erp_rate_limiter import get_erp_rate_limiter
        get_erp_rate_limiter().check_and_consume(organization_id, erp_type)
        return None
    except Exception as exc:
        if "rate limit exceeded" in str(exc).lower():
            return {
                "status": "rate_limited",
                "reason": str(exc),
                "erp": erp_type,
                "retry_after": getattr(exc, "retry_after", 5),
            }
        return None  # Non-rate-limit error — proceed


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

    # §11.1: Rate-limit check
    rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if rate_limited:
        return rate_limited

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
        "accounts_payable": "33",  # AP control account
        "payment_fees": "74",  # Bank Service Charges
        "revenue": "1",
        "expenses": "7",  # Expenses (default AP bill debit account)
        # Wave 3 / E2 + E4: VAT control accounts
        "vat_input": "TaxOnPurchases",
        "vat_output": "TaxOnSales",
    },
    "xero": {
        "cash": "090",  # Business Bank Account
        "accounts_receivable": "610",  # Accounts Receivable
        "accounts_payable": "800",  # Accounts Payable
        "payment_fees": "404",  # Bank Fees
        "revenue": "200",  # Sales
        "expenses": "400",  # General Expenses (default AP bill debit account)
        "vat_input": "820",   # VAT (input)
        "vat_output": "825",  # VAT (output) — RC self-assessed
    },
    "netsuite": {
        "cash": "1000",  # Cash and Cash Equivalents
        "accounts_receivable": "1200",  # Accounts Receivable
        "accounts_payable": "2000",  # Accounts Payable
        "payment_fees": "6800",  # Bank Service Charges
        "revenue": "4000",  # Sales Revenue
        "expenses": "67",  # Vendor expense (default AP bill debit account)
        "vat_input": "1410",
        "vat_output": "2410",
    },
    "sap": {
        "cash": "1000",  # Cash
        "accounts_receivable": "1100",  # AR
        "accounts_payable": "1600",  # AP control account (typical 1600/2100 in CoA)
        "payment_fees": "6200",  # Bank Charges
        "revenue": "4000",  # Revenue
        "expenses": "6000",  # General Expenses (default AP invoice GL account)
        "vat_input": "1576",   # SAP standard input VAT
        "vat_output": "3806",  # SAP standard output VAT
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


# ==================== ERP CUSTOM FIELD MAPPINGS (Module 5 Pass C) ====================
#
# Pass A persisted custom field mappings under
# settings_json["erp_field_mappings"][erp_type] (NetSuite custbody_*,
# SAP Z-fields, QB Class/Location, Xero tracking categories). Pass B
# surfaced connection health. Pass C makes the mapping load-bearing:
# at posting time, ``post_bill`` resolves the configured field IDs
# and the per-poster code stamps them onto the outbound payload.
#
# Two kinds of fields:
#   * Workflow fields (state_field, box_id_field, approver_field,
#     correlation_id_field) — the customer chose the ERP field id;
#     the value comes from the AP item / bill metadata at posting
#     time. Resolved by ``_resolve_workflow_custom_fields`` below.
#   * Dimension fields (department_field, class_field, location_field,
#     cost_center_field, profit_center_field, wbs_field) — the
#     customer renames a *standard* dimension. The poster reads the
#     mapping and writes the dimension under the configured field
#     name instead of the default.

def _get_org_field_mappings(organization_id: str, erp_type: str) -> Dict[str, str]:
    """Load per-tenant ERP field mappings from org settings_json.

    Returns ``{}`` (every-default) when nothing is configured. Tolerant
    of legacy stringified ``settings_json`` rows.
    """
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
        if not isinstance(settings, dict):
            return {}
        all_mappings = settings.get("erp_field_mappings") or {}
        if not isinstance(all_mappings, dict):
            return {}
        erp_key = str(erp_type or "").strip().lower()
        per_erp = all_mappings.get(erp_key) or {}
        return dict(per_erp) if isinstance(per_erp, dict) else {}
    except Exception:
        return {}


def _resolve_workflow_custom_fields(
    *,
    field_mappings: Dict[str, str],
    organization_id: str,
    ap_item_id: Optional[str],
) -> Dict[str, str]:
    """Compute the (erp_field_id → value) pairs for workflow fields.

    Reads the AP item once to pull state, box id, approver email, and
    correlation id, then matches each configured catalog key to its
    resolved value. Returns only entries the customer has both
    configured AND that have a non-empty value — empty payloads on
    optional custom fields are silent (the poster simply doesn't stamp
    them).

    Errors loading the AP item are non-fatal; we log and return ``{}``
    so a transient DB hiccup never blocks a bill from posting.
    """
    if not field_mappings or not ap_item_id:
        return {}

    try:
        db = _get_db()
        ap_item = db.get_ap_item(ap_item_id) or {}
    except Exception as exc:
        logger.warning(
            "[field_mapping] could not load AP item %s for custom-field resolution: %s",
            ap_item_id, exc,
        )
        return {}

    # Source-of-truth values per workflow key. Pull from common AP item
    # column aliases — the AP store has historically renamed columns
    # (state vs. status, approver_email vs. final_approver_email), so
    # we read the first non-empty alias.
    def _first(*keys) -> Optional[str]:
        for k in keys:
            v = ap_item.get(k)
            if v:
                return str(v)
        return None

    sources = {
        "state_field": _first("state", "status"),
        "box_id_field": _first("id", "ap_item_id", "box_id"),
        "approver_field": _first(
            "final_approver_email",
            "approver_email",
            "approved_by_email",
            "approved_by",
        ),
        "correlation_id_field": _first("correlation_id", "agent_correlation_id"),
    }

    out: Dict[str, str] = {}
    for catalog_key, value in sources.items():
        erp_field = field_mappings.get(catalog_key)
        if erp_field and value:
            out[str(erp_field)] = str(value)
    return out


def _dimension_field_name(
    field_mappings: Dict[str, str], catalog_key: str, default: str,
) -> str:
    """Resolve the ERP-side field name for a standard dimension.

    If the customer configured an override (e.g. NetSuite has
    ``department`` renamed to ``department_2``), use it; otherwise
    fall back to the catalog default.
    """
    return str(
        (field_mappings or {}).get(catalog_key)
        or default
    ).strip() or default


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
    tax_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    discount_amount: Optional[float] = None
    discount_terms: Optional[str] = None
    payment_terms: Optional[str] = None


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


# ==================== Pre-Post Validation (§12.3) ====================


def pre_post_validate(
    ap_item_id: str,
    organization_id: str,
    db: Any = None,
) -> Dict[str, Any]:
    """§12.3: Re-validate against current ERP/DB state before posting.

    Checks:
    1. AP item not already posted (erp_reference check)
    2. No duplicate bill with same invoice_number in trailing 90 days
    3. Vendor is active (not frozen, not pending onboarding)

    Returns {valid: bool, failures: [{check, reason}]}
    """
    if db is None:
        db = _get_db()

    failures: list = []
    item = db.get_ap_item(ap_item_id)
    if not item:
        return {"valid": False, "failures": [{"check": "item_exists", "reason": "AP item not found"}]}

    # 1. Already posted check
    if item.get("erp_reference"):
        return {"valid": False, "failures": [{"check": "already_posted", "reason": f"Already posted: {item['erp_reference']}"}]}

    # 1a. State guard — TOCTOU defence.
    #
    # invoice_posting._post_to_erp checks state == ready_to_post near
    # the top of the function, but then does a bunch of async work
    # (vendor create, audit-event write, adapter resolution) before
    # we land here. In that window, a concurrent reject_invoice call
    # on the same AP item writes state="rejected" at the DB level
    # (invoice_posting.py:784). Without this check, we'd then POST a
    # bill to the ERP for an item our own DB says is rejected —
    # ERP gets a live bill, local state says rejected, reconciliation
    # diverges and the CFO asks uncomfortable questions.
    #
    # Only ``ready_to_post`` and ``approved`` can post. Anything else
    # (rejected, needs_info, snoozed, failed_post not yet retried)
    # bails out with a structured failure the caller can render.
    current_state = str(item.get("state") or "").strip().lower()
    if current_state not in ("ready_to_post", "approved"):
        return {
            "valid": False,
            "failures": [{
                "check": "state_guard",
                "reason": f"AP item is in state '{current_state}', not ready_to_post",
            }],
        }

    # 2. Duplicate bill in trailing 90 days
    invoice_number = item.get("invoice_number") or ""
    vendor_name = item.get("vendor_name") or ""
    if invoice_number and vendor_name:
        try:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            all_items = db.list_ap_items(organization_id=organization_id, limit=500)
            for other in all_items:
                if other.get("id") == ap_item_id:
                    continue
                if (
                    other.get("invoice_number") == invoice_number
                    and other.get("vendor_name") == vendor_name
                    and other.get("erp_reference")
                    and (other.get("erp_posted_at") or other.get("created_at", "")) >= cutoff
                ):
                    failures.append({
                        "check": "duplicate_bill_90d",
                        "reason": f"Bill {invoice_number} for {vendor_name} already posted as {other['erp_reference']} within 90 days",
                    })
                    break
        except Exception as exc:
            logger.debug("[pre_post_validate] Duplicate check failed: %s", exc)

    # 3. Vendor active check
    if vendor_name:
        try:
            if hasattr(db, "get_vendor_profile"):
                profile = db.get_vendor_profile(organization_id, vendor_name)
                if profile:
                    status = str(profile.get("status") or "").lower()
                    if status in ("frozen", "suspended", "blocked"):
                        failures.append({
                            "check": "vendor_active",
                            "reason": f"Vendor '{vendor_name}' is {status}",
                        })
        except Exception as exc:
            logger.debug("[pre_post_validate] Vendor check failed: %s", exc)

    # 4. Wave 1 / A5 — mandatory GL coding at posting time.
    #
    # Per AP cycle reference doc Stage 5 + AICPA accuracy assertion:
    # GL is mandatory on every bill. Today the bill-posting path
    # silently defaults to the org's ``expenses`` account when the
    # invoice carries no GL — which both Hackett and Levvel call out
    # as a top driver of mis-classified spend ("inconsistent coding
    # for the same vendor across periods" / "incorrect cost-center
    # allocation that pollutes department-level financial reports").
    #
    # This gate is on by default. Operators can opt out per-tenant
    # via settings_json["mandatory_gl_at_posting"]=False for tenants
    # that deliberately want default-account fallback (typically
    # low-value automation flows). The default is enforced.
    #
    # Resolution: every line item in metadata.line_items must carry a
    # non-empty gl_code. Items without line_items must have a top-
    # level ap_item.metadata.gl_code, OR the org-level gl_account_map
    # must define an explicit ``expenses`` account (the default
    # fallback is what we're refusing here).
    if _mandatory_gl_enabled(db, organization_id):
        gl_failures = _check_mandatory_gl(item, db, organization_id)
        failures.extend(gl_failures)

    return {"valid": len(failures) == 0, "failures": failures}


def _mandatory_gl_enabled(db: Any, organization_id: str) -> bool:
    """Resolve the per-tenant ``mandatory_gl_at_posting`` toggle.

    Default True — the AICPA-aligned safe behaviour. Tenants that
    deliberately want default-account fallback set the flag to False
    in settings_json.
    """
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return True
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            import json as _json
            settings = _json.loads(settings)
        except Exception:
            return True
    if not isinstance(settings, dict):
        return True
    raw = settings.get("mandatory_gl_at_posting")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _check_mandatory_gl(
    ap_item: Dict[str, Any], db: Any, organization_id: str,
) -> List[Dict[str, str]]:
    """Per-line GL completeness check.

    Returns a list of failure dicts. Empty list = OK.
    """
    failures: List[Dict[str, str]] = []
    metadata = ap_item.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json as _json
            metadata = _json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    line_items = metadata.get("line_items")
    if not isinstance(line_items, list):
        line_items = None

    if line_items:
        # Every line must have a non-empty gl_code.
        missing_lines: List[int] = []
        for idx, line in enumerate(line_items):
            if not isinstance(line, dict):
                missing_lines.append(idx)
                continue
            gl_code = str(
                line.get("gl_code")
                or line.get("gl_account")
                or line.get("account_code")
                or ""
            ).strip()
            if not gl_code:
                missing_lines.append(idx)
        if missing_lines:
            failures.append({
                "check": "mandatory_gl",
                "reason": (
                    f"GL code missing on {len(missing_lines)} line item"
                    f"{'s' if len(missing_lines) != 1 else ''} "
                    f"(indexes: {missing_lines})"
                ),
                "missing_line_indexes": ",".join(str(i) for i in missing_lines),
            })
        return failures

    # Single-line invoice path: top-level GL OR an AP-item-level
    # gl_code field. Either lands the bill on a real account.
    top_level_gl = (
        ap_item.get("gl_code")
        or metadata.get("gl_code")
        or metadata.get("suggested_gl_code")
    )
    if not (str(top_level_gl or "").strip()):
        failures.append({
            "check": "mandatory_gl",
            "reason": (
                "AP item has no line items and no top-level GL code; "
                "explicit GL is required by org policy "
                "(settings_json.mandatory_gl_at_posting=true)."
            ),
        })
    return failures


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

    # §12.3: Pre-post validation — check before any ERP write
    if ap_item_id:
        validation = pre_post_validate(ap_item_id, organization_id)
        if not validation.get("valid"):
            logger.warning(
                "[post_bill] pre_post_validate failed for %s: %s",
                ap_item_id, validation.get("failures"),
            )
            return {
                "status": "pre_post_validation_failed",
                "reason": "pre_post_validate",
                "failures": validation.get("failures", []),
                "idempotency_key": idempotency_key,
            }

    connection = get_erp_connection(organization_id, entity_id=entity_id)

    if not connection:
        logger.warning("No ERP connected for %s", organization_id)
        return {"status": "skipped", "reason": "No ERP Connected", "idempotency_key": idempotency_key}

    # §11.1: Per-ERP rate limit check before any API call
    try:
        from clearledgr.integrations.erp_rate_limiter import get_erp_rate_limiter
        get_erp_rate_limiter().check_and_consume(organization_id, connection.type)
    except Exception as rate_exc:
        if "rate limit exceeded" in str(rate_exc).lower():
            return {
                "status": "rate_limited",
                "reason": str(rate_exc),
                "erp": connection.type,
                "retry_after": getattr(rate_exc, "retry_after", 5),
            }
        # Non-rate-limit errors: log and proceed
        logger.debug("[post_bill] Rate limiter check failed (non-fatal): %s", rate_exc)

    gl_map = _get_entity_gl_map(organization_id, entity_id) or _get_org_gl_map(organization_id)

    # Module 5 Pass C — resolve per-tenant custom field mappings.
    # ``field_mappings`` is the raw catalog → ERP-field-id dict (used
    # by the posters to rename dimension fields like department/class/
    # location). ``custom_fields`` is the pre-resolved (erp_field_id →
    # value) dict for workflow fields (state/box_id/approver/correlation_id)
    # that the posters stamp directly onto the outbound bill payload.
    field_mappings = _get_org_field_mappings(organization_id, connection.type)
    custom_fields = _resolve_workflow_custom_fields(
        field_mappings=field_mappings,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
    )

    if connection.type == "quickbooks":
        result = await post_bill_to_quickbooks(
            connection, bill, gl_map=gl_map,
            field_mappings=field_mappings, custom_fields=custom_fields,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="quickbooks",
                connection=connection, refresh_fn=refresh_quickbooks_token,
            )
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await post_bill_to_quickbooks(
                    connection, bill, gl_map=gl_map,
                    field_mappings=field_mappings, custom_fields=custom_fields,
                )
    elif connection.type == "xero":
        result = await post_bill_to_xero(
            connection, bill, gl_map=gl_map,
            field_mappings=field_mappings, custom_fields=custom_fields,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="xero",
                connection=connection, refresh_fn=refresh_xero_token,
            )
            if new_token:
                set_erp_connection(organization_id, connection)
                result = await post_bill_to_xero(
                    connection, bill, gl_map=gl_map,
                    field_mappings=field_mappings, custom_fields=custom_fields,
                )
    elif connection.type == "netsuite":
        result = await post_bill_to_netsuite(
            connection, bill, gl_map=gl_map,
            field_mappings=field_mappings, custom_fields=custom_fields,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            # H7: NetSuite uses OAuth 1.0a — no token refresh, but retry once
            # in case of transient clock-skew causing signature mismatch.
            logger.warning("NetSuite 401 for org %s — retrying once (clock-skew mitigation)", organization_id)
            result = await post_bill_to_netsuite(
                connection, bill, gl_map=gl_map,
                field_mappings=field_mappings, custom_fields=custom_fields,
            )
    elif connection.type == "sap":
        result = await post_bill_to_sap(
            connection, bill, gl_map=gl_map,
            field_mappings=field_mappings, custom_fields=custom_fields,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            # H9: SAP B1 session may have expired — retry forces a fresh Login.
            logger.warning("SAP 401 for org %s — retrying with fresh session", organization_id)
            result = await post_bill_to_sap(
                connection, bill, gl_map=gl_map,
                field_mappings=field_mappings, custom_fields=custom_fields,
            )
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


# ==================== Bill Reversal Dispatch ====================


async def reverse_bill(
    organization_id: str,
    erp_reference: str,
    *,
    reason: str,
    ap_item_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    actor_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reverse a posted ERP bill during the override window.

    This is the single entry point used by the Phase 1.4 override-window
    mechanism and any ops-surface "undo" action. It dispatches to the
    connector-specific ``reverse_bill_from_<erp>`` function, handles
    reauth retries on token expiry, enforces two layers of idempotency,
    and emits structured audit events on every success, skip, and
    failure.

    ``reason`` is MANDATORY — every reversal must have a caller-supplied
    reason string for the audit trail (e.g. ``"human_override"``,
    ``"validation_failure_reprocess"``, ``"duplicate_post_detected"``).

    Idempotency:
      1. If the AP item's metadata already carries a ``reversal_reference``
         we return ``status="already_reversed"`` without hitting the ERP.
      2. If the ``idempotency_key`` matches an existing
         ``erp_reversal_succeeded`` audit event we return the cached result.

    Per DESIGN_THESIS.md §8, the ability to reverse a post within the
    override window is the architectural precondition that lets
    Clearledgr auto-post with confidence — the human escape hatch makes
    autonomous posting safe.
    """
    org_id = str(organization_id or "").strip() or "default"
    ref = str(erp_reference or "").strip()
    if not ref:
        return {
            "status": "error",
            "reason": "missing_erp_reference",
            "idempotency_key": idempotency_key,
        }
    if not reason or not str(reason).strip():
        return {
            "status": "error",
            "reason": "missing_reversal_reason",
            "idempotency_key": idempotency_key,
        }

    reason_str = str(reason).strip()
    import json  # local import to match the module's existing style

    # --- Idempotency guard 1: AP item metadata cache ---
    if ap_item_id:
        db = _get_db()
        existing = db.get_ap_item(ap_item_id)
        if existing:
            meta = existing.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            cached_reversal = (meta or {}).get("reversal_reference") if isinstance(meta, dict) else None
            if cached_reversal:
                logger.info(
                    "Idempotency: AP item %s already has reversal_reference=%s",
                    ap_item_id, cached_reversal,
                )
                return {
                    "status": "already_reversed",
                    "reference_id": ref,
                    "reversal_ref": cached_reversal,
                    "reversal_method": (meta or {}).get("reversal_method"),
                    "erp": (meta or {}).get("reversal_erp_type"),
                    "idempotency_key": idempotency_key,
                }

    # --- Idempotency guard 2: audit event by idempotency_key ---
    if idempotency_key:
        try:
            db = _get_db()
            existing_event = db.get_ap_audit_event_by_key(idempotency_key)
            if existing_event and str(
                existing_event.get("event_type") or ""
            ) == "erp_reversal_succeeded":
                logger.info(
                    "Idempotency: reversal key %s already succeeded — returning cached result",
                    idempotency_key,
                )
                payload = existing_event.get("payload_json") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                return {
                    "status": "already_reversed",
                    "reference_id": ref,
                    "reversal_ref": (payload or {}).get("reversal_ref"),
                    "reversal_method": (payload or {}).get("reversal_method"),
                    "erp": (payload or {}).get("erp"),
                    "idempotency_key": idempotency_key,
                }
        except Exception as exc:
            logger.debug("Reversal idempotency lookup failed (non-fatal): %s", exc)

    connection = get_erp_connection(org_id, entity_id=entity_id)
    if not connection:
        logger.warning("No ERP connected for %s — cannot reverse bill", org_id)
        return {
            "status": "skipped",
            "reason": "no_erp_connected",
            "reference_id": ref,
            "idempotency_key": idempotency_key,
        }

    # §11.1: Rate-limit check
    _rate_limited = _enforce_erp_rate_limit(org_id, connection.type)
    if _rate_limited:
        return _rate_limited

    erp_type = str(connection.type or "").strip().lower()

    async def _dispatch_once() -> Dict[str, Any]:
        if erp_type == "quickbooks":
            # Try to pull cached sync_token from AP item metadata if available
            sync_token: Optional[str] = None
            if ap_item_id:
                db = _get_db()
                existing_item = db.get_ap_item(ap_item_id)
                if existing_item:
                    meta = existing_item.get("metadata") or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    if isinstance(meta, dict):
                        st = meta.get("erp_sync_token")
                        if st is not None:
                            sync_token = str(st)
            return await reverse_bill_from_quickbooks(
                connection, ref, reason=reason_str, sync_token=sync_token
            )
        if erp_type == "xero":
            return await reverse_bill_from_xero(connection, ref, reason=reason_str)
        if erp_type == "netsuite":
            return await reverse_bill_from_netsuite(connection, ref, reason=reason_str)
        if erp_type == "sap":
            return await reverse_bill_from_sap(connection, ref, reason=reason_str)
        return {
            "status": "error",
            "reason": "unknown_erp_type",
            "erp": erp_type,
            "reference_id": ref,
        }

    result = await _dispatch_once()

    # Reauth retry loop — refresh the token and retry once on expiry.
    if isinstance(result, dict) and result.get("needs_reauth"):
        refresh_fn = None
        if erp_type == "quickbooks":
            refresh_fn = refresh_quickbooks_token
        elif erp_type == "xero":
            refresh_fn = refresh_xero_token
        if refresh_fn is not None:
            try:
                new_token = await refresh_with_dedupe(
                    organization_id=org_id, erp_type=erp_type,
                    connection=connection, refresh_fn=refresh_fn,
                )
            except Exception as exc:
                logger.warning(
                    "Token refresh raised during reversal for %s: %s",
                    erp_type, exc,
                )
                new_token = None
            if new_token:
                set_erp_connection(org_id, connection)
                result = await _dispatch_once()
        elif erp_type in {"netsuite", "sap"}:
            # NetSuite uses OAuth1, SAP uses session login — retry once
            # triggers a fresh session via the connector itself.
            logger.warning(
                "%s reauth during reversal for org %s — retrying once",
                erp_type, org_id,
            )
            result = await _dispatch_once()

    if not isinstance(result, dict):
        result = {"status": "error", "reason": "reversal_returned_non_dict"}

    # Stamp the idempotency key on the result for the caller.
    if idempotency_key and not result.get("idempotency_key"):
        result = {**result, "idempotency_key": idempotency_key}
    if ap_item_id and not result.get("ap_item_id"):
        result = {**result, "ap_item_id": ap_item_id}

    # --- Audit event emission ---
    try:
        db = _get_db()
        audit_event_type = {
            "success": "erp_reversal_succeeded",
            "already_reversed": "erp_reversal_already_reversed",
            "skipped": "erp_reversal_skipped",
            "error": "erp_reversal_failed",
        }.get(result.get("status"), "erp_reversal_failed")

        audit_payload: Dict[str, Any] = {
            "ap_item_id": ap_item_id or "",
            "event_type": audit_event_type,
            "actor_type": "user" if actor_id else "system",
            "actor_id": actor_id or "erp_router.reverse_bill",
            "reason": (
                f"Bill reversal: {reason_str} (erp={erp_type}, "
                f"ref={ref}, status={result.get('status')})"
            ),
            "metadata": {
                "erp": result.get("erp") or erp_type,
                "original_erp_reference": ref,
                "reversal_ref": result.get("reversal_ref"),
                "reversal_method": result.get("reversal_method"),
                "reversal_reason": reason_str,
                "request_reason_code": result.get("reason"),
                "erp_error_detail": result.get("erp_error_detail"),
                "erp_error_code": result.get("erp_error_code"),
            },
            "organization_id": org_id,
            "source": "erp_router.reverse_bill",
            "idempotency_key": idempotency_key,
            "decision_reason": reason_str,
        }
        db.append_audit_event(audit_payload)
    except Exception as audit_exc:
        logger.warning(
            "Reversal audit event write failed (non-fatal): %s", audit_exc
        )

    # --- Persist reversal_reference on the AP item (for the guard on next call) ---
    if (
        ap_item_id
        and isinstance(result, dict)
        and result.get("status") in {"success", "already_reversed"}
    ):
        try:
            db = _get_db()
            current = db.get_ap_item(ap_item_id)
            if current:
                meta = current.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                meta["reversal_reference"] = (
                    result.get("reversal_ref") or result.get("reference_id") or ref
                )
                meta["reversal_method"] = result.get("reversal_method")
                meta["reversal_erp_type"] = result.get("erp") or erp_type
                meta["reversal_reason"] = reason_str
                meta["reversal_recorded_at"] = datetime.now(timezone.utc).isoformat()
                db.update_ap_item(ap_item_id, metadata=json.dumps(meta))
        except Exception as persist_exc:
            logger.warning(
                "Reversal metadata persistence failed (non-fatal): %s", persist_exc
            )

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

    # §11.1: Rate-limit check
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
        return _rate_limited

    if connection.type == "xero":
        result = await apply_credit_note_to_xero(
            connection,
            application,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="xero",
                connection=connection, refresh_fn=refresh_xero_token,
            )
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
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="quickbooks",
                connection=connection, refresh_fn=refresh_quickbooks_token,
            )
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

    # §11.1: Rate-limit check
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
        return _rate_limited

    if connection.type == "quickbooks":
        gl_map = _get_org_gl_map(organization_id)
        result = await apply_settlement_to_quickbooks(
            connection,
            application,
            gl_map=gl_map,
            idempotency_key=idempotency_key,
        )
        if isinstance(result, dict) and result.get("needs_reauth"):
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="quickbooks",
                connection=connection, refresh_fn=refresh_quickbooks_token,
            )
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
            new_token = await refresh_with_dedupe(
                organization_id=organization_id, erp_type="xero",
                connection=connection, refresh_fn=refresh_xero_token,
            )
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

    # §11.1: Rate-limit check
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
        return _rate_limited

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

    # §11.1: Rate-limit check — return None so caller falls back to "not found" path
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
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

    # §11.1: Rate-limit check — skip preflight checks when over limit
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
        result["erp_type"] = connection.type
        result["rate_limited"] = True
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

    # 3. GL code validation against org mapping + cached chart of accounts
    if gl_codes:
        gl_map = _get_org_gl_map(organization_id)
        valid_codes: set = set()
        if gl_map:
            valid_codes.update(gl_map.values())

        # Also pull codes from cached chart of accounts (no ERP call — cache only)
        try:
            cached_coa = _get_cached_chart_of_accounts(organization_id)
            if cached_coa and isinstance(cached_coa.get("accounts"), list):
                for acct in cached_coa["accounts"]:
                    code = str(acct.get("code") or "").strip()
                    acct_id = str(acct.get("id") or "").strip()
                    if code:
                        valid_codes.add(code)
                    if acct_id:
                        valid_codes.add(acct_id)
        except Exception:
            pass  # non-fatal — fall back to GL map only

        if valid_codes:
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

    # §11.1: Rate-limit check — treat as non-fatal, return verified=True so pipeline isn't blocked
    _rate_limited = _enforce_erp_rate_limit(org_id, connection.type)
    if _rate_limited:
        return {"verified": True, "bill": None, "erp_type": connection.type, "reason": "rate_limited"}

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
        client = get_http_client()
        resp = await client.get(url, timeout=30)
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

    # §11.1: Rate-limit check — skip attachment upload (non-fatal, caller treats None as warning)
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
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
    # §11.1: Rate-limit check — skip PO lookup (caller falls back to no-PO path)
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
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

    # §11.1: Rate-limit check
    _rate_limited = _enforce_erp_rate_limit(organization_id, connection.type)
    if _rate_limited:
        return {"paid": False, "reason": "rate_limited"}

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
                refreshed = bool(await refresh_with_dedupe(
                    organization_id=organization_id, erp_type="quickbooks",
                    connection=connection, refresh_fn=refresh_quickbooks_token,
                ))
            elif erp_type == "xero":
                refreshed = bool(await refresh_with_dedupe(
                    organization_id=organization_id, erp_type="xero",
                    connection=connection, refresh_fn=refresh_xero_token,
                ))
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


# ==================== Chart of Accounts Dispatcher ====================

_CHART_OF_ACCOUNTS_FETCHERS = {
    "quickbooks": get_chart_of_accounts_quickbooks,
    "xero": get_chart_of_accounts_xero,
    "netsuite": get_chart_of_accounts_netsuite,
    "sap": get_chart_of_accounts_sap,
}

# Default cache TTL: 24 hours (in seconds)
_COA_CACHE_TTL_SECONDS = 24 * 60 * 60


def _get_cached_chart_of_accounts(
    organization_id: str,
) -> Optional[Dict[str, Any]]:
    """Return cached chart of accounts from org settings_json, or None if stale/missing."""
    import json as _json

    try:
        db = _get_db()
        org = db.get_organization(organization_id)
        if not org:
            return None
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                return None
        cache = settings.get("chart_of_accounts_cache")
        if not isinstance(cache, dict):
            return None
        fetched_at = cache.get("fetched_at")
        if not fetched_at:
            return None
        # Parse the fetched_at timestamp and check TTL
        try:
            fetched_dt = datetime.fromisoformat(fetched_at)
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if age_seconds > _COA_CACHE_TTL_SECONDS:
                return None
        except (ValueError, TypeError):
            return None
        return cache
    except Exception:
        return None


def _save_chart_of_accounts_cache(
    organization_id: str,
    accounts: List[Dict[str, Any]],
    erp_type: str,
) -> None:
    """Store chart of accounts in org settings_json for caching."""
    import json as _json

    try:
        db = _get_db()
        org = db.get_organization(organization_id)
        if not org:
            return
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        if not isinstance(settings, dict):
            settings = {}

        settings["chart_of_accounts_cache"] = {
            "accounts": accounts,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "erp_type": erp_type,
            "account_count": len(accounts),
        }

        db.update_organization(organization_id, settings_json=settings)
    except Exception as exc:
        logger.warning("Failed to cache chart of accounts for org %s: %s", organization_id, exc)


async def get_chart_of_accounts(
    organization_id: str,
    entity_id: Optional[str] = None,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch chart of accounts from the connected ERP.

    Results are cached in organization settings for 24h.
    Pass force_refresh=True to bypass cache.

    Returns an empty list on any error so the caller is never blocked.
    """
    org_id = str(organization_id or "").strip() or "default"

    # Check cache first (unless force_refresh)
    if not force_refresh:
        cached = _get_cached_chart_of_accounts(org_id)
        if cached is not None:
            return cached.get("accounts", [])

    # Resolve ERP connection
    connection = get_erp_connection(org_id, entity_id=entity_id)
    if not connection:
        logger.debug("No ERP connection for org %s, returning empty chart of accounts", org_id)
        return []

    # §11.1: Rate-limit check — return empty list so caller falls back to cached/empty path
    _rate_limited = _enforce_erp_rate_limit(org_id, connection.type)
    if _rate_limited:
        logger.warning("Chart of accounts fetch rate-limited for org %s", org_id)
        return []

    erp_type = str(connection.type or "").strip().lower()
    fetcher = _CHART_OF_ACCOUNTS_FETCHERS.get(erp_type)
    if not fetcher:
        logger.warning("No chart-of-accounts fetcher for ERP type: %s", erp_type)
        return []

    try:
        accounts = await fetcher(connection)
    except Exception as exc:
        logger.error("Chart of accounts fetch failed for org %s (%s): %s", org_id, erp_type, exc)
        return []

    # Cache the result
    if accounts:
        _save_chart_of_accounts_cache(org_id, accounts, erp_type)

    return accounts


# =====================================================================
# Vendor list — full directory from ERP with caching
# =====================================================================

_VENDOR_LIST_FETCHERS = {
    "quickbooks": list_all_vendors_quickbooks,
    "xero": list_all_vendors_xero,
    "netsuite": list_all_vendors_netsuite,
    "sap": list_all_vendors_sap,
}

# PO list fetchers. NetSuite and SAP will 404 until their PO listers
# are built — they return empty so the sync path is safe to invoke.
_PO_LIST_FETCHERS = {
    "quickbooks": list_all_purchase_orders_quickbooks,
    "xero": list_all_purchase_orders_xero,
}


async def sync_purchase_orders_from_erp(organization_id: str) -> Dict[str, Any]:
    """Pull all open POs from the org's ERP and upsert to our DB.

    Returns a summary dict so the background scheduler can log what
    happened. Safe to call repeatedly — save_purchase_order is an upsert
    keyed on po_id, so re-runs are idempotent.

    Skips orgs that don't have an ERP connected and ERPs without a
    PO fetcher wired. Never raises — errors are logged and returned
    in the summary so the background loop stays healthy.
    """
    summary: Dict[str, Any] = {
        "organization_id": organization_id,
        "erp_type": None,
        "pos_fetched": 0,
        "pos_upserted": 0,
        "errors": [],
    }
    try:
        connection = get_erp_connection(organization_id)
        if not connection:
            summary["errors"].append("no_erp_connected")
            return summary
        summary["erp_type"] = connection.type
        fetcher = _PO_LIST_FETCHERS.get(connection.type)
        if not fetcher:
            summary["errors"].append(f"no_po_fetcher_for_{connection.type}")
            return summary
        po_rows = await fetcher(connection) or []
        summary["pos_fetched"] = len(po_rows)
        db = _get_db()
        for row in po_rows:
            # Upsert. Attach organization_id so the store row is
            # tenant-scoped; the fetchers don't know the org.
            row["organization_id"] = organization_id
            try:
                db.save_purchase_order(row)
                summary["pos_upserted"] += 1
            except Exception as exc:
                summary["errors"].append(f"save_failed:{row.get('po_id')}:{exc}")
    except Exception as exc:
        logger.warning(
            "[sync_purchase_orders_from_erp] org=%s failed: %s",
            organization_id, exc,
        )
        summary["errors"].append(str(exc))
    return summary


# Cache TTL: 24 hours
_VENDOR_LIST_CACHE_TTL_SECONDS = 24 * 60 * 60


def _get_cached_vendor_list(
    organization_id: str,
) -> Optional[Dict[str, Any]]:
    """Return cached vendor list from org settings_json, or None if stale/missing."""
    import json as _json

    try:
        db = _get_db()
        org = db.get_organization(organization_id)
        if not org:
            return None
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                return None
        cache = settings.get("vendor_list_cache")
        if not isinstance(cache, dict):
            return None
        fetched_at = cache.get("fetched_at")
        if not fetched_at:
            return None
        try:
            fetched_dt = datetime.fromisoformat(fetched_at)
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if age_seconds > _VENDOR_LIST_CACHE_TTL_SECONDS:
                return None
        except (ValueError, TypeError):
            return None
        return cache
    except Exception:
        return None


def _save_vendor_list_cache(
    organization_id: str,
    vendors: List[Dict[str, Any]],
    erp_type: str,
) -> None:
    """Store vendor list in org settings_json for caching."""
    import json as _json

    try:
        db = _get_db()
        org = db.get_organization(organization_id)
        if not org:
            return
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        if not isinstance(settings, dict):
            settings = {}

        settings["vendor_list_cache"] = {
            "vendors": vendors,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "erp_type": erp_type,
            "vendor_count": len(vendors),
        }

        db.update_organization(organization_id, settings_json=settings)
    except Exception as exc:
        logger.warning("Failed to cache vendor list for org %s: %s", organization_id, exc)


async def list_all_vendors(
    organization_id: str,
    entity_id: Optional[str] = None,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch full vendor directory from the connected ERP.

    Results are cached in organization settings for 24h.
    Pass force_refresh=True to bypass cache.

    Returns an empty list on any error so the caller is never blocked.
    """
    org_id = str(organization_id or "").strip() or "default"

    # Check cache first (unless force_refresh)
    if not force_refresh:
        cached = _get_cached_vendor_list(org_id)
        if cached is not None:
            return cached.get("vendors", [])

    # Resolve ERP connection
    connection = get_erp_connection(org_id, entity_id=entity_id)
    if not connection:
        logger.debug("No ERP connection for org %s, returning empty vendor list", org_id)
        return []

    # §11.1: Rate-limit check — return empty list so caller falls back to cached/empty path
    _rate_limited = _enforce_erp_rate_limit(org_id, connection.type)
    if _rate_limited:
        logger.warning("Vendor list fetch rate-limited for org %s", org_id)
        return []

    erp_type = str(connection.type or "").strip().lower()
    fetcher = _VENDOR_LIST_FETCHERS.get(erp_type)
    if not fetcher:
        logger.warning("No vendor-list fetcher for ERP type: %s", erp_type)
        return []

    try:
        vendors = await fetcher(connection)
    except Exception as exc:
        logger.error("Vendor list fetch failed for org %s (%s): %s", org_id, erp_type, exc)
        return []

    # Cache the result
    if vendors:
        _save_vendor_list_cache(org_id, vendors, erp_type)

    return vendors
