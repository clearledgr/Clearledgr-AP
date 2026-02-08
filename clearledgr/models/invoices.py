"""Invoice models for extraction and categorization."""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel
from clearledgr.models.transactions import Money


class InvoiceLineItem(CLBaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceExtraction(CLBaseModel):
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    total: Optional[Money] = None
    currency: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvoiceCategorization(CLBaseModel):
    gl_code: Optional[str] = None
    gl_name: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class Invoice(CLBaseModel):
    invoice_id: str
    extraction: InvoiceExtraction
    categorization: Optional[InvoiceCategorization] = None
    status: str = Field(default="extracted")
