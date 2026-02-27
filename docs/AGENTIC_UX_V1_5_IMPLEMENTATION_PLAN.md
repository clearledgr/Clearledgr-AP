# Clearledgr Agentic UX v1.5 Implementation Plan

## Purpose

This plan closes the gap between:

- the **agentic execution infrastructure** already present in Clearledgr AP v1, and
- the **user-facing perception** (currently still too workflow/status-heavy vs visibly agentic).

This is a **product-expression plan**, not a rewrite of the AP engine.

It preserves the AP v1 doctrine in `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`:

1. Gmail remains the primary operator surface
2. Slack/Teams remain approval/decision surfaces
3. ERP remains system of record
4. deterministic workflow, policy, and audit controls remain authoritative
5. agent behavior is visible and useful, but never bypasses server enforcement

## Codebase Reality Check (Verified)

Clearledgr already has real agentic infrastructure. The issue is mostly **how little of it is surfaced to operators by default**.

### What is already real in the codebase

1. **Agent runtime/orchestration**
   - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py`
   - Includes reflection/correction, workflow execution, and durable retry queue processing

2. **Browser-agent tool layer (policy/preview/confirmation/result)**
   - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`
   - `preview_command`, `dispatch_macro`, `enqueue_command`, `submit_result`

3. **Agent session APIs**
   - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
   - Session create/get, command preview/dispatch, result submission, fallback completion, policy APIs

4. **Gmail sidebar already has an agent execution section**
   - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
   - `renderAgentActions()` renders session state, next action, preflight preview, history, and approval buttons

