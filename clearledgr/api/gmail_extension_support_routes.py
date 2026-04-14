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

    # Sidebar-specific answer. Scope is always ONE invoice (plus vendor
    # context). Don't use the Slack query prompt — that's tuned for
    # cross-cutting "what's outstanding this week?" queries and gives
    # list-style answers that feel useless here.
    answer = await _answer_sidebar_query(
        query=query,
        focus_item=focus_item,
        vendor_items=items[1:] if len(items) > 1 else [],
        audit_events=audit_events,
        org_id=org_id,
    )

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


def _build_sidebar_context(
    focus_item: Dict[str, Any],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
) -> str:
    """Compact fact sheet handed to Claude."""
    lines: List[str] = []
    v = str(focus_item.get("vendor_name") or focus_item.get("vendor") or "Unknown")
    ref = str(focus_item.get("invoice_number") or focus_item.get("reference") or "")
    amt = _fmt_amount(focus_item.get("amount"), focus_item.get("currency"))
    due = str(focus_item.get("due_date") or "")[:10]
    overdue = _days_overdue(focus_item.get("due_date"))
    state = _describe_state(focus_item.get("state"))
    lines.append("FOCUS INVOICE (the one the user is asking about):")
    lines.append(f"  vendor: {v}")
    lines.append(f"  reference: {ref or '—'}")
    lines.append(f"  amount: {amt}")
    lines.append(f"  due: {due or '—'}" + (f" ({overdue} days overdue)" if (overdue and overdue > 0) else ""))
    lines.append(f"  state: {state}")
    po = focus_item.get("po_number") or focus_item.get("purchase_order_number")
    grn = focus_item.get("grn_number") or focus_item.get("goods_received_note_number")
    lines.append(f"  PO: {po or 'NOT LINKED'}")
    lines.append(f"  GRN: {grn or 'NOT LINKED'}")
    lines.append(f"  match_status: {focus_item.get('match_status') or '—'}")
    exception = str(focus_item.get("exception_reason") or focus_item.get("exception_code") or "").strip()
    if exception:
        lines.append(f"  exception: {exception}")
    paused = str(focus_item.get("workflow_paused_reason") or "").strip()
    if paused:
        lines.append(f"  workflow_paused_reason: {paused}")
    iban_verified = focus_item.get("vendor_iban_verified")
    if iban_verified is not None:
        lines.append(f"  vendor_iban_verified: {iban_verified}")

    if vendor_items:
        lines.append("")
        lines.append(f"OTHER OPEN INVOICES FROM {v.upper()} (up to 9):")
        for vi in vendor_items[:9]:
            r = str(vi.get("invoice_number") or "").strip() or "(no ref)"
            a = _fmt_amount(vi.get("amount"), vi.get("currency"))
            d = str(vi.get("due_date") or "")[:10] or "—"
            s = _describe_state(vi.get("state"))
            lines.append(f"  - #{r} | {a} | due:{d} | {s}")

    if audit_events:
        lines.append("")
        lines.append("RECENT AGENT ACTIONS ON THIS INVOICE (newest first, up to 10):")
        for e in audit_events[:10]:
            ts = str(e.get("ts") or e.get("created_at") or "")[:19]
            title = str(e.get("operator_title") or e.get("event_type") or "").strip() or "action"
            msg = str(e.get("operator_message") or e.get("summary") or "").strip()
            lines.append(f"  - {ts} | {title}" + (f" — {msg[:100]}" if msg else ""))

    return "\n".join(lines)


async def _answer_sidebar_query(
    query: str,
    focus_item: Optional[Dict[str, Any]],
    vendor_items: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
    org_id: str,
) -> str:
    """Answer a single-invoice sidebar question, Claude first, rule-based fallback."""
    if not focus_item:
        return (
            "Open an invoice in Gmail first — I need a specific record to answer "
            "questions about it."
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)

    context = _build_sidebar_context(focus_item, vendor_items, audit_events)
    system_prompt = (
        "You are Clearledgr's AP agent answering a finance teammate's question about "
        "a SPECIFIC invoice they have open in Gmail. You are NOT running a dashboard "
        "query — you are explaining one invoice and what needs to happen with it.\n\n"
        "INPUT SHAPE:\n"
        "You will receive the focus invoice's full state (vendor, amount, PO/GRN "
        "linkage, match status, exception reason, workflow pause reason, vendor IBAN "
        "verification), up to 9 other open invoices from the same vendor, and the "
        "focus invoice's recent agent-action timeline.\n\n"
        "ANSWER STYLE:\n"
        "- Open with the single most useful fact for the question asked. Don't "
        "  preamble, don't repeat the question back.\n"
        "- When the user asks 'why is this blocked' or 'what's wrong', walk through "
        "  the actual blockers (no PO, unverified IBAN, match failure, etc.) in a "
        "  short bulleted list, then one line on what they can do.\n"
        "- When the user asks about the vendor's other invoices, list them: ref, "
        "  amount with currency, state, due date. Sum totals only if they ask.\n"
        "- When the user asks 'what should I do', be prescriptive about the next "
        "  action (approve here, link a PO, trigger vendor onboarding, retry post).\n"
        "- Never tell the user to 'try asking about a specific vendor' — you ALREADY "
        "  have the specific vendor and invoice.\n"
        "- Include currency symbols and real numbers. Don't hedge with 'some' or "
        "  'a few'.\n"
        "- Be terse. 2-5 sentences unless a bulleted list is genuinely needed. No "
        "  markdown headers. Plain prose.\n"
        "- If the data genuinely doesn't support an answer, say what's missing "
        "  (e.g., 'no due date was extracted from this invoice')."
    )
    user_message = f"INVOICE CONTEXT\n{context}\n\nQUESTION: {query}"

    try:
        from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
        gateway = get_llm_gateway()
        resp = await gateway.call(
            LLMAction.SLACK_QUERY,
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
            organization_id=org_id,
        )
        answer = str(resp.content or "").strip() if resp else ""
        if not answer:
            return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)
        return answer
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sidebar/query] Claude call failed: %s", exc)
        return _answer_sidebar_query_rule_based(query, focus_item, vendor_items, audit_events)
