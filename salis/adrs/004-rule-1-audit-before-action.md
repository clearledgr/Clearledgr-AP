# ADR-004: Why audit rows are written BEFORE actions execute (Rule 1)

Status: Accepted
Date: 2026-04-12 (agent spec §1-§13 audit, first referenced in DESIGN_THESIS.md §7.6 earlier)
Author: Mo

## Context

Clearledgr's commercial promise is a trustworthy audit trail. A finance team adopts us because when something goes wrong — a duplicate bill posts, a payment goes to the wrong vendor, an approval gets skipped — they can reconstruct *exactly* what happened from the Box timeline.

That promise has one failure mode that kills it: **the side effect ran but we have no record of it running.** A bill posts to QuickBooks with no matching audit row. A vendor email goes out with no audit row. A Slack escalation happens with no audit row.

Any of those, even once, breaks the product's compliance story. A customer can't trust an audit trail that has holes.

The naive "log after you act" pattern — log it when it succeeds, log the failure when it fails — has this hole structurally. A crash between the side effect and the log = silent success + missing audit row.

## Decision

**Rule 1: every agent action writes its timeline entry BEFORE it executes.**

Implemented at `clearledgr/core/coordination_engine.py:375` (`_pre_write`):

1. The engine writes an `agent_action:{name}:executing` audit row with the action's full parameters.
2. Only after that row commits does it dispatch to the handler.
3. The handler runs. On success, a `:succeeded`/`:completed` post-write captures the outcome. On failure, a `:failed` post-write captures the error.
4. If the pre-write fails (DB blip, UNIQUE constraint race, storage outage), the engine retries 3 times with backoff. After 3 failures, it raises `_Rule1PreWriteFailed` and the action is **aborted**.
5. An aborted action never side-effects. The Box parks in an exception state with a clear error visible in the audit chain (via the last successful pre-write).

State-transitioning methods (e.g., `transition_onboarding_session_state`) follow the same invariant at a different layer: state UPDATE + audit INSERT share one transaction. If the audit write fails, the state write is rolled back.

## Consequences

**Wins:**
- An audit row exists for every side effect the system initiated. Reconstructing a Box's history from the timeline alone is always possible — the timeline IS the state.
- A customer can prove to their auditor that every bill their AP team approved went through the documented process. Zero silent posts.
- The bug class "ran but didn't log" is eliminated at the engine layer, not per-handler. Developers can't forget to log; the engine does it.
- Under concurrent writes, the `audit_events.idempotency_key` UNIQUE constraint catches duplicate-firing races (see ADR-007).

**Costs:**
- Audit write latency is on the critical path for every action. Measured worst case: ~5ms per action for SQLite, ~15-40ms for Postgres under concurrent load. Real but acceptable.
- The pre-write write amplifies storage. One action = 2-3 audit rows (pre-write, handler's own row if applicable, post-write). The timeline table grows faster than a "log-on-success" model. Not a concern at V1 volume; will need partitioning at some scale.
- Handlers that fail at network or ERP layer still produce a pre-write row. Timelines have "executing" rows with matching "failed" rows — noise for rare failures. Acceptable for the correctness guarantee.

## Alternatives considered

- **Log after success.** Rejected — has the silent-success hole described in Context.
- **Two-phase commit between audit DB and side-effect target.** Rejected — we don't control the target (QuickBooks, Slack), so 2PC isn't available.
- **"Best-effort audit" with a separate reconciler sweeping for missing rows.** Rejected — a reconciler that reads QuickBooks to discover our own posts is a bizarre inversion of authority, and it assumes the external system's records are reliable enough to trust after the fact. They aren't (QuickBooks has its own sync delays).
- **Write-ahead log with replay.** Same primitive as our current Rule 1, just more formal. Considered — the current implementation IS effectively a write-ahead log, just with audit_events as the log. No reason to add a separate WAL layer.

## Reference

Primary source: `clearledgr/core/coordination_engine.py:375` (`_pre_write`) and `clearledgr/core/coordination_engine.py:73` (`_Rule1PreWriteFailed`).
State+audit atomicity reference implementation: `clearledgr/core/stores/vendor_store.py:1785` (`transition_onboarding_session_state`) — copies state UPDATE + audit INSERT into one transaction.
Regression fence: `tests/test_box_invariants.py`.
