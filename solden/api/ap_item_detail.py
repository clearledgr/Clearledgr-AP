"""AP item detail endpoint — Module 2 (Exception Detail and Resolution).

Consolidates everything the workspace's exception-detail page needs into
one structured response so the frontend makes a single call instead of
fanning out to five backend endpoints.

  GET /api/workspace/ap-items/{ap_item_id}/detail

Response shape:
  {
    "item":      <build_worklist_item output — header, bill detail, line items,
                  vendor_intelligence, source_conflicts, attachment manifest>,
    "reasoning": <the harness made legible; see _build_reasoning_payload>,
    "match":     <three-way-match summary, or null if no PO / GR data>,
    "timeline":  <audit_events for the box, normalised, ordered desc>,
    "actions":   <available state-machine actions for the current state>,
  }

Why a single endpoint vs five?
  - Removes fan-out latency on the leader's exception screen — the
    detail page is one viewable surface, so it gets one network call.
  - Lets us colocate the "narrative" composition (rule decision +
    governance verdict + sources → plain language) on the server,
    where the data lives.
  - Keeps the frontend dumb: render what comes back, no orchestration.

The endpoint composes existing helpers — ``build_worklist_item``,
``run_three_way_match``, ``list_ap_audit_events``,
``normalize_operator_audit_events`` — so there is no new business
logic, just a new shape on top of them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from solden.core.ap_states import APState, normalize_state, VALID_TRANSITIONS
from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.core.org_utils import require_org
from solden.services.ap_item_service import (
    _resolve_item_for_detail,
    build_worklist_item,
)
from solden.services.ap_operator_audit import normalize_operator_audit_events

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace", tags=["ap-item-detail"])


# ---------------------------------------------------------------------------
# Action availability — derived from the canonical state machine.
# ---------------------------------------------------------------------------

# Intent name → state(s) it transitions to. The runtime owns the actual
# transition; this map only tells the frontend which buttons to show.
# Keep in lockstep with ``finance_skills/ap_skill.py::_INTENTS``.
_INTENT_TARGET_STATES: Dict[str, frozenset] = {
    "approve_invoice": frozenset({APState.APPROVED}),
    "reject_invoice": frozenset({APState.REJECTED}),
    "request_info": frozenset({APState.NEEDS_INFO}),
    "escalate_approval": frozenset({APState.NEEDS_APPROVAL}),
    # Module 2 spec line 99 — "send to specific person".
    # `reassign_approval` is the existing intent that hands off the
    # current approval step to a named approver; surfacing it in the
    # detail page satisfies the spec's named action.
    "reassign_approval": frozenset({APState.NEEDS_APPROVAL, APState.NEEDS_SECOND_APPROVAL}),
    "snooze_invoice": frozenset({APState.SNOOZED}),
    "unsnooze_invoice": frozenset({
        APState.VALIDATED, APState.NEEDS_INFO, APState.NEEDS_APPROVAL,
        APState.NEEDS_SECOND_APPROVAL, APState.FAILED_POST,
    }),
    "post_to_erp": frozenset({APState.POSTED_TO_ERP, APState.READY_TO_POST}),
    "reverse_invoice_post": frozenset({APState.REVERSED}),
    "request_approval": frozenset({APState.NEEDS_APPROVAL}),
    "manually_classify_invoice": frozenset({APState.RECEIVED, APState.VALIDATED}),
    "resubmit_invoice": frozenset({APState.VALIDATED, APState.RECEIVED}),
}


def available_intents(current_state: str) -> List[str]:
    """Intents whose target state is reachable from ``current_state``."""
    try:
        cur = APState(normalize_state(current_state))
    except ValueError:
        return []
    valid_targets = VALID_TRANSITIONS.get(cur, frozenset())
    available = []
    for intent, targets in _INTENT_TARGET_STATES.items():
        if targets & valid_targets:
            available.append(intent)
    return sorted(available)


def primary_intent(current_state: str, recommendation: Optional[str]) -> Optional[str]:
    """The single most likely action for the leader, given state + recommendation.

    Drives which button gets the primary (mint-green) styling on the
    detail page. The leader can still see every other available action;
    this is just the default-target highlight.
    """
    state = normalize_state(current_state)
    rec = (recommendation or "").lower().strip()

    if state == APState.NEEDS_APPROVAL.value:
        return "approve_invoice"
    if state == APState.NEEDS_INFO.value:
        return "request_info"
    if state == APState.FAILED_POST.value:
        return "post_to_erp"
    if state == APState.READY_TO_POST.value:
        return "post_to_erp"
    if state == APState.VALIDATED.value:
        if rec == "approve":
            return "approve_invoice"
        if rec == "needs_info":
            return "request_info"
        if rec in ("escalate", "reject"):
            return "escalate_approval"
        return "request_approval"
    return None


# ---------------------------------------------------------------------------
# Reasoning composition — "the harness made legible" (spec §2.97).
# ---------------------------------------------------------------------------

def _safe_json(value: Any) -> Any:
    """Best-effort JSON decode for fields stored as strings."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _latest_governance_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Most recent audit row carrying a governance verdict.

    Migration v50 added ``governance_verdict`` + ``agent_confidence``
    columns; verdicts are written by the governance gate on every
    runtime intent. The latest one is what the leader cares about.
    """
    for event in events:
        if event.get("governance_verdict"):
            return event
    return None


def _build_reasoning_payload(
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    timeline: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compose the agent reasoning payload — the centerpiece of Module 2.

    Pulls together:
      - The deterministic AP decision (rules cascade output, persisted
        to ap_item metadata at decision time).
      - The governance gate's verdict + agent_confidence on the most
        recent runtime intent.
      - The vendor-context inputs the rules cascade consulted.
      - The single-pass advisory hints (gl_coding / duplicate_analysis /
        risk_assessment) when the LLM produced any.
      - The recovery plan (AGENT_PLANNING output) for needs_info items.
      - A plain-language ``narrative`` so the frontend can render the
        story without re-deriving it from the parts.

    The shape is stable per route. Missing fields stay ``None`` rather
    than getting filled with placeholders — the frontend should render
    "we don't have this signal" calmly, not fake one.
    """
    vendor_intelligence = _safe_json(metadata.get("vendor_intelligence")) or {}
    if not isinstance(vendor_intelligence, dict):
        vendor_intelligence = {}

    risk_flags_raw = metadata.get("ap_decision_risk_flags") or item.get("reasoning_risks")
    risk_flags = _safe_json(risk_flags_raw) if isinstance(risk_flags_raw, str) else risk_flags_raw
    if not isinstance(risk_flags, list):
        risk_flags = []

    agent_decision = {
        "recommendation": metadata.get("ap_decision_recommendation")
            or vendor_intelligence.get("ap_decision"),
        "reasoning": metadata.get("ap_decision_reasoning") or item.get("reasoning_summary"),
        "risk_flags": [str(f) for f in risk_flags if f],
        "model": metadata.get("ap_decision_model") or "rules",
    }

    governance_event = _latest_governance_event(timeline)
    governance: Optional[Dict[str, Any]] = None
    if governance_event:
        governance = {
            "verdict": governance_event.get("governance_verdict"),
            "agent_confidence": governance_event.get("agent_confidence"),
            "recorded_at": governance_event.get("ts"),
            "decision_reason": governance_event.get("decision_reason"),
        }

    sources: Dict[str, Any] = {
        "vendor_context": vendor_intelligence.get("vendor_context") or {},
        "decision_feedback": vendor_intelligence.get("decision_feedback") or {},
        "single_pass_hints": vendor_intelligence.get("single_pass_hints") or None,
    }

    confidence_gate = item.get("confidence_gate") or {}
    if isinstance(confidence_gate, dict):
        sources["confidence_gate"] = {
            "requires_field_review": bool(confidence_gate.get("requires_field_review")),
            "confidence_blockers": confidence_gate.get("confidence_blockers") or [],
            "field_confidences": confidence_gate.get("field_confidences") or {},
        }

    risk_signals = _safe_json(metadata.get("risk_signals")) or item.get("risk_signals") or {}
    if isinstance(risk_signals, dict) and risk_signals:
        sources["risk_signals"] = risk_signals

    anomaly = _safe_json(metadata.get("anomaly_signals"))
    if isinstance(anomaly, dict) and anomaly:
        sources["anomaly_signals"] = anomaly

    recovery_plan = _safe_json(metadata.get("agent_recovery_plan"))
    if isinstance(recovery_plan, dict) and recovery_plan.get("steps"):
        sources["recovery_plan"] = recovery_plan

    narrative = _compose_narrative(
        item=item,
        agent_decision=agent_decision,
        governance=governance,
        sources=sources,
    )

    return {
        "agent_decision": agent_decision,
        "governance": governance,
        "sources": sources,
        "narrative": narrative,
    }


