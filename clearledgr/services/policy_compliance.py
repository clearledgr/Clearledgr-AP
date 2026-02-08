"""
Policy Compliance Service

Auto-check invoices against company policies:
- Approval thresholds
- Required approvers by amount/category
- Restricted vendors
- Budget limits
- PO requirements

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class PolicyAction(Enum):
    """Actions that can be enforced by policy."""
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_MULTI_APPROVAL = "require_multi_approval"
    REQUIRE_PO = "require_po"
    BLOCK = "block"
    FLAG_FOR_REVIEW = "flag_for_review"
    AUTO_APPROVE = "auto_approve"
    NOTIFY = "notify"


class PolicySeverity(Enum):
    """Severity of policy violation."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCK = "block"


@dataclass
class PolicyViolation:
    """A policy violation or requirement."""
    policy_id: str
    policy_name: str
    severity: PolicySeverity
    action: PolicyAction
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    required_approvers: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "severity": self.severity.value,
            "action": self.action.value,
            "message": self.message,
            "details": self.details,
            "required_approvers": self.required_approvers,
        }
    
    def to_slack_block(self) -> Dict[str, Any]:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{self.policy_name}*\n{self.message}"
            }
        }


@dataclass
class PolicyCheckResult:
    """Result of checking an invoice against policies."""
    compliant: bool
    violations: List[PolicyViolation]
    required_actions: List[PolicyAction]
    required_approvers: List[str]
    can_proceed: bool
    summary: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "compliant": self.compliant,
            "can_proceed": self.can_proceed,
            "summary": self.summary,
            "violations": [v.to_dict() for v in self.violations],
            "required_actions": [a.value for a in self.required_actions],
            "required_approvers": self.required_approvers,
        }


