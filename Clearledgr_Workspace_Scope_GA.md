# Clearledgr Dashboard — GA Scope

**Status:** Build spec — GA target
**Revision:** v2 — 2026-04-28
**Audience:** Engineering, design, founding team
**Owners:** Suleiman Mohammed (CTO), Mo Mbalam (CEO)
**Last edit:** 2026-04-28 (v2 — incorporates review of v1; see Revision log at end)

---

## What's different

Five things separate the Clearledgr dashboard from Tipalti, Stampli, AP Genius, Bill.com, and Avidxchange. Every module decision should be traceable to one of these.

1. **The agent does the work, not the team.** No leaderboards. No invoices-per-person metrics. No productivity rankings. The dashboard surfaces system performance and operational logistics; it never scores people on judgment work. Competitors treat the team as the unit of throughput; we treat the agent as the unit of throughput and the team as the judgment layer.

2. **The harness is the moat, made legible.** Every state-mutating action runs through `runtime.execute_intent` → loop service → governance deliberation → policy precheck → side effect → append-only audit. The dashboard is where this becomes visible to the leader: agent reasoning, governance verdict, confidence score, and source data references on every escalation. Competitors expose an LLM directly; we expose the harness.

3. **Surface architecture, not portal architecture.** Operators work in Gmail (sidebar), Slack (approvals), Teams (approvals), NetSuite (Suitelet panel), and SAP (Fiori extension). The dashboard is the leader's control plane, not another finance portal the team has to log into. Competitors force everyone into their portal; we meet operators where they already work.

4. **Audit log is non-negotiable infrastructure, not a feature.** Append-only at the database level via Postgres triggers, with `governance_verdict` and `agent_confidence` as queryable columns (migration v50). Hash-chained mirror + WORM-backed retention is the next design pass (see Module 7). Competitors have access logs; we have a tamper-evident workflow record CFOs defend with.

5. **Built for AP, architected for everything.** The Box primitive, AP state machine, planning engine, and coordination engine are workflow-agnostic. AP ships first because that's the wedge; AR / Recon / Close turn on through UI surfacing only, not re-architecture. Competitors ship modules; we ship a coordination layer.

The dashboard is the proof of all five. Build accordingly.

---

## Purpose

The dashboard is the **control plane** for Clearledgr's coordination layer. It is where finance leaders configure how the agent works, see whether it is working, and resolve exceptions the agent cannot handle alone.

It is NOT where finance teams do their daily work. The dashboard is where **leaders** work daily; **operators** work in Gmail, Slack, Teams, and the customer's ERP. Building operator UI into the dashboard would undermine the surface architecture and turn Clearledgr into another finance portal. The dashboard's value is in what only a leader needs.

This is the GA scope. We ship a dashboard that handles mid-market customers self-serve, lands enterprise customers without a "wait until v2" conversation, and gives the agent the configuration depth it needs to actually do the work.

---

## Design principles

Six rules every dashboard decision must be checked against.

**1. The agent does the work. People handle approvals and exceptions.**
The dashboard reflects this model. It does not score team productivity. It does not count invoices per person per day. It surfaces system performance and operational logistics, not individual labor metrics. Logistics ("Tobi has 8 invoices waiting, oldest 5 days old, on PTO") is in scope so the leader can re-route. Scoring is not.

**2. The dashboard reflects, it does not author.**
Authoritative data lives in the customer's ERP. The dashboard reads from and writes to the ERP through the integration layer. It does not maintain a parallel data model. Vendor masters, GL accounts, journal entries, and chart of accounts remain ERP-resident.

**3. Five reports, well-built. No custom report builder.**
The dashboard's primary value is letting the leader configure agent behavior. Reports are secondary infrastructure. We ship five standard reports (Volume, Agent Performance, Cycle Time, Exception Breakdown, Vendor Quality) and ship them well. We do not build a custom report builder. We do not ship AI-generated commentary on reports. We do not ship customer-configurable dashboards in v1.

**4. Every action has an audit trail. Every audit entry is tamper-evident at the DB level today; cryptographic hash chain + WORM mirror is Module 7's hardening pass.**
Today: append-only Postgres triggers, idempotency-key dedupe, `governance_verdict` + `agent_confidence` queryable columns. Aspirational target: hash-chained ledger + WORM-backed retention so even Clearledgr engineering cannot edit history. Module 7 covers the design.

**5. Built for AP, architected for everything.**
The dashboard ships with AP active. The rule engine, exception types, and surface architecture are workflow-agnostic. AR / Recon / Close turn on in the UI when each workflow ships, without re-architecture.

**6. Enterprise-ready from day 1.**
SAML SSO (Azure AD + Okta + Google Workspace SAML + OneLogin + generic SAML 2.0), custom roles, per-vendor permission scoping, SIEM integration, append-only audit log. These are not v2 features. Enterprise buyers expect them at first contact.

---

## Module list

Eleven modules. Built in roughly the sequence in "Build sequence" below, though many can be parallelised. **Each module has a one-page appendix at the end of this document** with API endpoints, data model deltas, test scenarios, and dependencies — that's where engineers go for buildable detail; this section is for product framing.

### Module 1 — Live Operations

The leader's daily landing page. Shows the current state of the AP coordination layer at a glance. This is what they open every morning to know what needs attention.

**Components:**
- Stat cards: in flight, awaiting approval, processed this week, agent exceptions
- Exceptions queue: every invoice currently stuck and needing human judgment, sorted by age, with vendor, amount, exception type, who's blocking, days stuck, agent's suggestion. Click through to Module 2.
- Approver workload strip: who has what on their plate. Surfaces logistics ("Tobi has 8 waiting, oldest 5 days, on PTO") so the leader can re-route. Logistics, not scoring.
- Multi-entity selector: leaders running multiple legal entities switch context here.
- System status footer: agent active, last action timestamp, sync status with each connected ERP, inbox, Slack workspace.

**Acceptance criteria:**
- Loads in under 2 seconds for 5,000 invoices in flight
- Stat cards refresh in real time as agent acts (websocket or SSE, max 30s lag)
- Exceptions queue shows top 20 by default, paginated, filterable by entity, exception type, vendor, age
- Approver workload pulls from active assignments
- Footer shows actual integration sync state, surfacing any error within 10 minutes of detection

**Out of scope:** trends over time (lives in Reports); per-approver speed metrics; customer-configurable widgets.

---

### Module 2 — Exception Detail and Resolution

