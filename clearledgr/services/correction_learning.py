"""
Correction Learning Service

When users correct the agent's decisions, learn from those corrections
to improve future accuracy.

Learns from:
- GL code corrections
- Vendor name corrections
- Amount corrections
- Classification corrections
- Approval/rejection overrides

Architecture: Part of the MEMORY LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class Correction:
    """A user correction to agent output."""
    correction_id: str
    correction_type: str  # "gl_code", "vendor", "amount", "classification", "approval"
    original_value: Any
    corrected_value: Any
    context: Dict[str, Any]
    user_id: str
    timestamp: str
    invoice_id: Optional[str] = None
    vendor: Optional[str] = None
    feedback: Optional[str] = None  # User's explanation


@dataclass
class LearningRule:
    """A rule learned from corrections."""
    rule_id: str
    rule_type: str
    condition: Dict[str, Any]
    action: Dict[str, Any]
    confidence: float
    learned_from: int  # Number of corrections
    created_at: str
    last_applied: Optional[str] = None
    success_rate: float = 1.0


class CorrectionLearningService:
    """
    Learns from user corrections to improve future decisions.
    
    Usage:
        service = CorrectionLearningService("org_123")
        
        # Record a correction
        service.record_correction(
            correction_type="gl_code",
            original_value="6100",
            corrected_value="6150",
            context={"vendor": "Stripe", "category": "software"},
            user_id="user@acme.com"
        )
        
        # Ask if agent should suggest learned value
        suggestion = service.suggest("gl_code", {"vendor": "Stripe"})
        if suggestion:
            print(f"Suggested GL: {suggestion['value']} (learned from {suggestion['learned_from']} corrections)")
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        
        # In-memory storage (would be database in production)
        self._corrections: List[Correction] = []
        self._learned_rules: Dict[str, LearningRule] = {}
        self._vendor_preferences: Dict[str, Dict[str, Any]] = defaultdict(dict)
    
    def record_correction(
        self,
        correction_type: str,
        original_value: Any,
        corrected_value: Any,
        context: Dict[str, Any],
        user_id: str,
        invoice_id: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a user correction and learn from it.
        
        Returns info about what was learned.
        """
        correction = Correction(
            correction_id=f"corr_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            context=context,
            user_id=user_id,
            timestamp=datetime.now().isoformat(),
            invoice_id=invoice_id,
            vendor=context.get("vendor"),
            feedback=feedback,
        )
        
        self._corrections.append(correction)
        
        # Learn from the correction
        learned = self._learn_from_correction(correction)
        
        logger.info(
            f"Recorded correction: {correction_type} "
            f"{original_value} -> {corrected_value} "
            f"(vendor: {context.get('vendor', 'N/A')})"
        )
        
        return {
            "correction_id": correction.correction_id,
            "learned": learned,
            "message": self._generate_learning_message(correction, learned),
        }
    
    def _learn_from_correction(self, correction: Correction) -> Dict[str, Any]:
        """Extract learning from a correction."""
        learned = {
            "rules_created": 0,
            "rules_updated": 0,
            "preferences_updated": [],
        }
        
        if correction.correction_type == "gl_code":
            learned.update(self._learn_gl_code(correction))
        
        elif correction.correction_type == "vendor":
            learned.update(self._learn_vendor_name(correction))
        
        elif correction.correction_type == "amount":
            learned.update(self._learn_amount_pattern(correction))
        
        elif correction.correction_type == "classification":
            learned.update(self._learn_classification(correction))
        
        elif correction.correction_type == "approval":
            learned.update(self._learn_approval_preference(correction))
        
        return learned
    
    def _learn_gl_code(self, correction: Correction) -> Dict[str, Any]:
        """Learn GL code preferences from correction."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # Create or update vendor GL preference
        rule_id = f"gl_{vendor.lower().replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            rule.learned_from += 1
            rule.action = {"gl_code": correction.corrected_value}
            rule.confidence = min(0.99, rule.confidence + 0.1)
            return {"rules_updated": 1}
        else:
            self._learned_rules[rule_id] = LearningRule(
                rule_id=rule_id,
                rule_type="gl_code",
                condition={"vendor": vendor},
                action={"gl_code": correction.corrected_value},
                confidence=0.7,  # Start with moderate confidence
                learned_from=1,
                created_at=datetime.now().isoformat(),
            )
            return {"rules_created": 1}
    
    def _learn_vendor_name(self, correction: Correction) -> Dict[str, Any]:
        """Learn vendor name normalization."""
        original = str(correction.original_value).lower()
        corrected = str(correction.corrected_value)
        
        # Store alias mapping
        rule_id = f"vendor_alias_{original.replace(' ', '_')}"
        
        self._learned_rules[rule_id] = LearningRule(
            rule_id=rule_id,
            rule_type="vendor_alias",
            condition={"raw_vendor": original},
            action={"normalized_vendor": corrected},
            confidence=0.9,  # High confidence for explicit correction
            learned_from=1,
            created_at=datetime.now().isoformat(),
        )
        
        return {"rules_created": 1, "preferences_updated": ["vendor_aliases"]}
    
    def _learn_amount_pattern(self, correction: Correction) -> Dict[str, Any]:
        """Learn amount expectations."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # Update vendor expected amount range
        corrected_amount = float(correction.corrected_value) if correction.corrected_value else 0
        
        if vendor not in self._vendor_preferences:
            self._vendor_preferences[vendor] = {}
        
        prefs = self._vendor_preferences[vendor]
        if "expected_amounts" not in prefs:
            prefs["expected_amounts"] = []
        
        prefs["expected_amounts"].append(corrected_amount)
        
        # Keep last 10 amounts
        prefs["expected_amounts"] = prefs["expected_amounts"][-10:]
        
        return {"preferences_updated": ["amount_expectations"]}
    
    def _learn_classification(self, correction: Correction) -> Dict[str, Any]:
        """Learn document classification patterns."""
        # Learn that certain patterns should be classified differently
        context = correction.context
        
        rule_id = f"classify_{context.get('sender', 'unknown')[:20]}"
        
        self._learned_rules[rule_id] = LearningRule(
            rule_id=rule_id,
            rule_type="classification",
            condition={
                "sender_contains": context.get("sender", ""),
                "subject_pattern": context.get("subject_pattern", ""),
            },
            action={"classification": correction.corrected_value},
            confidence=0.8,
            learned_from=1,
            created_at=datetime.now().isoformat(),
        )
        
        return {"rules_created": 1}
    
    def _learn_approval_preference(self, correction: Correction) -> Dict[str, Any]:
        """Learn approval preferences (e.g., always auto-approve this vendor)."""
        vendor = correction.vendor
        if not vendor:
            return {"rules_created": 0}
        
        # If user approved something agent wanted to flag, learn to be less strict
        # If user rejected something agent auto-approved, learn to be more careful
        
        original_decision = correction.original_value  # e.g., "flag_for_review"
        user_decision = correction.corrected_value  # e.g., "approved"
        
        if original_decision == "flag_for_review" and user_decision == "approved":
            # User is more permissive - lower the threshold for this vendor
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}
            
            self._vendor_preferences[vendor]["approval_bias"] = "permissive"
            self._vendor_preferences[vendor]["auto_approve_threshold_adj"] = -0.1
            
            return {"preferences_updated": ["approval_threshold"]}
        
        elif original_decision == "auto_approved" and user_decision == "rejected":
            # User is more strict - raise the threshold
            if vendor not in self._vendor_preferences:
                self._vendor_preferences[vendor] = {}
            
            self._vendor_preferences[vendor]["approval_bias"] = "strict"
            self._vendor_preferences[vendor]["auto_approve_threshold_adj"] = 0.1
            
            return {"preferences_updated": ["approval_threshold"]}
        
        return {"rules_created": 0}
    
    def suggest(
        self,
        suggestion_type: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Get a suggestion based on learned rules.
        
        Returns None if no learned rule applies.
        """
        if suggestion_type == "gl_code":
            return self._suggest_gl_code(context)
        
        elif suggestion_type == "vendor":
            return self._suggest_vendor_name(context)
        
        elif suggestion_type == "approval_threshold":
            return self._suggest_approval_threshold(context)
        
        return None
    
    def _suggest_gl_code(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest GL code based on learned patterns."""
        vendor = context.get("vendor", "")
        if not vendor:
            return None
        
        rule_id = f"gl_{vendor.lower().replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            
            # Update last applied
            rule.last_applied = datetime.now().isoformat()
            
            return {
                "value": rule.action.get("gl_code"),
                "confidence": rule.confidence,
                "learned_from": rule.learned_from,
                "message": f"Learned from {rule.learned_from} previous correction(s)",
            }
        
        return None
    
    def _suggest_vendor_name(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest normalized vendor name."""
        raw_vendor = context.get("raw_vendor", "").lower()
        if not raw_vendor:
            return None
        
        rule_id = f"vendor_alias_{raw_vendor.replace(' ', '_')}"
        
        if rule_id in self._learned_rules:
            rule = self._learned_rules[rule_id]
            return {
                "value": rule.action.get("normalized_vendor"),
                "confidence": rule.confidence,
                "learned_from": rule.learned_from,
            }
        
        return None
    
    def _suggest_approval_threshold(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Suggest approval threshold adjustment for vendor."""
        vendor = context.get("vendor", "")
        if not vendor or vendor not in self._vendor_preferences:
            return None
        
        prefs = self._vendor_preferences[vendor]
        
        if "auto_approve_threshold_adj" in prefs:
            return {
                "adjustment": prefs["auto_approve_threshold_adj"],
                "bias": prefs.get("approval_bias", "neutral"),
                "message": f"Adjusted based on previous corrections",
            }
        
        return None
    
    def _generate_learning_message(
        self,
        correction: Correction,
        learned: Dict[str, Any],
    ) -> str:
        """Generate a human-readable message about what was learned."""
        messages = []
        
        if learned.get("rules_created", 0) > 0:
            if correction.correction_type == "gl_code":
                messages.append(
                    f"Got it! I'll use GL {correction.corrected_value} for "
                    f"{correction.vendor} from now on."
                )
            elif correction.correction_type == "vendor":
                messages.append(
                    f"Learned: '{correction.original_value}' = '{correction.corrected_value}'"
                )
            else:
                messages.append(f"Created {learned['rules_created']} new rule(s)")
        
        if learned.get("rules_updated", 0) > 0:
            messages.append(f"Updated existing rule (now more confident)")
        
        if learned.get("preferences_updated"):
            prefs = ", ".join(learned["preferences_updated"])
            messages.append(f"Updated preferences: {prefs}")
        
        return " ".join(messages) if messages else "Correction recorded."
    
    def get_learning_stats(self) -> Dict[str, Any]:
        """Get statistics about what the agent has learned."""
        return {
            "total_corrections": len(self._corrections),
            "learned_rules": len(self._learned_rules),
            "vendor_preferences": len(self._vendor_preferences),
            "rules_by_type": self._count_rules_by_type(),
            "recent_corrections": len([
                c for c in self._corrections
                if (datetime.now() - datetime.fromisoformat(c.timestamp)).days <= 7
            ]),
        }
    
    def _count_rules_by_type(self) -> Dict[str, int]:
        """Count learned rules by type."""
        counts = defaultdict(int)
        for rule in self._learned_rules.values():
            counts[rule.rule_type] += 1
        return dict(counts)
    
    def ask_about_correction(
        self,
        correction_type: str,
        original_value: Any,
        corrected_value: Any,
        vendor: Optional[str] = None,
    ) -> str:
        """
        Generate a question to ask the user about applying a correction broadly.
        
        Called after a correction to see if user wants to apply it to all similar cases.
        """
        if correction_type == "gl_code" and vendor:
            return (
                f"Should I use GL {corrected_value} for all future "
                f"invoices from {vendor}?"
            )
        
        elif correction_type == "vendor":
            return (
                f"Should I always recognize '{original_value}' as '{corrected_value}'?"
            )
        
        elif correction_type == "approval" and vendor:
            if corrected_value == "approved":
                return (
                    f"Should I auto-approve similar invoices from {vendor} in the future?"
                )
            else:
                return (
                    f"Should I always flag {vendor} invoices for manual review?"
                )
        
        return ""


# Convenience function
def get_correction_learning(organization_id: str = "default") -> CorrectionLearningService:
    """Get a correction learning service instance."""
    return CorrectionLearningService(organization_id=organization_id)
