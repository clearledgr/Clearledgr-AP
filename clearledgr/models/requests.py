"""API request models."""
from typing import List, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel


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
