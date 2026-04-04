# Product Gaps — Clearledgr v1

Date: 2026-04-03
Source: Codebase audit + competitive analysis (Streak, BILL, Stampli)
Total items: 39 (32 done, 3 N/A, 4 remaining)

---

## Priority Tiers

### P0 — Blocks pilot (Cowrywise) ✅ ALL DONE
- ~~#1 Line item extraction and storage~~ ✅
- ~~#18 Multi-entity posting~~ ✅
- ~~#23 Multi-entity within one org~~ ✅

### P1 — Improves pilot quality (6/7 done)
- ~~#11 ERP sync monitoring~~ ✅
- ~~#4 Payment terms extraction~~ ✅
- ~~#26 Audit trail export~~ ✅
- ~~#19 Bill line items posting for NetSuite/SAP~~ ✅
- ~~#6 Multi-invoice email handling~~ ✅
- ~~#2 Tax amount extraction~~ ✅
- ~~#34 Auto-close after payment~~ ✅

### P2 — Blocks enterprise sales (6/16 done)
- ~~#5 Bank/payment details extraction~~ ✅
- ~~#9 Vendor communication agent~~ ✅
- ~~#10 Exception resolution agent~~ ✅
- ~~#13 Read chart of accounts from ERP~~ ✅
- ~~#15 Read full vendor list from ERP~~ ✅
- ~~#16 Sync vendor master data from ERP~~ ✅
- ~~#21 Exportable reports (CSV/JSON)~~ ✅
- ~~#22 Outlook/M365 support~~ ✅
- ~~#27 SSO/SAML~~ N/A (auth inherited from host platform)
- ~~#30 Monitoring/alerting~~ ✅
- ~~#32 Duplicate vendor consolidation~~ ✅
- ~~#35 Dispute/exception workflow~~ ✅
- ~~#36 Approval delegation~~ ✅
- ~~#37 Month-end accrual cutoff~~ ✅
- #28 SOC 2 — deferred (controls built, formal audit not started)
- #29 — remaining

### P3 — Post-pilot product expansion (6/13 done)
- ~~#3 Discount detection~~ ✅
- ~~#7 ZIP archive handling~~ ✅
- ~~#8 Payment tracking~~ ✅
- ~~#12 Spend analysis agent~~ ✅
- ~~#14 AP aging report~~ ✅
- ~~#17 Read payment status from ERP~~ ✅
- ~~#24 Outgoing webhooks~~ ✅
- ~~#25 Mobile/responsive~~ N/A (email IS the surface)
- ~~#38 Vendor statement reconciliation~~ ✅
- ~~#39 Tax compliance~~ ✅
- #20, #31 — remaining

---

## A. Email Extraction & Parsing

### 1. [DONE] Line item extraction and storage
**Priority:** P0
**Status:** Completed 2026-04-03
**What was built:**
- `line_items: Optional[List[Dict]]` added to InvoiceData (description, quantity, unit_price, amount, gl_code, tax)
- LLM extraction prompt returns structured line items
- Line items stored in AP item metadata, passed to ERP posting, displayed in sidebar and approval cards

### 2. [DONE] Tax amount extraction
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- `tax_amount`, `tax_rate`, `subtotal` fields added to InvoiceData
- Extraction prompt updated, displayed in sidebar and approval cards
- Tax handled correctly in ERP posting (separate line where required)

### 3. [DONE] Discount detection
**Priority:** P3
**Status:** Completed 2026-04-03
**What was built:**
- `discount_amount`, `discount_terms` fields added to InvoiceData
- Extracted from invoice text, factored into amount validation

### 4. [DONE] Payment terms extraction
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- `payment_terms` field added to InvoiceData
- Extraction prompt extracts NET 30, Due on receipt, 2/10 NET 30, etc.
- Compared against vendor profile terms, discrepancies flagged in validation gate

### 5. [DONE] Bank/payment details extraction
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `bank_details: Optional[Dict]` added to InvoiceData (bank_name, account_number, routing_number, iban, swift, sort_code)
- Extracted from invoice text, compared against stored vendor bank details
- Bank detail changes flagged as fraud signal in validation gate

