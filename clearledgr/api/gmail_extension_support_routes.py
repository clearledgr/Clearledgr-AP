"""Smaller support routes extracted from the Gmail extension adapter."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

logger = logging.getLogger(__name__)


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


class SidebarQueryTurn(BaseModel):
    """One prior Q&A exchange. The client sends up to the last few so
    Claude has continuity ("can I override it?" needs the prior "why is
    it blocked?" turn to resolve "it")."""
    q: str
    a: str


class SidebarQueryRequest(BaseModel):
    """Natural-language question from the thread sidebar. Single-invoice
    scope, conversation memory, streaming answer. §6.8."""
    query: str
    ap_item_id: Optional[str] = None
    organization_id: Optional[str] = "default"
    history: Optional[List[SidebarQueryTurn]] = None


def _load_sidebar_context(
    request_ap_item_id: Optional[str],
    request_org_id: Optional[str],
    user,
) -> Dict[str, Any]:
    """Shared: resolve org, focus item, vendor siblings, audit timeline."""
    org_id = resolve_org_id_for_user(user, request_org_id)
    db = get_db()

    focus_item = None
    if request_ap_item_id:
        try:
            focus_item = db.get_ap_item(request_ap_item_id)
        except Exception:  # noqa: BLE001
            focus_item = None
        if focus_item and str(focus_item.get("organization_id") or org_id) != org_id:
            raise HTTPException(status_code=403, detail="org_mismatch")

    vendor_items: List[Dict[str, Any]] = []
    if focus_item and focus_item.get("vendor_name"):
        vendor_name = str(focus_item.get("vendor_name") or "").strip().lower()
        focus_id = str(focus_item.get("id") or "")
        try:
            candidates = db.list_ap_items(organization_id=org_id, limit=40) or []
        except TypeError:
            candidates = []
        except Exception:  # noqa: BLE001
            candidates = []
        for vi in candidates:
            if str(vi.get("id") or "") == focus_id:
                continue
            if str(vi.get("vendor_name") or "").strip().lower() != vendor_name:
                continue
            state = str(vi.get("state") or "").lower()
            if state in {"closed", "rejected", "posted_to_erp"}:
                continue  # only surface OPEN invoices from the vendor
            vendor_items.append(vi)
            if len(vendor_items) >= 9:
                break

    audit_events: List[Dict[str, Any]] = []
    if focus_item and focus_item.get("id"):
        try:
            audit_events = db.list_ap_audit_events(
                ap_item_id=str(focus_item.get("id")),
                limit=30,
                order="desc",
            ) or []
        except TypeError:
            try:
                audit_events = db.list_ap_audit_events(str(focus_item.get("id"))) or []
            except Exception:  # noqa: BLE001
                audit_events = []
        except Exception:  # noqa: BLE001
            audit_events = []

    return {
        "org_id": org_id,
        "focus_item": focus_item,
        "vendor_items": vendor_items,
        "audit_events": audit_events,
    }


@router.post("/sidebar/query")
async def answer_sidebar_query(
    request: SidebarQueryRequest,
    _user=Depends(get_current_user),
):
    """Non-streaming answer. Kept for clients that can't stream SSE and
    as the JSON shape for post-answer rendering (references list)."""
    query = str(request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="empty_query")
    if len(query) > 1000:
        raise HTTPException(status_code=413, detail="query_too_long")

    ctx = _load_sidebar_context(request.ap_item_id, request.organization_id, _user)
    history = [(t.q, t.a) for t in (request.history or [])][-6:]  # cap at last 3 turns

    answer = await _answer_sidebar_query(
        query=query,
        focus_item=ctx["focus_item"],
        vendor_items=ctx["vendor_items"],
        audit_events=ctx["audit_events"],
        org_id=ctx["org_id"],
        history=history,
    )

    references = _extract_references(answer, ctx["audit_events"])

    return {
        "answer": str(answer or "").strip() or "I couldn't find an answer for that question.",
        "references": references,
        "context": {
            "ap_item_id": str(ctx["focus_item"].get("id")) if ctx["focus_item"] else None,
            "vendor": str(ctx["focus_item"].get("vendor_name")) if ctx["focus_item"] else None,
            "vendor_item_count": len(ctx["vendor_items"]),
            "audit_event_count": len(ctx["audit_events"]),
        },
    }


@router.post("/sidebar/query/stream")
async def stream_sidebar_query(
    request: SidebarQueryRequest,
    _user=Depends(get_current_user),
):
    """Server-Sent Events streaming answer. Falls back to emitting the
    full rule-based answer as one event if Claude / credits aren't
    available. Frontend consumes via EventSource or fetch+reader."""
    from fastapi.responses import StreamingResponse

    query = str(request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="empty_query")
    if len(query) > 1000:
        raise HTTPException(status_code=413, detail="query_too_long")

    ctx = _load_sidebar_context(request.ap_item_id, request.organization_id, _user)
    history = [(t.q, t.a) for t in (request.history or [])][-6:]

    async def event_stream():
        import json as _json
        full_answer_parts: List[str] = []
        try:
            async for chunk in _stream_sidebar_query(
                query=query,
                focus_item=ctx["focus_item"],
                vendor_items=ctx["vendor_items"],
                audit_events=ctx["audit_events"],
                org_id=ctx["org_id"],
                history=history,
            ):
                if not chunk:
                    continue
                full_answer_parts.append(chunk)
                yield "event: delta\n"
                yield f"data: {_json.dumps({'text': chunk})}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[sidebar/query/stream] unexpected error: %s", exc)
            yield "event: error\n"
            yield f"data: {_json.dumps({'message': 'stream_failed'})}\n\n"
        # Post-stream: send references + done event.
        full_answer = "".join(full_answer_parts)
        references = _extract_references(full_answer, ctx["audit_events"])
        yield "event: references\n"
        yield f"data: {_json.dumps({'references': references})}\n\n"
        yield "event: done\n"
        yield "data: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sidebar/query/suggestions")