Where the leader applies judgment. When the agent escalates, this is the screen the leader opens.

**Components:**
- Header: vendor, invoice number, amount, status, age
- Bill detail: every field the agent extracted (subsidiary, amount, tax, terms, GL account, department, memo, line items)
- 3-way match panel: PO status, GRN status, three-way match status, with deep links into ERP source records
- **Agent reasoning panel**: pulls from `audit_events.governance_verdict`, `audit_events.agent_confidence`, and the `canonical_audit_event` payload (per memory: harness audit columns from migration v50). Renders the agent's preview, the governance verdict, the confidence score, and the source data references in plain language. This is the harness made legible.
- Workflow timeline: every event in this invoice's lifecycle (from `audit_events`)
- Action buttons: approve, send to specific person, mark as duplicate, request more info from vendor, override and post anyway
- Ask the agent: free-form questions about this invoice ("show prior bills from this vendor", "what does PO 4471-A reference"). Returns within 10 seconds for typical questions. **Note: this is net-new LLM Q&A surface; sized in Phase 3.**
- **Batch resolution**: select multiple invoices in the exceptions queue, take a bulk action (batch approve, batch send to person, batch mark as duplicate). Each batch action records as individual audit entries.

**Acceptance criteria:**
- All actions write back to ERP via integration; no orphaned state
- Every action runs through `runtime.execute_intent` and writes the canonical audit event chain
- Batch actions support up to 200 invoices in one operation
- Agent reasoning shown in plain language with source references, not raw model output
- Ask-the-agent returns within 10 seconds for typical questions

**Out of scope:** cross-workflow batch resolution (AP only in v1).

---

### Module 3 — Approval Rules Configuration

Where the leader teaches the agent how to route work. This is where the customer's policies get encoded.

**v1 scope: JSON-mode rule editor + 4 starter rule templates + test mode + version history.** Rescoping note: the visual drag-drop rule builder is genuinely 4+ sprints of dedicated frontend work — own design doc (TBD: `Module-3-Visual-Rule-Builder.md`), shipped in v1.5. **Don't promise the visual builder for GA.**

**Components (v1):**
- Rule list view: all active rules, sortable, filterable by entity, workflow, trigger type
- **JSON-mode rule editor** with structured schema validation, syntax highlighting, and inline conflict detection
- **4 starter rule templates** — covers ~80% of common patterns: under-$1K auto-approve / $1K–$10K to manager / $10K–$50K to controller / $50K+ to dual-approval
- Conditions: amount, currency, vendor (name match, tag, list), GL account, department, entity, invoice age, vendor age, workflow type
- Actions: route to user, route to role, require N approvals, require dual approval, escalate after time, auto-approve, hold pending finance review
- Default routing: what happens when no rule matches
- **Per-vendor and per-amount scoping**: rules support "Sara can approve invoices under $50K from any vendor; Mo can approve any amount." Composes with the role system.
- Per-entity overrides: a multi-entity customer (Booking.com class) configures different rules per legal entity
- **Test mode**: run a sample invoice through the rule set with full trace through rule evaluation
- **Version history**: rule changes tracked with who changed what when, with one-click revert (backed by `policy_versions` table — migration v45)
- **Rule conflict detection**: surface when two rules match the same invoice with different actions, at save time, not silently resolved

**v1.5 scope (own design doc):**
- Visual drag-drop rule builder with AND/OR composition, nested conditions, live preview
- ML-suggested rules from historical patterns

**Acceptance criteria:**
- JSON editor + templates usable by a finance leader without engineering support (templates do the heavy lifting)
- Test mode shows the trace through rule evaluation, not just the final routing
- Rule conflicts surfaced at save time, not silently resolved
- Changes do not affect in-flight invoices unless explicitly applied retroactively
- Rule engine is workflow-agnostic; AP is the only active workflow in v1

**Out of scope:** rule templates marketplace; cross-customer rule sharing; ML-suggested rules (v1.5).

---

### Module 4 — Vendor Management

The vendor list, with what the agent needs to know about each. Vendor data lives in the customer's ERP. The dashboard reflects that data and adds Clearledgr-specific attributes the ERP does not track.

**Components:**
- Vendor list: all vendors, with status, payment terms, last bill, exception rate, IBAN verification status
- Vendor detail: ERP data (name, address, tax ID, terms) plus Clearledgr layer (verified IBANs, fraud flags, custom routing rules, agent confidence with this vendor)
- Bulk import: from ERP on connection, refreshable, handles 10,000+ vendors without blocking
- **Vendor onboarding workflow**: backed by the existing `vendor_onboarding_session` Box type (per memory: registered Box type alongside `ap_item`). The dashboard surfaces in-flight onboarding sessions and resolves them through the same harness pattern as AP exceptions.
- Verification: agent attempts auto-verification on creation (IBAN check, business registry lookup, prior payment match). Surfaces unverified vendors for human review.
- **Basic fraud signals** (rule-based, not ML): new IBAN doesn't match prior payments; unusually large invoice from low-frequency vendor; vendor created within last 30 days with first invoice over $X. Configurable per customer. Surfaced as exception types.
- Vendor performance view: exception rate per vendor over time. Identifies upstream data quality issues. Not a vendor leaderboard.

**Acceptance criteria:**
- Vendor master sync from NetSuite/SAP is bidirectional within 60 seconds of any change
- IBAN verification runs on vendor creation and on first payment to a new IBAN
- Bulk import handles 10,000 vendors without blocking
- Fraud signal rules are customer-configurable and audit-logged when triggered
- Vendor performance shows trends, not just current snapshots
- Onboarding sessions are first-class Boxes — Module 4 surfaces what's already in `vendor_onboarding_sessions`

**Out of scope:** vendor self-service portal; sophisticated ML-based fraud detection (basic rule-based signals only); vendor onboarding as a separate product.

---

### Module 5 — Integration Setup

Connect, test, and maintain the integrations. The leader sets up integrations during onboarding, but the dashboard supports ongoing connection management, credential rotation, and troubleshooting.

