"""Needs-info recovery planner — activates the registered AGENT_PLANNING
LLM action that previously had zero call sites.

When the deterministic AP cascade routes an invoice to ``needs_info``,
the operator gets one prioritized question (``info_needed``) and the
item parks until the vendor responds. That works for the simple case,
but for items where multiple gaps overlap (extraction confidence + a
PO mismatch + a missing due date), the operator has to decide on the
fly: chase the vendor first? escalate? mark stale?

This service asks Sonnet for a short ordered recovery plan over a
finite menu of recovery actions:

    ask_vendor_followup
    escalate_to_ap_manager
    propose_resubmission
    request_specific_field
    auto_match_against_po
    wait_with_timer
    mark_disputed

Important boundaries:

  - The plan is **advisory** — it is persisted to AP item metadata as
    ``agent_recovery_plan`` and surfaced in operator tooling, but does
    NOT execute any action by itself. Routing decisions still belong
    to the deterministic cascade.
  - The action menu is closed. Any plan step naming an action outside
    the whitelist is dropped, not executed. The LLM cannot escape into
    arbitrary coordination actions through this surface.
  - On any failure (no API key, gateway timeout, parse error,
    everything-filtered), the planner returns ``None`` and the
    needs_info path behaves exactly as before. Never raises.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


RECOVERY_ACTION_WHITELIST = frozenset({
    # Vendor outreach — re-prompt for missing info or full resubmission.
    "ask_vendor_followup",
    "request_specific_field",
    "propose_resubmission",
    # Escalation — pull a human in.
    "escalate_to_ap_manager",
    # Re-evaluation of existing data.
    "auto_match_against_po",
    # Time-based parking.
    "wait_with_timer",
    # State change for stuck items.
    "mark_disputed",
})


@dataclass
class RecoveryStep:
    """One step in a recovery plan.

    Attributes:
        action: The recovery action name (must be in
            ``RECOVERY_ACTION_WHITELIST``).
        rationale: Why this step is being proposed for THIS invoice.
            Surfaces in operator tooling so the human knows what the
            agent is thinking before they accept the suggestion.
        params: Action-specific arguments — e.g. ``{"field":
            "due_date"}`` for ``request_specific_field``,
            ``{"hours": 48}`` for ``wait_with_timer``. Free-form so
            the schema can grow without renames.
        trigger_after_hours: Optional delay before this step fires.
            Step 0 typically has 0 (immediate). Later steps have a
            non-zero offset so the chain is "do X, then if no
            response in N hours do Y".
    """

    action: str
    rationale: str
    params: Dict[str, Any] = field(default_factory=dict)
    trigger_after_hours: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecoveryPlan:
    """The full ordered plan output."""

    summary: str
    steps: List[RecoveryStep]
    model: str = "agent_planning"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "model": self.model,
        }


_RECOVERY_PROMPT = """An accounts-payable invoice has been routed to needs_info by the deterministic rules cascade. \
Propose a short ordered recovery plan (1-3 steps) the AP team can execute to unblock it.

Invoice context:
- Vendor: {vendor}
- Amount: {currency} {amount:,.2f}
- Invoice number: {invoice_number}
- Due date: {due_date}
- Extraction confidence: {confidence:.2f}
- Risk flags from cascade: {risk_flags}

Routing reasoning from the cascade:
{reasoning}

Specific question the cascade wants asked (may be empty):
{info_needed}

Vendor history snapshot:
- Total prior invoices: {history_count}
- Average amount: {currency} {avg_amount:,.2f}
- Always approved before: {always_approved}

Available recovery actions (closed set — using anything outside this list will be dropped):
{action_menu}

Return JSON only:
{{
  "summary": "<one-sentence plan summary for the operator>",
  "steps": [
    {{
      "action": "<one of the actions above>",
      "rationale": "<why this step, tied to the specific gap>",
      "params": {{"field": "..." | "hours": N | etc.}},
      "trigger_after_hours": <0 for immediate, N for delayed>
    }}
  ]
}}

