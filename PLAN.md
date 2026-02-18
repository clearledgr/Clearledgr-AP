# Clearledgr Execution Layer Build Plan (AP-First to Multi-Workflow)

## Summary
This plan builds Clearledgr as an embedded finance execution layer with **AP as the first shipped workflow** and a clear path to reconciliation and FP&A afterward.

Locked decisions for this plan:
- **Launch scope:** AP-only first, production-grade.
- **Data store:** Postgres as source of truth from day one (multi-tenant).
- **Approval channels at launch:** Slack and Teams (Slack implemented first, Teams parity completed before GA).
- **Primary ERP connector:** NetSuite first (mock retained for local/dev).
- **Deployment:** Multi-tenant cloud from day one.
- **UX:** Embedded only in existing tools. No standalone dashboard app.

---

## 1. Target System Definition

## 1.1 Product boundary (v1 ship)
Clearledgr AP v1 executes:
1. Email intake from Gmail
2. Invoice extraction and deterministic validation
3. Approval orchestration in Slack/Teams
4. ERP posting (NetSuite first)
5. Immutable audit trail
6. Email thread outcome updates

Out of scope until AP is stable in production:
- Reconciliation workflows
- Close workflows
- FP&A aggregation
- New destination dashboards

## 1.2 Runtime surfaces
- Gmail extension (InboxSDK): inline thread card + minimal queue state list.
- Slack and Teams: approval actions and exception decisions.
- ERP: NetSuite connector.
- Backend API: orchestration, state machine, audit, connector orchestration.
- Postgres: single source of truth for AP items, approvals, and audit events.

---

## 2. Canonical Workflow Contract

## 2.1 AP state machine (server-enforced, deterministic)
Primary:
- `received -> validated -> needs_approval -> approved -> ready_to_post -> posted_to_erp -> closed`

Exceptions:
- `validated -> needs_info`
- `needs_approval -> rejected`
- `ready_to_post -> failed_post`

Resubmission semantics:
- Rejected item is terminal.
- Resubmission creates a **new AP item** with metadata:
  - `supersedes_ap_item_id`
  - `supersedes_invoice_key`
  - `resubmission_reason`

No client may force state transitions directly.

## 2.2 Idempotency and dedupe
- Deduplication key: normalized `invoice_key` plus optional attachment hash checks.
- Transition idempotency: every external action uses stable idempotency keys.
- Posting idempotency: one ERP post per AP item unless explicit retry path from `failed_post`.

---

## 3. Architecture and Components

## 3.1 Backend modules
1. **Intake service**
   - Receives triage payloads and Gmail webhook-triggered processing.
   - Classifies AP relevance and parses body/attachments.
2. **Workflow engine**
   - Owns AP item lifecycle and legal transitions.
   - Enforces approval gating and posting preconditions.
3. **Approval adapter layer**
   - Slack adapter and Teams adapter with common action contract.
4. **ERP adapter layer**
   - NetSuite adapter first, mock adapter for local tests.
5. **Audit service**
   - Append-only event writer.
   - Query by AP item and by thread/message.
6. **Gmail thread updater**
   - Idempotent label and note updates after major outcomes.

## 3.2 Extension modules
1. InboxSDK sidebar renderer only.
2. Queue manager for periodic scan and backend sync.
3. Background worker for Gmail API/OAuth and attachment retrieval.
4. No dashboard routes, no generic navigation, no non-AP views.

---

## 4. Data Model and Interfaces

## 4.1 Core tables (Postgres)
1. `ap_items`
   - tenant/org scope, source refs, extracted fields, state, approval metadata, ERP ref, error fields.
2. `audit_events` (append-only)
   - `id, org_id, ap_item_id, ts, actor_type, actor_id, event_type, prev_state, new_state, payload_json, external_refs, idempotency_key`
3. `approvals`
   - channel (`slack` or `teams`), message/activity refs, approver, status, reason, timestamps.
4. `oauth_tokens`
   - Gmail, Slack, Teams tokens where applicable.
5. `erp_connections`
   - NetSuite credentials/config per tenant.

## 4.2 Required backend APIs
Existing AP routes remain and are hardened. Add or finalize:
- `POST /extension/triage`
- `POST /extension/submit-for-approval`
- `GET /extension/pipeline`
- `POST /api/slack/actions` (verified signatures)
- `POST /api/teams/actions` (verified signatures)
- `POST /api/audit/events`
- `GET /api/ap/items/{id}`
- `GET /api/ap/items/{id}/audit`
- `GET /api/ap/items/by-thread/{thread_id}`
- `GET /api/ap/items/by-thread/{thread_id}/audit`
- `POST /api/ap/items/{id}/retry-post` (legal only from `failed_post`)

## 4.3 External action payload contracts
Approval action payload (normalized for Slack/Teams):
- `ap_item_id`
- `run_id`
- `action` (`approve` or `reject`)
- `actor_id`
- `actor_display`
- `reason` (required for reject)
- `source_channel`
- `source_message_ref`
- `request_ts`

ERP post response contract:
- `status`
- `erp_reference_id` (required on success)
- `raw_response_redacted`
- `error_code/error_message` (on failure)

---

## 5. Security, Compliance, and Reliability

