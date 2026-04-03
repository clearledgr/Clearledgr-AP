# Product Gaps — Clearledgr v1

Date: 2026-04-03
Source: Codebase audit + competitive analysis (Streak, BILL, Stampli)
Total items: 33 (22 missing, 8 partial, 3 infrastructure)

---

## Priority Tiers

### P0 — Blocks pilot (Cowrywise)
- #1 Line item extraction and storage
- #18 Multi-entity posting (Africa vs US entities in one ERP)
- #23 Multi-entity within one org (inbox routing to different entities)

### P1 — Improves pilot quality
- #11 ERP sync monitoring (verify posts landed in NetSuite)
- #4 Payment terms extraction
- #26 Audit trail export (CSV for auditors)
- #19 Bill line items posting for NetSuite/SAP
- #6 Multi-invoice email handling

### P2 — Blocks enterprise sales
- #13 Read chart of accounts from ERP
- #16 Sync vendor master data from ERP
- #22 Outlook/M365 support
- #27 SSO/SAML implementation
- #29 Database migrations (Alembic)
- #30 Monitoring/alerting integration (Sentry/PagerDuty)
- #28 SOC 2 certification

### P3 — Post-pilot product expansion
- Everything else

---

## A. Email Extraction & Parsing

### 1. [MISSING] Line item extraction and storage
**Priority:** P0
**What's missing:** InvoiceData has no `line_items` field. Claude extracts line items in raw text but they're not parsed into structured data, stored in DB, validated, or passed to ERP posting.
**What's needed:**
- Add `line_items: Optional[List[Dict]]` to InvoiceData (each item: description, quantity, unit_price, amount, gl_code, tax)
- Update LLM extraction prompt to return structured line items
- Store line items in AP item metadata
- Pass line items to ERP posting functions
- Display line items in Gmail sidebar and approval cards
**Impact:** Without this, multi-line invoices are posted as a single amount. GL coding is per-invoice, not per-line. Cowrywise likely has multi-line invoices.
**Estimated effort:** 3-5 days

### 2. [MISSING] Tax amount extraction
**Priority:** P1
**What's missing:** Tax detection exists in reflection layer (checks if line_sum + tax = total) but no stored field. Can't distinguish tax-inclusive vs tax-exclusive at data level.
**What's needed:**
- Add `tax_amount: Optional[float]`, `tax_rate: Optional[float]`, `tax_type: Optional[str]` to InvoiceData
- Update extraction prompt
- Display in sidebar and approval cards
- Handle tax correctly when posting to ERP (some ERPs need tax as separate line)
**Estimated effort:** 2-3 days

### 3. [PARTIAL] Discount detection
**Priority:** P3
**What exists:** Comment in InvoiceData says "discount detection" but no field.
**What's needed:**
- Add `discount_amount: Optional[float]`, `discount_terms: Optional[str]` (e.g., "2/10 NET 30")
- Extract from invoice text
- Factor into amount validation (discount may explain amount mismatch)
**Estimated effort:** 1-2 days

### 4. [MISSING] Payment terms extraction
**Priority:** P1
**What's missing:** Vendor profile has `payment_terms` but invoices don't extract terms from the document itself.
**What's needed:**
- Add `payment_terms: Optional[str]` to InvoiceData
- Update extraction prompt to look for "NET 30", "Due on receipt", "2/10 NET 30", etc.
- Compare extracted terms against vendor profile terms
- Flag discrepancy if terms change unexpectedly
**Estimated effort:** 1-2 days

### 5. [MISSING] Bank/payment details extraction
**Priority:** P2
**What's missing:** No IBAN, SWIFT, routing number, bank name fields. Can't detect bank detail changes from invoice content.
**What's needed:**
- Add `bank_details: Optional[Dict]` to InvoiceData (bank_name, account_number, routing_number, iban, swift, sort_code)
- Extract from invoice text (usually in footer/payment section)
- Compare against stored vendor bank details
- Flag changes as fraud signal (high-severity validation warning)
**Estimated effort:** 2-3 days

### 6. [MISSING] Multi-invoice email handling
**Priority:** P1
**What's missing:** One email = one AP item. If a vendor sends one email with 3 invoice PDFs attached, only one AP item is created.
**What's needed:**
- Detect multiple invoice attachments per email
- Create one AP item per invoice (not per email)
- Link all AP items back to the same source email/thread
- Handle the case where one PDF contains multiple invoices (page-based splitting)
**Estimated effort:** 3-5 days