Constraints:
- 1 to 3 steps. More than 3 is a sign of overplanning.
- The first step ALWAYS has trigger_after_hours == 0.
- Use ``request_specific_field`` (with ``params.field``) for surgical missing-field asks. \
Use ``ask_vendor_followup`` for general re-prompts. Use ``propose_resubmission`` only when the document itself looks corrupted or unparseable.
- ``escalate_to_ap_manager`` is for items that are already stale or where vendor outreach would be inappropriate (high amount, fraud signals, vendor unresponsive).
- ``wait_with_timer`` is a common second step — "do X, if no response in 48h, escalate".
- No prose outside the JSON."""


async def propose_recovery_plan(
    invoice: Any,
    ap_decision: Any,
    vendor_profile: Optional[Dict[str, Any]] = None,
) -> Optional[RecoveryPlan]:
    """Propose an ordered recovery plan for a needs_info AP item.

    Returns ``None`` if the LLM is unavailable, the response can't be
    parsed, or every proposed step is filtered out by the whitelist.
    The needs_info path falls back to the existing single-question
    behavior in that case.
    """
    if not invoice or not ap_decision:
        return None
    if getattr(ap_decision, "recommendation", "") != "needs_info":
        return None

    vendor_profile = vendor_profile or {}
    action_menu = ", ".join(sorted(RECOVERY_ACTION_WHITELIST))
    risk_flags = list(getattr(ap_decision, "risk_flags", None) or [])
    risk_flags_str = ", ".join(risk_flags) if risk_flags else "(none)"

    prompt = _RECOVERY_PROMPT.format(
        vendor=getattr(invoice, "vendor_name", "unknown") or "unknown",
        currency=getattr(invoice, "currency", "USD") or "USD",
        amount=float(getattr(invoice, "amount", 0) or 0),
        invoice_number=getattr(invoice, "invoice_number", None) or "(missing)",
        due_date=getattr(invoice, "due_date", None) or "(missing)",
        confidence=float(getattr(invoice, "confidence", 0) or 0),
        risk_flags=risk_flags_str,
        reasoning=(getattr(ap_decision, "reasoning", "") or "")[:500],
        info_needed=(getattr(ap_decision, "info_needed", "") or "(none)")[:300],
        history_count=int(vendor_profile.get("invoice_count") or 0),
        avg_amount=float(vendor_profile.get("avg_invoice_amount") or 0.0),
        always_approved=bool(vendor_profile.get("always_approved")),
        action_menu=action_menu,
    )

    try:
        from clearledgr.core.llm_gateway import LLMAction, get_llm_gateway

        gateway = get_llm_gateway()
        resp = await gateway.call(
            LLMAction.AGENT_PLANNING,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content if isinstance(resp.content, str) else ""
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
    except Exception as exc:
        logger.debug(
            "[needs_info_recovery] AGENT_PLANNING call failed (advisory plan skipped): %s",
            exc,
        )
        return None

    summary = str(parsed.get("summary") or "").strip()
    raw_steps = parsed.get("steps") or []
    if not isinstance(raw_steps, list) or not summary:
        return None

    valid_steps: List[RecoveryStep] = []
    for raw in raw_steps[:3]:  # hard cap regardless of what the LLM returns
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()
        if action not in RECOVERY_ACTION_WHITELIST:
            logger.debug(
                "[needs_info_recovery] dropped step with out-of-whitelist action %r", action,
            )
            continue
        rationale = str(raw.get("rationale") or "").strip()
        if not rationale:
            continue
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            trigger = int(raw.get("trigger_after_hours") or 0)
        except (TypeError, ValueError):
            trigger = 0
        # Cap the trigger delay so we don't end up with a year-long
        # parking lot on a stuck item. 14 days is the longest
        # reasonable recovery delay; anything bigger means the item
        # should escalate or be marked stale instead.
        trigger = max(0, min(trigger, 14 * 24))
        valid_steps.append(RecoveryStep(
            action=action,
            rationale=rationale,
            params=params,
            trigger_after_hours=trigger,
        ))

    if not valid_steps:
        return None

    # The first step is always immediate. If the LLM returned a delayed
    # first step, normalise it so the chain reads "do X now, then maybe Y".
    if valid_steps[0].trigger_after_hours != 0:
        valid_steps[0].trigger_after_hours = 0

    return RecoveryPlan(summary=summary, steps=valid_steps)
