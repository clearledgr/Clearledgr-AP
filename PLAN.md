# Clearledgr Finance Agent — AP v1 GA Doctrine and Launch Spec (Canonical)

## Document Metadata
- **Status:** Canonical doctrine + contracts + launch-gates spec for Clearledgr AP v1
- **Last updated:** 2026-03-19
- **Owner:** Product + Engineering
- **Scope:** Accounts Payable (AP) v1 only
- **Supersedes:** prior AP-first execution-layer plan variants that used ambiguous "launch" terminology

## Summary
This document is the single source of truth for Clearledgr AP v1 doctrine, product positioning, engineering contracts, and GA launch gates.

Clearledgr is the **execution layer for finance operations**, embedding AI agents into the tools finance teams already use to execute finance workflows end-to-end. AP is its first production workflow. The agents are embedded in email, spreadsheets, ERPs, and communication tools — they don't just surface information, they perform the work. It is not a generic automation builder and not a standalone platform dashboard. AP v1 starts inside Gmail, routes approvals through Slack and Teams (or directly in email), posts to ERP systems as the system of record, and provides policy-governed orchestration plus an auditable execution trail.

### Locked product decisions
1. **Clearledgr is the execution layer for finance operations** (external positioning). AP is the first production workflow; new finance workflows are added over time.
2. **AP v1 starts in Gmail** (inbox-native intake and triage).
3. **Approvals happen in Slack and Teams at v1 GA** (co-equal channel parity requirement at GA).
4. **ERP remains the system of record**; Clearledgr orchestrates and executes.
5. **Clearledgr provides policy + orchestration + execution + audit**.
6. **"Streak-like" is internal UX doctrine only**, not external positioning.
7. **WhatsApp and Telegram are not product surfaces** and are out of product scope by default.
8. **"v1 launch" means GA launch**, not pilot launch.
9. **ERP commitment at v1 GA is phased with a defined connector set**: **NetSuite, QuickBooks, Xero, and SAP** are in the GA connector scope, but each connector is independently enabled only after passing the same adapter readiness gates.
10. **Execution is automated and audit-safe**; mutating/high‑risk actions are human‑confirmed by default unless policy explicitly allows autopilot.
11. **Clearledgr runs one finance agent runtime with AP as the first production skill**; future workflows expand as skills on the same runtime rather than separate product runtimes.
12. **Durability claims are truth-in-runtime claims**: AP v1 default durable orchestration backend is `local_db`; Temporal is optional and must only be claimed when actually enabled.
13. **Initial rollout geography is Europe and Africa first**; wider regional expansion comes after EMEA launch stability targets are met.
14. **Operator-facing time standard is Europe/London** for shared team coordination; persisted system timestamps remain UTC.

---

## 1. Product Doctrine

### 1.1 What Clearledgr is
Clearledgr is a **Finance AI Agent** embedded in the tools finance teams already use. It executes finance workflows end‑to‑end with policy controls and auditability.

For AP v1, Clearledgr:
1. Detects invoice and AP requests from email (Gmail primary).
2. Extracts and validates fields against internal and connected systems.
3. Routes approvals to finance decision-makers in Slack and Teams.
4. Posts approved invoices to ERP systems.
5. Records immutable, queryable execution and approval breadcrumbs.

### 1.2 What Clearledgr is not
Clearledgr is not:
1. A generic automation builder or no‑code workflow tool.
2. A consumer/personal AI assistant.
3. A "Streak for finance" product category.
4. A standalone AP dashboard required for daily operator work.
5. A consumer messaging workflow product (WhatsApp/Telegram excluded).
6. A services-led outsourced accounting firm.

### 1.3 Why AP is the wedge
AP is the right first workflow because:
1. Invoice intake and exceptions already originate in email.
2. Approvals are already distributed across chat and email contexts.
3. ERP posting is high-value and measurable.
4. AP creates reusable primitives for later workflows:
   - deterministic state machine
   - policy enforcement
   - adapter orchestration
   - audit trail
   - idempotent writes

### 1.4 Embedded surfaces doctrine
AP v1 is intentionally split across the surfaces finance teams already use:
1. **Gmail** = operational context, thread-level status, exceptions, and next action.
2. **Slack / Teams** = approval and escalation decision surfaces.
3. **ERP** = system of record for posted AP transactions.
4. **Clearledgr backend** = policy checks, workflow orchestration, execution engine, audit.

### 1.5 Internal UX doctrine ("Streak-like", internal only)
The phrase "Streak-like" is internal shorthand for UX principles, not external product positioning.

