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
    metadata: Dict[str, Any] = {}


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
    sources: List[SplitSourceRequest] = []


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
    metadata: Dict[str, Any] = {}


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
