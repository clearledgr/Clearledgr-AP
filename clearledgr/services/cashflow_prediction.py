"""
Cash Flow Prediction Service

Predicts upcoming AP payments based on:
- Recurring invoices (subscriptions)
- Historical patterns
- Known due dates
- Seasonal trends

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from clearledgr.core.database import get_db
from clearledgr.services.recurring_detection import get_recurring_detector

logger = logging.getLogger(__name__)


@dataclass
class PredictedPayment:
    """A predicted upcoming payment."""
    vendor: str
    amount: float
    currency: str
    expected_date: str
    confidence: float
    source: str  # "recurring", "due_date", "historical", "estimated"
    notes: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "amount": self.amount,
            "currency": self.currency,
            "expected_date": self.expected_date,
            "confidence": self.confidence,
            "source": self.source,
            "notes": self.notes,
        }


@dataclass
class CashFlowForecast:
    """A cash flow forecast for a period."""
    organization_id: str
    forecast_date: str
    period_start: str
    period_end: str
    total_predicted: float
    payments: List[PredictedPayment]
    confidence: float
    breakdown_by_week: Dict[str, float] = field(default_factory=dict)
    breakdown_by_vendor: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "forecast_date": self.forecast_date,
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "total_predicted": self.total_predicted,
            "confidence": self.confidence,
            "payments": [p.to_dict() for p in self.payments],
            "breakdown_by_week": self.breakdown_by_week,
            "breakdown_by_vendor": self.breakdown_by_vendor,
        }
    
    def to_slack_blocks(self) -> List[Dict[str, Any]]:
        """Convert to Slack blocks for display."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "AP Forecast"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{self.period_start} to {self.period_end}*\n"
                            f"Predicted AP: *${self.total_predicted:,.2f}*\n"
                            f"Confidence: {self.confidence*100:.0f}%"
                }
            },
            {"type": "divider"},
        ]
        
        # Add weekly breakdown
        if self.breakdown_by_week:
            week_text = "\n".join([
                f"• {week}: ${amount:,.2f}"
                for week, amount in sorted(self.breakdown_by_week.items())[:4]
            ])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*By Week:*\n{week_text}"
                }
            })
        
        # Add top vendors
        if self.breakdown_by_vendor:
            sorted_vendors = sorted(
                self.breakdown_by_vendor.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            vendor_text = "\n".join([
                f"• {vendor}: ${amount:,.2f}"
                for vendor, amount in sorted_vendors
            ])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Top Vendors:*\n{vendor_text}"
                }
            })
        
        # Add upcoming payments
        if self.payments:
            upcoming = self.payments[:5]
            payment_text = "\n".join([
                f"• {p.vendor}: ${p.amount:,.2f} ({p.expected_date})"
                for p in upcoming
            ])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Upcoming:*\n{payment_text}"
                }
            })
        
        return blocks


