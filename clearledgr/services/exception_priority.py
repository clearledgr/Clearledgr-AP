"""
Exception Priority Classification for Clearledgr v1 (Autonomous Edition)

Implements priority classification from product_spec_updated.md:
- Critical: amount > $10,000, duplicates, posting failures
- High: amount > $1,000, missing counterparty
- Medium: timing differences, minor amount mismatches
- Low: < $50 timing differences

Priority determines:
- Notification urgency (Critical = immediate Slack alert)
- Display order in CLEXCEPTIONS sheet
- Daily summary grouping
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime


class ExceptionPriority(Enum):
    """Exception priority levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ExceptionType(Enum):
    """Types of reconciliation exceptions."""
    MISSING_COUNTERPARTY = "missing_counterparty"
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE_DETECTED = "duplicate_detected"
    TIMING_DIFFERENCE = "timing_difference"
    UNMATCHED_GATEWAY = "unmatched_gateway"
    UNMATCHED_BANK = "unmatched_bank"
    UNMATCHED_SAP = "unmatched_sap"
    POSTING_FAILED = "posting_failed"
    VALIDATION_ERROR = "validation_error"
    FEE_DISCREPANCY = "fee_discrepancy"
    CURRENCY_MISMATCH = "currency_mismatch"
    PARTIAL_MATCH = "partial_match"


@dataclass
class ClassifiedException:
    """Exception with priority classification and metadata."""
    exception_id: str
    source: str  # gateway, bank, sap
    transaction_ids: List[str]
    amount: float
    currency: str
    date: Optional[str]
    description: str
    
    exception_type: ExceptionType
    priority: ExceptionPriority
    
    reason: str
    explanation: str
    suggested_action: str
    
    assigned_to: Optional[str] = None
    status: str = "Pending"
    notes: Optional[str] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "source": self.source,
            "transaction_ids": self.transaction_ids,
            "amount": self.amount,
            "currency": self.currency,
            "date": self.date,
            "description": self.description,
            "exception_type": self.exception_type.value,
            "priority": self.priority.value,
            "reason": self.reason,
            "explanation": self.explanation,
            "suggested_action": self.suggested_action,
            "assigned_to": self.assigned_to,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }
    
    def to_sheets_row(self) -> List[Any]:
        """Convert to CLEXCEPTIONS sheet row format."""
        return [
            self.exception_id,
            self.source,
            ", ".join(self.transaction_ids),
            self.date or "",
            self.amount,
            self.description,
            self.reason,
            self.explanation,
            self.suggested_action,
            self.priority.value,
            self.status,
            self.assigned_to or "",
            self.notes or "",
        ]