def _compose_narrative(
    *,
    item: Dict[str, Any],
    agent_decision: Dict[str, Any],
    governance: Optional[Dict[str, Any]],
    sources: Dict[str, Any],
) -> str:
    """Render a plain-language story of how the agent arrived at its routing.

    This is *not* a model call. The narrative is deterministic — built
    from the same parts the leader sees in the panel, just stitched
    into prose. It exists so the page has a single human-readable
    summary line above the structured detail; the structured detail
    below is what the leader actually drills into.
    """
    vendor = item.get("vendor") or item.get("vendor_name") or "this vendor"
    rec = (agent_decision.get("recommendation") or "").strip().lower()
    reasoning = (agent_decision.get("reasoning") or "").strip()

    intro_by_rec = {
        "approve": f"The agent recommended approving the bill from {vendor}.",
        "needs_info": f"The agent paused on this bill from {vendor} and requested more information before proceeding.",
        "escalate": f"The agent escalated this bill from {vendor} for human review.",
        "reject": f"The agent recommended rejecting this bill from {vendor}.",
    }
    intro = intro_by_rec.get(
        rec,
        f"The agent processed this bill from {vendor}.",
    )

    parts = [intro]

    if reasoning:
        parts.append(reasoning)

    if governance and governance.get("verdict"):
        verdict = str(governance.get("verdict") or "").lower()
        if verdict in ("block", "blocked"):
            parts.append(
                "The governance gate blocked the agent's intended action; the bill is now waiting on a human."
            )
        elif verdict in ("escalate", "veto"):
            parts.append(
                "The governance gate escalated the agent's intended action for human review."
            )
        elif verdict in ("allow", "permitted"):
            confidence = governance.get("agent_confidence")
            if isinstance(confidence, (int, float)):
                parts.append(
                    f"Governance allowed the action with {confidence * 100:.0f}% agent confidence."
                )

    confidence_gate = sources.get("confidence_gate") or {}
    blockers = confidence_gate.get("confidence_blockers") or []
    if blockers:
        parts.append(
            f"Field review is required because {len(blockers)} extracted "
            f"{'field' if len(blockers) == 1 else 'fields'} fell below the confidence floor."
        )

    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# Three-way match — best-effort, never blocks the detail load.