### 6. [DONE] Multi-invoice email handling
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- Detects multiple invoice attachments per email
- Creates one AP item per invoice, all linked to same source email/thread
- Handles multi-invoice PDFs via page-based splitting

### 7. [DONE] Email attachment archive handling
**Priority:** P3
**Status:** Completed 2026-04-03
**What was built:**
- ZIP archive detection and extraction
- Each contained file processed as a potential invoice

---

## B. AI Agent Capabilities

### 8. [DONE] Payment tracking (full lifecycle)
**Priority:** P3
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/core/stores/payment_store.py` — payments + payment_events tables
- Payment statuses: ready_for_payment, scheduled, completed, partial, failed, reversed, overdue, closed_by_credit
- ERP polling (hourly) detects payment completion, reversal, overdue, failure
- Agent tools: `check_payment_readiness`, `verify_erp_posting` in APSkill
- Slack notifications on payment status changes

### 9. [DONE] Vendor communication agent
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/services/vendor_communication_templates.py` — 6 templates (missing_po, missing_amount, missing_due_date, bank_details_verification, general_inquiry, followup_reminder)
- `clearledgr/services/auto_followup.py` — Gmail send capability (gmail.send scope), falls back to draft on 403
- Response detection: background loop scans vendor reply threads every 30 min, links responses to AP items
- Follow-up escalation: auto-escalate after configurable days without response
- HTML sanitization, field truncation for security

### 10. [DONE] Exception resolution agent
**Priority:** P2
**Status:** Completed 2026-04-03 (commit `e49ab0a`)
**What was built:**
- `clearledgr/services/exception_resolver.py` — 11 resolution strategies
- Strategies: missing_po (auto-resolve via ERP lookup), vendor_not_found (auto-create), amount_anomaly (suggest), duplicate (suggest), vendor_mismatch (fuzzy match), low_confidence (identify fields), erp_sync_mismatch (re-verify then re-post), missing_approval, budget_exceeded, currency_mismatch, payment_terms_violation
- Background sweep every 45 min for unresolved exceptions
- Agent tool `resolve_exception` registered in APSkill

### 11. [DONE] ERP sync monitoring agent
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- Agent tool `verify_erp_posting` calls ERP to confirm bill exists with matching reference
- Background ERP reconciliation every hour for recently posted invoices
- Discrepancies flagged, auto-retry on ERP rejection
- ERP sync verification sweep every 3 hours

### 12. [DONE] Spend analysis agent
**Priority:** P3
**Status:** Completed 2026-04-03 (commit `3ba557d`)
**What was built:**
- `clearledgr/services/spend_analysis.py` — SpendAnalysisService with full portfolio analytics
- Top vendors by spend, spend by GL category, monthly trends with MoM %, budget utilization, portfolio anomaly detection (spend spikes >50%, new vendors, missing GL mappings)
- `GET /api/workspace/spend-analysis` endpoint
- Agent tool `analyze_spending` registered in APSkill

---

## C. ERP Read & Write

### 13. [DONE] Read chart of accounts from ERP
**Priority:** P2
**Status:** Completed 2026-04-03 (commit `0ac0e2a`)
**What was built:**
- `get_chart_of_accounts()` for all 4 ERPs (QuickBooks, Xero, NetSuite, SAP)
- Normalized output: account_id, code, name, type, sub_type, active, currency
- 24h caching in org `settings_json` with periodic refresh
- `GET /api/workspace/chart-of-accounts` endpoint
- GL validation enhanced to check against cached chart of accounts