**Components:**
- Connection list: every integration (NetSuite, SAP, QuickBooks, Xero, Gmail, Slack, Teams), status, last sync, account or workspace identifier
- Connect flow: OAuth or credential-based per integration. Per ERP, configure GL mapping (which Clearledgr field maps to which ERP field), default GL accounts, default approval routing
- **Custom field mapping UI**: for non-standard NetSuite or SAP setups, the leader maps additional ERP fields to Clearledgr fields through a structured UI. Not infinitely flexible — bounded set of mappable Clearledgr fields with dropdowns. Prevents customer-onboarding bottlenecks.
- Connection health: per integration, recent sync log, error count, latency
- Credential rotation: refresh tokens, rotate API keys without re-onboarding
- Test action: run a test transaction that verifies the agent can read and write to the ERP
- **Outbound webhooks**: configure webhooks for events (invoice approved, invoice paid, exception raised). Customer's downstream systems consume the webhook. Bounded set of event types; no custom payload composition in v1.

**Acceptance criteria:**
- NetSuite SuiteApp or SAP via BTP setup completes in under 30 minutes by an ERP admin
- Gmail and Slack OAuth flows complete in under 5 minutes
- GL mapping has reasonable defaults that work for 80% of customers without customisation
- Custom field mapping UI handles 90% of non-standard cases without engineering involvement
- Connection errors surface to leader within 10 minutes of detection
- Webhook delivery has retry, exponential backoff, and a delivery log

**Out of scope:** connectors beyond the seven listed; SIEM-specific connectors (use generic webhook); custom webhook payload composition; inbound webhooks.

---

### Module 6 — Users, Roles, and Permissions

Who can do what in the dashboard and across the surfaces.

**Standard role mapping** (canonical thesis taxonomy — see `clearledgr/core/auth.py` for the rank table):

| Dashboard label | Codebase enum | Rank | Permissions surface |
|---|---|---|---|
| Owner | `ROLE_OWNER` | 80 | All actions; only role that can manage other Owners |
| CFO | `ROLE_CFO` | 70 | All actions except manage-Owners; defends fraud-control config |
| Financial Controller | `ROLE_FINANCIAL_CONTROLLER` | 60 | Manage rules, vendors, integrations, custom roles, billing, audit log read |
| AP Manager | `ROLE_AP_MANAGER` | 40 | Approve invoices, manage exceptions, route work; cannot manage rules / users / integrations |
| AP Clerk | `ROLE_AP_CLERK` | 20 | Operate the queue; cannot approve > policy-defined ceiling |
| Read-only | `ROLE_READ_ONLY` | 10 | View timelines, audit log, reports; cannot mutate anything |

**Components:**
- User list: every user, role, last active, invitation status
- **Custom roles**: leaders compose custom roles from the permission matrix. Bounded to 10 custom roles per customer to prevent sprawl.
- Permission matrix: granular permissions for configure rules, manage vendors, approve invoices, see reports, manage integrations, view audit log, export data, manage users
- Per-entity scoping: a user can have different roles in different legal entities (Sara is AP Manager in EU entity, Read-only in US entity)
- Per-amount scoping: composes with rules — "Sara can approve up to $50K"
- Invite flow: email invitation, role assignment, optional restriction to specific entities or workflows
- **SAML SSO**: Azure AD + Okta + Google Workspace SAML + OneLogin + generic SAML 2.0. Cert-pinning policy + JIT provisioning rules in design doc TBD (`Module-6-SAML-Implementation.md`). Configurable per customer.
- User offboarding: remove access immediately across dashboard and all surfaces. Audit-logged.

**Acceptance criteria:**
- Six standard roles cover 80% of common cases without custom permissions
- Custom roles cover the remaining 20% without engineering involvement
- Role assigned in dashboard automatically applies to Gmail, Slack, Teams, and ERP surfaces (single source of truth)
- SAML SSO setup completes in under 60 minutes by a customer's IT team
- User offboarding removes access within 30 seconds across all surfaces
- All permission changes audit-logged

**Out of scope:** SCIM provisioning (post-GA); federated identity beyond SAML; time-bound or conditional permissions.

---

### Module 7 — Audit Trail

Every workflow action, logged, searchable, exportable, append-only at the DB level today.

**v1 scope (have today):**
- `audit_events` table with `box_id`, `box_type`, `event_type`, `prev_state`, `new_state`, `actor_type`, `actor_id`, `payload_json`, `idempotency_key`, `correlation_id`, `governance_verdict`, `agent_confidence`, `organization_id`, `ts` (per migration v50)
- Append-only Postgres triggers: `clearledgr_prevent_append_only_mutation()` rejects UPDATE/DELETE on `audit_events` and `ap_policy_audit_events`
- Idempotency-key dedupe via UNIQUE constraint
- Search and filter: by user, date range, event type, workflow, vendor, entity
- Export: CSV and PDF, with date range and filter applied
- **SIEM integration**: audit events streamed via webhook to customer's SIEM (Splunk, Datadog, Elastic, etc.) in near real time. Generic webhook format
- Retention: indefinite by default. Customer can configure longer retention for compliance reasons.
- Admins cannot edit via the application; superusers with raw DB access can. Closing this gap requires Module 7's hardening pass.

**v2 hardening (own design doc — `Module-7-Audit-Hardening.md`):**
- Cryptographic hash chain — each entry hashes the prior; chain root publication strategy TBD (transparency log? customer-supplied pinning? both?)
- WORM-backed mirror (S3 Object Lock or equivalent) for the canonical chain
- Standalone verification tool: customer or auditor verifies the chain independently
- Migration plan: how do we backfill the chain over an existing `audit_events` table without breaking idempotency?
- "Even Clearledgr employees cannot edit" only becomes true once both hash chain + WORM mirror ship.

**Acceptance criteria:**
- v1: All workflow actions write an audit entry within 5 seconds; search returns within 3 seconds for date ranges up to 1 year + 100K events; export of full year completes within 60 seconds; SIEM webhook has retry; failed deliveries surface to admin within 1 hour.
- v2: Hash chain integrity verifiable via standalone tool; WORM-backed mirror reconciliation runs nightly; ledger immutability claim defensible to auditors.

**Out of scope:** automated SOC 2 compliance reporting (existing SOC2 evidence packet covers this — see project memory); audit log analysis / anomaly detection.

---

### Module 8 — Reports

Five fixed reports, well-designed. No custom report builder. No AI-generated commentary.

**The five reports:**
- **Volume**: invoices processed over time, by entity, by vendor. Daily, weekly, monthly granularity.
- **Agent Performance**: agent confidence trend, auto-resolution rate, exception rate over time.
- **Cycle Time**: average days from invoice receipt to ERP post, by entity. System-wide, not per-person.
- **Exception Breakdown**: which exception types are most common, trending up or down. Identifies upstream issues to fix.
- **Vendor Quality**: vendors ranked by exception rate. Identifies which vendor relationships need a conversation.