5. **ERP fallback reconciliation**
   - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py`
   - Browser fallback preview/confirmation and AP-state reconciliation paths are implemented

### Why it still does not feel agentic

1. The Gmail UI emphasizes AP workflow state more than agent intent/execution.
2. The most visibly agentic controls are hidden behind debug-oriented affordances.
3. There is no strong per-invoice “agent timeline” narrative (intent -> tool -> result -> next step).
4. There is no simple operator command/delegation flow (e.g., “chase approver”, “collect W-9”, “retry post with preview”).
5. Proactive actions (nudges, drafts, suggested next steps) are not prominently surfaced as agent work.

## Product Goal (v1.5)

Make Clearledgr feel like an **embedded AP agent** that executes work in Gmail, while preserving:

- deterministic AP state machine controls
- human approval gates
- policy enforcement
- auditability and traceability

## Implementation Status (Current)

### Landed (initial AX1 pass)

1. Gmail agent section now presents an **Agent timeline** (instead of generic execution status wording)
2. Timeline UI merges:
   - browser-agent/session events
   - AP audit breadcrumbs
3. Timeline groups recent activity into operator-readable buckets:
   - planned
   - awaiting approval
   - executing
   - completed
   - blocked / failed

This is the first perception-oriented UX pass. It improves visibility, but AX1 is not fully complete until timeline summaries and grouping are validated against more production event shapes.

### Landed (initial AX2 pass)

1. Gmail agent section now includes a non-debug **Agent actions (preview-first)** menu
2. Initial bounded intents are wired to existing infrastructure:
   - preview/run ERP fallback macro
   - preview W-9 collection macro
   - route approval
   - explain blockers
3. Macro previews/runs and blocker explanations reuse the same visible agent summary card in the Gmail thread panel

This is an initial AX2 pass. It improves delegation UX, but AX2 is not fully complete until we add higher-level intent phrasing, richer action recommendations, and stronger UX coverage tests.

### Landed (AX2 refinement pass)

1. AX2 buttons now use operator-facing intent phrasing (less macro-oriented wording)
2. Agent Actions are dynamically ranked by AP state, `next_action`, and blockers
3. Gmail agent section shows a visible “Recommended next move” summary above bounded actions

This improves the delegate-and-watch experience without adding new backend behavior. AX2 is still not fully complete until richer suggestions (including draft replies/nudges) and UI coverage tests are added.

### Landed (AX2 refinement pass 2)

1. AX2 Agent Actions now includes a state-gated **Draft vendor info request** intent (non-debug)
2. The agent action reuses the existing `/extension/needs-info-draft/{id}` backend flow and Gmail compose prefill behavior
3. Recommendation ranking prioritizes vendor-draft actions when the AP item is in `needs_info` / `request_info`

This closes the biggest AX2 gap around vendor follow-up delegation while keeping the action bounded and auditable. Remaining AX2 work is primarily nudges/recommendations polish plus UI coverage tests.

### Landed (AX2 completion pass)

1. Added **Run vendor docs check (W-9)** intent to complete preview/run parity for collection actions
2. Added a bounded **Agent command bar** that maps operator phrases (e.g. “retry ERP posting”, “draft vendor info request”, “explain blockers”) to supported intents only
3. Command bar and action buttons now share the same bounded intent execution path (`runAgentIntent`) with existing policy/preview/confirmation rails

AX2 feature scope is now complete for v1.5. Remaining work for this area is UI coverage tests and future intent expansion (nudges/finance summaries) which are handled under AX3+.

### Landed (initial AX3 pass)

1. Blocked invoices now show an **Agent suggested next step** panel in Gmail (proactive recovery focus)
2. Added bounded proactive actions:
   - `Nudge approver(s)` (reuses approval re-route path as an initial nudge mechanism)
   - `Summarize exception for finance lead` (context + audit based summary card)
3. AX2 bounded command bar now recognizes proactive phrases like:
   - “nudge approver”
   - “summary for finance lead”

This is an initial AX3 pass focused on proactive visibility and one-click recovery support inside Gmail. Remaining AX3 work includes dedicated nudge delivery semantics (beyond re-route), finance-lead sharing/export actions, and richer auditable proactive outcomes.

### Landed (AX3 refinement pass)

1. `Nudge approver(s)` now uses a dedicated backend endpoint (`/extension/approval-nudge`) with AP audit events and real channel-level nudge semantics (Slack thread reply + Teams re-send best effort)
2. Added one-click `Share finance summary` action in Gmail Agent Actions / proactive panel
3. `Share finance summary` uses a dedicated backend endpoint (`/extension/finance-summary-share`) that prepares a finance-lead email draft and records an AP audit event for the share action

AX3 still has follow-on work (richer proactive recommendations, target-specific formatting polish, and UI coverage tests), but the core proactive action loop is now in place and auditable.

### Landed (AX3 refinement pass 2)

1. `Share finance summary` now supports multiple targets:
   - `email_draft`
   - `slack_thread`
   - `teams_reply`
2. Gmail Agent Actions exposes a visible **Finance summary target** selector so operators choose the delivery target before sharing
3. Slack/Teams summary shares are recorded as AP audit events with delivery results (`finance_summary_shared` / `finance_summary_share_failed`)

AX3 remains open for target-specific formatting polish and UI coverage tests, but the core multi-target proactive share flow is now in place.

### Landed (AX3 refinement pass 3)

1. Added a bounded `Preview finance summary share` Agent Action (and command-bar phrase mapping) that uses the same audited backend path with `preview_only=true`
2. `/extension/finance-summary-share` now returns target-specific previews before delivery for:
   - `email_draft`
   - `slack_thread`
   - `teams_reply`
3. Gmail Agent Actions renders a target-specific preview card (including payload preview) before sending, preserving the preview-first UX pattern

AX3 now has preview-first finance-summary sharing across supported targets. Remaining work at this point was final formatting polish and regression coverage.

### Landed (AX3 completion pass)

1. Added target-specific finance summary share previews across all supported targets with preview-first Gmail UX:
   - `email_draft`
   - `slack_thread`
   - `teams_reply`
2. Added backend regression coverage for AX3 endpoints:
   - dedicated approver nudges (`/extension/approval-nudge`)
   - finance summary share previews (`/extension/finance-summary-share`, preview mode)
3. Tightened Gmail preview rendering so operators can review target-specific payload content before sending
4. Added a lightweight Node-based InboxSDK UI helper/render test harness (AX1–AX3 coverage) to validate timeline grouping, bounded intent parsing/recommendations, and finance-share preview rendering
5. Added a browserless InboxSDK integration harness (fake DOM + mocked Gmail SDK APIs) that executes the real `inboxsdk-layer.js` module and covers:
   - bootstrap/sidebar mount lifecycle
   - InboxSDK handler registration (compose/thread/thread-row)
   - sidebar button event wiring
   - thread-row labeling + thread lifecycle callbacks

AX3 feature scope is now complete for v1.5. The remaining frontend testing gap is now narrower: real-browser E2E coverage (actual Gmail/InboxSDK runtime behavior), not basic DOM/event/Gmail-SDK integration regression coverage.

### Landed (AX4 initial pass)

1. Added a Gmail **Batch agent ops** section (still embedded in the sidebar, not a separate dashboard) with bounded preview/run controls for:
   - processing low-risk `ready_to_post` items (preview + run)
   - previewing failed-post retry candidates (preview-only in this initial pass)
   - nudging aging approvals (preview + run)
2. Preserved real agent/audit paths by reusing existing per-item flows:
   - ERP posting macro dispatch (`dispatchAgentMacro`) for low-risk batch posting
   - audited approval nudges (`nudgeApproval`) for aging approvals
3. Added AX4 helper/render coverage and browserless integration coverage for the new batch panel preview path.
4. Fixed a frontend integration-harness async loop exposed by AX4 tests by auto-seeding minimal `contexts/sources` in mocked queue updates (preventing repeated `ensureItemContext()` hydration loops during tests).

AX4 is now started with preview-first batch autonomy controls in Gmail. Remaining AX4 work is batch execution/result polish (especially retry/run semantics and richer per-item batch outcome summaries), not initial UX structure or regression coverage.

### Landed (AX4 refinement pass 1)

1. Enabled **Run retries** for `failed_post` batch candidates in the Gmail Batch Agent Ops panel using the canonical AP retry endpoint (`POST /api/ap/items/{id}/retry-post`) instead of a UI-only shortcut.
2. Added richer per-item batch outcome summaries for:
   - low-risk ERP macro dispatch runs
   - failed-post retry runs (posted vs re-queued vs failed)
   - approval nudge runs
3. Extended the InboxSDK browserless integration harness with AX4 retry-run coverage and per-item result assertions.

AX4 now supports real preview/run behavior for the most important batch ops (low-risk posting dispatch, failed-post retries, approval nudges) while preserving the same audited per-item execution paths. Remaining AX4 work is operator UX polish (result formatting/detail expansion) and optional batch controls (limits/selection policies), not core batch execution capability.

### Landed (AX4 refinement pass 2)

1. Added AX4 **batch execution policy controls** inside the Gmail sidebar batch panel:
   - max items (`3 / 5 / 10 / 20`)
   - optional amount cap (applies to both preview and run selection)
2. Batch previews and runs now show **policy-aware candidate counts** (`selected`, `amount-excluded`, `deferred by limit`) so operators can see why a batch is smaller than total candidates.
3. Batch result summaries now render **expandable per-item outcome details** (status + item label + result detail) instead of only aggregate line summaries.
4. Added **post-run refresh indicators** in batch summaries (e.g., posted / ready-to-post / failed-post counts after queue sync) so operators can confirm current state after the run completes.
5. Extended helper and browserless InboxSDK integration tests to cover:
   - policy filtering
   - retry-run per-item result rendering
   - refresh-indicator rendering

AX4 now has real batch execution capability plus the core operator trust UX (policy boundaries, per-item outcomes, and refresh-state confirmation). Remaining AX4 work is optional polish (selection presets, richer result formatting, batch-level undo/rollback semantics where applicable), not foundational batch autonomy behavior.

### Landed (AX4 refinement pass 3 / optional polish)

1. Added **selection presets** to the Gmail Batch Agent Ops panel:
   - `Queue order`
   - `Lowest risk first`
   - `Oldest first`
2. Batch result rendering now **groups per-item outcomes by result class** (`Successful`, `Needs follow-up`, `Failed`) with clearer status presentation for operators.
3. Added a **Rerun failed subset** action for AX4 batch result summaries (where retryable failures exist), reusing the same bounded batch execution path and canonical backend/AP endpoints.
4. Extended AX4 helper + browserless integration coverage to validate:
   - preset-aware selection behavior
   - grouped result rendering
   - rerun-failed-subset execution behavior

AX4 optional polish is now complete for v1.5. Remaining future work for this area is advanced controls (selection presets beyond current set, batch-level scheduling, or rollback semantics), not core UX or trust gaps.

### Landed (AX5 initial pass)

1. Added explicit **browser fallback timeline stage labeling** in the Gmail Agent Timeline using existing AP audit events:
   - API post failed / fallback unavailable
   - fallback preview generated
   - confirmation captured
   - runner executing
   - result reconciled (success/failure)
2. Added a per-invoice **Browser fallback status banner** in the Gmail thread card that surfaces:
   - current fallback stage
   - AP state linkage
   - ERP reference (when available)
   - redacted error evidence / fallback reason (when applicable)
3. Improved **Web context browser-event rows** so tool actions and recent fallback-related evidence events are human-readable (tool label + status + detail + timestamp)
4. Extended the frontend test stack for AX5:
   - helper/render coverage for fallback timeline labeling and fallback status banner rendering
   - browserless InboxSDK integration coverage proving fallback trust state appears in thread context + agent timeline with real module execution

AX5 is now started with the core trust UX in place. Remaining AX5 work is presentation polish (targeted wording/formatting tweaks) and real-browser Gmail/InboxSDK smoke coverage, not visibility of fallback state itself.

### Landed (AX5 polish pass)

1. Refined browser fallback timeline rows with **explicit stage progress chips** (e.g. `S4/5`, `S5/5`) so operators can scan fallback progress without reading raw audit labels.
2. Upgraded the Gmail **Browser fallback status banner** with:
   - stage progress (`Stage X of 5`)
   - stage label
   - trust note clarifying whether completion has been reconciled vs still awaiting runner callback
   - compact reached-stage chips for recent fallback progression
3. Improved Web-context browser event readability with:
   - status tone treatment
   - fallback-evidence tags for fallback-related browser actions/evidence capture
4. Extended AX5 helper + browserless InboxSDK integration coverage to assert:
   - stage progress labels
   - trust-note wording
   - fallback-evidence visibility in the Web tab

AX5 now covers both visibility and trust-oriented operator messaging for browser fallback in the Gmail sidebar. Remaining work for this area is primarily real-browser Gmail/InboxSDK smoke/E2E validation and optional wording polish based on pilot feedback.

### Landed (AX6 initial pass)

1. Extended `/api/ops/ap-kpis` (via `MetricsStore.get_ap_kpis()`) with an `agentic_telemetry` bundle covering:
   - straight-through vs human intervention rates
   - awaiting-approval timing
   - ERP browser-fallback rate
   - agent suggestion acceptance and manual-override-required rates
   - approval override rate
   - top blocker reasons (confidence/policy/budget/ERP/other)
2. Extended browser-agent metrics aggregation to expose transparent human-control telemetry (confirmation-required vs confirmed actions) used by AX6 KPI rollups.
3. Wired the Gmail debug KPI panel to render the new agentic telemetry block (including ratio-to-percent formatting fixes and blocker summaries) using the existing queue/KPI snapshot pipeline.
4. Added targeted AX6 test coverage:
   - backend `/api/ops/ap-kpis` assertion for `agentic_telemetry` payload shape/content
   - browserless InboxSDK integration coverage verifying Gmail KPI panel rendering for AX6 metrics

AX6 is now started with a working operator-facing KPI layer in Gmail and a backend telemetry bundle suitable for ops/admin surfaces. Remaining work is productization and surfacing polish (digests/admin views/positioning dashboards), not metric derivation.

### Landed (AX7 initial pass)

1. Added Slack/Teams approval-surface parity copy for agentic decision framing:
   - **Why this needs your decision** (budget / confidence / validation / duplicate-driven summary)
   - **What happens next** (action outcome guidance for approve/reject/request-info flows)
2. Added consistent **requested by agent on behalf of AP workflow** metadata on both channels.
3. Added explicit **source of truth** wording linking back to Gmail/AP context on both channels, including Teams `Open Gmail context` action parity with Slack’s Gmail link.
4. Routed Teams approval-card copy through the same workflow-derived approval context used for Slack, so channel copy stays aligned with real validation/budget/confidence signals instead of drifting.
5. Added targeted channel-card tests covering AX7 copy/metadata/link behavior.

AX7 is now started with the core agentic presentation parity in place for Slack and Teams decision surfaces. Remaining work is copy polish, richer “what happens next” per edge case, and pilot feedback tuning (not structural channel parity).

### Landed (AX6 productization pass)

1. Productized agentic telemetry beyond debug-only backend data by updating KPI digest builders consumed by ops surfaces:
   - Slack digest text/blocks now include AX6 metrics (fallback rate, suggestion acceptance, manual override rate, top blockers).
   - Teams digest card now includes an explicit **Agentic telemetry** section and blocker summary.
2. Gmail sidebar KPI section now shows a non-debug **Agentic snapshot** card (compact metrics + blockers) while preserving the richer debug panel when debug mode is enabled.
3. Added regression coverage for AX6 productized surfaces:
   - `/api/ops/ap-kpis/digest` payload assertions for Slack + Teams agentic metrics
   - Gmail browserless integration assertion for non-debug compact KPI rendering

AX6 now has both derivation and product surfacing paths in place. Remaining AX6 work is packaging these metrics into release dashboards and customer-facing reporting narratives, not core metric capture/rendering.

### Landed (AX7 polish pass)

1. Tightened Slack/Teams decision framing through shared workflow-derived copy:
   - clearer **why this needs your decision** summary synthesis from validation/confidence/budget/duplicate context
   - action-oriented **what happens next** lines
2. Preserved channel parity with consistent metadata and source-of-truth language while reducing drift risk by deriving copy from a single workflow helper.
3. Teams approval cards now include an explicit **Open Gmail context** action for parity with Slack’s Gmail link-back affordance.

AX7 now includes both structural parity and first-pass copy polish. Remaining AX7 work is mostly micro-copy tuning from pilot feedback and richer edge-case phrasing.

### Landed (Real Gmail/Chrome E2E smoke scaffold)

1. Added a manual-gated real-browser smoke test scaffold for the Gmail extension:
   - loads extension in Chrome (Playwright persistent context)
   - opens Gmail inbox URL
   - validates baseline runtime path
2. The smoke test is intentionally gated behind `RUN_GMAIL_E2E=1` and separate from deterministic local CI expectations.
3. Added explicit npm script:
   - `test:e2e-smoke`

This closes the “no real-browser path at all” gap with a practical starter harness while keeping deterministic CI stable. Full authenticated Gmail runtime assertions remain a pilot/staging follow-on.

### Landed (AX6 surfacing completion pass)

1. Expanded AX6 metrics into broader operator surfaces beyond ops digest/debug:
   - `/analytics/dashboard/{organization_id}` now includes `agentic_telemetry` and an `agentic_snapshot` summary.
   - `/api/admin/bootstrap` dashboard payload now includes the same `agentic_telemetry` + `agentic_snapshot` contract for admin/home surfaces.
2. Added backend regression coverage for both surfaces to ensure telemetry stays visible outside debug-only or ops-only pathways.

AX6 product surfacing is now complete for v1.5 implementation scope (derivation + digest + Gmail + analytics/admin contracts). Remaining work is launch packaging and external reporting narratives, not backend/UI contract gaps.

### Landed (AX7 edge-case copy completion pass)

1. Extended shared Slack/Teams approval copy to explicitly handle high-friction edge cases:
   - confidence-review-required approvals now explain confidence-override capture on approve
   - validation/policy blockers now produce request-info guidance specific to missing policy/evidence context
   - duplicate-risk paths now provide explicit reject semantics for confirmed duplicates
   - hard budget blocks now use stronger approve-override phrasing
2. Added targeted tests validating edge-case phrasing branches in the shared approval-copy helper.

AX7 copy coverage is now complete for planned v1.5 semantics (including richer “what happens next” paths for core edge conditions). Remaining tuning is pilot feedback wording refinement, not missing branch logic.

### Landed (Real Gmail/Chrome authenticated runtime assertion pass)

1. Upgraded the manual-gated E2E harness with authenticated runtime assertions (`GMAIL_E2E_ASSERT_AUTH=1`):
   - fails fast on Gmail sign-in pages when authenticated mode is requested
   - verifies extension service worker presence
   - verifies Clearledgr sidebar selectors mount in real Gmail runtime
2. Added optional screenshot capture output for pilot evidence collection.
3. Added explicit npm entrypoint:
   - `test:e2e-auth`

Real Gmail runtime validation now has an authenticated assertion path while still remaining opt-in/manual for local/staging execution.

### Landed (Experience + Agent quality pass)

1. Gmail decision workspace now includes an explicit **Operator brief** in-thread:
   - **What happened**
   - **Why this needs attention/decision**
   - **Best next step** + expected outcome
2. The brief synthesizes AP state, queue next-action, fallback status, and persisted agent reasoning into action-oriented guidance instead of forcing operators to infer from raw state labels.
3. Approval-surface reasoning quality improved in the shared workflow copy helper:
   - priority-weighted reason synthesis (budget/confidence/validation/PO/duplicate/vendor-queue context)
   - explicit `recommended_action_text` for deterministic operator guidance
   - Slack now renders **Recommended now** in approval blocks
   - Teams receives the same recommendation as the first **What happens next** line
4. Added regression coverage:
   - Gmail browserless integration test for operator brief rendering
   - workflow/channel tests for recommended-action copy and PO-context reasoning

This closes the current “experience + agent quality” delta for v1.5 implementation scope by improving decision legibility and recommendation quality without weakening deterministic controls.

### Landed (Learning-loop quality pass)

1. Added persistent vendor decision-feedback storage (`vendor_decision_feedback`) for per-tenant/per-vendor human outcomes:
   - approve / reject / request-info decisions
   - agent recommendation at decision time
   - override marker and outcome context
2. Wired workflow feedback capture into human decision paths:
   - `approve_invoice()` now records feedback and updates vendor profile outcome on successful post
   - `reject_invoice()` now records feedback and rejected terminal outcome linkage
   - `request_budget_adjustment()` now records request-info feedback signals
3. AP reasoning now consumes aggregated feedback summary in decision context:
   - feedback counts, override rate, strict/permissive bias, and approve→reject/request-info patterns
   - fallback path now becomes more conservative when strict human feedback is established
4. Added regression coverage for:
   - feedback summary aggregation/override pattern derivation
   - fallback behavior under strict feedback bias
   - end-to-end workflow feedback persistence from approve/reject/request-info actions

This closes the current learning-loop gap for v1.5 by ensuring human decisions are persisted and fed back into future recommendations instead of being only one-off action outcomes.

## Non-Negotiable Guardrails

1. Do not turn Gmail into a generic automation builder.
2. Do not replace deterministic AP workflow steps with opaque LLM-only behavior.
3. Do not move daily AP work into Admin Console.
4. Do not hide failures, blocks, or confirmation requirements.
5. Do not create channel-specific semantics drift between Slack and Teams.

## Implementation Strategy

Use a **thin product layer** on top of the existing agent/session/tooling infrastructure:

1. expose the agent timeline clearly
2. expose bounded command/delegation actions
3. add proactive agent outcomes (drafts/nudges/summaries)
4. improve trust signals (what happened, why, what next)

This is an additive UX and API contract improvement, not a platform reset.

## Workstreams (Sequenced)

## AX1. Gmail Agent Timeline (Primary Perception Fix)

### Goal

Make the agent’s work legible in the Gmail thread panel.

### Current reusable base

- `renderAgentActions()` in `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
- `GET /api/agent/sessions/{session_id}` in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
- `BrowserAgentService.get_session()` in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`
- AP audit/context APIs in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py`

