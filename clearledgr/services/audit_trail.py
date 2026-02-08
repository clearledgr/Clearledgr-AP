"""
Audit Trail Service

Complete explainable history of every decision:
- When each step happened
- What the agent decided
- Why it made that decision
- Who approved/modified

Architecture: Part of the MEMORY LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """Types of audit events."""
    # Lifecycle events
    RECEIVED = "received"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    
    # Analysis events
    ANALYZED = "analyzed"
    DUPLICATE_CHECK = "duplicate_check"
    ANOMALY_CHECK = "anomaly_check"
    POLICY_CHECK = "policy_check"
    
    # Decision events
    DECISION_MADE = "decision_made"
    AUTO_APPROVED = "auto_approved"
    FLAGGED = "flagged"
    ROUTED = "routed"
    
    # Human events
    APPROVAL_REQUESTED = "approval_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    COMMENT_ADDED = "comment_added"
    
    # Action events
    POSTED = "posted"
    PAYMENT_SCHEDULED = "payment_scheduled"
    PAYMENT_SENT = "payment_sent"
    
    # System events
    ERROR = "error"
    RETRY = "retry"
    NOTIFICATION_SENT = "notification_sent"


@dataclass
class AuditEvent:
    """A single event in the audit trail."""
    event_id: str
    event_type: AuditEventType
    timestamp: str
    actor: str  # "agent", "system", or user email
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    duration_ms: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "summary": self.summary,
            "details": self.details,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
        }
    
    def format_line(self) -> str:
        """Format as a single line for display."""
        time_str = self.timestamp.split("T")[1][:8] if "T" in self.timestamp else self.timestamp
        
        confidence_str = f" ({self.confidence*100:.0f}%)" if self.confidence else ""
        reasoning_str = f" - {self.reasoning}" if self.reasoning else ""
        
        return f"[{time_str}] {self.summary}{confidence_str}{reasoning_str}"


@dataclass
class AuditTrail:
    """Complete audit trail for an invoice."""
    invoice_id: str
    organization_id: str
    vendor: str
    amount: float
    events: List[AuditEvent]
    current_status: str
    created_at: str
    last_updated: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "organization_id": self.organization_id,
            "vendor": self.vendor,
            "amount": self.amount,
            "current_status": self.current_status,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "events": [e.to_dict() for e in self.events],
            "event_count": len(self.events),
        }
    
    def format_timeline(self) -> str:
        """Format as a readable timeline."""
        lines = [
            f"Invoice {self.invoice_id} ({self.vendor} ${self.amount:,.2f})",
            f"Status: {self.current_status}",
            "-" * 50,
        ]
        
        for event in self.events:
            lines.append(event.format_line())
        
        return "\n".join(lines)
    
    def to_slack_blocks(self) -> List[Dict[str, Any]]:
        """Format for Slack display."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Audit Trail: {self.vendor}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Invoice:* {self.invoice_id}\n"
                        f"*Amount:* ${self.amount:,.2f}\n"
                        f"*Status:* {self.current_status}"
                    )
                }
            },
            {"type": "divider"},
        ]
        
        # Add events (most recent first, limited to 10)
        recent_events = list(reversed(self.events))[:10]
        
        timeline_text = "\n".join([
            f"• `{e.timestamp.split('T')[1][:8]}` {e.summary}"
            + (f" _({e.reasoning})_" if e.reasoning else "")
            for e in recent_events
        ])
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recent Events:*\n{timeline_text}"
            }
        })
        
        if len(self.events) > 10:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_Showing 10 of {len(self.events)} events_"
                    }
                ]
            })
        
        return blocks