# ---------------------------------------------------------------------------

def _safe_three_way_match(
    db: Any, *, organization_id: str, ap_item_id: str, actor: str,
) -> Optional[Dict[str, Any]]:
    """Run the 3-way match if PO data is available; never raise.

    The match runner is a write path (it persists ``match_status`` on
    the AP item), but Module 2 spec asks for a *display* of the match
    on the detail page. The runner is idempotent for a given input and
    cheap when there's no PO context, so we run it on every detail
    load and surface the result. If anything fails we return None and
    the panel renders an empty state.
    """
    try:
        from solden.services.three_way_match_runner import run_three_way_match
        summary = run_three_way_match(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            actor=actor,
        )
        if summary is None:
            return None
        match = summary.to_dict()
        # Module 2 spec line 96: "deep links into ERP source records"
        # for PO/GR. Synthesize URLs once on the server based on the
        # primary ERP connection so the frontend just renders an <a>.
        po_number = match.get("po_number")
        gr_number = match.get("gr_number") or match.get("grn_number")
        if po_number or gr_number:
            from solden.api.workspace_shell import _resolve_erp_deep_link_id
            try:
                conns = db.get_erp_connections(organization_id) if hasattr(db, "get_erp_connections") else []
            except Exception:
                conns = []
            primary = next((c for c in (conns or []) if c.get("is_active", 1)), None) or (conns[0] if conns else None)
            if primary:
                erp_type = str(primary.get("erp_type") or "").strip().lower()
                deep_id = _resolve_erp_deep_link_id(primary) or ""
                base_url = str(primary.get("base_url") or "").strip().rstrip("/")
                match["erp_type"] = erp_type
                if po_number:
                    match["po_url"] = _build_erp_po_url(erp_type, deep_id, base_url, str(po_number))
                if gr_number:
                    match["gr_url"] = _build_erp_gr_url(erp_type, deep_id, base_url, str(gr_number))
        return match
    except Exception as exc:
        logger.debug("[ap_item_detail] three-way match skipped: %s", exc)
        return None