async def sidebar_query_suggestions(
    ap_item_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query("default"),
    _user=Depends(get_current_user),
):
    """Seed starter questions based on invoice state so users aren't
    staring at a blank input. Max 4 chips — curated per state."""
    ctx = _load_sidebar_context(ap_item_id, organization_id, _user)
    focus = ctx["focus_item"]
    if not focus:
        return {"suggestions": [
            "What's in the AP queue right now?",
            "Show me overdue invoices",
        ]}

    state = str(focus.get("state") or "").lower()
    blockers = _blockers_for(focus)
    vendor = str(focus.get("vendor_name") or "this vendor").strip()
    has_vendor_siblings = len(ctx["vendor_items"]) > 0

    suggestions: List[str] = []
    # State-specific lead question
    if state in {"needs_info", "failed_post"} or blockers:
        suggestions.append("Why is this invoice blocked?")
    elif state in {"needs_approval", "pending_approval"}:
        suggestions.append("Is this safe to approve?")
    elif state in {"approved", "ready_to_post"}:
        suggestions.append("When will this be paid?")
    elif state in {"posted_to_erp"}:
        suggestions.append("Show me the ERP entry")
    else:
        suggestions.append("What's the status of this invoice?")

    # Vendor-aware follow-up
    if has_vendor_siblings:
        suggestions.append(f"What else is open from {vendor}?")
    else:
        suggestions.append(f"What's {vendor}'s payment history?")

    # Decision help
    overdue = _days_overdue(focus.get("due_date"))
    if overdue and overdue > 0:
        suggestions.append("What happens if I don't pay this?")
    elif blockers:
        suggestions.append("What do I need to do next?")
    else:
        suggestions.append("Can the agent handle this automatically?")

    return {"suggestions": suggestions[:4]}


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


# =============================================================================
# Sidebar query — Claude prompt + rule-based fallback
# =============================================================================


