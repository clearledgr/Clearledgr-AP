"""Vendor self-service portal — external API for vendors to check payment status.

Vendors access this via a unique link sent in communication emails.
No login required — link contains a signed token with vendor + org context.

Endpoints:
- GET /vendor-portal/status — payment status for vendor's invoices
- GET /vendor-portal/invoices — list of invoices from this vendor
- POST /vendor-portal/bank-details — submit updated bank details (queued for review)
- POST /vendor-portal/documents — submit documents (queued for review)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vendor-portal", tags=["vendor-portal"])

# Token secret for vendor portal links
_PORTAL_SECRET = os.getenv("VENDOR_PORTAL_SECRET", "")


def _get_portal_secret() -> str:
    secret = _PORTAL_SECRET or os.getenv("VENDOR_PORTAL_SECRET", "")
    if not secret:
        from clearledgr.core.secrets import require_secret
        secret = require_secret("CLEARLEDGR_SECRET_KEY")
    return secret


def generate_vendor_portal_token(
    organization_id: str,
    vendor_name: str,
    expires_hours: int = 720,  # 30 days
) -> str:
    """Generate a signed token for vendor portal access."""
    import time
    expires_at = int(time.time()) + (expires_hours * 3600)
    payload = f"{organization_id}:{vendor_name}:{expires_at}"
    sig = hmac.new(
        _get_portal_secret().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    import base64
    token = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()
    return token


def _validate_portal_token(token: str) -> Dict[str, str]:
    """Validate and decode a vendor portal token."""
    import base64, time
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        parts = decoded.rsplit(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid token format")
        payload, sig = parts[0], parts[1]
        expected_sig = hmac.new(
            _get_portal_secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid signature")

        org_id, vendor_name, expires_at = payload.split(":", 2)
        if int(expires_at) < int(time.time()):
            raise ValueError("Token expired")

        return {"organization_id": org_id, "vendor_name": vendor_name}
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid portal token: {exc}")


@router.get("/status")
def vendor_payment_status(
    token: str = Query(..., description="Vendor portal access token"),
) -> Dict[str, Any]:
    """Check payment status for this vendor's invoices."""
    ctx = _validate_portal_token(token)
    from clearledgr.core.database import get_db

    db = get_db()
    items = db.get_ap_items_by_vendor(
        ctx["organization_id"], ctx["vendor_name"], days=180, limit=20,
    )

    invoices = []
    for item in items:
        state = item.get("state", "unknown")
        payment_status = "processing"
        if state in ("posted_to_erp", "closed"):
            payment_status = "paid" if state == "closed" else "approved_pending_payment"
        elif state in ("needs_approval", "approved", "ready_to_post"):
            payment_status = "under_review"
        elif state == "rejected":
            payment_status = "rejected"
        elif state == "needs_info":
            payment_status = "information_requested"

        invoices.append({
            "invoice_number": item.get("invoice_number"),
            "amount": item.get("amount"),
            "currency": item.get("currency", "USD"),
            "due_date": item.get("due_date"),
            "status": payment_status,
            "received_at": item.get("created_at"),
        })

    return {
        "vendor_name": ctx["vendor_name"],
        "invoices": invoices,
        "count": len(invoices),
    }


@router.get("/invoices")
def vendor_invoice_list(
    token: str = Query(..., description="Vendor portal access token"),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    """List invoices from this vendor."""
    ctx = _validate_portal_token(token)
    from clearledgr.core.database import get_db

    db = get_db()
    items = db.get_ap_items_by_vendor(
        ctx["organization_id"], ctx["vendor_name"], days=365, limit=limit,
    )

    return {
        "vendor_name": ctx["vendor_name"],
        "invoices": [
            {
                "invoice_number": item.get("invoice_number"),
                "amount": item.get("amount"),
                "currency": item.get("currency", "USD"),
                "due_date": item.get("due_date"),
                "state": item.get("state"),
                "created_at": item.get("created_at"),
                "erp_reference": item.get("erp_reference"),
            }
            for item in items
        ],
        "count": len(items),
    }


@router.post("/bank-details")
def submit_bank_details(
    token: str = Query(..., description="Vendor portal access token"),
    body: dict = {},
) -> Dict[str, Any]:
    """Submit updated bank details (queued for AP team review, not auto-applied)."""
    ctx = _validate_portal_token(token)
    from clearledgr.core.database import get_db

    db = get_db()
    # Store as a vendor communication/dispute for AP team review
    try:
        from clearledgr.services.dispute_service import get_dispute_service
        svc = get_dispute_service(ctx["organization_id"])

        # Find the most recent AP item for this vendor
        items = db.get_ap_items_by_vendor(ctx["organization_id"], ctx["vendor_name"], days=90, limit=1)
        ap_item_id = items[0]["id"] if items else "vendor_portal_submission"

        dispute = svc.open_dispute(
            ap_item_id=ap_item_id,
            dispute_type="bank_detail_change",
            description=f"Vendor submitted updated bank details via portal: {body.get('bank_name', 'N/A')}",
            vendor_name=ctx["vendor_name"],
            vendor_email=body.get("contact_email", ""),
        )

        logger.info(
            "[VendorPortal] Bank detail submission from %s (org=%s)",
            ctx["vendor_name"], ctx["organization_id"],
        )

        return {
            "status": "submitted_for_review",
            "reference": dispute["id"],
            "message": "Bank details have been submitted and will be reviewed by the AP team.",
        }
    except Exception as exc:
        logger.error("[VendorPortal] Bank detail submission failed: %s", exc)
        return {"status": "error", "message": "Submission failed. Please contact the AP team directly."}