class AuditTrailService:
    """
    Maintains complete audit trail for invoices.
    
    Usage:
        service = AuditTrailService("org_123")
        
        # Log an event
        service.log(
            invoice_id="inv_123",
            event_type=AuditEventType.CLASSIFIED,
            summary="Classified as Invoice",
            reasoning="High confidence from AI extraction",
            confidence=0.94
        )
        
        # Get full trail
        trail = service.get_trail("inv_123")
        print(trail.format_timeline())
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        
        # In-memory storage (would be database in production)
        self._trails: Dict[str, AuditTrail] = {}
    
    def log(
        self,
        invoice_id: str,
        event_type: AuditEventType,
        summary: str,
        actor: str = "agent",
        details: Optional[Dict[str, Any]] = None,
        reasoning: Optional[str] = None,
        confidence: Optional[float] = None,
        duration_ms: Optional[int] = None,
        vendor: Optional[str] = None,
        amount: Optional[float] = None,
    ) -> AuditEvent:
        """
        Log an event to the audit trail.
        """
        event = AuditEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=event_type,
            timestamp=datetime.now().isoformat(),
            actor=actor,
            summary=summary,
            details=details or {},
            reasoning=reasoning,
            confidence=confidence,
            duration_ms=duration_ms,
        )
        
        # Get or create trail
        if invoice_id not in self._trails:
            self._trails[invoice_id] = AuditTrail(
                invoice_id=invoice_id,
                organization_id=self.organization_id,
                vendor=vendor or "Unknown",
                amount=amount or 0,
                events=[],
                current_status="new",
                created_at=datetime.now().isoformat(),
                last_updated=datetime.now().isoformat(),
            )
        
        trail = self._trails[invoice_id]
        trail.events.append(event)
        trail.last_updated = datetime.now().isoformat()
        
        # Update status based on event type
        status_map = {
            AuditEventType.RECEIVED: "received",
            AuditEventType.CLASSIFIED: "classified",
            AuditEventType.EXTRACTED: "extracted",
            AuditEventType.DECISION_MADE: "pending_approval",
            AuditEventType.AUTO_APPROVED: "approved",
            AuditEventType.APPROVED: "approved",
            AuditEventType.REJECTED: "rejected",
            AuditEventType.POSTED: "posted",
            AuditEventType.PAYMENT_SENT: "paid",
        }
        
        if event_type in status_map:
            trail.current_status = status_map[event_type]
        
        # Update vendor/amount if provided
        if vendor:
            trail.vendor = vendor
        if amount:
            trail.amount = amount
        
        # Persist to database
        self._persist_event(invoice_id, event)
        
        logger.info(f"Audit: [{invoice_id}] {event_type.value}: {summary}")
        
        return event
    
    def _persist_event(self, invoice_id: str, event: AuditEvent) -> None:
        """Persist event to database."""
        try:
            if hasattr(self.db, 'save_audit_event'):
                self.db.save_audit_event(
                    invoice_id=invoice_id,
                    organization_id=self.organization_id,
                    event=event.to_dict(),
                )
        except Exception as e:
            logger.warning(f"Failed to persist audit event: {e}")
    
    def get_trail(self, invoice_id: str) -> Optional[AuditTrail]:
        """Get the complete audit trail for an invoice."""
        # Check in-memory first
        if invoice_id in self._trails:
            return self._trails[invoice_id]
        
        # Try to load from database
        try:
            if hasattr(self.db, 'get_audit_trail'):
                data = self.db.get_audit_trail(
                    invoice_id=invoice_id,
                    organization_id=self.organization_id,
                )
                if data:
                    trail = self._dict_to_trail(data)
                    self._trails[invoice_id] = trail
                    return trail
        except Exception as e:
            logger.warning(f"Failed to load audit trail: {e}")
        
        return None
    
    def _dict_to_trail(self, data: Dict[str, Any]) -> AuditTrail:
        """Convert dictionary to AuditTrail."""
        events = [
            AuditEvent(
                event_id=e.get("event_id", ""),
                event_type=AuditEventType(e.get("event_type", "received")),
                timestamp=e.get("timestamp", ""),
                actor=e.get("actor", "system"),
                summary=e.get("summary", ""),
                details=e.get("details", {}),
                reasoning=e.get("reasoning"),
                confidence=e.get("confidence"),
                duration_ms=e.get("duration_ms"),
            )
            for e in data.get("events", [])
        ]
        
        return AuditTrail(
            invoice_id=data.get("invoice_id", ""),
            organization_id=data.get("organization_id", self.organization_id),
            vendor=data.get("vendor", "Unknown"),
            amount=data.get("amount", 0),
            events=events,
            current_status=data.get("current_status", "unknown"),
            created_at=data.get("created_at", ""),
            last_updated=data.get("last_updated", ""),
        )
    
    def log_classification(
        self,
        invoice_id: str,
        classification: str,
        confidence: float,
        reasoning: str,
        vendor: Optional[str] = None,
    ) -> AuditEvent:
        """Convenience method for logging classification."""
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.CLASSIFIED,
            summary=f"Classified as {classification}",
            reasoning=reasoning,
            confidence=confidence,
            vendor=vendor,
        )
    
    def log_extraction(
        self,
        invoice_id: str,
        fields: Dict[str, Any],
        confidence: float,
        vendor: Optional[str] = None,
        amount: Optional[float] = None,
    ) -> AuditEvent:
        """Convenience method for logging extraction."""
        field_summary = ", ".join([f"{k}={v}" for k, v in list(fields.items())[:3]])
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.EXTRACTED,
            summary=f"Extracted: {field_summary}...",
            details={"fields": fields},
            confidence=confidence,
            vendor=vendor,
            amount=amount,
        )
    
    def log_decision(
        self,
        invoice_id: str,
        decision: str,
        reasoning: str,
        confidence: float,
        factors: Optional[List[Dict[str, Any]]] = None,
    ) -> AuditEvent:
        """Convenience method for logging decisions."""
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.DECISION_MADE,
            summary=f"Decision: {decision}",
            reasoning=reasoning,
            confidence=confidence,
            details={"factors": factors} if factors else None,
        )
    
    def log_approval(
        self,
        invoice_id: str,
        approved_by: str,
        comment: Optional[str] = None,
    ) -> AuditEvent:
        """Convenience method for logging approvals."""
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.APPROVED,
            summary=f"Approved by {approved_by}",
            actor=approved_by,
            details={"comment": comment} if comment else None,
        )
    
    def log_rejection(
        self,
        invoice_id: str,
        rejected_by: str,
        reason: str,
    ) -> AuditEvent:
        """Convenience method for logging rejections."""
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.REJECTED,
            summary=f"Rejected by {rejected_by}",
            actor=rejected_by,
            reasoning=reason,
        )
    
    def log_posting(
        self,
        invoice_id: str,
        erp: str,
        erp_id: str,
        gl_code: Optional[str] = None,
    ) -> AuditEvent:
        """Convenience method for logging ERP posting."""
        return self.log(
            invoice_id=invoice_id,
            event_type=AuditEventType.POSTED,
            summary=f"Posted to {erp} as {erp_id}",
            details={"erp": erp, "erp_id": erp_id, "gl_code": gl_code},
        )
    
    def get_recent_activity(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get recent activity across all invoices."""
        all_events = []
        
        for trail in self._trails.values():
            for event in trail.events:
                all_events.append({
                    "invoice_id": trail.invoice_id,
                    "vendor": trail.vendor,
                    "amount": trail.amount,
                    "event": event.to_dict(),
                })
        
        # Sort by timestamp descending
        all_events.sort(key=lambda x: x["event"]["timestamp"], reverse=True)
        
        return all_events[:limit]
    
    def export_for_compliance(
        self,
        invoice_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Export trail in compliance-friendly format."""
        trail = self.get_trail(invoice_id)
        
        if not trail:
            return None
        
        return {
            "invoice_id": trail.invoice_id,
            "organization_id": trail.organization_id,
            "vendor": trail.vendor,
            "amount": trail.amount,
            "currency": "USD",
            "status": trail.current_status,
            "timeline": [
                {
                    "timestamp": e.timestamp,
                    "action": e.event_type.value,
                    "actor": e.actor,
                    "summary": e.summary,
                    "reasoning": e.reasoning,
                    "confidence": e.confidence,
                }
                for e in trail.events
            ],
            "exported_at": datetime.now().isoformat(),
        }
    
    # =========================================================================
    # ENHANCED QUERY CAPABILITIES
    # =========================================================================
    
    def query_trails(
        self,
        vendor: Optional[str] = None,
        status: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        actor: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditTrail]:
        """
        Query audit trails with filters.
        """
        results = list(self._trails.values())
        
        # Filter by vendor
        if vendor:
            vendor_lower = vendor.lower()
            results = [t for t in results if vendor_lower in t.vendor.lower()]
        
        # Filter by status
        if status:
            results = [t for t in results if t.current_status == status]
        
        # Filter by amount range
        if min_amount is not None:
            results = [t for t in results if t.amount >= min_amount]
        if max_amount is not None:
            results = [t for t in results if t.amount <= max_amount]
        
        # Filter by date range
        if start_date:
            results = [t for t in results if t.created_at >= start_date]
        if end_date:
            results = [t for t in results if t.created_at <= end_date]
        
        # Filter by event type (has at least one event of this type)
        if event_type:
            results = [
                t for t in results 
                if any(e.event_type == event_type for e in t.events)
            ]
        
        # Filter by actor
        if actor:
            results = [
                t for t in results 
                if any(e.actor == actor for e in t.events)
            ]
        
        # Sort by last updated descending
        results.sort(key=lambda t: t.last_updated, reverse=True)
        
        return results[:limit]
    
    def get_events_by_type(
        self,
        event_type: AuditEventType,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all events of a specific type."""
        events = []
        
        for trail in self._trails.values():
            for event in trail.events:
                if event.event_type == event_type:
                    events.append({
                        "invoice_id": trail.invoice_id,
                        "vendor": trail.vendor,
                        "amount": trail.amount,
                        "event": event.to_dict(),
                    })
        
        events.sort(key=lambda x: x["event"]["timestamp"], reverse=True)
        return events[:limit]
    
    def get_events_by_actor(
        self,
        actor: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get all events by a specific actor."""
        events = []
        
        for trail in self._trails.values():
            for event in trail.events:
                if event.actor == actor:
                    events.append({
                        "invoice_id": trail.invoice_id,
                        "vendor": trail.vendor,
                        "amount": trail.amount,
                        "event": event.to_dict(),
                    })
        
        events.sort(key=lambda x: x["event"]["timestamp"], reverse=True)
        return events[:limit]
    
    def get_summary_stats(self) -> Dict[str, Any]:
        """Get audit trail summary statistics."""
        trails = list(self._trails.values())
        
        # Count by status
        status_counts = {}
        for trail in trails:
            status_counts[trail.current_status] = status_counts.get(trail.current_status, 0) + 1
        
        # Count by event type
        event_counts = {}
        for trail in trails:
            for event in trail.events:
                event_type = event.event_type.value
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        # Count by actor
        actor_counts = {}
        for trail in trails:
            for event in trail.events:
                actor_counts[event.actor] = actor_counts.get(event.actor, 0) + 1
        
        # Average events per trail
        avg_events = sum(len(t.events) for t in trails) / len(trails) if trails else 0
        
        return {
            "total_trails": len(trails),
            "total_events": sum(len(t.events) for t in trails),
            "average_events_per_trail": round(avg_events, 1),
            "status_distribution": status_counts,
            "event_type_distribution": event_counts,
            "actor_distribution": actor_counts,
            "total_amount": sum(t.amount for t in trails),
        }
    
    def export_to_csv(self, invoice_ids: Optional[List[str]] = None) -> str:
        """Export audit trails to CSV format."""
        import io
        import csv
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            "Invoice ID", "Vendor", "Amount", "Status", "Event Type",
            "Event Time", "Actor", "Summary", "Reasoning", "Confidence"
        ])
        
        trails = [self._trails[id] for id in invoice_ids if id in self._trails] if invoice_ids else list(self._trails.values())
        
        for trail in trails:
            for event in trail.events:
                writer.writerow([
                    trail.invoice_id,
                    trail.vendor,
                    trail.amount,
                    trail.current_status,
                    event.event_type.value,
                    event.timestamp,
                    event.actor,
                    event.summary,
                    event.reasoning or "",
                    event.confidence or "",
                ])
        
        return output.getvalue()
    
    def export_to_json(self, invoice_ids: Optional[List[str]] = None) -> str:
        """Export audit trails to JSON format."""
        import json
        
        trails = [self._trails[id] for id in invoice_ids if id in self._trails] if invoice_ids else list(self._trails.values())
        
        return json.dumps({
            "organization_id": self.organization_id,
            "exported_at": datetime.now().isoformat(),
            "trails": [t.to_dict() for t in trails],
            "summary": self.get_summary_stats(),
        }, indent=2)
    
    def get_compliance_report(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a compliance report for audit purposes.
        """
        trails = self.query_trails(start_date=start_date, end_date=end_date, limit=10000)
        
        # Group by status
        approved = [t for t in trails if t.current_status == "approved"]
        rejected = [t for t in trails if t.current_status == "rejected"]
        pending = [t for t in trails if t.current_status in ["pending_approval", "pending", "new"]]
        posted = [t for t in trails if t.current_status == "posted"]
        paid = [t for t in trails if t.current_status == "paid"]
        
        # Auto vs manual approvals
        auto_approved = 0
        manual_approved = 0
        for trail in approved + posted + paid:
            for event in trail.events:
                if event.event_type == AuditEventType.AUTO_APPROVED:
                    auto_approved += 1
                    break
                elif event.event_type == AuditEventType.APPROVED:
                    manual_approved += 1
                    break
        
        # Rejections with reasons
        rejection_reasons = {}
        for trail in rejected:
            for event in trail.events:
                if event.event_type == AuditEventType.REJECTED and event.reasoning:
                    reason = event.reasoning[:50]
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        
        return {
            "report_period": {
                "start": start_date or "All time",
                "end": end_date or datetime.now().isoformat(),
            },
            "total_invoices": len(trails),
            "total_amount": sum(t.amount for t in trails),
            "status_summary": {
                "approved": {"count": len(approved), "amount": sum(t.amount for t in approved)},
                "rejected": {"count": len(rejected), "amount": sum(t.amount for t in rejected)},
                "pending": {"count": len(pending), "amount": sum(t.amount for t in pending)},
                "posted": {"count": len(posted), "amount": sum(t.amount for t in posted)},
                "paid": {"count": len(paid), "amount": sum(t.amount for t in paid)},
            },
            "approval_method": {
                "auto_approved": auto_approved,
                "manual_approved": manual_approved,
                "auto_approval_rate": round(auto_approved / (auto_approved + manual_approved) * 100, 1) if (auto_approved + manual_approved) > 0 else 0,
            },
            "rejection_reasons": rejection_reasons,
            "generated_at": datetime.now().isoformat(),
        }
    
    def get_all_trails(self) -> List[AuditTrail]:
        """Get all audit trails."""
        return list(self._trails.values())


# Convenience function
def get_audit_trail(organization_id: str = "default") -> AuditTrailService:
    """Get an audit trail service instance."""
    return AuditTrailService(organization_id=organization_id)


# ============================================================================
# Legacy compatibility layer for main.py imports
# ============================================================================

def init_audit_db():
    """Initialize audit database (no-op, db is initialized on first use)."""
    # Database is lazily initialized via get_db() in the service
    # This function exists for backwards compatibility
    pass


class AuditActions:
    """Action types for audit events."""
    EMAIL_PROCESSED = "email_processed"
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_COMPLETED = "task_completed"
    INVOICE_PROCESSED = "invoice_processed"
    PAYMENT_MADE = "payment_made"


class EntityTypes:
    """Entity types for audit tracking."""
    EMAIL = "email"
    TASK = "task"
    INVOICE = "invoice"
    PAYMENT = "payment"
    VENDOR = "vendor"


class SourceTypes:
    """Source types for audit events."""
    EMAIL = "email"
    SLACK = "slack"
    API = "api"
    SYSTEM = "system"


def record_audit_event(
    user_email: str,
    action: str,
    entity_type: str,
    entity_id: str = None,
    source_type: str = "system",
    metadata: Dict[str, Any] = None,
    organization_id: str = "default",
) -> str:
    """Record an audit event (legacy function)."""
    service = get_audit_trail(organization_id)
    
    # Map to new event type
    event_type_map = {
        AuditActions.EMAIL_PROCESSED: AuditEventType.RECEIVED,
        AuditActions.TASK_CREATED: AuditEventType.DECISION_MADE,
        AuditActions.TASK_ASSIGNED: AuditEventType.ROUTED,
        AuditActions.TASK_COMPLETED: AuditEventType.APPROVED,
        AuditActions.INVOICE_PROCESSED: AuditEventType.EXTRACTED,
        AuditActions.PAYMENT_MADE: AuditEventType.PAYMENT_SENT,
    }
    
    event_type = event_type_map.get(action, AuditEventType.DECISION_MADE)
    
    if entity_id:
        service.log(
            invoice_id=entity_id,
            event_type=event_type,
            actor=user_email,
            summary=f"{action}: {entity_type}",
            details=metadata or {},
        )

    return str(uuid.uuid4())


def get_entity_history(entity_type: str, entity_id: str, organization_id: str = "default") -> Dict[str, Any]:
    """Get history for an entity (legacy function)."""
    service = get_audit_trail(organization_id)
    trail = service.get_trail(entity_id)
    
    if not trail:
        return {"entity_type": entity_type, "entity_id": entity_id, "events": []}
    
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "events": [e.to_dict() for e in trail.events],
    }


def get_user_activity(user_email: str, limit: int = 50, organization_id: str = "default") -> Dict[str, Any]:
    """Get activity for a user (legacy function)."""
    service = get_audit_trail(organization_id)
    
    # Collect events from all trails for this user
    events = []
    for trail in service.get_all_trails():
        for event in trail.events:
            if event.actor == user_email:
                events.append(event.to_dict())
    
    # Sort by timestamp and limit
    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return {
        "user_email": user_email,
        "events": events[:limit],
        "total": len(events),
    }
