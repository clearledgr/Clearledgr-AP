"""
Cross-Invoice Analysis Service

Analyzes invoices across time to detect:
- Duplicates (same vendor + amount + date range)
- Anomalies (unusual amounts, unexpected vendors)
- Patterns (spending trends, vendor frequency)

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import re

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


def _ai_evaluate_duplicates(
    current_vendor: str,
    current_amount: float,
    current_invoice_number: Optional[str],
    current_date: Optional[str],
    flagged: List["DuplicateAlert"],
) -> List["DuplicateAlert"]:
    """Ask Claude to reason about whether flagged items are true duplicates.

    Claude considers: amendments, credit notes, recurring invoices,
    partial payments, and replacement invoices — things deterministic
    scoring can't distinguish.
    """
    try:
        import os, json
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return flagged

        import httpx
        matches_text = "\n".join(
            f"  - Invoice #{d.details.get('matching_invoice_number', '?')}, "
            f"amount {d.details.get('matching_amount', '?')}, "
            f"date {d.details.get('matching_date', '?')}, "
            f"score {d.match_score:.1%}: {d.message}"
            for d in flagged
        )

        prompt = f"""You are an AP automation expert. Evaluate these potential duplicate invoices.

CURRENT INVOICE:
  Vendor: {current_vendor}
  Amount: {current_amount}
  Invoice #: {current_invoice_number or 'N/A'}
  Date: {current_date or 'N/A'}

FLAGGED MATCHES:
{matches_text}

For each match, determine:
1. Is this a TRUE DUPLICATE (same invoice submitted twice)?
2. Is this a RECURRING INVOICE (same vendor, similar amount, different period)?
3. Is this an AMENDMENT/REVISION (replaces the original)?
4. Is this a CREDIT NOTE or DEBIT NOTE against the original?
5. Is this UNRELATED (coincidental match)?

Return JSON array with one object per match:
[{{"invoice_number": "...", "verdict": "duplicate|recurring|amendment|credit|unrelated", "confidence": 0.0-1.0, "reasoning": "one sentence"}}]

