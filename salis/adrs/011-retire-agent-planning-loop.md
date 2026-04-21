# ADR-011: Retire the Claude tool-use planning loop; rules decide, LLM describes

Status: Accepted
Date: 2026-04-21
Author: Mo

## Context

Between February and April 2026 the agent runtime accumulated two parallel implementations:

1. `DeterministicPlanningEngine` + `CoordinationEngine` (spec-compliant; rules produce the Plan, coordinator dispatches actions). This is what the deck and `AGENT_DESIGN_SPECIFICATION.md` describe.
2. `AgentPlanningEngine` (Claude tool-use loop that let Claude call a set of `APSkill` / `CompoundSkill` / `ReconSkill` tools to "plan" its own way through an AP invoice). This was registered at startup and wired into `FinanceAgentRuntime` but — on inspection — was dead code in the hot path. The real AP invoice processing ran through `InvoiceWorkflowService` via the deterministic path.

In parallel, `APDecisionService.decide()` called Claude Sonnet with a forced tool-use schema to pick `approve | needs_info | escalate | reject`. A post-hoc `enforce_gate_constraint` clamped the output when the validation gate had failed. That's LLM-as-router, dressed up — the deck said rules decide, but the code actually had Claude deciding the routing except when the gate overrode it.

The pitch deck's slide 3 says: "Rules decide. LLM describes. No financial write is at the mercy of model judgment." The code did not match that claim.

## Decision

Retire the Claude tool-use planning loop and all supporting abstractions. Demote `APDecisionService` to a deterministic 10-step routing cascade. Claude is called only at the five actions listed in spec §7.1:

1. `classify_email`
2. `extract_invoice_fields`
3. `generate_exception_reason`
4. `classify_vendor_response`
5. `draft_vendor_response`

Concrete changes, landed 2026-04-21 as commits `98ba1e1` → `94c98eb`:

- **Deleted** `clearledgr/core/agent_runtime.py` (`AgentPlanningEngine`, ~670 LOC).
- **Deleted** `clearledgr/core/skills/` entirely (`APSkill`, `CompoundSkill`, `ReconSkill`, `FinanceSkill` base — ~2000 LOC).
- **Removed** env vars `AGENT_PLANNING_LOOP`, `AGENT_LEGACY_FALLBACK_ON_ERROR`, `AGENT_RUNTIME_MODEL`.
- **Rewrote** `APDecisionService`: removed the tool-use schema, prompt builder, few-shot examples, `_call_claude`, `_parse_response` (~620 LOC). The 10-step rule cascade is now the single source of routing truth. Claude is not called in the decision path.
- **Added** drift fences: `TestLLMBoundaryFence` + `TestPlannerActionCoverage` in `test_execution_engine.py` assert exactly 5 actions call the LLM gateway and every planner action has a handler. `test_state_audit_atomicity.py` asserts the `update_ap_item` funnel's UPDATE + audit INSERT commit together or neither.
- **Added** first-class Box lifecycle records: `box_exceptions` + `box_outcomes` tables (migration v43) with mirroring hooks in `update_ap_item`. Customer-admin surface added at `/api/admin/box/exceptions` (Gmail extension UI in `ExceptionsPage.js`).
- **Added** webhook event types `box.exception_raised`, `box.exception_resolved`, `box.outcome_recorded` emitted from `BoxLifecycleStore`.

## Consequences

What this buys:

- **The code now matches the deck's claim.** Customer conversations about the audit story are no longer partially fictional.
- **One planning runtime, one execution runtime.** The cognitive overhead of tracking which engine runs when is gone. `DeterministicPlanningEngine.plan(event, state) → Plan` is the only planning path. `CoordinationEngine.execute(plan)` is the only execution path.
- **Smaller attack surface for prompt injection.** There's no longer a Claude call whose output decides routing. Claude is constrained to description-generation and classification on structured inputs.
- **Drift fences.** Future refactors that try to add a 6th LLM call, a new planner action without a handler, or a torn state+audit commit will fail tests. Invariants are now enforced by the test suite, not just by vigilance.
- **First-class audit artefacts.** Exceptions and outcomes are queryable, filterable, and subscribable. SOC 2 posture is in the product, not a retrofit.

What it costs:

- **~5,600 LOC deleted + ~2,200 LOC added, across ~60 files.** This was a surgical refactor, not a greenfield rewrite, but it touched a lot of test files and wired up a new admin route. Full suite went 2,066 → 2,349 passing.
- **APDecisionService lost the narrative richness Claude provided.** The rule cascade produces 1-2 sentence reasoning strings; Claude used to generate fuller prose. If operator UX needs that back, it can be added via `generate_exception_reason` at the exception surface — not inside the routing decision.
- **V1.2 clawback event types are in the enum but have no planners.** The planning engine now raises on unhandled events (and records a `box_exceptions` row if the event names a Box). If a producer fires a clawback event before we've shipped the planners, the failure is loud rather than silent. Acceptable because there are no clawback producers wired yet.

## Alternatives considered

**Option A: Keep the Claude tool-use loop; just fix the decision service.** Rejected because the tool-use loop was dead code in the hot path — nothing was calling it in production. Keeping it around pollutes the mental model ("which engine is active?") and invites future drift.

**Option B: Keep Claude in APDecisionService but always overlay the rule cascade on top.** Rejected because that's exactly what we had before — Claude decides, rules override. The failure mode is rules agreeing with Claude on `approve` when they shouldn't have (gate passed but rules would escalate on other signals). Cleaner to have rules emit the decision directly, with Claude only enriching narrative if needed.

**Option C: Write the spec to match the code (Claude-decides-routing).** Rejected. The deck makes an explicit customer-facing claim ("rules decide, LLM describes") that is unambiguously the right positioning for a financial-write product. Walking that back would hurt both trust and differentiation vs LLM-first AP vendors.

## References

- Commits: `98ba1e1`, `5d55baa`, `73c3733`, `0dc7e12`, `88773e6`, `e23ac3f`, `c6da2ff`, `94c98eb` (2026-04-21).
- Drift fence tests: `tests/test_execution_engine.py`, `tests/test_state_audit_atomicity.py`, `tests/test_box_lifecycle_store.py`, `tests/test_box_exceptions_admin_api.py`.
- Spec §3 (action space), §4 (planning), §7.1 (LLM boundary) in `../AGENT_DESIGN_SPECIFICATION.md`.
