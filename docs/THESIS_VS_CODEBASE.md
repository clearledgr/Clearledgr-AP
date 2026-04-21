# Thesis vs Codebase — Ship-readiness audit

**Audit date:** 2026-04-21
**Docs of truth:** `DESIGN_THESIS.md` · `AGENT_DESIGN_SPECIFICATION.md` · `CLEARLEDGR_OVERVIEW.md` · deck slides 2–3 (Problem + Solution)
**Verdict:** Codebase faithfully implements the deck. Every load-bearing claim maps to a named primitive in a single logical home. Two real gaps to address before shipping the deck and one explicit thesis contradiction in the Gmail sidebar. Detail below.

---

## Executive summary

| Deck claim | Verdict | Confidence |
|---|---|---|
| "Finance workflows have no persistent home" (problem) | Defensible as market problem; not a codebase claim | — |
| "Give every workflow a persistent home" (Box) | **Match** | High |
| "State, timeline, exceptions, outcome" per instance | **Match** | High |
| "Attributable record" | **Match** | High |
| "An agent advances each one where it can" | **Match** | High |
| "Humans decide on the exceptions" | **Match** | High |
| Gmail render target | **Match with one contradiction** (sidebar approve button) | Medium |
| Slack decision surface | **Match** | High |
| ERP system of record (SAP, NetSuite, Xero, QuickBooks) | **Match** | High |
| Backoffice = connect to customer's own admin (outbound) | **Match** | High |
| "Rules decide. LLM describes. No financial write at the mercy of model judgment" | **Match** | High |

**Safe to pitch today:** the entire Problem/Solution slide narrative, with one deck-copy edit on Gmail (see §Gaps 1) and two honesty edits on scope (see §Gaps 2–3).

---

# PART A — Deck claim audit

This part is external-facing. Every claim on the Problem + Solution slides is mapped to code evidence. Where a claim is weak or contradicted, it's flagged with a suggested deck edit.

## A.1 Box = persistent home with state, timeline, exceptions, outcome, attributable

**Claim:** "Every workflow instance becomes a persistent, attributable record: state, timeline, exceptions, outcome."

**Evidence (match):**