def _build_erp_po_url(erp_type: str, deep_id: str, base_url: str, po_number: str) -> Optional[str]:
    """Synthesize a deep-link URL into the ERP's purchase-order screen.

    Falls back to None when we don't have enough information to
    build a known-correct URL — frontend renders plain code in that
    case rather than a dead link.
    """
    from urllib.parse import quote
    po = quote(po_number, safe="")
    if erp_type == "quickbooks" and deep_id:
        return f"https://app.qbo.intuit.com/app/purchaseorder?txnId={po}"
    if erp_type == "xero":
        return f"https://go.xero.com/Accounts/Payable/PurchaseOrders/Edit/?id={po}"
    if erp_type == "netsuite" and deep_id:
        return f"https://{deep_id}.app.netsuite.com/app/accounting/transactions/purchord.nl?id={po}"
    if erp_type == "sap" and base_url:
        return f"{base_url}/sap/bc/ui2/flp#PurchaseOrder-display?PurchaseOrder={po}"
    return None


def _build_erp_gr_url(erp_type: str, deep_id: str, base_url: str, gr_number: str) -> Optional[str]:
    """Same as _build_erp_po_url but for goods-receipt records."""
    from urllib.parse import quote
    gr = quote(gr_number, safe="")
    if erp_type == "netsuite" and deep_id:
        return f"https://{deep_id}.app.netsuite.com/app/accounting/transactions/itemrcpt.nl?id={gr}"
    if erp_type == "sap" and base_url:
        return f"{base_url}/sap/bc/ui2/flp#GoodsMovement-display?MaterialDocument={gr}"
    if erp_type == "xero":
        return None  # Xero doesn't model GR as a separate record
    if erp_type == "quickbooks":
        return None  # QB folds GR into the PO close flow
    return None


# ---------------------------------------------------------------------------
# The endpoint.
# ---------------------------------------------------------------------------

class AskTheAgentRequest(BaseModel):
    """Body for POST /api/workspace/ap-items/{id}/ask."""
    question: str = Field(..., min_length=2, max_length=1000)