### 14. [DONE] Read open AP aging report
**Priority:** P3
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/services/ap_aging_report.py` — `APAgingReport` service with 5 aging buckets (current, 1-30, 31-60, 61-90, 90+ days)
- Vendor breakdown per bucket, summary stats (total open, overdue %, vendor count)
- `GET /api/ap/items/aging` endpoint
- Filters: only open states (received through posted_to_erp), requires due_date
- 22 tests covering all buckets, filters, vendor breakdown, summary, API, and helpers

### 15. [DONE] Read full vendor list from ERP
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `list_all_vendors_quickbooks()` — QB Query API with STARTPOSITION pagination (1000/page)
- `list_all_vendors_xero()` — Contacts API with page pagination (100/page), filtered to IsSupplier==true
- `list_all_vendors_netsuite()` — SuiteQL with offset+limit pagination (1000/page)
- `list_all_vendors_sap()` — OData with $skip+$top pagination (500/page), filtered to CardType=cSupplier
- Normalized output per vendor: vendor_id, name, email, phone, tax_id, currency, active, address, payment_terms, balance
- `list_all_vendors()` router dispatcher with 24h cache in org settings_json
- `GET /api/workspace/erp-vendors` endpoint with active_only and search filters
- 24 tests covering all 4 ERPs, pagination, caching, dispatcher, and API endpoint

### 16. [DONE] Sync vendor master data from ERP
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/services/vendor_erp_sync.py` — `sync_vendors_from_erp()` service
- Pulls all vendors from ERP (via `list_all_vendors`), upserts to Clearledgr vendor profiles
- Change detection: new vendors, deactivated vendors, reactivated vendors, payment terms changes
- ERP-sourced fields stored in profile metadata (vendor_id, email, phone, address, tax_id, currency, balance)
- Preserves existing custom metadata on update
- Background job: daily at 6am UTC via `agent_background.py`
- Slack alerts on significant changes (new vendors, deactivations, terms changes)
- 11 tests covering all sync scenarios and background wiring