It means:
1. Work happens in context (the email thread).
2. Users should not need another daily tab for routine AP operations.
3. UI should remain light and decision-first.
4. Backend reliability carries the product trust burden.
5. Progressive disclosure hides technical detail until needed.

---

## 2. AP v1 Release Taxonomy (Pilot vs GA)

### 2.1 Terminology (canonical)
1. **v1 Pilot** = pre-GA validation phase with design partners.
2. **v1 GA** = official v1 launch.
3. **v1 launch** = **v1 GA** only.

This distinction is mandatory in all future docs and tickets.

### 2.2 v1 Pilot definition (pre-GA)
v1 Pilot exists to validate:
1. Extraction quality and confidence gating.
2. AP state machine behavior in real operator workflows.
3. Approval UX in Slack/Teams.
4. ERP posting reliability and connector readiness.
5. Operator trust and exception handling.

Pilot may use staged connector/channel enablement. Pilot readiness does not imply GA parity.

### 2.3 v1 GA definition (launch)
v1 GA is the first generally available AP release and requires:
1. Gmail AP workspace as the primary operator surface.
2. Slack and Teams approval parity (co-equal channels).
3. ERP posting is production-grade for the GA-supported ERP set (NetSuite, QuickBooks, Xero, SAP in current scope), with each connector enabled only once its adapter passes readiness gates.
4. Reliability and trust gates in Section 7.
5. Launch acceptance criteria in Section 8.

### 2.4 Non-goals for AP v1 (pilot and GA)
AP v1 does not include:
1. Reconciliation workflows.
2. Month-end close workflows.
3. FP&A aggregation as a primary shipped workflow.
4. A standalone dashboard for daily AP operations.
5. Consumer messaging channels (WhatsApp/Telegram).
6. Outlook inbox intake in GA scope (explicitly de-scoped; Gmail is the only inbox surface for AP v1 GA).

### 2.5 Gmail-native operations shell (release taxonomy context)
Clearledgr uses Gmail as the primary product shell for AP v1. The thread panel remains the daily AP execution UI, while Gmail-native routed pages handle setup and operational administration.

Gmail-native admin/operator responsibilities:
1. Integration setup and diagnostics.
2. Policy configuration.
3. Team access and onboarding.
4. Subscription/usage visibility.
5. Health checks and required-action surfacing.

Onboarding spine decision (locked):
1. Use an admin-first onboarding/account-management backbone for all tenant setup and reconnect flows.
2. OAuth integration setup is initiated from authenticated backend APIs surfaced inside Gmail-native pages.
3. Gmail thread work UI must not become a cluttered onboarding/configuration surface.
4. Gmail shell default pinned nav remains intentionally sparse: `Home`, `Pipeline`, `Connections`.
5. `Activity` and other secondary pages remain available from Home or pinning, not as default clutter.
6. `Pipeline` is the default AP queue/process surface with AP-first slices, finance-native filters/sorts, and direct thread <-> pipeline reopening.
7. Saved pipeline views are persisted per authenticated user and organization; `Home` may surface pinned views and finance-native starter views.
8. Gmail authorization is explicit from inline CTAs; the extension must not auto-launch Gmail OAuth at startup.

---

## 3. UX Doctrine and Operator Experience

### 3.1 Gmail thread card doctrine (primary AP operator experience)
The Gmail thread is the AP workflow control surface. Each invoice thread (or grouped invoice entity) should present a compact AP card that shows:
1. **Invoice identity strip** (vendor, invoice number, amount, due date, PO status)
2. **One status badge**
3. **Plain-language blockers** (for example: waiting on approver, PO missing, review extracted fields)
4. **One primary next action** (single prioritized action)
5. **Compact secondary actions**
6. **Evidence checklist**
7. **Collapsed audit disclosure**
8. Inline history stays capped in-thread: surface the most important key events first and keep background/system activity collapsed by default.

### 3.2 Progressive disclosure rules (required)
To avoid a "cluttered plugin" outcome:
1. Show decision-relevant information first.
2. Collapse technical details (IDs, payloads, traces) by default.
3. Collapse source lists and deep context sections by default unless they contain blockers.
4. Hide empty sections instead of showing placeholder panels.
5. Present one active invoice workspace at a time; use compact navigation for queue traversal.

### 3.3 Gmail UI anti-patterns (must avoid)
1. Rebuilding a full AP dashboard inside Gmail.
2. Showing multiple large sections with empty placeholder copy.
3. Surfacing raw system identifiers in the primary decision view.
4. Rendering long lists when a focused active-item workspace is available.
5. Mixing operator actions and admin setup/config in the same UI surface.

