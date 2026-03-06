"""AP Decision Service — LLM-based invoice routing.

Replaces hardcoded confidence-threshold routing with Claude reasoning.
Claude receives full vendor context (history, patterns, policy) and decides
what to do with an invoice.  The AP state machine guardrails remain
unchanged — this service produces the *input* recommendation, not the
final state transition.

Decision path:
  APDecisionService.decide()
    → assembles vendor context from VendorStore + CorrectionLearningService
    → calls Claude Sonnet with a structured reasoning prompt
    → returns APDecision(recommendation, reasoning, risk_flags, ...)

Fallback path (no API key, Claude timeout, parse error):
  _fallback_decision() → reproduces existing rule-based logic wrapped in APDecision
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

_VALID_RECOMMENDATIONS = {"approve", "needs_info", "escalate", "reject"}


@dataclass
class APDecision:
    """Structured output from APDecisionService.decide()."""

    recommendation: str           # "approve" | "needs_info" | "escalate" | "reject"
    reasoning: str                 # 2-3 sentence explanation (shown in Gmail/Slack)
    confidence: float              # 0.0-1.0 — Claude's confidence in its decision
    info_needed: Optional[str]     # if needs_info: exact question to send vendor
    risk_flags: List[str]          # anomaly signals detected
    vendor_context_used: Dict[str, Any]  # summary of vendor data consulted
    model: str                     # which Claude model (or "fallback")
    fallback: bool = False         # True when rule-based fallback was used


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _days_since(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso[:19].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _format_history_row(h: Dict[str, Any]) -> str:
    date = (h.get("invoice_date") or h.get("created_at") or "")[:10]
    amt = h.get("amount")
    amt_str = f"${amt:.2f}" if amt is not None else "unknown"
    state = h.get("final_state") or "pending"
    exc = h.get("exception_code")
    exc_str = f" [{exc}]" if exc else " [clean]"
    return f"  {date} | {amt_str} | {state}{exc_str}"


_FEW_SHOT_EXAMPLES = """EXAMPLES (learn from these, do NOT copy reasoning verbatim):

Example 1 — approve
Scenario: Acme Hosting, 24 prior invoices, avg $450, stddev $30. Invoice is $461. Gate passed. Confidence 97%.
Output:
{"recommendation":"approve","reasoning":"Acme Hosting has 24 clean invoices with an average of $450 (±$30). This $461 invoice falls within the expected range and all validation gates passed with 97% extraction confidence. Safe to approve autonomously.","confidence":0.97,"info_needed":null,"risk_flags":[]}

Example 2 — escalate
Scenario: FastShip Logistics, bank details changed 12 days ago. Invoice $8,200, slightly above avg of $7,800. Gate passed.
Output:
{"recommendation":"escalate","reasoning":"FastShip Logistics changed bank/payment details 12 days ago — this is a significant fraud indicator. Even though the amount is within normal range and the validation gate passed, a recent banking change requires human sign-off before any payment proceeds.","confidence":0.92,"info_needed":null,"risk_flags":["bank_details_changed"]}

Example 3 — needs_info
Scenario: DevTools Inc, requires_po=true, no PO number on invoice. Amount $2,100. Gate failed: po_required_missing.
Output:
{"recommendation":"needs_info","reasoning":"DevTools Inc requires a purchase order reference per org policy, but this invoice does not include one. Cannot approve or route without the PO number.","confidence":0.88,"info_needed":"Please provide the purchase order number for invoice #INV-2041 so we can proceed with payment.","risk_flags":["po_required_missing"]}