### Changes

1. Replace generic “Execution status” presentation with a first-class **Agent Timeline** section.
2. Show event groups:
   - `planned`
   - `awaiting approval`
   - `executing`
   - `completed`
   - `blocked / failed`
3. Display human-readable summaries for:
   - extraction corrections
   - validation blocks
   - approval routing
   - ERP posting attempts
   - browser fallback preview/confirmation/results
4. Merge AP audit breadcrumbs and browser-agent events into a single operator-readable timeline (with details on demand).

### Code touchpoints

- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`

### Acceptance criteria

1. Operator can see what the agent did and what is blocked without opening raw payloads.
2. Browser fallback path is visible as a timeline event, not only an infrastructure detail.
3. Timeline status stays consistent with AP item state and audit trail.

### Tests

- Frontend rendering unit/snapshot tests (if available) or deterministic DOM tests for timeline grouping
- API contract tests for timeline payload shape
- Regression checks on existing agent session endpoints

## AX2. Bounded Agent Command Surface in Gmail (Delegate-and-Watch)

### Goal

Let operators delegate AP tasks in plain product language without turning the product into a chat bot.

### Current reusable base

- `dispatch_agent_macro`, `preview_agent_command`, `enqueue_agent_command` in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
- Browser macros in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`
- Gmail agent UI action buttons in `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`