1. Multi-tenant isolation
   - Every row scoped by `organization_id`.
   - No cross-tenant queries.
2. Signature verification
   - Slack: HMAC `v0` verification with timestamp replay protection.
   - Teams: JWT/signature verification via Microsoft channel security.
3. Secret handling
   - All secrets from env/secret manager, never persisted plaintext in UI storage.
4. Audit immutability
   - Insert-only audit rows.
   - No update/delete endpoints for audit events.
5. Retry model
   - Exponential backoff for transient failures.
   - Explicit dead-letter state through `failed_post` and `needs_info`.
6. No silent failure
   - User-safe surfaced status in Gmail.
   - Ops logs with correlation IDs.

---

## 6. Implementation Phases

## Phase 0: Baseline hardening (current AP code stabilization)
1. Final AP-only route and module pruning.
2. Remove duplicate/legacy route registrations.
3. Harden parser correctness for common invoice formats.
4. Stabilize scan lifecycle and pagination.
5. Ensure no client-side approve/reject bypasses.

Exit criteria:
- AP queue auto-populates without manual rescan.
- No deprecated InboxSDK usage.
- No dashboard or non-AP code paths reachable.

## Phase 1: AP v1 GA completion
1. Postgres-first configuration and migration path from SQLite dev.
2. Final Slack + Teams approval adapter interface.
3. Teams callback endpoint with signature verification.
4. NetSuite connector implementation:
   - create payable/bill
   - return stable ERP reference
   - idempotent post keys
5. Gmail thread update idempotency and consistent labels.
6. Complete runbook, architecture docs, and operational alerts.

Exit criteria:
- AP item flows email to approval to ERP to audit without manual stitching.
- Both Slack and Teams approvals supported in production.
- Every transition and external action audited.

## Phase 2: AP scale and operations hardening
1. Throughput improvements for multi-invoice inboxes.
2. Tenant-level policy engine (thresholds, required docs, vendor rules).
3. Approval SLA timers and escalation routing.
4. Operational observability:
   - queue lag
   - approval latency
   - post failure rate
   - per-tenant health.

Exit criteria:
- Stable multi-tenant ops under expected production load.
- Error budgets and oncall runbooks proven.

## Phase 3: Post-AP expansion to execution layer
Only after AP SLOs are met for sustained period:
1. Reconciliation execution workflow
2. FP&A data aggregation workflow
3. Shared orchestration framework reused from AP primitives (state machine, audit, adapters)

---

## 7. Testing and Acceptance Criteria

## 7.1 Automated tests (minimum required)
1. Intake creates AP item in `received`.
2. Validation routes to `validated` or `needs_info`.
3. Illegal transitions are rejected server-side.
4. Reject path writes actor, reason, timestamp, audit.
5. Resubmission after reject creates new AP item with `supersedes` metadata.
6. Slack signature verification: invalid rejected, valid accepted.
7. Teams callback verification: invalid rejected, valid accepted.
8. Approval idempotency: repeated callback does not duplicate transitions/posts.
9. ERP post success persists `erp_reference_id` and transitions to `closed`.
10. ERP post failure transitions to `failed_post` and writes audit.
11. Gmail thread update invoked and idempotent after post/reject/needs_info.
12. Pipeline endpoints return AP-only states.

## 7.2 End-to-end scenarios
1. Single invoice email, approval, NetSuite posting, thread updated.
2. Multi-invoice same vendor day, duplicate prevention and correct grouping.
3. Rejected invoice resubmitted with corrected attachment.
4. Backend outage during scan then recovery without data loss.
5. Slack outage fallback to Teams approvals (and inverse).

## 7.3 Production readiness gates
- 0 unauthorized state transitions in logs.
- 0 unaudited transitions/actions.
- >99% callback signature verification pass/fail correctness.
- No duplicate ERP posts for identical idempotency keys.
- Tenant data isolation checks pass.

---

## 8. Rollout Plan

1. Internal dogfood tenant with mock ERP.
2. Pilot tenant with NetSuite sandbox.
3. Controlled production launch:
   - feature gates per tenant
   - channel-by-channel enablement (Slack and Teams)
4. Full production with SLO dashboards for operators only (not product dashboards).

Rollback:
- Disable ERP posting per tenant while preserving intake and approvals.
- Keep AP items in `ready_to_post` for retry after incident resolution.

---

## 9. Documentation Deliverables

1. `README.md` updated to AP-only execution scope.
2. `ARCHITECTURE.md` with module and data-flow diagrams.
3. `RUNBOOK.md` covering:
   - local start
   - OAuth setup
   - Slack/Teams callback testing
   - NetSuite sandbox testing
   - failure triage and retry steps
4. `DECISIONS.md` with locked semantics and channel/ERP choices.

---

## 10. Assumptions and Defaults

1. AP-only remains the only shipped workflow until AP SLOs are met.
2. Postgres is mandatory for production; SQLite allowed for local development.
3. NetSuite is first real ERP connector; mock mode retained for tests/dev.
4. Slack and Teams both must support approval actions at launch.
5. Gmail extension remains InboxSDK-only with minimal queue and inline context.
6. No standalone web app or dashboard is introduced.
7. Audit trail is backend source of truth; extension may cache for display only.
