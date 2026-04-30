"""
Agent Anomaly Detection Service for Clearledgr Reconciliation v1

Detects anomalies and unusual patterns in financial data.

Two layers, separated by design:

  1. **Rule layer** (this module's ``detect_*`` functions): pure z-score
     statistics over historical series. Decides if there's an anomaly.
     No model judgment — the cascade in ``APDecisionService`` reads the
     boolean ``is_anomaly`` flag and gates accordingly.
  2. **LLM augmentation** (``explain_volume_anomaly``): when the rule
     layer flags an anomaly, this asynchronous helper takes the
     numeric context plus vendor history and asks Haiku to write a
     context-aware operator explanation — what's likely going on with
     THIS specific invoice/vendor, not the generic
     "verify data completeness" boilerplate. Augmentation never gates
     a decision; if it fails or the API key is absent, the rule output
     ships unchanged with the generic suggestion.

This split honours the deck thesis: rules decide, LLM describes. The
explanation is advisory copy for the operator — never a routing
signal.
"""
from __future__ import annotations

import json
import logging
from statistics import mean, stdev
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def detect_volume_anomalies(
    current_volume: float,
    historical_volumes: List[float],
    threshold_std: float = 2.0
) -> Dict:
    """
    Detect volume anomalies (sudden spikes or drops).
    
    Args:
        current_volume: Current period volume
        historical_volumes: List of historical volumes
        threshold_std: Number of standard deviations for threshold
    
    Returns:
        Dict with anomaly detection results
    """
    if len(historical_volumes) < 3:
        return {
            "is_anomaly": False,
            "reason": "insufficient_history",
            "confidence": 0.0
        }
    
    avg_volume = mean(historical_volumes)
    volume_std = stdev(historical_volumes) if len(historical_volumes) > 1 else 0
    
    if volume_std == 0:
        return {
            "is_anomaly": False,
            "reason": "no_variance",
            "confidence": 0.0
        }
    
    z_score = (current_volume - avg_volume) / volume_std if volume_std > 0 else 0
    
    is_anomaly = abs(z_score) > threshold_std
    anomaly_type = None
    
    if is_anomaly:
        if z_score > threshold_std:
            anomaly_type = "spike"
        elif z_score < -threshold_std:
            anomaly_type = "drop"
    
    confidence = min(1.0, abs(z_score) / threshold_std) if is_anomaly else 0.0
    
    return {
        "is_anomaly": is_anomaly,
        "anomaly_type": anomaly_type,
        "z_score": z_score,
        "current_volume": current_volume,
        "average_volume": avg_volume,
        "confidence": confidence,
        "suggestion": _get_volume_anomaly_suggestion(anomaly_type, z_score) if is_anomaly else None
    }


def detect_match_rate_anomalies(
    current_match_rate: float,
    historical_match_rates: List[float],
    threshold_std: float = 2.0
) -> Dict:
    """
    Detect anomalies in match rates.
    
    Args:
        current_match_rate: Current match rate percentage
        historical_match_rates: List of historical match rates
        threshold_std: Number of standard deviations for threshold
    
    Returns:
        Dict with anomaly detection results
    """
    if len(historical_match_rates) < 3:
        return {
            "is_anomaly": False,
            "reason": "insufficient_history",
            "confidence": 0.0
        }
    
    avg_rate = mean(historical_match_rates)
    rate_std = stdev(historical_match_rates) if len(historical_match_rates) > 1 else 0
    
    if rate_std == 0:
        return {
            "is_anomaly": False,
            "reason": "no_variance",
            "confidence": 0.0
        }
    
    z_score = (current_match_rate - avg_rate) / rate_std if rate_std > 0 else 0
    
    is_anomaly = abs(z_score) > threshold_std
    anomaly_type = None
    
    if is_anomaly:
        if z_score < -threshold_std:  # Lower match rate is bad
            anomaly_type = "degradation"
        elif z_score > threshold_std:  # Higher match rate is good, but unusual
            anomaly_type = "improvement"
    
    confidence = min(1.0, abs(z_score) / threshold_std) if is_anomaly else 0.0
    
    return {
        "is_anomaly": is_anomaly,
        "anomaly_type": anomaly_type,
        "z_score": z_score,
        "current_match_rate": current_match_rate,
        "average_match_rate": avg_rate,
        "confidence": confidence,
        "suggestion": _get_match_rate_anomaly_suggestion(anomaly_type, z_score) if is_anomaly else None
    }


