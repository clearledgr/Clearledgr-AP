# Manifesto Review — file-by-file vs. The Back Office Runtime Manifesto

Running record of reviewing the codebase against the manifesto + what we're
building for. Per file: what it's for, manifesto fit, drift/gaps found, the fix,
and the test that proves it. Method: read each file fully, fix drift/gaps, test,
move on.

## Yardstick (review checklist)

The runtime must hold five primitives: **State** (typed central transitions +
policy version), **Ownership** (who acts next, explicit/auditable),
**Dependencies** (what it waits on, visible), **Exceptions** (what's stuck + why,
structured), **History** (every transition/override/reversal, reconstructable +
reversible). Tenets: coordination through shared state (no chokepoint/giant
prompt); the agent is bounded (rules decide, LLM describes; never moves money;
never authors vendor-facing text; every action audited + reversible); sovereign /
removable (operator owns the record); finance is the wedge, the architecture
generalizes.

---

## Spine

### `solden/core/box_registry.py` — ALIGNED (the generality spine)
- **For:** declares each workflow type as data (`BoxType`: source table, state
  field, open/terminal/exception states, initial state, gated_actions,
  governance_skill_id) so shared primitives dispatch by `box_type`; generic
  get/create/update_box dispatch + org-aware resolver for tenant specs.
- **Fit:** this is "the architecture that runs AP runs procurement/compliance/VO"
  in code — flat, dispatch-by-data, declarative specs ride the same spine. Carries
  the State + governance per-type policy. Strong.
- **Drift fixed:** docstring said "two BoxTypes (ap_item, bank_match)" but three
  are registered (+purchase_order), and oversold AP-subordinate `bank_match` as the
  generality proof. Corrected: purchase_order (AP-peer) + declarative WorkflowSpec
  are the real generality proof; bank_match is the AP-subordinate closing-leg type.
- **Decision (no fix):** dormant `vendor_onboarding` code is kept — the manifesto
  names VO as a generalization target, so it's option-value, honestly documented
  as unregistered. Not rot (cf. the removed `task_runs`, which claimed a capability
  that didn't exist).
- **Verdict:** aligned; doc drift fixed. Tests: box_registry / declarative /
  governance clusters green.

### `solden/core/ap_states.py` — ALIGNED (textbook State primitive)
- **For:** the canonical AP state machine — `APState` enum (typed states),
  `VALID_TRANSITIONS`, central `validate_transition`/`transition_or_raise`,
  `CURRENT_AP_POLICY_VERSION` stamped on every transition, the
  failure-recoverability classifier, `OverrideContext` audit metadata, and a
  `WorkflowStateMachine` protocol shim.
- **Fit:** exactly the manifesto's State primitive — typed (not strings),
  validated centrally, policy-version recorded, reversible (REVERSED terminal +
  approval-revert edges). AP-specific by design (the wedge); the protocol shim +
  declarative WorkflowSpec generalize it. Strong.
- **Drift fixed:** the header path summary documented only the original PLAN.md
  paths and omitted dual-approval, snooze, and the payment-tracking lifecycle
  (now the default close path). Updated the header to the current graph + marked
  `VALID_TRANSITIONS` authoritative + "keep in sync" to guard future drift. The
  payment lifecycle note reaffirms Solden does NOT execute payment.
- **Known roadmap (not a fix):** `policy_version` is a flat `"v1"`, honestly
  documented as a precursor to a real policy registry (linked rules / file hash).
  Recording the version satisfies the primitive today; the registry is the next
  step, not drift.
- **Verdict:** aligned; header drift fixed.

### `solden/core/coordination_engine.py` — ALIGNED (the "no chokepoint" proof)
- **For:** the reactive dispatcher. Takes a deterministic `Plan` and coordinates
  its execution one `Action` at a time — writes the timeline BEFORE each action
  (Rule 1, `_pre_write`), never assumes success (Rule 2, confirmation before
  advance), routes approvals to humans, persists `pending_plan` + `waiting_condition`
  atomically on async waits, and resumes via CAS (`_handle_resume_plan` /
  `_cas_clear_pending_plan`) so redelivery never double-executes.
- **Fit:** this file *is* the manifesto's "coordination through shared state, not
  orchestration through a chokepoint." Verified the header's claims against the
  body: no LLM decides the next action (plans come from
  `DeterministicPlanningEngine`); risky financial writes (`post_bill`,
  `schedule_payment`, `reverse_erp_post`, `freeze_vendor_payments`) route through
  `_evaluate_governance_for_action` with fail-closed + per-box-type gating; the
  Box record (`ap_items`, `audit_events`, `bank_match_boxes`) is the substrate, the
  engine a pure consumer/producer; reversibility is structural via `_pre_write`.
  The honest header note — the name predates the manifesto, `ReactiveDispatcher`
  fits better, rename blast radius (100+ callsites) isn't worth it — stays. Strong.
- **Drift fixed:** (1) line 24 stale package path `clearledgr/core/planning_engine.py`
  → `solden/core/...`. (2) "No LLM in the loop" read literally was slightly
  overclaimed — the engine *does* invoke the LLM gateway for bounded actions
  (`classify_vendor_reply`, `generate_exception_reason`, email parse). Tightened to
  "No LLM in the **decision** loop" with an explicit note that the model reads
  unstructured input + writes operator prose only when the deterministic plan
  already chose that action ("rules decide, the model describes"). Now unimpeachable
  under diligence.
- **Verdict:** aligned; two doc drifts fixed. Tests: engine resume/idempotency/
  governance-event/box-lock/async-hygiene clusters green (55 passed).

### Audit / Exception / Ownership stores — ALIGNED (History + Exceptions + Ownership)
Reviewed as a cluster because they carry three of the five primitives.

- **`solden/core/stores/box_lifecycle_store.py`** (Exceptions + Outcomes) — the
  `box_exceptions` / `box_outcomes` rows: structured, attributable, idempotent,
  cross-box-type, with an org-wide unresolved queue (`list_unresolved_exceptions`).
  **Gap investigated, found NOT a hole:** the structured-row INSERT commits, then a
  narration `audit_events` row is emitted best-effort in a *separate* txn — which
  looked like a torn-write hazard against the codebase's own atomicity bar
  (`test_state_audit_atomicity`, `set_ap_item_owner_atomic`). Traced the read path:
  the reconstructable-record surfaces (`box_export.py`, `box_projection.py`) read
  `audit_events` + `box_exceptions` + `box_outcomes` as three independent sources
  and merge them, so a dropped narration never loses an exception/outcome. State
  transitions DO need atomicity (audit_events is their sole record — and it's
  enforced); exceptions/outcomes have their own atomic single-INSERT tables, so they
  don't. **Fixed:** the docstring claimed "the timeline narrates the lifecycle
  faithfully" (overclaim) → rewrote it to state the real durability model (structured
  row is source of truth; narration is a best-effort mirror; the export/projection
  merge of three sources is what holds the History primitive). **Proven by** a new
  test `test_box_export_api.py::test_export_sources_exceptions_and_outcome_from_structured_tables`
  — raises an exception + records an outcome, asserts both surface in the export by
  their structured fields.
