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
from datetime import datetime, timedelta

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


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
            # Get invoices from database
            # This uses the database's invoice history
            invoices = self.db.get_invoices_by_vendor(
                vendor=vendor,
                organization_id=self.organization_id,
                days=days,
            ) if hasattr(self.db, 'get_invoices_by_vendor') else []
            
            return invoices or []
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
    ) -> List[DuplicateAlert]:
        """Check for potential duplicate invoices."""
        duplicates = []
        
        for inv in recent_invoices:
            # Skip self
            if exclude_gmail_id and inv.get("gmail_id") == exclude_gmail_id:
                continue
            
            match_score = 0.0
            match_reasons = []
            
            # Check invoice number match (strongest signal)
            if invoice_number and inv.get("invoice_number"):
                if invoice_number.lower() == inv.get("invoice_number", "").lower():
                    match_score += 0.5
                    match_reasons.append("Same invoice number")
            
            # Check amount match
            inv_amount = inv.get("amount", 0)
            if inv_amount > 0 and amount > 0:
                amount_diff = abs(amount - inv_amount) / amount
                if amount_diff <= self.DUPLICATE_AMOUNT_TOLERANCE:
                    match_score += 0.3
                    match_reasons.append(f"Same amount (${amount:,.2f})")
            
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
                except:
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
        
        return duplicates[:3]  # Return top 3 potential duplicates
    
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
        
        # Check for frequency anomaly (too many invoices in short time)
        recent_count = len([
            inv for inv in recent_invoices
            if inv.get("created_at") and self._within_days(inv.get("created_at"), 7)
        ])
        
        if recent_count >= 3:
            anomalies.append(AnomalyAlert(
                severity="warning",
                anomaly_type="frequency",
                message=f"Multiple invoices ({recent_count}) from {vendor} in past 7 days",
                expected_value=1,
                actual_value=recent_count,
                deviation_pct=(recent_count - 1) * 100,
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
        """Check if a date is within N days of now."""
        try:
            if isinstance(date_value, str):
                date_value = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            
            cutoff = datetime.now() - timedelta(days=days)
            return date_value.replace(tzinfo=None) >= cutoff
        except:
            return False


# Convenience function
def get_cross_invoice_analyzer(organization_id: str = "default") -> CrossInvoiceAnalyzer:
    """Get a cross-invoice analyzer instance."""
    return CrossInvoiceAnalyzer(organization_id=organization_id)
