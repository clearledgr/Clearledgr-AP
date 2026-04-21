"""AP Decision Service — deterministic invoice routing.

The deck promises: rules decide, LLM describes. No financial write is at
the mercy of model judgment. This service is the rules half of that
promise — `APDecisionService.decide()` computes the routing recommendation
(approve | needs_info | escalate | reject) from a fixed 10-step policy
cascade over validation gate, vendor history, anomaly signals, and org
config. Claude is **not** called here; narrative description belongs to
spec §7.1 actions (`generate_exception_reason`, `draft_vendor_response`)
that run from exception and outreach surfaces, not from inside the
routing decision.

`enforce_gate_constraint` remains as a defensive no-op: the rule cascade
cannot emit `approve` on a failed gate, but the helper stays so any future
upstream that bypasses the cascade still cannot route `approve` past a
failed gate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.utils import safe_float_or_none

logger = logging.getLogger(__name__)

_VALID_RECOMMENDATIONS = {"approve", "needs_info", "escalate", "reject"}


@dataclass
class APDecision:
    """Structured output from APDecisionService.decide()."""

    recommendation: str           # "approve" | "needs_info" | "escalate" | "reject"
    reasoning: str                 # 2-3 sentence explanation (shown in Gmail/Slack)
    confidence: float              # 0.0-1.0 — confidence in the routing decision
    info_needed: Optional[str]     # if needs_info: exact question to send vendor
    risk_flags: List[str]          # anomaly signals detected
    vendor_context_used: Dict[str, Any]  # summary of vendor data consulted
    model: str                     # routing source; always "rules" post-Phase 4
    fallback: bool = False         # retained for schema compat; always False
    gate_override: bool = False    # True if enforce_gate_constraint overrode
    original_recommendation: Optional[str] = None  # original rec, if overridden


_VALID_WHEN_GATE_FAILED = frozenset({"escalate", "needs_info", "reject"})


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


def compute_vendor_risk_score(
    vendor_profile: Optional[Dict[str, Any]] = None,
    cross_invoice_analysis: Optional[Dict[str, Any]] = None,
    anomaly_signals: Optional[Dict[str, Any]] = None,
    decision_feedback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute a composite vendor risk score from 0.0 (safe) to 1.0 (high risk).

    Components (each 0.0-1.0, weighted):
      - vendor_familiarity (0.30): new vendors are riskier
      - duplicate_risk    (0.25): from cross-invoice analysis
      - anomaly_risk      (0.20): amount/volume anomalies
      - override_risk     (0.15): high human override rate
      - bank_change_risk  (0.10): recent bank detail changes
    """
    vendor_profile = vendor_profile or {}
    cross_invoice_analysis = cross_invoice_analysis or {}
    anomaly_signals = anomaly_signals or {}
    decision_feedback = decision_feedback or {}

    scores: Dict[str, float] = {}
    flags: list = []

    # 1. Vendor familiarity (new = risky)
    invoice_count = int(vendor_profile.get("invoice_count") or 0)
    if invoice_count == 0:
        scores["vendor_familiarity"] = 1.0
        flags.append("new_vendor")
    elif invoice_count < 3:
        scores["vendor_familiarity"] = 0.6
        flags.append("low_history")
    else:
        scores["vendor_familiarity"] = 0.0

    # 2. Duplicate risk
    duplicates = cross_invoice_analysis.get("duplicates") or []
    if any(d.get("severity") == "high" for d in duplicates):
        scores["duplicate_risk"] = 1.0
        flags.append("high_duplicate_match")
    elif duplicates:
        scores["duplicate_risk"] = 0.5
        flags.append("possible_duplicate")
    else:
        scores["duplicate_risk"] = 0.0

    # 3. Anomaly risk
    anomalies = cross_invoice_analysis.get("anomalies") or []
    volume_anomaly = anomaly_signals.get("volume", {})
    if any(a.get("severity") == "high" for a in anomalies) or volume_anomaly.get("is_anomaly"):
        scores["anomaly_risk"] = 1.0
        flags.append("amount_anomaly")
    elif anomalies:
        scores["anomaly_risk"] = 0.4
    else:
        scores["anomaly_risk"] = 0.0

    # 4. Override risk (humans keep disagreeing with agent)
    override_rate = float(decision_feedback.get("override_rate") or 0.0)
    if override_rate >= 0.4:
        scores["override_risk"] = 1.0
        flags.append("high_override_rate")
    elif override_rate >= 0.2:
        scores["override_risk"] = 0.5
    else:
        scores["override_risk"] = 0.0

    # 5. Bank change recency
    bank_days = _days_since(vendor_profile.get("bank_details_changed_at"))
    if bank_days is not None and bank_days <= 14:
        scores["bank_change_risk"] = 1.0
        flags.append("recent_bank_change")
    elif bank_days is not None and bank_days <= 30:
        scores["bank_change_risk"] = 0.5
        flags.append("bank_change_30d")
    else:
        scores["bank_change_risk"] = 0.0

    # Weighted composite
    weights = {
        "vendor_familiarity": 0.30,
        "duplicate_risk": 0.25,
        "anomaly_risk": 0.20,
        "override_risk": 0.15,
        "bank_change_risk": 0.10,
    }
    composite = sum(scores.get(k, 0) * w for k, w in weights.items())

    return {
        "score": round(composite, 3),
        "components": scores,
        "flags": flags,
        "level": "high" if composite >= 0.7 else "medium" if composite >= 0.4 else "low",
    }