- **`solden/api/box_owner_routes.py`** (Ownership) — clean. Manual reassign override;
  auto-assignment lives in the engine hook + `services/box_owner`; uses the atomic
  owner write; tenant-scoped 404-not-403; "the audit event is the source of truth for
  reassignment history, the column is just current state." No drift.
- **`solden/api/box_exceptions_admin.py`** (Exceptions surface) — strong: org-scoped
  severity-ranked queue, stats, attributed resolve, the cause-clustering exception
  graph (the richest "what's stuck and why" view), tenant isolation with
  defense-in-depth. **Fixed:** stale "lexicographic sort in SQLite" comment (Postgres-
  only since C.2/C.3) → "lexicographic text sort in SQL"; the re-sort logic stays (text
  `severity DESC` mis-orders in Postgres too).
- **`solden/core/box_summary.py`** (read-side context view) — reads `last_3_actions`
  from `audit_events` and open issues from fraud_flags/field_confidences. Honest (no
  fabricated currency). Not the reconstructable record (that's box_export); a 3-item
  agent-context view. No drift.
- **Verdict:** aligned; History/Exceptions/Ownership all hold. 2 doc drifts fixed +
  1 robustness test added. Tests: export/owner/audit-chain/atomicity/exceptions-admin/
  policy-version/entity-scope clusters green (61 passed).

### `solden/core/planning_engine.py` + `solden/services/finance_agent_governance.py` — ALIGNED ("rules decide" + "agent is bounded")
- **planning_engine — For:** the deterministic planner. `plan(event, box_state)` is a
  pure dict dispatch (event type → `_plan_*` method) producing a typed `Plan` of
  `Action`s. No LLM decides the plan; Actions tagged `"LLM"` (classify_email,
  extract_invoice_fields, …) run the model WITHIN an action during execution.
  "Rules decide, the model describes." Missing-handler path raises + records an
  `unhandled_event_type` box_exception (Exceptions primitive — no silent drops);
  `correlation_id` carried for idempotent redelivery (durability).
- **planning_engine — Fit:** this is the manifesto's "rules decide" proof. The VO
  event types are gated dormant before dispatch (`_DEPRECATED_VO_EVENTS`), matching
  the VO-subordinate decision. Strong.
- **planning_engine — Drift fixed:** the dispatch table still labelled the VO planners
  "Vendor onboarding v1.1 — spec §5" as if live, while `plan()` refuses those events
  via the gate above — a reader scanning only the dispatcher would be misled. Added a
  cross-reference noting they're gated dormant / option-value, "do not assume in-the-
  table = live."
- **governance — For:** `evaluate_doctrine` runs four deterministic gates:
  forbidden_actions + belief_alignment (unconditional STATE gates — authority can't
  bypass), promotion_gates + autonomy_policy (earned-autonomy gates — an operator CAN
  override). Risky actions (`_RISKY_ACTIONS`: post_to_erp, auto_approve,
  retry_recoverable_failures, resume_workflow) require *earned* autonomy via quality-
  proof gates; fail-closed; the 2026-05-06 fix records the real gate state in the audit
  row instead of a fake "pass."
- **governance — Fit:** textbook "the agent is bounded." `_RISKY_ACTIONS` tops out at
  `post_to_erp` — no money-movement token, consistent with "Solden never moves money."
  No drift. No stale paths / vendor-name issues.
- **Verdict:** both aligned; 1 planning_engine doc-coherence fix. Tests: planning /
  vo-deprecation / governance / governance-event-path green (56 passed).