### 7. [MISSING] Email attachment archive handling
**Priority:** P3
**What's missing:** ZIP/RAR archives containing invoices are not unpacked.
**What's needed:**
- Detect ZIP/RAR attachments
- Extract contents
- Process each contained file as a potential invoice
**Estimated effort:** 1-2 days

---

## B. AI Agent Capabilities

### 8. [MISSING] Payment scheduling agent
**Priority:** P3
**What's missing:** No tool to schedule or trigger payments after invoice is posted to ERP. Finance still does this manually.
**What's needed:**
- New agent tool: `schedule_payment` — creates a payment record linked to the posted AP item
- Integration with ERP payment runs (batch payments)
- Payment status tracking (scheduled, processing, completed, failed)
- Slack notification when payment is due or completed
**Estimated effort:** 5-7 days (depends on ERP payment APIs)

### 9. [PARTIAL] Vendor communication agent
**Priority:** P2
**What exists:** Creates Gmail drafts for missing info. Can't send. No response tracking.
**What's missing:**
- Gmail send capability (requires `gmail.send` OAuth scope upgrade)
- Response detection: when vendor replies to the follow-up, link the response to the AP item
- Follow-up escalation: if vendor doesn't reply in X days, re-send or escalate
- Template library for common vendor communications
**Estimated effort:** 3-5 days

### 10. [MISSING] Exception resolution agent
**Priority:** P2
**What's missing:** No tool to auto-resolve common exceptions.
**What's needed:**
- New agent tool: `resolve_exception`
- Auto-resolution strategies:
  - Missing PO: search ERP for matching PO by vendor+amount, auto-attach if found
  - Wrong amount: calculate specific discrepancy, suggest correction
  - Vendor mismatch: suggest correct vendor from known aliases
  - Missing approval: identify correct approver from rules, auto-route
**Estimated effort:** 3-5 days

### 11. [MISSING] ERP sync monitoring agent
**Priority:** P1
**What's missing:** After posting, no verification that the invoice actually landed in the ERP.
**What's needed:**
- New agent tool: `verify_erp_sync` — calls ERP to confirm bill exists with matching reference
- Periodic background check for recently posted invoices (last 24h)
- Flag discrepancies (posted in Clearledgr but missing in ERP)
- Auto-retry if ERP confirms the bill was rejected after initial acceptance
**Estimated effort:** 2-3 days

### 12. [MISSING] Spend analysis agent
**Priority:** P3
**What's missing:** No tool to analyze spend patterns at org level.
**What's needed:**
- New agent tool: `analyze_spending`
- Capabilities: top vendors by spend, spend by GL category, month-over-month trends, budget utilization, anomaly detection at portfolio level
- Output as structured data for dashboard display
**Estimated effort:** 3-5 days

---

## C. ERP Read & Write

### 13. [MISSING] Read chart of accounts from ERP
**Priority:** P2
**What's missing:** Can validate individual GL codes but can't pull the full chart of accounts.
**What's needed:**
- `get_chart_of_accounts(organization_id)` function for each ERP
- QB: GET /v3/company/{realm}/query?query=SELECT * FROM Account
- Xero: GET /api.xro/2.0/Accounts
- NetSuite: SuiteQL SELECT * FROM account
- SAP: GET /b1s/v1/ChartOfAccounts
- Cache locally with periodic refresh
- Use for GL mapping configuration UI
**Estimated effort:** 2-3 days

### 14. [MISSING] Read open AP aging report
**Priority:** P3
**What's missing:** No aging buckets (0-30, 31-60, 61-90, 90+ days).
**What's needed:**
- `get_ap_aging(organization_id)` function
- Compute aging from posted AP items by due date
- Display in workspace dashboard
**Estimated effort:** 1-2 days

### 15. [PARTIAL] Read full vendor list from ERP
**Priority:** P2
**What exists:** Can search vendors by name. Can't list all or paginate.
**What's needed:**
- `list_all_vendors(organization_id, page_token)` with pagination for each ERP
- Used for initial vendor sync and vendor directory population
**Estimated effort:** 2-3 days

### 16. [MISSING] Sync vendor master data from ERP
**Priority:** P2
**What's missing:** No periodic sync of vendor records from ERP.
**What's needed:**
- Background job that runs daily/weekly
- Pulls vendor records (name, address, payment terms, tax ID, bank details) from ERP
- Updates Clearledgr vendor profiles
- Detects changes (new vendors, updated bank details, deactivated vendors)
**Estimated effort:** 3-5 days

