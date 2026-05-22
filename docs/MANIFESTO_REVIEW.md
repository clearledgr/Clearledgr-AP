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
