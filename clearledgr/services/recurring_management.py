"""
Recurring Invoice Management Service

Manage subscription/recurring invoice rules:
- View all recurring patterns detected
- Configure auto-approval rules per vendor
- Set amount tolerance thresholds
- Manage subscription schedules

This gives users visibility and control over automatic processing.
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import uuid

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class RecurringAction(Enum):
    """Action to take for recurring invoices."""
    AUTO_APPROVE = "auto_approve"
    SEND_FOR_APPROVAL = "send_for_approval"
    FLAG_FOR_REVIEW = "flag_for_review"
    IGNORE = "ignore"


class RecurringFrequency(Enum):
    """Invoice frequency."""
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


@dataclass
class RecurringRule:
    """A rule for handling recurring invoices from a vendor."""
    rule_id: str
    vendor: str
    vendor_aliases: List[str] = field(default_factory=list)
    
    # Expected pattern
    expected_frequency: RecurringFrequency = RecurringFrequency.MONTHLY
    expected_amount: Optional[float] = None
    amount_tolerance_pct: float = 5.0  # Allow 5% variance
    
    # Actions
    action: RecurringAction = RecurringAction.AUTO_APPROVE
    require_amount_match: bool = True
    notify_on_auto_approve: bool = True
    
    # GL assignment
    default_gl_code: Optional[str] = None
    gl_description: Optional[str] = None
    
    # Tracking
    last_invoice_date: Optional[str] = None
    next_expected_date: Optional[str] = None
    total_invoices: int = 0
    total_amount: float = 0.0
    
    # Metadata
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "vendor": self.vendor,
            "vendor_aliases": self.vendor_aliases,
            "expected_frequency": self.expected_frequency.value,
            "expected_amount": self.expected_amount,
            "amount_tolerance_pct": self.amount_tolerance_pct,
            "action": self.action.value,
            "require_amount_match": self.require_amount_match,
            "notify_on_auto_approve": self.notify_on_auto_approve,
            "default_gl_code": self.default_gl_code,
            "gl_description": self.gl_description,
            "last_invoice_date": self.last_invoice_date,
            "next_expected_date": self.next_expected_date,
            "total_invoices": self.total_invoices,
            "total_amount": self.total_amount,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }


@dataclass
class RecurringInvoice:
    """A recurring invoice instance."""
    invoice_id: str
    rule_id: Optional[str]
    vendor: str
    amount: float
    currency: str
    invoice_date: str
    
    # Pattern match info
    matched_rule: bool = False
    amount_variance_pct: Optional[float] = None
    days_from_expected: Optional[int] = None
    
    # Auto-processing result
    auto_approved: bool = False
    auto_approval_reason: Optional[str] = None
    flagged_reason: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "rule_id": self.rule_id,
            "vendor": self.vendor,
            "amount": self.amount,
            "currency": self.currency,
            "invoice_date": self.invoice_date,
            "matched_rule": self.matched_rule,
            "amount_variance_pct": self.amount_variance_pct,
            "days_from_expected": self.days_from_expected,
            "auto_approved": self.auto_approved,
            "auto_approval_reason": self.auto_approval_reason,
            "flagged_reason": self.flagged_reason,
        }


class RecurringManagementService:
    """
    Manage recurring invoice rules and processing.
    
    Features:
    - Create/update/delete recurring rules
    - Match incoming invoices to rules
    - Auto-approve based on rules
    - Track subscription history
    - Predict next invoice
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self._rules: Dict[str, RecurringRule] = {}
        self._history: Dict[str, List[RecurringInvoice]] = {}  # By rule_id
        self._load_rules()
    
    def _load_rules(self):
        """Load rules from database."""
        try:
            rules = self.db.get_recurring_rules(self.organization_id)
            for r in rules:
                rule = RecurringRule(
                    rule_id=r["rule_id"],
                    vendor=r["vendor"],
                    vendor_aliases=r.get("vendor_aliases", []),
                    expected_frequency=RecurringFrequency(r.get("expected_frequency", "monthly")),
                    expected_amount=r.get("expected_amount"),
                    amount_tolerance_pct=r.get("amount_tolerance_pct", 5.0),
                    action=RecurringAction(r.get("action", "auto_approve")),
                    require_amount_match=r.get("require_amount_match", True),
                    default_gl_code=r.get("default_gl_code"),
                    enabled=r.get("enabled", True),
                )
                self._rules[rule.rule_id] = rule
        except Exception:
            pass
    
    def create_rule(
        self,
        vendor: str,
        expected_frequency: RecurringFrequency = RecurringFrequency.MONTHLY,
        expected_amount: Optional[float] = None,
        amount_tolerance_pct: float = 5.0,
        action: RecurringAction = RecurringAction.AUTO_APPROVE,
        default_gl_code: Optional[str] = None,
        vendor_aliases: Optional[List[str]] = None,
        notes: Optional[str] = None,
    ) -> RecurringRule:
        """
        Create a new recurring invoice rule.
        
        Args:
            vendor: Primary vendor name
            expected_frequency: How often invoices arrive
            expected_amount: Expected amount (None = any amount)
            amount_tolerance_pct: Allowed variance percentage
            action: What to do when invoice matches
            default_gl_code: GL code to assign
            vendor_aliases: Other names for this vendor
            notes: User notes
        
        Returns:
            RecurringRule object
        """
        rule_id = f"REC-{uuid.uuid4().hex[:8]}"
        
        rule = RecurringRule(
            rule_id=rule_id,
            vendor=vendor,
            vendor_aliases=vendor_aliases or [],
            expected_frequency=expected_frequency,
            expected_amount=expected_amount,
            amount_tolerance_pct=amount_tolerance_pct,
            action=action,
            default_gl_code=default_gl_code,
            notes=notes,
        )
        
        self._rules[rule_id] = rule
        self._save_rule(rule)
        
        logger.info(f"Created recurring rule {rule_id} for {vendor}")
        
        return rule
    
    def update_rule(
        self,
        rule_id: str,
        updates: Dict[str, Any],
    ) -> RecurringRule:
        """Update an existing rule."""
        rule = self._rules.get(rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        
        # Apply updates
        if "vendor" in updates:
            rule.vendor = updates["vendor"]
        if "vendor_aliases" in updates:
            rule.vendor_aliases = updates["vendor_aliases"]
        if "expected_frequency" in updates:
            rule.expected_frequency = RecurringFrequency(updates["expected_frequency"])
        if "expected_amount" in updates:
            rule.expected_amount = updates["expected_amount"]
        if "amount_tolerance_pct" in updates:
            rule.amount_tolerance_pct = updates["amount_tolerance_pct"]
        if "action" in updates:
            rule.action = RecurringAction(updates["action"])
        if "require_amount_match" in updates:
            rule.require_amount_match = updates["require_amount_match"]
        if "notify_on_auto_approve" in updates:
            rule.notify_on_auto_approve = updates["notify_on_auto_approve"]
        if "default_gl_code" in updates:
            rule.default_gl_code = updates["default_gl_code"]
        if "enabled" in updates:
            rule.enabled = updates["enabled"]
        if "notes" in updates:
            rule.notes = updates["notes"]
        
        rule.updated_at = datetime.now().isoformat()
        self._save_rule(rule)
        
        return rule
    
    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            try:
                self.db.delete_recurring_rule(self.organization_id, rule_id)
            except Exception:
                pass
            return True
        return False
    
    def get_rule(self, rule_id: str) -> Optional[RecurringRule]:
        """Get a rule by ID."""
        return self._rules.get(rule_id)
    
    def get_all_rules(self, enabled_only: bool = True) -> List[RecurringRule]:
        """Get all rules."""
        rules = list(self._rules.values())
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        return sorted(rules, key=lambda r: r.vendor.lower())
    
    def find_matching_rule(
        self,
        vendor: str,
        amount: Optional[float] = None,
    ) -> Optional[RecurringRule]:
        """
        Find a rule that matches the vendor.
        
        Args:
            vendor: Vendor name to match
            amount: Invoice amount (for amount matching)
        
        Returns:
            Matching rule or None
        """
        vendor_lower = vendor.lower()
        
        for rule in self._rules.values():
            if not rule.enabled:
                continue
            
            # Check vendor name match
            vendor_match = (
                vendor_lower == rule.vendor.lower() or
                any(vendor_lower == alias.lower() for alias in rule.vendor_aliases) or
                vendor_lower in rule.vendor.lower() or
                rule.vendor.lower() in vendor_lower
            )
            
            if vendor_match:
                # Check amount if required
                if rule.require_amount_match and rule.expected_amount and amount:
                    variance = abs(amount - rule.expected_amount) / rule.expected_amount * 100
                    if variance > rule.amount_tolerance_pct:
                        continue  # Amount doesn't match
                
                return rule
        
        return None
    
    def process_invoice(
        self,
        invoice_id: str,
        vendor: str,
        amount: float,
        currency: str = "USD",
        invoice_date: Optional[str] = None,
    ) -> RecurringInvoice:
        """
        Process an invoice against recurring rules.
        
        Args:
            invoice_id: The invoice ID
            vendor: Vendor name
            amount: Invoice amount
            currency: Currency code
            invoice_date: Invoice date (ISO format)
        
        Returns:
            RecurringInvoice with processing result
        """
        invoice = RecurringInvoice(
            invoice_id=invoice_id,
            rule_id=None,
            vendor=vendor,
            amount=amount,
            currency=currency,
            invoice_date=invoice_date or datetime.now().isoformat(),
        )
        
        # Find matching rule
        rule = self.find_matching_rule(vendor, amount)
        
        if rule:
            invoice.rule_id = rule.rule_id
            invoice.matched_rule = True
            
            # Calculate variance if expected amount set
            if rule.expected_amount:
                invoice.amount_variance_pct = abs(amount - rule.expected_amount) / rule.expected_amount * 100
            
            # Calculate days from expected
            if rule.next_expected_date:
                try:
                    expected = datetime.fromisoformat(rule.next_expected_date.replace("Z", "+00:00"))
                    actual = datetime.fromisoformat(invoice_date) if invoice_date else datetime.now()
                    invoice.days_from_expected = (actual - expected).days
                except Exception:
                    pass
            
            # Apply action
            if rule.action == RecurringAction.AUTO_APPROVE:
                # Check if amount is within tolerance
                if invoice.amount_variance_pct and invoice.amount_variance_pct > rule.amount_tolerance_pct:
                    invoice.flagged_reason = f"Amount variance {invoice.amount_variance_pct:.1f}% exceeds tolerance {rule.amount_tolerance_pct}%"
                else:
                    invoice.auto_approved = True
                    invoice.auto_approval_reason = f"Matched recurring rule: {rule.vendor}"
                    
            elif rule.action == RecurringAction.FLAG_FOR_REVIEW:
                invoice.flagged_reason = f"Rule configured for review: {rule.notes or 'No notes'}"
                
            elif rule.action == RecurringAction.IGNORE:
                invoice.flagged_reason = "Ignored by rule"
            
            # Update rule stats
            rule.last_invoice_date = invoice.invoice_date
            rule.total_invoices += 1
            rule.total_amount += amount
            rule.next_expected_date = self._calculate_next_expected(rule)
            rule.updated_at = datetime.now().isoformat()
            self._save_rule(rule)
            
            # Track history
            if rule.rule_id not in self._history:
                self._history[rule.rule_id] = []
            self._history[rule.rule_id].append(invoice)
        
        return invoice
    
    def _calculate_next_expected(self, rule: RecurringRule) -> str:
        """Calculate the next expected invoice date."""
        if not rule.last_invoice_date:
            return datetime.now().isoformat()
        
        try:
            last = datetime.fromisoformat(rule.last_invoice_date.replace("Z", "+00:00"))
        except Exception:
            last = datetime.now()
        
        days_map = {
            RecurringFrequency.WEEKLY: 7,
            RecurringFrequency.BIWEEKLY: 14,
            RecurringFrequency.MONTHLY: 30,
            RecurringFrequency.QUARTERLY: 90,
            RecurringFrequency.ANNUAL: 365,
            RecurringFrequency.IRREGULAR: 30,  # Default to monthly
        }
        
        days = days_map.get(rule.expected_frequency, 30)
        next_date = last + timedelta(days=days)
        
        return next_date.isoformat()
    
    def get_rule_history(
        self,
        rule_id: str,
        limit: int = 20,
    ) -> List[RecurringInvoice]:
        """Get invoice history for a rule."""
        history = self._history.get(rule_id, [])
        return sorted(history, key=lambda i: i.invoice_date, reverse=True)[:limit]
    
    def get_upcoming_invoices(
        self,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get expected invoices in the next N days."""
        upcoming = []
        now = datetime.now()
        cutoff = now + timedelta(days=days)
        
        for rule in self._rules.values():
            if not rule.enabled or not rule.next_expected_date:
                continue
            
            try:
                expected = datetime.fromisoformat(rule.next_expected_date.replace("Z", "+00:00"))
                if now <= expected <= cutoff:
                    upcoming.append({
                        "rule_id": rule.rule_id,
                        "vendor": rule.vendor,
                        "expected_date": rule.next_expected_date,
                        "expected_amount": rule.expected_amount,
                        "frequency": rule.expected_frequency.value,
                        "days_until": (expected - now).days,
                        "action": rule.action.value,
                    })
            except Exception:
                continue
        
        return sorted(upcoming, key=lambda x: x["days_until"])
    
    def get_subscription_summary(self) -> Dict[str, Any]:
        """Get summary of all subscriptions."""
        rules = list(self._rules.values())
        active_rules = [r for r in rules if r.enabled]
        
        # Calculate monthly spend
        monthly_spend = 0
        for rule in active_rules:
            if rule.expected_amount:
                multiplier = {
                    RecurringFrequency.WEEKLY: 4.33,
                    RecurringFrequency.BIWEEKLY: 2.17,
                    RecurringFrequency.MONTHLY: 1,
                    RecurringFrequency.QUARTERLY: 0.33,
                    RecurringFrequency.ANNUAL: 0.083,
                    RecurringFrequency.IRREGULAR: 1,
                }.get(rule.expected_frequency, 1)
                monthly_spend += rule.expected_amount * multiplier
        
        # Count by action
        by_action = {}
        for rule in active_rules:
            action = rule.action.value
            if action not in by_action:
                by_action[action] = 0
            by_action[action] += 1
        
        return {
            "total_rules": len(rules),
            "active_rules": len(active_rules),
            "estimated_monthly_spend": round(monthly_spend, 2),
            "by_action": by_action,
            "upcoming_7_days": len(self.get_upcoming_invoices(7)),
            "upcoming_30_days": len(self.get_upcoming_invoices(30)),
        }
    
    def detect_new_recurring(
        self,
        vendor: str,
        invoices: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if a vendor's invoices form a recurring pattern.
        Used to suggest new rules.
        
        Args:
            vendor: Vendor name
            invoices: List of historical invoices
        
        Returns:
            Suggested rule parameters or None
        """
        if len(invoices) < 2:
            return None
        
        # Sort by date
        sorted_inv = sorted(invoices, key=lambda i: i.get("date", ""))
        
        # Calculate intervals between invoices
        intervals = []
        amounts = []
        
        for i in range(1, len(sorted_inv)):
            try:
                prev_date = datetime.fromisoformat(sorted_inv[i-1]["date"].replace("Z", "+00:00"))
                curr_date = datetime.fromisoformat(sorted_inv[i]["date"].replace("Z", "+00:00"))
                intervals.append((curr_date - prev_date).days)
            except Exception:
                continue
            
            amounts.append(sorted_inv[i].get("amount", 0))
        
        if not intervals:
            return None
        
        # Detect frequency
        avg_interval = sum(intervals) / len(intervals)
        
        if avg_interval < 10:
            frequency = RecurringFrequency.WEEKLY
        elif avg_interval < 20:
            frequency = RecurringFrequency.BIWEEKLY
        elif avg_interval < 45:
            frequency = RecurringFrequency.MONTHLY
        elif avg_interval < 120:
            frequency = RecurringFrequency.QUARTERLY
        elif avg_interval < 400:
            frequency = RecurringFrequency.ANNUAL
        else:
            return None  # Too irregular
        
        # Calculate amount variance
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            max_variance = max(abs(a - avg_amount) / avg_amount * 100 for a in amounts) if avg_amount > 0 else 100
        else:
            avg_amount = None
            max_variance = 100
        
        # Only suggest if pattern is consistent
        if max_variance > 20:
            return None
        
        return {
            "vendor": vendor,
            "suggested_frequency": frequency.value,
            "suggested_amount": round(avg_amount, 2) if avg_amount else None,
            "invoice_count": len(invoices),
            "avg_interval_days": round(avg_interval, 1),
            "amount_variance_pct": round(max_variance, 1),
            "confidence": min(0.9, len(invoices) * 0.15),  # More invoices = higher confidence
        }
    
    def _save_rule(self, rule: RecurringRule) -> None:
        """Save rule to database."""
        try:
            self.db.save_recurring_rule(self.organization_id, rule.to_dict())
        except Exception as e:
            logger.warning(f"Failed to save recurring rule: {e}")


# Singleton
_recurring_services: Dict[str, RecurringManagementService] = {}


def get_recurring_management(organization_id: str = "default") -> RecurringManagementService:
    """Get recurring management service for an organization."""
    if organization_id not in _recurring_services:
        _recurring_services[organization_id] = RecurringManagementService(organization_id)
    return _recurring_services[organization_id]