### Changes

1. Add a small **Agent Actions** menu in Gmail (non-debug) for bounded intents:
   - Retry ERP post (preview first)
   - Collect W-9 (preview/run)
   - Request missing info draft
   - Re-route approval
   - Summarize blockers
2. Add dynamic recommendation ordering (based on AP state, `next_action`, and blockers) and present a clear recommended next move in the agent panel.
3. Add optional command bar for structured prompts mapped to supported intents (not free-form arbitrary tool execution).
4. Keep preview/confirmation required for high-risk actions.

### Design constraint

This is **intent routing to existing tools/macros**, not unrestricted natural-language automation.

### Code touchpoints

- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/queue-manager.js`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`

### Acceptance criteria

1. Operators can trigger common AP agent actions without debug mode.
2. Every action shows preview/confirmation requirements before execution.
3. All actions remain policy-scoped and audited.

## AX3. Proactive Agent Outcomes (Nudges, Drafts, and Next-Step Explanations)

### Goal

Make the agent feel proactive, not just reactive.

### Current reusable base

- Gmail draft endpoint and compose prefill flow (already used for needs-info drafts)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
- Slack/Teams approval and notification services
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/teams_api.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_notifications.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/teams_notifications.py`

### Changes

1. Expose “Agent suggested next step” in Gmail for blocked invoices.
2. Add one-click proactive actions:
   - Draft vendor info request
   - Nudge approver(s)
   - Summarize exception for finance lead
3. Show a short explanation of why the agent suggests that action (policy/confidence/budget/failure reason).

### Code touchpoints

- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py`
- notification services above

