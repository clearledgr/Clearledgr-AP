"""API request models."""
from typing import List, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel
from clearledgr.models.transactions import BankTransaction, GLTransaction
from clearledgr.models.reconciliation import ReconciliationConfig


class AttachmentInput(CLBaseModel):
    filename: str
    content_type: Optional[str] = None
    content_base64: Optional[str] = None
    content_text: Optional[str] = None


class InvoiceExtractionRequest(CLBaseModel):
    email_subject: Optional[str] = None
    email_sender: Optional[str] = None
    email_body: Optional[str] = None
    attachments: List[AttachmentInput] = Field(default_factory=list)
    organization_id: Optional[str] = None
    requester: Optional[str] = None


class ReconciliationRequest(CLBaseModel):
    bank_transactions: List[BankTransaction] = Field(default_factory=list)
    gl_transactions: List[GLTransaction] = Field(default_factory=list)
    config: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    organization_id: Optional[str] = None
    requester: Optional[str] = None