def enforce_gate_constraint(
    decision: APDecision,
    validation_gate: Optional[Dict[str, Any]],
) -> APDecision:
    """Defensive no-op since Phase 4: the rule cascade cannot emit
    `approve` on a failed gate. The helper stays so any future upstream
    that bypasses the cascade still cannot route `approve` past a failed
    gate — the hard backstop for §7.6.
    """
    if validation_gate is None:
        return decision

    gate_passed = bool(validation_gate.get("passed", True))
    if gate_passed:
        return decision

    if decision.recommendation in _VALID_WHEN_GATE_FAILED:
        return decision

    reason_codes = validation_gate.get("reason_codes") or []
    reason_codes_str = ", ".join(str(c) for c in reason_codes) if reason_codes else "unknown"
    override_reason = (
        f"Deterministic validation gate failed ({reason_codes_str}); "
        f"'{decision.recommendation}' is not a valid outcome when the gate "
        "has not passed. Routed to human review per §7.6 architectural "
        "constraint."
    )

    logger.warning(
        "[APDecision] Gate override applied upstream of rules: recommendation "
        "'%s' with failed gate codes %s. Forcing 'escalate'. Original "
        "reasoning: %s",
        decision.recommendation,
        reason_codes,
        (decision.reasoning or "")[:200],
    )

    return APDecision(
        recommendation="escalate",
        reasoning=override_reason,
        confidence=decision.confidence,
        info_needed=None,
        risk_flags=list(decision.risk_flags) + ["gate_override_applied"],
        vendor_context_used=decision.vendor_context_used,
        model=decision.model,
        fallback=decision.fallback,
        gate_override=True,
        original_recommendation=decision.recommendation,
    )


