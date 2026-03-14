# Clearledgr Agentic Autonomy Next Program (HITL-Preserved)

## Objective
Maximize safe agentic autonomy for AP workflows while preserving non-negotiable human-in-the-loop controls for risky actions.

This program does **not** target zero-HITL autonomy. It targets:
- autonomous execution for low-risk paths
- proactive recovery and routing by the agent
- explicit human confirmations at policy/risk boundaries

## Guardrails (Locked)
1. Keep Gmail as primary operator surface.
2. Keep Slack/Teams as approval decision surfaces.
3. Keep deterministic policy gates (budget, confidence, PO/exception, auth).
4. No silent state changes: all fallback/retry/override outcomes must be auditable.
5. No generic automation-builder pivot.

---

## Execution Plan (Next 2–4 weeks)

## Phase A (P0): High-Leverage Autonomy Gains With Existing Architecture

### A1. Vendor Follow-up Autonomy Loop (Draft → Track → Reconcile)
**Goal**
Close the current “request info” loop with stronger agent ownership after human-triggered request-info decisions.

**Implementation**
- Extend follow-up state metadata for each AP item:
  - `needs_info_draft_id`, `followup_last_sent_at`, `followup_attempt_count`, `followup_next_action`.
- Add controlled nudge scheduling for unanswered vendor requests (draft-first or resend template with guardrails).
- Surface follow-up progress in Gmail timeline/operator brief.

**Code touchpoints**
- `clearledgr/services/invoice_workflow.py`
- `clearledgr/services/auto_followup.py`
- `clearledgr/api/gmail_extension.py`
- `clearledgr/core/stores/ap_store.py`

**Acceptance tests**
- Request-info action creates/links draft and follow-up metadata.
- If no response by SLA window, agent proposes or queues next nudge (without auto-sending high-risk actions).
- Gmail thread context clearly shows follow-up state and next operator action.

### Landed (A1 pass)
1. Added normalized `needs_info` follow-up metadata persistence in workflow paths:
   - `needs_info_draft_id`
   - `followup_last_sent_at`
   - `followup_attempt_count`
   - `followup_next_action`
   - `followup_sla_due_at`
2. Added guarded `POST /extension/vendor-followup` action to prepare/re-prepare vendor follow-up drafts with SLA/attempt guardrails (draft-first, never auto-send).
3. Extended worklist payload + Gmail sidebar/operator brief rendering to show follow-up progress and next action guidance.
4. Added regression tests for workflow metadata, extension endpoint behavior, and Gmail sidebar rendering coverage.

---

### A2. Policy-Aware Batch Autonomy Expansion (Still Preview-First)
**Goal**
Increase throughput by safely broadening batch operations beyond current retry/posting/nudge primitives.

**Landed (2026-02-27)**
- Added batch intents in Gmail sidebar Batch Agent Ops:
  - `prepare_vendor_followups`
  - `route_low_risk_for_approval`
  - `retry_recoverable_failures`
- Added deterministic preview policy outputs with selected/excluded reason summaries.
- Added run-path idempotency key propagation per batch item.
- Added secured extension endpoints for low-risk approval routing + recoverable retries.
- Added workflow/state precheck helpers for low-risk approval routing and recoverable retry classification.
- Added UI + API + workflow acceptance coverage for preview determinism, run outcomes, and idempotent replay.

### Agent Core Runtime Seam (Platform Direction)
**Landed (initial)**
- Added a canonical finance-agent intent contract:
  - `POST /api/agent/intents/preview`
  - `POST /api/agent/intents/execute`
- Added `FinanceAgentRuntime` as the backend intent runtime seam (`clearledgr/services/finance_agent_runtime.py`).
- Routed the existing `route_low_risk_for_approval` AP action through the runtime to preserve backward compatibility while establishing an agent-first platform boundary.

**Landed (expanded)**
- Extended runtime intents to include:
  - `prepare_vendor_followups`
  - `retry_recoverable_failures`
- Preserved intent-level idempotency replay and per-item runtime audit trails across all three batch intents.
- Updated Gmail extension queue-manager execution paths to use canonical `POST /api/agent/intents/execute` as the single batch-intent execution path.
- Converted legacy extension batch endpoints into runtime adapters so execution logic lives only in `FinanceAgentRuntime`.
- Added explicit skill packaging under the runtime:
  - `FinanceSkill` contract (`preview`, `execute`, `policy_precheck`, `audit_contract`)
  - `APFinanceSkill` module for AP intents
  - `WorkflowHealthSkill` read-only module (`read_ap_workflow_health`) as the first non-AP expansion proof.

**Implementation**
- Add new batch intents with explicit policy prechecks:
  - `prepare_vendor_followups`
  - `route_low_risk_for_approval`
  - `retry_recoverable_failures`
- Keep preview/run split; keep per-item outcomes and idempotency keys.
- Add policy summaries to batch preview (why selected, why excluded).

**Code touchpoints**
- `ui/gmail-extension/src/inboxsdk-layer.js`
- `clearledgr/api/gmail_extension.py`
- `clearledgr/services/invoice_workflow.py`
- `clearledgr/core/ap_states.py`

**Acceptance tests**
- Preview returns deterministic selected/excluded sets with reasons.
- Run path executes only preview-eligible items and emits per-item audit events.
- Duplicate batch runs remain idempotent.

---

### A3. Recommendation Calibration Layer (Tenant-Specific)
**Goal**
Improve decision quality consistency by calibrating recommendation thresholds using observed human outcomes.

**Implementation**
- Build per-tenant calibration profile from feedback:
  - reject-after-approve ratio
  - request-info-after-approve ratio
  - override rate by decision type.