### Acceptance criteria

1. Blocked invoices always show a suggested recovery action.
2. Suggested action can be executed in one click (or one confirmation) from Gmail.
3. Suggested action reason is visible and auditable.

## AX4. Batch Agent Ops in Gmail (Low-Risk Autonomy Controls)

### Goal

Support “delegate a batch” behavior without introducing a separate AP dashboard.

### Current reusable base

- Gmail worklist and pipeline APIs in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py`
- Canonical `next_action`, `requires_field_review`, `confidence_blockers` in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py`
- Workflow service and agent orchestrator in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py`

### Changes

1. Add batch actions for filtered items in the Gmail sidebar/worklist context:
   - Process low-risk ready items
   - Retry failed posts (preview mode)
   - Send approval nudges for aging approvals
2. Add preview mode summaries before batch execution:
   - items affected
   - blocked items
   - actions requiring human confirmation
3. Provide explicit “dry run / run” semantics for agentic batch operations.

### Acceptance criteria

1. Batch actions are bounded to policy-safe/legal AP states.
2. Operators see what will happen before execution.
3. Results flow into the same per-item agent timeline and audit trail.

## AX5. Browser Fallback Visibility and Trust UX

### Goal

Turn browser fallback from “backend fallback mechanism” into a visible, trustworthy agent capability.

### Current reusable base

- Preview/confirmation/dispatch/finalization in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py`
- Browser command preview/queue/result in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py`
- Browser fallback completion endpoint in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`

