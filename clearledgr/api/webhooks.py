"""
Webhook Endpoints

Receives real-time events from:

Payment Gateways:
- Stripe (payouts, charges)
- Paystack (settlements, transfers)
- Flutterwave (transfers)

ERPs:
- QuickBooks (accounts, invoices)
- Xero (contacts, invoices, bank transactions)
- NetSuite (GL accounts, journal entries, vendor bills)
- SAP (GL accounts, journal entries, invoices)

Open Banking (by region):
- Plaid (US/Canada)
- TrueLayer (UK)
- Tink (EU)
- Africa: No open banking - use Gmail for bank statements

When webhook fires → Event bus → Autonomous processing → Real-time sync to surfaces
"""

import logging
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, Header

from clearledgr.core.event_bus import get_event_bus, Event, EventType
from clearledgr.services.realtime_sync import (
    notify_gateway_settled,
    notify_exception_created,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ==================== STRIPE ====================

@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
):
    """
    Receive Stripe webhooks.
    
    Events we care about:
    - payout.paid: Settlement hit the bank
    - payout.failed: Settlement failed
    - charge.succeeded: Customer payment successful
    - charge.refunded: Refund processed
    """
    import os
    
    body = await request.body()
    
    # Verify signature
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if webhook_secret and stripe_signature:
        if not verify_stripe_signature(body, stripe_signature, webhook_secret):
            raise HTTPException(status_code=400, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("type", "")
    data = payload.get("data", {}).get("object", {})
    
    logger.info(f"Stripe webhook: {event_type}")
    
    # Map to our events
    if event_type == "payout.paid":
        await handle_stripe_payout(data)
    elif event_type == "payout.failed":
        await handle_stripe_payout_failed(data)
    elif event_type == "charge.succeeded":
        await handle_stripe_charge(data)
    
    return {"status": "ok"}


def verify_stripe_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Stripe webhook signature."""
    try:
        import time
        
        # Parse signature
        elements = dict(item.split("=") for item in signature.split(","))
        timestamp = elements.get("t", "")
        expected_sig = elements.get("v1", "")
        
        if not timestamp or not expected_sig:
            return False
        
        # Check timestamp (within 5 minutes)
        if abs(time.time() - int(timestamp)) > 300:
            return False
        
        # Compute expected signature
        signed_payload = f"{timestamp}.{payload.decode()}"
        computed_sig = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        return hmac.compare_digest(computed_sig, expected_sig)
    except Exception:
        return False


async def handle_stripe_payout(data: Dict[str, Any]):
    """Handle Stripe payout.paid event."""
    bus = get_event_bus()
    org_id = data.get("metadata", {}).get("organization_id", "default")
    amount = data.get("amount", 0) / 100  # Stripe uses cents
    currency = (data.get("currency") or "EUR").upper()
    
    await bus.publish(Event(
        type=EventType.GATEWAY_WEBHOOK_RECEIVED,
        data={
            "gateway": "stripe",
            "type": "payout",
            "amount": amount,
            "currency": currency,
            "date": datetime.fromtimestamp(data.get("arrival_date", 0)).strftime("%Y-%m-%d"),
            "reference": data.get("id"),
            "description": f"Stripe payout {data.get('id')}",
            "status": "completed",
        },
        organization_id=org_id,
    ))
    
    # Notify all surfaces (Slack, Sheets, etc.)
    await notify_gateway_settled(org_id, "stripe", amount, data.get("id"), currency)


async def handle_stripe_payout_failed(data: Dict[str, Any]):
    """Handle Stripe payout.failed event."""
    bus = get_event_bus()
    
    await bus.publish(Event(
        type=EventType.EXCEPTION_DETECTED,
        data={
            "type": "payout_failed",
            "gateway": "stripe",
            "amount": data.get("amount", 0) / 100,
            "reference": data.get("id"),
            "reason": data.get("failure_message", "Unknown"),
            "priority": "high",
        },
        organization_id=data.get("metadata", {}).get("organization_id", "default"),
    ))


async def handle_stripe_charge(data: Dict[str, Any]):
    """Handle Stripe charge.succeeded event."""
    bus = get_event_bus()
    
    await bus.publish(Event(
        type=EventType.GATEWAY_TRANSACTION_RECEIVED,
        data={
            "gateway": "stripe",
            "type": "charge",
            "amount": data.get("amount", 0) / 100,
            "currency": (data.get("currency") or "EUR").upper(),
            "date": datetime.fromtimestamp(data.get("created", 0)).strftime("%Y-%m-%d"),
            "reference": data.get("id"),
            "description": data.get("description") or f"Stripe charge {data.get('id')}",
            "customer_email": data.get("billing_details", {}).get("email"),
            "fee": (data.get("balance_transaction", {}).get("fee", 0) or 0) / 100,
        },
        organization_id=data.get("metadata", {}).get("organization_id", "default"),
    ))


# ==================== PAYSTACK ====================

@router.post("/paystack")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: Optional[str] = Header(None, alias="X-Paystack-Signature"),
):
    """
    Receive Paystack webhooks.
    
    Events we care about:
    - charge.success: Customer payment successful
    - transfer.success: Settlement to bank
    - transfer.failed: Settlement failed
    """
    import os
    
    body = await request.body()
    
    # Verify signature
    secret_key = os.getenv("PAYSTACK_SECRET_KEY")
    if secret_key and x_paystack_signature:
        expected = hmac.new(
            secret_key.encode(),
            body,
            hashlib.sha512,
        ).hexdigest()
        
        if not hmac.compare_digest(expected, x_paystack_signature):
            raise HTTPException(status_code=400, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("event", "")
    data = payload.get("data", {})
    
    logger.info(f"Paystack webhook: {event_type}")
    
    if event_type == "charge.success":
        await handle_paystack_charge(data)
    elif event_type == "transfer.success":
        await handle_paystack_transfer(data)
    elif event_type == "transfer.failed":
        await handle_paystack_transfer_failed(data)
    
    return {"status": "ok"}


async def handle_paystack_charge(data: Dict[str, Any]):
    """Handle Paystack charge.success event."""
    bus = get_event_bus()
    
    # Paystack amounts are in kobo (1/100 of currency)
    amount = data.get("amount", 0) / 100
    
    await bus.publish(Event(
        type=EventType.GATEWAY_TRANSACTION_RECEIVED,
        data={
            "gateway": "paystack",
            "type": "charge",
            "amount": amount,
            "currency": data.get("currency", "NGN"),
            "date": data.get("paid_at", "")[:10] if data.get("paid_at") else datetime.now().strftime("%Y-%m-%d"),
            "reference": data.get("reference"),
            "description": f"Payment from {data.get('customer', {}).get('email', 'customer')}",
            "customer_email": data.get("customer", {}).get("email"),
            "fee": data.get("fees", 0) / 100,
        },
        organization_id=data.get("metadata", {}).get("organization_id", "default"),
    ))


async def handle_paystack_transfer(data: Dict[str, Any]):
    """Handle Paystack transfer.success (settlement) event."""
    bus = get_event_bus()
    
    amount = data.get("amount", 0) / 100
    org_id = data.get("metadata", {}).get("organization_id", "default")
    currency = data.get("currency", "NGN")
    
    await bus.publish(Event(
        type=EventType.GATEWAY_WEBHOOK_RECEIVED,
        data={
            "gateway": "paystack",
            "type": "settlement",
            "amount": amount,
            "currency": currency,
            "date": data.get("transferred_at", "")[:10] if data.get("transferred_at") else datetime.now().strftime("%Y-%m-%d"),
            "reference": data.get("transfer_code"),
            "description": f"Paystack settlement {data.get('transfer_code')}",
            "status": "completed",
        },
        organization_id=org_id,
    ))
    
    # Notify all surfaces
    await notify_gateway_settled(org_id, "paystack", amount, data.get("transfer_code"), currency)


async def handle_paystack_transfer_failed(data: Dict[str, Any]):
    """Handle Paystack transfer.failed event."""
    bus = get_event_bus()
    
    await bus.publish(Event(
        type=EventType.EXCEPTION_DETECTED,
        data={
            "type": "settlement_failed",
            "gateway": "paystack",
            "amount": data.get("amount", 0) / 100,
            "reference": data.get("transfer_code"),
            "reason": data.get("reason", "Unknown"),
            "priority": "high",
        },
        organization_id=data.get("metadata", {}).get("organization_id", "default"),
    ))


# ==================== FLUTTERWAVE ====================

@router.post("/flutterwave")
async def flutterwave_webhook(
    request: Request,
    verif_hash: Optional[str] = Header(None, alias="verif-hash"),
):
    """
    Receive Flutterwave webhooks.
    
    Events we care about:
    - charge.completed: Payment received
    - transfer.completed: Settlement to bank
    """
    import os
    
    # Verify hash
    secret_hash = os.getenv("FLUTTERWAVE_SECRET_HASH")
    if secret_hash and verif_hash:
        if verif_hash != secret_hash:
            raise HTTPException(status_code=400, detail="Invalid verification hash")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("event", "")
    data = payload.get("data", {})
    
    logger.info(f"Flutterwave webhook: {event_type}")
    
    if event_type == "charge.completed":
        await handle_flutterwave_charge(data)
    elif event_type == "transfer.completed":
        await handle_flutterwave_transfer(data)
    
    return {"status": "ok"}


async def handle_flutterwave_charge(data: Dict[str, Any]):
    """Handle Flutterwave charge.completed event."""
    bus = get_event_bus()
    
    await bus.publish(Event(
        type=EventType.GATEWAY_TRANSACTION_RECEIVED,
        data={
            "gateway": "flutterwave",
            "type": "charge",
            "amount": data.get("amount", 0),
            "currency": data.get("currency", "NGN"),
            "date": data.get("created_at", "")[:10] if data.get("created_at") else datetime.now().strftime("%Y-%m-%d"),
            "reference": data.get("flw_ref") or data.get("tx_ref"),
            "description": data.get("narration") or f"Flutterwave payment {data.get('id')}",
            "customer_email": data.get("customer", {}).get("email"),
            "fee": data.get("app_fee", 0),
        },
        organization_id=data.get("meta", {}).get("organization_id", "default"),
    ))


async def handle_flutterwave_transfer(data: Dict[str, Any]):
    """Handle Flutterwave transfer.completed event."""
    bus = get_event_bus()
    
    amount = data.get("amount", 0)
    org_id = data.get("meta", {}).get("organization_id", "default")
    currency = data.get("currency", "NGN")
    
    await bus.publish(Event(
        type=EventType.GATEWAY_WEBHOOK_RECEIVED,
        data={
            "gateway": "flutterwave",
            "type": "transfer",
            "amount": amount,
            "currency": currency,
            "date": data.get("created_at", "")[:10] if data.get("created_at") else datetime.now().strftime("%Y-%m-%d"),
            "reference": data.get("reference"),
            "description": f"Flutterwave transfer {data.get('id')}",
            "status": "completed",
        },
        organization_id=org_id,
    ))
    
    # Notify all surfaces
    await notify_gateway_settled(org_id, "flutterwave", amount, data.get("reference"), currency)


# ==================== QUICKBOOKS ====================

@router.post("/quickbooks")
async def quickbooks_webhook(request: Request):
    """
    Receive QuickBooks webhooks.
    
    Events we care about:
    - Account: GL account created/updated/deleted
    - Invoice: Invoice created/updated
    - JournalEntry: JE created/updated
    - BankTransaction: Bank feed transaction
    """
    import os
    
    # QuickBooks sends a verification request first
    verifier_token = os.getenv("QUICKBOOKS_WEBHOOK_TOKEN")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # Handle verification challenge
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    
    event_notifications = payload.get("eventNotifications", [])
    
    for notification in event_notifications:
        realm_id = notification.get("realmId")
        data_change_event = notification.get("dataChangeEvent", {})
        entities = data_change_event.get("entities", [])
        
        for entity in entities:
            entity_name = entity.get("name")
            entity_id = entity.get("id")
            operation = entity.get("operation")
            
            logger.info(f"QuickBooks webhook: {entity_name} {operation} ({entity_id})")
            
            await handle_quickbooks_entity(realm_id, entity_name, entity_id, operation)
    
    return {"status": "ok"}


async def handle_quickbooks_entity(
    realm_id: str,
    entity_name: str,
    entity_id: str,
    operation: str,
):
    """Handle QuickBooks entity change."""
    bus = get_event_bus()
    
    if entity_name == "Account" and operation in ["Create", "Update"]:
        # GL account changed - sync mappings
        await bus.publish(Event(
            type=EventType.ERP_GL_UPDATED,
            data={
                "erp": "quickbooks",
                "entity": "account",
                "entity_id": entity_id,
                "operation": operation.lower(),
            },
            organization_id=realm_id,
        ))
    
    elif entity_name == "JournalEntry" and operation == "Create":
        # New JE created (possibly from Clearledgr)
        await bus.publish(Event(
            type=EventType.ERP_JE_POSTED,
            data={
                "erp": "quickbooks",
                "entity_id": entity_id,
            },
            organization_id=realm_id,
        ))
    
    elif entity_name == "BankTransaction" and operation == "Create":
        # New bank transaction from bank feed
        await bus.publish(Event(
            type=EventType.BANK_TRANSACTION_RECEIVED,
            data={
                "source": "quickbooks_bank_feed",
                "transaction_id": entity_id,
            },
            organization_id=realm_id,
        ))


# ==================== XERO ====================

@router.post("/xero")
async def xero_webhook(
    request: Request,
    x_xero_signature: Optional[str] = Header(None, alias="x-xero-signature"),
):
    """
    Receive Xero webhooks.
    
    Events we care about:
    - INVOICE.CREATE/UPDATE
    - BANKSTATEMENT.CREATE
    - ACCOUNT.CREATE/UPDATE
    """
    import os
    import base64
    
    body = await request.body()
    
    # Verify signature
    webhook_key = os.getenv("XERO_WEBHOOK_KEY")
    if webhook_key and x_xero_signature:
        expected = base64.b64encode(
            hmac.new(
                webhook_key.encode(),
                body,
                hashlib.sha256,
            ).digest()
        ).decode()
        
        if not hmac.compare_digest(expected, x_xero_signature):
            # Xero sends a challenge - respond with empty 401 to register
            raise HTTPException(status_code=401)
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    events = payload.get("events", [])
    
    for event in events:
        tenant_id = event.get("tenantId")
        event_type = event.get("eventType")
        resource_id = event.get("resourceId")
        
        logger.info(f"Xero webhook: {event_type} ({resource_id})")
        
        await handle_xero_event(tenant_id, event_type, resource_id)
    
    return {"status": "ok"}


async def handle_xero_event(tenant_id: str, event_type: str, resource_id: str):
    """Handle Xero webhook event."""
    bus = get_event_bus()
    
    if event_type.startswith("INVOICE"):
        await bus.publish(Event(
            type=EventType.ERP_INVOICE_RECEIVED,
            data={
                "erp": "xero",
                "invoice_id": resource_id,
                "operation": event_type.split(".")[1].lower(),
            },
            organization_id=tenant_id,
        ))
    
    elif event_type.startswith("BANKSTATEMENT"):
        await bus.publish(Event(
            type=EventType.BANK_STATEMENT_RECEIVED,
            data={
                "source": "xero_bank_feed",
                "statement_id": resource_id,
            },
            organization_id=tenant_id,
        ))
    
    elif event_type.startswith("ACCOUNT"):
        await bus.publish(Event(
            type=EventType.ERP_GL_UPDATED,
            data={
                "erp": "xero",
                "entity": "account",
                "entity_id": resource_id,
                "operation": event_type.split(".")[1].lower(),
            },
            organization_id=tenant_id,
        ))


# ==================== TRUELAYER (UK OPEN BANKING) ====================

@router.post("/truelayer")
async def truelayer_webhook(
    request: Request,
    tl_signature: Optional[str] = Header(None, alias="tl-signature"),
):
    """
    Receive TrueLayer webhooks for UK open banking.
    
    TrueLayer is a leading UK open banking provider under PSD2.
    """
    import os
    
    # Verify signature
    secret = os.getenv("TRUELAYER_WEBHOOK_SECRET")
    if secret and tl_signature:
        body = await request.body()
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(expected, tl_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("type")
    data = payload.get("data", {})
    
    logger.info(f"TrueLayer webhook: {event_type}")
    
    if event_type == "transactions_available":
        await handle_truelayer_transactions(data)
    
    return {"status": "ok"}


async def handle_truelayer_transactions(data: Dict[str, Any]):
    """Handle TrueLayer new transactions notification."""
    bus = get_event_bus()
    
    account_id = data.get("account_id")
    
    await bus.publish(Event(
        type=EventType.BANK_TRANSACTIONS_AVAILABLE,
        data={
            "source": "truelayer",
            "account_id": account_id,
            "from_date": data.get("from"),
            "to_date": data.get("to"),
        },
        organization_id="default",
    ))


# ==================== TINK (EU OPEN BANKING) ====================

@router.post("/tink")
async def tink_webhook(
    request: Request,
    x_tink_signature: Optional[str] = Header(None, alias="x-tink-signature"),
):
    """
    Receive Tink webhooks for EU open banking.
    
    Tink (owned by Visa) is a major EU open banking provider under PSD2.
    Also covers Nordigen which merged with Tink.
    """
    import os
    
    # Verify signature
    secret = os.getenv("TINK_WEBHOOK_SECRET")
    if secret and x_tink_signature:
        body = await request.body()
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(expected, x_tink_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("event")
    context = payload.get("context", {})
    
    logger.info(f"Tink webhook: {event_type}")
    
    if event_type == "account:transactions:modified":
        await handle_tink_transactions(context)
    
    return {"status": "ok"}


async def handle_tink_transactions(context: Dict[str, Any]):
    """Handle Tink transactions modified event."""
    bus = get_event_bus()
    
    credential_id = context.get("credentialId")
    account_ids = context.get("accountIds", [])
    
    await bus.publish(Event(
        type=EventType.BANK_TRANSACTIONS_AVAILABLE,
        data={
            "source": "tink",
            "credential_id": credential_id,
            "account_ids": account_ids,
        },
        organization_id="default",
    ))


# ==================== PLAID (US/CANADA BANK FEEDS) ====================

@router.post("/plaid")
async def plaid_webhook(
    request: Request,
    plaid_verification: Optional[str] = Header(None, alias="Plaid-Verification"),
):
    """
    Receive Plaid webhooks for real-time bank transactions.
    
    Events we care about:
    - TRANSACTIONS: SYNC_UPDATES_AVAILABLE
    - TRANSACTIONS: INITIAL_UPDATE / HISTORICAL_UPDATE
    """
    import os
    
    # Plaid sends verification webhook
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")
    
    logger.info(f"Plaid webhook: {webhook_type}/{webhook_code} for item {item_id}")
    
    # Handle verification
    if webhook_type == "WEBHOOK_UPDATE_ACKNOWLEDGED":
        return {"status": "ok"}
    
    if webhook_type == "TRANSACTIONS":
        await handle_plaid_transactions(payload)
    
    return {"status": "ok"}


async def handle_plaid_transactions(payload: Dict[str, Any]):
    """Handle Plaid transaction webhook."""
    bus = get_event_bus()
    
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")
    new_transactions = payload.get("new_transactions", 0)
    removed_transactions = payload.get("removed_transactions", [])
    
    # Get organization from item_id mapping (would be stored during connection)
    # For now, use default
    org_id = "default"
    
    if webhook_code in ["SYNC_UPDATES_AVAILABLE", "INITIAL_UPDATE", "DEFAULT_UPDATE"]:
        await bus.publish(Event(
            type=EventType.BANK_TRANSACTIONS_AVAILABLE,
            data={
                "source": "plaid",
                "item_id": item_id,
                "new_count": new_transactions,
                "removed_ids": removed_transactions,
            },
            organization_id=org_id,
        ))
        
        logger.info(f"Plaid: {new_transactions} new transactions available for {item_id}")


# ==================== NETSUITE ====================

@router.post("/netsuite")
async def netsuite_webhook(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Receive NetSuite webhooks via SuiteScript RESTlet.
    
    NetSuite doesn't have native webhooks, so we create a RESTlet
    that calls our endpoint when records change.
    
    Events we care about:
    - account: GL account created/updated
    - journalentry: JE created/updated/posted
    - vendorbill: Vendor bill for matching
    - customerpayment: Payment received
    """
    import os
    
    # Verify authorization (shared secret or OAuth)
    expected_token = os.getenv("NETSUITE_WEBHOOK_TOKEN")
    if expected_token:
        if not authorization or not authorization.replace("Bearer ", "") == expected_token:
            raise HTTPException(status_code=401, detail="Invalid authorization")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    record_type = payload.get("recordType")
    record_id = payload.get("recordId")
    action = payload.get("action", "update")  # create, update, delete
    account_id = payload.get("accountId")  # NetSuite account ID
    
    logger.info(f"NetSuite webhook: {record_type} {action} ({record_id})")
    
    await handle_netsuite_event(account_id, record_type, record_id, action, payload.get("data", {}))
    
    return {"status": "ok"}


async def handle_netsuite_event(
    account_id: str,
    record_type: str,
    record_id: str,
    action: str,
    data: Dict[str, Any],
):
    """Handle NetSuite record change."""
    bus = get_event_bus()
    
    if record_type == "account" and action in ["create", "update"]:
        # GL account changed - sync mappings
        await bus.publish(Event(
            type=EventType.ERP_GL_UPDATED,
            data={
                "erp": "netsuite",
                "entity": "account",
                "entity_id": record_id,
                "operation": action,
                "account_data": data,
            },
            organization_id=account_id or "default",
        ))
    
    elif record_type == "journalentry":
        if action == "create":
            await bus.publish(Event(
                type=EventType.ERP_JE_POSTED,
                data={
                    "erp": "netsuite",
                    "entity_id": record_id,
                    "je_data": data,
                },
                organization_id=account_id or "default",
            ))
    
    elif record_type == "vendorbill" and action == "create":
        # New vendor bill - try to match to payments
        await bus.publish(Event(
            type=EventType.ERP_INVOICE_RECEIVED,
            data={
                "erp": "netsuite",
                "invoice_id": record_id,
                "invoice_type": "vendor_bill",
                "invoice_data": data,
            },
            organization_id=account_id or "default",
        ))
    
    elif record_type == "customerpayment" and action == "create":
        # Customer payment received
        await bus.publish(Event(
            type=EventType.GATEWAY_TRANSACTION_RECEIVED,
            data={
                "gateway": "netsuite",
                "type": "customer_payment",
                "reference": record_id,
                "amount": data.get("total"),
                "currency": data.get("currency", "USD"),
                "date": data.get("trandate"),
                "description": f"NetSuite payment {record_id}",
            },
            organization_id=account_id or "default",
        ))


# ==================== SAP ====================

@router.post("/sap")
async def sap_webhook(
    request: Request,
    x_sap_signature: Optional[str] = Header(None, alias="x-sap-signature"),
):
    """
    Receive SAP webhooks via SAP Event Mesh or custom ABAP.
    
    SAP can send events via:
    - SAP Event Mesh (cloud)
    - Custom ABAP program calling HTTP
    - SAP CPI (Cloud Platform Integration)
    
    Events we care about:
    - GLAccount: GL account master data change
    - JournalEntry: Accounting document posted
    - BusinessPartner: Vendor/customer master change
    """
    import os
    
    # Verify signature (HMAC-SHA256)
    secret = os.getenv("SAP_WEBHOOK_SECRET")
    if secret and x_sap_signature:
        body = await request.body()
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(expected, x_sap_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # SAP Event Mesh format
    event_type = payload.get("type") or payload.get("eventType")
    data = payload.get("data", {})
    company_code = data.get("CompanyCode") or payload.get("companyCode")
    
    logger.info(f"SAP webhook: {event_type}")
    
    await handle_sap_event(company_code, event_type, data)
    
    return {"status": "ok"}


async def handle_sap_event(
    company_code: str,
    event_type: str,
    data: Dict[str, Any],
):
    """Handle SAP event."""
    bus = get_event_bus()
    org_id = company_code or "default"
    
    # Normalize event type (SAP uses various formats)
    event_type_lower = (event_type or "").lower()
    
    if "glaccount" in event_type_lower or "glmaster" in event_type_lower:
        # GL account master data changed
        await bus.publish(Event(
            type=EventType.ERP_GL_UPDATED,
            data={
                "erp": "sap",
                "entity": "gl_account",
                "entity_id": data.get("GLAccount"),
                "company_code": company_code,
                "account_data": data,
            },
            organization_id=org_id,
        ))
    
    elif "journalentry" in event_type_lower or "accountingdocument" in event_type_lower:
        # Accounting document posted
        await bus.publish(Event(
            type=EventType.ERP_JE_POSTED,
            data={
                "erp": "sap",
                "entity_id": data.get("AccountingDocument") or data.get("DocumentNumber"),
                "fiscal_year": data.get("FiscalYear"),
                "company_code": company_code,
                "je_data": data,
            },
            organization_id=org_id,
        ))
    
    elif "invoice" in event_type_lower or "suppilerinvoice" in event_type_lower:
        # Supplier invoice received
        await bus.publish(Event(
            type=EventType.ERP_INVOICE_RECEIVED,
            data={
                "erp": "sap",
                "invoice_id": data.get("SupplierInvoice") or data.get("InvoiceDocument"),
                "invoice_type": "supplier_invoice",
                "vendor": data.get("Supplier"),
                "amount": data.get("InvoiceGrossAmount"),
                "currency": data.get("DocumentCurrency"),
                "invoice_data": data,
            },
            organization_id=org_id,
        ))
    
    elif "payment" in event_type_lower:
        # Payment document
        await bus.publish(Event(
            type=EventType.GATEWAY_TRANSACTION_RECEIVED,
            data={
                "gateway": "sap",
                "type": "payment",
                "reference": data.get("PaymentDocument"),
                "amount": data.get("AmountInTransactionCurrency"),
                "currency": data.get("TransactionCurrency", "EUR"),
                "date": data.get("PostingDate"),
                "description": f"SAP payment {data.get('PaymentDocument')}",
            },
            organization_id=org_id,
        ))
