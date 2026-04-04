"""Finance document routing — single source of truth for document classification and workflow.

Every email that arrives in a finance team's inbox is one of these document types.
Each type has a deterministic workflow: what state it starts in, whether it needs
approval, whether it creates an AP item, and what labels it gets in Gmail.

This is the core routing table. The classifier produces the type, this module
decides what to do with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DocumentRoute:
    """Defines how a document type flows through Clearledgr."""
    type: str                          # Canonical type ID
    label: str                         # Human-readable label
    plural_label: str                  # Plural form
    creates_ap_item: bool              # Does this create an AP item?
    initial_state: str                 # Starting state if AP item is created
    needs_approval: bool               # Requires approval workflow?
    needs_erp_posting: bool            # Should be posted to ERP as a bill?
    auto_close: bool                   # Auto-close on creation (no workflow)?
    gmail_label: str                   # Gmail label to apply
    workflow_guidance: str             # What the operator should know
    match_to: Optional[str] = None     # Should be matched against another record type


# ---------------------------------------------------------------------------
# Complete finance document taxonomy
# ---------------------------------------------------------------------------

DOCUMENT_ROUTES = {
    # --- AP payables (full workflow) ---
    "invoice": DocumentRoute(
        type="invoice",
        label="Invoice",
        plural_label="Invoices",
        creates_ap_item=True,
        initial_state="received",
        needs_approval=True,
        needs_erp_posting=True,
        auto_close=False,
        gmail_label="Clearledgr/Invoices",
        workflow_guidance="Vendor bill requiring approval and payment.",
    ),
    "payment_request": DocumentRoute(
        type="payment_request",
        label="Payment request",
        plural_label="Payment requests",
        creates_ap_item=True,
        initial_state="received",
        needs_approval=True,
        needs_erp_posting=True,
        auto_close=False,
        gmail_label="Clearledgr/Payment Requests",
        workflow_guidance="Non-invoice payment request. Route to approval before payment.",
    ),
    "debit_note": DocumentRoute(
        type="debit_note",
        label="Debit note",
        plural_label="Debit notes",
        creates_ap_item=True,
        initial_state="received",
        needs_approval=True,
        needs_erp_posting=True,
        auto_close=False,
        gmail_label="Clearledgr/Invoices",
        workflow_guidance="Additional charge from vendor. Link to original invoice if applicable.",
    ),

    # --- Credits (reduce what you owe) ---
    "credit_note": DocumentRoute(
        type="credit_note",
        label="Credit note",
        plural_label="Credit notes",
        creates_ap_item=True,
        initial_state="received",
        needs_approval=False,
        needs_erp_posting=True,
        auto_close=False,
        gmail_label="Clearledgr/Credit Notes",
        workflow_guidance="Vendor credit reducing your balance. Match to the original invoice.",
        match_to="invoice",
    ),
    "refund": DocumentRoute(
        type="refund",
        label="Refund",
        plural_label="Refunds",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Refunds",
        workflow_guidance="Refund confirmation. Record for reconciliation.",
        match_to="invoice",
    ),

    # --- Subscription / auto-charged (not payables) ---
    "subscription_notification": DocumentRoute(
        type="subscription_notification",
        label="Subscription charge",
        plural_label="Subscription charges",
        creates_ap_item=True,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="SaaS subscription charge — card was already billed. Recorded for GL coding and expense tracking.",
    ),

    # --- Payment confirmations (already paid) ---
    "receipt": DocumentRoute(
        type="receipt",
        label="Receipt",
        plural_label="Receipts",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Receipts",
        workflow_guidance="Payment receipt. Transaction already completed.",
        match_to="invoice",
    ),
    "remittance_advice": DocumentRoute(
        type="remittance_advice",
        label="Remittance advice",
        plural_label="Remittance advices",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Payments",
        workflow_guidance="Proof of payment sent to vendor. Match to the original AP item.",
        match_to="invoice",
    ),

    # --- Reconciliation inputs (not payables) ---
    "statement": DocumentRoute(
        type="statement",
        label="Vendor statement",
        plural_label="Vendor statements",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="Vendor account summary. Use for statement reconciliation, not a payable.",
    ),
    "bank_notification": DocumentRoute(
        type="bank_notification",
        label="Bank notification",
        plural_label="Bank notifications",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="Bank charge, direct debit, or FX notification. Record for reconciliation.",
    ),

    # --- Procurement / PO (informational) ---
    "po_confirmation": DocumentRoute(
        type="po_confirmation",
        label="PO confirmation",
        plural_label="PO confirmations",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="Vendor confirmed your purchase order. Update PO status.",
    ),

    # --- Tax / compliance ---
    "tax_document": DocumentRoute(
        type="tax_document",
        label="Tax document",
        plural_label="Tax documents",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="VAT invoice, WHT certificate, or tax receipt. Flag for tax compliance reporting.",
    ),

    # --- Vendor communications ---
    "contract_renewal": DocumentRoute(
        type="contract_renewal",
        label="Contract / renewal",
        plural_label="Contracts & renewals",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="Vendor contract or renewal notice. Review terms and link to vendor profile.",
    ),
    "dispute_response": DocumentRoute(
        type="dispute_response",
        label="Dispute response",
        plural_label="Dispute responses",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="Clearledgr/Processed",
        workflow_guidance="Vendor reply to a dispute. Link to existing dispute and notify operator.",
        match_to="dispute",
    ),

    # --- Fallback ---
    "noise": DocumentRoute(
        type="noise",
        label="Not finance-related",
        plural_label="Non-finance emails",
        creates_ap_item=False,
        initial_state="closed",
        needs_approval=False,
        needs_erp_posting=False,
        auto_close=True,
        gmail_label="",
        workflow_guidance="Not a finance document. No action needed.",
    ),
}

# All valid document type IDs
VALID_DOCUMENT_TYPES = frozenset(DOCUMENT_ROUTES.keys())

# Types that create AP items
AP_ITEM_TYPES = frozenset(
    t for t, r in DOCUMENT_ROUTES.items() if r.creates_ap_item
)

# Types that need the full approval workflow
APPROVAL_TYPES = frozenset(
    t for t, r in DOCUMENT_ROUTES.items() if r.needs_approval
)

# Types that should be matched to an existing record
MATCH_TYPES = frozenset(
    t for t, r in DOCUMENT_ROUTES.items() if r.match_to
)


def get_route(document_type: str) -> DocumentRoute:
    """Get the routing config for a document type. Falls back to noise."""
    normalized = str(document_type or "").strip().lower()
    # Handle aliases
    if normalized in ("subscription", "saas_charge", "recurring_charge"):
        normalized = "subscription_notification"
    if normalized in ("credit_memo", "creditnote"):
        normalized = "credit_note"
    if normalized in ("payment_confirmation", "payment"):
        normalized = "receipt"
    if normalized in ("bank_statement",):
        normalized = "statement"
    return DOCUMENT_ROUTES.get(normalized, DOCUMENT_ROUTES["noise"])


def should_create_ap_item(document_type: str) -> bool:
    return get_route(document_type).creates_ap_item


def get_initial_state(document_type: str) -> str:
    return get_route(document_type).initial_state


def needs_approval(document_type: str) -> bool:
    return get_route(document_type).needs_approval


def get_gmail_label(document_type: str) -> str:
    return get_route(document_type).gmail_label
