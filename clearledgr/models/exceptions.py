"""Exception and approval models."""
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel


class ExceptionItem(CLBaseModel):
    exception_id: str
    entity_type: str
    entity_id: str
    reason: str
    severity: str = Field(default="medium")
    status: str = Field(default="open")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ApprovalDecision(CLBaseModel):
    decision_id: str
    exception_id: str
    approved: bool
    approved_by: Optional[str] = None
    notes: Optional[str] = None
    decided_at: datetime