def detect_exception_patterns(
    exceptions: List[Dict],
    historical_exceptions: List[List[Dict]]
) -> Dict:
    """
    Detect patterns in exceptions.
    
    Args:
        exceptions: Current period exceptions
        historical_exceptions: List of historical exception lists
    
    Returns:
        Dict with pattern detection results
    """
    current_count = len(exceptions)
    
    if len(historical_exceptions) < 2:
        return {
            "has_pattern": False,
            "reason": "insufficient_history"
        }
    
    historical_counts = [len(exc_list) for exc_list in historical_exceptions]
    avg_count = mean(historical_counts)
    
    # Check for sudden increase
    if current_count > avg_count * 1.5:
        return {
            "has_pattern": True,
            "pattern_type": "exception_spike",
            "current_count": current_count,
            "average_count": avg_count,
            "suggestion": "Review exceptions - significant increase detected. Consider checking data quality or matching configuration."
        }
    
    # Check for common exception reasons
    if exceptions:
        reason_counts = {}
        for exc in exceptions:
            reason = exc.get("reason", "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        most_common_reason = max(reason_counts.items(), key=lambda x: x[1])
        if most_common_reason[1] > len(exceptions) * 0.5:  # >50% same reason
            return {
                "has_pattern": True,
                "pattern_type": "common_reason",
                "reason": most_common_reason[0],
                "count": most_common_reason[1],
                "percentage": (most_common_reason[1] / len(exceptions)) * 100,
                "suggestion": f"Most exceptions ({most_common_reason[1]}/{len(exceptions)}) are due to: {most_common_reason[0]}. Consider adjusting matching configuration."
            }
    
    return {
        "has_pattern": False,
        "reason": "no_significant_patterns"
    }


def _get_volume_anomaly_suggestion(anomaly_type: str, z_score: float) -> str:
    """Get suggestion for volume anomaly."""
    if anomaly_type == "spike":
        return f"Volume spike detected (z-score: {z_score:.2f}). Verify data completeness and check for duplicate transactions."
    elif anomaly_type == "drop":
        return f"Volume drop detected (z-score: {z_score:.2f}). Verify all data sources are included and check for missing periods."
    return "Volume anomaly detected. Review data sources."


def _get_match_rate_anomaly_suggestion(anomaly_type: str, z_score: float) -> str:
    """Get suggestion for match rate anomaly."""
    if anomaly_type == "degradation":
        return f"Match rate degradation detected (z-score: {z_score:.2f}). Consider reviewing matching tolerances or data quality."
    elif anomaly_type == "improvement":
        return f"Match rate improvement detected (z-score: {z_score:.2f}). This is positive - consider documenting what changed."
    return "Match rate anomaly detected. Review reconciliation configuration."


_ANOMALY_EXPLAIN_PROMPT = """A rule-based check has flagged an amount anomaly on an invoice. \
Your job is to write a one-sentence operator-facing explanation that names the most likely \
business reason given the context, plus one concrete next step.

Context:
- Vendor: {vendor}
- Current invoice amount: {currency} {current:,.2f}
- Vendor's last {n_recent} invoice amounts: {recent}
- Historical average: {currency} {avg:,.2f}
- Historical std dev: {currency} {stddev:,.2f}
- Z-score: {z_score:.2f} ({direction})

Return JSON only:
{{
  "explanation": "<one sentence — what is the likely cause and what should the operator look at>",
  "likely_causes": ["<2-3 short candidate causes ranked by likelihood>"],
  "next_step": "<one short action — e.g. 'Compare line items to {prev_amount}', 'Check for annual renewal', 'Verify with PO line for unit pricing'>"
}}

Be specific to the numbers. Do not output the generic phrase \"verify data completeness\" — \
prefer concrete checks tied to this vendor's pattern. If the move looks like an annual renewal, \
say so. If line-item drift is more likely, say that. If the amount looks like a tax/discount \
flip, say that. No prose outside the JSON."""


async def explain_volume_anomaly(
    anomaly_result: Dict[str, Any],
    *,
    vendor_name: str,
    invoice_amount: float,
    recent_amounts: List[float],
    currency: str = "USD",
) -> Dict[str, Any]:
    """Augment a rule-detected volume anomaly with a context-aware LLM
    explanation. Never raises — falls back to the unmodified input on
    any failure (no API key, gateway timeout, JSON parse error).

    The rules layer has already decided there's an anomaly; this helper
    only refines the operator-facing text. If the input is not an
    anomaly, the input is returned unchanged.
    """
    if not isinstance(anomaly_result, dict):
        return anomaly_result
    if not anomaly_result.get("is_anomaly"):
        return anomaly_result

    avg = float(anomaly_result.get("average_volume") or 0.0)
    z_score = float(anomaly_result.get("z_score") or 0.0)
    direction = "spike" if z_score > 0 else "drop"
    recent = list(recent_amounts or [])[-6:]
    recent_str = ", ".join(f"{v:,.2f}" for v in recent) if recent else "no history"
    stddev_val = 0.0
    if len(recent) > 1:
        try:
            stddev_val = float(stdev(recent))
        except Exception:
            stddev_val = 0.0
    prev_amount = recent[-1] if recent else None
    prev_amount_str = f"{currency} {prev_amount:,.2f}" if prev_amount is not None else "the prior invoice"

    prompt = _ANOMALY_EXPLAIN_PROMPT.format(
        vendor=vendor_name or "this vendor",
        currency=currency,
        current=float(invoice_amount or 0.0),
        n_recent=len(recent),
        recent=recent_str,
        avg=avg,
        stddev=stddev_val,
        z_score=z_score,
        direction=direction,
        prev_amount=prev_amount_str,
    )

    try:
        from clearledgr.core.llm_gateway import LLMAction, get_llm_gateway

        gateway = get_llm_gateway()
        resp = await gateway.call(
            LLMAction.EXPLAIN_ANOMALY,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content if isinstance(resp.content, str) else ""
        if not raw:
            return anomaly_result
        # Trim a code-fence wrapper if Haiku decided to add one despite
        # the JSON-only instruction. A robust gateway wrapper isn't worth
        # it for a single advisory call.
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
        explanation = str(parsed.get("explanation") or "").strip()
        if not explanation:
            return anomaly_result
        enriched = dict(anomaly_result)
        enriched["llm_explanation"] = explanation
        likely = parsed.get("likely_causes")
        if isinstance(likely, list):
            enriched["likely_causes"] = [str(x) for x in likely if x]
        next_step = str(parsed.get("next_step") or "").strip()
        if next_step:
            enriched["next_step"] = next_step
        # Replace the generic suggestion with the contextual one so
        # operator-facing surfaces (Slack card, sidebar) get the better
        # copy without any plumbing change. Keep the generic one under
        # ``rule_suggestion`` for audit/debug.
        enriched["rule_suggestion"] = enriched.get("suggestion")
        enriched["suggestion"] = explanation
        return enriched
    except Exception as exc:
        logger.debug(
            "[anomaly_detection] LLM explanation skipped (rule output preserved): %s",
            exc,
        )
        return anomaly_result