### Changes

1. Show explicit timeline stages when fallback is used:
   - API post failed
   - browser fallback prepared
   - preview generated
   - confirmation captured
   - runner executing
   - result reconciled (success/failure)
2. Show operator-safe evidence summary:
   - ERP reference (if available)
   - redacted error code/message on failure
3. Add per-item fallback badge/status language in Gmail to prevent “silent fallback” ambiguity.

### Acceptance criteria

1. Operators can tell when/why browser fallback occurred.
2. Result reconciliation is visible and linked to AP state transitions.
3. No fallback path appears “done” before completion callback is reconciled.

## AX6. Agentic KPI and Telemetry Layer (Perception + Ops)

### Goal

Measure agent usefulness, not just workflow completion.

### Metrics to add (operator + product)

1. `% invoices handled straight-through`
2. `% invoices requiring human intervention`
3. average time in `awaiting approval`
4. `% ERP posts needing browser fallback`
5. `% agent suggestions accepted`
6. `% agent actions requiring manual override`
7. top blocker reasons (confidence/policy/budget/ERP)

### Code touchpoints

- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/metrics_store.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`

### Acceptance criteria

1. Ops/admin surfaces expose agentic outcomes (not only technical health).
2. Metrics can support product positioning claims (e.g., reduced rekeying, exception-only handling).

## AX7. Slack/Teams Agentic Presentation Parity (Lightweight)

### Goal

Keep Slack/Teams as decision surfaces, but make them feel like interacting with an AP agent instead of static forms.

### Changes

1. Add “why this needs your decision” summary line (policy/confidence/budget/exception reason).
2. Add “what happens next” line per action where possible.
3. Include “requested by agent on behalf of AP workflow” metadata consistently.
4. Add link-back wording to Gmail/AP context as the source of truth.

### Code touchpoints

- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/teams_api.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`