class ExceptionPriorityClassifier:
    """
    Classifies reconciliation exceptions by priority level.
    
    Priority rules from product_spec_updated.md:
    - Critical: amount > $10,000, duplicates, posting errors
    - High: amount > $1,000, missing counterparty  
    - Medium: timing differences > $50
    - Low: timing differences < $50
    """
    
    # Threshold amounts (in base currency)
    CRITICAL_AMOUNT_THRESHOLD = 10000.0
    HIGH_AMOUNT_THRESHOLD = 1000.0
    LOW_TIMING_THRESHOLD = 50.0
    
    # Exception types that are always critical
    CRITICAL_TYPES = {
        ExceptionType.DUPLICATE_DETECTED,
        ExceptionType.POSTING_FAILED,
    }
    
    # Exception types that are high priority by default
    HIGH_TYPES = {
        ExceptionType.MISSING_COUNTERPARTY,
        ExceptionType.CURRENCY_MISMATCH,
    }
    
    def classify_priority(
        self,
        exception_type: ExceptionType,
        amount: float,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> ExceptionPriority:
        """
        Determine priority level for an exception.
        
        Args:
            exception_type: Type of exception
            amount: Transaction amount (absolute value)
            additional_context: Extra info (e.g., variance_pct, days_difference)
            
        Returns:
            ExceptionPriority level
        """
        amount = abs(amount)
        context = additional_context or {}
        
        # Critical by type
        if exception_type in self.CRITICAL_TYPES:
            return ExceptionPriority.CRITICAL
        
        # Critical by amount
        if amount >= self.CRITICAL_AMOUNT_THRESHOLD:
            return ExceptionPriority.CRITICAL
        
        # High by type
        if exception_type in self.HIGH_TYPES:
            return ExceptionPriority.HIGH
        
        # High by amount
        if amount >= self.HIGH_AMOUNT_THRESHOLD:
            return ExceptionPriority.HIGH
        
        # Timing differences
        if exception_type == ExceptionType.TIMING_DIFFERENCE:
            if amount < self.LOW_TIMING_THRESHOLD:
                return ExceptionPriority.LOW
            else:
                return ExceptionPriority.MEDIUM
        
        # Amount mismatches
        if exception_type == ExceptionType.AMOUNT_MISMATCH:
            variance_pct = context.get("variance_pct", 0)
            if variance_pct > 10:
                return ExceptionPriority.HIGH
            elif variance_pct > 5:
                return ExceptionPriority.MEDIUM
            else:
                return ExceptionPriority.LOW
        
        # Default to medium for unmatched items
        if exception_type in {
            ExceptionType.UNMATCHED_GATEWAY,
            ExceptionType.UNMATCHED_BANK,
            ExceptionType.UNMATCHED_SAP,
        }:
            if amount >= self.HIGH_AMOUNT_THRESHOLD:
                return ExceptionPriority.HIGH
            elif amount >= self.LOW_TIMING_THRESHOLD:
                return ExceptionPriority.MEDIUM
            else:
                return ExceptionPriority.LOW
        
        # Default to medium
        return ExceptionPriority.MEDIUM
    
    def classify_exception(
        self,
        exception_id: str,
        source: str,
        transaction_ids: List[str],
        amount: float,
        currency: str,
        date: Optional[str],
        description: str,
        exception_type: ExceptionType,
        reason: str,
        explanation: str,
        suggested_action: str,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> ClassifiedException:
        """
        Create a classified exception with priority.
        
        Args:
            Various exception fields
            
        Returns:
            ClassifiedException with priority assigned
        """
        priority = self.classify_priority(exception_type, amount, additional_context)
        
        return ClassifiedException(
            exception_id=exception_id,
            source=source,
            transaction_ids=transaction_ids,
            amount=amount,
            currency=currency,
            date=date,
            description=description,
            exception_type=exception_type,
            priority=priority,
            reason=reason,
            explanation=explanation,
            suggested_action=suggested_action,
        )
    
    def classify_from_raw(
        self,
        raw_exception: Dict[str, Any],
    ) -> ClassifiedException:
        """
        Classify an exception from raw reconciliation output.
        
        Args:
            raw_exception: Raw exception dict from reconciliation engine
            
        Returns:
            ClassifiedException with priority assigned
        """
        # Extract fields
        exception_id = raw_exception.get("exception_id") or f"exc_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        source = raw_exception.get("source", "unknown")
        
        tx_ids = raw_exception.get("tx_ids", [])
        if isinstance(tx_ids, str):
            tx_ids = [tx_ids]
        elif not tx_ids:
            tx_ids = [raw_exception.get("tx_id", "")]
        
        amount = abs(float(raw_exception.get("amounts", 0) or raw_exception.get("amount", 0) or 0))
        currency = raw_exception.get("currency", "EUR")
        date = raw_exception.get("dates") or raw_exception.get("date")
        description = raw_exception.get("description", "")
        
        reason = raw_exception.get("reason", "no_match")
        explanation = raw_exception.get("llm_explanation", "")
        suggested_action = raw_exception.get("suggested_action", "Review and resolve manually")
        
        # Determine exception type from reason
        exception_type = self._reason_to_type(reason, raw_exception)
        
        # Additional context for priority calculation
        context = {
            "variance_pct": raw_exception.get("variance_pct", 0),
            "days_difference": raw_exception.get("days_difference", 0),
        }
        
        return self.classify_exception(
            exception_id=exception_id,
            source=source,
            transaction_ids=tx_ids,
            amount=amount,
            currency=currency,
            date=date,
            description=description,
            exception_type=exception_type,
            reason=reason,
            explanation=explanation,
            suggested_action=suggested_action,
            additional_context=context,
        )
    
    def _reason_to_type(
        self, reason: str, raw_exception: Dict[str, Any]
    ) -> ExceptionType:
        """Map reason string to ExceptionType."""
        reason_lower = reason.lower()
        source = raw_exception.get("source", "").lower()
        
        if "duplicate" in reason_lower:
            return ExceptionType.DUPLICATE_DETECTED
        elif "counterparty" in reason_lower or "missing" in reason_lower:
            return ExceptionType.MISSING_COUNTERPARTY
        elif "amount" in reason_lower or "mismatch" in reason_lower:
            return ExceptionType.AMOUNT_MISMATCH
        elif "timing" in reason_lower or "date" in reason_lower:
            return ExceptionType.TIMING_DIFFERENCE
        elif "fee" in reason_lower:
            return ExceptionType.FEE_DISCREPANCY
        elif "currency" in reason_lower:
            return ExceptionType.CURRENCY_MISMATCH
        elif "failed" in reason_lower or "error" in reason_lower:
            return ExceptionType.POSTING_FAILED
        elif "validation" in reason_lower:
            return ExceptionType.VALIDATION_ERROR
        elif "partial" in reason_lower:
            return ExceptionType.PARTIAL_MATCH
        elif source == "gateway":
            return ExceptionType.UNMATCHED_GATEWAY
        elif source == "bank":
            return ExceptionType.UNMATCHED_BANK
        elif source == "sap" or source == "internal":
            return ExceptionType.UNMATCHED_SAP
        else:
            return ExceptionType.UNMATCHED_GATEWAY
    
    def sort_by_priority(
        self, exceptions: List[ClassifiedException]
    ) -> List[ClassifiedException]:
        """Sort exceptions by priority (critical first) then by amount."""
        priority_order = {
            ExceptionPriority.CRITICAL: 0,
            ExceptionPriority.HIGH: 1,
            ExceptionPriority.MEDIUM: 2,
            ExceptionPriority.LOW: 3,
        }
        
        return sorted(
            exceptions,
            key=lambda e: (priority_order[e.priority], -e.amount)
        )
    
    def group_by_priority(
        self, exceptions: List[ClassifiedException]
    ) -> Dict[ExceptionPriority, List[ClassifiedException]]:
        """Group exceptions by priority level."""
        groups: Dict[ExceptionPriority, List[ClassifiedException]] = {
            ExceptionPriority.CRITICAL: [],
            ExceptionPriority.HIGH: [],
            ExceptionPriority.MEDIUM: [],
            ExceptionPriority.LOW: [],
        }
        
        for exc in exceptions:
            groups[exc.priority].append(exc)
        
        return groups
    
    def generate_summary(
        self, exceptions: List[ClassifiedException]
    ) -> Dict[str, Any]:
        """
        Generate exception summary for Slack notifications.
        
        Matches product_spec_updated.md daily summary format.
        """
        groups = self.group_by_priority(exceptions)
        
        critical = groups[ExceptionPriority.CRITICAL]
        high = groups[ExceptionPriority.HIGH]
        medium = groups[ExceptionPriority.MEDIUM]
        low = groups[ExceptionPriority.LOW]
        
        # Calculate totals by type
        type_counts: Dict[str, int] = {}
        type_amounts: Dict[str, float] = {}
        
        for exc in exceptions:
            type_key = exc.exception_type.value
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
            type_amounts[type_key] = type_amounts.get(type_key, 0) + exc.amount
        
        # Format breakdown for Slack
        breakdown_lines = []
        if type_counts.get("missing_counterparty"):
            count = type_counts["missing_counterparty"]
            amount = type_amounts["missing_counterparty"]
            breakdown_lines.append(f"• {count} missing bank counterparty (~${amount:,.0f})")
        if type_counts.get("amount_mismatch"):
            count = type_counts["amount_mismatch"]
            breakdown_lines.append(f"• {count} amount mismatches >$100")
        if type_counts.get("duplicate_detected"):
            count = type_counts["duplicate_detected"]
            breakdown_lines.append(f"• {count} duplicate transaction detected")
        if type_counts.get("timing_difference"):
            count = type_counts["timing_difference"]
            low_count = len([e for e in exceptions 
                           if e.exception_type == ExceptionType.TIMING_DIFFERENCE 
                           and e.amount < self.LOW_TIMING_THRESHOLD])
            breakdown_lines.append(f"• {count} timing differences ({low_count} all <$50)")
        
        return {
            "total_count": len(exceptions),
            "critical_count": len(critical),
            "high_count": len(high),
            "medium_count": len(medium),
            "low_count": len(low),
            "breakdown": breakdown_lines,
            "critical_exceptions": [e.to_dict() for e in critical[:5]],
            "total_amount": sum(e.amount for e in exceptions),
            "requires_immediate_attention": len(critical) > 0,
        }
    
    def get_alerts(
        self, exceptions: List[ClassifiedException]
    ) -> List[ClassifiedException]:
        """
        Get exceptions that require immediate Slack alerts.
        
        Per spec: amount > $10,000, duplicates, posting failures
        """
        return [
            e for e in exceptions 
            if e.priority == ExceptionPriority.CRITICAL
        ]


# Convenience function for backward compatibility
def classify_exceptions(
    raw_exceptions: List[Dict[str, Any]]
) -> List[ClassifiedException]:
    """
    Classify a list of raw exceptions.
    
    Args:
        raw_exceptions: List of raw exception dicts
        
    Returns:
        List of ClassifiedException sorted by priority
    """
    classifier = ExceptionPriorityClassifier()
    classified = [classifier.classify_from_raw(e) for e in raw_exceptions]
    return classifier.sort_by_priority(classified)
