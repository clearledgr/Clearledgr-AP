"""Smaller support routes extracted from the Gmail extension adapter."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.api.gmail_extension_common import resolve_org_id_for_user
from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.gmail_extension_support import (
    build_amount_validation_payload,
    build_form_prefill_payload,
    build_gl_suggestion_payload,
    build_needs_info_draft_payload,
    build_vendor_suggestion_payload,
)


router = APIRouter()


@router.get("/health")
def extension_health():
    return {
        "status": "ok",
        "service": "clearledgr-gmail-extension",
        "differentiators": [
            "audit_link_generation",
            "human_in_the_loop",
            "multi_system_routing",
        ],
    }


class GLSuggestionRequest(BaseModel):
    vendor_name: str
    amount: Optional[float] = None
    description: Optional[str] = None
    organization_id: Optional[str] = "default"


class VendorSuggestionRequest(BaseModel):
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    extracted_vendor: Optional[str] = None
    organization_id: Optional[str] = "default"


@router.post("/suggestions/gl-code")
async def suggest_gl_code(
    request: GLSuggestionRequest,
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, request.organization_id)
    return build_gl_suggestion_payload(
        organization_id=org_id,
        vendor_name=request.vendor_name,
    )


@router.post("/suggestions/vendor")
async def suggest_vendor(
    request: VendorSuggestionRequest,
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, request.organization_id)
    return build_vendor_suggestion_payload(
        organization_id=org_id,
        sender_email=request.sender_email,
        extracted_vendor=request.extracted_vendor,
    )


@router.post("/suggestions/amount-validation")
async def validate_amount(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    organization_id: str = Body("default"),
    _user=Depends(get_current_user),
):
    resolve_org_id_for_user(_user, organization_id)
    return build_amount_validation_payload(vendor_name, amount)


@router.get("/suggestions/form-prefill/{email_id}")
async def get_form_prefill(
    email_id: str,
    organization_id: str = "default",
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, organization_id)
    db = get_db()
    invoice = db.get_invoice_by_email_id(email_id)
    try:
        return build_form_prefill_payload(
            email_id=email_id,
            organization_id=org_id,
            invoice=invoice,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="org_mismatch")


class SidebarQueryRequest(BaseModel):
    """Natural-language question from the Gmail thread sidebar about the
    current invoice / vendor. DESIGN_THESIS.md §6.8 specifies this for
    Slack — we reuse the same agent layer for the sidebar so the answer
    format and grounding are identical across decision surfaces.
    """
    query: str
    ap_item_id: Optional[str] = None
    organization_id: Optional[str] = "default"


@router.post("/sidebar/query")
async def answer_sidebar_query(
    request: SidebarQueryRequest,
    _user=Depends(get_current_user),
):
    """Answer a conversational query posed from the thread sidebar.

    Scope: the user is on a Gmail thread tied to one AP item. We pull
    the invoice itself, the vendor's recent history (for "what else is
    open from this vendor" style questions), and the invoice's audit
    timeline (for "why is this stuck" style questions). That bundle is
    handed to the existing Claude conversational layer
    (`_answer_query_with_context`), which is already battle-tested via
    the Slack query surface.
    """
    query = str(request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="empty_query")
    if len(query) > 1000:
        raise HTTPException(status_code=413, detail="query_too_long")

    org_id = resolve_org_id_for_user(_user, request.organization_id)
    db = get_db()

    # Assemble context for the one-invoice scope the sidebar cares about.
    focus_item = None
    if request.ap_item_id:
        try:
            focus_item = db.get_ap_item(request.ap_item_id)
        except Exception:  # noqa: BLE001 — best-effort context load
            focus_item = None
        if focus_item and str(focus_item.get("organization_id") or org_id) != org_id:
            raise HTTPException(status_code=403, detail="org_mismatch")

    # Include the focus item + up to 9 of the same vendor's recent items
    # so the agent can answer "what else is open from X?" correctly.
    items: list = []
    if focus_item:
        items.append(focus_item)
        vendor_name = str(focus_item.get("vendor_name") or "").strip()
        if vendor_name:
            try:
                vendor_items = db.list_ap_items(
                    organization_id=org_id,
                    limit=10,
                ) or []
            except TypeError:
                # Older store signature — fall back to no vendor context.
                vendor_items = []
            except Exception:  # noqa: BLE001
                vendor_items = []
            focus_id = str(focus_item.get("id") or "")
            for vi in vendor_items:
                if str(vi.get("id") or "") == focus_id:
                    continue
                if str(vi.get("vendor_name") or "").strip().lower() == vendor_name.lower():
                    items.append(vi)
                if len(items) >= 10:
                    break

    # Audit timeline for the focus item only. 30 events is what the
    # Slack path uses too.
    audit_events: list = []
    if focus_item and focus_item.get("id"):
        try:
            audit_events = db.list_ap_audit_events(
                ap_item_id=str(focus_item.get("id")),
                limit=30,
                order="desc",
            ) or []
        except TypeError:
            # Older signature — try kwargs-less call.
            try:
                audit_events = db.list_ap_audit_events(str(focus_item.get("id"))) or []
            except Exception:  # noqa: BLE001
                audit_events = []
        except Exception:  # noqa: BLE001
            audit_events = []

    # Reuse the Slack conversational layer — same system prompt, same
    # grounding, same Claude action registration. One agent voice across
    # decision surfaces (§6.8 thesis intent).
    try:
        from clearledgr.api.slack_invoices import _answer_query_with_context
        answer = await _answer_query_with_context(
            query=query,
            items=items,
            org_id=org_id,
            audit_events=audit_events,
        )
    except Exception as exc:  # noqa: BLE001
        # Last-resort fallback so the sidebar always gets a response.
        try:
            from clearledgr.api.slack_invoices import _answer_query_rule_based
            answer = _answer_query_rule_based(query, items)
        except Exception:  # noqa: BLE001
            answer = "I couldn't answer that right now. Please try again."

    return {
        "answer": str(answer or "").strip() or "I couldn't find an answer for that question.",
        "context": {
            "ap_item_id": str(focus_item.get("id")) if focus_item else None,
            "vendor": str(focus_item.get("vendor_name")) if focus_item else None,
            "item_count": len(items),
            "audit_event_count": len(audit_events),
        },
    }


@router.get("/needs-info-draft/{ap_item_id}")
async def get_needs_info_draft(
    ap_item_id: str,
    reason: Optional[str] = Query(None, description="What information is needed — pre-fills the email body"),
    _user=Depends(get_current_user),
):
    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    try:
        return build_needs_info_draft_payload(
            ap_item_id=ap_item_id,
            ap_item=ap_item,
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
