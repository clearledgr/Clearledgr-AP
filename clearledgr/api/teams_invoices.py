"""Teams interactive handlers for AP invoice approvals."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow


router = APIRouter(prefix="/teams/invoices", tags=["teams-invoices"])


def _parse_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _lookup_ap_item_id(organization_id: str, email_id: str, invoice_number: Optional[str] = None) -> Optional[str]:
    db = get_db()
    row = db.get_ap_item_by_thread(organization_id, email_id) if hasattr(db, "get_ap_item_by_thread") else None
    if row and row.get("id"):
        return str(row.get("id"))
    if invoice_number and hasattr(db, "get_ap_item_by_vendor_invoice"):
        # Vendor is optional here; teams callbacks often only carry email_id.
        return None
    return None


def _upsert_teams_metadata(
    organization_id: str,
    email_id: str,
    *,
    conversation_id: Optional[str],
    message_id: Optional[str],
    actor: str,
    action: str,
    status: str,
    reason: Optional[str] = None,
) -> None:
    db = get_db()
    row = db.get_ap_item_by_thread(organization_id, email_id) if hasattr(db, "get_ap_item_by_thread") else None
    if not row:
        return
    ap_item_id = str(row.get("id") or "")
    if not ap_item_id:
        return
    try:
        metadata_raw = row.get("metadata")
        if isinstance(metadata_raw, dict):
            metadata = dict(metadata_raw)
        elif isinstance(metadata_raw, str) and metadata_raw.strip():
            metadata = json.loads(metadata_raw)
        else:
            metadata = {}
    except Exception:
        metadata = {}
    metadata["teams"] = {
        "channel": conversation_id,
        "message_id": message_id,
        "state": status,
        "last_action": action,
        "updated_by": actor,
        "reason": reason,
    }
    db.update_ap_item(ap_item_id, metadata=metadata)


@router.post("/interactive")
async def handle_teams_interactive(request: Request) -> Dict[str, Any]:
    """Handle Teams approval/budget actions for AP invoices."""
    payload = _parse_payload(await request.json())
    if not payload:
        raise HTTPException(status_code=400, detail="invalid_payload")

    action = str(payload.get("action") or "").strip().lower()
    email_id = str(payload.get("email_id") or payload.get("gmail_id") or "").strip()
    if not email_id:
        raise HTTPException(status_code=400, detail="email_id_required")

    organization_id = str(payload.get("organization_id") or "default")
    actor = str(payload.get("actor") or payload.get("user_email") or "teams_user")
    conversation_id = (
        str(payload.get("conversation_id") or payload.get("channel_id") or "").strip() or None
    )
    message_id = str(payload.get("message_id") or payload.get("activity_id") or "").strip() or None
    justification = str(payload.get("justification") or "").strip()

    workflow = get_invoice_workflow(organization_id)
    kwargs = {
        "source_channel": "teams",
        "source_channel_id": conversation_id,
        "source_message_ref": message_id,
    }

    if action in {"approve_invoice", "post_to_erp"}:
        result = await workflow.approve_invoice(
            gmail_id=email_id,
            approved_by=actor,
            **kwargs,
        )
    elif action in {"approve_budget_override", "approve_override"}:
        if not justification:
            justification = "Approved over budget in Teams"
        result = await workflow.approve_invoice(
            gmail_id=email_id,
            approved_by=actor,
            allow_budget_override=True,
            override_justification=justification,
            **kwargs,
        )
    elif action in {"request_budget_adjustment", "request_adjustment"}:
        result = await workflow.request_budget_adjustment(
            gmail_id=email_id,
            requested_by=actor,
            reason=justification or "budget_adjustment_requested_in_teams",
            **kwargs,
        )
    elif action in {"reject_invoice", "reject_budget"}:
        result = await workflow.reject_invoice(
            gmail_id=email_id,
            reason=justification or ("rejected_over_budget_in_teams" if action == "reject_budget" else "rejected_in_teams"),
            rejected_by=actor,
            **kwargs,
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported_action")

    _upsert_teams_metadata(
        organization_id,
        email_id,
        conversation_id=conversation_id,
        message_id=message_id,
        actor=actor,
        action=action,
        status=str(result.get("status") or "unknown"),
        reason=str(result.get("reason") or ""),
    )

    return {
        "status": result.get("status"),
        "action": action,
        "email_id": email_id,
        "result": result,
    }
