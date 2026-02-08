"""Audit trail service wrapper."""
from typing import Any, Dict, Optional
from clearledgr.services import audit_trail


class AuditTrailService:
    def __init__(self) -> None:
        audit_trail.init_audit_db()

    def record_event(
        self,
        user_email: str,
        action: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        source_name: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        organization_id: Optional[str] = None,
    ) -> str:
        merged_metadata: Dict[str, Any] = dict(metadata or {})
        if source_id is not None:
            merged_metadata["source_id"] = source_id
        if source_name is not None:
            merged_metadata["source_name"] = source_name
        if before_state is not None:
            merged_metadata["before_state"] = before_state
        if after_state is not None:
            merged_metadata["after_state"] = after_state

        return audit_trail.record_audit_event(
            user_email=user_email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            source_type=source_type,
            metadata=merged_metadata,
            organization_id=organization_id,
        )