### 17. [MISSING] Read payment status from ERP
**Priority:** P3
**What's missing:** After bill is posted, can't check if payment was actually made.
**What's needed:**
- `get_payment_status(organization_id, bill_reference)` for each ERP
- Track payment lifecycle: scheduled → processing → completed → failed
**Estimated effort:** 2-3 days

### 18. [MISSING] Multi-entity posting
**Priority:** P0
**What's missing:** Can post to one ERP connection per org. Can't route invoices to different subsidiaries/entities within one ERP instance.
**What's needed:**
- `entity_id` field on ERP connection (which subsidiary/company)
- Multiple ERP connections per org for the same ERP type but different entities
- Entity routing rules: vendor X → Entity A, vendor Y → Entity B
- Entity selection during approval
- SAP: company_code routing. NetSuite: subsidiary routing. QB: separate realm per entity.
**Estimated effort:** 5-7 days

### 19. [PARTIAL] Bill line items posting for NetSuite/SAP
**Priority:** P1
**What exists:** QuickBooks and Xero post line items correctly. NetSuite and SAP post as single amount.
**What's needed:**
- NetSuite: POST vendor bill with `expense` or `item` line array
- SAP: POST PurchaseInvoices with `DocumentLines` array
- Map each line item to a GL account
- Handle tax per line item
**Estimated effort:** 2-3 days (depends on #1 line item extraction)

---

## D. Platform Capabilities

### 20. [MISSING] Payment execution
**Priority:** P3
**What's missing:** No payment triggering after approval. Clearledgr posts the bill but finance still triggers payment in ERP.
**What's needed:**
- Payment run integration (batch payments via ERP API)
- Payment method selection (ACH, wire, check)
- Payment approval workflow (separate from invoice approval)
**Estimated effort:** 7-10 days

### 21. [MISSING] Exportable reports (PDF/CSV)
**Priority:** P2
**What's missing:** Metrics exist but no export. Controllers can't pull AP reports for month-end.
**What's needed:**
- `/api/reports/export` endpoint with format parameter (csv, pdf, json)
- Report types: AP aging, vendor spend, approval velocity, posting status, audit trail
- Date range filtering
- Download link generation
**Estimated effort:** 3-5 days

### 22. [MISSING] Outlook/M365 support
**Priority:** P2
**What's missing:** Gmail only. Enterprise buyers on Microsoft stack are blocked.
**What's needed:**
- Microsoft Graph API integration for email reading
- Outlook add-in (sidebar equivalent)
- OAuth flow for M365
- Email polling/push via Microsoft Graph subscriptions
- Env vars exist (`MICROSOFT_CLIENT_ID`, etc.) but no processing implementation
**Estimated effort:** 10-15 days (significant new surface)

### 23. [MISSING] Multi-entity within one org
**Priority:** P0
**What's missing:** Org-level isolation only. No subsidiary/division routing from one inbox.
**What's needed:**
- `entities` table (id, org_id, name, erp_connection_id, gl_mapping, approval_rules)
- Entity detection from invoice (by vendor, GL code, cost center, or explicit rules)
- Entity selection in sidebar
- Entity-specific approval chains
- Entity-specific ERP posting (links to #18)
**Estimated effort:** 5-7 days

### 24. [MISSING] Outgoing webhooks
**Priority:** P3
**What's missing:** No webhook system to notify external systems of AP events.
**What's needed:**
- Webhook registration API (URL, events, secret)
- Event types: invoice.received, invoice.approved, invoice.posted, invoice.rejected
- Delivery with retry (use existing notification retry queue)
- HMAC signature for security
**Estimated effort:** 3-5 days

### 25. [MISSING] Mobile app or mobile-optimized view
**Priority:** P3
**What's missing:** Workspace console is desktop-only. Slack mobile works for approvals.
**What's needed:**
- Responsive workspace CSS (minimum viable)
- OR dedicated mobile app (React Native or Flutter)
**Estimated effort:** 3-5 days (responsive) or 15-20 days (native app)

### 26. [MISSING] Audit trail export
**Priority:** P1
**What's missing:** Audit trail exists in DB but no export endpoint.
**What's needed:**
- `/api/ap/audit/export` endpoint
- Formats: CSV, JSON, PDF
- Filters: date range, vendor, state, actor
- Include all 22 event types with full detail
**Estimated effort:** 2-3 days

### 27. [MISSING] SSO/SAML implementation
**Priority:** P2
**What's missing:** Gated in Enterprise tier but not implemented.
**What's needed:**
- SAML 2.0 SP implementation (python3-saml or similar)
- SSO configuration UI in workspace admin
- Identity provider metadata upload
- Just-in-time user provisioning from SAML assertion
- Session management tied to SAML session
**Estimated effort:** 5-7 days

---

## E. Security & Infrastructure

### 28. [MISSING] SOC 2 certification
**Priority:** P2
**What's missing:** Controls exist but no formal audit.
**What's needed:**
- Engage SOC 2 auditor (Vanta, Drata, or manual)
- Document controls (encryption, access, audit trail, change management)
- Remediate gaps identified by auditor
- Complete Type II audit (12-month observation period)
**Estimated effort:** 3-6 months (not code work, process work)

### 29. [MISSING] Database migrations
**Priority:** P2
**What's missing:** Tables created lazily. No migration framework.
**What's needed:**
- Integrate Alembic (SQLAlchemy migration tool) or a lightweight alternative
- Generate initial migration from current schema
- Migration on startup or deploy
- Rollback capability
**Estimated effort:** 3-5 days

### 30. [MISSING] Monitoring/alerting integration
**Priority:** P2
**What's missing:** Critical errors log to stdout but no external alerting.
**What's needed:**
- Sentry integration for error tracking
- PagerDuty or Slack webhook for critical alerts (dead letter, posting failures, auth failures)
- Health check dashboard (Datadog or similar)
- Uptime monitoring
**Estimated effort:** 2-3 days

---

## F. Data Quality

### 31. [PARTIAL] Non-English invoice handling
**Priority:** P3
**What exists:** Claude supports many languages. Extraction prompts are English-only.
**What's needed:**
- Language detection on incoming emails
- Localized extraction prompts (or explicit "extract regardless of language" instruction)
- Field mapping for common non-English labels (Facture, Montant, Fälligkeitsdatum, etc.)
**Estimated effort:** 2-3 days

### 32. [MISSING] Duplicate vendor consolidation
**Priority:** P2
**What's missing:** "Acme Corp", "Acme Corporation", "ACME" create 3 separate vendor profiles.
**What's needed:**
- Fuzzy vendor name matching during profile creation
- Merge UI in workspace admin
- Alias management (one canonical vendor, multiple known names)
- Auto-suggest merges based on similarity scoring
**Estimated effort:** 3-5 days

### 33. [PARTIAL] Historical data import
**Priority:** P3
**What exists:** `/extension/repair-historical-invoices` reprocesses Gmail emails.
**What's needed:**
- Bulk CSV/Excel import endpoint for existing AP data
- Field mapping UI (map CSV columns to Clearledgr fields)
- Validation and dedup during import
- Import from legacy AP systems
**Estimated effort:** 3-5 days

---

## Summary

| Priority | Items | Total effort estimate |
|----------|-------|---------------------|
| P0 (blocks pilot) | #1, #18, #23 | 13-19 days |
| P1 (improves pilot) | #2, #4, #6, #11, #19, #26 | 11-17 days |
| P2 (blocks enterprise) | #5, #9, #10, #13, #15, #16, #21, #22, #27, #28, #29, #30, #32 | 44-68 days |
| P3 (post-pilot) | #3, #7, #8, #12, #14, #17, #20, #24, #25, #31, #33 | 30-50 days |
| **Total** | **33 items** | **98-154 days** |

---

## Implementation Order

### Sprint 1: Pilot blockers (P0) — ~2 weeks
1. #23 Multi-entity within one org (entity table, routing rules)
2. #18 Multi-entity posting (entity-specific ERP connections)
3. #1 Line item extraction and storage

### Sprint 2: Pilot quality (P1) — ~2 weeks
4. #19 Bill line items posting for NetSuite/SAP
5. #6 Multi-invoice email handling
6. #11 ERP sync monitoring agent
7. #4 Payment terms extraction
8. #2 Tax amount extraction
9. #26 Audit trail export

### Sprint 3: Enterprise foundations (P2) — ~4 weeks
10. #29 Database migrations
11. #30 Monitoring/alerting
12. #13 Chart of accounts from ERP
13. #16 Vendor master data sync
14. #32 Duplicate vendor consolidation
15. #21 Exportable reports
16. #27 SSO/SAML
17. #22 Outlook/M365 support (start)

### Sprint 4+: Expansion (P3) — ongoing
18. Everything else
