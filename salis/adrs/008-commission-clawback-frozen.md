# ADR-008: Why commission clawback is frozen (not cancelled)

Status: Accepted
Date: 2026-04-21 (freeze decision locked in the 2026-04-21 CEO plan)
Author: Mo

## Context

Commission clawback is the spec'd second workflow class for Clearledgr. Origin: Booking.com design partnership surfaced "we chase hotel sales teams to return overpaid commissions" as a real pain — a Box-shaped workflow where the customer is an enterprise on SAP SD.

The spec is written (`commission-clawback-spec.md`). Some infrastructure work has been pre-committed:
- Box abstraction (ADR-001) was partly motivated by clawback landing as a second Box type cleanly.
- The coordination engine was renamed from "execution engine" partly anticipating more workflow classes.
- Some store-level work (audit events keyed on box_type, cross-Box linking via `box_links`) is partially motivated by clawback.

But the implementation hasn't started. SAP SD is the critical path for clawback and that's a Booking.com-side timing dependency — enterprise sandbox provisioning typically takes weeks or months.

## Decision

**Commission clawback is FROZEN, not CANCELLED.** Specifically:

1. The spec (`commission-clawback-spec.md`) stays in the tree. Changes require explicit un-freeze.
2. No `clawback_case` box type is registered. The BoxType registry in `box_registry.py` has only `ap_item` and `vendor_onboarding_session` at V1.
3. Architecture decisions made partly in anticipation of clawback (Box abstraction, engine rename, audit generalization) stand. They earned their keep even without clawback landing — vendor onboarding benefited the same way.
4. Clawback is un-frozen when: (a) AP V1 is live with a paying customer, AND (b) Booking.com's SAP SD sandbox is accessible. Until both, no clawback implementation work.

## Consequences

**Wins:**

1. Engineering hours go to AP V1 launch (where revenue is) and not to clawback scaffolding (where revenue is months away, blocked on Booking-side timing we don't control).

2. The spec existing now means un-freezing is a continuation, not a restart. When SAP SD access lands, the design is ready.

3. The foundational infrastructure (Box abstraction, coordination engine generalization) already absorbed clawback's architectural requirements. The thaw doesn't require architectural rework.

**Costs:**

1. Booking.com may lose momentum during the freeze. Design-partner relationships cool when no build progress is visible. Mitigation: quarterly design-partner check-ins that don't require code.

2. Features in AP V1 that clawback would reuse (or vice versa) have to be built AP-first, with a potential "redo for clawback" later. Low risk because clawback's requirements fit within the coordination engine's existing contract.

3. The spec file will drift from the codebase reality the longer it's frozen. Before unfreeze, a spec-review pass against the current code is mandatory.

## Alternatives considered

- **Cancel clawback entirely.** Rejected — Booking.com is a genuine design-partner relationship and SAP SD is a lucrative enterprise vertical. Cancelling would torpedo both.

- **Build clawback in parallel with AP V1 launch.** Rejected — violates the "AP-only until V1 launches" scope lock. Engineering capacity doesn't support two workflow classes in active development pre-revenue.

- **Start the scaffolding now (Box type, state machine, one adapter) without full implementation.** Considered. Rejected because even scaffolding pulls focus from AP, and we don't yet know which SAP SD details the spec got wrong. Scaffolding against a spec that hasn't met the sandbox = almost-certainly-will-be-rewritten code. Wait for sandbox.

## Un-freeze checklist (for future Mo / Suleiman)

When you're ready to un-freeze clawback:
1. Re-read `commission-clawback-spec.md`. Has it drifted from how the codebase actually works?
2. Confirm Booking.com SAP SD sandbox access. If not available, there's no point un-freezing.
3. Register `clawback_case` in `box_registry.py`.
4. Add `ClawbackState` enum + `VALID_TRANSITIONS` dict in `clearledgr/core/clawback_states.py`.
5. Add a clawback skill to the planning engine.
6. Add a clawback webhook adapter for SAP SD events.
7. Update DESIGN_THESIS.md to reflect clawback as live V2, not planned V1.1.

## Reference

- `commission-clawback-spec.md` (frozen spec).
- `2026-04-21-path-to-first-customer.md` CEO plan § "Non-goals" — lists clawback among V1 non-goals.
- ADR-001 references clawback as the third Box type that motivated the Box-first refactor.
