"""Ingestion event models."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel
from clearledgr.models.transactions import BankTransaction


class IngestionEvent(CLBaseModel):
    source: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    organization_id: Optional[str] = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NormalizedEvent(CLBaseModel):
    source: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    organization_id: Optional[str] = None


class IngestionResult(CLBaseModel):
    status: str
    workflow_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class EmailAttachment(CLBaseModel):
    filename: str
    content_type: Optional[str] = None
    content_base64: Optional[str] = None
    content_text: Optional[str] = None


class EmailIngestRequest(CLBaseModel):
    """Direct email ingestion payload (bank statements, transaction CSV/PDF already parsed)."""

    email_id: Optional[str] = None
    thread_id: Optional[str] = None
    email_subject: Optional[str] = None
    email_sender: Optional[str] = None
    received_date: Optional[datetime] = None
    organization_id: Optional[str] = None
    source: str = "gmail"
    transactions: List[BankTransaction] = Field(default_factory=list, description="Normalized bank transactions from the email/attachments")
    attachments: List[EmailAttachment] = Field(default_factory=list)
    trigger_reconciliation: bool = Field(default=True, description="If true, start reconciliation immediately")
