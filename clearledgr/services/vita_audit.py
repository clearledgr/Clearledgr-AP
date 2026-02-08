"""
Vita AI Audit Service - Tracks all commands and actions executed via Vita.

Captures complete audit trail for compliance:
- Who issued the command (user identity)
- What was requested (original command)
- What was executed (interpreted action)
- When it happened (timestamp)
- What was the result (success/failure, affected records)
- From which surface (Gmail, Sheets, Slack)
"""

import uuid
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum


class ActionRiskLevel(Enum):
    """Risk classification for actions."""
    LOW = "low"           # Read-only, status queries
    MEDIUM = "medium"     # Reconciliation, categorization
    HIGH = "high"         # Posting entries, approvals
    CRITICAL = "critical" # Bulk operations, deletions


class ActionStatus(Enum):
    """Status of an executed action."""
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class VitaAuditEntry:
    """A single audit log entry for a Vita-triggered action."""
    
    # Identity
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Who
    user_id: str = ""
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    organization_id: Optional[str] = None
    
    # What was requested
    original_command: str = ""
    interpreted_intent: str = ""
    extracted_entities: Dict[str, Any] = field(default_factory=dict)
    
    # What action was taken
    action_type: str = ""  # reconcile, categorize, approve, post, etc.
    action_parameters: Dict[str, Any] = field(default_factory=dict)
    risk_level: ActionRiskLevel = ActionRiskLevel.LOW
    
    # When
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    execution_timestamp: Optional[str] = None
    
    # Where
    surface: str = ""  # gmail, sheets, slack
    surface_context: Dict[str, Any] = field(default_factory=dict)  # sheet_id, channel, email_id
    
    # Result
    status: ActionStatus = ActionStatus.PENDING_CONFIRMATION
    result: Optional[Dict[str, Any]] = None
    affected_records: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    
    # Confirmation
    confirmation_required: bool = False
    confirmation_message: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        data['risk_level'] = self.risk_level.value
        data['status'] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VitaAuditEntry':
        """Create from dictionary."""
        data['risk_level'] = ActionRiskLevel(data.get('risk_level', 'low'))
        data['status'] = ActionStatus(data.get('status', 'pending_confirmation'))
        return cls(**data)