### 17. [DONE] Read payment status from ERP
**Priority:** P3
**Status:** Completed 2026-04-03 (built as part of #8)
**What was built:**
- `get_payment_status_quickbooks()`, `get_payment_status_xero()`, `get_payment_status_netsuite()`, `get_payment_status_sap()` — all 4 ERPs
- `get_bill_payment_status()` router dispatcher with token refresh + retry on 401
- Normalized output: paid, payment_amount, payment_date, payment_method, payment_reference, partial, remaining_balance
- Background polling hourly via `_poll_payment_statuses()` — caps at 50 ready/scheduled + 20 reversal checks + 20 overdue checks per org per tick
- Payment lifecycle tracking: ready_for_payment → scheduled → completed/partial/failed/reversed/overdue

### 18. [DONE] Multi-entity posting
**Priority:** P0
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/core/stores/entity_store.py` — EntityStore mixin with entities table
- Entity-scoped ERP connections, GL mappings, approval rules
- Entity routing rules: vendor → entity mapping
- SAP company_code routing, NetSuite subsidiary routing, QB separate realm per entity

### 19. [DONE] Bill line items posting for NetSuite/SAP
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- NetSuite: vendor bill with `expense`/`item` line array
- SAP: PurchaseInvoices with `DocumentLines` array
- All 4 ERPs post line items with per-line GL account and tax handling

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

### 21. [DONE] Exportable reports (CSV/JSON)
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/services/report_export.py` — report generation service with CSV serialization
- 3 report types: `ap_aging` (vendor breakdown by bucket), `vendor_spend` (vendors + GL + trends), `posting_status` (AP items with posting timing)
- Audit trail already exported via existing `GET /api/ap/items/audit/export`
- `GET /api/workspace/reports/export?report_type=...&format=csv|json` endpoint
- Filters: period_days, start_date, end_date, vendor (substring match)
- CSV download with Content-Disposition header, JSON with row_count and columns
- 15 tests covering all report types, filters, CSV serialization, and API endpoint

### 22. [DONE] Outlook/M365 support
**Priority:** P2
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/services/outlook_api.py` — OutlookAPIClient, OutlookToken, OutlookTokenStore (Fernet-encrypted), Microsoft Graph scopes (Mail.Read, Mail.ReadWrite, Mail.Send)
- `clearledgr/services/outlook_autopilot.py` — OutlookAutopilot polling loop (mirrors GmailAutopilot), 5-min interval, catch-up rescan on startup, subscription limit checks
- `clearledgr/services/outlook_email_processor.py` — bridges Outlook messages into existing AP pipeline via process_invoice()
- `clearledgr/api/outlook_routes.py` — OAuth connect/callback/disconnect, status, webhook (Graph change notifications with validation handshake)
- `outlook_autopilot_state` DB table for polling state persistence
- Wired into app_startup.py (auto-starts alongside Gmail autopilot)
- Graph subscription support for push notifications (new mail → webhook → next poll picks up)
- `ui/outlook-addin/` — Office Add-in with manifest.xml, taskpane (Preact + HTM + Office.js), same backend API as Gmail extension
- Sidebar shows: invoice card (vendor, amount, state, field confidence), agent timeline, action buttons (approve, post, reject), worklist
- Reads current email context via Office.js mailbox API, matches to AP item by conversationId
- Auth via Office SSO token exchange
- 24 tests covering tokens, store, config, API client, autopilot, routes, webhooks, DB state

### 23. [DONE] Multi-entity within one org
**Priority:** P0
**Status:** Completed 2026-04-03
**What was built:**
- `entities` table with CRUD via EntityStore mixin
- Entity detection from invoice (by vendor, GL code, cost center, explicit rules)
- Entity selection in sidebar, entity-specific approval chains
- Entity-specific ERP posting (integrated with #18)

### 24. [DONE] Outgoing webhooks
**Priority:** P3
**Status:** Completed 2026-04-03
**What was built:**
- `clearledgr/core/stores/webhook_store.py` — WebhookStore mixin with subscription CRUD, wildcard (`*`) event matching
- `clearledgr/services/webhook_delivery.py` — async delivery with HMAC-SHA256 signing (`X-Clearledgr-Signature`), event emission, retry via existing notification queue
- `webhook_subscriptions` table (id, org, url, event_types JSON, secret, is_active)
- 11 event types: invoice.received, .validated, .needs_approval, .approved, .rejected, .ready_to_post, .posted_to_erp, .closed, .needs_info, .failed_post + payment events
- Auto-emitted on every AP state transition (fire-and-forget post-commit hook in `update_ap_item`)
- Failed deliveries enqueued in `pending_notifications` with exponential backoff (5 retries)
- Webhook retry handler wired into existing `process_retry_queue()` (channel="webhook")
- API: `GET/POST /api/workspace/webhooks`, `DELETE /webhooks/{id}`, `POST /webhooks/{id}/test`
- 21 tests covering store, HMAC, delivery, emission, retry, and API endpoints

### 25. [N/A] Mobile app or mobile-optimized view
**Priority:** N/A
**Status:** Not applicable — by design, Clearledgr renders inside Gmail (extension sidebar) and Outlook (add-in taskpane), which are already responsive. Approvals route to Slack/Teams mobile. No standalone app surface exists for daily use. Workspace admin console is setup-only, not a daily workflow surface.

### 26. [DONE] Audit trail export
**Priority:** P1
**Status:** Completed 2026-04-03
**What was built:**
- `GET /api/ap/items/audit/export` endpoint with CSV and JSON formats
- Filters: date range, vendor, state
- Streaming CSV response for large exports
- All audit event types with full detail

### 27. [N/A] SSO/SAML implementation
**Priority:** N/A
**Status:** Not applicable — Clearledgr authenticates through the email provider (Gmail Chrome identity, Outlook Office SSO) and messaging platform (Slack/Teams OAuth). There is no standalone login surface where SSO/SAML would apply. Enterprise identity is inherited from the host platform.

---

## E. Security & Infrastructure

### 28. [DEFERRED] SOC 2 certification
**Priority:** P2 (enterprise requirement)
**Status:** Technical controls built, formal audit not started.
**Why it matters:** Clearledgr reads invoices from customer inboxes, sends them to Claude for extraction, stores extracted financial data (amounts, vendors, line items), and holds OAuth tokens with inbox read access. Enterprise procurement requires SOC 2 for this data handling profile.
**What's already built (code-level controls):**
- Fernet encryption at rest for all tokens/secrets
- Append-only audit trail with DB triggers (no UPDATE/DELETE on audit_events)
- Tenant isolation (organization_id on every query, verified at API layer)
- Minimal OAuth scopes per provider
- HMAC-SHA256 webhook signatures
**What's needed (process, not code):**
- Engage Vanta/Drata for automated compliance monitoring
- Document controls mapping to SOC 2 Trust Service Criteria
- Complete Type II audit (~3-6 month observation period)
**Estimated effort:** 3-6 months (process work, minimal code changes)

### 29. [MISSING] Database migrations
**Priority:** P2
**What's missing:** Tables created lazily. No migration framework.
**What's needed:**
- Integrate Alembic (SQLAlchemy migration tool) or a lightweight alternative
- Generate initial migration from current schema
- Migration on startup or deploy
- Rollback capability
**Estimated effort:** 3-5 days

### 30. [DONE] Monitoring/alerting integration
**Priority:** P2
**Status:** Completed 2026-04-04
**What was built:**
- Sentry SDK integration in `main.py` — opt-in via `SENTRY_DSN` env var, FastAPI + httpx integrations, configurable trace sample rate
- `clearledgr/services/monitoring.py` — MonitoringService with 5 threshold checks: dead letters, auth failures, stale autopilot, overdue invoices, posting failure rate
- Alerts emitted to configurable channels (slack, webhook, log) via `MONITOR_ALERT_CHANNELS` env var
- Critical alerts also captured as Sentry events
- All thresholds overridable via `MONITOR_THRESHOLD_*` env vars
- `GET /api/ops/monitoring-health` endpoint for on-demand health checks
- Runs hourly in background loop (alongside anomaly detection)
- 16 tests covering all checks, thresholds, alerts, API, and background wiring

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

### 32. [DONE] Duplicate vendor consolidation
**Priority:** P2
**Status:** Completed 2026-04-04
**What was built:**
- `clearledgr/services/vendor_dedup.py` — VendorDedupService with detect, merge, alias management, and name resolution
- Detection: fuzzy matching via existing `vendor_similarity()` (Jaccard + SequenceMatcher), configurable threshold, returns ranked clusters with canonical suggestion (most invoices)
- Merge: consolidates aliases, reassigns AP items to canonical, deletes duplicate profiles
- Alias management: add/remove aliases on vendor_aliases JSON array, idempotent
- Name resolution: `resolve_vendor_name()` maps any alias to its canonical vendor
- API: `GET /vendor-intelligence/duplicates`, `POST /vendor-intelligence/merge`, `POST/DELETE .../aliases`
- 19 tests covering detection, merge, aliases, resolution, and API endpoints

### 33. [N/A] Historical data import
**Priority:** N/A
**Status:** Not needed — Clearledgr processes invoices as they arrive (Streak model). Historical AP data lives in the ERP (already readable via chart of accounts, vendor list, payment status APIs). The existing `/extension/repair-historical-invoices` handles the one valid case: backfilling Gmail emails that arrived before Clearledgr was connected.

---

## G. AP Lifecycle Completeness

### 34. [DONE] Auto-close after payment
**Priority:** P1
**Status:** Already built (part of #8 payment tracking implementation)
**What exists:**
- Full payment detected → `posted_to_erp` → `closed` with audit event `closed_by_payment` (agent_background.py:947-966)
- Credit/write-off closure → `posted_to_erp` → `closed` with audit event `closed_by_credit` (agent_background.py:879-898)
- Partial payment does NOT close — waits for full payment
- Slack notifications on both payment completion and credit closure
- Payment events logged in payment_events table

### 35. [DONE] Dispute/exception workflow
**Priority:** P2
**Status:** Completed 2026-04-04
**What was built:**
- `disputes` table (id, ap_item_id, org, type, status, vendor, description, resolution, timestamps)
- `clearledgr/core/stores/dispute_store.py` — DisputeStore mixin with full CRUD
- `clearledgr/services/dispute_service.py` — DisputeService with lifecycle: open → vendor_contacted → response_received → resolved/escalated/closed
- 8 dispute types: missing_po, wrong_amount, vendor_mismatch, missing_info, duplicate, bank_detail_change, erp_sync_mismatch, other
- Summary endpoint with counts by status and type
- Existing background infrastructure already handles: vendor reply detection (30 min scan), auto-escalation, follow-up resend, Slack notifications
- API: `GET/POST /disputes`, `GET /disputes/summary`, `POST /disputes/{id}/resolve`, `POST /disputes/{id}/escalate`
- 18 tests covering store, service lifecycle, summary, and API endpoints

### 36. [DONE] Approval delegation
**Priority:** P2
**Status:** Completed 2026-04-04
**What was built:**
- `delegation_rules` table (delegator, delegate, date range, active flag)
- `clearledgr/services/approval_delegation.py` — DelegationService with rule CRUD, delegate resolution (with date range), approver list resolution, auto-reassignment
- Date-bounded rules: `starts_at`/`ends_at` for scheduled OOO periods
- `resolve_approvers()` swaps delegated approvers in any approval list
- `auto_reassign_pending_approvals()` wired into background approval timeout checks — reassigns pending chains to delegates
- API: `GET/POST /delegation-rules`, `POST /delegation-rules/{id}/deactivate`
- 16 tests covering rules, resolution, date ranges, and API endpoints

### 37. [DONE] Month-end accrual cutoff
**Priority:** P2
**Status:** Completed 2026-04-04
**What was built:**
- `clearledgr/services/period_close.py` — PeriodCloseService with full period management
- Configurable cutoff dates per org (`close_day_offset` in settings_json, default: 5th of next month)
- Period lock/unlock: prevents posting invoices dated in locked periods
- Backdate detection: finds invoices received after cutoff that belong to the prior period
- Accrual report: uninvoiced liabilities by vendor, currency-aware, for any period
- Current period detection with closing window and days-until-close
- `check_posting_allowed()` for integration into validation gate
- API: `GET /period-close/current`, `GET /period-close/accruals/{period}`, `GET /period-close/backdated/{period}`, `POST /period-close/lock/{period}`, `POST /period-close/unlock/{period}`
- 18 tests covering detection, lock/unlock, accruals, backdating, config, and API

### 38. [DONE] Vendor statement reconciliation
**Priority:** P3
**Status:** Completed 2026-04-04
**What was built:**
- `clearledgr/services/vendor_statement_recon.py` — VendorStatementRecon with 4-tier matching strategy
- Matching: exact reference → partial reference → amount+date proximity (5-day tolerance) → amount-only
- Output: matched items, amount discrepancies, unmatched on statement, unmatched in Clearledgr
- Summary: match rate %, totals, difference, counts per category
- Reference normalization (strips punctuation for INV/003 = INV-003)
- `POST /api/workspace/vendor-intelligence/reconcile-statement` endpoint
- 12 tests covering all match types, unmatched, discrepancies, summary, and API

### 39. [DONE] Tax compliance reporting (Europe/Africa)
**Priority:** P3
**Status:** Completed 2026-04-04
**What was built:**
- `clearledgr/services/tax_compliance.py` — TaxComplianceService with full Europe/Africa tax support
- VAT number validation for 27 EU states + UK + Nigeria + Kenya + Ghana + South Africa (regex patterns per country)
- Standard VAT rates and WHT rates per country
- Reverse charge detection for intra-EU B2B transactions
- Annual vendor payment totals with tax ID validation status
- Tax summary report: vendor totals, missing/invalid tax IDs, reverse charge applicable, WHT applicable with estimated amounts
- `GET /api/workspace/tax-compliance/summary?buyer_country=NG&year=2026`
- `POST /api/workspace/tax-compliance/validate-tax-id`
- 28 tests covering VAT validation (EU, UK, NG, KE, GH, ZA), reverse charge, rate lookups, service, and API

---

## Completion Status

### Already built (as of 2026-04-03):
- ✅ #1 Line item extraction and storage
- ✅ #2 Tax amount extraction
- ✅ #3 Discount detection
- ✅ #4 Payment terms extraction
- ✅ #5 Bank/payment details extraction
- ✅ #6 Multi-invoice email handling
- ✅ #7 ZIP archive handling
- ✅ #8 Payment tracking (full lifecycle with ERP polling)
- ✅ #9 Vendor communication agent (send, response detection, escalation, 6 templates)
- ✅ #10 Exception resolution agent (11 strategies)
- ✅ #11 ERP sync monitoring agent
- ✅ #12 Spend analysis agent (portfolio analytics)
- ✅ #13 Chart of accounts from ERP (all 4 ERPs, 24h cache)
- ✅ #14 AP aging report (5 buckets, multi-currency, vendor breakdown)
- ✅ #15 Read full vendor list from ERP (all 4 ERPs, paginated, 24h cache)
- ✅ #17 Read payment status from ERP (all 4 ERPs, hourly polling, lifecycle tracking)
- ✅ #16 Sync vendor master data from ERP (daily sync, change detection, Slack alerts)
- ✅ #18 Multi-entity posting
- ✅ #19 Bill line items for NetSuite/SAP
- ✅ #23 Multi-entity within one org
- ✅ #21 Exportable reports (CSV/JSON: aging, spend, posting status)
- ✅ #22 Outlook/M365 support (Graph API, OAuth, autopilot, webhooks, add-in sidebar)
- ✅ #24 Outgoing webhooks (HMAC-signed, 11 event types, retry queue, management API)
- ✅ #26 Audit trail export
- ✅ #30 Monitoring/alerting (Sentry, threshold checks, multi-channel alerts)
- ✅ #32 Duplicate vendor consolidation (fuzzy detection, merge, alias management)
- ✅ #34 Auto-close after payment (already built in #8 payment tracking)
- ✅ #35 Dispute/exception workflow (disputes table, lifecycle, summary, API)
- ✅ #36 Approval delegation (delegation rules, date ranges, auto-reassign)
- ✅ #37 Month-end accrual cutoff (period lock, backdate detection, accrual report)
- ✅ #38 Vendor statement reconciliation (4-tier matching, discrepancy detection)
- ✅ #39 Tax compliance (EU/UK/Africa VAT validation, reverse charge, WHT rates)

---

## Summary

| Priority | Done | Remaining | Remaining effort |
|----------|------|-----------|-----------------|
| P0 (blocks pilot) | #1, #18, #23 | — | ✅ DONE |
| P1 (improves pilot) | #2, #4, #6, #11, #19, #26, #34 | — | ✅ DONE |
| P2 (blocks enterprise) | #5, #9, #10, #13, #15, #16, #21, #22, #30, #32, #35, #36, #37 | #28, #29 | 6-11 days |
| P3 (post-pilot) | #3, #7, #8, #12, #14, #17, #24, #38, #39 | #20, #31 | 9-18 days |
| N/A | #25, #27, #33 | — | — |
| **Total** | **32 done** | **4 remaining** | **15-29 days** |

---

## Implementation Order

### Done ✅ (32 items)
1. #23 Multi-entity within one org
2. #18 Multi-entity posting
3. #1 Line item extraction and storage
4. #19 Bill line items for NetSuite/SAP
5. #6 Multi-invoice email handling
6. #11 ERP sync monitoring agent
7. #4 Payment terms extraction
8. #2 Tax amount extraction
9. #26 Audit trail export
10. #3 Discount detection
11. #5 Bank/payment details extraction
12. #7 ZIP archive handling
13. #8 Payment tracking (full lifecycle)
14. #9 Vendor communication agent
15. #10 Exception resolution agent
16. #12 Spend analysis agent
17. #13 Chart of accounts from ERP
18. #14 AP aging report
19. #15 Read full vendor list from ERP
20. #16 Vendor master data sync
21. #21 Exportable reports
22. #22 Outlook/M365 support
23. #24 Outgoing webhooks
24. #30 Monitoring/alerting

### Sprint 3: Enterprise foundations (P2) — ~3 weeks
26. #29 Database migrations
27. #35 Dispute/exception workflow
28. #36 Approval delegation
29. #37 Month-end accrual cutoff

### Sprint 4+: Expansion (P3) — ongoing
31. #20 Payment execution
32. #31 Non-English invoice handling
34. #33 Historical data import
35. #38 Vendor statement reconciliation
36. #39 1099/tax reporting
