# Clearledgr AP v1 Backend Contracts (Canonical AP Surfaces)

This document captures the AP v1 backend contracts used by embedded operator surfaces (Gmail, Slack, Teams) and AP workflow integrations.

It is aligned with the canonical doctrine and launch-gate requirements in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

## Scope

This document covers AP v1 contracts for:
1. Gmail extension worklist/workspace
2. AP item context and audit retrieval
3. Slack/Teams approval action semantics
4. AP state machine and transition enforcement expectations
5. ERP posting result and idempotency contracts

Out of scope:
1. Legacy reconciliation and Sheets contracts
2. Non-AP workflow contracts
3. Consumer messaging channels

## Shared Conventions

### Authentication and authorization
1. Endpoints may use JWT bearer auth, API keys, session tokens, or verified signed callbacks depending on the surface.
2. Channel callbacks (Slack/Teams) must use channel-native signature/security verification.
3. All mutating AP actions must be authorized at org scope and evaluated against server policy/state rules.

### Tenant / organization scope
1. All AP data is organization-scoped.
2. Requests and responses must not leak cross-tenant data.
3. `organization_id` may be passed explicitly or derived from authenticated user/session/context depending on route.

### Time and IDs
1. Timestamps should be ISO-8601 in API payloads.
2. IDs should be treated as opaque strings.
3. Idempotency/correlation IDs must be propagated for approval and posting actions.

### Error contract principles
Errors returned to embedded surfaces should be:
1. operator-safe
2. reason-coded where possible
3. actionable (what failed, what next)

---

## 1. Gmail Extension -> Backend Contracts

### 1.1 Worklist (invoice-centric AP list for Gmail)

**Endpoint**

`GET /extension/worklist`

**Purpose**

Returns the AP worklist for the Gmail embedded experience. This is the canonical list endpoint for the invoice-centric AP workspace.

**Contract expectations (minimum fields per item)**

1. `id`
2. `state`
3. `vendor_name`
4. `invoice_number`
5. `amount`
6. `due_date`
7. `confidence`
8. `exception_code`
9. `exception_severity`
10. `next_action`
11. `source_count`
12. `primary_source`
13. `merge_reason`
14. `has_context_conflict`
15. `requires_field_review`
16. `confidence_blockers`

**Notes**
1. Gmail should render one active item at a time, using this worklist as the active-record source.
2. `Pipeline` should use the same canonical worklist contract as its queue/control-plane source.
3. Legacy `/extension/pipeline` may exist for compatibility but is not the preferred AP v1 worklist contract.

### 1.2 AP item context (cross-system context for selected invoice)

**Endpoint**

`GET /api/ap/items/{ap_item_id}/context`

**Purpose**

Returns normalized, operator-safe context for the selected AP item across linked sources and connected systems.

**Context sections (conceptual)**
1. Email/source summary
2. Web/portal context (if available)
3. Approval context (Slack/Teams)
4. ERP status / references
5. Validation / policy / budget / PO summaries

**Contract expectations**
1. Context must surface blockers and required actions clearly.
2. Technical payload fragments should not be required for normal operator decisions.

### 1.3 AP item audit trail (progressive disclosure)

**Endpoint**

`GET /api/ap/items/{ap_item_id}/audit`

**Purpose**

Returns audit events/breadcrumbs for AP workflow transparency and support/debugging.

**Contract expectations**
1. Events are append-only from the API consumer perspective.
2. Responses are safe for embedded display (sensitive payloads redacted as needed).
3. Event ordering is stable and timestamped.

### 1.4 Confidence verification / review support (AP v1 gating)

The Gmail experience may call confidence verification/review-related endpoints to:
1. evaluate posting eligibility
2. present field-level mismatches or blockers
3. collect override justification

If multiple endpoints exist for this in the codebase, the AP v1 rule is:
- **critical-field confidence gating must be enforced server-side**
- the UI may assist, but cannot be the final enforcement layer

---

## 2. Slack and Teams Approval Action Contract (Common Semantics)

Slack and Teams must support the same AP decision semantics at v1 GA.

### 2.1 Canonical action payload (normalized)

Minimum normalized fields:
1. `ap_item_id`
2. `run_id`
3. `action` (`approve`, `reject`, `request_info`)
4. `actor_id`
5. `actor_display`
6. `reason` (required for reject; required where policy demands)
7. `source_channel` (`slack` or `teams`)
8. `source_message_ref`
9. `request_ts`
10. `idempotency_key` (or equivalent correlation key)

