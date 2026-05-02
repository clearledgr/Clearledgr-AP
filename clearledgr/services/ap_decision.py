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
    ap_item: Optional[Dict[str, Any]] = None,
    org_thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute a composite vendor risk score from 0.0 (safe) to 1.0 (high risk).

    Components (each 0.0-1.0, weighted):
      - vendor_familiarity (0.30): new vendors are riskier
      - duplicate_risk    (0.25): from cross-invoice analysis
      - anomaly_risk      (0.20): amount/volume anomalies
      - override_risk     (0.15): high human override rate
      - bank_change_risk  (0.10): recent bank detail changes

    Module 4 spec line 158 — three customer-configurable fraud rules:
      1. New IBAN doesn't match prior payments  (bank_change_alert_days)
      2. Unusually large invoice from low-frequency vendor
         (low_frequency_invoice_count_threshold + multiplier)
      3. Vendor created within last 30 days with first invoice over $X
         (new_vendor_days + new_vendor_first_invoice_max)

    Thresholds come from org settings via ``org_thresholds`` arg.
    Defaults preserve historical behaviour for orgs that haven't
    configured custom thresholds yet.
    """
    vendor_profile = vendor_profile or {}
    cross_invoice_analysis = cross_invoice_analysis or {}
    anomaly_signals = anomaly_signals or {}
    decision_feedback = decision_feedback or {}
    ap_item = ap_item or {}
    thresholds = dict(org_thresholds or {})

    # Customer-configurable threshold defaults (Module 4 fraud rules)
    bank_alert_days = int(thresholds.get("bank_change_alert_days") or 14)
    bank_warn_days = int(thresholds.get("bank_change_warn_days") or 30)
    low_freq_count = int(thresholds.get("low_frequency_invoice_count_threshold") or 3)
    low_freq_mult = float(thresholds.get("low_frequency_invoice_multiplier") or 3.0)
    new_vendor_days = int(thresholds.get("new_vendor_days") or 30)
    new_vendor_max = float(thresholds.get("new_vendor_first_invoice_max") or 10000.0)

    scores: Dict[str, float] = {}
    flags: list = []

    # 1. Vendor familiarity (new = risky). Customer-configurable
    #    "low_freq_count" tunes when "low_history" kicks in.
    invoice_count = int(vendor_profile.get("invoice_count") or 0)
    if invoice_count == 0:
        scores["vendor_familiarity"] = 1.0
        flags.append("new_vendor")
        # Spec rule #3: vendor created in last `new_vendor_days` AND
        # first invoice over `new_vendor_first_invoice_max` is a
        # configurable fraud signal.
        try:
            amount = float(ap_item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        vendor_created_at = vendor_profile.get("created_at") or vendor_profile.get("first_seen_at")
        vendor_age_days = _days_since(vendor_created_at)
        if amount > new_vendor_max and (vendor_age_days is None or vendor_age_days <= new_vendor_days):
            flags.append("new_vendor_high_amount")
            scores["vendor_familiarity"] = 1.0
    elif invoice_count < low_freq_count:
        scores["vendor_familiarity"] = 0.6
        flags.append("low_history")
        # Spec rule #2: unusually large invoice from low-frequency
        # vendor. "Unusually large" = amount > multiplier × vendor avg.
        try:
            amount = float(ap_item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        avg_amount = float(vendor_profile.get("avg_invoice_amount") or 0)
        if avg_amount > 0 and amount > avg_amount * low_freq_mult:
            flags.append("low_frequency_high_amount")
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

    # 5. Bank change recency. Spec rule #1: "new IBAN doesn't match
    #    prior payments." Customer-configurable alert window.
    bank_days = _days_since(vendor_profile.get("bank_details_changed_at"))
    if bank_days is not None and bank_days <= bank_alert_days:
        scores["bank_change_risk"] = 1.0
        flags.append("recent_bank_change")
    elif bank_days is not None and bank_days <= bank_warn_days:
        scores["bank_change_risk"] = 0.5
        flags.append("bank_change_warn")
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


def _apply_single_pass_hints(
    decision: APDecision,
    hints: Dict[str, Any],
    invoice: Any,
) -> APDecision:
    """Apply single-pass LLM advisory hints as a downgrade-only filter.

    The deterministic cascade is the source of truth. The LLM's
    advisory output (``gl_coding`` / ``duplicate_analysis`` /
    ``risk_assessment``) can pull a recommendation toward stricter
    review when it spots a fraud or duplicate signal the rules missed,
    but it can never push toward approval. Three transformations:

      - ``risk_assessment.fraud_signals`` are appended to ``risk_flags``
        for any non-clean recommendation. Audit trail / Slack card
        readers see the LLM's signal alongside the rule's reasoning.
      - ``risk_assessment.fraud_risk == "high"`` AND current
        recommendation is ``approve`` → downgrade to ``escalate`` with
        ``single_pass_high_fraud_risk`` flag.
      - ``duplicate_analysis.is_duplicate is True`` AND current
        recommendation is ``approve`` → downgrade to ``escalate`` with
        ``single_pass_duplicate_hint`` flag. Note: the deterministic
        Step 6 already escalates on real duplicate evidence; this hint
        catches the gap where the LLM saw a duplicate signal that the
        cross-invoice evaluator hasn't caught yet (e.g., first
        intake, no DB rows yet).
    """
    risk = (hints.get("risk_assessment") or {}) if isinstance(hints, dict) else {}
    duplicate = (hints.get("duplicate_analysis") or {}) if isinstance(hints, dict) else {}

    fraud_signals = risk.get("fraud_signals") or []
    fraud_signals = [str(s) for s in fraud_signals if s]
    fraud_risk = str(risk.get("fraud_risk") or "none").lower().strip()
    is_duplicate = bool(duplicate.get("is_duplicate"))

    new_flags = list(decision.risk_flags)
    new_recommendation = decision.recommendation
    new_reasoning = decision.reasoning
    overridden = False
    original_recommendation = decision.original_recommendation

    if decision.recommendation == "approve" and fraud_risk == "high":
        new_recommendation = "escalate"
        signals_str = ", ".join(fraud_signals) or "unspecified"
        amount = getattr(invoice, "amount", 0) or 0
        new_reasoning = (
            f"Single-pass extractor flagged HIGH fraud risk for "
            f"{getattr(invoice, 'vendor_name', 'this vendor')} "
            f"(${amount:.2f}). Signals: {signals_str}. The deterministic "
            "rules cascade did not gate on these signals, but high "
            "fraud-risk hints from the extractor force human review."
        )
        if "single_pass_high_fraud_risk" not in new_flags:
            new_flags.append("single_pass_high_fraud_risk")
        overridden = True
        original_recommendation = original_recommendation or decision.recommendation

    elif decision.recommendation == "approve" and is_duplicate:
        new_recommendation = "escalate"
        amount = getattr(invoice, "amount", 0) or 0
        supersedes = duplicate.get("supersedes_reference")
        new_reasoning = (
            f"Single-pass extractor flagged this invoice from "
            f"{getattr(invoice, 'vendor_name', 'this vendor')} "
            f"(${amount:.2f}) as a likely duplicate"
            + (f" (supersedes {supersedes})." if supersedes else ".")
            + " The cross-invoice evaluator hasn't caught it (likely "
            "first intake or fresh history), but the LLM signal is "
            "enough to route to human review."
        )
        if "single_pass_duplicate_hint" not in new_flags:
            new_flags.append("single_pass_duplicate_hint")
        overridden = True
        original_recommendation = original_recommendation or decision.recommendation

    elif fraud_risk in ("medium", "high") and fraud_signals:
        for sig in fraud_signals:
            tag = f"llm_signal:{sig}"[:80]
            if tag not in new_flags:
                new_flags.append(tag)

    if not overridden and new_flags == decision.risk_flags:
        return decision

    return APDecision(
        recommendation=new_recommendation,
        reasoning=new_reasoning,
        confidence=decision.confidence,
        info_needed=decision.info_needed,
        risk_flags=new_flags,
        vendor_context_used=decision.vendor_context_used,
        model=decision.model,
        fallback=decision.fallback,
        gate_override=decision.gate_override,
        original_recommendation=original_recommendation,
    )


def _evaluate_rules_for_invoice(
    invoice: Any,
    vendor_context: Dict[str, Any],
    *,
    organization_id: str,
) -> Optional[APDecision]:
    """Run the workspace rule engine. Returns an APDecision if a rule
    matches; None if no rule matches OR the engine is unavailable
    (so the caller falls through to the legacy 10-step cascade).

    Action → recommendation mapping:
      auto_approve              → approve
      route_to_role / _user     → needs_approval (with target captured
                                  in info_needed)
      require_n_approvals       → needs_approval (n captured in flags)
      require_dual_approval     → needs_approval (dual_approval flag)
      escalate_after            → escalate (delayed-firing escalation
                                  is a future-work hook; the rule
                                  match itself routes to escalate)
      hold_for_finance_review   → escalate
    """
    if not organization_id:
        return None
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.rule_engine import (
            build_invoice_context, evaluate_rules,
        )
        db = get_db()
        rules = db.list_rules(organization_id, workflow="ap")
    except Exception as exc:
        logger.debug(
            "[ap_decision] rule engine unavailable, falling back: %s", exc,
        )
        return None
    if not rules:
        return None

    try:
        ctx = build_invoice_context(invoice)
        # Stamp the entity into the context so entity-scoped rules
        # match correctly.
        if getattr(invoice, "entity_id", None):
            ctx["entity_id"] = invoice.entity_id
        result = evaluate_rules(ctx, rules)
    except Exception as exc:
        logger.debug(
            "[ap_decision] rule evaluation failed, falling back: %s", exc,
        )
        return None

    if result.matched_rule is None:
        return None

    return _rule_match_to_decision(
        invoice, result.matched_rule, result.matched_actions,
        vendor_context_used=vendor_context,
    )


def _rule_match_to_decision(
    invoice: Any,
    rule: Dict[str, Any],
    actions: List[Dict[str, Any]],
    *,
    vendor_context_used: Dict[str, Any],
) -> APDecision:
    """Translate matched rule actions into an APDecision."""
    rule_name = rule.get("name") or "rule"
    vendor_name = getattr(invoice, "vendor_name", "this vendor")

    has_auto_approve = any(a.get("type") == "auto_approve" for a in actions)
    has_dual = any(a.get("type") == "require_dual_approval" for a in actions)
    has_n = any(a.get("type") == "require_n_approvals" for a in actions)
    has_route = any(a.get("type") in ("route_to_role", "route_to_user") for a in actions)
    has_escalate = any(
        a.get("type") in ("escalate_after", "hold_for_finance_review")
        for a in actions
    )

    risk_flags: List[str] = [f"rule_matched:{rule.get('id', '')}"[:80]]
    info_needed: Optional[str] = None

    if has_auto_approve and not (has_route or has_dual or has_n or has_escalate):
        return APDecision(
            recommendation="approve",
            reasoning=(
                f"Rule '{rule_name}' matched and routes {vendor_name} "
                "to auto-approval."
            ),
            confidence=0.95,
            info_needed=None,
            risk_flags=risk_flags,
            vendor_context_used=vendor_context_used or {},
            model="rules:workspace",
        )

    if has_escalate:
        risk_flags.append("rule_action:escalate")
        return APDecision(
            recommendation="escalate",
            reasoning=(
                f"Rule '{rule_name}' matched and routes {vendor_name} "
                "to human review."
            ),
            confidence=0.9,
            info_needed=None,
            risk_flags=risk_flags,
            vendor_context_used=vendor_context_used or {},
            model="rules:workspace",
        )

    if has_dual:
        risk_flags.append("rule_action:dual_approval")
    if has_n:
        for a in actions:
            if a.get("type") == "require_n_approvals":
                n = int(a.get("n") or 2)
                risk_flags.append(f"rule_action:require_{n}_approvals")
                break
    if has_route:
        for a in actions:
            if a.get("type") == "route_to_role":
                risk_flags.append(f"rule_action:role:{a.get('role')}")
                info_needed = (
                    f"Rule '{rule_name}' routes this invoice to role "
                    f"{a.get('role')}."
                )
                break
            if a.get("type") == "route_to_user":
                risk_flags.append(f"rule_action:user:{a.get('user_email')}")
                info_needed = (
                    f"Rule '{rule_name}' routes this invoice to "
                    f"{a.get('user_email')}."
                )
                break

    return APDecision(
        recommendation="needs_info" if info_needed and not (has_dual or has_n) else "needs_info",
        reasoning=(
            f"Rule '{rule_name}' matched. Routing for human review "
            "per the configured action set."
        ),
        confidence=0.9,
        info_needed=info_needed,
        risk_flags=risk_flags,
        vendor_context_used=vendor_context_used or {},
        model="rules:workspace",
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
        single_pass_hints: Optional[Dict[str, Any]] = None,
    ) -> APDecision:
        """Compute the routing recommendation deterministically. Never raises.

        ``single_pass_hints`` carries the LLM-side advisory output from
        :mod:`clearledgr.services.single_pass_processor` (``gl_coding``,
        ``duplicate_analysis``, ``risk_assessment``). The cascade is the
        single source of truth, but the hints are applied as a
        downgrade-only filter at the end: if the LLM saw a fraud or
        duplicate signal that the deterministic rules missed, the
        recommendation can drop from ``approve`` to ``escalate`` with
        the hint surfaced in ``risk_flags``. Hints can never upgrade a
        decision.
        """
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

        # Module 3 — workspace rule engine evaluation runs FIRST.
        # If a rule matches, its actions translate into the routing
        # recommendation directly. If no rule matches (or rules are
        # disabled / unavailable), fall through to the deterministic
        # 10-step cascade so legacy orgs without rules behave exactly
        # as before. The validation gate is still enforced — a failed
        # gate skips rules entirely so a rule can't auto-approve a
        # bill that the gate has flagged.
        rule_decision: Optional[APDecision] = None
        if validation_gate.get("passed", True):
            rule_decision = _evaluate_rules_for_invoice(
                invoice, vendor_context_used,
                organization_id=org_config.get("organization_id") or "",
            )

        if rule_decision is not None:
            decision = rule_decision
        else:
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
        if single_pass_hints:
            decision = _apply_single_pass_hints(decision, single_pass_hints, invoice)
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