## Delivery Plan (Suggested)

## Phase 1 (1 week): Perception Unlock

1. AX1 Gmail Agent Timeline
2. AX2 Non-debug Agent Actions menu (bounded intents)
3. AX5 Browser fallback visibility states

Success criterion:
- Clearledgr visibly looks agentic in Gmail demos without weakening controls.

## Phase 2 (1 week): Proactive Behavior

1. AX3 proactive drafts/nudges/suggested recovery actions
2. AX7 Slack/Teams copy/presentation parity improvements

Success criterion:
- Agent initiates useful next steps and explains them.

## Phase 3 (1-2 weeks): Operator Scale + Metrics

1. AX4 batch agent ops in Gmail
2. AX6 agentic KPI/telemetry surfaces

Success criterion:
- Product can demonstrate exception-only handling and operator leverage.

## Testing and Validation Plan

### UX/contract tests

1. Gmail agent session rendering with:
   - idle session
   - blocked-for-approval
   - failed action
   - fallback preview/confirmation/result
2. Agent action menu dispatch/preview/confirm flows
3. Browser fallback completion reflected in Gmail timeline
4. Slack/Teams approval cards include reason + next-step explanation

### Safety regression checks

1. AP state-machine transition tests remain green
2. Policy/confidence gating tests remain green
3. Channel callback security/idempotency tests remain green
4. Audit completeness tests remain green

## Documentation Update Requirements (When Implementing)

For each AX workstream landed, update these docs:

1. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
   - operator-visible behavior and UX doctrine updates
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
   - any new Gmail/agent session API contracts
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
   - demo/user-flow narrative changes
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md` (if in launch scope)
   - new evidence items for agentic UX behavior and fallback visibility

## What “Agentic” Means for Clearledgr (Positioning)

Clearledgr should feel like:

- an **AP agent embedded in Gmail** that can execute work safely,
- not a generic “automation builder,”
- not a chat bot with hidden logic,
- and not a traditional AP dashboard where humans rekey everything.

The strongest differentiator is:

**visible autonomous execution + deterministic controls + ERP write-back + audit trail**

in the tools finance teams already use.
