"""
Clearledgr Audit Logging

Immutable audit trail for all financial operations.
Required for SOX compliance and financial controls.
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict
import uuid
import sqlite3
from contextlib import contextmanager


class AuditAction(str, Enum):
    """Auditable actions."""
    # Authentication
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    LOGIN_FAILED = "auth.login_failed"
    
    # Transactions
    TRANSACTION_CREATE = "transaction.create"
    TRANSACTION_UPDATE = "transaction.update"
    TRANSACTION_DELETE = "transaction.delete"
    
    # Reconciliation
    RECONCILIATION_START = "reconciliation.start"
    RECONCILIATION_COMPLETE = "reconciliation.complete"
    RECONCILIATION_FAILED = "reconciliation.failed"
    MATCH_CREATE = "match.create"
    MATCH_APPROVE = "match.approve"
    MATCH_REJECT = "match.reject"
    
    # Exceptions
    EXCEPTION_CREATE = "exception.create"
    EXCEPTION_ASSIGN = "exception.assign"
    EXCEPTION_RESOLVE = "exception.resolve"
    EXCEPTION_ESCALATE = "exception.escalate"
    
    # Journal Entries
    DRAFT_CREATE = "draft.create"
    DRAFT_APPROVE = "draft.approve"
    DRAFT_REJECT = "draft.reject"
    DRAFT_POST = "draft.post"
    
    # System
    CONFIG_CHANGE = "config.change"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    PERMISSION_CHANGE = "permission.change"
    
    # AI/Vita
    VITA_COMMAND = "vita.command"
    VITA_EXECUTE = "vita.execute"
    AI_DECISION = "ai.decision"


@dataclass
class AuditEntry:
    """Immutable audit log entry."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: str = ""
    user_id: str = ""
    user_email: str = ""
    organization_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    checksum: str = ""
    
    def __post_init__(self):
        """Calculate checksum for tamper detection."""
        if not self.checksum:
            self.checksum = self._calculate_checksum()
    
    def _calculate_checksum(self) -> str:
        """Calculate SHA-256 checksum of entry data."""
        data = {
            "id": self.id,
            "timestamp": self.timestamp,
            "action": self.action,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
        }
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    def verify(self) -> bool:
        """Verify entry hasn't been tampered with."""
        return self.checksum == self._calculate_checksum()
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditLogger:
    """
    Audit logger that writes to SQLite.
    
    Features:
    - Immutable entries with checksums
    - Tamper detection
    - Query by user, org, action, time range
    - Export for compliance reports
    """
    
    def __init__(self, db_path: str = "audit_trail.sqlite3"):
        self.db_path = db_path
        self._initialize_db()
    
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _initialize_db(self):
        """Create audit table if not exists."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    user_id TEXT,
                    user_email TEXT,
                    organization_id TEXT,
                    resource_type TEXT,
                    resource_id TEXT,
                    details TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    previous_state TEXT,
                    new_state TEXT,
                    checksum TEXT NOT NULL
                )
            """)
            
            # Create indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(organization_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id)")
    
    def log(
        self,
        action: AuditAction,
        user_id: str,
        organization_id: str,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        user_email: str = "",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        previous_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """
        Log an auditable action.
        
        Args:
            action: The action being performed
            user_id: ID of user performing action
            organization_id: Organization context
            resource_type: Type of resource (transaction, match, etc)
            resource_id: ID of specific resource
            details: Additional context
            user_email: User's email for readability
            ip_address: Client IP address
            user_agent: Client user agent
            previous_state: State before action (for updates)
            new_state: State after action (for updates)
        
        Returns:
            The created audit entry
        """
        entry = AuditEntry(
            action=action.value if isinstance(action, AuditAction) else action,
            user_id=user_id,
            user_email=user_email,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
            previous_state=previous_state,
            new_state=new_state,
        )
        
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO audit_log (
                    id, timestamp, action, user_id, user_email, organization_id,
                    resource_type, resource_id, details, ip_address, user_agent,
                    previous_state, new_state, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id,
                entry.timestamp,
                entry.action,
                entry.user_id,
                entry.user_email,
                entry.organization_id,
                entry.resource_type,
                entry.resource_id,
                json.dumps(entry.details),
                entry.ip_address,
                entry.user_agent,
                json.dumps(entry.previous_state) if entry.previous_state else None,
                json.dumps(entry.new_state) if entry.new_state else None,
                entry.checksum,
            ))
        
        return entry
    
    def query(
        self,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEntry]:
        """Query audit log with filters."""
        conditions = []
        params = []
        
        if organization_id:
            conditions.append("organization_id = ?")
            params.append(organization_id)
        
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        
        if action:
            conditions.append("action = ?")
            params.append(action)
        
        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)
        
        if resource_id:
            conditions.append("resource_id = ?")
            params.append(resource_id)
        
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time.isoformat())
        
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time.isoformat())
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        with self._connect() as conn:
            cursor = conn.execute(f"""
                SELECT * FROM audit_log
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            
            entries = []
            for row in cursor:
                entry = AuditEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    action=row["action"],
                    user_id=row["user_id"],
                    user_email=row["user_email"],
                    organization_id=row["organization_id"],
                    resource_type=row["resource_type"],
                    resource_id=row["resource_id"],
                    details=json.loads(row["details"]) if row["details"] else {},
                    ip_address=row["ip_address"],
                    user_agent=row["user_agent"],
                    previous_state=json.loads(row["previous_state"]) if row["previous_state"] else None,
                    new_state=json.loads(row["new_state"]) if row["new_state"] else None,
                    checksum=row["checksum"],
                )
                entries.append(entry)
            
            return entries
    
    def verify_integrity(self, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify integrity of audit log.
        
        Returns:
            Dict with verification results
        """
        entries = self.query(organization_id=organization_id, limit=10000)
        
        total = len(entries)
        valid = 0
        invalid = []
        
        for entry in entries:
            if entry.verify():
                valid += 1
            else:
                invalid.append(entry.id)
        
        return {
            "total_entries": total,
            "valid_entries": valid,
            "invalid_entries": len(invalid),
            "invalid_ids": invalid[:10],  # First 10 for investigation
            "integrity_verified": len(invalid) == 0,
        }
    
    def export_for_compliance(
        self,
        organization_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict[str, Any]]:
        """Export audit log for compliance reporting."""
        entries = self.query(
            organization_id=organization_id,
            start_time=start_time,
            end_time=end_time,
            limit=100000,
        )
        
        return [entry.to_dict() for entry in entries]


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def audit_log(
    action: AuditAction,
    user_id: str,
    organization_id: str,
    **kwargs,
) -> AuditEntry:
    """Convenience function to log an audit entry."""
    return get_audit_logger().log(action, user_id, organization_id, **kwargs)