**Components:**
- Each report supports date range filter, entity filter, export to CSV and PDF.
- **Scheduled email delivery**: leader configures "email me this report every Monday." Standard intervals (daily, weekly, monthly). No custom scheduling.

**Acceptance criteria:**
- Each report loads in under 5 seconds for one year of data
- Scheduled email delivery is reliable (>99% on-time delivery)
- No personally identifying ranking ("your team is X% slower than benchmark")
- Reports are designed, not generated; finite set, well-styled

**Out of scope:** custom report builder; AI-generated insights; customer-configurable dashboards; cross-customer benchmarking.

**Underlying infrastructure decision (open question):** materialized views vs. read replica vs. separate analytics DB for reports queries. Affects every other module's instrumentation cost. See Open questions log.

---

### Module 9 — Multi-Entity and Multi-Currency

Mid-market enterprises run multiple legal entities, often across currencies. The dashboard handles this natively.

**Components:**
- Entity hierarchy: parent and subsidiary structure mirrored from ERP
- Per-entity rules, vendors, users, approvers
- Multi-currency support: invoices in any currency, FX rates pulled from ERP, conversion to functional currency for reporting
- Cross-entity views (for leaders with permission across multiple entities): combined exceptions queue, combined reporting
- **Per-entity audit log scoping**: an entity's auditors see only that entity's events. **Net-new access pattern**: current `audit_events` is org-scoped via `organization_id`. Decision deferred to Module 9's design pass — either (a) add `entity_id` column with backfill via `metadata.entity_id` lookup, or (b) define entity-scoped audit views over the org-scoped table.

**Acceptance criteria:**
- Supports up to 50 legal entities per customer
- Currency conversion uses ERP-provided rates, not third-party rates
- Cross-entity views respect per-entity permissions
- Per-entity rules do not leak across entities
- Per-entity audit log scoping enforced at query time, not application time

**Out of scope:** intercompany transaction handling (lives in Recon); consolidation reporting (lives in FP&A workflow).

---

### Module 10 — Onboarding

A new customer goes from contract signed to first invoice processed in days, not weeks.

**Components:**
- Onboarding checklist: every step from connecting integrations to going live, with clear status
- Self-serve setup: customer can complete most steps without Clearledgr intervention
- Sample data mode: customer can run sample invoices through the system before going live with real data
- Integration health checks: confirms each integration is correctly configured before allowing go-live
- Default rule sets: pre-built starter rules for common patterns
- Default exception type configurations: pre-built for the standard exception types
- **Optional staging tier** (enterprise only — Booking.com class): full separate org with separate ERP sandbox connection, mirrored config from production. Staging is a deployment concern, not a UI concern; called out so it's not lost.

**Acceptance criteria:**
- Mid-market customer (Cowrywise class) self-serves end-to-end onboarding in under 4 hours of leader time
- Enterprise customer (Booking.com class) onboards in under 5 days with one Clearledgr-guided session
- Sample data mode does not contaminate production data
- Health checks catch 90% of common misconfigurations before go-live
- Staging tier (when enabled) provisions in under 30 minutes from "request access"

**Out of scope:** white-glove onboarding service (offered separately, not a product feature); migration tooling from competitor products (post-GA based on demand).

---

### Module 11 — Settings and Account Management

The plumbing of the dashboard. Every product has these and customers expect them to be solid.

**Components:**
- Account profile: company name, primary contact, billing contact
- Billing: subscription tier, usage, upcoming invoice, payment method, billing history
- Notifications: per-user notification preferences across email, Slack, in-app
- **Org-level escalation policy**: "if an exception sits >24h, page the on-call admin." Policy-level, not user-level. Composes with approval rules from Module 3.
- API keys: customer-side API keys for their own integrations
- Plan management: upgrade/downgrade tier, add or remove workflow modules (AR, Recon, Close as they ship) — see Pricing/tier mapping below
- Data export: full account data export (CSV/JSON) for portability

**Acceptance criteria:**
- Plan changes take effect within 5 minutes
- Data export of full account completes for accounts up to 1M invoices
- API keys can be created, scoped, rotated, revoked
- Org-level escalation policy fires within 1 minute of threshold breach

**Out of scope:** self-serve plan downgrade past a feature usage threshold (requires support); marketplace integrations or third-party apps.

---

## What we deliberately do NOT build for GA

These come up. Each is a "no" for GA, with reasoning.

**Team performance scoring.** No leaderboards, productivity rankings, per-approver speed metrics. The agent does the work. People handle approvals and exceptions, both of which are judgment, not throughput. Scoring is wrong philosophically and politically charged with the buyer.

**Operator UI in the dashboard.** Operators work in Gmail, Slack, Teams, NetSuite Suitelet panel, or SAP Fiori extension. The dashboard does not have an "approve invoices" view for operators. Building it would undermine the surface architecture.

**Custom report builder.** Months of work for marginal value. Five well-designed standard reports beat a flexible builder customers struggle to use.

**Vendor portal / vendor self-service.** Vendors interact with the customer's AP team via email. Building a vendor portal requires a different security model, separate legal review, and meaningful product surface area. Post-GA.

**Sophisticated ML-based fraud detection.** Real ML fraud detection requires training data we won't have for 12+ months. Basic rule-based fraud signals are in scope. Sophisticated detection ships when we have the data.

**Mobile native app.** Approvers handle approvals in Gmail or Slack on mobile. Leaders use the dashboard from desktop. Mobile-responsive web covers the gaps. **Acceptance criterion: end-to-end approve-from-mobile-Slack flow works on iPhone and Android.**

**Customer-configurable dashboards.** Fixed views in v1. Customisation creates support burden disproportionate to value.

**Rule templates marketplace.** Customer-to-customer rule sharing requires governance, security review, and curation. Post-GA.

**SCIM provisioning.** Genuine enterprise feature, but typically requested at Series A scale of customer. Manual user provisioning with SAML covers GA needs.

**AI-generated insights or commentary on reports.** Nice-to-have, not core. Post-GA.

**Inbound webhooks for triggering workflows from external systems.** Reverses integration direction; significant security and architectural surface. Post-GA.

**Visual drag-drop rule builder (deferred to v1.5).** Module 3 ships with JSON-mode editor + 4 starter rule templates for GA. Visual builder is its own design doc and the single largest UI investment outside of GA.

---

## Architectural decisions

These shape the build and should be locked in early.