def _fmt_amount(amount: Any, currency: Any) -> str:
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return "unknown amount"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(str(currency or "").upper(), "")
    curr = str(currency or "").upper() or "USD"
    return f"{sym}{amt:,.2f}" if sym else f"{curr} {amt:,.2f}"


def _days_overdue(due_date: Any) -> Optional[int]:
    if not due_date:
        return None
    try:
        s = str(due_date)[:10]
        due = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta_days = int((now - due).total_seconds() // 86400)
        return delta_days
    except Exception:  # noqa: BLE001
        return None


def _describe_state(state: Any) -> str:
    s = str(state or "").lower()
    return {
        "received": "just arrived — not yet validated",
        "validated": "extracted and validated, waiting on matching",
        "needs_approval": "matched, pending approval",
        "pending_approval": "matched, pending approval",
        "needs_info": "blocked — needs info from you or the vendor",
        "approved": "approved, ready to post to the ERP",
        "ready_to_post": "approved, scheduled to post to the ERP",
        "posted_to_erp": "posted to the ERP",
        "closed": "closed",
        "failed_post": "ERP posting failed — needs retry or connector fix",
        "rejected": "rejected",
        "snoozed": "snoozed by a user",
        "reversed": "reversed after posting",
    }.get(s, s.replace("_", " ") or "unknown")


def _invoice_blurb(focus: Dict[str, Any]) -> str:
    """Compact single-invoice description. The atom of every sidebar answer."""
    vendor = str(focus.get("vendor_name") or focus.get("vendor") or "Unknown vendor").strip()
    ref = str(focus.get("invoice_number") or focus.get("reference") or "").strip()
    amount = _fmt_amount(focus.get("amount"), focus.get("currency"))
    state = _describe_state(focus.get("state"))
    parts = [f"{vendor}", ref and f"#{ref}", amount, state]
    return " · ".join([p for p in parts if p])


def _blockers_for(focus: Dict[str, Any]) -> List[str]:
    """Enumerate the operational reasons this invoice isn't moving."""
    blockers: List[str] = []
    match_status = str(focus.get("match_status") or "").lower()
    po = focus.get("po_number") or focus.get("purchase_order_number")
    grn = focus.get("grn_number") or focus.get("goods_received_note_number")
    if not po:
        blockers.append("no Purchase Order linked (required for 3-way match)")
    elif match_status in {"failed", "exception", "mismatch"}:
        blockers.append(f"3-way match failed ({match_status})")
    if not grn and po:
        blockers.append("no Goods Receipt Note linked")
    iban_verified = focus.get("vendor_iban_verified")
    if iban_verified is False:
        blockers.append("vendor IBAN is unverified — payment can't be scheduled yet")
    exception_reason = str(focus.get("exception_reason") or focus.get("exception_code") or "").strip()
    if exception_reason:
        blockers.append(f"exception flagged: {exception_reason}")
    paused = str(focus.get("workflow_paused_reason") or "").strip()
    if paused:
        blockers.append(f"workflow paused: {paused}")
    return blockers


def _next_action_hint(focus: Dict[str, Any], blockers: List[str]) -> str:
    state = str(focus.get("state") or "").lower()
    if state == "needs_approval":
        return "You can approve from the sidebar or from the Slack/Teams card the agent sent you."
    if state == "needs_info":
        return "Reply to the agent's request, or click 'Send follow-up' to the vendor."
    if state == "failed_post":
        return "Retry posting from the Pipeline actions, or check the ERP connector status in Settings."
    if blockers:
        if "no Purchase Order" in " ".join(blockers):
            return "Link a PO from your ERP, or override the match policy if this is an exception."
        if "IBAN" in " ".join(blockers):
            return "Trigger vendor onboarding to verify the IBAN before payment."
    return ""


def _answer_sidebar_query_rule_based(
    query: str,
    focus_item: Optional[Dict[str, Any]],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
) -> str:
    """Rule-based fallback that actually uses the invoice context.

    This fires when Claude is unavailable (no API key, credits exhausted,
    timeout). The old path handed off to the Slack rule engine, which is
    designed for broad "what's outstanding?" queries and responds with
    "try asking about a specific vendor" — useless when we have a
    specific invoice right in front of us.
    """
    q = query.strip().lower()

    if not focus_item:
        return (
            "Open an invoice in Gmail first — I need a specific record to answer "
            "questions about it."
        )

    vendor = str(focus_item.get("vendor_name") or focus_item.get("vendor") or "this vendor").strip()
    blurb = _invoice_blurb(focus_item)
    blockers = _blockers_for(focus_item)
    overdue = _days_overdue(focus_item.get("due_date"))
    amount = _fmt_amount(focus_item.get("amount"), focus_item.get("currency"))

    # Intent routing — cover the common question shapes first.
    asks_why = any(w in q for w in ["why", "blocked", "stuck", "exception", "problem", "wrong"])
    asks_when = any(w in q for w in ["when", "due", "overdue", "late"])
    asks_vendor = any(w in q for w in ["vendor", "other", "else", "more invoices", "open from"])
    asks_amount = any(w in q for w in ["how much", "amount", "total"])
    asks_status = any(w in q for w in ["status", "state", "where"])
    asks_next = any(w in q for w in ["next", "what do i do", "what should i do", "how do i"])

    if asks_why:
        if not blockers:
            state = _describe_state(focus_item.get("state"))
            return f"This invoice ({blurb}) isn't blocked — it's {state}. Nothing needs your attention right now."
        lines = [f"This invoice ({blurb}) is stuck because:"]
        for b in blockers:
            lines.append(f"  • {b}")
        hint = _next_action_hint(focus_item, blockers)
        if hint:
            lines.append("")
            lines.append(hint)
        return "\n".join(lines)

    if asks_when:
        due_date = str(focus_item.get("due_date") or "")[:10]
        if overdue is None:
            return f"No due date recorded for this invoice ({blurb})."
        if overdue > 0:
            return f"Due {due_date} — {overdue} days overdue."
        if overdue == 0:
            return f"Due today ({due_date})."
        return f"Due {due_date} — in {abs(overdue)} days."

    if asks_vendor:
        if not vendor_items:
            return f"This is the only open invoice from {vendor} right now."
        total = sum(float(vi.get("amount") or 0) for vi in vendor_items) + float(focus_item.get("amount") or 0)
        lines = [f"{vendor} has {len(vendor_items) + 1} open invoices totalling roughly {_fmt_amount(total, focus_item.get('currency'))}:"]
        for vi in [focus_item] + vendor_items[:8]:
            ref = str(vi.get("invoice_number") or "").strip() or "(no ref)"
            amt = _fmt_amount(vi.get("amount"), vi.get("currency"))
            st = _describe_state(vi.get("state"))
            lines.append(f"  • #{ref} — {amt} — {st}")
        return "\n".join(lines)

    if asks_amount:
        return f"This invoice from {vendor} is {amount}."

    if asks_status or asks_next:
        state = _describe_state(focus_item.get("state"))
        hint = _next_action_hint(focus_item, blockers)
        if hint:
            return f"{blurb}. {hint}"
        return f"{blurb}. No action needed from you right now."

    # Default: give a structured summary of the invoice + blockers + recent
    # agent actions. Better than sending the user back to the input with a
    # "try asking something else" message.
    lines = [f"**{blurb}**"]
    if overdue and overdue > 0:
        lines.append(f"⚠ {overdue} days overdue.")
    if blockers:
        lines.append("")
        lines.append("Blocked on:")
        for b in blockers[:4]:
            lines.append(f"  • {b}")
    if audit_events:
        recent = audit_events[0] if audit_events else None
        if recent:
            title = str(recent.get("operator_title") or recent.get("event_type") or "").strip()
            ts = str(recent.get("ts") or recent.get("created_at") or "")[:16].replace("T", " ")
            if title:
                lines.append("")
                lines.append(f"Most recent agent action: {title} ({ts}).")
    hint = _next_action_hint(focus_item, blockers)
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)


