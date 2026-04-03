"""Canonical TypedDict shapes for the most important AP data structures.

These definitions document the *actual* dict shapes returned by service
functions today.  They are **not yet enforced** at every call-site; the
goal is gradual adoption so new code can import and annotate with them.

Usage::

    from clearledgr.core.typed_dicts import APItemDict, WorklistItemDict

    def my_helper(item: APItemDict) -> None: ...
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from typing_extensions import NotRequired, TypedDict


class APItemDict(TypedDict, total=False):
    """Shape of the dict returned by ``ClearledgrDB.get_ap_item()``
    and consumed throughout the AP pipeline.

    ``total=False`` because many columns are nullable / only populated
    after certain lifecycle stages.
    """

    id: str
    organization_id: str
    thread_id: Optional[str]
    message_id: Optional[str]
    vendor_name: Optional[str]
    invoice_number: Optional[str]
    amount: Optional[float]
    currency: Optional[str]
    due_date: Optional[str]
    state: str
    subject: Optional[str]
    sender: Optional[str]
    erp_reference: Optional[str]
    erp_posted_at: Optional[str]
    erp_bill_id: Optional[str]
    last_error: Optional[str]
    exception_code: Optional[str]
    exception_severity: Optional[str]
    requires_field_review: bool
    confidence_blockers: Optional[Any]
    source_conflicts: Optional[Any]
    field_confidences: Optional[Dict[str, Any]]
    metadata: Any  # JSON blob stored as str or dict
    invoice_key: Optional[str]
    workflow_id: Optional[str]
    supersedes_ap_item_id: Optional[str]
    superseded_by_ap_item_id: Optional[str]
    entity_id: Optional[str]
    entity_code: Optional[str]
    entity_name: Optional[str]
    entity_routing_status: Optional[str]
    entity_route_reason: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class WorklistItemDict(TypedDict, total=False):
    """Shape of the dict returned by ``build_worklist_item()``.

    Extends APItemDict with derived / presentation fields that the sidebar,
    admin console, and approval surfaces consume.
    """

    # --- everything from APItemDict is present (inherited at runtime) ---
    id: str
    organization_id: str
    state: str
    vendor_name: Optional[str]
    amount: Optional[float]
    currency: Optional[str]
    invoice_number: Optional[str]
    due_date: Optional[str]

    # Derived fields
    document_type: str
    has_attachment: bool
    attachment_count: int
    attachment_url: Optional[str]
    attachment_names: List[str]
    source_count: int
    primary_source: Dict[str, Optional[str]]
    confidence_gate: Dict[str, Any]
    field_confidences: Dict[str, Any]
    requires_field_review: bool
    requires_extraction_review: bool
    confidence_blockers: List[Any]
    field_provenance: Dict[str, Any]
    field_evidence: Dict[str, Any]
    source_conflicts: List[Dict[str, Any]]
    risk_signals: Dict[str, Any]
    entity_routing: Dict[str, Any]
    entity_routing_status: str
    entity_candidates: List[Dict[str, Any]]
    budget_status: Optional[str]
    budget_requires_decision: bool
    exception_code: Optional[str]
    exception_severity: Optional[str]
    next_action: Optional[str]
    pipeline_blockers: List[Dict[str, Any]]
    field_review_blockers: List[Dict[str, Any]]
    blocked_fields: List[str]
    workflow_paused_reason: Optional[str]
    approval_followup: Dict[str, Any]
    approval_wait_minutes: int
    approval_pending_assignees: List[str]
    erp_status: str
    erp_connector_available: bool
    non_invoice_review_required: bool
    non_invoice_resolution: Dict[str, Any]
    linked_finance_documents: List[Dict[str, Any]]
    ap_decision_reasoning: Optional[str]
    ap_decision_recommendation: Optional[str]
    ap_decision_risk_flags: List[Any]
    needs_info_question: Optional[str]
    needs_info_draft_id: Optional[str]
    followup_next_action: Optional[str]
    gl_suggestion: Optional[Dict[str, Any]]
    vendor_suggestion: Optional[Dict[str, Any]]
    is_resubmission: bool
    has_resubmission: bool


class ValidationGateResult(TypedDict, total=False):
    """Shape of the dict returned by
    ``InvoiceValidationMixin._evaluate_deterministic_validation()``.
    """

    passed: bool
    checked_at: str
    reason_codes: List[str]
    reasons: List[Dict[str, Any]]
    policy_compliance: Dict[str, Any]
    po_match_result: Optional[Dict[str, Any]]
    budget_impact: List[Dict[str, Any]]
    budget: Dict[str, Any]
    confidence_gate: Dict[str, Any]
    erp_preflight: Optional[Dict[str, Any]]


__all__ = [
    "APItemDict",
    "WorklistItemDict",
    "ValidationGateResult",
]