- Inject calibration into AP decision context and fallback scoring, while preserving hard gates.
- Add operator-visible “calibration active” telemetry.

**Code touchpoints**
- `clearledgr/services/ap_decision.py`
- `clearledgr/core/stores/vendor_store.py`
- `clearledgr/api/ops.py`
- `clearledgr/core/stores/metrics_store.py`

**Acceptance tests**
- With strict calibration profile, borderline approvals route to human review.
- With permissive profile and clean history, low-risk approvals increase.
- Calibration shifts are observable in metrics snapshots.

---

## Phase B (P1): Reliability and Runtime Autonomy Maturity

### B1. Durable Agent Task Queue for Proactive Jobs
**Goal**
Make proactive/autonomous actions restart-safe and auditable end-to-end.

**Implementation**
- Add durable queue entries for:
  - approval nudges
  - vendor follow-up nudges
  - retry-post operations
  - periodic AP health scans.
- Persist next-run timestamps, attempts, status, and terminal outcomes.
- Ensure background workers only execute durable jobs (no fire-and-forget gaps).

**Code touchpoints**
- `clearledgr/services/agent_background.py`
- `clearledgr/core/database.py`
- `clearledgr/core/stores/*` (job persistence APIs)
- `clearledgr/services/invoice_workflow.py`

**Acceptance tests**
- Queued proactive jobs survive process restart.
- Job retries back off and terminate correctly after max attempts.
- Audit trail links job execution to affected AP item(s).

---

### B2. Agent Decision Explainability Contract (Operator-Grade)
**Goal**
Make every recommendation instantly understandable and actionable.

**Implementation**
- Standardize explanation fields across Gmail/Slack/Teams:
  - `what_happened`, `why_now`, `recommended_now`, `expected_outcome`, `risk_notes`.
- Ensure every action button has matching expected-outcome language.
- Add compact confidence + policy blocker annotations.

**Code touchpoints**
- `ui/gmail-extension/src/inboxsdk-layer.js`
- `clearledgr/services/invoice_workflow.py`
- `clearledgr/services/slack_api.py`
- `clearledgr/services/teams_api.py`

**Acceptance tests**
- All action surfaces render the explanation contract.
- Edge cases (budget hard block, confidence review, duplicate risk, fallback pending) have deterministic copy branches.
- No surface falls back to raw debug/internal labels by default.

---

### B3. Browser Runner Trust Hardening (External Runner Ready)
**Goal**
Keep browser fallback agentic while tightening trust boundaries for externalized runners.

**Implementation**
- Require signed runner identity + scoped session claims for completion callbacks.
- Enforce callback replay protection and strict idempotency windows.
- Add callback integrity evidence to audit metadata.

**Code touchpoints**
- `clearledgr/api/agent_sessions.py`
- `clearledgr/services/browser_agent.py`
- `clearledgr/core/auth.py`
- `tests/test_browser_agent_layer.py`

**Acceptance tests**
- Unauthorized/expired/replayed callbacks are rejected and audited.
- Valid signed callback finalizes state exactly once.
- Reconciled completion always updates AP state and timeline.

---

## Phase C (P2): Productized Agent Autonomy Signals

### C1. Autonomy Scoreboard (Operator + Admin Views)
**Goal**
Make agent value and control balance measurable in daily operations.

**Implementation**
- Add autonomy scorecard tiles:
  - autonomous completion rate (policy-safe)
  - human-intervention rate by reason
  - recovery success rate
  - time-to-resolution reduction.
- Show trend windows (7d/30d) and top blockers.

**Code touchpoints**
- `clearledgr/api/analytics.py`
- `clearledgr/api/ops.py`
- `ui/gmail-extension/src/inboxsdk-layer.js`
- `clearledgr/core/stores/metrics_store.py`

**Acceptance tests**
- Metrics contract stable across ops/admin/Gmail surfaces.
- Ratios are computed from canonical audited events only.
- Missing data degrades gracefully with explicit “insufficient data” signaling.

---

### C2. Tenant Policy Templates for Safe Autonomy Expansion
**Goal**
Enable gradual autonomy increase without code changes.

**Implementation**
- Add policy profiles:
  - `conservative`, `balanced`, `throughput`.
- Profiles tune low-risk thresholds and proactive limits, never bypassing hard controls.
- Provide profile diff preview before apply.

**Code touchpoints**
- `clearledgr/api/settings.py`
- `clearledgr/services/invoice_workflow.py`
- `clearledgr/api/gmail_extension.py`

**Acceptance tests**
- Profile changes alter routing/autonomy behavior predictably.
- Hard gates remain enforced under every profile.
- Profile changes are audited with actor and timestamp.

---

## Delivery Sequence
1. A1 Vendor follow-up autonomy loop
2. A2 Policy-aware batch autonomy expansion
3. A3 Recommendation calibration layer
4. B1 Durable proactive task queue
5. B2 Explainability contract standardization
6. B3 Browser runner trust hardening
7. C1 Autonomy scoreboard
8. C2 Policy templates

## Definition of Done (per item)
1. Code shipped with feature gate where needed.
2. Unit/integration tests added for success + failure + idempotency paths.
3. Audit evidence fields present and queryable.
4. Operator-facing UX copy updated (Gmail + Slack/Teams where applicable).
5. Runbook/update note added to docs.

## Immediate Next Step
Start **A1 Vendor Follow-up Autonomy Loop** first.  
It is the highest-impact autonomy gain with low architectural risk and directly improves AP cycle-time without weakening HITL boundaries.
