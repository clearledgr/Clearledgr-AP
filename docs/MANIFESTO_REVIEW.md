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

### `solden/core/llm_gateway.py` — ALIGNED (the bounded-LLM chokepoint)
- **For:** the single funnel for every LLM call. `LLMAction` enum + `ACTION_REGISTRY`
  whitelist every permitted action with per-action caps (model tier, max output/input
  tokens, timeout); deterministic actions are NOT in the enum and cannot reach the
  model. Budget enforcement + input truncation + retry are centralized here.
- **Fit:** this is the manifesto's "the agent is bounded — rules decide, the model
  describes" enforced structurally. The 14 actions split cleanly into the two permitted
  roles: read unstructured input (classify_email, extract_invoice_fields,
  classify_vendor_response, duplicate_evaluation, po_line_match, single_pass_extract,
  extract_box_fields) and write operator-facing prose (generate_exception_reason,
  explain_state, slack_query, explain_anomaly, narrate_insight, ask_the_agent).
  `DRAFT_VENDOR_RESPONSE` is already gone (zero vendor-facing authoring). Verified
  `agent_planning` does NOT make the executable plan (that's the deterministic engine):
  it writes an advisory needs_info recovery suggestion, steps drawn from a fixed safe
  whitelist (no post_to_erp / money / auto-approve), persisted to metadata for operator
  display, "never executed automatically" (invoice_workflow.py:1296).
- **Drift fixed (structural — a lying surface):** removed `LLMAction.AP_DECISION` from
  the enum + registry. It had **zero callers** (every `"ap_decision"` in the tree is the
  DETERMINISTIC `APDecisionService`'s recommendation string, not a gateway action) AND
  it advertised an LLM-makes-the-AP-decision capability that directly contradicts "rules
  decide, the model never routes." Same class as the vestigial `task_runs` removal. Also
  fixed the leftover `LLMAction.AP_DECISION` reference at the system-prompt branch
  (would have `AttributeError`'d) and added a registry clarifier on `agent_planning`.
- **Verdict:** aligned; 1 dead+contradictory action removed, 1 leftover ref fixed, 2
  clarifiers added. Grep-verified zero callers + `import main` clean. Tests: gateway /
  budget-cap / call-box-link / cost-summary / email-parser / needs-info-recovery green
  (65 passed).

---

## AP domain (the wedge)

### `solden/services/ap_decision.py` — ALIGNED (the "rules decide" half), no fix
- **For:** `APDecisionService.decide()` — the deterministic routing recommendation
  (approve | needs_info | escalate | reject) from a fixed 10-step policy cascade. The
  rules half of "rules decide, the model describes."
- **Fit:** read closely. Confirmed the header is true ("Claude is **not** called here").
  The standout is the LLM-hint handling: `apply_single_pass_hints` is a **downgrade-only
  filter** — the model's advisory output can pull `approve → escalate` on a fraud /
  duplicate signal the rules missed, but there is no path where it pushes toward
  approval. Worst-case model influence is "route to a human." `enforce_gate_constraint`
  is a defensive no-op so nothing can route `approve` past a failed gate. Textbook
  bounded. No drift.

### `invoice_workflow.py` / `finance_agent_runtime.py` / `ap_store.py` — ALIGNED; drift fixed
Invariant sweep across the two large orchestration files (2459 + 2287 lines) +
`ap_store.py`, plus the specific fixes below. **Money-movement invariant verified
clean:** zero `execute_payment` / `send_money` / `initiate_payment` / `transfer_funds` /
`wire_transfer` anywhere in the AP core — consistent with "Solden never moves money."
- **invoice_workflow.py — Drift fixed:** auto-approval attribution was
  `approved_by = "clearledgr-auto:{reason}"` — stale brand in operator-visible audit
  data (the approver shown for auto-approved invoices). Renamed to `"solden-auto:"`.
  Forward-only (audit-correct: historical rows keep their truthful value; we never
  rewrite the trail). Single write site, nothing parses the prefix.
- **ap_store.py — Drift fixed (a lying-wiring docstring + a real retention hole):**
  `reap_expired_audit_exports` was orphaned — defined + unit-tested but **never wired
  into any production sweep**, with a docstring falsely claiming it's "called by the
  orphan-task-runs sweeper at startup" (that sweeper never called it; git -S confirms
  it was never wired). Audit exports carry an `expires_at` TTL by design, so expired
  exports were lingering forever — a data-retention gap on the sovereignty primitive's
  own artifact. **Fixed:** wired it into the hourly background tick
  (`agent_background._run_loop`, `tick % 4 == 0`) and corrected the docstring. Also
  fixed 2 stale `clearledgr/` path comments in the file header + reaper.
- **Note:** the two big orchestration files were invariant-swept (money movement,
  stale paths, deterministic-decision + bounded-LLM call sites), not read line-by-line;
  `ap_decision.py` was read closely. A deeper line read of `finance_agent_runtime.py`
  (the runtime facade) is the one remaining AP-core item if fuller coverage is wanted;
  its durability path (`resume_pending_agent_tasks` → `agent_retry_jobs` drain) was
  already verified in the durable-runtime work.
- **Verdict:** aligned; 1 brand-data drift fixed, 1 orphaned-reaper retention hole
  wired + docstring fixed, 2 stale paths fixed. Tests: audit-export / ap_decision /
  invoice-workflow-controls / runtime-state-transitions green (97 passed). `import main`
  clean.

---

## Surfaces + periphery (invariant sweep)

The boundary files (Slack / Gmail / Teams / ERP adapters, dozens of files) were swept
for the manifesto invariants that matter at the surface, not read line-by-line.

- **Zero vendor-facing text — HOLDS (strongly).** `gmail.send` is deliberately out of
  the OAuth scope list; no send/draft functions exist in `gmail_api.py` (removed
  2026-05-02); every `send_vendor_email` / `draft_vendor_response` handler is gone from
  the engine + planner with explanatory comments. The remaining `send_vendor_*` are
  Slack notifications to OPERATORS (vendor is the subject, not the recipient). The
  dormant `draft_vendor_master_record` lives only on the VO-gated path.
- **No money movement — HOLDS.** Confirmed across AP core; surfaces post to ERP / mark
  ready-to-pay, never move money.
- **No LLM vendor name on operator surfaces — HOLDS.** The "Claude" hits on surface
  files are all docstrings + `logger.*` server logs (internal dev surfaces), not
  operator-facing UI strings. The rule is marketing/operator-visible only; logs and
  docstrings are exempt. No fix.
- **Brand drift fixed (user-visible artifacts):** download filenames
  `clearledgr-account-*.json` (workspace_shell), `clearledgr-*.csv/.pdf`
  (workspace_reports) → `solden-*` (Content-Disposition; what the customer downloads).
  Updated the test that locked the old prefix. Slack digest confidence-note emoji
  `:clearledgr:` (renders literally in the operator's Slack if the custom emoji isn't
  uploaded) → standard `:bar_chart:` (always renders).
- **Deliberately left:** internal Redis namespace keys (`clearledgr:events:*`, consumer
  groups, locks, semaphores), health-endpoint service identifiers (`clearledgr-core`),
  and external config identifiers (XSUAA audience, launch URL) — same intentionally-
  untouched backend-identifier bucket as env vars + the runtime domain; renaming risks
  orphaning live state / breaking monitoring with no customer-facing benefit. The 24
  comment-only stale `clearledgr/` path refs are cosmetic; not churned.
- **Verdict:** invariants hold; 4 user-visible brand drifts fixed (3 filenames + 1
  Slack emoji) + 1 test updated. Tests: workspace-reports / report-export / slack /
  vendor-activation green. `import main` clean.

### `finance_agent_runtime.py` + `finance_agent_loop.py` — ALIGNED, no fix
- **runtime (facade):** stable preview/execute intent-contract seam, decomposed into
  `finance_runtime_*` submodules; generic `ActionContext` (box-type-agnostic); skill
  registry (AP, vendor-compliance, workflow-health, reconciliation, procurement).
  `execute_skill_request` does `_ensure_supported` → idempotency replay (durability,
  no double-execute) → delegates to the loop. Durability: `resume_pending_agent_tasks`
  → `drain_agent_retry_jobs`. No money movement, no stale paths/brand. Read closely.
- **loop:** "observe → recall → deliberate → act → verify → learn." `observe()` builds
  belief + recall + preview + `build_deliberation` (governance); `run_skill_request`
  blocks on `should_execute` and writes a `loop_blocked_by_doctrine` audit row;
  `_emit_plan_observed` gives the sync skill path Rule-1 audit parity with the async
  event path; `attempt_self_recovery` for recoverable failures. This is the bounded
  agent at its core — every action observed, deliberated, audited, then executed.
- **Verdict:** both strongly aligned; no drift found. The facade delegates the bounds
  to the loop; the loop enforces + audits them.

### `agent_memory.py` + learning services (`compounding_learning` / `learning` / `finance_learning`) — ALIGNED, no fix
The "compounding" thesis (the agent improves over time), bounded correctly.
- **Tenant isolation (sovereignty):** every learning table is `organization_id NOT NULL`
  with org in the UNIQUE constraints; services are org-scoped at construction (raise on
  empty org). compounding_learning is Postgres-backed + org-partitioned (per the
  cross-tenant-bleed fix 47ad418a).
- **Durability:** `learning.py` is PG-backed with a TTL write-through cache (PG is the
  source of truth; explicit invariant that every mutator writes through before the next
  destructive reload; graceful DB-failure retry instead of serving stale-empty) — the
  in-memory-only persistence bug (21eba14a) is fixed.
- **Bounded:** learning feeds ADVISORY context (vendor GL patterns, belief, recall) into
  the loop; it does not hold routing authority — the deterministic `APDecisionService`
  still decides. Learning improves data-quality suggestions, not the routing verdict.
- **task_runs remnants confirmed removed** from agent_memory (validates the earlier
  durable-runtime cleanup). No stale paths / brand.
- **Verdict:** aligned; no drift. Tenant-isolated + persisted + advisory.

### ERP adapters (`erp_router` + native intake adapters) — ALIGNED; 1 brand fix
The most sensitive boundary — money movement + the structured-vs-unstructured LLM line.
- **Money-movement boundary correct:** `post_bill` posts a vendor bill (an AP entry —
  "we owe this vendor") to the ERP with an idempotency guard on `erp_reference` (no
  duplicate bills); it pays nothing. Zero `execute_payment` / `initiate_payment` /
  `send_payment` / `process_payment` anywhere in the ERP layer. No `schedule_payment`
  implementation that moves money. Solden writes the AP record; the ERP/bank executes
  payment. "Solden never moves money" holds at its most sensitive point.
- **Structured intake skips the LLM:** the native-intake adapters
  (NetSuite/SAP/QB/Xero) make ZERO LLM-gateway calls — they build `InvoiceData`
  directly from the structured ERP envelope. The model only reads unstructured
  Gmail/Outlook input, exactly as the memory invariant says.
- **Drift fixed:** `field_mapping_catalog.py` docstring had one leftover
  `any-clearledgr-field` in an otherwise-rebranded "Solden → ERP" file → `solden-field`.
- **Left:** internal Redis namespace keys (`clearledgr:erp_rate:*`,
  `clearledgr:erp_refresh_lock:*`) — same untouched-backend-identifier bucket.
- **Verdict:** aligned; 1 brand-consistency fix. Tests: native-intake-pipeline /
  adapter-contracts / field-mapping-posters / api-first green (55 passed).

### Render targets (Gmail / Slack / Teams / NetSuite panel / SAP Fiori) — ALIGNED; 2 doc fixes
- **Roster intact:** the surface files are exactly the canonical 5 render targets
  (`gmail_extension`, `slack_invoices`, `teams_invoices`, `netsuite_panel`,
  `sap_extension`) — no surface added or dropped. Matches `reference_render_targets`.
- **Every surface audits operator actions:** proven by the dedicated audit-integration
  tests (netsuite-panel / sap-fiori / teams) + intake-audit-coverage — operator
  approve/reject on each surface writes an audit row (History primitive at the edge),
  tenant-scoped.
- **Invariants (from the surfaces sweep) hold:** operator-facing only (zero
  vendor-facing send), no money movement, no LLM vendor name in UI strings.
- **Drift fixed:** `sap_extension.py` API-doc token example `<clearledgr-jwt>` →
  `<solden-jwt>`; `slack_invoices.py` comment path `clearledgr/services/...` →
  `solden/services/...` (the file exists there).
- **Left:** Slack interactive action-ID prefixes `cl_erp_approve_*` / `cl_erp_reject_*`
  (matched by `startswith`, coupled to messages already posted in customers' Slack —
  same infra-identifier risk class as Redis keys).
- **Verdict:** aligned; 2 doc-brand fixes. Tests: netsuite/sap/teams audit-integration
  + intake-audit-coverage green (22 passed). `import main` clean.

---

## Coverage summary (this review pass)

Reviewed against the manifesto, top to bottom: the **spine** (box_registry, ap_states,
coordination_engine, the audit/exception/ownership stores, planning_engine + governance,
llm_gateway), the **AP wedge** (ap_decision close + invoice_workflow/ap_store/
agent_background swept), the **agent runtime** (finance_agent_runtime facade +
finance_agent_loop close), the **learning cluster** (agent_memory + 3 learning services),
the **ERP adapters** (money boundary + native intake), and the **5 render targets**
(audit + invariants). Structural fixes: removed the contradictory `LLMAction.AP_DECISION`
and the vestigial task_runs; wired the orphaned audit-export reaper. The five primitives
hold, the agent is bounded (rules decide / downgrade-only model / never moves money /
zero vendor-facing text / audited+reversible), and the architecture is box-type-agnostic.
Full suite: 4286 passed / 0 failed.
