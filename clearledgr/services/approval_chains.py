"""
Multi-Level Approval Chain Service

Handles approval routing based on:
- Amount thresholds
- Vendor type
- GL code / Department
- Custom rules

Supports:
- Sequential approval chains
- Parallel approvals
- Approval delegation
- Escalation
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid

logger = logging.getLogger(__name__)


class ApprovalLevel(Enum):
    """Approval authority levels."""
    LEVEL_1 = "level_1"      # Manager / Team Lead ($0 - $1,000)
    LEVEL_2 = "level_2"      # Director ($1,001 - $10,000)
    LEVEL_3 = "level_3"      # VP / Controller ($10,001 - $50,000)
    LEVEL_4 = "level_4"      # CFO / CEO ($50,001+)
    EMERGENCY = "emergency"  # Emergency approval (any amount, requires justification)


class ApprovalStatus(Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    DELEGATED = "delegated"
    EXPIRED = "expired"


class ApprovalType(Enum):
    """Type of approval routing."""
    SEQUENTIAL = "sequential"  # One after another
    PARALLEL = "parallel"      # All at once, all must approve
    ANY = "any"               # Any one approver is sufficient


@dataclass
class Approver:
    """Represents an approver in the chain."""
    user_id: str
    email: str
    name: str
    level: ApprovalLevel
    department: Optional[str] = None
    max_amount: float = 0.0  # Maximum amount this approver can authorize
    is_active: bool = True
    delegates: List[str] = field(default_factory=list)  # User IDs this person delegates to


@dataclass
class ApprovalStep:
    """A single step in the approval chain."""
    step_id: str
    level: ApprovalLevel
    approvers: List[str]  # User IDs
    approval_type: ApprovalType = ApprovalType.ANY
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    comments: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "level": self.level.value,
            "approvers": self.approvers,
            "approval_type": self.approval_type.value,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "comments": self.comments,
        }


@dataclass
class ApprovalChain:
    """Complete approval chain for an invoice."""
    chain_id: str
    invoice_id: str
    vendor_name: str
    amount: float
    gl_code: Optional[str] = None
    department: Optional[str] = None
    
    steps: List[ApprovalStep] = field(default_factory=list)
    current_step: int = 0
    
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    # Metadata
    requester_id: Optional[str] = None
    requester_name: Optional[str] = None
    organization_id: str = "default"
    
    def get_current_step(self) -> Optional[ApprovalStep]:
        """Get the current pending step."""
        if 0 <= self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None
    
    def get_pending_approvers(self) -> List[str]:
        """Get user IDs of pending approvers."""
        step = self.get_current_step()
        if step and step.status == ApprovalStatus.PENDING:
            return step.approvers
        return []
    
    def is_fully_approved(self) -> bool:
        """Check if all steps are approved."""
        return all(s.status == ApprovalStatus.APPROVED for s in self.steps)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "invoice_id": self.invoice_id,
            "vendor_name": self.vendor_name,
            "amount": self.amount,
            "gl_code": self.gl_code,
            "department": self.department,
            "steps": [s.to_dict() for s in self.steps],
            "current_step": self.current_step,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "requester_id": self.requester_id,
            "requester_name": self.requester_name,
            "organization_id": self.organization_id,
        }


@dataclass
class ApprovalRule:
    """Rule for determining approval requirements."""
    rule_id: str
    name: str
    priority: int = 0  # Higher priority rules checked first
    
    # Conditions (all must match if specified)
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    vendor_patterns: List[str] = field(default_factory=list)
    gl_codes: List[str] = field(default_factory=list)
    departments: List[str] = field(default_factory=list)
    
    # Required approvers
    required_levels: List[ApprovalLevel] = field(default_factory=list)
    specific_approvers: List[str] = field(default_factory=list)
    approval_type: ApprovalType = ApprovalType.SEQUENTIAL
    
    is_active: bool = True
    
    def matches(
        self,
        amount: float,
        vendor_name: str = "",
        gl_code: str = "",
        department: str = "",
    ) -> bool:
        """Check if this rule applies to the given invoice."""
        if not self.is_active:
            return False
        
        # Check amount range
        if self.min_amount is not None and amount < self.min_amount:
            return False
        if self.max_amount is not None and amount > self.max_amount:
            return False
        
        # Check vendor patterns
        if self.vendor_patterns:
            vendor_lower = vendor_name.lower()
            if not any(p.lower() in vendor_lower for p in self.vendor_patterns):
                return False
        
        # Check GL codes
        if self.gl_codes and gl_code:
            if gl_code not in self.gl_codes:
                return False
        
        # Check departments
        if self.departments and department:
            if department not in self.departments:
                return False
        
        return True


class ApprovalChainService:
    """
    Service for managing multi-level approval chains.
    """
    
    # Default amount thresholds
    DEFAULT_THRESHOLDS = {
        ApprovalLevel.LEVEL_1: 1000,
        ApprovalLevel.LEVEL_2: 10000,
        ApprovalLevel.LEVEL_3: 50000,
        ApprovalLevel.LEVEL_4: float('inf'),
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._chains: Dict[str, ApprovalChain] = {}
        self._approvers: Dict[str, Approver] = {}
        self._rules: List[ApprovalRule] = []
        self._delegations: Dict[str, str] = {}  # from_user -> to_user
        self._thresholds = dict(self.DEFAULT_THRESHOLDS)
        
        # Initialize default rules
        self._init_default_rules()
    
    def _init_default_rules(self):
        """Set up default approval rules based on amount thresholds."""
        # Small amounts - manager approval
        self._rules.append(ApprovalRule(
            rule_id="default-small",
            name="Small Invoices",
            priority=10,
            max_amount=1000,
            required_levels=[ApprovalLevel.LEVEL_1],
        ))
        
        # Medium amounts - director approval
        self._rules.append(ApprovalRule(
            rule_id="default-medium",
            name="Medium Invoices",
            priority=20,
            min_amount=1000.01,
            max_amount=10000,
            required_levels=[ApprovalLevel.LEVEL_1, ApprovalLevel.LEVEL_2],
        ))
        
        # Large amounts - VP/Controller approval
        self._rules.append(ApprovalRule(
            rule_id="default-large",
            name="Large Invoices",
            priority=30,
            min_amount=10000.01,
            max_amount=50000,
            required_levels=[ApprovalLevel.LEVEL_1, ApprovalLevel.LEVEL_2, ApprovalLevel.LEVEL_3],
        ))
        
        # Very large - CFO approval
        self._rules.append(ApprovalRule(
            rule_id="default-xlarge",
            name="Very Large Invoices",
            priority=40,
            min_amount=50000.01,
            required_levels=[ApprovalLevel.LEVEL_1, ApprovalLevel.LEVEL_2, ApprovalLevel.LEVEL_3, ApprovalLevel.LEVEL_4],
        ))
    
    def register_approver(self, approver: Approver):
        """Register an approver in the system."""
        self._approvers[approver.user_id] = approver
        logger.info(f"Registered approver: {approver.name} ({approver.level.value})")
    
    def add_rule(self, rule: ApprovalRule):
        """Add a custom approval rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: -r.priority)  # Higher priority first
        logger.info(f"Added approval rule: {rule.name}")
    
    def set_delegation(self, from_user_id: str, to_user_id: str, until: datetime = None):
        """Set up approval delegation (out of office)."""
        self._delegations[from_user_id] = to_user_id
        logger.info(f"Delegation set: {from_user_id} -> {to_user_id}")
    
    def remove_delegation(self, from_user_id: str):
        """Remove delegation."""
        if from_user_id in self._delegations:
            del self._delegations[from_user_id]
    
    def get_delegate(self, user_id: str) -> str:
        """Get the delegate for a user, or the user themselves."""
        return self._delegations.get(user_id, user_id)
    
    def create_approval_chain(
        self,
        invoice_id: str,
        vendor_name: str,
        amount: float,
        gl_code: str = "",
        department: str = "",
        requester_id: str = None,
        requester_name: str = None,
    ) -> ApprovalChain:
        """
        Create an approval chain for an invoice based on rules.
        """
        chain_id = f"chain-{uuid.uuid4().hex[:12]}"
        
        # Find matching rule
        matching_rule = None
        for rule in self._rules:
            if rule.matches(amount, vendor_name, gl_code, department):
                matching_rule = rule
                break
        
        if not matching_rule:
            # Default to single level based on amount
            level = self._get_level_for_amount(amount)
            matching_rule = ApprovalRule(
                rule_id="auto",
                name="Auto-generated",
                required_levels=[level],
            )
        
        # Build approval steps
        steps = []
        for i, level in enumerate(matching_rule.required_levels):
            approvers = self._get_approvers_for_level(level, department)
            
            # Apply delegations
            approvers = [self.get_delegate(a) for a in approvers]
            
            step = ApprovalStep(
                step_id=f"step-{i+1}",
                level=level,
                approvers=approvers,
                approval_type=matching_rule.approval_type if i == len(matching_rule.required_levels) - 1 else ApprovalType.ANY,
            )
            steps.append(step)
        
        chain = ApprovalChain(
            chain_id=chain_id,
            invoice_id=invoice_id,
            vendor_name=vendor_name,
            amount=amount,
            gl_code=gl_code,
            department=department,
            steps=steps,
            requester_id=requester_id,
            requester_name=requester_name,
            organization_id=self.organization_id,
        )
        
        self._chains[chain_id] = chain
        logger.info(f"Created approval chain {chain_id} for invoice {invoice_id}: {len(steps)} steps")
        
        return chain
    
    def _get_level_for_amount(self, amount: float) -> ApprovalLevel:
        """Determine approval level based on amount."""
        for level, threshold in sorted(self._thresholds.items(), key=lambda x: x[1]):
            if amount <= threshold:
                return level
        return ApprovalLevel.LEVEL_4
    
    def _get_approvers_for_level(self, level: ApprovalLevel, department: str = "") -> List[str]:
        """Get all approvers at a given level."""
        approvers = []
        for approver in self._approvers.values():
            if approver.level == level and approver.is_active:
                if not department or not approver.department or approver.department == department:
                    approvers.append(approver.user_id)
        
        # If no specific approvers, return placeholder
        if not approvers:
            approvers = [f"approver-{level.value}"]
        
        return approvers
    
    def approve_step(
        self,
        chain_id: str,
        user_id: str,
        comments: str = "",
    ) -> ApprovalChain:
        """
        Approve the current step in a chain.
        """
        chain = self._chains.get(chain_id)
        if not chain:
            raise ValueError(f"Chain {chain_id} not found")
        
        step = chain.get_current_step()
        if not step:
            raise ValueError("No pending step to approve")
        
        # Check if user can approve (either original approver or delegate)
        can_approve = user_id in step.approvers or any(
            self.get_delegate(a) == user_id for a in step.approvers
        )
        
        if not can_approve:
            raise ValueError(f"User {user_id} is not authorized to approve this step")
        
        # Approve the step
        step.status = ApprovalStatus.APPROVED
        step.approved_by = user_id
        step.approved_at = datetime.now()
        step.comments = comments
        
        logger.info(f"Step {step.step_id} approved by {user_id}")
        
        # Move to next step
        chain.current_step += 1
        
        # Check if chain is complete
        if chain.current_step >= len(chain.steps):
            chain.status = ApprovalStatus.APPROVED
            chain.completed_at = datetime.now()
            logger.info(f"Chain {chain_id} fully approved")
        
        return chain
    
    def reject_step(
        self,
        chain_id: str,
        user_id: str,
        reason: str,
    ) -> ApprovalChain:
        """
        Reject the current step in a chain (entire chain is rejected).
        """
        chain = self._chains.get(chain_id)
        if not chain:
            raise ValueError(f"Chain {chain_id} not found")
        
        step = chain.get_current_step()
        if not step:
            raise ValueError("No pending step to reject")
        
        step.status = ApprovalStatus.REJECTED
        step.approved_by = user_id
        step.approved_at = datetime.now()
        step.rejection_reason = reason
        
        chain.status = ApprovalStatus.REJECTED
        chain.completed_at = datetime.now()
        
        logger.info(f"Chain {chain_id} rejected by {user_id}: {reason}")
        
        return chain
    
    def escalate_step(
        self,
        chain_id: str,
        reason: str = "Timeout",
    ) -> ApprovalChain:
        """
        Escalate the current step to the next level.
        """
        chain = self._chains.get(chain_id)
        if not chain:
            raise ValueError(f"Chain {chain_id} not found")
        
        step = chain.get_current_step()
        if not step:
            raise ValueError("No pending step to escalate")
        
        # Mark current step as escalated
        step.status = ApprovalStatus.ESCALATED
        step.comments = f"Escalated: {reason}"
        
        # Move to next step
        chain.current_step += 1
        
        logger.info(f"Step {step.step_id} escalated: {reason}")
        
        return chain
    
    def get_chain(self, chain_id: str) -> Optional[ApprovalChain]:
        """Get a specific approval chain."""
        return self._chains.get(chain_id)
    
    def get_chain_by_invoice(self, invoice_id: str) -> Optional[ApprovalChain]:
        """Get approval chain for an invoice."""
        for chain in self._chains.values():
            if chain.invoice_id == invoice_id:
                return chain
        return None
    
    def get_pending_approvals(self, user_id: str) -> List[ApprovalChain]:
        """Get all chains pending approval by a user."""
        pending = []
        for chain in self._chains.values():
            if chain.status == ApprovalStatus.PENDING:
                if user_id in chain.get_pending_approvers():
                    pending.append(chain)
        return pending
    
    def get_approval_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        chains = list(self._chains.values())
        
        pending = [c for c in chains if c.status == ApprovalStatus.PENDING]
        approved = [c for c in chains if c.status == ApprovalStatus.APPROVED]
        rejected = [c for c in chains if c.status == ApprovalStatus.REJECTED]
        
        return {
            "total_chains": len(chains),
            "pending": {
                "count": len(pending),
                "total_amount": sum(c.amount for c in pending),
            },
            "approved": {
                "count": len(approved),
                "total_amount": sum(c.amount for c in approved),
            },
            "rejected": {
                "count": len(rejected),
                "total_amount": sum(c.amount for c in rejected),
            },
            "active_approvers": len([a for a in self._approvers.values() if a.is_active]),
            "active_delegations": len(self._delegations),
        }


# Singleton instance cache
_instances: Dict[str, ApprovalChainService] = {}


def get_approval_chain_service(organization_id: str = "default") -> ApprovalChainService:
    """Get or create approval chain service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = ApprovalChainService(organization_id)
    return _instances[organization_id]