**Web app, mobile-responsive.** Single codebase. Preact + HTM stack (already shipping per memory). Test on iPad and large phone breakpoints; do not optimise for small phone in v1.

**Real-time updates via websocket or SSE.** The Live Operations view reflects agent actions as they happen, not on refresh. Stat cards and exceptions queue update in near real time.

**Same backend as the surfaces.** The dashboard, Gmail integration, Slack integration, SuiteApp, SAP integration, and Teams integration all hit the same backend. Workflow state is single source of truth. Surfaces are views over it.

**Workflow-agnostic rule engine.** The rule engine processes rules for any workflow class. AP is what ships in the UI for v1; adding AR is surfacing rules for AR in the UI, not extending the engine.

**Multi-tenant by construction.** Every row in the database has `organization_id`. Cross-tenant queries forbidden at the data layer, enforced by middleware, not application code. Non-negotiable for SOC 2.

**Auth: SAML SSO in core auth path from day 1.** Even before customers turn it on, the auth model supports SAML. Adding it for a specific customer is configuration, not refactor.

**Audit log: append-only at DB level today; hash-chained + WORM-backed in Module 7's hardening pass.**

**Permission model: role-based with attribute extensions.** Standard RBAC for the six base roles; ABAC extensions for per-entity, per-amount, per-vendor scoping.

**Soft-delete everything.** Customers may want to recover deleted vendors, rules, users. Hard deletes only via support intervention.

**Feature flags from day 1.** Even within GA, some features (custom roles, SIEM integration, staging tier) ship behind flags so they can be enabled per customer or per tier.

---

## Data model — new entities

The dashboard introduces or formalizes six entities. Schemas below are minimum-viable; columns may grow during build.

### `rules`
```
id                  TEXT PRIMARY KEY
organization_id     TEXT NOT NULL
entity_id           TEXT                   -- nullable; entity-scoped if set
workflow            TEXT NOT NULL          -- 'ap' for v1
priority            INTEGER NOT NULL       -- evaluation order
conditions_json     JSONB NOT NULL         -- { all_of: [...], any_of: [...] }
actions_json        JSONB NOT NULL         -- [{type: 'route_to_role', role: 'ap_manager'}]
status              TEXT NOT NULL          -- 'active' | 'paused' | 'archived'
version             INTEGER NOT NULL       -- monotonic per (org, rule_name)
created_by          TEXT NOT NULL
created_at          TIMESTAMPTZ NOT NULL
updated_at          TIMESTAMPTZ NOT NULL
```

### `box_exceptions` (already exists per migration v43; surfacing in dashboard scope)
Already shipped: `id, box_id, box_type, organization_id, exception_type, severity, reason, raised_at, raised_by, resolved_at, resolved_by, resolution_note, metadata_json`. No schema change needed.

### `vendor_extensions`
```
vendor_id           TEXT PRIMARY KEY       -- foreign key to ERP vendor master ID
organization_id     TEXT NOT NULL
verified_ibans_json JSONB                  -- list of {iban, verified_at, verified_by, source}
fraud_flags_json    JSONB                  -- list of {flag_type, raised_at, severity, note}
custom_routing_json JSONB                  -- vendor-specific routing override
agent_confidence    REAL                   -- rolling avg from prior bills
last_synced_from_erp_at TIMESTAMPTZ
```

### `custom_roles`
```
id                  TEXT PRIMARY KEY
organization_id     TEXT NOT NULL
name                TEXT NOT NULL
description         TEXT
permissions_json    JSONB NOT NULL         -- list of permission tokens
based_on            TEXT                   -- one of the standard roles, optional
created_by          TEXT NOT NULL
created_at          TIMESTAMPTZ NOT NULL
UNIQUE (organization_id, name)
```

### `audit_events` (already exists per migration v50; canonical table)
Already shipped: `id, box_id, box_type, event_type, prev_state, new_state, actor_type, actor_id, payload_json, external_refs, idempotency_key, source, correlation_id, workflow_id, run_id, decision_reason, governance_verdict, agent_confidence, organization_id, ts`. No schema change needed for v1; v2 adds hash-chain columns.

### `webhook_subscriptions` (already exists; surfacing in dashboard scope)
```
id, organization_id, url, event_types (json), secret, is_active, description, created_at, updated_at
```

### `escalation_policies`
```
id                  TEXT PRIMARY KEY
organization_id     TEXT NOT NULL
trigger_type        TEXT NOT NULL          -- 'exception_age' | 'invoice_age_in_state'
threshold_minutes   INTEGER NOT NULL
target_role         TEXT                   -- ROLE_* enum
target_user_id      TEXT
target_channel      TEXT NOT NULL          -- 'email' | 'slack' | 'teams'
status              TEXT NOT NULL          -- 'active' | 'paused'
created_at          TIMESTAMPTZ NOT NULL
```

---

## State diagrams

### AP Item lifecycle (canonical — already in `clearledgr/core/ap_states.py`)

```
                  [received]
                       │
                       ▼
                  [validated]──────────────┐
                       │                   ▼
                       ├───►[needs_info]──►[snoozed]
                       │         │            │
                       ▼         │            ▼
              [needs_approval]◄──┘     [validated|...]
                       │
                       ├──►[approved]──►[ready_to_post]──►[posted_to_erp]
                       │                                       │
                       │                                       ├──►[reversed] (terminal)
                       │                                       │
                       │                                       └──►[closed] (terminal)
                       │
                       └──►[rejected]──►[closed] (terminal)
```

Terminals: `closed`, `reversed`, `rejected`. State machine validation enforced at the data layer in `ap_store.update_ap_item` (per audit P1 fix).

### Exception lifecycle (`box_exceptions` table)

```
[raised]──►[acknowledged]──►[in_progress]──►[resolved]
   │            │                │
   │            ▼                │
   │       [reassigned]──────────┤
   │            │                │
   └──►[escalated]────────────────┘
                                 │
                                 ▼
                            [resolution_note + resolved_by + resolved_at]
```

Backed by `box_exceptions.resolved_at` (NULL = open) + `severity` + `metadata_json` (carries assignment chain).

### User onboarding flow

```
[invited]──►[accepted]──►[mfa_enrolled]──►[active]
   │                                          │
   ▼                                          ▼
[expired_invite]                         [offboarded]
   │                                          │
   ▼                                          ▼
[reinvited]                              [audit_log_only]
```

---

## Pricing / tier mapping

