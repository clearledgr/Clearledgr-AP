"""Invoice data model — the single intake-data shape used across every
intake channel (Gmail, Outlook, NetSuite, SAP, vendor portals, manual
upload). What started as the email-extraction record is now the
canonical contract every coordination call site reads from.

Channel-agnostic by design: the historic ``gmail_id`` is now treated
as a generic per-source idempotency key. For non-Gmail channels we
synthesize it from the source identifier (e.g.
``netsuite-bill:5135``, ``sap-bill:1010/5105600123/2026``) so the ~80
read sites that key off ``gmail_id`` continue to work without
touching them.

The handful of call sites that read ``gmail_id`` as an *actual Gmail
message resource id* (Gmail labels API, email-fetch, Gmail deeplinks)
guard on ``source_type == "gmail"`` or ``erp_native`` first. See
``state_observers.GmailLabelObserver`` and
``approval_card_builder._build_message_link`` for the reference
guard pattern.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


SourceType = Literal["gmail", "outlook", "netsuite", "sap_s4hana", "portal", "manual"]


@dataclass
class InvoiceData:
    """Extracted invoice data — channel-agnostic.

    Constructed by every intake path: the Gmail processor builds it
    from extracted email fields; the NetSuite + SAP webhook
    dispatchers build it after fetching enrichment context from the
    ERP. Either way, downstream coordination
    (``InvoiceWorkflowService.process_new_invoice``) treats them
    identically.
    """
    gmail_id: str = ""
    """Per-source idempotency key. For Gmail: the Gmail message id.
    For ERP-native: ``f"{source_type}-bill:{source_id}"`` synthesized
    from the source identifier. The 80+ read sites that use
    ``invoice.gmail_id`` as a foreign key (DB lookups, Slack thread
    storage, audit references) work uniformly — only sites that call
    Gmail-specific APIs (labels, message-fetch, mail.google.com URL
    builder) require source-type guards."""

    subject: str = ""
    sender: str = ""
    vendor_name: str = ""
    amount: float = 0.0
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
    # Payment terms (e.g. "Net 30", "Due on receipt", "2/10 Net 30")
    payment_terms: Optional[str] = None
    # Tax extraction
    tax_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    subtotal: Optional[float] = None
    # Discount extraction
    discount_amount: Optional[float] = None
    discount_terms: Optional[str] = None  # e.g., "2/10 NET 30" (2% discount if paid in 10 days)
    # Bank/payment details extracted from invoice
    bank_details: Optional[Dict[str, Any]] = None
    # Dict shape: {"bank_name": str, "account_number": str, "routing_number": str, "iban": str, "swift": str, "sort_code": str}
    # Line items (structured extraction)
    # Each line item: {"description": str, "quantity": float, "unit_price": float,
    #   "amount": float, "gl_code": Optional[str], "tax_amount": Optional[float]}
    line_items: Optional[List[Dict[str, Any]]] = None

    # ── Channel-aware extension (added 2026-04-25 to support
    # ERP-native intake — bills landing in NetSuite/SAP without
    # touching Gmail). See module docstring + DESIGN_THESIS §5 ──

    source_type: SourceType = "gmail"
    """The intake channel. Gates Gmail-specific side-effects (label
    sync, message-fetch, deeplinks) — anything not Gmail short-circuits
    those branches. Use ``erp_native`` below for the broader "ERP
    posted this, not us" semantics."""

    source_id: Optional[str] = None
    """Canonical per-source identifier. For Gmail this is the message
    id (mirrored into ``gmail_id`` for back-compat). For NetSuite this
    is the bill internal id. For SAP this is the composite key
    ``"<CC>/<DocNum>/<FY>"``. The dispatcher constructs the synthetic
    ``gmail_id`` value from this field via ``__post_init__``."""

    erp_native: bool = False
    """True when the intake source is the ERP itself (NetSuite SuiteScript
    afterSubmit, SAP Event Mesh / BAdI). Implies (a) the bill is
    already posted in the ERP — Clearledgr must NOT call
    ``post_to_erp`` again, just track it, and (b) Gmail-specific
    side-effects (label sync, email parsing) are skipped."""

    erp_metadata: Optional[Dict[str, Any]] = field(default=None)
    """Channel-specific metadata that downstream coordination + audit
    paths read. Examples:
    NetSuite: ``{"ns_internal_id", "subsidiary_id", "payment_hold",
        "approval_status", "external_id", "po_internal_ids",
        "item_receipt_ids", "ns_account_id"}``
    SAP: ``{"company_code", "supplier_invoice", "fiscal_year",
        "payment_blocking_reason", "po_numbers", "material_doc_ids",
        "sap_status"}``"""

    def __post_init__(self) -> None:
        """Synthesize ``gmail_id`` for non-Gmail sources so existing
        keying logic at 80+ call sites continues to work without
        touching them.

        Resolution order:
          1. If ``gmail_id`` is already set → leave it (Gmail path,
             unchanged behaviour).
          2. Else if ``source_id`` is set → derive
             ``gmail_id = f"{source_type}-bill:{source_id}"`` for ERP
             channels, ``f"{source_type}:{source_id}"`` otherwise.
          3. Else leave ``gmail_id`` empty — caller must pass a key.
        """
        if not self.gmail_id and self.source_id:
            if self.source_type in ("netsuite", "sap_s4hana"):
                self.gmail_id = f"{self.source_type}-bill:{self.source_id}"
            else:
                self.gmail_id = f"{self.source_type}:{self.source_id}"
        # Mirror in the other direction so consumers can use either
        # field without checking which was populated first.
        if not self.source_id and self.gmail_id and self.source_type == "gmail":
            self.source_id = self.gmail_id