# Shared audit-event humanizer. Raw event types look like
# "ap_invoice_processing_field_review_required" — humans and Claude both
# prefer "Field review required". Mirrors the frontend humanizer in
# ThreadSidebar.js so sidebar and Claude context read the same.
_HUMANIZE_STRIP_PREFIXES = (
    "ap_invoice_processing_",
    "agent_action:",
    "ap_",
    "invoice_",
    "workflow_",
)


def _humanize_event_type(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # Already humanized (has spaces and no snake_case)
    if " " in s and "_" not in s and ":" not in s:
        return s if len(s) <= 100 else s[:97] + "…"
    lower = s.lower()
    for prefix in _HUMANIZE_STRIP_PREFIXES:
        if lower.startswith(prefix):
            lower = lower[len(prefix):]
            break
    lower = lower.replace(":", " ").replace("_", " ").strip()
    if not lower:
        return ""
    return lower[:1].upper() + lower[1:]


def _humanize_audit_line(event: Dict[str, Any]) -> str:
    """Turn a raw audit row into one line Claude can quote back verbatim.

    Format: "{HH:MM timestamp} — {human title}[ · {short message}]"
    """
    raw_ts = str(event.get("ts") or event.get("created_at") or "").strip()
    # Trim to HH:MM on a date — compact but unambiguous.
    ts_short = raw_ts[:16].replace("T", " ") if raw_ts else ""
    title = event.get("operator_title") or event.get("title") or event.get("event_type") or ""
    title = _humanize_event_type(title)
    msg = str(
        event.get("operator_message")
        or event.get("summary")
        or event.get("reason")
        or ""
    ).strip()
    if msg and len(msg) > 100:
        msg = msg[:97] + "…"
    base = f"{ts_short} — {title}" if ts_short else title
    return f"{base} · {msg}" if msg else base


def _build_sidebar_context(
    focus_item: Dict[str, Any],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
) -> str:
    """Human-curated fact sheet handed to Claude.

    Previous version dumped raw DB fields ("match_status: NOT LINKED").
    This version narrates the invoice the way a teammate would describe
    it — Claude mirrors the phrasing back, so answers feel natural
    instead of robotic.
    """
    lines: List[str] = []
    vendor = str(focus_item.get("vendor_name") or focus_item.get("vendor") or "Unknown vendor").strip()
    ref = str(focus_item.get("invoice_number") or focus_item.get("reference") or "").strip()
    amount = _fmt_amount(focus_item.get("amount"), focus_item.get("currency"))
    due_iso = str(focus_item.get("due_date") or "")[:10]
    overdue = _days_overdue(focus_item.get("due_date"))
    state_description = _describe_state(focus_item.get("state"))

    # Narrative header
    header_bits = [f"An invoice from {vendor}"]
    if ref:
        header_bits.append(f"(reference #{ref})")
    header_bits.append(f"for {amount}.")
    lines.append("INVOICE:")
    lines.append("  " + " ".join(header_bits))
    lines.append(f"  Current status: {state_description}.")

    # Due-date narration
    if due_iso:
        if overdue is not None and overdue > 0:
            lines.append(f"  Due {due_iso} — {overdue} days overdue.")
        elif overdue == 0:
            lines.append(f"  Due today ({due_iso}).")
        elif overdue is not None and overdue < 0:
            lines.append(f"  Due {due_iso} — in {abs(overdue)} days.")
        else:
            lines.append(f"  Due {due_iso}.")
    else:
        lines.append("  No due date was extracted from this invoice.")

    # 3-way match narration
    po = focus_item.get("po_number") or focus_item.get("purchase_order_number")
    grn = focus_item.get("grn_number") or focus_item.get("goods_received_note_number")
    match_status = str(focus_item.get("match_status") or "").lower()
    if po and grn:
        if match_status in {"passed", "matched", "ok"}:
            lines.append(f"  Three-way match passed (PO {po}, GRN {grn}).")
        elif match_status in {"failed", "mismatch", "exception"}:
            lines.append(f"  Three-way match failed despite PO {po} and GRN {grn} being linked.")
        else:
            lines.append(f"  PO {po} and GRN {grn} are linked. Match status: {match_status or 'pending'}.")
    elif po and not grn:
        lines.append(f"  PO {po} is linked, but no Goods Receipt Note is attached.")
    elif grn and not po:
        lines.append(f"  GRN {grn} is linked, but no Purchase Order is attached.")
    else:
        lines.append("  No Purchase Order or Goods Receipt Note is linked to this invoice.")

    # Vendor-side narration
    iban_verified = focus_item.get("vendor_iban_verified")
    if iban_verified is True:
        lines.append(f"  {vendor}'s bank details (IBAN) have been verified.")
    elif iban_verified is False:
        lines.append(f"  {vendor}'s bank details (IBAN) have NOT been verified — payment cannot be scheduled yet.")

    # Exception / pause reason (already human text usually)
    exception = str(focus_item.get("exception_reason") or focus_item.get("exception_code") or "").strip()
    if exception:
        lines.append(f"  Exception flagged: {exception}")
    paused = str(focus_item.get("workflow_paused_reason") or "").strip()
    if paused:
        lines.append(f"  Workflow paused because: {paused}")

    # Vendor siblings
    if vendor_items:
        total = sum(float(vi.get("amount") or 0) for vi in vendor_items) + float(focus_item.get("amount") or 0)
        lines.append("")
        lines.append(
            f"OTHER OPEN INVOICES FROM {vendor} "
            f"({len(vendor_items)} more, {_fmt_amount(total, focus_item.get('currency'))} total "
            "including this one):"
        )
        for vi in vendor_items[:9]:
            r = str(vi.get("invoice_number") or "").strip() or "(no reference)"
            a = _fmt_amount(vi.get("amount"), vi.get("currency"))
            d = str(vi.get("due_date") or "")[:10] or "no due date"
            s = _describe_state(vi.get("state"))
            lines.append(f"  - #{r}, {a}, {s}, due {d}")

    # Audit timeline — humanized
    if audit_events:
        lines.append("")
        lines.append("WHAT THE AGENT HAS DONE ON THIS INVOICE (newest first):")
        for e in audit_events[:10]:
            lines.append("  - " + _humanize_audit_line(e))

    return "\n".join(lines)


def _build_system_prompt() -> str:
    return (
        "You are Clearledgr's AP agent answering a finance teammate's question about "
        "a SPECIFIC invoice they have open in Gmail. You are NOT running a dashboard "
        "query — you are explaining ONE invoice and what should happen with it.\n\n"
        "GROUNDING:\n"
        "- You receive a curated fact sheet describing the focus invoice, any other "
        "  open invoices from the same vendor, and recent agent actions on this "
        "  invoice. Treat this as ground truth. Do not invent amounts, dates, PO "
        "  numbers, or vendor history.\n"
        "- If the fact sheet says something isn't linked (e.g., 'No Purchase Order'), "
        "  don't hedge — say it plainly.\n\n"
        "CONVERSATION:\n"
        "- Earlier turns in this conversation are included as chat history. Resolve "
        "  pronouns like 'it', 'them', 'that invoice' against that history. When "
        "  the user follows up with 'can I override it?', they mean the thing you "
        "  were just discussing.\n\n"
        "ANSWER STYLE:\n"
        "- Open with the single most useful fact. No preamble. No repeating the "
        "  question back.\n"
        "- For 'why is this blocked', list real blockers in a short bullet list, "
        "  then one sentence on what they can do next.\n"
        "- For vendor questions, list invoices with reference, amount, state, due "
        "  date. Sum totals only if asked.\n"
        "- For 'what should I do', be prescriptive about the next step: approve "
        "  here, link a PO, trigger vendor onboarding, retry post.\n"
        "- Use real numbers with currency symbols. Never hedge with 'some' or 'a few'.\n"
        "- When referencing an agent action, include its timestamp (HH:MM) so the "
        "  user can find it in the Agent Actions timeline above — e.g., 'The agent "
        "  flagged this at 09:12 for field review.'\n"
        "- 2-5 sentences unless a bulleted list is genuinely needed. Use markdown "
        "  sparingly: `**bold**` for emphasis on one key phrase, `-` for bullets, "
        "  nothing else. No headers.\n"
        "- If the data genuinely doesn't support an answer, say what's missing."
    )


def _build_messages(
    query: str,
    context: str,
    history: Optional[List[tuple]] = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    # Plant the curated fact sheet as the first user turn so it's part of
    # the conversation context, not just a floating header.
    messages.append({
        "role": "user",
        "content": f"Here is the invoice I'm looking at:\n\n{context}",
    })
    messages.append({
        "role": "assistant",
        "content": "Got it — I have this invoice in view. What would you like to know?",
    })
    for (q, a) in (history or []):
        q_clean = str(q or "").strip()
        a_clean = str(a or "").strip()
        if not q_clean or not a_clean:
            continue
        messages.append({"role": "user", "content": q_clean})
        messages.append({"role": "assistant", "content": a_clean})
    messages.append({"role": "user", "content": query})
    return messages


async def _answer_sidebar_query(
    query: str,
    focus_item: Optional[Dict[str, Any]],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
    org_id: str,
    history: Optional[List[tuple]] = None,
) -> str:
    """Non-streaming answer. Claude first, rule-based fallback."""
    if not focus_item:
        return (
            "Open an invoice in Gmail first — I need a specific record to answer "
            "questions about it."
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)

    context = _build_sidebar_context(focus_item, vendor_items, audit_events)
    messages = _build_messages(query, context, history)

    try:
        from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
        gateway = get_llm_gateway()
        resp = await gateway.call(
            LLMAction.SLACK_QUERY,
            messages=messages,
            system_prompt=_build_system_prompt(),
            organization_id=org_id,
        )
        answer = str(resp.content or "").strip() if resp else ""
        if not answer:
            return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)
        return answer
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sidebar/query] Claude call failed: %s", exc)
        return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)