- **Persistent record** — [ap_items](../clearledgr/core/database.py) table (database.py:657). Primary key `id`, columns for every lifecycle field.
- **State** — [APState](../clearledgr/core/ap_states.py#L41) enum + [VALID_TRANSITIONS](../clearledgr/core/ap_states.py#L64). 11 canonical states, DB-trigger enforced at [database.py:353](../clearledgr/core/database.py#L353) (`enforce_valid_ap_state` — direct SQL can't bypass the state machine).
- **Timeline** — [audit_events](../clearledgr/core/database.py) table with append-only DB triggers ([database.py:296-323](../clearledgr/core/database.py#L296-L323)): `trg_audit_events_no_update`, `trg_audit_events_no_delete`, and the policy twin. Implemented on both Postgres and SQLite. This is the "attributable, tamper-resistant timeline" claim.
- **Exceptions** — [BoxLifecycleStore.raise_box_exception](../clearledgr/core/stores/box_lifecycle_store.py) with the admin queue at [box_exceptions_admin.py](../clearledgr/api/box_exceptions_admin.py). Planning engine raises a box exception on every unhandled event type ([planning_engine.py:84-103](../clearledgr/core/planning_engine.py#L84-L103)).
- **Outcome** — state machine has distinct terminal states: `closed` (successful), `rejected`, `reversed`. `reversed` is deliberately NOT folded into `closed` so post-then-reverse stays distinguishable from post-and-paid ([ap_states.py:76-84](../clearledgr/core/ap_states.py#L76-L84)).
- **Attributable** — `approved_by`, `approved_at`, `rejected_by`, `rejected_at`, `user_id`, `organization_id`, `erp_posted_at`, `approval_policy_version`, `supersedes_ap_item_id` (chain across resubmissions). Every mutation is identity-stamped.
- **Org-scoped** — `organization_id` on every row. Multi-tenant.

**Safe to pitch.**

## A.2 Agent advances workflows autonomously where it can

**Claim:** "An agent advances each one where it can."

**Evidence (match):**

- **Planning engine** — [DeterministicPlanningEngine](../clearledgr/core/planning_engine.py#L30). Dispatches 20 event types ([planning_engine.py:50-71](../clearledgr/core/planning_engine.py#L50-L71)) to typed handlers. Pure deterministic — no Claude calls at the planning layer (file header: "No Claude calls. Claude is only called WITHIN specific Actions during execution").
- **Coordination engine** — [CoordinationEngine](../clearledgr/core/coordination_engine.py#L95). Registers 50+ action handlers covering every category in Agent Spec §3 (Email, Classification, ERP, Box/State, Communication, Vendor, Fraud).
- **Runtime facade** — [FinanceAgentRuntime](../clearledgr/services/finance_agent_runtime.py#L94) is the public surface.
- **Durability** — [TaskStore](../clearledgr/core/stores/task_store.py#L28) + `task_runs` table + resume on startup (see `get_platform_finance_runtime().resume_pending_agent_tasks()` called from `main.py`). A crash mid-plan is recoverable.
- **Rule 1 enforcement** — [_Rule1PreWriteFailed](../clearledgr/core/coordination_engine.py#L73) exception aborts the action if the pre-write timeline entry can't land. No side effect happens without a corresponding audit row.
- **19-step invoice plan** — Agent Spec §9.1 is implemented at [planning_engine.py:120+](../clearledgr/core/planning_engine.py#L120) (`_plan_email_received`).

**Safe to pitch.**

## A.3 Humans decide on the exceptions

**Claim:** "Humans decide on the exceptions."

**Evidence (match):**

- **Exception pause** — planner halts plan on `needs_info`, `escalate`, or unhandled event types; records `agent_action:*:paused` audit entries (per memory, commit `8030196`).
- **Exception queue** — [box_exceptions_admin.py](../clearledgr/api/box_exceptions_admin.py) exposes the unresolved list scoped by organization, role-gated to `admin`/`owner`.
- **Intent dispatch** — Slack button clicks land in [slack_invoices.py:326-346](../clearledgr/api/slack_invoices.py#L326-L346), dispatched through [dispatch_runtime_intent](../clearledgr/services/agent_command_dispatch.py) as `approve_invoice` / `reject_invoice` / `needs_info`. The approval returns through the runtime intent bus and re-enters the Box.
- **Override window** — [services/override_window.py](../clearledgr/services/override_window.py) + state machine transition `posted_to_erp → reversed` ([ap_states.py:74](../clearledgr/core/ap_states.py#L74)). Default 15 min per thesis.

**Safe to pitch.**

## A.4 Gmail — AP work surface (sidebar, thread injection, inbox labels)

**Claim:** "Gmail. AP work surface. Sidebar, thread injection, inbox labels."

**Evidence (match):**

- **Seven InboxSDK injection points** — all present in [inboxsdk-layer.js](../ui/gmail-extension/src/inboxsdk-layer.js):
  1. **Home route** — `'clearledgr/home'` registered at [inboxsdk-layer.js:1723](../ui/gmail-extension/src/inboxsdk-layer.js#L1723), [HomePage.js](../ui/gmail-extension/src/routes/pages/HomePage.js)
  2. **NavMenu** — [inboxsdk-layer.js:2409-2445](../ui/gmail-extension/src/inboxsdk-layer.js#L2409)
  3. **Lists (inbox stage)** — driven by Clearledgr Gmail labels via [services/gmail_labels.py](../clearledgr/services/gmail_labels.py)
  4. **Gmail label architecture** — canonical taxonomy in `gmail_labels.py`; Phase 2 bidirectional sync (label → `LABEL_CHANGED` event → planner)
  5. **Toolbars** — [inboxsdk-layer.js:1049](../ui/gmail-extension/src/inboxsdk-layer.js#L1049), [1091](../ui/gmail-extension/src/inboxsdk-layer.js#L1091)
  6. **Conversations (thread sidebar)** — [inboxsdk-layer.js:752](../ui/gmail-extension/src/inboxsdk-layer.js#L752), [ThreadSidebar.js](../ui/gmail-extension/src/components/ThreadSidebar.js)
  7. **Router (Kanban pipelines)** — 15+ routes registered ([PipelinePage.js](../ui/gmail-extension/src/routes/pages/PipelinePage.js), [ReviewPage.js](../ui/gmail-extension/src/routes/pages/ReviewPage.js), etc.)
- Bonus: Compose handler + keyboard shortcuts (G-H for Home, matches Streak UX pattern).
- **Labels as AP pipeline** — state → label mapping + bidirectional sync in [services/gmail_labels.py](../clearledgr/services/gmail_labels.py). Only 4 action labels trigger intents (Approved / Exception / Review Required / Not Finance); status labels explicitly excluded to avoid label-loops.

**One contradiction with thesis — not with deck copy.** The sidebar has an Approve button gated on `matchPassed` at [ThreadSidebar.js:1336-1339](../ui/gmail-extension/src/components/ThreadSidebar.js#L1336-L1339). The thesis says "Sidebar does NOT have approve/reject buttons — those route to Slack/Teams." The deck copy doesn't assert this either way, so the deck is not currently wrong — but see §Gaps 1 for the decision to make.

**Safe to pitch the deck copy. Decide the sidebar button before anyone audits code against thesis.**

## A.5 Slack — approvals and escalations

**Claim:** "Slack. Approvals and escalations. Decisions where approvers live."

**Evidence (match):**

- [slack_invoices.py](../clearledgr/api/slack_invoices.py) — interactive handler routes through `_dispatch_runtime_intent` to the runtime intent bus. Three primary intents: `approve_invoice` (L328), `reject_invoice` (L415), `needs_info` (L400).
- Escalation + digest + override-window notifications in [services/slack_notifications.py](../clearledgr/services/slack_notifications.py) and [services/slack_digest.py](../clearledgr/services/slack_digest.py).
- Teams is flag-gated at default-off via [is_teams_enabled()](../clearledgr/core/feature_flags.py#L58). That's thesis-compliant for V1 (Slack is V1, Teams V1.x) and the deck explicitly shows only Slack.

**Safe to pitch.**

## A.6 ERP — system of record (SAP, NetSuite, Xero, QuickBooks)

**Claim:** "ERP. System of record. SAP, NetSuite, Xero, QuickBooks."

**Evidence (match):**

- **All four posters exist:**
  - [post_bill_to_quickbooks](../clearledgr/integrations/erp_quickbooks.py#L165)
  - [post_bill_to_xero](../clearledgr/integrations/erp_xero.py#L170)
  - [post_bill_to_netsuite](../clearledgr/integrations/erp_netsuite.py#L292)
  - [post_bill_to_sap](../clearledgr/integrations/erp_sap.py#L230)
- **Unified router** — [erp_router.py:801](../clearledgr/integrations/erp_router.py#L801) dispatches on connection type.
- **Token auto-refresh + retry** — on 401, [erp_router.py:902-933](../clearledgr/integrations/erp_router.py#L902) refreshes tokens and retries once.
- **Per-tenant GL map** — `settings_json["gl_account_map"]` loaded per-org, passed to each poster as `gl_map`.
- **SAP pre-flight validation** — validates `vendor_id`, `amount > 0`, `company_code` before the httpx call; returns structured `sap_validation_failed` on failure.
- **Posting ≠ payment** — thesis commitment. `post_bill` creates the ERP record; `schedule_payment` is a separate action. State machine has `posted_to_erp` distinct from any payment-settled state.

**Safe to pitch.** Caveat: Starter tier (Xero/QuickBooks self-serve) is a shippable happy path; NetSuite/SAP is Enterprise managed implementation — honestly stated in [DESIGN_THESIS.md](../DESIGN_THESIS.md) commitment #10.

## A.7 Backoffice — connect to customer's own admin / internal ops

**Claim:** "Backoffice. Your own admin system. Where internal ops tools and dashboards already live."

**This means:** Clearledgr connects to the customer's existing backoffice (Retool, Superset, internal admin, etc.) — outbound. Not Clearledgr giving them an admin console.

**Evidence (match):**

- **Outbound webhook delivery** — [services/webhook_delivery.py](../clearledgr/services/webhook_delivery.py). HMAC-SHA256 signed payloads ([compute_signature L68-74](../clearledgr/services/webhook_delivery.py#L68-L74)), delivery with timeout + retry queue on failure (max 5 retries via the notification retry infra).
- **Subscription management** — [stores/webhook_store.py](../clearledgr/core/stores/webhook_store.py) — per-org subscriptions with event-type filtering.
- **Wired to real state transitions:**
  - AP state change → [ap_store.py:392](../clearledgr/core/stores/ap_store.py#L392) calls `emit_state_change_webhook`
  - Vendor state → [vendor_store.py:1981](../clearledgr/core/stores/vendor_store.py#L1981) calls `emit_vendor_state_change_webhook`
  - Vendor invited → [vendor_store.py:1598](../clearledgr/core/stores/vendor_store.py#L1598) calls `emit_vendor_invited_webhook`
  - Box lifecycle → [box_lifecycle_store.py:88-93](../clearledgr/core/stores/box_lifecycle_store.py#L88-L93) calls `emit_webhook_event`
  - Monitoring → [services/monitoring.py:561-562](../clearledgr/services/monitoring.py#L561-L562)
- **Event catalog** — 14 event types documented in the module header ([webhook_delivery.py:1-22](../clearledgr/services/webhook_delivery.py#L1-L22)): `invoice.received/validated/approved/rejected/posted_to_erp/closed/needs_info`, `vendor.invited/kyc_complete/bank_verified/activated/suspended`, `payment.completed/failed/reversed`.

**Safe to pitch.** This is the integration surface the deck promises. A customer can wire any internal tool (Retool, Superset, custom admin, PagerDuty, n8n) to these webhooks and render Box state inside their existing backoffice.

## A.8 "Rules decide. LLM describes. No financial write is at the mercy of model judgment."

**Claim:** bottom-of-slide commitment.

**Evidence (match):**

- **Planning engine is deterministic** — file header at [planning_engine.py:6-9](../clearledgr/core/planning_engine.py#L6-L9): "The planning engine is pure deterministic code. No Claude calls. Claude is only called WITHIN specific Actions during execution." Implemented as a dispatch table, not an LLM call.
- **LLM Gateway enforces the boundary** — [LLMAction](../clearledgr/core/llm_gateway.py#L47) is a closed enum; file comment at [L48-L49](../clearledgr/core/llm_gateway.py#L48-L49): "Deterministic actions are NOT listed here and CANNOT call Claude through the gateway." Only 5 spec actions + 2 extended (`ap_decision`, `agent_planning`).
- **3-way match is rule-based** — `run_three_way_match` is in the ERP action section of the coordination engine at [coordination_engine.py:147](../clearledgr/core/coordination_engine.py#L147), marked DET in Agent Spec §3.
- **5 extraction guardrails run before ERP write** — `run_extraction_guardrails` at [coordination_engine.py:139](../clearledgr/core/coordination_engine.py#L139), Agent Spec §7.1. Extraction outputs are never used for ERP actions without deterministic re-validation.
- **Pre-post validation** — `pre_post_validate` at [coordination_engine.py:149](../clearledgr/core/coordination_engine.py#L149) re-reads Box + ERP state before every `post_bill`. Idempotent: existing-bill check prevents double-post.
- **System prompt architecture** — 4-section template (Role / Output format / Constraints / Guardrail reminder) per Agent Spec §7.2, enforced by gateway.

**Safe to pitch — this is the single strongest commitment in the deck and the code is built around it.**

---

# PART B — Engineering go-live assessment

Internal-facing. What's shippable today, what's shippable with caveats, what isn't.

## B.1 What is shippable

| Area | Status | Evidence |
|---|---|---|
| Box persistence + state machine | **Shippable** | `ap_items` + state-guard DB trigger + 345 tests green |
| Append-only audit timeline | **Shippable** | DB triggers prevent UPDATE/DELETE on `audit_events` and `ap_policy_audit_events` |
| Agent planning + coordination | **Shippable** | Rule 1 enforced, 20 event types wired, 50+ action handlers |
| LLM gateway + DET/LLM boundary | **Shippable** | Closed action enum; model defaults to Haiku 4.5 / Sonnet 4.6 |
| 4 ERP posters + token refresh | **Shippable** for QB/Xero self-serve; **managed impl** required for NetSuite/SAP |
| Gmail extension (7 injection points) | **Shippable** | InboxSDK; 23/23 frontend component tests pass |
| Gmail labels as AP pipeline | **Shippable** | Phase 2 bidirectional sync live |
| Slack approvals + escalations | **Shippable** | Intent dispatch + digest + override-window notifications |
| Outbound webhook delivery | **Shippable** | HMAC-signed, retry-queued, wired into AP/vendor/box state transitions |
| Secrets hardening | **Shippable** | `require_secret()` crashes in prod on missing |
| Override window | **Shippable** | Service + state-machine transition + scheduled reversal |
| Fraud controls (thesis §8) | **Shippable** | IBAN change freeze, domain lock, lookalike detection, amount ceiling, velocity, duplicate — all have dedicated test files |
| Autonomy tier per entity | **Shippable** | [entity_store.py:249](../clearledgr/core/stores/entity_store.py#L249) reads from `entity.settings_json` with org fallback |
| Multi-tenancy | **Shippable** | `organization_id` on every Box, timeline, audit, webhook subscription |
| Test coverage | **Strong** | 157 test files; thesis-specific suites for onboarding gates, IBAN freeze, domain lock, fraud controls, override window, autonomy config, box exceptions admin |

## B.2 Shippable with caveats

| Area | Caveat | Impact |
|---|---|---|
| Vendor onboarding v1.1 | KYC / open-banking / portal adapters are stubs — see [coordination_engine.py:207-222](../clearledgr/core/coordination_engine.py#L207-L222) (`_handle_onboarding_adapter_pending`). The planning flow executes end-to-end but writes "adapter pending" neutral audit entries in place of real provider calls. | **Vendor onboarding pipeline renders and flows, but KYC decisions are not yet provider-verified.** The deck's "live AP" claim is unaffected; don't pitch vendor onboarding as shipped-live yet. |
| Teams adapter | Gated default-off, service + API exist but not wired in V1. | Thesis-compliant. Deck already shows Slack only. Keep it out of live feature list. |
| Vendor activation SLA & open-banking | Tests exist (`test_vendor_activation_sla`, `test_vendor_domain_lookalike`), but the open-banking verifier at [onboarding/bank_verifier.py](../clearledgr/services/onboarding/bank_verifier.py) is an adapter surface — check provider keys before staging drill. | Gate live vendor onboarding on the open-banking provider integration being signed off. |
| Celery vs FastAPI background | `services/celery_tasks.py` exists; Agent Spec §11 calls for a Celery fleet behind Redis Streams. Verify which is the prod runtime before quoting SLAs. | Performance claims in Agent Spec §11 (50-invoice scenario) depend on this. Don't quote 2-minute SLAs externally until the prod worker model is confirmed. |
| Redis rate limiting | Memory notes this as an outstanding operational blocker. | Rate-limit protection for multi-tenant spike safety. Low customer-visible risk at pilot scale, but list as a beta-blocker. |
| Staging E2E drill | Memory notes: not yet completed. | Do not ship to a customer's production inbox without this. |

## B.3 Known gaps — not in code, not pitchable as live

| Gap | Where the thesis promises it | Status |
|---|---|---|
| Vendor KYC real providers | Thesis §10 + Agent Spec §10 (onboarding lifecycle) | Flow wired, adapter stubs. Provider selection + wiring is the gating work. |
| Payment Execution (Q4 roadmap) | Thesis §12-month sequence | Not started. Roadmap item, not V1. |
| Multi-entity workspace (Q2 roadmap) | Thesis §12-month sequence | Config surface exists; workspace hierarchy UI not built. Roadmap. |
| Outlook | Explicitly out of V1 | Not built — consistent with thesis boundary. |

---

# PART C — Gaps to resolve before shipping the deck

Three items. First two are deck-copy calls; third is a code call.

## Gap 1 — Sidebar Approve button contradicts thesis

**Where:** [ThreadSidebar.js:1336-1339](../ui/gmail-extension/src/components/ThreadSidebar.js#L1336-L1339) renders Approve when `matchPassed`; wires to [SidebarApp.js:2165-2173](../ui/gmail-extension/src/components/SidebarApp.js#L2165-L2173) which calls `queueManager.approveAndPost()` → direct to ERP, bypassing Slack.

**Thesis commitment:** "Sidebar does NOT have approve/reject buttons — those route to Slack/Teams."

**The tension:** For auto-matched, 3-way-matched invoices under confidence threshold, the Approve button is a "happy path shortcut" that feels useful. But it creates a second approval path that bypasses the intent bus, which:
- violates the single-decision-surface principle
- gives two different audit trails (direct-API vs intent-bus)
- is what an auditor or a thesis-faithful investor will flag when they compare deck to code

**Three options:**
1. **Remove the button.** Cleanest. Preserves the thesis commitment exactly. ~10 lines of code.
2. **Route it through the intent bus.** Keep the UX, fix the architecture — the button calls `dispatch_runtime_intent("approve_invoice")` like Slack does, so the audit trail and policy gating are identical. Thesis-compliant in spirit, if not in letter.
3. **Amend the thesis.** Accept that "one-click approve in sidebar for fully matched invoices" is a product commitment and document it as an exception.

**Recommendation:** Option 2. Keeps the user-visible behavior, removes the audit/policy divergence, and can be done without changing the deck copy. ~30 minutes of work.

## Gap 2 — Deck doesn't mention "V1 is Slack-only (Teams V1.x)"

The deck's Solution slide says "Slack" for decisions. That's honest. No edit needed for the slide itself — but if a prospect asks "do you support Teams?" the honest answer is "Teams is feature-flagged off for V1, shipping in V1.x."

Have that answer ready. Don't get caught pitching Teams as live.

## Gap 3 — Deck doesn't distinguish vendor onboarding states

The Solution slide implies the agent advances every workflow instance. That's true for AP. For vendor onboarding, the flow runs end-to-end but against adapter stubs for KYC + open-banking.

**Recommendation:** If vendor onboarding comes up in a meeting, say: "AP is live with two design partners. Vendor onboarding is in the platform architecture and the pipeline flows end-to-end; we're wiring the KYC and open-banking providers before putting real vendors through it."

That's faithful to what's in code. Don't claim vendor onboarding as a live pipeline alongside AP.

---

# Appendix — Primitive → Code map

For fast reference when questions come up mid-deck-prep.

| Thesis / deck concept | Code primitive | Location |
|---|---|---|
| Box type / registry | `BoxType` | [box_registry.py:37](../clearledgr/core/box_registry.py#L37) |
| Box lifecycle (state + timeline + outcome) | `BoxLifecycleStore` | [box_lifecycle_store.py:60](../clearledgr/core/stores/box_lifecycle_store.py#L60) |
| Box summary (render-ready) | `BoxSummary`, `build_box_summary()` | [box_summary.py](../clearledgr/core/box_summary.py) |
| Exception queue | admin router | [box_exceptions_admin.py](../clearledgr/api/box_exceptions_admin.py) |
| AP state machine | `APState`, `VALID_TRANSITIONS` | [ap_states.py](../clearledgr/core/ap_states.py) |
| Append-only timeline | DB triggers on `audit_events` | [database.py:296-323](../clearledgr/core/database.py#L296-L323) |
| State-guard at DB layer | `enforce_valid_ap_state` trigger | [database.py:353](../clearledgr/core/database.py#L353) |
| Planning (deterministic) | `DeterministicPlanningEngine` | [planning_engine.py:30](../clearledgr/core/planning_engine.py#L30) |
| Coordination (executes plans) | `CoordinationEngine` | [coordination_engine.py:95](../clearledgr/core/coordination_engine.py#L95) |
| Rule 1 enforcement | `_Rule1PreWriteFailed` | [coordination_engine.py:73](../clearledgr/core/coordination_engine.py#L73) |
| Agent loop (observe→deliberate→act) | `FinanceAgentLoopService` | [finance_agent_loop.py](../clearledgr/services/finance_agent_loop.py) |
| Governance gate | `finance_agent_governance` | [finance_agent_governance.py](../clearledgr/services/finance_agent_governance.py) |
| Runtime facade | `FinanceAgentRuntime`, `APActionContext` | [finance_agent_runtime.py:94](../clearledgr/services/finance_agent_runtime.py#L94) |
| Durability | `TaskStore`, `task_runs` | [task_store.py:28](../clearledgr/core/stores/task_store.py#L28) |
| LLM Gateway + closed action registry | `LLMAction` enum | [llm_gateway.py:47](../clearledgr/core/llm_gateway.py#L47) |
| Secrets hardening | `require_secret()` | [secrets.py:22](../clearledgr/core/secrets.py#L22) |
| ERP router | `post_bill()` | [erp_router.py:801](../clearledgr/integrations/erp_router.py#L801) |
| QuickBooks poster | `post_bill_to_quickbooks` | [erp_quickbooks.py:165](../clearledgr/integrations/erp_quickbooks.py#L165) |
| Xero poster | `post_bill_to_xero` | [erp_xero.py:170](../clearledgr/integrations/erp_xero.py#L170) |
| NetSuite poster | `post_bill_to_netsuite` | [erp_netsuite.py:292](../clearledgr/integrations/erp_netsuite.py#L292) |
| SAP poster | `post_bill_to_sap` | [erp_sap.py:230](../clearledgr/integrations/erp_sap.py#L230) |
| Slack intent dispatch | `_dispatch_runtime_intent` | [slack_invoices.py:141](../clearledgr/api/slack_invoices.py#L141) |
| Customer-backoffice outbound webhooks | `webhook_delivery`, `webhook_store` | [webhook_delivery.py](../clearledgr/services/webhook_delivery.py), [webhook_store.py](../clearledgr/core/stores/webhook_store.py) |
| Override window | service | [override_window.py](../clearledgr/services/override_window.py) |
| Gmail labels pipeline | `gmail_labels.py` | [gmail_labels.py](../clearledgr/services/gmail_labels.py) |
| InboxSDK 7 injection points | bootstrap | [inboxsdk-layer.js](../ui/gmail-extension/src/inboxsdk-layer.js) |
| Thread sidebar | `ThreadSidebar` | [ThreadSidebar.js](../ui/gmail-extension/src/components/ThreadSidebar.js) |
| Vendor first-class object | `VendorStore` mixin | [vendor_store.py](../clearledgr/core/stores/vendor_store.py) |
| Fraud controls | per-check actions in coordination engine | [coordination_engine.py:181-186](../clearledgr/core/coordination_engine.py#L181-L186) |

---

## How to use this document

- **Before sending the deck:** read §Gaps (three items). Decide Option 1/2/3 on the sidebar button. Have the Teams answer and the vendor-onboarding answer ready.
- **While pitching:** Part A is your claim-by-claim receipt. Every deck claim has a file path.
- **During technical due diligence:** Part A + Appendix are enough for a CTO-level review. Part B is what you'd hand an engineering DD.
- **For the engineering team:** Part B.2 is the pre-ship punch list.