| Feature / Module | Free trial | Starter | Growth | Enterprise |
|---|---|---|---|---|
| Modules 1, 2, 5, 11 (core) | ✓ | ✓ | ✓ | ✓ |
| Module 3 (rules — JSON + templates) | 5 rules | 25 rules | unlimited | unlimited |
| Module 3 visual rule builder (v1.5) | — | — | ✓ | ✓ |
| Module 4 (vendors + fraud signals) | ✓ basic | ✓ | ✓ | ✓ |
| Module 6 (users) | 3 users | 15 users | 50 users | unlimited |
| Module 6 SAML SSO | — | — | ✓ | ✓ |
| Module 6 custom roles | — | — | 5 roles | 10 roles |
| Module 7 audit log search/export | 30-day retention | 1-year | 3-year | indefinite |
| Module 7 SIEM webhook | — | — | ✓ | ✓ |
| Module 7 hash-chain + WORM (v2) | — | — | — | ✓ |
| Module 8 reports | 3 of 5 | all 5 | all 5 + scheduled email | all 5 + scheduled email + API |
| Module 9 multi-entity | 1 entity | 3 entities | 10 entities | 50 entities |
| Module 10 staging tier | — | — | — | ✓ |
| Module 11 API keys | — | 1 key | 5 keys | unlimited |

Pricing levels themselves (annual contract values) are owned by Mo + sales; this matrix is feature-gate scope only.

---

## Build sequence (revised)

Approximate ordering. Some can be parallelised; some have hard dependencies. **Sprint estimates revised upward from v1's 16 sprints to 18 sprints** based on Module 3 (visual builder deferred but JSON-editor still 3 sprints), Module 6 (SAML 2 sprints), Module 7 (v1 audit 1 sprint, v2 hardening separate).

**Phase 1 — Foundation (sprints 1–4)**
- Module 5: Integration Setup (must come first; nothing works without integrations)
- Module 6: Users, Roles, Permissions (with SAML in core auth path)
- Module 7 v1: Audit Trail (must be in place before any user-facing module ships, so all actions are logged from day 1) — **this is mostly already shipped per memory; sprint 1–2 is wiring + UI**
- Multi-tenancy infrastructure (already shipped — verification only)
- Workflow-agnostic rule engine architecture

**Phase 2a — Vendors + multi-entity (sprints 5–8)**
- Module 4: Vendor Management
- Module 9: Multi-Entity and Multi-Currency

**Phase 2b — Rules (sprints 9–12) — own phase due to size**
- Module 3 v1: JSON editor + starter templates + test mode + version history + conflict detection
- Module 3 v1.5 (visual builder) is post-GA

**Phase 3 — The leader's daily work (sprints 13–15)**
- Module 1: Live Operations
- Module 2: Exception Detail and Resolution (with batch resolution + harness reasoning panel)

**Phase 4 — Oversight (sprint 16)**
- Module 8: Reports (with scheduled email delivery)

**Phase 5 — Operational polish (sprints 17–18)**
- Module 10: Onboarding (with self-serve flow, sample data, default rule sets, optional staging)
- Module 11: Settings and Account Management
- SOC 2 Type 1 readiness
- Performance optimisation
- Mobile-responsive QA
- Security review (penetration test)

**Total estimate (revised):** ~18 sprints / 36 weeks for a 3-engineer team focused on the dashboard. Realistic risk-adjusted: 40 weeks. The 4 weeks of buffer goes against (a) SAP BTP certification delays, (b) NetSuite SuiteApp review, (c) penetration test findings.

**Module 7 v2 hardening (hash chain + WORM) is its own design + 2 additional sprints, sequenced after GA.**

---

## Risk register

Ten risks ranked by impact × likelihood. Each has a mitigation that should be active by the named phase.

| # | Risk | Likelihood | Impact | Mitigation | Active by |
|---|---|---|---|---|---|
| 1 | NetSuite SuiteApp review takes 6+ weeks (their queue, not ours) | High | High | Submit for review at end of Phase 1; budget the wait into Phase 5 buffer; ship NetSuite-CSV-fallback for early customers | Phase 1 close |
| 2 | SAP BTP certification + XSUAA cert rotation policy | High | High | Engage BTP partner early; design `Module-7-Audit-Hardening.md` with cert rotation in mind | Phase 2a |
| 3 | Visual rule builder demand exceeds JSON-editor + templates | Medium | High | Ship Phase 2b with JSON; reserve Phase 6 (post-GA) for visual builder; sales messaging anchors on the templates | Phase 2b |
| 4 | Hash-chain backfill on existing `audit_events` rows (Module 7 v2) breaks idempotency-key UNIQUE | Medium | High | Design pass before any backfill code; chain over a NEW column; original `idempotency_key` untouched | Module 7 v2 design |
| 5 | ERP API rate limits surface during multi-entity bulk sync | Medium | Medium | Token bucket per `(org, erp_type)`; circuit breaker; surface to leader in Module 5 | Phase 2a |
| 6 | Customer ERP customizations break field-mapping defaults | High | Medium | Module 5 custom field mapping UI is bounded but flexible; `Module-5-Field-Mapping-Catalog.md` lists supported field types | Phase 1 |
| 7 | Data warehouse decision for reports (materialized views vs. read replica vs. analytics DB) | Medium | Medium | Decision in open questions; default to materialized views for v1 and revisit at scale | Phase 4 |
| 8 | SAML edge cases per IdP (especially Azure AD attribute mapping) | High | Low | Design doc names IdPs in scope; integration tests per IdP; manual onboarding session for first 5 enterprise customers | Phase 1 |
| 9 | Audit table size at year-2 customer scale (>10M rows / org) | Low | High | Partition by `(organization_id, ts month)`; warm storage tier for >12 month-old rows; surface retention config in Module 7 | Module 7 v2 |
| 10 | FX rate provenance for multi-currency reports — ERP-supplied rates may not match audit-side requirements | Medium | Low | Explicit rate-source field on every conversion; audit log records the rate + source per posted invoice | Phase 2a |

---

## Open questions log

Twelve unanswered design questions surfaced now so they don't surprise mid-build. Each must be answered by the named phase.

