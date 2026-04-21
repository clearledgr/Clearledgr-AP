# ADR-001: Why the Box is the central abstraction

Status: Accepted
Date: 2026-04-11 (Box Foundation commit `0fad232`)
Author: Mo

## Context

Early Clearledgr modeled the product as "AP invoice automation." The code treated AP invoices as the central domain object: tables were named for AP, methods were named for AP, the audit trail was keyed on `ap_item_id`.

Then a second workflow class showed up: vendor onboarding (a vendor goes through KYC, submits bank details, activates). Then a third was committed: commission clawback (driven by Booking.com's SAP SD direction).

The code couldn't absorb the second one cleanly. `vendor_onboarding_state_transition` events had to pass `ap_item_id=""` to the audit funnel — the "semantic lie" (see `vendor_store.py:1929` before the v42 migration). Clawback would have made it worse.

The product thesis — that finance work needs a persistent home per workflow instance — called this abstraction "the Box." But the code didn't have Boxes. It had AP items that sometimes pretended to be other things.

## Decision

Promote the Box from doctrine to code.

- A **Box** is a persistent home for one workflow instance.
- Each Box has a `box_type` (registered in `clearledgr/core/box_registry.py`) and a `box_id`.
- Every shared primitive (`audit_events`, `llm_call_log`, `pending_notifications`) is keyed on `(box_id, box_type)`.
- The first two registered types: `ap_item`, `vendor_onboarding_session`. Clawback comes later as `clawback_case`.
- Migration v42 drops the `ap_item_id` column on shared primitives after backfilling `box_id` + `box_type`.

## Consequences

**Wins:**
- No more `ap_item_id=""` lies.
- Adding clawback (or any future workflow class) is a registry entry + state machine + a small amount of type-specific handlers. The audit model, idempotency model, and reconstruction model work for free.
- `list_box_audit_events(box_type, box_id)` is a generic reader — every Box's timeline rebuilds the same way.
- `get_box_health` in `metrics_store.py` is parameterized by `box_type`, so Operations Console gets multi-workflow-class health panels without rework.

**Costs:**
- Migration v42 was non-trivial — Postgres-and-SQLite dual-path backfill + append-only trigger recreate. The commit is durable but it's the kind of migration that gets eyed carefully in code review forever.
- `append_audit_event` now requires `(box_id, box_type)` or `ap_item_id`; 30+ callers had to be audited.
- The `_Rule1PreWriteFailed` contract tightened because the pre-write now MUST carry Box context.

## Alternatives considered

- **Keep AP-coupled naming; special-case vendor onboarding forever.** Would have shipped faster but clawback would have broken it. Explicit path-dependency.
- **Separate tables per workflow class** (one `ap_audit_events`, one `vendor_audit_events`, one `clawback_audit_events`). Considered and rejected — the reconstruction model, idempotency model, and cross-Box link model all want one table. Three tables would triple the code surface without adding value.
- **A fully generic "entity" abstraction** (Django-style contenttypes). Rejected as over-engineering. The product has three Box types and will have maybe five at steady state. A registry is enough; a full polymorphic system is not.
