"""AP item API request contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LinkSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)
    subject: Optional[str] = None
    sender: Optional[str] = None
    detected_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LinkGmailThreadRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    message_id: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    detected_at: Optional[str] = None
    note: Optional[str] = None


class UpdateApItemFieldsRequest(BaseModel):
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    note: Optional[str] = None


class CreateApItemTaskRequest(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    task_type: str = Field(default="follow_up", min_length=1)
    priority: str = Field(default="medium", min_length=1)
    due_date: Optional[str] = None
    assignee_email: Optional[str] = None
    note: Optional[str] = None


class UpdateApItemTaskStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)
    note: Optional[str] = None


class AssignApItemTaskRequest(BaseModel):
    assignee_email: str = Field(..., min_length=1)


class AddApItemTaskCommentRequest(BaseModel):
    comment: str = Field(..., min_length=1)


class AddApItemNoteRequest(BaseModel):
    body: str = Field(..., min_length=1)


class AddApItemCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)


class AddApItemFileRequest(BaseModel):
    label: str = Field(..., min_length=1)
    url: Optional[str] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None


class CreateComposeRecordRequest(BaseModel):
    draft_id: Optional[str] = None
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    recipients: List[str] = Field(default_factory=list)
    body_preview: Optional[str] = None
    note: Optional[str] = None


class LinkComposeDraftRequest(BaseModel):
    draft_id: Optional[str] = None
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    recipients: List[str] = Field(default_factory=list)
    body_preview: Optional[str] = None
    note: Optional[str] = None


class MergeItemsRequest(BaseModel):
    source_ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_merge", min_length=1)


class SplitSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)


class SplitItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_split", min_length=1)
    sources: List[SplitSourceRequest] = Field(default_factory=list)


class ResubmitRejectedItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="corrected_resubmission", min_length=1)
    initial_state: str = Field(default="received", min_length=1)
    copy_sources: bool = True
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResolveFieldReviewRequest(BaseModel):
    field: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, description="email, attachment, or manual")
    manual_value: Optional[Any] = None
    note: Optional[str] = None
    auto_resume: bool = True


class BulkResolveFieldReviewRequest(BaseModel):
    ap_item_ids: List[str] = Field(..., min_length=1)
    field: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, description="email, attachment, or manual")
    manual_value: Optional[Any] = None
    note: Optional[str] = None
    auto_resume: bool = True


class ResolveNonInvoiceReviewRequest(BaseModel):
    outcome: str = Field(..., min_length=1)
    related_reference: Optional[str] = None
    related_ap_item_id: Optional[str] = None
    note: Optional[str] = None
    close_record: bool = True


class ResolveEntityRouteRequest(BaseModel):
    selection: Optional[str] = None
    entity_id: Optional[str] = None
    entity_code: Optional[str] = None
    entity_name: Optional[str] = None
    note: Optional[str] = None