| # | Question | Owner | Decision needed by |
|---|---|---|---|
| 1 | Materialized views vs. read replica vs. analytics DB for reports? | Suleiman | Phase 4 start |
| 2 | Hash-chain root publication strategy: transparency log? Customer-supplied pinning? Both? | Suleiman + Mo | Module 7 v2 design |
| 3 | Per-entity audit log scoping: new `entity_id` column (with backfill) OR query-time view filter? | Suleiman | Phase 2a start |
| 4 | Which exact SAML IdPs are GA-supported? (Default proposal: Azure AD, Okta, Google Workspace SAML, OneLogin, generic) | Mo | Phase 1 start |
| 5 | JIT provisioning rules for SAML — auto-create with which default role? Auto-deactivate after N days idle? | Mo | Phase 1 start |
| 6 | Real-time update protocol — websocket vs. SSE vs. long-poll? | Suleiman | Phase 3 start |
| 7 | Module 2 "Ask the agent" — Claude-grounded? Cached responses? What's the privacy posture for cross-customer prompt leakage? | Mo + Suleiman | Phase 3 start |
| 8 | Module 4 fraud signal default thresholds — ship customer-blank or with sane defaults (and which)? | Mo | Phase 2a start |
| 9 | Module 6 custom role limit (10 per customer) — is this enforceable across self-serve, or does Enterprise tier remove the cap? | Mo | Phase 1 start |
| 10 | Module 7 retention — indefinite by default? Or 7-year default with opt-in extension? Compliance counsel input needed. | Mo | Phase 1 start |
| 11 | Module 8 scheduled email — sent from Clearledgr's mail infrastructure or via the customer's connected Gmail? Ownership of failed delivery? | Mo | Phase 4 start |
| 12 | Module 10 staging tier — separate Railway environment per Enterprise customer, or namespace-based partition in production? | Suleiman | Phase 5 start |

---

## GA readiness criteria

Two sets: **engineering criteria** (build is done) and **buyer-side criteria** (the dashboard is shippable to a paying customer).

### Engineering criteria

1. All eleven modules pass acceptance criteria above.
2. SOC 2 Type 1 certification is complete (audit log, access controls, change management, encryption at rest and in transit).
3. Performance: 99% of dashboard pages load in under 3 seconds at typical customer scale (up to 5,000 invoices in flight).
4. Reliability: 99.9% uptime over a 30-day rolling window.
5. Security: penetration test completed by reputable third party with no critical findings open.
6. All Open Questions answered, all Risk Register items at "active" mitigation status.
7. Per-module appendix kept current with any changes between v2 of this doc and GA.

### Buyer-side criteria

1. A mid-market customer (Cowrywise class) can self-serve onboard, configure rules, and run AP through the system without Clearledgr support beyond initial setup.
2. An enterprise customer (Booking.com class) can deploy the SuiteApp or SAP integration, configure their multi-entity rules, federate identity via SAML, and stream audit logs to their SIEM.
3. The 5-minute demo script (below) runs cleanly without engineering intervention.
4. A first-time leader can find their daily-attention items within 30 seconds of landing on Module 1.
5. A CFO can defend any past audit decision to an external auditor by walking the audit trail backward, in under 5 minutes per invoice.

Once both sets are met, the dashboard ships GA and the next iteration brings AR online.

---

## Demo script (5 minutes)

The path that proves the dashboard is GA-quality.

1. **0:00–1:00 — Module 1 Live Operations.** Land on the dashboard. Three stat cards (in-flight, awaiting approval, agent exceptions). Exception queue with two actionable items at the top. Approver workload strip shows "Tobi has 8 waiting, oldest 5 days." System status footer green. *Prove: leader knows what needs attention in 30 seconds.*

2. **1:00–2:30 — Module 2 Exception Detail.** Click into the top exception. Bill detail. 3-way match panel shows PO matched, GRN missing. **Agent reasoning panel: "I escalated because the GRN is not yet recorded against PO 4471-A. The vendor invoice arrived before goods receipt. Confidence: 0.62. Governance verdict: should_execute_with_human."** Action: "Send to Receiving for GRN" with one click. *Prove: the harness is legible.*

3. **2:30–3:15 — Module 3 Approval Rules.** Open a rule from the templates. JSON editor showing the under-$1K-auto-approve template. Test mode: paste a sample invoice, see the rule trace. Save. Version history shows the prior rule, one-click revert. *Prove: configuration is auditable and recoverable.*

4. **3:15–4:00 — Module 7 Audit Trail.** Search for the invoice from step 2. Every event shown: agent extraction, validation, exception raised, governance verdict, "send to Receiving" action. SIEM webhook delivery icon green. Export to CSV. *Prove: every action is queryable, exportable, defensible.*

5. **4:00–5:00 — Module 4 + 9 Multi-entity.** Switch entity selector from "EU Operations" to "US Operations." Same dashboard, US-scoped. Vendor list shows US-only vendors. *Prove: enterprise-ready, not a startup demo.*

If this script can't run cleanly, scope is wrong.

---

## Dashboard success metrics

How we know the dashboard is doing its job post-GA.

| Metric | Target | Measured how |
|---|---|---|
| **Activation**: % of customers self-serve-onboarded (vs. requiring Clearledgr-guided setup) | ≥70% by month 3 post-GA | Module 10 onboarding completion event in `audit_events` |
| **Engagement**: % of leader users active weekly | ≥80% | Login + at-least-one-action event in last 7 days |
| **Time-to-first-resolved-exception**: from first login to first Module 2 resolution | ≤24h for 75% of customers | Activity timestamps |
| **Time-to-first-rule-saved**: from first login to first Module 3 rule save | ≤7 days for 60% of customers | Rule creation event |
| **Audit log read rate**: % of customers who view Module 7 in any given month | ≥40% | Module 7 page view events (signal of compliance posture) |
| **Custom-role usage rate**: % of Enterprise customers using custom roles | ≥30% | Custom role count > 0 |
| **SAML SSO adoption**: % of Enterprise customers federating identity | ≥80% within 60 days of contract start | SAML config presence |
| **Retention proxy**: of customers active in month N, % active in month N+3 | ≥90% | Activity rollup |

---

## Out-of-scope for the dashboard, but in scope for the broader product

These belong elsewhere in the system, not in the dashboard. Listed here so we don't accidentally build them in the dashboard:

- **Operator-facing approval UI** — lives in Gmail (sidebar), Slack (slash commands and threads), Teams (cards), and ERP (NetSuite Suitelet panel + SAP Fiori extension)
- **Bill capture from email** — happens in the agent backend, triggered by Gmail or forwarded email
- **Payment execution** — initiated through the dashboard or ERP, executed via the customer's payment rail
- **Vendor self-service** — no vendor portal; vendors interact via email

---

## Per-module appendix