---
"""


def _build_reasoning_prompt(
    invoice: Any,  # InvoiceData
    vendor_profile: Optional[Dict[str, Any]],
    vendor_history: List[Dict[str, Any]],
    decision_feedback: Dict[str, Any],
    correction_suggestions: Dict[str, Any],
    validation_gate: Dict[str, Any],
    org_config: Dict[str, Any],
) -> str:
    """Assemble the full reasoning prompt for Claude."""

    org_name = org_config.get("name") or org_config.get("organization_id") or "your organisation"

    # ---- Vendor section ----
    vendor_lines = [f"VENDOR: {invoice.vendor_name}"]

    if vendor_profile:
        count = vendor_profile.get("invoice_count") or 0
        avg = vendor_profile.get("avg_invoice_amount")
        stddev = vendor_profile.get("amount_stddev")
        always_approved = bool(vendor_profile.get("always_approved"))
        bank_changed_at = vendor_profile.get("bank_details_changed_at")
        typical_day = vendor_profile.get("typical_invoice_day")
        requires_po = bool(vendor_profile.get("requires_po"))

        if count:
            avg_str = f"${avg:.2f}" if avg else "unknown"
            vendor_lines.append(f"History: {count} invoice(s) processed, avg {avg_str}")
            if stddev and avg:
                vendor_lines.append(f"Typical amount range: ${avg - 2*stddev:.2f} – ${avg + 2*stddev:.2f}")
        else:
            vendor_lines.append("History: first invoice from this vendor")

        if always_approved and count >= 3:
            vendor_lines.append("Pattern: always approved historically — no exceptions in last run.")

        bank_days = _days_since(bank_changed_at)
        if bank_days is not None and bank_days <= 30:
            vendor_lines.append(
                f"⚠ RISK: Bank/payment details changed {bank_days} day(s) ago. Treat with caution."
            )

        if typical_day:
            vendor_lines.append(f"Typical invoice day: {typical_day} of the month")

        if requires_po:
            vendor_lines.append("Policy: PO reference required for this vendor")
    else:
        vendor_lines.append("History: no prior invoices — first time seen")

    # ---- Invoice history ----
    history_section = ""
    if vendor_history:
        rows = [_format_history_row(h) for h in vendor_history[:6]]
        history_section = "RECENT INVOICES (last {}):\n{}".format(
            len(rows), "\n".join(rows)
        )

    # ---- Learned patterns ----
    learned_lines = []
    gl_sug = correction_suggestions.get("gl_code")
    if gl_sug:
        learned_lines.append(
            f"Suggested GL code: {gl_sug.get('value')} "
            f"(confidence {gl_sug.get('confidence', 0):.0%}, "
            f"from {gl_sug.get('learned_from', 0)} correction(s))"
        )
    learned_section = ("LEARNED PATTERNS:\n" + "\n".join(learned_lines)) if learned_lines else ""

    # ---- Human feedback loop ----
    feedback_section = ""
    total_feedback = int(decision_feedback.get("total_feedback") or 0)
    if total_feedback > 0:
        strictness = str(decision_feedback.get("strictness_bias") or "neutral")
        override_rate = float(decision_feedback.get("override_rate") or 0.0)
        feedback_lines = [
            "HUMAN DECISION FEEDBACK (recent):",
            f"- Decisions logged: {total_feedback}",
            (
                f"- Approve: {int(decision_feedback.get('approve_count') or 0)}, "
                f"Reject: {int(decision_feedback.get('reject_count') or 0)}, "
                f"Request info: {int(decision_feedback.get('request_info_count') or 0)}"
            ),
            f"- Human override rate vs agent recommendation: {override_rate:.0%}",
            f"- Bias: {strictness}",
        ]
        reject_after_approve = int(decision_feedback.get("reject_after_approve_count") or 0)
        request_info_after_approve = int(decision_feedback.get("request_info_after_approve_count") or 0)
        if reject_after_approve or request_info_after_approve:
            feedback_lines.append(
                "- Pattern when agent suggested approve: "
                f"{reject_after_approve} reject / {request_info_after_approve} request-info outcomes"
            )
        recent_reasons = decision_feedback.get("recent_reasons")
        if isinstance(recent_reasons, list) and recent_reasons:
            feedback_lines.append("- Recent reasons: " + " | ".join(str(r) for r in recent_reasons[:3]))
        feedback_section = "\n".join(feedback_lines)

    # ---- Org policy ----
    po_required = org_config.get("po_required", False)
    auto_threshold = org_config.get("auto_approve_confidence_threshold", 0.95)
    approval_amount = org_config.get("approval_required_above_amount")
    policy_lines = [
        f"PO required: {'yes' if po_required else 'no'}",
        f"Auto-approve confidence threshold: {auto_threshold:.0%}",
    ]
    if approval_amount:
        policy_lines.append(f"Human approval required for amounts above: ${approval_amount:.2f}")

    # ---- Validation gate ----
    gate_passed = validation_gate.get("passed", True)
    reason_codes = validation_gate.get("reason_codes") or []
    gate_str = "PASSED" if gate_passed else f"FAILED — {', '.join(reason_codes)}"

    # ---- Field confidences ----
    fc = invoice.field_confidences or {}
    fc_lines = []
    for f_name, label in (
        ("vendor", "Vendor"), ("amount", "Amount"),
        ("invoice_number", "Invoice #"), ("due_date", "Due date"),
    ):
        val = _safe_float(fc.get(f_name))
        if val is not None:
            fc_lines.append(f"  {label}: {val:.0%}")

    # ---- Current invoice ----
    invoice_lines = [
        f"Amount: ${invoice.amount} {invoice.currency}" + (f" — confidence {_safe_float(fc.get('amount')):.0%}" if fc.get('amount') else ""),
        f"Vendor: {invoice.vendor_name}" + (f" — confidence {_safe_float(fc.get('vendor')):.0%}" if fc.get('vendor') else ""),
        f"Invoice #: {invoice.invoice_number or 'missing'}" + (f" — confidence {_safe_float(fc.get('invoice_number')):.0%}" if fc.get('invoice_number') else ""),
        f"Due: {invoice.due_date or 'missing'}",
        f"PO ref: {invoice.po_number or 'none'}",
        f"Subject: {invoice.subject}",
    ]

    sections = [
        f"You are the AP agent for {org_name}.",
        "\n".join(vendor_lines),
    ]
    if history_section:
        sections.append(history_section)
    if feedback_section:
        sections.append(feedback_section)
    if learned_section:
        sections.append(learned_section)
    sections.append("ORG POLICY:\n" + "\n".join(policy_lines))
    sections.append(f"VALIDATION GATE: {gate_str}")
    sections.append("CURRENT INVOICE:\n" + "\n".join(invoice_lines))
    if fc_lines:
        sections.append("FIELD CONFIDENCE SCORES:\n" + "\n".join(fc_lines))

    sections.append(_FEW_SHOT_EXAMPLES + """---