class CashFlowPredictionService:
    """
    Predicts upcoming AP payments.
    
    Usage:
        service = CashFlowPredictionService("org_123")
        
        # Get 30-day forecast
        forecast = service.forecast(days=30)
        print(f"Expected AP: ${forecast.total_predicted:,.2f}")
        
        # Get weekly breakdown
        for week, amount in forecast.breakdown_by_week.items():
            print(f"{week}: ${amount:,.2f}")
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self._recurring_detector = None
    
    @property
    def recurring_detector(self):
        """Lazy-load recurring detector."""
        if self._recurring_detector is None:
            self._recurring_detector = get_recurring_detector(self.organization_id)
        return self._recurring_detector
    
    def forecast(self, days: int = 30) -> CashFlowForecast:
        """
        Generate a cash flow forecast for the next N days.
        """
        now = datetime.now()
        end_date = now + timedelta(days=days)
        
        payments: List[PredictedPayment] = []
        
        # Source 1: Known due dates
        due_payments = self._get_known_due_dates(days)
        payments.extend(due_payments)
        
        # Source 2: Recurring subscriptions
        recurring_payments = self._predict_recurring(days)
        payments.extend(recurring_payments)
        
        # Source 3: Historical patterns
        historical_payments = self._predict_from_history(days)
        payments.extend(historical_payments)
        
        # Deduplicate (prefer known due dates over predictions)
        payments = self._deduplicate_payments(payments)
        
        # Sort by date
        payments.sort(key=lambda p: p.expected_date)
        
        # Calculate totals
        total = sum(p.amount for p in payments)
        
        # Calculate weekly breakdown
        weekly = self._calculate_weekly_breakdown(payments)
        
        # Calculate vendor breakdown
        by_vendor = defaultdict(float)
        for p in payments:
            by_vendor[p.vendor] += p.amount
        
        # Overall confidence (weighted average)
        if payments:
            confidence = sum(p.confidence * p.amount for p in payments) / total if total > 0 else 0.5
        else:
            confidence = 0.5
        
        logger.info(f"Forecast generated: ${total:,.2f} over {days} days, {len(payments)} payments")
        
        return CashFlowForecast(
            organization_id=self.organization_id,
            forecast_date=now.isoformat(),
            period_start=now.strftime("%Y-%m-%d"),
            period_end=end_date.strftime("%Y-%m-%d"),
            total_predicted=total,
            payments=payments,
            confidence=confidence,
            breakdown_by_week=dict(weekly),
            breakdown_by_vendor=dict(by_vendor),
        )
    
    def _get_known_due_dates(self, days: int) -> List[PredictedPayment]:
        """Get payments with known due dates."""
        payments = []
        
        try:
            if hasattr(self.db, 'get_upcoming_due'):
                upcoming = self.db.get_upcoming_due(
                    organization_id=self.organization_id,
                    days=days,
                ) or []
                
                for inv in upcoming:
                    payments.append(PredictedPayment(
                        vendor=inv.get("vendor", "Unknown"),
                        amount=inv.get("amount", 0),
                        currency=inv.get("currency", "USD"),
                        expected_date=inv.get("due_date", ""),
                        confidence=0.95,  # High confidence - known due date
                        source="due_date",
                        notes=f"Invoice #{inv.get('invoice_number', 'N/A')}",
                    ))
        except Exception as e:
            logger.warning(f"Failed to get due dates: {e}")
        
        return payments
    
    def _predict_recurring(self, days: int) -> List[PredictedPayment]:
        """Predict recurring subscription payments."""
        payments = []
        
        try:
            # Get all recurring patterns
            patterns = self.recurring_detector.get_all_patterns() if hasattr(self.recurring_detector, 'get_all_patterns') else []
            
            now = datetime.now()
            end_date = now + timedelta(days=days)
            
            for pattern in patterns:
                vendor = pattern.get("vendor", "")
                amount = pattern.get("typical_amount", 0)
                frequency_days = pattern.get("frequency_days", 30)
                last_date_str = pattern.get("last_invoice_date", "")
                
                if not vendor or amount <= 0 or frequency_days <= 0:
                    continue
                
                # Calculate next expected date
                try:
                    if last_date_str:
                        last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
                    else:
                        last_date = now - timedelta(days=frequency_days)
                    
                    next_date = last_date + timedelta(days=frequency_days)
                    
                    # Add all occurrences within the forecast period
                    while next_date <= end_date:
                        if next_date >= now:
                            payments.append(PredictedPayment(
                                vendor=vendor,
                                amount=amount,
                                currency=pattern.get("currency", "USD"),
                                expected_date=next_date.strftime("%Y-%m-%d"),
                                confidence=0.85,  # Good confidence for recurring
                                source="recurring",
                                notes=f"Every ~{frequency_days} days",
                            ))
                        next_date += timedelta(days=frequency_days)
                        
                except Exception as e:
                    logger.warning(f"Error predicting recurring for {vendor}: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to predict recurring: {e}")
        
        return payments
    
    def _predict_from_history(self, days: int) -> List[PredictedPayment]:
        """Predict based on historical patterns (non-recurring)."""
        payments = []
        
        try:
            # Get historical averages by vendor
            if hasattr(self.db, 'get_vendor_averages'):
                averages = self.db.get_vendor_averages(
                    organization_id=self.organization_id,
                    days=90,  # Look at last 90 days
                ) or {}
                
                # For vendors with regular but not strictly recurring payments
                for vendor, data in averages.items():
                    avg_amount = data.get("average_amount", 0)
                    invoice_count = data.get("invoice_count", 0)
                    avg_frequency = data.get("average_frequency_days", 0)
                    
                    # Skip if already covered by recurring
                    if avg_frequency > 0 and avg_frequency < 45:  # Probably recurring
                        continue
                    
                    # Estimate based on historical frequency
                    if invoice_count >= 2 and avg_amount > 0:
                        expected_in_period = (days / 90) * invoice_count
                        if expected_in_period >= 0.5:
                            # Predict at least one payment
                            mid_date = datetime.now() + timedelta(days=days // 2)
                            payments.append(PredictedPayment(
                                vendor=vendor,
                                amount=avg_amount * max(1, round(expected_in_period)),
                                currency="USD",
                                expected_date=mid_date.strftime("%Y-%m-%d"),
                                confidence=0.5,  # Lower confidence for estimates
                                source="historical",
                                notes=f"Based on {invoice_count} invoices in last 90 days",
                            ))
                            
        except Exception as e:
            logger.warning(f"Failed to predict from history: {e}")
        
        return payments
    
    def _deduplicate_payments(
        self,
        payments: List[PredictedPayment],
    ) -> List[PredictedPayment]:
        """Remove duplicate predictions, preferring higher confidence."""
        # Group by vendor + approximate date
        grouped = defaultdict(list)
        
        for payment in payments:
            # Create key based on vendor and week
            try:
                date = datetime.strptime(payment.expected_date, "%Y-%m-%d")
                week_key = date.strftime("%Y-W%W")
            except:
                week_key = "unknown"
            
            key = f"{payment.vendor}:{week_key}"
            grouped[key].append(payment)
        
        # Keep highest confidence for each group
        deduplicated = []
        for key, group in grouped.items():
            best = max(group, key=lambda p: p.confidence)
            deduplicated.append(best)
        
        return deduplicated
    
    def _calculate_weekly_breakdown(
        self,
        payments: List[PredictedPayment],
    ) -> Dict[str, float]:
        """Calculate weekly totals."""
        weekly = defaultdict(float)
        
        for payment in payments:
            try:
                date = datetime.strptime(payment.expected_date, "%Y-%m-%d")
                week_key = f"Week of {(date - timedelta(days=date.weekday())).strftime('%b %d')}"
                weekly[week_key] += payment.amount
            except:
                pass
        
        return dict(weekly)
    
    def get_next_week_summary(self) -> Dict[str, Any]:
        """Get a quick summary for the next 7 days."""
        forecast = self.forecast(days=7)
        
        return {
            "total": forecast.total_predicted,
            "payment_count": len(forecast.payments),
            "top_vendor": max(
                forecast.breakdown_by_vendor.items(),
                key=lambda x: x[1],
                default=("None", 0)
            ),
            "confidence": forecast.confidence,
        }


# Convenience function
def get_cashflow_predictor(organization_id: str = "default") -> CashFlowPredictionService:
    """Get a cash flow prediction service instance."""
    return CashFlowPredictionService(organization_id=organization_id)