### 2.2 Behavioral requirements (server-side)

1. Callbacks/actions must pass channel-native verification.
2. Duplicate callbacks or repeated button clicks must be idempotent.
3. Server enforces legal AP transitions before applying decision.
4. Server enforces policy requirements (for example reason-required overrides).
5. Every accepted or rejected action attempt is auditable.

### 2.3 Result semantics (operator-facing)

Embedded channel responses should communicate one of:
1. `accepted` (action applied)
2. `rejected` (invalid or unauthorized)
3. `duplicate_ignored`
4. `stale_action`
5. `blocked_by_policy_or_state`

These should map to operator-safe messages in Slack/Teams cards.

### 2.4 Callback precedence matrix

Slack and Teams callbacks must resolve in this order before dispatch:

1. `duplicate_callback`
2. `stale_action`
3. `superseded_by_state_*` when workflow has already moved beyond the approval window
4. `blocked_by_policy_or_state`
5. dispatch to the workflow runtime

This keeps both approval surfaces aligned when duplicate, late, or superseded decisions arrive.

---

## 3. AP State Machine Contract (Server-Enforced)

This document references the canonical AP state machine in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

### Core enforcement rules
1. Clients may request actions, not arbitrary state assignments.
2. Server validates transitions against legal paths.
3. Illegal transitions are rejected and logged.
4. Resubmissions after terminal rejection create new AP items with linkage metadata.
5. Retry posting is legal only from documented retry states (for example `failed_post`).

### Transition side effects (minimum)
For each state transition or external action, server should ensure:
1. policy and precondition checks are evaluated
2. audit event is written
3. user-facing status is consistent across Gmail and channel surfaces

---

## 4. ERP Posting Contract (API-First, Idempotent)

### 4.1 Posting preconditions

ERP posting must only be attempted when:
1. AP item is in a legal posting state (for example `ready_to_post`)
2. required approvals are complete
3. critical-field confidence gate is satisfied or explicitly overridden
4. policy and validation blocks have been resolved or overridden per policy

### 4.2 Idempotency requirements

1. Every posting attempt must include or derive an idempotency key.
2. Repeated requests with the same logical action must not create duplicate ERP transactions.
3. Retry attempts and outcomes must be auditable.

### 4.3 ERP post response contract (normalized)

Minimum normalized fields:
1. `erp_type`
2. `status`
3. `erp_reference` (required on success)
4. `idempotency_key`
5. `error_code` (normalized; required on failure)
6. `error_message` (operator-safe where surfaced)
7. `raw_response_redacted` (where stored/returned)

### 4.4 Failure semantics

On posting failure:
1. AP item transitions deterministically to a failure/retry state (for example `failed_post`)
2. audit event is written
3. user-facing surfaces show an actionable exception and next step

### 4.5 Browser fallback (if enabled)

If a browser-based posting fallback exists for some ERP/workflow paths:
1. API-first remains default
2. fallback must be policy-gated
3. preview + confirmation is required for high-risk/mutating actions
4. planned and executed actions must be audited

---

## 5. Audit Event Contract (AP v1)

### 5.1 Minimum audit event fields

AP audit events should support at least:
1. `id`
2. `organization_id`
3. `ap_item_id`
4. `timestamp`
5. `actor_type`
6. `actor_id`
7. `event_type` / `action`
8. `prev_state`
9. `new_state`
10. `result`
11. `payload_json` (redacted where needed)
12. `correlation_id` / `idempotency_key`

### 5.2 Coverage requirements (launch-critical)

Audit coverage must include:
1. validation outcomes
2. exception detection and resolution
3. approval requests and decisions
4. ERP posting attempts and outcomes
5. retries
6. overrides (confidence/policy/fallback)

AP v1 GA requires audit completeness as a launch gate (see `PLAN.md`).

---

## 6. Compatibility and Legacy Notes

This repository still contains legacy and experimental routes (including reconciliation- and Sheets-related paths). They may appear in OpenAPI docs, but they are not the canonical AP v1 workflow contract unless explicitly referenced in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- this document

When there is a conflict:
1. `PLAN.md` wins for doctrine and launch gates
2. this file wins for AP v1 backend contract interpretation
3. exhaustive route listing remains available via runtime OpenAPI (`/docs`) but should not be treated as product-scope truth
