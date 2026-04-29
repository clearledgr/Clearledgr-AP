# Accounts Payable Workflow Cycle — Industry Standard Reference

**Status:** Engineering reference document
**Audience:** Engineering, product, founding team
**Purpose:** Canonical description of the AP workflow as it exists in industry practice, used as the build reference for the agent backend and the dashboard's workflow engine.

---

## Scope and conventions

This document describes the standard procure-to-pay (P2P) accounts payable cycle as practiced in mid-market and enterprise finance organizations, drawing on the APQC Process Classification Framework (PCF category 9.5: "Manage accounts payable"), the Institute of Finance & Management (IOFM) AP standards, and the AP Association's process definitions.

The cycle has nine stages from invoice receipt to workflow close. Real-world variations are noted at each stage. The state model at the end of this document maps each stage to named states and transitions for the agent backend.

A few terminology conventions:

- **Invoice** = vendor's request for payment, regardless of format (PDF, EDI, paper, portal submission)
- **Bill** = the same artifact once it has been entered into the AP system; some ERPs use "vendor bill" instead of "invoice"
- **PO-driven** = the invoice references an existing purchase order
- **Non-PO** = the invoice has no PO; common for services, recurring expenses, contractor work
- **Three-way match** = matching invoice + purchase order + goods/services receipt
- **Two-way match** = matching invoice + purchase order only (used when goods receipt is not tracked separately, e.g., for many service invoices)
- **Approval routing** = determining which human(s) must authorize the invoice for payment
- **Posting** = creating the journal entry in the general ledger that recognizes the liability and/or expense

---

## Stage 1: Invoice receipt

### What happens

Vendors submit invoices through one or more channels:

