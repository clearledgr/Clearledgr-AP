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
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

AP_POLICY_NAME = "ap_business_v1"


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "condition": self.condition,
            "action": self.action.value,
            "severity": self.severity.value,
            "required_approvers": self.required_approvers,
            "enabled": self.enabled,
        }

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
        elif condition_type == "vendor_threshold":
            return self._check_vendor_threshold(invoice)
        elif condition_type == "category_approval":
            return self._check_category_approval(invoice)
        elif condition_type == "vendor_restriction":
            return self._check_vendor_restriction(invoice)
        elif condition_type == "po_required":
            return self._check_po_required(invoice)
        elif condition_type == "new_vendor":
            return self._check_new_vendor(invoice)
        elif condition_type == "budget_status":
            return self._check_budget_status(invoice)

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
        vendor = str(invoice.get("vendor") or invoice.get("vendor_name") or "").lower()
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
            vendor_name = invoice.get("vendor") or invoice.get("vendor_name")
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=f"New vendor '{vendor_name}' requires approval",
                details={"vendor": vendor_name, "is_new": True},
                required_approvers=self.required_approvers,
            )

        return None

    def _check_vendor_threshold(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check amount threshold for specific vendors."""
        vendor_name = str(invoice.get("vendor") or invoice.get("vendor_name") or "").strip()
        if not vendor_name:
            return None

        vendor_l = vendor_name.lower()
        contains = str(self.condition.get("vendor_contains") or "").strip().lower()
        regex = str(self.condition.get("vendor_regex") or "").strip()
        if contains and contains not in vendor_l:
            return None
        if regex:
            try:
                if not re.search(regex, vendor_name, flags=re.IGNORECASE):
                    return None
            except re.error:
                return None

        amount = self._to_number(invoice.get("amount"))
        threshold = self._to_number(self.condition.get("threshold"))
        operator = self.condition.get("operator", "gte")
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

        if not triggered:
            return None

        return PolicyViolation(
            policy_id=self.policy_id,
            policy_name=self.name,
            severity=self.severity,
            action=self.action,
            message=f"Vendor '{vendor_name}' amount ${amount:,.2f} triggered threshold ${threshold:,.2f}",
            details={
                "vendor": vendor_name,
                "amount": amount,
                "threshold": threshold,
                "operator": operator,
            },
            required_approvers=self.required_approvers,
        )

    def _check_budget_status(self, invoice: Dict[str, Any]) -> Optional[PolicyViolation]:
        """Check policy against computed budget status."""
        budget_entries = invoice.get("budget_impact")
        if not isinstance(budget_entries, list):
            return None

        statuses = {
            str(status).strip().lower()
            for status in self.condition.get("statuses", ["critical", "exceeded"])
            if str(status).strip()
        }
        if not statuses:
            statuses = {"critical", "exceeded"}

        target_budget_name = str(self.condition.get("budget_name") or "").strip().lower()
        for entry in budget_entries:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("after_approval_status") or "").strip().lower()
            if status not in statuses:
                continue
            if target_budget_name:
                budget_name = str(entry.get("budget_name") or "").strip().lower()
                if budget_name != target_budget_name:
                    continue
            return PolicyViolation(
                policy_id=self.policy_id,
                policy_name=self.name,
                severity=self.severity,
                action=self.action,
                message=str(
                    entry.get("warning_message")
                    or f"Budget '{entry.get('budget_name', 'unnamed')}' would be {status}"
                ),
                details=entry,
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
    
    def __init__(self, organization_id: str = "default", policy_name: str = AP_POLICY_NAME):
        self.organization_id = organization_id
        self.policy_name = policy_name
        self.db = get_db()
        self.policies = self._load_policies()

    def _load_policies(self) -> List[Policy]:
        """Load policies for the organization."""
        default_policies = [self._dict_to_policy(item.to_dict()) for item in DEFAULT_POLICIES]

        # Try to load organization AP policy document first.
        policy_doc: Dict[str, Any] = {}
        try:
            if hasattr(self.db, "get_ap_policy"):
                current = self.db.get_ap_policy(self.organization_id, policy_name=self.policy_name) or {}
                policy_doc = current.get("config_json") if isinstance(current.get("config_json"), dict) else {}
                if current and not current.get("enabled", True):
                    return []
        except Exception as exc:
            logger.warning("Failed to load AP policy document for %s: %s", self.organization_id, exc)

        custom_policies, errors = self._policies_from_config(policy_doc)
        if errors:
            logger.warning("AP policy parse warnings for %s: %s", self.organization_id, "; ".join(errors))

        if not custom_policies:
            return default_policies

        inherit_defaults = bool(policy_doc.get("inherit_defaults", True))
        if not inherit_defaults:
            return custom_policies

        merged: Dict[str, Policy] = {policy.policy_id: policy for policy in default_policies}
        for policy in custom_policies:
            merged[policy.policy_id] = policy
        return list(merged.values())
    
    def _dict_to_policy(self, data: Dict[str, Any]) -> Policy:
        """Convert dictionary to Policy object."""
        action_value = str(data.get("action", "require_approval")).strip()
        severity_value = str(data.get("severity", "info")).strip()
        try:
            action = PolicyAction(action_value)
        except ValueError:
            action = PolicyAction.REQUIRE_APPROVAL
        try:
            severity = PolicySeverity(severity_value)
        except ValueError:
            severity = PolicySeverity.INFO
        return Policy(
            policy_id=data.get("policy_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            condition=data.get("condition", {}),
            action=action,
            severity=severity,
            required_approvers=data.get("required_approvers", []),
            enabled=data.get("enabled", True),
        )

    def _policies_from_config(self, config: Dict[str, Any]) -> Tuple[List[Policy], List[str]]:
        """Build policy objects from a declarative AP policy document."""
        if not isinstance(config, dict):
            return [], ["config must be an object"]

        policies: List[Policy] = []
        errors: List[str] = []

        # 1) Explicit policy rules.
        explicit_rules = config.get("policies")
        if not isinstance(explicit_rules, list):
            explicit_rules = config.get("rules")
        if isinstance(explicit_rules, list):
            for idx, rule in enumerate(explicit_rules):
                if not isinstance(rule, dict):
                    errors.append(f"rules[{idx}] is not an object")
                    continue
                policy = self._dict_to_policy(rule)
                if not policy.policy_id:
                    policy.policy_id = f"rule_{idx + 1}"
                if not policy.name:
                    policy.name = policy.policy_id
                if not isinstance(policy.condition, dict):
                    errors.append(f"{policy.policy_id}: condition must be an object")
                    continue
                policies.append(policy)

        # 2) Threshold shortcuts.
        for idx, threshold in enumerate(config.get("approval_thresholds", []) or []):
            if not isinstance(threshold, dict):
                errors.append(f"approval_thresholds[{idx}] is not an object")
                continue
            amount = Policy._to_number(threshold.get("threshold") or threshold.get("min_amount"))
            if amount is None:
                errors.append(f"approval_thresholds[{idx}] missing numeric threshold")
                continue
            approvers = threshold.get("approvers") or threshold.get("required_approvers") or []
            if not isinstance(approvers, list):
                approvers = [str(approvers)] if approvers else []
            policy = Policy(
                policy_id=str(threshold.get("policy_id") or f"approval_threshold_{idx + 1}"),
                name=str(threshold.get("name") or f"Approval threshold {idx + 1}"),
                description=str(threshold.get("description") or "Amount-based approval threshold"),
                condition={
                    "type": "amount_threshold",
                    "threshold": amount,
                    "operator": str(threshold.get("operator") or "gte"),
                },
                action=(
                    PolicyAction.REQUIRE_MULTI_APPROVAL
                    if len(approvers) > 1
                    else PolicyAction.REQUIRE_APPROVAL
                ),
                severity=(
                    PolicySeverity(str(threshold.get("severity") or "warning"))
                    if str(threshold.get("severity") or "warning") in {item.value for item in PolicySeverity}
                    else PolicySeverity.WARNING
                ),
                required_approvers=[str(item) for item in approvers if str(item).strip()],
                enabled=bool(threshold.get("enabled", True)),
            )
            policies.append(policy)

        # 3) Vendor-specific shortcuts.
        for idx, rule in enumerate(config.get("vendor_rules", []) or []):
            if not isinstance(rule, dict):
                errors.append(f"vendor_rules[{idx}] is not an object")
                continue
            vendor_contains = str(rule.get("vendor_contains") or rule.get("vendor") or "").strip()
            vendor_regex = str(rule.get("vendor_regex") or "").strip()
            if not vendor_contains and not vendor_regex:
                errors.append(f"vendor_rules[{idx}] missing vendor matcher")
                continue
            threshold = Policy._to_number(rule.get("threshold") or rule.get("min_amount"))
            blocked = bool(rule.get("blocked", False))
            approvers = rule.get("approvers") or rule.get("required_approvers") or []
            if not isinstance(approvers, list):
                approvers = [str(approvers)] if approvers else []
            if blocked:
                condition = {"type": "vendor_restriction", "vendors": [vendor_contains or vendor_regex]}
                action = PolicyAction.BLOCK
            else:
                if threshold is None:
                    errors.append(f"vendor_rules[{idx}] missing numeric threshold for non-block rule")
                    continue
                condition = {
                    "type": "vendor_threshold",
                    "vendor_contains": vendor_contains,
                    "vendor_regex": vendor_regex,
                    "threshold": threshold,
                    "operator": str(rule.get("operator") or "gte"),
                }
                action = (
                    PolicyAction.REQUIRE_MULTI_APPROVAL
                    if len(approvers) > 1
                    else PolicyAction.REQUIRE_APPROVAL
                )
            try:
                severity = PolicySeverity(str(rule.get("severity") or ("block" if blocked else "warning")))
            except ValueError:
                severity = PolicySeverity.WARNING
            policies.append(
                Policy(
                    policy_id=str(rule.get("policy_id") or f"vendor_rule_{idx + 1}"),
                    name=str(rule.get("name") or f"Vendor rule {idx + 1}"),
                    description=str(rule.get("description") or "Vendor-specific policy"),
                    condition=condition,
                    action=action,
                    severity=severity,
                    required_approvers=[str(item) for item in approvers if str(item).strip()],
                    enabled=bool(rule.get("enabled", True)),
                )
            )

        # 4) Budget rule shortcuts.
        for idx, rule in enumerate(config.get("budget_rules", []) or []):
            if not isinstance(rule, dict):
                errors.append(f"budget_rules[{idx}] is not an object")
                continue
            statuses = rule.get("statuses")
            if not isinstance(statuses, list):
                status = rule.get("status")
                statuses = [status] if status else ["critical", "exceeded"]
            try:
                action = PolicyAction(str(rule.get("action") or "flag_for_review"))
            except ValueError:
                action = PolicyAction.FLAG_FOR_REVIEW
            try:
                severity = PolicySeverity(str(rule.get("severity") or "warning"))
            except ValueError:
                severity = PolicySeverity.WARNING
            approvers = rule.get("approvers") or rule.get("required_approvers") or []
            if not isinstance(approvers, list):
                approvers = [str(approvers)] if approvers else []
            policies.append(
                Policy(
                    policy_id=str(rule.get("policy_id") or f"budget_rule_{idx + 1}"),
                    name=str(rule.get("name") or f"Budget rule {idx + 1}"),
                    description=str(rule.get("description") or "Budget impact policy"),
                    condition={
                        "type": "budget_status",
                        "statuses": [str(status).lower() for status in statuses if str(status).strip()],
                        "budget_name": str(rule.get("budget_name") or "").strip(),
                    },
                    action=action,
                    severity=severity,
                    required_approvers=[str(item) for item in approvers if str(item).strip()],
                    enabled=bool(rule.get("enabled", True)),
                )
            )

        # 5) Escalation path shortcuts.
        for idx, path in enumerate(config.get("escalation_paths", []) or []):
            if not isinstance(path, dict):
                errors.append(f"escalation_paths[{idx}] is not an object")
                continue
            min_amount = Policy._to_number(path.get("min_amount") or path.get("threshold"))
            if min_amount is None:
                errors.append(f"escalation_paths[{idx}] missing numeric min_amount")
                continue
            approvers = path.get("approvers") or path.get("required_approvers") or []
            if not isinstance(approvers, list):
                approvers = [str(approvers)] if approvers else []
            action = (
                PolicyAction.REQUIRE_MULTI_APPROVAL
                if len(approvers) > 1
                else PolicyAction.REQUIRE_APPROVAL
            )
            policies.append(
                Policy(
                    policy_id=str(path.get("policy_id") or f"escalation_{idx + 1}"),
                    name=str(path.get("name") or f"Escalation {idx + 1}"),
                    description=str(path.get("description") or "Escalation path"),
                    condition={
                        "type": "amount_threshold",
                        "threshold": min_amount,
                        "operator": str(path.get("operator") or "gte"),
                    },
                    action=action,
                    severity=PolicySeverity.WARNING,
                    required_approvers=[str(item) for item in approvers if str(item).strip()],
                    enabled=bool(path.get("enabled", True)),
                )
            )

        return policies, errors

    def describe_effective_policies(self) -> List[Dict[str, Any]]:
        """Serialize current effective policy set."""
        return [policy.to_dict() for policy in self.policies]

    def validate_policy_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate policy document and return parse errors."""
        _, errors = self._policies_from_config(config or {})
        return errors

    def get_policy_document(self) -> Dict[str, Any]:
        """
        Return persisted AP policy document with effective fallback defaults.
        """
        current = self.db.get_ap_policy(self.organization_id, policy_name=self.policy_name) if hasattr(self.db, "get_ap_policy") else None
        if current:
            return {
                "policy_name": self.policy_name,
                "version": int(current.get("version") or 0),
                "enabled": bool(current.get("enabled", True)),
                "config": current.get("config_json") if isinstance(current.get("config_json"), dict) else {},
                "updated_by": current.get("updated_by"),
                "created_at": current.get("created_at"),
            }
        return {
            "policy_name": self.policy_name,
            "version": 0,
            "enabled": True,
            "config": {
                "inherit_defaults": True,
                "policies": [policy.to_dict() for policy in DEFAULT_POLICIES],
            },
            "updated_by": "system",
            "created_at": None,
        }
    
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
def get_policy_compliance(
    organization_id: str = "default",
    policy_name: str = AP_POLICY_NAME,
) -> PolicyComplianceService:
    """Get a policy compliance service instance."""
    return PolicyComplianceService(organization_id=organization_id, policy_name=policy_name)