Decide what to do with this invoice. Choose exactly one action:
- approve: safe to proceed autonomously, all signals are green
- needs_info: I need specific information from the vendor before proceeding (state exactly what)
- escalate: a human must review this — state why clearly
- reject: clear problem with evidence — state reason and evidence

Consider:
1. Does the amount match the vendor's historical pattern (within ~2 standard deviations)?
2. Are there anomaly signals (bank details changed, amount spike, duplicate timing, missing required fields)?
3. Did the validation gate pass? If not, what are the reason codes?
4. Is field confidence sufficient for autonomous action (>= 95% for critical fields)?
5. Is this vendor always approved with no anomalies? If so, lean toward approve unless a risk flag is present.
6. Is a PO required but missing?
7. Respect recent human feedback patterns for this tenant/vendor (strict/permissive bias), but never bypass deterministic policy gates.

Return ONLY valid JSON — no prose, no markdown fences:
{"recommendation":"approve|needs_info|escalate|reject","reasoning":"2-3 sentences explaining your decision, referencing specific vendor history or signals","confidence":0.0,"info_needed":null,"risk_flags":[]}""")

    return "\n\n".join(s for s in sections if s)


class APDecisionService:
    """LLM-based AP invoice routing using Claude with full vendor context."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def decide(
        self,
        invoice: Any,  # InvoiceData
        *,
        vendor_profile: Optional[Dict[str, Any]] = None,
        vendor_history: Optional[List[Dict[str, Any]]] = None,
        decision_feedback: Optional[Dict[str, Any]] = None,
        correction_suggestions: Optional[Dict[str, Any]] = None,
        validation_gate: Optional[Dict[str, Any]] = None,
        org_config: Optional[Dict[str, Any]] = None,
    ) -> APDecision:
        """Call Claude with vendor context to decide how to route this invoice.

        Returns APDecision.  Never raises — falls back to rule-based decision
        if the API is unavailable or the call fails.
        """
        vendor_profile = vendor_profile or {}
        vendor_history = vendor_history or []
        decision_feedback = decision_feedback or {}
        correction_suggestions = correction_suggestions or {}
        validation_gate = validation_gate or {"passed": True, "reason_codes": []}
        org_config = org_config or {}

        vendor_context_used = {
            "invoice_count": vendor_profile.get("invoice_count", 0),
            "avg_invoice_amount": vendor_profile.get("avg_invoice_amount"),
            "always_approved": bool(vendor_profile.get("always_approved")),
            "bank_details_changed_at": vendor_profile.get("bank_details_changed_at"),
            "requires_po": bool(vendor_profile.get("requires_po")),
            "history_rows_used": len(vendor_history),
            "feedback_count": int(decision_feedback.get("total_feedback") or 0),
            "feedback_override_rate": float(decision_feedback.get("override_rate") or 0.0),
            "feedback_strictness_bias": str(decision_feedback.get("strictness_bias") or "neutral"),
        }

        if not self._api_key:
            logger.info("[APDecision] No API key — using rule-based fallback")
            return self._fallback_decision(
                invoice,
                validation_gate,
                vendor_context_used,
                decision_feedback=decision_feedback,
            )

        try:
            prompt = _build_reasoning_prompt(
                invoice=invoice,
                vendor_profile=vendor_profile,
                vendor_history=vendor_history,
                decision_feedback=decision_feedback,
                correction_suggestions=correction_suggestions,
                validation_gate=validation_gate,
                org_config=org_config,
            )
            raw = self._call_claude(prompt)
            return self._parse_response(raw, vendor_context_used)
        except Exception as exc:
            logger.warning("[APDecision] Claude call failed (%s) — using rule-based fallback", exc)
            result = self._fallback_decision(
                invoice,
                validation_gate,
                vendor_context_used,
                decision_feedback=decision_feedback,
            )
            result.fallback = True
            return result

    def _call_claude(self, prompt: str) -> Dict[str, Any]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": _MODEL,
            "max_tokens": 512,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = requests.post(_API_URL, headers=headers, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _parse_response(
        self, data: Dict[str, Any], vendor_context_used: Dict[str, Any]
    ) -> APDecision:
        content = data.get("content", [])
        if isinstance(content, list):
            text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            text = str(content or "")

        # Strip markdown fences
        text = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if fence:
            text = fence.group(1)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            obj = re.search(r"\{[\s\S]+?\}", text)
            if obj:
                try:
                    parsed = json.loads(obj.group(0))
                except json.JSONDecodeError:
                    raise ValueError(f"Claude did not return valid JSON: {text[:200]}")
            else:
                raise ValueError(f"Claude did not return valid JSON: {text[:200]}")

        rec = str(parsed.get("recommendation") or "escalate").lower().strip()
        if rec not in _VALID_RECOMMENDATIONS:
            rec = "escalate"

        return APDecision(
            recommendation=rec,
            reasoning=str(parsed.get("reasoning") or "No reasoning provided."),
            confidence=_safe_float(parsed.get("confidence")) or 0.0,
            info_needed=parsed.get("info_needed") or None,
            risk_flags=[str(f) for f in (parsed.get("risk_flags") or [])],
            vendor_context_used=vendor_context_used,
            model=_MODEL,
            fallback=False,
        )

    def _fallback_decision(
        self,
        invoice: Any,
        validation_gate: Dict[str, Any],
        vendor_context_used: Optional[Dict[str, Any]] = None,
        decision_feedback: Optional[Dict[str, Any]] = None,
    ) -> APDecision:
        """Rule-based decision that replicates the existing routing logic.

        Used when Claude is unavailable, so the workflow is never blocked.
        """
        gate_passed = validation_gate.get("passed", True)
        reason_codes = validation_gate.get("reason_codes") or []
        confidence = _safe_float(getattr(invoice, "confidence", None)) or 0.0
        decision_feedback = decision_feedback or {}
        strictness_bias = str(decision_feedback.get("strictness_bias") or "neutral").strip().lower()
        has_strict_feedback = strictness_bias == "strict" and int(decision_feedback.get("total_feedback") or 0) >= 3

        # Check for a missing PO on a PO-required vendor
        po_required = "po_required_missing" in reason_codes
        if po_required and not getattr(invoice, "po_number", None):
            return APDecision(
                recommendation="needs_info",
                reasoning=(
                    f"PO reference is required for {invoice.vendor_name} but was not found in this invoice. "
                    "Requesting the PO number from the vendor before proceeding."
                ),
                confidence=0.85,
                info_needed=(
                    f"Could you please provide the purchase order number for invoice "
                    f"{getattr(invoice, 'invoice_number', '') or 'this invoice'}?"
                ),
                risk_flags=["po_required_missing"],
                vendor_context_used=vendor_context_used or {},
                model="fallback",
                fallback=True,
            )

        if not gate_passed:
            return APDecision(
                recommendation="escalate",
                reasoning=(
                    f"Validation gate failed for {invoice.vendor_name}: "
                    f"{', '.join(reason_codes) or 'unknown reason'}. Human review required."
                ),
                confidence=0.90,
                info_needed=None,
                risk_flags=list(reason_codes),
                vendor_context_used=vendor_context_used or {},
                model="fallback",
                fallback=True,
            )

        if gate_passed and has_strict_feedback and confidence >= 0.95:
            return APDecision(
                recommendation="escalate",
                reasoning=(
                    f"Recent human feedback for {invoice.vendor_name} is strict "
                    "(frequent reject/request-info outcomes), so this invoice is routed "
                    "for human review despite high extraction confidence."
                ),
                confidence=max(0.8, confidence - 0.1),
                info_needed=None,
                risk_flags=["human_feedback_strict_bias"],
                vendor_context_used=vendor_context_used or {},
                model="fallback",
                fallback=True,
            )

        if confidence >= 0.95:
            return APDecision(
                recommendation="approve",
                reasoning=(
                    f"All validation gates passed and extraction confidence is {confidence:.0%} "
                    f"for {invoice.vendor_name} ${getattr(invoice, 'amount', 0):.2f}. "
                    "Safe to proceed autonomously."
                ),
                confidence=confidence,
                info_needed=None,
                risk_flags=[],
                vendor_context_used=vendor_context_used or {},
                model="fallback",
                fallback=True,
            )

        return APDecision(
            recommendation="escalate",
            reasoning=(
                f"Extraction confidence is {confidence:.0%} — below the 95% threshold "
                f"for autonomous approval of {invoice.vendor_name} "
                f"${getattr(invoice, 'amount', 0):.2f}. Routing to human review."
            ),
            confidence=confidence,
            info_needed=None,
            risk_flags=["low_extraction_confidence"],
            vendor_context_used=vendor_context_used or {},
            model="fallback",
            fallback=True,
        )