- **Email**: by far the most common channel. Vendor sends an email to a designated AP address (e.g., `ap@company.com`, `invoices@company.com`) with the invoice as a PDF attachment, image, or occasionally inline in the email body
- **Vendor portal**: vendor uploads to a portal the customer operates (Coupa, Ariba, Tipalti, Bill.com Vendor Network, or the customer's own ERP-hosted portal)
- **EDI**: structured electronic submission, common in retail and manufacturing (EDI 810 invoice transaction set)
- **Paper**: physical mail, scanned by AP team or by a mailroom service
- **In-platform billing**: SaaS vendors send invoices via Stripe, Chargebee, or similar; the customer receives them through the vendor's billing system

The relative volume of these channels varies enormously by industry, company size, and vendor base. According to Basware's 2025 *Beyond the Checkbox* report (272 million invoices analyzed), 57% of invoices still arrive as PDFs or paper rather than structured electronic formats.

### Receipt capture

Upon arrival, the invoice must be captured into the AP system. This means:

1. The invoice file is stored
2. The arrival timestamp is recorded
3. The invoice is queued for processing
4. Initial metadata (sender, channel, file type) is logged

For audit purposes, the original invoice file must be retained immutably. Tampering with the original is a SOX violation in scope and an audit finding in any jurisdiction.

### Branching

- **Known channel + valid file**: proceed to Stage 2
- **Unknown sender / suspicious source**: route to security review; do not auto-process
- **Malformed file or unreadable image**: hold for human triage
- **Duplicate detection on receipt**: if the invoice file or invoice number matches a recent submission, flag as potential duplicate

### Common pain points

- Invoices that sit in shared inboxes for hours or days before anyone opens them
- Vendors emailing the wrong address (an employee's personal email instead of `ap@`)
- Multiple invoices in a single email attachment
- Invoices forwarded by employees with no AP routing context
- Vendor submission errors (wrong company entity, wrong subsidiary)

---

## Stage 2: Data extraction

### What happens

The invoice content is parsed into structured fields:

**Required fields:**
- Vendor name
- Vendor identifier (tax ID, registration number, or internal vendor code if matchable)
- Invoice number (vendor's reference)
- Invoice date
- Due date or payment terms
- Currency
- Subtotal, tax amount, total amount
- Bill-to entity (which legal entity within the customer is the addressee)

**Conditional fields:**
- Purchase order number (if PO-driven)
- Line items (if itemized)
- Remittance details (bank account, payment instructions)
- Tax breakdown (VAT, GST, sales tax by jurisdiction)
- Cost center or department references
- Project or job code

### Extraction methods

- **OCR (optical character recognition)**: machine reading of scanned or PDF invoices. Industry-standard accuracy on clean PDFs is approximately 85–95% per field; on scanned paper or low-quality images, accuracy drops significantly
- **EDI parsing**: structured electronic submissions are extracted directly from the EDI transaction; near-100% accuracy
- **LLM-based extraction**: large language models with vision capability extract fields with reasoning. Accuracy depends on model and prompt quality; field-level confidence scoring is supported
- **Manual entry**: a human re-keys fields. Highly accurate but slow

### Confidence handling

Extraction is probabilistic, not binary. Different fields may have different confidence levels on the same invoice. A robust system handles this in three ways:

1. **High confidence on all required fields**: advance to Stage 3 automatically
2. **Low confidence on specific fields**: surface only those fields for human review
3. **Low confidence on critical fields (vendor, amount, currency)**: hold the entire invoice for human verification before any further processing

Field-level confidence thresholds are typically configurable per customer and per field.

### Branching

- **All required fields high confidence**: proceed to Stage 3
- **Specific fields low confidence**: surface for human review, advance partial state
- **Vendor or amount low confidence**: hold for full human verification
- **Image too poor to extract reliably**: route for manual entry or rescan

### Common pain points

- Inconsistent invoice formats from the same vendor over time
- Vendors using non-standard terminology (e.g., "due upon receipt" with no specific date)
- Multi-line invoices where individual line totals don't sum to the stated total
- Currency ambiguity (e.g., "$" without specifying USD/CAD/AUD)
- Line item granularity (one invoice with 200 line items vs. one invoice with one line)

---

## Stage 3: Vendor matching and verification

### What happens

The extracted vendor information is matched against the customer's vendor master record, typically held in the ERP.

**Matching logic:**

The system attempts to match the extracted vendor on multiple attributes:

1. Tax identifier (VAT number, EIN, etc.) — strongest match
2. Registered company name (exact or fuzzy match)
3. Bank account / IBAN against historical payments
4. Address against vendor master
5. Email domain against vendor contact records

A successful match retrieves: payment terms, default GL coding, approval rules specific to this vendor, payment method preferences, currency, and any vendor-specific flags (preferred vendor, approved vendor only, etc.).

### Vendor verification

For new vendors (no match found): the system triggers vendor onboarding before the invoice can advance. Vendor onboarding typically requires:

- Tax identifier verification (VIES check for EU VAT, IRS TIN match for US, etc.)
- Bank account verification (penny test, named-account verification, or third-party verification service)
- Sanctions and compliance screening (OFAC for US, EU sanctions list, UN consolidated list)
- W-9, W-8BEN, or equivalent tax form on file (jurisdiction-dependent)
- Vendor record creation in the ERP

For existing vendors with new bank details: the system flags this for human verification. Bank detail changes are a primary fraud vector — fraudsters impersonate vendors and request bank changes. Verification typically requires out-of-band confirmation (phone call to a known vendor contact, signed letter on letterhead, etc.).

### Branching

- **Vendor matched, no flags**: proceed to Stage 4 (PO-driven) or Stage 5 (non-PO)
- **Vendor matched, new bank details**: hold for verification
- **Vendor matched, on hold or blocked list**: hold for resolution
- **Vendor not matched**: route to vendor onboarding; invoice cannot advance until vendor exists in the master record
- **Multiple vendor matches (duplicates in master)**: surface to human to disambiguate

### Common pain points

- Duplicate vendor records in the ERP (same vendor entered twice with different spellings or different bank accounts)
- Vendor name on invoice differs from registered name (DBA vs legal name)
- Vendor uses different bank accounts for different products or regions
- Multinational vendors with multiple legal entities, only one of which matches your records
- Vendor onboarding bottleneck: a new vendor takes days or weeks to set up, blocking the invoice

---

## Stage 4: Purchase order and goods receipt matching

### What happens (PO-driven path)

If the invoice references a purchase order, the system retrieves the PO and the associated goods receipt (GRN) and performs a three-way match:

| Document | Source |
|----------|--------|
| Purchase Order | Created by the buying department before the goods/services were ordered |
| Goods Received Note | Created by the receiving department or operations team when delivery was confirmed |
| Invoice | Submitted by the vendor after delivery |

A clean three-way match means:

- The vendor on the invoice matches the vendor on the PO
- The line items on the invoice match the line items on the GRN
- The unit prices on the invoice match the unit prices on the PO
- The quantities on the invoice are within tolerance of the quantities on the GRN
- The total on the invoice does not exceed the PO amount + tolerance

Tolerances are typically configured per customer, often as both a percentage (e.g., 2% on price variance) and an absolute amount (e.g., $50). Some customers use stricter tolerances for high-value invoices and looser ones for low-value.

### Two-way match

For service invoices and other non-physical-goods scenarios, customers often skip the GRN and perform a two-way match (invoice vs PO only). Whether two-way or three-way is required is typically determined by:

- The PO type (services PO vs goods PO)
- The customer's policy
- The amount threshold (lower-value invoices may waive GRN)

### Match exceptions

Common exception types:

- **Quantity variance**: invoice bills more units than were received
- **Price variance**: invoice price exceeds PO price beyond tolerance
- **Item mismatch**: invoice line item does not match any PO line item
- **Missing GRN**: goods were ordered but no receipt was logged
- **Partial delivery**: invoice covers items still in transit
- **PO closed**: the PO has already been fully invoiced
- **No PO found**: the PO number on the invoice does not exist in the system
- **Amended PO**: vendor references the original PO, but it has been amended (e.g., PO 4471 vs PO 4471-A)

Resolution typically requires input from the PO owner or the receiving department. The buying department may need to amend the PO; the receiving department may need to log a missed GRN; the AP team may need to reject or hold the invoice.

### Non-PO path

If the invoice has no PO reference, Stage 4 is skipped. The invoice advances directly to Stage 5 (GL coding) and then to Stage 6 (approval routing). However:

- Many companies still apply some matching to non-PO invoices, such as matching against vendor history, expected ranges, or contracts on file
- Non-PO invoices often require additional approval scrutiny because there is no pre-authorized PO to reference
- Some companies maintain a "non-PO blanket approval" for recurring vendors (utilities, rent, software subscriptions) to reduce friction

### Branching

- **Clean three-way or two-way match**: proceed to Stage 5
- **Match exception**: hold in `match_exception` state, route to PO owner or relevant department
- **No PO referenced**: skip to Stage 5 (non-PO path)
- **PO closed or invalid**: route to buying department for resolution

### Common pain points

- PO owners on PTO when their input is needed
- Receiving department not logging GRNs in real time, causing invoices to arrive before goods receipts
- Vendor invoices arriving before the goods are physically received
- Amended POs not propagating to AP visibility
- Currency mismatches between PO (in customer's functional currency) and invoice (in vendor's currency)
- Tax handling differences between PO line items and invoice line items

---

## Stage 5: General ledger coding

### What happens

The expense represented by the invoice must be coded to the correct general ledger accounts. This determines how the spend rolls up into financial reporting.

**Required coding dimensions:**

- GL account (e.g., 6210 — Cloud Infrastructure)
- Cost center, department, or business unit
- Legal entity (if multi-entity)
- Project or job code (if project-tracked)
- Tax treatment (deductible, non-deductible, capitalized, expensed)

For PO-driven invoices, GL coding is typically inherited from the PO. The PO was coded when it was created, and the invoice inherits that coding.

For non-PO invoices, GL coding must be applied at this stage. Options:

- **Vendor default**: the vendor master holds a default GL account, applied automatically
- **Pattern matching**: match against historical coding for similar invoices from the same vendor
- **Rule-based**: apply rules based on vendor, amount, line item description, or other attributes
- **Human assignment**: a controller, accountant, or department head manually assigns codes

### Multi-line and multi-cost-center invoices

A single invoice may need to be split across multiple GL accounts or cost centers. Examples:

- A team offsite invoice that spans engineering and product cost centers
- A SaaS contract that covers multiple departments
- A consulting engagement that splits across two projects

The system must support line-level coding (each line item gets its own coding) and split coding (a single line item is allocated across multiple cost centers).

### Tax handling

Tax (VAT, GST, sales tax) requires its own coding. Common scenarios:

- Standard rate (e.g., 20% UK VAT)
- Reduced rate (e.g., 5% UK VAT on energy)
- Zero rate or exempt
- Reverse charge (intra-EU services)
- Use tax (US, when sales tax wasn't collected at purchase)
- Cross-border with multiple jurisdictions

### Branching

- **Coding fully determined (PO-driven or auto-coded)**: proceed to Stage 6
- **Coding ambiguous**: surface to human for assignment
- **Multi-line / split coding required**: surface to human for allocation, with system suggestions

### Common pain points

- Inconsistent coding for the same vendor across periods (one month coded to "Software," next month to "IT Services")
- Incorrect cost center allocation that pollutes department-level financial reports
- Tax coding errors that surface only at month-end or at audit
- Capital vs. expense decisions on borderline items (when does software become a capitalizable asset?)
- Cross-border invoices with complex tax treatment

---

## Stage 6: Approval routing

### What happens

Approved by who, in what order, and within what timeframe is determined by the customer's approval policy.

### Common approval policies

- **Threshold-based**: amounts under $X auto-approved or routed to the manager; amounts above $X routed to the director, VP, or CFO
- **Hierarchical**: each invoice must be approved by the requester's manager, then upward to the appropriate level for the amount
- **Department-based**: invoices coded to a specific department route to that department's leader
- **Vendor-based**: certain vendors have specific approvers (e.g., legal services route to General Counsel)
- **Project-based**: project invoices route to the project owner regardless of amount
- **Dual approval**: above a threshold, two approvers required (e.g., department head + finance)
- **Segregation of duties**: the requester and approver must be different people; the approver and the AP processor must be different people

### Approval mechanism

Approvers receive notification through one or more channels:

- Email with a link to approve/reject
- ERP-native workflow (NetSuite Approval Routing, SAP SRM, Workday)
- Slack or Teams notification with inline approval action
- Mobile app push notification
- Custom dashboard

Approval typically requires:

- Ability to view the invoice, PO, GRN, and any supporting documentation
- Ability to view the proposed GL coding
- Ability to add comments or request changes
- Ability to delegate to another approver (if OOO)
- Ability to reject with a reason

### SLA and escalation

Most companies set internal SLAs for approval turnaround (e.g., 3 business days for amounts under $10K, 5 business days above). When SLAs are exceeded:

- The invoice is escalated to the approver's manager
- The AP team or invoice requester is notified
- For time-sensitive payments (early-pay discount, due date approaching), additional alerts fire

### Branching

- **Approved by all required approvers**: proceed to Stage 7
- **Rejected**: route back to invoice requester or AP team for resolution
- **Approver out of office**: route to delegate or backup approver
- **Approval SLA exceeded**: escalate
- **Information requested**: pause workflow, route to AP team to gather info, then re-route to approver

### Common pain points

This stage is consistently identified by industry research (APQC, Ardent Partners, IOFM benchmarks) as the dominant source of cycle time in AP. Common issues:

- Approvers ignore email notifications
- Approvers travel or take PTO without setting up delegation
- Approvers want context that isn't included in the notification (e.g., "who requested this?", "what project is this for?")
- Routing rules don't handle edge cases (acting managers, recently restructured org charts)
- Mobile approval is unreliable; some systems require the approver to log in to a desktop ERP
- Vendor chases come during approval delays, adding pressure on the AP team

---

## Stage 7: Payment scheduling and execution preparation

### What happens

Once an invoice is approved, it enters the ready-to-pay queue. The system prepares the payment but does not necessarily execute it immediately.

### Payment timing decisions

- **Pay on due date**: maximize float by paying as late as possible without missing the due date
- **Pay early for discount**: if the vendor offers early-payment discount terms (e.g., 2/10 net 30 — 2% discount if paid within 10 days), evaluate whether the discount exceeds the cost of capital
- **Pay in batch**: aggregate approved invoices into weekly or biweekly payment runs to reduce transaction costs
- **Pay immediately**: for time-sensitive payments (utilities at risk of disconnection, vendors with short terms)

### Payment method selection

- **ACH (US) / SEPA (EU) / equivalent domestic transfer**: low cost, standard for domestic vendor payments
- **Wire transfer**: higher cost, faster, used for high-value or cross-border payments
- **Check**: still common in some industries, especially for non-bank-enabled vendors
- **Card payment**: some vendors accept, others don't; can earn rewards but vendor pays interchange
- **Vendor-specific payment networks**: Bill.com Network, Tipalti, AvidXchange — the vendor and customer both use the same network, payment routes through that network
- **Cross-border payments**: SWIFT, FX-conversion services (Wise, Revolut Business), or specialized multi-currency providers

### Approval-to-payment workflow

- The approved invoice is added to the payment queue
- The AP team reviews the queue (often weekly) for the next payment run
- The team selects which invoices to include in the run
- A pre-payment review confirms vendor details, amount, and payment method
- The payment file is generated (NACHA file for ACH, SEPA XML for SEPA, etc.)
- The file is uploaded to the bank or payment platform, or the payment is initiated through the platform's UI
- Some companies require dual control: one person prepares the payment, another releases it

### Branching

- **Standard scheduling**: invoice waits in queue until next scheduled payment run
- **Discount eligible**: if early-pay discount available and economically favorable, schedule for early payment
- **Urgent**: bypass the normal queue (with appropriate approval)
- **Hold**: payment paused for cash flow, dispute resolution, or other reasons

### Common pain points

- Cash flow constraints that delay otherwise-approved payments
- Payment file errors that bounce at the bank (wrong routing number, wrong account format)
- Vendor disputes that emerge after approval but before payment
- FX timing on cross-border payments
- Payment runs that miss vendors due to file errors

---

## Stage 8: Payment execution and ERP posting

### What happens

The payment is executed via the chosen rail, and the corresponding journal entry posts to the general ledger.

### Payment execution

- The payment file is processed by the bank or payment platform
- Funds debit from the customer's bank account
- Funds credit (eventually) to the vendor's bank account
- Settlement timing varies by rail: ACH typically 1–3 business days, SEPA same-day to next-day, wire same-day, cross-border 1–5 days

### Confirmation and reconciliation

- The bank or payment platform sends back a confirmation: payment ID, settlement timestamp, amount, status
- The AP system records the confirmation against the original invoice workflow
- Bank reconciliation (typically a separate workflow at month-end) matches the payment debit to the vendor disbursement

### ERP posting

The journal entry recognizing the payment posts to the GL:

```
Debit: Accounts Payable (vendor liability cleared)
Credit: Cash (or specific bank account)
```

If the expense was not recognized at invoice receipt (some companies recognize at payment instead), the expense entry posts here:

```
Debit: Expense Account (per GL coding)
Credit: Cash
```

Most modern AP processes recognize the expense at invoice receipt (matching principle, accrual basis), with the payment entry simply clearing the liability.

### Failed payments

Payments can fail for a variety of reasons:

- Insufficient funds in the customer's account
- Incorrect vendor banking details
- Bank rejecting the payment for compliance reasons
- Vendor account closed or frozen
- Cross-border regulatory issues

Failed payments require re-routing: investigate the cause, correct the underlying issue, and re-attempt the payment.

### Branching

- **Payment successful, posted to ERP**: proceed to Stage 9
- **Payment failed**: hold for investigation, return to Stage 7 once resolved
- **Payment partial**: vendor received less than full amount; investigate (likely FX or fee deduction); resolve with vendor

### Common pain points

- Payment files that bounce at the bank for technical reasons
- Vendor banking details that have silently changed (without the vendor notifying)
- FX rate differences between booking and execution
- Bank fees deducted from the payment (vendor receives less than billed)
- Compliance holds (sanctions screening, AML reviews)

---

## Stage 9: Vendor communication and workflow close

### What happens

Once the payment is executed and the journal entry posted, the workflow moves to close. Several things may still happen:

### Vendor remittance advice

The customer sends the vendor a remittance advice — a notification of payment with details: which invoices were paid, the amount of each, the payment method, the payment reference. This helps the vendor reconcile their accounts receivable. Many vendors require remittance advice as part of their standard process.

### Vendor inquiries

Vendors may contact the customer's AP team about:

- "Where is my payment?" (chasing a payment that has been made but not received or not reconciled on the vendor side)
- "I received a partial payment" (FX or fee deduction)
- "I received an unexpected payment" (incorrect vendor or duplicate)
- "I have a question about an old invoice"

These inquiries require the AP team to look up the workflow history and respond. Well-run AP teams treat these as part of the standard workflow; less mature teams handle them as ad hoc inbox queries.

### Workflow close

The workflow is marked complete when:

- Payment has been executed and confirmed
- ERP posting has been verified
- Any vendor inquiries have been resolved
- The full audit trail is complete and immutable

After close, the workflow is read-only. The audit trail is retained per the customer's retention policy (typically 7 years for tax purposes in most jurisdictions; longer for regulated industries).

### Disputes and re-opens

A closed workflow may need to re-open if:

- Vendor disputes the payment amount or completeness
- An audit finding identifies an error
- A duplicate payment is discovered
- A clawback is needed (vendor was overpaid)

Re-opening is a controlled action — it requires audit-trail justification, and the original close state is preserved.

### Branching

- **Clean close, no inquiries**: workflow archived
- **Vendor inquiry resolved**: workflow archived after resolution
- **Dispute or re-open**: workflow returns to an earlier stage based on the issue

### Common pain points

- Vendors not reconciling on their side, leading to repeated chases for already-paid invoices
- Inquiries from vendors about old invoices that require digging through historical records
- Remittance advice not being sent or being sent in a format the vendor can't process
- Disputes that surface long after payment, requiring re-investigation

---

## State model

Below is the state model for a single invoice workflow, suitable for engineering implementation as a finite state machine. Each state is named, each transition has a trigger, and each transition records an audit-trail entry.

### States

| State | Description | Stage |
|-------|-------------|-------|
| `received` | Invoice received, not yet processed | 1 |
| `extraction_in_progress` | Extraction running | 2 |
| `extraction_failed` | Extraction unable to complete; held for human triage | 2 |
| `extracted_pending_review` | Extracted with low confidence on one or more fields | 2 |
| `extracted` | Extraction complete with high confidence | 2 |
| `vendor_matching` | Matching against vendor master | 3 |
| `vendor_unmatched` | Vendor not found in master; pending onboarding | 3 |
| `vendor_unverified` | Existing vendor with new bank details; pending verification | 3 |
| `vendor_blocked` | Vendor on hold or blocked list | 3 |
| `vendor_matched` | Vendor confirmed and ready | 3 |
| `match_in_progress` | Performing PO and GRN match | 4 |
| `match_exception` | Match failed; held for resolution | 4 |
| `match_complete` | Three-way or two-way match successful | 4 |
| `non_po_pending` | Non-PO invoice awaiting GL coding | 5 |
| `gl_coding_in_progress` | Auto-coding running | 5 |
| `gl_coding_pending_review` | Coding ambiguous; held for human assignment | 5 |
| `gl_coded` | Coding complete | 5 |
| `awaiting_approval` | In approval queue with one or more approvers | 6 |
| `approval_escalated` | SLA exceeded; escalated to next level | 6 |
| `approval_rejected` | Rejected by an approver; returned for revision | 6 |
| `approved` | All required approvals received | 6 |
| `payment_scheduled` | In payment queue | 7 |
| `payment_held` | Payment paused for cash flow, dispute, or other reason | 7 |
| `payment_in_flight` | Payment file submitted to bank or payment platform | 8 |
| `payment_failed` | Payment did not execute; held for investigation | 8 |
| `payment_executed` | Payment confirmed by bank | 8 |
| `posted` | Journal entry posted to ERP; vendor liability cleared | 8 |
| `awaiting_remittance` | Payment executed; remittance advice pending | 9 |
| `vendor_inquiry_open` | Vendor has raised an inquiry | 9 |
| `disputed` | Active dispute; workflow re-opened | 9 |
| `complete` | Workflow closed, audit trail finalized | 9 |
| `archived` | Past retention review point; read-only | 9 |

### Key transitions

Each transition records:

1. The from-state and to-state
2. The trigger (agent action, human action, system event, time-based)
3. The actor (user ID, agent ID, or system process)
4. The timestamp
5. Any input data (extracted fields, approval comments, override reasons)
6. The reason if the transition is exceptional (override, escalation, manual intervention)

The audit trail must be append-only and tamper-evident (hash-chained), as specified in the dashboard scope document.

### Concurrency notes

A single invoice is handled by one workflow instance, but multiple workflow instances run concurrently across the system. State transitions are atomic per instance. No cross-instance coordination is required at the state level; coordination across invoices (e.g., vendor-level patterns, batch payment runs) happens at higher abstractions.

Race conditions to handle:

- Approver clicks approve and reject within the same second from different surfaces (Gmail, Slack, dashboard) — handle with optimistic locking and a clear "first wins"
- Payment file already submitted but the customer attempts to hold the invoice — payment cannot be retracted at the file submission point; rolling back requires bank cooperation
- Vendor master record updated mid-workflow — re-validate the vendor match if material attributes (bank account, tax ID, status) changed

---

## Branching summary

The full cycle has multiple paths through it, depending on the invoice attributes and customer policy:

**Path A: Clean PO-driven invoice from a known vendor**
Receipt → extract → vendor matched → 3-way match clean → GL coded (PO-inherited) → approved within SLA → payment scheduled → executed → posted → complete.

**Path B: Non-PO invoice from a known vendor**
Receipt → extract → vendor matched → skip Stage 4 → GL coded (rule-based or human-assigned) → approved → payment scheduled → executed → posted → complete.

**Path C: Invoice with match exception**
Receipt → extract → vendor matched → match exception → held → resolved by PO owner or AP team → match complete → GL coded → approved → payment → posted → complete.

**Path D: New vendor**
Receipt → extract → vendor unmatched → vendor onboarding → vendor matched → continue normal flow.

**Path E: Bank detail change (potential fraud)**
Receipt → extract → vendor unverified → human verification (out-of-band) → vendor matched → continue normal flow.

**Path F: Approval escalation**
Receipt → ... → awaiting approval → SLA exceeded → escalated → approved (or rejected by escalation level).

**Path G: Payment failure**
Receipt → ... → payment in flight → payment failed → investigate → re-schedule → payment executed → posted → complete.

**Path H: Post-payment dispute**
Complete → disputed → re-investigation → re-opened to relevant stage → re-resolved → complete.

---

## Industry data and benchmarks

Where industry data exists for cycle time, accuracy, and exception rates, it is included below. Where data does not exist or varies significantly by company, that is noted.

### Cycle time

- The widely cited industry estimate for end-to-end AP cycle time (invoice receipt to payment) ranges from 8 to 25 business days for organizations without significant automation. APQC cross-industry benchmark data (publicly available subsets) places median cycle time around 10–14 days for mid-market and around 7–10 days for top-quartile performers.
- Approval routing (Stage 6) is consistently identified as the dominant source of cycle time in research from APQC, Ardent Partners, and Levvel Research. Approval delays typically account for 40–60% of total cycle time.
- AP automation (OCR, three-way match automation, electronic approval) typically reduces cycle time by 30–60% according to vendor case studies; magnitude depends heavily on starting maturity.

### Exception rates

- Industry reports from IOFM and Ardent Partners suggest that 20–40% of invoices encounter at least one exception requiring human intervention, with the majority being match exceptions (Stage 4) or coding ambiguities (Stage 5). The exact rate varies enormously by industry, vendor base, and PO discipline.

### Volume distribution

- The PO-driven vs non-PO ratio varies widely by company type. Manufacturers and distributors are typically 80%+ PO-driven. Service companies and SaaS companies are often 30–50% PO-driven. There is no single industry-standard ratio.

### Cost per invoice

- Industry benchmarks for the fully loaded cost of processing a single invoice (labor, technology, overhead) range from approximately $5 to $25 depending on automation level and company size. Top-quartile performers in APQC benchmarks operate at $3–$5 per invoice; bottom-quartile at $15–$25.

---

## Notes for implementation

Some practical guidance for engineering teams building against this reference:

1. **Workflow state must be the source of truth.** All surfaces (dashboard, ERP-native UI, Gmail, Slack) read from and write to the same workflow state. No surface holds its own state.

2. **State transitions are events, not just status updates.** Every transition is a discrete event that publishes to the audit log, may trigger notifications, and may trigger downstream agent actions. Treat the workflow engine as event-driven.

3. **Idempotency at every state transition.** Network retries, double-clicks, and race conditions are inevitable. Each state transition must be idempotent — re-applying the same transition with the same inputs produces the same result.

4. **Confidence scores propagate.** Extraction confidence (Stage 2) should be retained on the workflow record and visible at later stages. An approver should know whether the GL code was auto-applied with 95% confidence or human-assigned.

5. **Human-in-the-loop is a first-class concept, not an exception.** Most invoices in early stages will involve human review somewhere. Build the UI and workflow engine assuming humans are part of the loop, not an unexpected interruption.

6. **The agent is one actor among many.** The agent advances workflows where rules permit, but humans (clerks, approvers, leaders) and external systems (ERP webhooks, bank confirmations) also drive transitions. Don't conflate "agent" with "system."

7. **Audit is non-negotiable.** Every state transition must be recorded with full context. Rebuilding the workflow history from logs is required for SOC 2, audit findings, and customer trust.

---

## Sources and further reading

- APQC Process Classification Framework (PCF), category 9.5: "Manage accounts payable"
- Institute of Finance & Management (IOFM) AP standards and benchmarks
- Ardent Partners *State of ePayables* annual reports
- Levvel Research *Payables Insight* reports
- Basware *Beyond the Checkbox* 2025 report
- Hackett Group AP benchmark studies
- Gartner research on accounts payable automation
- ISO 20022 financial messaging standards (for cross-border payment context)
- AICPA AP audit guidance

Engineering teams should consult these sources for deeper detail on specific aspects (audit requirements, payment file formats, tax handling, jurisdiction-specific compliance).

---

**Document maintained by:** Mo Mbalam (CEO), Suleiman Mohammed (CTO)
**Last reviewed:** April 2026
**Next review:** when AR workflow ships (Q4 2026)
