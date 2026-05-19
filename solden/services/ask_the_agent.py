"""Module 2 — "Ask the agent" Q&A on the exception detail page.

Spec line 100: "Ask the agent: free-form questions about this invoice
('show prior bills from this vendor', 'what does PO 4471-A reference').
Returns within 10 seconds for typical questions."

The agent is bounded to a structured context bundle around the current
invoice — it has access to:
  - the invoice's extracted fields + state + governance verdict
  - vendor profile + recent invoice history (last 12 months)
  - 3-way match data + PO line items if any
  - audit timeline for this invoice

Outside that bundle, the model has no tools — no DB queries, no web
fetches, no other invoices. This is "explain what's already known
about THIS bill," not "do agent work." Keeps the latency bound + the
hallucination surface tight.

Output is plain prose with citation markers (`[s1]`, `[s2]`, ...)
mapped to source rows the SPA can render as inline references.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict, List, Optional

from solden.core.llm_gateway import LLMAction, get_llm_gateway

logger = logging.getLogger(__name__)


def ask_the_agent(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    question: str,
) -> Dict[str, Any]:
    """Run a single Ask-the-agent turn against the bounded invoice context.

    Returns:
      {
        "answer": str,
        "sources": [{"id": "s1", "type": "...", "summary": "..."}, ...],
        "model": "claude-sonnet-4-x",
        "latency_ms": int,
        "fallback": bool,   # true when LLM was unavailable
      }
    """
    question = str(question or "").strip()
    if not question:
        return {"answer": "", "sources": [], "model": None, "fallback": False, "error": "empty_question"}

    context = _build_context(db, organization_id, ap_item_id)
    if not context.get("item"):
        return {"answer": "", "sources": [], "model": None, "fallback": False,
                "error": "ap_item_not_found"}

    sources = _enumerate_sources(context)
    user_prompt = _render_user_prompt(question, context, sources)

    try:
        gateway = get_llm_gateway()
    except Exception as exc:
        logger.warning("[ask_the_agent] gateway unavailable: %s", exc)
        return _fallback_response(question, context, sources)

    import time as _time
    start = _time.perf_counter()
    try:
        response = gateway.call_sync(
            action=LLMAction.ASK_THE_AGENT,
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=_SYSTEM_PROMPT,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
        )
        latency_ms = response.latency_ms or int((_time.perf_counter() - start) * 1000)
        # response.content is a string for plain text actions; can be a
        # list for tool-use responses (we don't use tools here).
        content = response.content
        if isinstance(content, list):
            # Concatenate any text blocks; ignore tool_use blocks.
            text = "".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            text = str(content or "")
        if not text.strip():
            return _fallback_response(question, context, sources, latency_ms=latency_ms)
        return {
            "answer": text.strip(),
            "sources": sources,
            "model": response.model,
            "latency_ms": latency_ms,
            "fallback": False,
        }
    except Exception as exc:
        latency_ms = int((_time.perf_counter() - start) * 1000)
        logger.warning("[ask_the_agent] gateway call failed in %dms: %s", latency_ms, exc)
        return _fallback_response(question, context, sources, latency_ms=latency_ms)


# ─── Context bundling ──────────────────────────────────────────────


def _build_context(db, organization_id: str, ap_item_id: str) -> Dict[str, Any]:
    """Pull the invoice + vendor history + 3-way match + audit timeline."""
    try:
        item = db.get_ap_item(ap_item_id) if hasattr(db, "get_ap_item") else None
    except Exception:
        item = None
    if not item or str(item.get("organization_id") or "") != organization_id:
        return {"item": None}

    vendor_name = item.get("vendor_name") or item.get("vendor") or ""
    vendor_profile = None
    vendor_history: List[Dict[str, Any]] = []
    if vendor_name and hasattr(db, "get_vendor_profile"):
        try:
            vendor_profile = db.get_vendor_profile(organization_id, vendor_name)
        except Exception:
            vendor_profile = None
    if vendor_name and hasattr(db, "list_ap_items"):
        try:
            all_items = db.list_ap_items(organization_id, limit=500) or []
            vendor_history = [
                {
                    "id": it.get("id"),
                    "invoice_number": it.get("invoice_number"),
                    "amount": it.get("amount"),
                    "currency": it.get("currency"),
                    "state": it.get("state"),
                    "created_at": it.get("created_at"),
                    "exception_code": it.get("exception_code"),
                }
                for it in all_items
                if (it.get("vendor_name") or it.get("vendor") or "") == vendor_name
                and it.get("id") != ap_item_id
            ][:20]
        except Exception:
            vendor_history = []

    three_way = None
    if hasattr(db, "get_three_way_match_summary"):
        try:
            three_way = db.get_three_way_match_summary(ap_item_id)
        except Exception:
            three_way = None

    audit_events: List[Dict[str, Any]] = []
    if hasattr(db, "list_ap_audit_events"):
        try:
            events = db.list_ap_audit_events(ap_item_id, limit=20, order="asc") or []
            audit_events = [
                {
                    "ts": ev.get("ts"),
                    "event_type": ev.get("event_type"),
                    "prev_state": ev.get("prev_state"),
                    "new_state": ev.get("new_state"),
                    "decision_reason": ev.get("decision_reason"),
                    "agent_confidence": ev.get("agent_confidence"),
                }
                for ev in events
            ]
        except Exception:
            audit_events = []

    return {
        "item": item,
        "vendor_name": vendor_name,
        "vendor_profile": vendor_profile,
        "vendor_history": vendor_history,
        "three_way": three_way,
        "audit_events": audit_events,
    }


def _enumerate_sources(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the [s1], [s2], ... map the model can cite."""
    sources: List[Dict[str, Any]] = []
    item = context.get("item") or {}
    sources.append({
        "id": "s1",
        "type": "invoice",
        "summary": (
            f"This invoice: {item.get('vendor_name')} · "
            f"{item.get('invoice_number') or '(no number)'} · "
            f"{item.get('currency') or ''} {item.get('amount')} · "
            f"state={item.get('state')}"
        ),
    })
    if context.get("vendor_profile"):
        sources.append({
            "id": "s2",
            "type": "vendor_profile",
            "summary": f"Vendor profile for {context.get('vendor_name')}",
        })
    if context.get("vendor_history"):
        sources.append({
            "id": f"s{len(sources) + 1}",
            "type": "vendor_history",
            "summary": f"{len(context['vendor_history'])} prior invoices from this vendor",
        })
    if context.get("three_way"):
        sources.append({
            "id": f"s{len(sources) + 1}",
            "type": "three_way_match",
            "summary": "Three-way match summary (PO + GR variances)",
        })
    if context.get("audit_events"):
        sources.append({
            "id": f"s{len(sources) + 1}",
            "type": "audit_timeline",
            "summary": f"{len(context['audit_events'])} timeline events",
        })
    return sources