async def _stream_sidebar_query(
    query: str,
    focus_item: Optional[Dict[str, Any]],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
    org_id: str,
    history: Optional[List[tuple]] = None,
):
    """Async generator yielding text chunks. Falls back to emitting the
    full rule-based answer as one chunk if Claude is unavailable."""
    if not focus_item:
        yield (
            "Open an invoice in Gmail first — I need a specific record to answer "
            "questions about it."
        )
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)
        return

    context = _build_sidebar_context(focus_item, vendor_items, audit_events)
    messages = _build_messages(query, context, history)

    try:
        from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
        gateway = get_llm_gateway()
        any_chunk = False
        async for chunk in gateway.stream(
            LLMAction.SLACK_QUERY,
            messages=messages,
            system_prompt=_build_system_prompt(),
            organization_id=org_id,
        ):
            if chunk:
                any_chunk = True
                yield chunk
        if not any_chunk:
            yield _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sidebar/query/stream] Claude stream failed: %s", exc)
        yield _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)


# Reference extraction: find HH:MM timestamps in the answer that match
# audit events, so the frontend can turn them into clickable links that
# scroll to the matching row in the Agent Actions timeline.
import re as _re

_TIMESTAMP_PATTERN = _re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _extract_references(answer: str, audit_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not answer or not audit_events:
        return []
    refs: List[Dict[str, Any]] = []
    seen: set = set()
    for match in _TIMESTAMP_PATTERN.finditer(answer):
        hhmm = match.group(0)
        if hhmm in seen:
            continue
        # Find an audit event whose ts contains this HH:MM (first match wins).
        for e in audit_events:
            ts = str(e.get("ts") or e.get("created_at") or "")
            if hhmm in ts[:16]:
                refs.append({
                    "label": hhmm,
                    "event_id": str(e.get("id") or ""),
                    "event_type": str(e.get("event_type") or ""),
                    "offset": match.start(),
                })
                seen.add(hhmm)
                break
    return refs