class VitaAuditService:
    """
    Service for recording and querying Vita AI audit logs.
    
    In production, this would write to a database. For now, uses in-memory
    storage with file persistence option.
    """
    
    def __init__(self):
        self._entries: Dict[str, VitaAuditEntry] = {}
        self._pending_confirmations: Dict[str, VitaAuditEntry] = {}
    
    def create_entry(
        self,
        user_id: str,
        original_command: str,
        interpreted_intent: str,
        action_type: str,
        surface: str,
        risk_level: ActionRiskLevel = ActionRiskLevel.LOW,
        user_email: Optional[str] = None,
        user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
        extracted_entities: Optional[Dict[str, Any]] = None,
        action_parameters: Optional[Dict[str, Any]] = None,
        surface_context: Optional[Dict[str, Any]] = None,
    ) -> VitaAuditEntry:
        """
        Create a new audit entry when a command is received.
        
        Args:
            user_id: Unique identifier of the user who issued the command
            original_command: The exact text the user typed/spoke
            interpreted_intent: What Vita understood the command to mean
            action_type: The type of action to be performed
            surface: Where the command came from (gmail, sheets, slack)
            risk_level: Risk classification of the action
            user_email: User's email address
            user_name: User's display name
            organization_id: User's organization
            extracted_entities: Entities extracted from command (vendors, dates, etc.)
            action_parameters: Parameters for the action
            surface_context: Context from the surface (sheet_id, channel, etc.)
        
        Returns:
            The created audit entry
        """
        entry = VitaAuditEntry(
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            organization_id=organization_id,
            original_command=original_command,
            interpreted_intent=interpreted_intent,
            action_type=action_type,
            action_parameters=action_parameters or {},
            risk_level=risk_level,
            surface=surface,
            surface_context=surface_context or {},
            extracted_entities=extracted_entities or {},
            confirmation_required=risk_level in [ActionRiskLevel.MEDIUM, ActionRiskLevel.HIGH, ActionRiskLevel.CRITICAL],
        )
        
        # Set confirmation message based on risk level
        if entry.confirmation_required:
            entry.status = ActionStatus.PENDING_CONFIRMATION
            entry.confirmation_message = self._generate_confirmation_message(entry)
            self._pending_confirmations[entry.audit_id] = entry
        
        self._entries[entry.audit_id] = entry
        return entry
    
    def _generate_confirmation_message(self, entry: VitaAuditEntry) -> str:
        """Generate appropriate confirmation message based on action and risk."""
        if entry.risk_level == ActionRiskLevel.CRITICAL:
            return f"This is a critical action: {entry.interpreted_intent}. Type 'CONFIRM' to proceed."
        elif entry.risk_level == ActionRiskLevel.HIGH:
            return f"This will {entry.interpreted_intent}. Are you sure? [Confirm] [Cancel]"
        else:  # MEDIUM
            return f"Ready to {entry.interpreted_intent}. Proceed? [Yes] [No]"
    
    def confirm_action(
        self,
        audit_id: str,
        confirmed_by: str,
        confirmed_by_email: Optional[str] = None,
    ) -> Optional[VitaAuditEntry]:
        """
        Confirm a pending action.
        
        Args:
            audit_id: The audit entry to confirm
            confirmed_by: User ID of the person confirming
            confirmed_by_email: Email of the person confirming
        
        Returns:
            The updated audit entry, or None if not found
        """
        entry = self._entries.get(audit_id)
        if not entry:
            return None
        
        if entry.status != ActionStatus.PENDING_CONFIRMATION:
            return entry
        
        entry.status = ActionStatus.CONFIRMED
        entry.confirmed_by = confirmed_by
        entry.confirmed_at = datetime.utcnow().isoformat()
        
        # Remove from pending
        self._pending_confirmations.pop(audit_id, None)
        
        return entry
    
    def cancel_action(self, audit_id: str, cancelled_by: str) -> Optional[VitaAuditEntry]:
        """Cancel a pending action."""
        entry = self._entries.get(audit_id)
        if not entry:
            return None
        
        entry.status = ActionStatus.CANCELLED
        entry.result = {"cancelled_by": cancelled_by, "cancelled_at": datetime.utcnow().isoformat()}
        self._pending_confirmations.pop(audit_id, None)
        
        return entry
    
    def record_execution(
        self,
        audit_id: str,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        affected_records: Optional[List[str]] = None,
        error_message: Optional[str] = None,
    ) -> Optional[VitaAuditEntry]:
        """
        Record the result of an executed action.
        
        Args:
            audit_id: The audit entry to update
            success: Whether the action succeeded
            result: Result data from the action
            affected_records: List of record IDs that were affected
            error_message: Error message if failed
        
        Returns:
            The updated audit entry
        """
        entry = self._entries.get(audit_id)
        if not entry:
            return None
        
        entry.execution_timestamp = datetime.utcnow().isoformat()
        entry.status = ActionStatus.EXECUTED if success else ActionStatus.FAILED
        entry.result = result
        entry.affected_records = affected_records or []
        entry.error_message = error_message
        
        return entry
    
    def get_entry(self, audit_id: str) -> Optional[VitaAuditEntry]:
        """Get a specific audit entry."""
        return self._entries.get(audit_id)
    
    def get_pending_confirmations(self, user_id: Optional[str] = None) -> List[VitaAuditEntry]:
        """Get all pending confirmations, optionally filtered by user."""
        entries = list(self._pending_confirmations.values())
        if user_id:
            entries = [e for e in entries if e.user_id == user_id]
        return entries
    
    def get_user_history(
        self,
        user_id: str,
        limit: int = 50,
        action_types: Optional[List[str]] = None,
    ) -> List[VitaAuditEntry]:
        """Get audit history for a specific user."""
        entries = [e for e in self._entries.values() if e.user_id == user_id]
        
        if action_types:
            entries = [e for e in entries if e.action_type in action_types]
        
        # Sort by timestamp descending
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        return entries[:limit]
    
    def get_organization_history(
        self,
        organization_id: str,
        limit: int = 100,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[VitaAuditEntry]:
        """Get audit history for an organization (for compliance reporting)."""
        entries = [e for e in self._entries.values() if e.organization_id == organization_id]
        
        if start_date:
            entries = [e for e in entries if e.timestamp >= start_date]
        if end_date:
            entries = [e for e in entries if e.timestamp <= end_date]
        
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        return entries[:limit]
    
    def generate_compliance_report(
        self,
        organization_id: str,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """
        Generate a compliance report for audit purposes.
        
        Returns summary statistics and detailed entries for the period.
        """
        entries = self.get_organization_history(
            organization_id=organization_id,
            limit=10000,
            start_date=start_date,
            end_date=end_date,
        )
        
        # Calculate statistics
        total_commands = len(entries)
        by_action_type: Dict[str, int] = {}
        by_user: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        by_risk_level: Dict[str, int] = {}
        
        for entry in entries:
            by_action_type[entry.action_type] = by_action_type.get(entry.action_type, 0) + 1
            by_user[entry.user_id] = by_user.get(entry.user_id, 0) + 1
            by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1
            by_risk_level[entry.risk_level.value] = by_risk_level.get(entry.risk_level.value, 0) + 1
        
        return {
            "report_generated": datetime.utcnow().isoformat(),
            "organization_id": organization_id,
            "period": {"start": start_date, "end": end_date},
            "summary": {
                "total_commands": total_commands,
                "by_action_type": by_action_type,
                "by_user": by_user,
                "by_status": by_status,
                "by_risk_level": by_risk_level,
            },
            "entries": [e.to_dict() for e in entries],
        }


# Global instance
_vita_audit_service: Optional[VitaAuditService] = None


def get_vita_audit_service() -> VitaAuditService:
    """Get or create the global Vita audit service instance."""
    global _vita_audit_service
    if _vita_audit_service is None:
        _vita_audit_service = VitaAuditService()
    return _vita_audit_service