### 3.4 Slack and Teams approval card doctrine
Slack and Teams cards are decision surfaces, not full workflow dashboards.

Each approval card must show:
1. Invoice summary (vendor, invoice #, amount, due date)
2. Validation summary (PO/budget status, confidence status, key exceptions)
3. Requested action and reason
4. Available actions (`approve`, `reject`, `request_info`)
5. Link back to Gmail context / AP item details
6. Clear result feedback (decision accepted, duplicate click ignored, action blocked, etc.)

### 3.5 Gmail-native admin pages doctrine (non-daily workflow)
Gmail-native routed pages are for setup and operations, not routine AP processing.

They must provide:
1. Integration setup (Gmail, Slack, Teams, ERP)
2. Approval channel configuration
3. Policy configuration and version visibility
4. Health and required-action diagnostics
5. Team access / invites
6. Plan/subscription visibility
7. A lightweight `Home` launch hub for readiness, recent activity, and quick links

Operational shell rules:
1. `Home`, `Pipeline`, and `Connections` are the default pinned pages.
2. `Activity` is secondary and available from Home or user pinning.
3. Health/debug/admin pages are role-gated and never default pinned.
4. `Pipeline` must ship finance-native saved views: starter views by default, personal pinned views persisted per authenticated user/org, and Home shortcuts into those views.
5. Routed pages may support future-skill groundwork, but AP remains the only production-grade workflow in launch doctrine.

### 3.6 Gmail thread work doctrine
AP v1 Gmail UX is a single operator workspace and must not regress to mixed diagnostic surfaces:

1. **Work panel (`Clearledgr AP`)** is action-first and decision-focused.
2. Gmail must not render KPI telemetry, batch controls, raw agent events, or debug panels.
3. Work panel must keep operator-critical context above fold:
   - invoice identity strip
   - status badge + blockers
   - primary action + compact secondary actions
4. Evidence and audit in Work panel must use progressive disclosure by default.
5. Ops/monitoring/batch/debug must be provided via Gmail-native routed pages with admin/operator access gating, not embedded in the thread work card.
6. Reason capture for reject/override/budget/escalation actions must use inline reason sheet UX, not native browser dialogs (`prompt/confirm`).
7. Extension shipping bundle must enforce `src -> dist` parity in CI; stale `dist` or forbidden legacy Gmail strings are release blockers.
8. Legacy extension popup/options/demo surfaces are not part of AP v1 runtime UX; if retained, they must live under docs archival paths and not ship.
9. Work audit copy must be backend-owned (`operator_title`, `operator_message`, `operator_severity`, optional `operator_action_hint`) for canonical material events.
10. Gmail auth must be initiated from inline CTAs in the product shell, not from automatic startup popups.

---

## 4. AP Execution Contract (Server-Enforced, Deterministic)

### 4.1 Canonical AP state machine (server-enforced)
No client may force AP item state changes directly. All transitions are validated server-side against legal paths.

#### Primary path
- `received -> validated -> needs_approval -> approved -> ready_to_post -> posted_to_erp -> closed`

#### Exception paths
- `validated -> needs_info`
- `needs_approval -> rejected`
- `ready_to_post -> failed_post`
- `failed_post -> ready_to_post` (explicit retry path only, with audit)
- `needs_info -> validated` (after required data received/reviewed)

#### Resubmission semantics
1. Rejected item is terminal.
2. Resubmission creates a new AP item (not state resurrection) with linkage metadata:
   - `supersedes_ap_item_id`
   - `supersedes_invoice_key`
   - `resubmission_reason`

### 4.2 Deterministic validation and policy checks (pre-write guardrail)
Before approval routing and before posting, workflow must enforce:
1. Field presence/format checks
2. Duplicate detection and merge/linking logic
3. PO/receipt matching checks (where data is available)
4. Budget checks and policy compliance gates
5. Approval policy resolution (who must approve, what thresholds apply)
6. Confidence gate evaluation for critical extracted fields

No external mutating write (ERP post, irreversible approval completion path if applicable) may proceed without passing applicable deterministic checks or explicit override rules.

### 4.3 Extraction confidence gating (launch-critical)
Critical-field confidence gating is mandatory for AP v1.

#### Critical fields (default)
1. `vendor`
2. `invoice_number`
3. `amount`
4. `due_date` (when present/required by workflow/policy)

#### Default behavior
1. Low-confidence critical fields block posting.
2. Blocked items surface `requires_field_review` and `confidence_blockers`.
3. Override requires explicit justification and audit event.
4. Thresholds are policy-configurable, but a default threshold must be defined and enforced.

#### Default threshold
- **Critical-field confidence threshold default:** `95%` (unless org policy overrides)

### 4.4 Exception handling and escalation
Exceptions must be explicit, reason-coded, and visible in-context.

Minimum requirements:
1. `exception_code` and `exception_severity` on AP item surfaces
2. Deterministic routing rules for:
   - duplicate risk
   - low confidence
   - no PO / mismatch
   - budget overage
   - posting failure
3. Clear operator next action for each exception state
4. Audit events for exception detection, override, and resolution

### 4.5 Idempotency and dedupe (required)
#### Dedupe
1. Deduplication key must use normalized invoice identity and source evidence (invoice number and/or attachment hash strategy where applicable).
2. Cross-thread duplicates must link to one invoice-centric AP item when confidence/validation rules allow.

#### Idempotency
1. Approval actions must be idempotent across duplicate callbacks/clicks.
2. ERP posting must require an idempotency key.
3. Retry behavior must not create duplicate ERP transactions.
4. Idempotency keys and retry attempts must be auditable.

### 4.6 ERP posting contract (workflow-level)
ERP posting is API-first. If browser fallback is supported, it is gated and audited.

Requirements:
1. Post only from legal state (`ready_to_post`) unless a documented exception path exists.
2. Return stable ERP reference on success.
3. Persist normalized success/failure result.
4. On failure, transition deterministically to `failed_post` with audit.
5. Browser fallback (if enabled) requires:
   - preview
   - confirmation
   - policy allowance
   - audit event of planned and executed action

### 4.7 Audit coverage contract (non-negotiable)
Every AP state transition and every external mutating action must generate an audit event.

Required audit coverage includes:
1. Validation outcomes (including policy/budget/PO checks)
2. Approval requests and decisions
3. Overrides (confidence, policy, budget, posting fallback)
4. ERP post attempts and results
5. Retry attempts
6. Exception escalations and resolutions

---

## 5. Channel Contracts (Gmail / Slack / Teams)

### 5.1 Gmail contract (primary operator surface)
Gmail is the intake and triage surface for AP v1.

Gmail responsibilities:
1. Thread-level AP status visibility
2. Exception and next-action visibility
3. Extracted field display and targeted review prompts
4. Source evidence navigation (linked emails/threads/attachments)
5. Audit breadcrumb display (progressive disclosure)
6. Operator commands that do not bypass server state/policy enforcement

#### Gmail worklist/workspace contract (backend API)
`GET /extension/worklist`

Required item fields (minimum):
1. `id`
2. `state`
3. `vendor_name`
4. `invoice_number`
5. `amount`
6. `due_date`
7. `exception_code`
8. `exception_severity`
9. `confidence`
10. `next_action`
11. `source_count`
12. `primary_source`
13. `merge_reason`
14. `has_context_conflict`
15. `requires_field_review`
16. `confidence_blockers`

### 5.2 Slack contract (approval and exception decisions)
Slack is a co-equal GA approval channel.

Slack requirements:
1. Signature verification and replay protection
2. Common approval action contract support
3. Idempotent action handling
4. Result feedback to user/card
5. Audit/event propagation for all actions and failures

### 5.3 Teams contract (approval and exception decisions)
Teams is a co-equal GA approval channel and must support the same approval semantics as Slack.

Teams requirements:
1. Verified callback/security model
2. Common approval action contract support
3. Idempotent action handling
4. Result feedback to user/card
5. Audit/event propagation for all actions and failures

### 5.4 Common Slack/Teams approval action contract (canonical)
Approval action payload (normalized):
1. `ap_item_id`
2. `run_id`
3. `action` (`approve`, `reject`, `request_info`)
4. `actor_id`
5. `actor_display`
6. `reason` (required for `reject`; required where policy demands for overrides)
7. `source_channel` (`slack` or `teams`)
8. `source_message_ref`
9. `request_ts`
10. `idempotency_key` (or equivalent unique action correlation)

Behavioral requirements:
1. Duplicate callbacks are safe and do not duplicate transitions/posts.
2. Invalid or unauthorized actions are rejected and auditable.
3. Result states are reflected back to channel surfaces (or explicitly marked stale/expired).

---

## 6. ERP Support Contract (Phased: GA Connector Set)

### 6.1 Supported ERP list for AP v1
- **v1 Pilot → v1 GA connector scope:** **NetSuite, QuickBooks, Xero, SAP** (each adapter is API-first with optional gated browser fallback where policy allows).
- **Connector enablement rule:** each ERP is enabled independently only after readiness gates pass for that connector/tenant scope.
- **Post‑GA expansions:** additional ERPs beyond this set as separate, versioned adapters.

### 6.2 Definition of “supported” (per ERP)
“Supported” does **not** mean “connector exists.” It means:
1. Connectivity/auth readiness check
2. Vendor lookup (and optional create if policy permits)
3. Account/GL lookup as required
4. AP bill/invoice create flow
5. Submit/finalize behavior (or equivalent)
6. Stable ERP reference return on success
7. Status lookup/verification
8. Idempotent posting + retry safety
9. Structured error normalization (mapped to canonical error codes)
10. Audit coverage verification for posting and retries

### 6.3 Canonical ERP post response contract
ERP post response must include:
1. `erp_type`
2. `status`
3. `erp_reference` (required on success)
4. `idempotency_key`
5. `error_code` (normalized, on failure)
6. `error_message` (operator-safe)
7. `raw_response_redacted` (stored where allowed)

### 6.4 Browser fallback policy (API-first, gated fallback)
Default policy:
1. API-first for ERP writes.
2. Browser fallback only if:
   - API path is unavailable/incomplete for a required capability,
   - policy explicitly permits it,
   - a preview is generated,
   - human confirmation is captured,
   - the action is fully audited.

### 6.5 Adapter expansion rule (how we add ERPs safely)
- Add ERPs **one at a time** behind the adapter interface.
- Each ERP must pass a readiness checklist (sandbox E2E, idempotency, error mapping, audit coverage, runbook) **before** being marketed as supported.

## 7. Reliability and Trust Requirements (GA Launch Gates)

### 7.1 Extraction confidence gates
Launch gate requirements:
1. Critical-field confidence gate is active and enforced server-side.
2. Low-confidence critical fields block posting and require review.
3. Overrides require explicit justification and audit.
4. Field-level blockers are visible in Gmail and channel actions where applicable.

### 7.2 Deterministic state enforcement
Launch gate requirements:
1. 100% server-side transition validation against legal paths.
2. Illegal transition attempts are rejected and logged.
3. No client or integration callback can set arbitrary state directly.

### 7.3 Idempotent ERP posting and action handling
Launch gate requirements:
1. ERP posting requires idempotency keys.
2. Duplicate approval callbacks/clicks are safe.
3. Retrying after transient failures does not create duplicate ERP transactions.
4. Retry attempts and outcomes are auditable.

### 7.4 Audit completeness and immutability
Launch gate requirements:
1. Every AP state transition is audited.
2. Every external mutating action is audited.
3. Approval decisions capture actor, timestamp, and reason where applicable.
4. Audit completeness is a launch gate, not a post-launch hardening item.
5. Audit entries are append-only and protected from routine mutation paths.

### 7.5 Exception handling and escalation clarity
Launch gate requirements:
1. Exceptions are reason-coded and severity-ranked.
2. Each exception state has a deterministic next action or escalation path.
3. Operators can see why the item is blocked and how to resolve it.
4. Slack/Teams decisions and ERP failures surface clear operator-safe outcomes.

### 7.6 Observability and operator support requirements
Launch gate requirements:
1. Correlation IDs across intake, approval, posting, and audit events
2. Metrics for:
   - approval latency
   - post failure rate
   - retry rate
   - extraction correction rate
   - exception rate
3. Per-tenant/channel/ERP diagnostics in ops/admin surfaces
4. Actionable health status for auth/token expiry, connector degradation, and callback failures

### 7.7 Failure mode behavior (must be defined and tested)
AP v1 GA must define and validate behavior for:
1. ERP connector auth expiry / disconnect
2. Slack/Teams callback duplication or delayed delivery
3. Gmail rescan/reprocessing after backend outage
4. Posting failure after approval
5. Low-confidence extraction discovered late in workflow
6. Browser fallback failure (if fallback path is enabled)

### 7.8 Runtime/durability truth-in-claims
Launch gate requirements:
1. Runtime status surfaces must expose the active durability backend (`local_db` vs Temporal when available).
2. Product/docs/operator messaging must not imply Temporal semantics when `temporal_available=false`.
3. Any non-durable fallback behavior must be feature-gated and auditable.

---

## 8. Metrics and Launch Success Criteria

### 8.1 Primary launch metric (AP v1)
**Primary metric:** Cycle-time reduction for AP invoice processing vs customer baseline.

### 8.2 Supporting metrics (required)
Track at minimum:
1. Extraction correction rate (critical fields)
2. Approval turnaround time
3. Posting success rate by ERP (QB/Xero/NetSuite/SAP)
4. Exception rate and exception resolution time
5. Duplicate-prevention effectiveness (duplicate post prevention)
6. Audit coverage completeness
7. Retry success rate after transient failures

### 8.3 Pilot-to-GA graduation criteria
Pre-GA pilot must demonstrate:
1. Stable end-to-end AP flow on real customer data patterns
2. Predictable exception handling and operator trust
3. Proven channel action reliability (Slack and Teams)
4. Proven connector readiness for all four GA ERPs
5. Acceptable extraction correction burden

Pilot success alone is not sufficient unless GA parity and reliability gates are met.

### 8.4 GA launch acceptance criteria (minimum)
AP v1 GA launch requires all of the following:
1. Gmail + Slack + Teams channel contracts operational and validated
2. GA-supported ERP connector(s) readiness gates passed for the enabled GA connector scope (NetSuite, QuickBooks, Xero, SAP as applicable)
3. State machine enforcement proven in tests and staging validation
4. Audit completeness validated for transitions and external actions
5. Idempotent posting and duplicate action handling validated
6. Runbooks and on-call/operator procedures documented
7. Rollback controls validated
8. Gmail doctrine tests prove sparse nav, thread-card limits, explicit auth initiation, role-gated routes, runtime-backed AP actions, and pipeline slice/view persistence
9. Manual product review checklist is completed for thread card, pipeline slices, Home lightness, route gating, Gmail auth flow, Slack/Teams -> Gmail roundtrip, and ERP post roundtrip

### 8.5 Post-GA monitoring and rollback triggers
Post-GA launch operations must define thresholds for:
1. Elevated post failure rate
2. Connector-specific degradation
3. Callback security/verification failures
4. Audit write failures or audit coverage gaps
5. Duplicate posting incidents

Rollback controls must support at least:
1. Per-tenant ERP posting disablement (intake and approvals continue)
2. Per-channel action disablement with safe fallback path
3. Connector-specific feature gating without disabling all AP intake

---

## 9. Rollout Plan (Pilot -> GA)

### 9.1 Internal validation
1. Dogfood with internal/staging tenants
2. Mock and sandbox ERP validation
3. Gmail + Slack + Teams callback reliability testing
4. State machine / idempotency / audit coverage tests

Exit criteria:
1. No unauthorized state transitions in validation logs
2. No duplicate ERP posts for identical idempotency keys
3. Audit coverage checks pass for required flows

### 9.2 Design-partner pilot (pre-GA)
Purpose: validate operator UX, extraction quality, and connector behavior in real workflows.

Pilot activities:
1. Onboard design partners
2. Measure baseline AP cycle time
3. Run guided rollout with feature gates
4. Collect exception and correction patterns
5. Harden runbooks and support procedures

Pilot may use staged enablement, but must still progress toward GA parity validation.

### 9.3 Channel parity validation (Slack + Teams)
Before GA:
1. Slack and Teams must each pass the common approval action contract test matrix
2. Both channels must support:
   - `approve`
   - `reject`
   - `request_info`
   - result feedback / stale action handling
3. Duplicate callback/action handling must be verified in both channels

### 9.4 ERP readiness validation (phased adapters)
Before GA:
1. GA-enabled ERP adapter(s) for the release scope (from NetSuite, QuickBooks, Xero, SAP) must complete readiness checklists
2. Required capabilities for AP posting must be enabled and validated
3. Connector-specific limitations must be documented and must not break workflow semantics
4. Readiness evidence must be archived for signoff

### 9.5 GA launch
GA launch occurs only when:
1. Section 7 reliability/trust gates pass
2. Section 8 GA acceptance criteria pass
3. Channel and ERP parity validation is complete
4. Rollback controls are tested

### 9.6 Controlled expansion after GA
After GA:
1. Expand tenant cohorts and volumes in controlled increments
2. Tighten SLOs and error budgets
3. Continue AP hardening before expanding to new finance workflows
4. Reuse AP primitives for future finance skills (for example disputes, collections, close support) only after AP stability targets are sustained

---

## 10. Doc Reconciliation Matrix (Canonical Doc Governance)

### 10.1 Canonical document roles
This matrix prevents contradictions across strategy, backlog, and assessment docs.

| Document | Role | Canonical for | Not canonical for | Update trigger |
|---|---|---|---|---|
| `PLAN.md` | Doctrine + contracts + launch spec | Product doctrine, AP v1 release taxonomy, channel/ERP parity, GA launch gates | Point-in-time implementation status, backlog sequencing | Product/engineering doctrine change, launch-gate changes |
| `TODO_BACKLOG.md` | Execution backlog | Work sequencing, ownership, implementation streams, release buckets | Product doctrine, launch definitions | Backlog grooming / implementation progress |
| `VISION.md` | Product direction framing | Long-range positioning and runtime expansion direction | Canonical launch gates or release taxonomy | Strategy updates |
| `docs/GA_LAUNCH_READINESS_TRACKER.md` | Point-in-time readiness report | Current launch-readiness findings and evidence checklist status | Canonical product doctrine or future release commitments | New readiness audit run |

### 10.2 Required cross-doc consistency rules
1. `PLAN.md` defines release terminology (`pilot`, `GA`, `launch`).
2. `TODO_BACKLOG.md` must not redefine launch doctrine; it maps work to the doctrine in `PLAN.md`.
3. `VISION.md` may describe future direction and expansion, but must not override `PLAN.md` contracts.
4. `docs/GA_LAUNCH_READINESS_TRACKER.md` must be treated as a dated report and cite the `PLAN.md` version/assumptions it assessed.

### 10.3 Current cross-doc notes (as of 2026-02-25)
1. `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md` is a point-in-time readiness assessment and must be interpreted against this `PLAN.md` revision.
2. Findings in dated readiness docs are valuable as engineering risk inputs, but those documents are **not** the canonical source for AP v1 doctrine or launch definitions.
3. `/Users/mombalam/Desktop/Clearledgr.v1/TODO_BACKLOG.md` and `/Users/mombalam/Desktop/Clearledgr.v1/VISION.md` should be interpreted as execution and strategy inputs aligned to this `PLAN.md`.

### 10.4 Terminology normalization (mandatory)
To avoid scope drift and false completion claims, all future docs and tickets must distinguish:
1. **Connector exists** vs **operational parity enabled**
2. **Pilot-ready** vs **launch-ready (GA)**
3. **Supported in code path** vs **validated and enabled in production**
4. **Embedded UX doctrine** vs **product positioning**

---

## Canonical Public APIs / Interfaces / Types (Spec-Level Requirements)

This section is normative for implementation and QA. It defines the interfaces that AP v1 depends on, even if implementation details evolve.

### A. Gmail extension worklist/workspace APIs
1. `GET /extension/worklist`
   - Returns invoice-centric AP items for Gmail operator workflow
   - Must include status, confidence, exception, next action, and source linkage fields

2. `GET /api/ap/items/{ap_item_id}/context`
   - Returns normalized cross-system context (email, approvals, ERP, web/portal, etc.)
   - Must expose blocking conditions and operator-safe summaries

3. `GET /api/ap/items/{ap_item_id}/audit`
   - Returns audit breadcrumbs and detailed events for progressive disclosure
   - Must include backend-normalized operator contract fields (`operator_code`, `operator_title`, `operator_message`, `operator_severity`, `operator_next_action`) so embedded clients do not own event/reason wording maps

### B. Approval action interfaces (Slack/Teams)
Approval action handling must normalize Slack and Teams actions into the common contract in Section 5.4 and enforce:
1. Signature/security verification
2. Idempotency
3. Server-side policy and state validation
4. Audit propagation

### C. ERP posting interfaces
ERP posting behavior must provide:
1. Idempotent posting with required idempotency key
2. Stable ERP reference on success
3. Normalized error codes on failure
4. Safe retry semantics
5. Optional preview/confirm contract for fallback/high-risk paths (if applicable)
6. Provider-agnostic adapter contract for GA ERPs (NetSuite, QuickBooks, Xero, SAP):
   - `validate(payload)`
   - `post(organization_id, bill, idempotency_key, ...)`
   - `get_status(organization_id, external_ref)`
   - `reconcile(organization_id, entity_id)`

### C.1 Finance agent runtime contract (skill-dispatch)
The runtime must support canonical typed contracts for all skills:
1. `SkillRequest`:
   - `org_id`
   - `skill_id`
   - `task_type`
   - `entity_id`
   - `correlation_id`
   - `payload`
2. `SkillResponse`:
   - `status` (`completed` / `blocked` / `awaiting_human` / `failed`, with skill-specific detail allowed)
   - `recommended_next_action`
   - `legal_actions`
   - `blockers`
   - `confidence`
   - `evidence_refs`
3. `ActionExecution`:
   - `entity_id`
   - `action`
   - `preview`
   - `reason` (optional)
   - `idempotency_key`
4. `AuditEvent`:
   - `event_id`
   - `org_id`
   - `skill_id`
   - `entity_id`
   - `action`
   - `actor`
   - `outcome`
   - `timestamp`
   - `correlation_id`
   - `evidence_refs`

### C.2 Skill package manifest contract (promotion gate)
Every runtime skill must publish a capability manifest with these required sections:
1. `state_machine`
2. `action_catalog`
3. `policy_pack`
4. `evidence_schema`
5. `adapter_bindings`
6. `kpi_contract`

Runtime and API requirements:
1. `GET /api/agent/intents/skills` must include each skill manifest and manifest validation status.
2. `GET /api/agent/intents/skills/{skill_id}/readiness` must evaluate promotion gates against measured metrics where available.
3. Skills without runtime metrics support must be explicitly reported as `manifest_only` (not silently treated as GA-ready).

### C.3 Admin Ops readiness + calibration interfaces
Admin/operator ops surfaces must expose connector and learning readiness through authenticated APIs:
1. `GET /api/admin/ops/connector-readiness`
   - Returns per-connector readiness rows for NetSuite/QuickBooks/Xero/SAP.
   - Must include checklist status, connector configuration status, rollback blocks, and readiness blocker reasons.
2. `GET /api/admin/ops/learning-calibration`
   - Returns latest persisted tenant calibration snapshot derived from operator outcomes.
3. `POST /api/admin/ops/learning-calibration/recompute`
   - Recomputes and persists calibration snapshot with version/timestamp metadata.
4. Ops endpoints above are role-gated (`owner`/`admin`/`operator`) and are not rendered in Gmail Work UX.

### D. AP item type requirements (minimum fields)
AP item surfaces and APIs should support these canonical fields (minimum; implementation may include additional fields):
1. `id`
2. `state`
3. `vendor_name`
4. `invoice_number`
5. `amount`
6. `due_date`
7. `confidence`
8. `exception_code`
9. `exception_severity`
10. `next_action`
11. `source_count`
12. `merge_reason`
13. `has_context_conflict`
14. `requires_field_review`
15. `confidence_blockers`

### E. Audit event type requirements (minimum fields)
Audit events should support:
1. `id`
2. `organization_id`
3. `ap_item_id`
4. `timestamp`
5. `actor_type`
6. `actor_id`
7. `action` / `event_type`
8. `prev_state`
9. `new_state`
10. `result`
11. `payload_json` (redacted where needed)
12. `correlation_id` / `idempotency_key`

---

## GA Launch Gate Defaults (Decision Defaults)

These defaults apply unless superseded by a later explicit revision to this `PLAN.md`.

### Extraction and validation defaults
1. Critical-field confidence gating is enabled by default.
2. Default critical-field threshold is 95% confidence.
3. Low-confidence critical fields block posting and require review.
4. Overrides require justification and audit logging.

### State machine and idempotency defaults
1. All AP state transitions are server-enforced.
2. Illegal transitions are rejected and logged.
3. ERP posting requires idempotency keys.
4. Duplicate approval callbacks/actions must not duplicate transitions or posts.

### Audit and traceability defaults
1. Every state transition is audited.
2. Every external mutating action is audited.
3. Approval decisions capture actor, timestamp, and reason where applicable.
4. Audit completeness is a GA launch gate.

### Channel defaults
1. Gmail is the primary AP intake/triage operator surface.
2. Slack and Teams are co-equal GA approval channels.
3. Gmail-native routed pages handle setup/ops workflows; the thread panel remains the daily AP workflow UI.

### Runtime/durability defaults
1. AP v1 durable orchestration defaults to the DB-backed `local_db` runtime.
2. Temporal is optional and only treated as active when runtime status reports `temporal_available=true`.
3. Non-durable retry/fallback behavior must be explicitly gated and auditable.

### ERP defaults
1. AP v1 GA connector scope is **NetSuite, QuickBooks, Xero, SAP**, with per-connector readiness gating.
2. Additional ERPs beyond this set are enabled post‑GA once their adapter passes readiness gates.
3. API-first posting is the default execution path.
4. Browser fallback, if supported, is gated by preview + confirmation + audit + policy.

---

## Appendix: Implementation Guidance for Engineers (Non-Canonical Sequencing)

This appendix is intentionally lightweight. The canonical sequencing and ownership should live in `TODO_BACKLOG.md`. It exists only to prevent misinterpretation of doctrine as implementation order.

Recommended sequencing themes:
1. State machine enforcement and audit completeness
2. Extraction confidence gating and review UX
3. Slack/Teams parity hardening
4. ERP parity hardening (QB/Xero/NetSuite/SAP)
5. Ops health, observability, and rollback readiness

If `TODO_BACKLOG.md` conflicts with this `PLAN.md`, update the backlog to match this document or submit a doctrine revision to `PLAN.md`.