Return ONLY valid JSON."""

        from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
        gateway = get_llm_gateway()
        llm_response = gateway.call_sync(
            LLMAction.DUPLICATE_EVALUATION,
            messages=[{"role": "user", "content": prompt}],
        )
        text = llm_response.content if isinstance(llm_response.content, str) else ""
        verdicts = json.loads(text)

        # Enrich flagged duplicates with AI verdicts
        for i, dup in enumerate(flagged):
            if i < len(verdicts):
                v = verdicts[i]
                verdict = v.get("verdict", "duplicate")
                dup.details["ai_verdict"] = verdict
                dup.details["ai_reasoning"] = v.get("reasoning", "")
                dup.details["ai_confidence"] = v.get("confidence", 0.5)

                # Downgrade score for non-duplicates
                if verdict in ("recurring", "unrelated"):
                    dup = DuplicateAlert(
                        severity="info",
                        message=f"{verdict.title()}: {v.get('reasoning', dup.message)}",
                        matching_invoice_id=dup.matching_invoice_id,
                        match_score=max(0.1, dup.match_score * 0.3),
                        details=dup.details,
                    )
                    flagged[i] = dup
                elif verdict == "amendment":
                    dup.details["is_amendment"] = True
                    dup = DuplicateAlert(
                        severity="warning",
                        message=f"Amendment: {v.get('reasoning', dup.message)}",
                        matching_invoice_id=dup.matching_invoice_id,
                        match_score=dup.match_score,
                        details=dup.details,
                    )
                    flagged[i] = dup

        return flagged
    except Exception as exc:
        logger.debug("AI duplicate evaluation failed: %s", exc)
        return flagged


def _normalize_invoice_number(raw: str) -> str:
    """Normalize invoice number for comparison: lowercase, strip whitespace and common prefixes."""
    val = str(raw or "").strip().lower()
    val = val.lstrip("#")
    val = re.sub(r"^inv(?:oice)?[-\s]*", "", val)
    return val.strip()


@dataclass
class DuplicateAlert:
    """Alert for potential duplicate invoice."""
    severity: str  # "high", "warning", "info"
    message: str
    matching_invoice_id: str
    match_score: float
    details: Dict[str, Any]


@dataclass
class AnomalyAlert:
    """Alert for anomalous invoice."""
    severity: str
    anomaly_type: str  # "amount", "frequency", "vendor"
    message: str
    expected_value: Any
    actual_value: Any
    deviation_pct: float


@dataclass 
class CrossInvoiceAnalysis:
    """Result of cross-invoice analysis."""
    has_issues: bool
    duplicates: List[DuplicateAlert]
    anomalies: List[AnomalyAlert]
    vendor_stats: Dict[str, Any]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_issues": self.has_issues,
            "duplicates": [
                {
                    "severity": d.severity,
                    "message": d.message,
                    "matching_invoice_id": d.matching_invoice_id,
                    "match_score": d.match_score,
                }
                for d in self.duplicates
            ],
            "anomalies": [
                {
                    "severity": a.severity,
                    "type": a.anomaly_type,
                    "message": a.message,
                    "deviation_pct": a.deviation_pct,
                }
                for a in self.anomalies
            ],
            "vendor_stats": self.vendor_stats,
            "recommendations": self.recommendations,
        }


class CrossInvoiceAnalyzer:
    """
    Analyzes invoices across the organization's history to detect issues.

    Velocity threshold is resolved from the organization's fraud-control
    config (``fraud_controls.vendor_velocity_max_per_week``) — there is no
    separate hard-coded setting. The gate blocks at the configured max;
    this analyzer's "frequency" anomaly fires at 70% of the max (floor 3)
    as an early-warning signal that does NOT block but is surfaced to
    Claude's reasoning.

    Usage:
        analyzer = CrossInvoiceAnalyzer("org_123")
        analysis = analyzer.analyze(
            vendor="Stripe",
            amount=299.00,
            invoice_number="INV-123",
            invoice_date="2026-01-15"
        )

        if analysis.has_issues:
            for dup in analysis.duplicates:
                print(f"Duplicate: {dup.message}")
    """

    # Configuration
    DUPLICATE_AMOUNT_TOLERANCE = 0.01  # 1% tolerance for amount match
    DUPLICATE_DAYS_WINDOW = 7  # Look for duplicates within 7 days
    ANOMALY_AMOUNT_THRESHOLD = 0.30  # 30% deviation is anomalous

    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        # Velocity thresholds derived from the org's fraud_controls config.
        # Loaded lazily on first use to avoid import-time DB dependencies.
        self._velocity_max_per_week: Optional[int] = None
        self._velocity_warning_threshold: Optional[int] = None

    def _get_velocity_thresholds(self) -> tuple[int, int]:
        """Return (warning_threshold, hard_max) for vendor velocity.

        hard_max is the configured blocking ceiling (same value the
        validation gate uses — single source of truth). warning_threshold
        is 70% of the hard max, floored at 3, so early-warning signals
        fire before the hard block kicks in.
        """
        if self._velocity_max_per_week is not None and self._velocity_warning_threshold is not None:
            return self._velocity_warning_threshold, self._velocity_max_per_week
        try:
            from clearledgr.core.fraud_controls import load_fraud_controls
            config = load_fraud_controls(self.organization_id, self.db)
            hard_max = int(config.vendor_velocity_max_per_week)
        except Exception as exc:
            logger.warning(
                "[CrossInvoiceAnalyzer] Failed to load fraud_controls "
                "for velocity threshold (org=%s): %s — using default 10",
                self.organization_id, exc,
            )
            hard_max = 10
        warning = max(3, int(hard_max * 0.7))
        self._velocity_max_per_week = hard_max
        self._velocity_warning_threshold = warning
        return warning, hard_max
    
    def analyze(
        self,
        vendor: str,
        amount: float,
        invoice_number: Optional[str] = None,
        invoice_date: Optional[str] = None,
        currency: str = "USD",
        gmail_id: Optional[str] = None,  # Exclude self from duplicate check
    ) -> CrossInvoiceAnalysis:
        """
        Perform cross-invoice analysis.
        
        Returns analysis with duplicates, anomalies, and recommendations.
        """
        duplicates = []
        anomalies = []
        recommendations = []
        
        # Get recent invoices for this vendor
        recent_invoices = self._get_recent_invoices(vendor, days=90)
        
        # Check for duplicates
        duplicate_alerts = self._check_duplicates(
            vendor=vendor,
            amount=amount,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            recent_invoices=recent_invoices,
            exclude_gmail_id=gmail_id,
            currency=currency,
        )
        duplicates.extend(duplicate_alerts)
        
        # Check for anomalies
        anomaly_alerts = self._check_anomalies(
            vendor=vendor,
            amount=amount,
            recent_invoices=recent_invoices,
        )
        anomalies.extend(anomaly_alerts)
        
        # Calculate vendor statistics
        vendor_stats = self._calculate_vendor_stats(vendor, recent_invoices, amount)
        
        # Generate recommendations
        if duplicates:
            recommendations.append("Review for potential duplicate payment")
        if anomalies:
            for a in anomalies:
                if a.anomaly_type == "amount":
                    recommendations.append(f"Verify amount - {a.deviation_pct:.0f}% different from typical")
        if not recent_invoices:
            recommendations.append("New vendor - verify payment details before first payment")
        
        has_issues = bool(duplicates) or any(a.severity == "high" for a in anomalies)
        
        logger.info(
            f"Cross-invoice analysis for {vendor}: "
            f"{len(duplicates)} duplicates, {len(anomalies)} anomalies"
        )
        
        return CrossInvoiceAnalysis(
            has_issues=has_issues,
            duplicates=duplicates,
            anomalies=anomalies,
            vendor_stats=vendor_stats,
            recommendations=recommendations,
        )
    
    def _get_recent_invoices(self, vendor: str, days: int = 90) -> List[Dict[str, Any]]:
        """Get recent invoices for a vendor."""
        try:
            if hasattr(self.db, "get_vendor_invoice_history"):
                return self.db.get_vendor_invoice_history(
                    self.organization_id, vendor, limit=50
                ) or []
            return []
        except Exception as e:
            logger.warning(f"Failed to get recent invoices: {e}")
            return []
    
    def _check_duplicates(
        self,
        vendor: str,
        amount: float,
        invoice_number: Optional[str],
        invoice_date: Optional[str],
        recent_invoices: List[Dict[str, Any]],
        exclude_gmail_id: Optional[str] = None,
        currency: str = "USD",
    ) -> List[DuplicateAlert]:
        """Check for potential duplicate invoices."""
        duplicates = []
        current_currency = str(currency or "USD").strip().upper()

        for inv in recent_invoices:
            # Skip self
            if exclude_gmail_id and inv.get("gmail_id") == exclude_gmail_id:
                continue

            match_score = 0.0
            match_reasons = []

            # Check invoice number match (strongest signal) — normalized
            # comparison. Currency-independent: "INV-1234" is the same
            # invoice regardless of what currency it's quoted in.
            if invoice_number and inv.get("invoice_number"):
                if _normalize_invoice_number(invoice_number) == _normalize_invoice_number(inv.get("invoice_number", "")):
                    match_score += 0.5
                    match_reasons.append("Same invoice number")

            # Check amount match — only if currencies match. €100 and
            # $100 are NOT duplicates; comparing them as raw floats
            # would generate false positives for international AP.
            inv_currency = str(inv.get("currency") or "USD").strip().upper()
            inv_amount = inv.get("amount", 0)
            if (
                inv_amount > 0
                and amount > 0
                and inv_currency == current_currency
            ):
                amount_diff = abs(amount - inv_amount) / max(amount, inv_amount)
                if amount_diff <= self.DUPLICATE_AMOUNT_TOLERANCE:
                    match_score += 0.3
                    # Use the real currency symbol where we can; default
                    # "$" is fine as a fallback for unknown codes.
                    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(current_currency, f"{current_currency} ")
                    match_reasons.append(f"Same amount ({sym}{amount:,.2f})")
            
            # Check date proximity
            if invoice_date and inv.get("created_at"):
                try:
                    current_date = datetime.strptime(invoice_date, "%Y-%m-%d")
                    inv_date = inv.get("created_at")
                    if isinstance(inv_date, str):
                        inv_date = datetime.fromisoformat(inv_date.replace("Z", "+00:00"))
                    
                    days_apart = abs((current_date - inv_date.replace(tzinfo=None)).days)
                    if days_apart <= self.DUPLICATE_DAYS_WINDOW:
                        match_score += 0.2
                        match_reasons.append(f"Within {days_apart} days of previous invoice")
                except Exception:
                    pass

            # Create alert if match score is high enough
            if match_score >= 0.5:
                severity = "high" if match_score >= 0.8 else "warning"
                
                duplicates.append(DuplicateAlert(
                    severity=severity,
                    message=f"Potential duplicate: {', '.join(match_reasons)}",
                    matching_invoice_id=inv.get("gmail_id", inv.get("id", "unknown")),
                    match_score=match_score,
                    details={
                        "matching_amount": inv_amount,
                        "matching_date": str(inv.get("created_at", "")),
                        "matching_invoice_number": inv.get("invoice_number"),
                        "reasons": match_reasons,
                    }
                ))
        
        # Sort by match score
        duplicates.sort(key=lambda d: d.match_score, reverse=True)
        top_duplicates = duplicates[:3]

        # AI reasoning: ask Claude to evaluate flagged duplicates
        if top_duplicates:
            top_duplicates = _ai_evaluate_duplicates(
                current_vendor=vendor,
                current_amount=amount,
                current_invoice_number=invoice_number,
                current_date=invoice_date,
                flagged=top_duplicates,
            )

        return top_duplicates
    
    def _check_anomalies(
        self,
        vendor: str,
        amount: float,
        recent_invoices: List[Dict[str, Any]],
    ) -> List[AnomalyAlert]:
        """Check for anomalies in the invoice."""
        anomalies = []
        
        if not recent_invoices or amount <= 0:
            return anomalies
        
        # Calculate typical amount for this vendor
        amounts = [inv.get("amount", 0) for inv in recent_invoices if inv.get("amount", 0) > 0]
        
        if not amounts:
            return anomalies
        
        avg_amount = sum(amounts) / len(amounts)

        if avg_amount < 0.01:
            return anomalies

        # Check for amount anomaly
        if avg_amount > 0:
            deviation_pct = abs(amount - avg_amount) / avg_amount
            
            if deviation_pct > self.ANOMALY_AMOUNT_THRESHOLD:
                if amount > avg_amount:
                    severity = "high" if deviation_pct > 0.5 else "warning"
                    message = f"Amount ${amount:,.2f} is {deviation_pct*100:.0f}% higher than typical ${avg_amount:,.2f}"
                else:
                    severity = "info"
                    message = f"Amount ${amount:,.2f} is {deviation_pct*100:.0f}% lower than typical ${avg_amount:,.2f}"
                
                anomalies.append(AnomalyAlert(
                    severity=severity,
                    anomaly_type="amount",
                    message=message,
                    expected_value=avg_amount,
                    actual_value=amount,
                    deviation_pct=deviation_pct * 100,
                ))
        
        # Check for frequency anomaly. Threshold comes from fraud_controls
        # (single source of truth with the validation gate). Fires at 70% of
        # the hard max as an early-warning signal; the gate blocks at the
        # hard max itself.
        recent_count = len([
            inv for inv in recent_invoices
            if inv.get("created_at") and self._within_days(inv.get("created_at"), 7)
        ])

        warning_threshold, hard_max = self._get_velocity_thresholds()
        if recent_count >= warning_threshold:
            # Escalate severity to "high" when the count has reached or
            # passed the hard max — at that point the gate is already
            # blocking, so this anomaly is reinforcing the block rather
            # than acting as an early warning.
            severity = "high" if recent_count >= hard_max else "warning"
            anomalies.append(AnomalyAlert(
                severity=severity,
                anomaly_type="frequency",
                message=(
                    f"Multiple invoices ({recent_count}) from {vendor} in past "
                    f"7 days (warning at {warning_threshold}, blocking at "
                    f"{hard_max})"
                ),
                expected_value=hard_max,
                actual_value=recent_count,
                deviation_pct=((recent_count - warning_threshold) / max(warning_threshold, 1)) * 100,
            ))

        return anomalies
    
    def _calculate_vendor_stats(
        self,
        vendor: str,
        recent_invoices: List[Dict[str, Any]],
        current_amount: float,
    ) -> Dict[str, Any]:
        """Calculate statistics about this vendor."""
        if not recent_invoices:
            return {
                "is_new_vendor": True,
                "invoice_count": 0,
                "total_paid": 0,
            }
        
        amounts = [inv.get("amount", 0) for inv in recent_invoices if inv.get("amount", 0) > 0]
        
        return {
            "is_new_vendor": False,
            "invoice_count": len(recent_invoices),
            "total_paid": sum(amounts),
            "average_amount": sum(amounts) / len(amounts) if amounts else 0,
            "min_amount": min(amounts) if amounts else 0,
            "max_amount": max(amounts) if amounts else 0,
            "current_vs_average": (
                (current_amount / (sum(amounts) / len(amounts)) - 1) * 100
                if amounts and sum(amounts) > 0 else 0
            ),
        }
    
    def _within_days(self, date_value: Any, days: int) -> bool:
        """Check if a date is within N days of now.

        Previously this stripped tzinfo off ``date_value`` then compared
        to a tz-aware ``cutoff`` — raises TypeError on every call since
        Python rejects naive/aware comparisons. The bug silently
        disabled the frequency anomaly check (every timestamp fell
        through the except and returned False). Normalize both sides
        to tz-aware UTC before comparing.
        """
        try:
            if isinstance(date_value, str):
                date_value = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            if date_value.tzinfo is None:
                # Treat naive timestamps as UTC — consistent with how
                # the rest of the codebase persists timestamps.
                date_value = date_value.replace(tzinfo=timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            return date_value >= cutoff
        except Exception:
            return False


# Convenience function
def get_cross_invoice_analyzer(organization_id: str = "default") -> CrossInvoiceAnalyzer:
    """Get a cross-invoice analyzer instance."""
    return CrossInvoiceAnalyzer(organization_id=organization_id)