@dataclass
class Policy:
    """A company policy rule."""
    policy_id: str
    name: str
    description: str
    condition: Dict[str, Any]  # Conditions that trigger this policy
    action: PolicyAction
    severity: PolicySeverity
    required_approvers: List[str] = field(default_factory=list)
    enabled: bool = True

    @staticmethod
    def _to_number(value: Any) -> Optional[float]:
        """Coerce policy values into float safely (supports common currency strings)."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            text = re.sub(r'[^0-9,.\-]', '', text)
            if not text:
                return None
            if ',' in text and '.' in text:
                if text.rfind(',') > text.rfind('.'):
                    text = text.replace('.', '').replace(',', '.')
                else:
                    text = text.replace(',', '')
            elif ',' in text:
                parts = text.split(',')
                if len(parts) == 2 and len(parts[1]) <= 2:
                    text = parts[0] + '.' + parts[1]
                else:
                    text = text.replace(',', '')
            try:
                return float(text)
            except ValueError:
                return None
        return None
    
    def evaluate(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Evaluate if this policy applies to the invoice."""
        if not self.enabled:
            return None
        
        condition_type = self.condition.get("type")
        
        if condition_type == "amount_threshold":
            return self._check_amount_threshold(invoice)
        elif condition_type == "category_approval":
            return self._check_category_approval(invoice)
        elif condition_type == "vendor_restriction":
            return self._check_vendor_restriction(invoice)
        elif condition_type == "po_required":
            return self._check_po_required(invoice)
        elif condition_type == "new_vendor":
            return self._check_new_vendor(invoice)
        
        return None
    
    def _check_amount_threshold(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check amount-based policies."""
        amount = self._to_number(invoice.get("amount"))
        threshold = self._to_number(self.condition.get("threshold"))
        operator = self.condition.get("operator", "gt")

        if amount is None or threshold is None:
            return None
        
        triggered = False
        if operator == "gt" and amount > threshold:
            triggered = True
        elif operator == "gte" and amount >= threshold:
            triggered = True
        elif operator == "lt" and amount < threshold:
            triggered = True
        elif operator == "lte" and amount <= threshold:
            triggered = True
        
        if triggered:
            operator_text = {
                "gt": "greater than",
                "gte": "greater than or equal to",
                "lt": "less than",
                "lte": "less than or equal to",
            }.get(operator, operator)
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=f"Amount ${amount:,.2f} is {operator_text} policy threshold ${threshold:,.2f}",
                details={"amount": amount, "threshold": threshold},
                required_approvers=self.required_approvers,
            )
        
        return None
    
    def _check_category_approval(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check category-based approval requirements."""
        category = invoice.get("category", "").lower()
        vendor_intel = invoice.get("vendor_intelligence", {})
        invoice_category = vendor_intel.get("category", "").lower()
        
        target_categories = [c.lower() for c in self.condition.get("categories", [])]
        
        if category in target_categories or invoice_category in target_categories:
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=f"Category '{category or invoice_category}' requires special approval",
                details={"category": category or invoice_category},
                required_approvers=self.required_approvers,
            )
        
        return None
    
    def _check_vendor_restriction(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check vendor restrictions."""
        vendor = invoice.get("vendor", "").lower()
        restricted = [v.lower() for v in self.condition.get("vendors", [])]
        
        for restricted_vendor in restricted:
            if restricted_vendor in vendor or vendor in restricted_vendor:
                return PolicyViolation(
                    policy_id=self.policy_id,
                    policy_name=self.name,
                    severity=self.severity,
                    action=self.action,
                    message=f"Vendor '{invoice.get('vendor')}' is restricted",
                    details={"vendor": invoice.get("vendor")},
                    required_approvers=self.required_approvers,
                )
        
        return None
    
    def _check_po_required(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check if PO is required."""
        amount = self._to_number(invoice.get("amount"))
        threshold = self._to_number(self.condition.get("threshold")) or 0.0
        po_number = invoice.get("po_number") or invoice.get("purchase_order")

        if amount is None:
            return None
        
        if amount >= threshold and not po_number:
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=f"PO required for invoices over ${threshold:,.2f}",
                details={"amount": amount, "threshold": threshold},
                required_approvers=self.required_approvers,
            )
        
        return None
    
    def _check_new_vendor(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check new vendor policies."""
        vendor_intel = invoice.get("vendor_intelligence", {})
        is_known = vendor_intel.get("known_vendor", True)
        is_first_invoice = invoice.get("is_first_invoice", False)
        
        if not is_known or is_first_invoice:
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=f"New vendor '{invoice.get('vendor')}' requires approval",
                details={"vendor": invoice.get("vendor"), "is_new": True},
                required_approvers=self.required_approvers,
            )
        
        return None


# Default policies - organizations can customize
DEFAULT_POLICIES = [
    Policy(
        policy_id="amt_500",
        name="Manager Approval Required",
        description="Invoices over $500 require manager approval",
        condition={"type": "amount_threshold", "threshold": 500, "operator": "gt"},
        action=PolicyAction.REQUIRE_APPROVAL,
        severity=PolicySeverity.INFO,
        required_approvers=["manager"],
    ),
    Policy(
        policy_id="amt_2500",
        name="Director Approval Required",
        description="Invoices over $2,500 require director approval",
        condition={"type": "amount_threshold", "threshold": 2500, "operator": "gt"},
        action=PolicyAction.REQUIRE_APPROVAL,
        severity=PolicySeverity.WARNING,
        required_approvers=["director"],
    ),
    Policy(
        policy_id="amt_10000",
        name="Executive Approval Required",
        description="Invoices over $10,000 require executive approval",
        condition={"type": "amount_threshold", "threshold": 10000, "operator": "gt"},
        action=PolicyAction.REQUIRE_MULTI_APPROVAL,
        severity=PolicySeverity.WARNING,
        required_approvers=["director", "cfo"],
    ),
    Policy(
        policy_id="consulting_approval",
        name="Consulting Requires CFO",
        description="Consulting and professional services require CFO approval",
        condition={"type": "category_approval", "categories": ["consulting", "professional services", "legal"]},
        action=PolicyAction.REQUIRE_APPROVAL,
        severity=PolicySeverity.INFO,
        required_approvers=["cfo"],
    ),
    Policy(
        policy_id="po_required",
        name="PO Required",
        description="Invoices over $1,000 require a PO number",
        condition={"type": "po_required", "threshold": 1000},
        action=PolicyAction.FLAG_FOR_REVIEW,
        severity=PolicySeverity.WARNING,
    ),
    Policy(
        policy_id="new_vendor",
        name="New Vendor Approval",
        description="First invoice from new vendors requires approval",
        condition={"type": "new_vendor"},
        action=PolicyAction.REQUIRE_APPROVAL,
        severity=PolicySeverity.INFO,
        required_approvers=["manager"],
    ),
]


class PolicyComplianceService:
    """
    Checks invoices against company policies.
    
    Usage:
        service = PolicyComplianceService("org_123")
        
        result = service.check(invoice_data)
        
        if not result.compliant:
            for violation in result.violations:
                print(f"Policy: {violation.policy_name}")
                print(f"Action: {violation.action}")
                print(f"Approvers: {violation.required_approvers}")
        
        if result.can_proceed:
            # Proceed with appropriate routing
            pass
        else:
            # Block the invoice
            pass
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self.policies = self._load_policies()
    
    def _load_policies(self) -> List[Policy]:
        """Load policies for the organization."""
        # Try to load custom policies from database
        try:
            if hasattr(self.db, 'get_policies'):
                custom_policies = self.db.get_policies(self.organization_id)
                if custom_policies:
                    return [self._dict_to_policy(p) for p in custom_policies]
        except:
            pass
        
        # Return default policies
        return DEFAULT_POLICIES.copy()
    
    def _dict_to_policy(self, data: Dict[str, Any]) -> Policy:
        """Convert dictionary to Policy object."""
        return Policy(
            policy_id=data.get("policy_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            condition=data.get("condition", {}),
            action=PolicyAction(data.get("action", "require_approval")),
            severity=PolicySeverity(data.get("severity", "info")),
            required_approvers=data.get("required_approvers", []),
            enabled=data.get("enabled", True),
        )
    
    def check(self, invoice: Dict[str, Any]) -> PolicyCheckResult:
        """
        Check an invoice against all applicable policies.
        """
        violations: List[PolicyViolation] = []
        required_actions: set = set()
        required_approvers: set = set()
        
        for policy in self.policies:
            violation = policy.evaluate(invoice)
            if violation:
                violations.append(violation)
                required_actions.add(violation.action)
                for approver in violation.required_approvers:
                    required_approvers.add(approver)
        
        # Determine if invoice can proceed
        blocking_actions = {PolicyAction.BLOCK}
        can_proceed = not any(v.action in blocking_actions for v in violations)
        
        # Generate summary
        if not violations:
            summary = "Invoice complies with all policies"
            compliant = True
        else:
            compliant = False
            if len(violations) == 1:
                summary = violations[0].message
            else:
                summary = f"{len(violations)} policy requirements apply"
        
        logger.info(
            f"Policy check: {len(violations)} violations, "
            f"can_proceed={can_proceed}, approvers={list(required_approvers)}"
        )
        
        return PolicyCheckResult(
            compliant=compliant,
            violations=violations,
            required_actions=list(required_actions),
            required_approvers=list(required_approvers),
            can_proceed=can_proceed,
            summary=summary,
        )
    
    def get_routing(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determine approval routing based on policies.
        """
        result = self.check(invoice)
        
        routing = {
            "requires_approval": False,
            "approvers": [],
            "approval_type": "single",
            "flags": [],
        }
        
        if PolicyAction.REQUIRE_MULTI_APPROVAL in result.required_actions:
            routing["requires_approval"] = True
            routing["approval_type"] = "sequential"  # or "parallel"
            routing["approvers"] = result.required_approvers
        elif PolicyAction.REQUIRE_APPROVAL in result.required_actions:
            routing["requires_approval"] = True
            routing["approval_type"] = "single"
            routing["approvers"] = result.required_approvers[:1] if result.required_approvers else ["manager"]
        
        if PolicyAction.FLAG_FOR_REVIEW in result.required_actions:
            routing["flags"].append("needs_review")
        
        if PolicyAction.BLOCK in result.required_actions:
            routing["blocked"] = True
            routing["block_reasons"] = [v.message for v in result.violations if v.action == PolicyAction.BLOCK]
        
        return routing
    
    def format_for_slack(self, result: PolicyCheckResult) -> List[Dict[str, Any]]:
        """Format policy check result for Slack."""
        blocks = []
        
        if result.compliant:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Policy Compliant*"
                }
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Policy Requirements ({len(result.violations)})*"
                }
            })
            
            for violation in result.violations:
                blocks.append(violation.to_slack_block())
            
            if result.required_approvers:
                approver_text = ", ".join([f"@{a}" for a in result.required_approvers])
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Required approvers:* {approver_text}"
                    }
                })
        
        return blocks
    
    def add_policy(self, policy: Policy) -> None:
        """Add a new policy."""
        self.policies.append(policy)
        # Persist to database if available
        try:
            if hasattr(self.db, 'save_policy'):
                self.db.save_policy(self.organization_id, policy.__dict__)
        except:
            pass
    
    def update_policy(self, policy_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing policy."""
        for i, policy in enumerate(self.policies):
            if policy.policy_id == policy_id:
                for key, value in updates.items():
                    if hasattr(policy, key):
                        setattr(policy, key, value)
                return True
        return False


# Convenience function
def get_policy_compliance(organization_id: str = "default") -> PolicyComplianceService:
    """Get a policy compliance service instance."""
    return PolicyComplianceService(organization_id=organization_id)