@router.post("/ap-items/{ap_item_id}/ask")
def ask_the_agent_endpoint(
    ap_item_id: str,
    request: AskTheAgentRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Module 2 spec line 100 — "Ask the agent" Q&A.

    The model is bounded to a structured context bundle around the
    requested invoice (item + vendor profile + last 20 vendor invoices
    + 3-way match + audit timeline). It cannot run other queries; it
    answers only what the bundle supports. Returns within 10-15s on a
    cache-warm Sonnet path.
    """
    organization_id = require_org(user)
    db = get_db()

    # Verify the item belongs to this tenant — _resolve_item_for_detail
    # already returns 404 (no membership leak) for cross-tenant ids.
    item = _resolve_item_for_detail(
        db, organization_id=organization_id, ap_item_ref=ap_item_id,
    )
    resolved_id = item.get("id") or ap_item_id

    from solden.services.ask_the_agent import ask_the_agent
    return ask_the_agent(
        db,
        organization_id=organization_id,
        ap_item_id=resolved_id,
        question=request.question,
    )


@router.get("/ap-items/{ap_item_id}/bank-match")
def get_ap_item_bank_match(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Bank reconciliation status for one AP item.

    Pulls ``payment_confirmations`` for the bill, then the matched
    ``bank_statement_lines`` linked to those confirmations. Returns
    a composite ``status`` the SPA renders on the record detail page:

      - ``no_payment``     — the bill hasn't been paid yet (no
                             confirmation rows). Bank match doesn't
                             apply at this stage.
      - ``awaiting_match`` — payment confirmed by the ERP but no
                             bank statement line has matched it yet.
                             Either no statement imported or the
                             matcher hasn't found the line.
      - ``matched``        — every confirmation has a matched/
                             reconciled bank line. Closes the loop.
      - ``ambiguous``      — at least one bank line is ``ambiguous``
                             or ``unmatched`` despite being linked to
                             a confirmation. Needs human review.
    """
    organization_id = require_org(user)
    db = get_db()

    # _resolve_item_for_detail enforces tenant isolation; cross-tenant
    # ids 404 without leaking membership.
    item = _resolve_item_for_detail(
        db, organization_id=organization_id, ap_item_ref=ap_item_id,
    )
    resolved_id = item.get("id") or ap_item_id

    confirmations: List[Dict[str, Any]] = []
    if hasattr(db, "list_payment_confirmations_for_ap_item"):
        try:
            confirmations = db.list_payment_confirmations_for_ap_item(
                organization_id, resolved_id,
            ) or []
        except Exception as exc:
            logger.warning(
                "[bank-match] confirmations lookup failed for %s: %s",
                resolved_id, exc,
            )

    confirmation_ids = [str(c.get("id")) for c in confirmations if c.get("id")]
    lines: List[Dict[str, Any]] = []
    if confirmation_ids and hasattr(db, "list_bank_statement_lines_for_confirmations"):
        try:
            lines = db.list_bank_statement_lines_for_confirmations(
                organization_id, confirmation_ids,
            ) or []
        except Exception as exc:
            logger.warning(
                "[bank-match] lines lookup failed for %s: %s",
                resolved_id, exc,
            )

    if not confirmations:
        status = "no_payment"
    elif not lines:
        status = "awaiting_match"
    else:
        clean_states = {"matched", "reconciled"}
        if all(str(l.get("match_status") or "").lower() in clean_states for l in lines):
            status = "matched"
        else:
            status = "ambiguous"

    def _compact_confirmation(c: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": c.get("id"),
            "source": c.get("source"),
            "payment_id": c.get("payment_id"),
            "status": c.get("status"),
            "amount": float(c["amount"]) if c.get("amount") is not None else None,
            "currency": c.get("currency"),
            "settlement_at": c.get("settlement_at"),
            "rail": c.get("rail"),
            "payment_reference": c.get("payment_reference"),
        }

    def _compact_line(line: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": line.get("id"),
            "import_id": line.get("import_id"),
            "value_date": line.get("value_date"),
            "amount": float(line["amount"]) if line.get("amount") is not None else None,
            "currency": line.get("currency"),
            "counterparty": line.get("counterparty"),
            "description": line.get("description"),
            "bank_reference": line.get("bank_reference"),
            "match_status": line.get("match_status"),
            "match_confidence": line.get("match_confidence"),
            "match_reason": line.get("match_reason"),
            "payment_confirmation_id": line.get("payment_confirmation_id"),
        }

    return {
        "status": status,
        "ap_item_id": resolved_id,
        "confirmations": [_compact_confirmation(c) for c in confirmations],
        "lines": [_compact_line(l) for l in lines],
    }


@router.get("/ap-items/{ap_item_id}/detail")
def get_ap_item_detail(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Consolidated detail payload for the workspace exception page.

    Returns 404 ``ap_item_not_found`` for both missing items and items
    in a different tenant — preserves the no-membership-leak guarantee
    in ``_resolve_item_for_detail``.
    """
    organization_id = require_org(user)
    db = get_db()

    item = _resolve_item_for_detail(
        db,
        organization_id=organization_id,
        ap_item_ref=ap_item_id,
    )
    enriched = build_worklist_item(db, item)
    resolved_id = enriched.get("id") or item.get("id") or ap_item_id

    metadata = item.get("metadata")
    if isinstance(metadata, str):
        metadata = _safe_json(metadata) or {}
    elif not isinstance(metadata, dict):
        metadata = {}

    raw_events = []
    try:
        raw_events = db.list_ap_audit_events(resolved_id, order="desc") or []
    except TypeError:
        # Older signature without ``order`` kwarg.
        raw_events = db.list_ap_audit_events(resolved_id) or []
    except Exception as exc:
        logger.warning("[ap_item_detail] timeline load failed for %s: %s", resolved_id, exc)
        raw_events = []
    timeline = normalize_operator_audit_events(raw_events)

    reasoning = _build_reasoning_payload(enriched, metadata, raw_events)

    match = _safe_three_way_match(
        db,
        organization_id=organization_id,
        ap_item_id=resolved_id,
        actor=getattr(user, "user_id", "workspace_detail"),
    )

    current_state = enriched.get("state") or item.get("state") or "received"
    actions = {
        "available": available_intents(current_state),
        "primary": primary_intent(
            current_state,
            (reasoning.get("agent_decision") or {}).get("recommendation"),
        ),
    }

    return {
        "item": enriched,
        "reasoning": reasoning,
        "match": match,
        "timeline": timeline,
        "actions": actions,
    }
