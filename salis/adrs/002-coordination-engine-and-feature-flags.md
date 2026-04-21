# ADR-002: Why one coordination engine + feature-flagged surfaces

Status: Accepted
Date: 2026-04-12 (`CoordinationEngine` rename + §5 spec lock)
Author: Mo

## Context

The early architecture had separate "execution" code paths for each surface: Slack handlers called their own services, Teams handlers called their own services, Outlook autopilot had its own loop, the ERP posting path had a bespoke retry. Each surface treated workflow state as a local concern.

This was building toward the classic sprawl: N surfaces × M workflows × K actions = a maintenance pit.

The product's real shape is different: one workflow at a time advances through state transitions, with surfaces as adapters. A Slack approve button and a Teams approve button should do the SAME thing — advance the Box to `approved` and fire `approve_invoice`. Different surface, same action.

## Decision

One **CoordinationEngine** (formerly "ExecutionEngine") drives every Box. All surfaces dispatch into it through the same contract:

- `CoordinationEngine.execute(plan: Plan)` consumes a `Plan` (a list of `Action`s)
- Each `Action.name` maps to one handler via `_register_handlers`
- Surfaces produce Plans (via the planning loop) or trigger specific Actions (via the agent-intent API) — they don't contain workflow logic themselves

V1.1 surfaces (Teams, Outlook) live in the tree but are **feature-flagged off**:

- `FEATURE_TEAMS_ENABLED=false` by default
- `FEATURE_OUTLOOK_ENABLED=false` by default
- The strict runtime profile (`main.py:252`-on) removes V1.1 routes from the production mount

## Consequences

**Wins:**
- Adding Teams support on a customer's sign-date is a flag flip + per-tenant Microsoft bot registration (~1-2 days of operational work), not a 3-week engineering scramble mid-sales-cycle.
- The workflow logic exists once. A bug in `approve_invoice` is fixed once for every surface.
- New Box types (see ADR-001) reuse the engine. Clawback won't need its own engine.
- Unit tests against `CoordinationEngine` directly are authoritative — they test the contract every surface must honor.

**Costs:**
- Routes + code for V1.1 surfaces live in the tree and incur maintenance (test-run time, dependency updates, code-review surface).
- The first-time reader has to understand the engine before any surface makes sense. `handoff-tour.md` + this ADR are how we absorb that cost.
- Feature flags that aren't exercised can rot. A periodic CI job that enables all flags and runs the full suite catches this — not built yet (see TODOS).

## Alternatives considered

- **Separate services per surface** (Teams-worker, Outlook-worker, Slack-worker). Rejected — triples the ops footprint for features not yet earning revenue.
- **Build Teams/Outlook only when a customer signs who needs it.** Rejected for sales-cycle reasons (see ADR-005 for the same argument applied to ERPs). The engineering cost of "just ship Teams now" is small; the cost of "we need Teams in 3 weeks" mid-deal is the deal.
- **Single monolith without the engine abstraction.** Rejected — without a central coordinator, Rule 1 (audit-before-action, see ADR-004) can't be enforced universally. The engine is how Rule 1 stops being "a pattern everyone has to remember" and starts being "a thing that can't go wrong."