One page per module. **Templates are filled in for Modules 1, 3, and 7 as worked examples; Modules 2, 4, 5, 6, 8, 9, 10, 11 use the same structure and will be filled in during the build.** Each appendix is the engineer's source of truth: wireframe link, API endpoints, data model deltas, test scenarios, dependencies.

### Appendix template

```
**Wireframe:** [Figma link / TBD]

**API endpoints used:**
- METHOD /path — purpose

**Data model deltas:**
- New tables / columns introduced by this module
- Pre-existing tables this module reads from

**Test scenarios:**
- Scenario 1: ...
- Scenario 2: ...

**Dependencies:** other modules / infrastructure that must be in place first.
```

---

### Appendix — Module 1: Live Operations

**Wireframe:** TBD — Figma file under `Dashboard/Module 1` workspace.

**API endpoints used:**
- `GET /api/workspace/bootstrap` — current org + user + dashboard_stats (already shipped)
- `GET /api/ap/items?state=needs_approval&order=age_desc&limit=20` — exceptions queue
- `GET /api/workspace/approver-workload` — net-new; per-approver active assignment counts
- `GET /api/workspace/integrations` — system status footer
- `WS /api/workspace/live` — net-new websocket; pushes stat-card deltas + new exception arrivals

**Data model deltas:** none — reads from `ap_items`, `audit_events`, `box_exceptions`.

**Test scenarios:**
1. Loads in <2s with 5,000 in-flight items (perf test).
2. Exception arrives via Gmail-push → appears in queue within 30s without user-initiated refresh.
3. Approver on PTO (per `users.metadata.pto_until`) renders with strikethrough in workload strip.
4. Switching entity via selector re-scopes all four stat cards in <1s.
5. ERP integration error surfaces in footer within 10 minutes of detection.

**Dependencies:** Module 5 (integrations live), Module 6 (entity-scoped role check), Module 7 v1 (audit-events read path).

---

### Appendix — Module 3: Approval Rules Configuration (v1)

**Wireframe:** TBD — JSON editor + template gallery + test-mode panel.

**API endpoints used:**
- `GET /api/workspace/rules?organization_id=X` — list rules
- `POST /api/workspace/rules` — create
- `PUT /api/workspace/rules/{id}` — update (writes new `policy_versions` row + audit event)
- `DELETE /api/workspace/rules/{id}` — soft-delete
- `POST /api/workspace/rules/test` — test mode; runs sample invoice through rule set
- `GET /api/workspace/rules/{id}/versions` — version history
- `POST /api/workspace/rules/{id}/revert/{version}` — one-click revert
- `GET /api/workspace/rules/templates` — starter templates

**Data model deltas:**
- New: `rules` table (schema in Data Model section above)
- Pre-existing: `policy_versions` (migration v45) — stores rule history
- New: `audit_events` rows with `event_type='rule_created' | 'rule_updated' | 'rule_deleted' | 'rule_reverted'` and box_type='rule'

**Test scenarios:**
1. Save rule that conflicts with existing rule → conflict surfaced at save time, not after.
2. Test mode with sample invoice → returns evaluation trace showing which rule matched (or default routing if none).
3. Rule update → in-flight invoices unaffected unless `apply_retroactively=true`.
4. Revert to prior version → new `policy_versions` row + audit event; downstream surfaces pick up via cache invalidation.
5. Save rule with malformed JSON → 422 with specific schema-validation error.
6. Non-admin user attempts rule create → 403.

**Dependencies:** Module 6 (admin role check), Module 7 v1 (audit event chain), `policy_versions` table (already shipped per memory).

---

### Appendix — Module 7: Audit Trail (v1)

**Wireframe:** TBD — searchable table + export button + SIEM config side-panel.

**API endpoints used:**
- `GET /api/workspace/audit?organization_id=X&from=&to=&event_type=&actor=&limit=100&cursor=` — search
- `GET /api/workspace/audit/{event_id}` — single-event detail with linked entity context
- `POST /api/workspace/audit/export` — async CSV / PDF export job
- `GET /api/workspace/audit/exports/{job_id}` — poll export status + download URL
- `POST /api/workspace/audit/siem-webhook` — register / update SIEM webhook
- `GET /api/workspace/audit/siem-deliveries?limit=50` — recent delivery log

**Data model deltas:**
- Pre-existing: `audit_events` (already shipped, with `governance_verdict` + `agent_confidence` from migration v50).
- Pre-existing: append-only triggers (verified per audit P5).
- New (Module 7 v2 only — separate phase): `audit_events.prev_hash`, `audit_events.entry_hash`, `audit_chain_roots` table for periodic root publication.

**Test scenarios:**
1. Event written within 5s of action (assertion in canonical AP flow integration test).
2. UPDATE / DELETE attempts on `audit_events` raise (already covered in `test_audit_events_table_is_append_only`).
3. Search for date range with 100K matching events returns first page in <3s.
4. Export of full year completes within 60s and produces CSV that round-trips to PDF.
5. SIEM webhook with retry on 5xx; delivery log surfaces failures within 1h.
6. Even Clearledgr engineering cannot UPDATE an event row via the api (covered today; v2 strengthens at the storage layer).

**Dependencies:** none — Module 7 v1 is foundational; Modules 1–11 read from it.

---

### Appendices for Modules 2, 4, 5, 6, 8, 9, 10, 11

Same structure as above. **Filled in during the build** (each takes ~30 minutes once the wireframe lands). Engineers should not start implementation on a module whose appendix is still empty — that's the gate that keeps the spec ahead of the code.

---

## Revision log

| Revision | Date | Author | Summary |
|---|---|---|---|
| v1 | April 2026 | Mo Mbalam | Initial scope draft. 11 modules, 6 principles, 16-sprint estimate. |
| v2 | 2026-04-28 | Mo Mbalam (with Claude-assisted review) | Adds: "What's different" header, harness reference in Module 2, role-mapping table to codebase enums, Module 7 split (v1 / v2), Module 3 v1 vs v1.5 split (visual builder deferred), data model section, state diagrams, pricing/tier matrix, risk register (10), open questions log (12), revised build sequence (16→18 sprints), buyer-side GA criteria, demo script, success metrics, per-module appendix template (3 worked + 8 stubbed). |

---

## Contact

Mo Mbalam, CEO — mo@clearledgr.com
Suleiman Mohammed, CTO — suleiman@clearledgr.com

For dashboard scope questions or change requests, raise in #product-dashboard on Slack or directly with Suleiman.
