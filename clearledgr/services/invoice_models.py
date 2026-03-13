"""Invoice data model — extracted from invoice_workflow.py for modularity."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class InvoiceData:
    """Extracted invoice data from email."""
    gmail_id: str
    subject: str
    sender: str
    vendor_name: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    attachment_url: Optional[str] = None
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    # Raw invoice text for discount detection
    invoice_text: Optional[str] = None
    # Agent reasoning (added 2026-01-23)
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[list] = None
    reasoning_risks: Optional[list] = None
    # Full intelligence (added 2026-01-23)
    vendor_intelligence: Optional[Dict] = None
    policy_compliance: Optional[Dict] = None
    priority: Optional[Dict] = None
    budget_impact: Optional[list] = None
    po_match_result: Optional[Dict[str, Any]] = None
    budget_check_result: Optional[Dict[str, Any]] = None
    potential_duplicates: int = 0
    insights: Optional[list] = None
    field_confidences: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    erp_preflight: Optional[Dict[str, Any]] = None