class APDecisionService:
    """Deterministic AP invoice routing.

    Post-Phase 4, this service does not call Claude. The 10-step policy
    cascade in `_compute_routing_decision` is the single source of
    routing truth.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        # api_key kept for signature compatibility; no longer used.
        _ = api_key

    @property
    def is_available(self) -> bool:
        """Always True — rules are always available. Retained for callers."""
        return True

    async def decide(
        self,
        invoice: Any,  # InvoiceData
        *,
        vendor_profile: Optional[Dict[str, Any]] = None,
        vendor_history: Optional[List[Dict[str, Any]]] = None,
        decision_feedback: Optional[Dict[str, Any]] = None,
        correction_suggestions: Optional[Dict[str, Any]] = None,
        validation_gate: Optional[Dict[str, Any]] = None,
        org_config: Optional[Dict[str, Any]] = None,
        cross_invoice_analysis: Optional[Dict[str, Any]] = None,
        anomaly_signals: Optional[Dict[str, Any]] = None,
        vendor_risk_score: Optional[Dict[str, Any]] = None,
        box_summary: Optional[str] = None,
    ) -> APDecision:
        """Compute the routing recommendation deterministically. Never raises."""
        vendor_profile = vendor_profile or {}
        vendor_history = vendor_history or []
        decision_feedback = decision_feedback or {}
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
            "has_duplicate_alerts": bool(
                cross_invoice_analysis and cross_invoice_analysis.get("has_issues")
            ),
            "vendor_risk_level": (vendor_risk_score or {}).get("level", "unknown"),
        }

        decision = self._compute_routing_decision(
            invoice,
            validation_gate,
            vendor_context_used,
            decision_feedback=decision_feedback,
            vendor_risk_score=vendor_risk_score,
            vendor_profile=vendor_profile,
            cross_invoice_analysis=cross_invoice_analysis,
            org_config=org_config,
        )
        return enforce_gate_constraint(decision, validation_gate)

    def _compute_routing_decision(
        self,
        invoice: Any,
        validation_gate: Dict[str, Any],
        vendor_context_used: Optional[Dict[str, Any]] = None,
        decision_feedback: Optional[Dict[str, Any]] = None,
        vendor_risk_score: Optional[Dict[str, Any]] = None,
        vendor_profile: Optional[Dict[str, Any]] = None,
        cross_invoice_analysis: Optional[Dict[str, Any]] = None,
        org_config: Optional[Dict[str, Any]] = None,
    ) -> APDecision:
        """Ten-step policy cascade. The single source of routing truth."""
        gate_passed = validation_gate.get("passed", True)
        reason_codes = validation_gate.get("reason_codes") or []
        confidence = safe_float_or_none(getattr(invoice, "confidence", None)) or 0.0
        decision_feedback = decision_feedback or {}
        vendor_profile = vendor_profile or {}
        cross_invoice_analysis = cross_invoice_analysis or {}
        org_config = org_config or {}
        try:
            from clearledgr.services.adaptive_thresholds import get_adaptive_threshold_service
            auto_threshold = get_adaptive_threshold_service(
                org_config.get("organization_id", "default")
            ).get_threshold_for_vendor(invoice.vendor_name)
        except Exception:
            auto_threshold = float(org_config.get("auto_approve_confidence_threshold", 0.95))
        strictness_bias = str(decision_feedback.get("strictness_bias") or "neutral").strip().lower()
        has_strict_feedback = strictness_bias == "strict" and int(decision_feedback.get("total_feedback") or 0) >= 3

        # Step 1: PO required but missing → needs_info
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
                model="rules",
            )

        # Step 2: Validation gate failed → escalate
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
                model="rules",
            )

        # Step 3: Bank details changed within 30 days → fraud signal → escalate
        bank_changed_at = vendor_profile.get("bank_details_changed_at")
        if bank_changed_at:
            days_since_change = _days_since(bank_changed_at)
            if days_since_change is not None and days_since_change <= 30:
                return APDecision(
                    recommendation="escalate",
                    reasoning=(
                        f"Bank account details for {invoice.vendor_name} were changed "
                        f"{days_since_change} day(s) ago — a potential fraud signal. "
                        "Routing to human review."
                    ),
                    confidence=min(1.0, max(0.7, confidence - 0.2)),
                    info_needed=None,
                    risk_flags=["bank_details_recently_changed"],
                    vendor_context_used=vendor_context_used or {},
                    model="rules",
                )

        # Step 4: Strict human feedback bias → escalate
        if gate_passed and has_strict_feedback and confidence >= auto_threshold:
            return APDecision(
                recommendation="escalate",
                reasoning=(
                    f"Recent human feedback for {invoice.vendor_name} is strict "
                    "(frequent reject/request-info outcomes), so this invoice is routed "
                    "for human review despite high extraction confidence."
                ),
                confidence=min(1.0, max(0.8, confidence - 0.1)),
                info_needed=None,
                risk_flags=["human_feedback_strict_bias"],
                vendor_context_used=vendor_context_used or {},
                model="rules",
            )

        # Step 5: High vendor risk score → escalate
        risk_level = (vendor_risk_score or {}).get("level", "low")
        risk_flags_from_score = (vendor_risk_score or {}).get("flags") or []
        if risk_level == "high":
            return APDecision(
                recommendation="escalate",
                reasoning=(
                    f"Vendor risk score is high for {invoice.vendor_name} "
                    f"(flags: {', '.join(risk_flags_from_score)}). "
                    "Routing to human review regardless of extraction confidence."
                ),
                confidence=min(1.0, max(0.7, confidence - 0.15)),
                info_needed=None,
                risk_flags=risk_flags_from_score,
                vendor_context_used=vendor_context_used or {},
                model="rules",
            )

        # Step 6: Duplicate invoice detected → escalate
        cross_duplicates = cross_invoice_analysis.get("duplicates") or []
        if cross_duplicates:
            return APDecision(
                recommendation="escalate",
                reasoning=(
                    f"Duplicate invoice signal detected for {invoice.vendor_name} "
                    f"(${getattr(invoice, 'amount', 0):.2f}). "
                    "Routing to human review to confirm this is not a re-submission."
                ),
                confidence=min(1.0, max(0.7, confidence - 0.1)),
                info_needed=None,
                risk_flags=["duplicate_invoice_detected"],
                vendor_context_used=vendor_context_used or {},
                model="rules",
            )

        # Step 7: Amount >2σ from vendor historical average → escalate
        avg = safe_float_or_none(vendor_profile.get("avg_invoice_amount"))
        stddev = safe_float_or_none(vendor_profile.get("amount_stddev"))
        current_amount = safe_float_or_none(getattr(invoice, "amount", None)) or 0.0
        if avg is not None and stddev is not None and stddev > 0:
            if abs(current_amount - avg) > 2 * stddev:
                return APDecision(
                    recommendation="escalate",
                    reasoning=(
                        f"Invoice amount ${current_amount:.2f} for {invoice.vendor_name} "
                        f"is more than 2 standard deviations from the historical average "
                        f"(avg=${avg:.2f}, σ=${stddev:.2f}). Routing to human review."
                    ),
                    confidence=min(1.0, max(0.65, confidence - 0.15)),
                    info_needed=None,
                    risk_flags=["amount_anomaly_2sigma"],
                    vendor_context_used=vendor_context_used or {},
                    model="rules",
                )

        # Step 8: Trusted vendor (always approved) → approve at lower threshold
        always_approved = bool(vendor_profile.get("always_approved"))
        trusted_threshold = max(0.90, auto_threshold - 0.05)
        if always_approved and confidence >= trusted_threshold:
            return APDecision(
                recommendation="approve",
                reasoning=(
                    f"{invoice.vendor_name} has a 100% approval history and extraction confidence "
                    f"is {confidence:.0%} (trusted vendor threshold: {trusted_threshold:.0%}). "
                    "Safe to proceed."
                ),
                confidence=confidence,
                info_needed=None,
                risk_flags=[],
                vendor_context_used=vendor_context_used or {},
                model="rules",
            )

        # Step 9: Confidence meets org threshold → approve
        if confidence >= auto_threshold:
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
                model="rules",
            )

        # Step 10: Default → escalate (below threshold)
        return APDecision(
            recommendation="escalate",
            reasoning=(
                f"Extraction confidence is {confidence:.0%} — below the "
                f"{auto_threshold:.0%} threshold for autonomous approval of "
                f"{invoice.vendor_name} ${getattr(invoice, 'amount', 0):.2f}. "
                "Routing to human review."
            ),
            confidence=confidence,
            info_needed=None,
            risk_flags=["low_extraction_confidence"],
            vendor_context_used=vendor_context_used or {},
            model="rules",
        )
