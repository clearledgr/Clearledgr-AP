# ADR-007: Why idempotency piggybacks on audit_events instead of a new table

Status: Accepted
Date: 2026-04-20 (hardening pass this session; earlier `decision_idempotency_key` on approvals predated it)
Author: Mo

## Context

API-level idempotency protects mutating endpoints: if a client retries the same request (network blip, user double-click, front-end retry loop), the second call should return the same response as the first without re-executing the side effect.

The canonical pattern (Stripe) is:
1. Client sends `Idempotency-Key: <uuid>` header.
2. Server stores `(key, response_body, status_code, expires_at)` somewhere.
3. On retry with the same key, the stored response is returned; the handler never re-runs.

The obvious implementation is a dedicated `idempotency_responses` table. We considered it. We chose something else.

## Decision

**Idempotency responses are stored as audit_events rows** with:
- `event_type = "api_idempotent_response"`
- `idempotency_key` set (the column already exists UNIQUE on audit_events)
- `payload_json.response` = the cached response body
- `box_id` + `box_type` = the action's Box context, or a synthetic `api_action` shell for endpoints without a Box

Helper module: `clearledgr/core/idempotency.py` — `load_idempotent_response`, `save_idempotent_response`, `resolve_idempotency_key`.

Wired into 8 action endpoints:
- `POST /extension/post-to-erp`
- `POST /extension/submit-for-approval`
- `POST /extension/reject-invoice`
- `POST /api/ap/items/{id}/retry-post` (header-only)
- `POST /api/ap/items/{id}/snooze`
- `POST /api/ap/items/bulk-approve`
- `POST /api/ap/items/bulk-reject`
- `POST /api/ap/items/bulk-snooze`
- `POST /api/ap/items/bulk-retry-post`

## Consequences

**Wins:**

1. **One UNIQUE column, one writer path.** `audit_events.idempotency_key` already had a UNIQUE constraint. `append_audit_event` already handled the race between concurrent writers (pre-check + INSERT + UNIQUE-violation catch → return existing row). Piggybacking reuses that battle-tested path.

2. **Zero schema surface added.** No new table. No new migration. No new query patterns to profile. The load-bearing primitive (UNIQUE on a column in audit_events) was already there.

3. **Responses live next to the action audit.** When debugging an idempotent replay, a developer can see the action's pre-write, post-write, AND the cached response in the same `list_box_audit_events` output. One story, one place.

4. **Cross-race safety inherited for free.** Two simultaneous POSTs with the same idempotency key: both try to save-then-return. The UNIQUE constraint fires on the second. The helper treats that as "someone else persisted it" and returns the winner's row. Same primitive the audit funnel already uses.

**Costs:**

1. **audit_events grows faster.** Every idempotent request adds one row. At pre-seed volumes, not a concern. At 100k/day it'd need partitioning anyway; the idempotency rows are a small fraction of the growth.

2. **Slight box_type pollution.** Bulk endpoints don't have a single Box, so they write with `box_type='api_action'`. This introduces a "fake" box type that's only meaningful for idempotency records. Acceptable — the registry doesn't register it, and queries that drill down on real box types (ap_item, vendor_onboarding_session) filter by type anyway.

3. **Can't trivially expire old idempotency records independently of the audit trail.** A cleanup sweep against audit_events would have to filter by event_type. Today this is fine (nothing expires audit_events). If a retention policy ever lands, we'd need the filter.

## Alternatives considered

- **Dedicated `idempotency_responses` table.** Cleaner in the "table per concern" sense. Rejected because the UNIQUE + race-safe-insert primitive is already in `append_audit_event`; duplicating it in a new table = duplicate code that has to stay in sync.

- **Redis with TTL.** Fast path, volatile. Rejected because losing idempotency records to a Redis flush = duplicate ERP posts when clients retry. The whole point is durability across process/infra failures.

- **No server-side idempotency; require clients to handle it.** Rejected because our clients include Slack (interactive handlers can fire multiple times) and Gmail webhooks (Google Pub/Sub retries). Server has to handle it.

- **Application-layer dedup via `task_runs`.** The `task_runs` table has `idempotency_key` too. Considered — could we route all API idempotency through the task runner? Rejected because task_runs is scoped to agent planning loops; API actions that don't spin up a task run don't belong there.

## Reference

Primary surface: `clearledgr/core/idempotency.py`, `clearledgr/core/stores/ap_store.py:1648` (`append_audit_event` with idempotency pre-check).
Regression fence: `tests/test_endpoint_idempotency.py` (11 tests, helper-level + endpoint-level).
Design note: header wins over body field (Stripe convention). See `resolve_idempotency_key`.