# ─── Prompts ───────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You are the AP agent answering questions about a specific invoice "
    "in a finance operator's workspace. Your job is to explain what is "
    "already known about THIS invoice from the structured context bundle "
    "you receive — never invent data, never reference invoices outside the "
    "vendor history bundle, and never propose actions you cannot back up "
    "with a source.\n\n"
    "Answer in plain prose, two to four sentences for typical questions. "
    "Cite sources inline using the marker syntax [s1], [s2] etc. that the "
    "user prompt enumerates. If the bundle does not contain the data the "
    "operator asks for, say so explicitly — 'I don't see that in the "
    "context I have for this invoice' is the right answer when relevant.\n\n"
    "Never invent financial figures, vendor identifiers, dates, or PO "
    "numbers. Never speculate beyond the context. Never recommend actions "
    "the operator should take — that is the action-bar's job, not yours."
)


def _render_user_prompt(question: str, context: Dict[str, Any], sources: List[Dict[str, Any]]) -> str:
    """Compose the structured context the model sees per turn."""
    item = context.get("item") or {}
    sources_block = "\n".join(f"  {s['id']}: {s['summary']}" for s in sources)

    item_block = _json.dumps({
        "vendor_name": item.get("vendor_name"),
        "invoice_number": item.get("invoice_number"),
        "amount": item.get("amount"),
        "currency": item.get("currency"),
        "due_date": item.get("due_date"),
        "invoice_date": item.get("invoice_date"),
        "state": item.get("state"),
        "exception_code": item.get("exception_code"),
        "po_number": item.get("po_number"),
        "gl_code": item.get("gl_code"),
        "department": item.get("department"),
    }, default=str, indent=2)

    history_block = _json.dumps(
        context.get("vendor_history") or [],
        default=str, indent=2,
    )
    audit_block = _json.dumps(
        context.get("audit_events") or [],
        default=str, indent=2,
    )
    three_way_block = _json.dumps(
        context.get("three_way") or {},
        default=str, indent=2,
    )
    profile = context.get("vendor_profile") or {}
    profile_block = _json.dumps({
        "invoice_count": profile.get("invoice_count"),
        "first_seen_at": profile.get("first_seen_at") or profile.get("created_at"),
        "avg_invoice_amount": profile.get("avg_invoice_amount"),
        "fraud_flags": profile.get("fraud_flags"),
        "iban_verified": profile.get("iban_verified"),
        "registry_verified": profile.get("registry_verified"),
    }, default=str, indent=2)

    return (
        "## Available sources\n"
        f"{sources_block}\n\n"
        "## [s1] This invoice\n"
        f"```json\n{item_block}\n```\n\n"
        "## [s2] Vendor profile\n"
        f"```json\n{profile_block}\n```\n\n"
        "## Vendor history (last 20 invoices, this vendor, excluding the one in question)\n"
        f"```json\n{history_block}\n```\n\n"
        "## Three-way match summary\n"
        f"```json\n{three_way_block}\n```\n\n"
        "## Audit timeline (chronological)\n"
        f"```json\n{audit_block}\n```\n\n"
        "## Operator question\n"
        f"{question}\n\n"
        "## Your answer\n"
        "Two to four sentences. Cite the source IDs inline."
    )


# ─── Fallback ──────────────────────────────────────────────────────


def _fallback_response(
    question: str,
    context: Dict[str, Any],
    sources: List[Dict[str, Any]],
    *,
    latency_ms: int = 0,
) -> Dict[str, Any]:
    """When the LLM gateway is unavailable, render a deterministic
    summary of the context bundle so the operator still sees something
    actionable. Not as good as Sonnet, but never silent.
    """
    item = context.get("item") or {}
    history = context.get("vendor_history") or []
    parts: List[str] = []
    parts.append(
        f"I can see {item.get('vendor_name', '?')}'s invoice "
        f"{item.get('invoice_number') or '(no number)'} for "
        f"{item.get('currency') or ''} {item.get('amount')}. [s1]"
    )
    if history:
        parts.append(
            f"This vendor has {len(history)} prior invoice(s) on file. "
            f"[s{3 if context.get('vendor_profile') else 2}]"
        )
    parts.append(
        "(LLM unavailable; this is a deterministic context summary. "
        "Reload to retry the model.)"
    )
    return {
        "answer": " ".join(parts),
        "sources": sources,
        "model": None,
        "latency_ms": latency_ms,
        "fallback": True,
    }
