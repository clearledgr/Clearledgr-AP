# Clearledgr GA Gaps & Fixes Tracker

**Generated:** 2026-02-25
**Source:** Codebase audit vs PLAN.md (canonical doctrine, 2026-02-25)
**Status legend:** ✅ Fixed | 🔧 In Progress | ❌ Open | ℹ️ Doc/Process

---

## Summary Table

| # | Gap | Severity | Effort | Status | File(s) |
|---|-----|----------|--------|--------|---------|
| 1 | Teams not co-equal to Slack (security, card update, metadata) | Critical | M | ✅ Fixed | `teams_verify.py`, `teams_api.py`, `teams_invoices.py` |
| 2 | SAP GL account lookup is a stub | Critical | M | ✅ Fixed | `clearledgr/services/erp/sap.py` |
| 3 | SAP status polling not implemented | Critical | M | ✅ Fixed | `clearledgr/services/erp_sync.py` |
| 4 | No ERP readiness checklists populated | Critical | L | ℹ️ Templates Created | `docs/ERP_READINESS_CHECKLISTS.md` |
| 5 | Workflow crash recovery (no checkpoint semantics) | High | L | ❌ Open | `clearledgr/services/invoice_workflow.py` |
| 6 | Correlation IDs not systematically propagated | High | S | ✅ Fixed | `main.py`, `clearledgr/api/slack_invoices.py`, `teams_invoices.py` |
| 7 | Extraction correction rate not tracked | High | S | ✅ Fixed | `clearledgr/api/ops.py` |
| 8 | Teams card not updated after action | High | S | ✅ Fixed | `clearledgr/services/teams_api.py`, `teams_invoices.py` |
| 9 | No automated post-GA monitoring thresholds | High | M | ❌ Open | Requires external alerting wiring |
| 10 | `exception_code`/`exception_severity` in metadata blob | Medium | M | ✅ Fixed | `clearledgr/core/database.py`, `clearledgr/core/stores/ap_store.py` |
| 11 | Teams metadata asymmetric vs Slack | Medium | S | ✅ Fixed | `clearledgr/core/database.py`, `clearledgr/api/teams_invoices.py` |
| 12 | No runbooks or operator procedures | Medium | M | ℹ️ Created | `docs/RUNBOOKS.md` |
| 13 | Resubmission flow not end-to-end verified | Medium | S | ✅ Verified | `clearledgr/api/ap_workflow.py` |
| 14 | Gmail worklist endpoint unauthenticated | Medium | S | ✅ Fixed | `clearledgr/api/gmail_extension.py` |
| 15 | `approve_invoice()` override flags are ad-hoc booleans | Medium | S | ✅ Fixed | `clearledgr/core/ap_states.py`, `clearledgr/api/gmail_extension.py` |
| 16 | Browser fallback E2E test coverage thin | Medium | M | ❌ Open | Tests needed |
| 17 | Gmail watch expiry not surfaced in health check | Low | S | ✅ Fixed | `clearledgr/api/admin_console.py` |
| 18 | Durable DB queue has no dead-letter visibility | Low | M | ❌ Open | Ops surface needed |

---

## Remaining Open Items (Post-Pilot)

### Gap #5 — Workflow crash recovery
**Root cause:** `approve_invoice()` in `clearledgr/services/invoice_workflow.py` is a single async
coroutine spanning 4 state transitions + ERP post. A mid-execution crash leaves the AP item in a
transitional state with no automatic recovery.

**Required:** Decompose into idempotent, re-entrant steps. Each state transition must be safe to
re-execute if the item is already in the target state. The ERP post step must be independently
retryable via idempotency key without re-running approval logic.

**Approach:** Introduce a `resume_workflow(ap_item_id)` method that inspects current state and
re-dispatches from the correct step. Wire this into the durable retry queue on startup.

---

### Gap #9 — Post-GA monitoring thresholds
**Required by PLAN.md §8.5:**
- Elevated post failure rate threshold → trigger per-tenant ERP disable
- Connector-specific degradation → alert + disable
- Audit write failures → PagerDuty/alerting
- Duplicate posting incidents → circuit breaker

**Approach:** Add threshold evaluation to the durable retry worker loop; emit structured
alert events when rate thresholds are crossed. Wire to Slack digest endpoint.

---

### Gap #16 — Browser fallback E2E test
**Required:** A test covering: API fail → fallback dispatch → macro preview → confirmation
capture → live execution → `posted_to_erp` state transition → reconciliation.

---

### Gap #18 — Dead-letter queue ops surface
**Required:** An ops endpoint `/api/ops/retry-queue` that lists stuck/dead-lettered retry jobs
with their error, backoff state, and manual retry/skip actions.

---

## Completed Fixes (Detail)

### Gap #1 — Teams security + card update
- **teams_verify.py:** Added `MAX_REQUEST_AGE_SECONDS = 300` and `iat` claim validation inside
  `verify_teams_token()`. Requests with `iat` older than 5 minutes now raise HTTP 401.
- **teams_api.py:** Added `update_activity()` method using Bot Framework REST API
  (`{service_url}/v3/conversations/{conversation_id}/activities/{activity_id}`).
  Token acquired via client credentials OAuth flow with `TEAMS_APP_ID` + `TEAMS_APP_PASSWORD`.
- **teams_invoices.py:** After successful dispatch, extracts `serviceUrl` + `activityId` from
  Teams payload and calls `TeamsAPIClient.update_activity()` with result card.

### Gap #2 — SAP GL account lookup
- **erp/sap.py:** `list_gl_accounts()` now calls the SAP OData API (`{base_url}/ChartOfAccounts`)
  when `dry_run=False`. Returns real GL accounts from SAP. Falls back to empty list (not mock)
  on failure. Requires `SAP_BASE_URL` and `SAP_BEARER_TOKEN` on the SAPAdapter or injected
  connection context.

### Gap #3 — SAP status polling
- **erp_sync.py:** Added `SAP = "sap"` to `ERPType` enum. Added `_sync_sap_bill()` method that
  calls `GET {connection.base_url}/PurchaseInvoices({doc_entry})` with Bearer auth. Normalizes
  SAP document status to `PaymentStatus`. Added `SAP` case to `sync_bill_status()` dispatch.

### Gap #6 — Correlation ID middleware
- **main.py:** Added `CorrelationIdMiddleware` (Starlette `BaseHTTPMiddleware`) that:
  - Reads `X-Correlation-ID` request header, or generates `uuid4()` if absent.
  - Stores in `request.state.correlation_id`.
  - Echoes back as `X-Correlation-ID` response header.
- Registered before `RequestLoggingMiddleware` (outermost position).

### Gap #7 — Extraction correction rate metric
- **ops.py:** Added `GET /api/ops/extraction-quality` endpoint that queries `audit_events` for
  `event_type = 'correction_applied'` within a configurable time window and returns:
  `correction_count`, `correction_rate_pct`, `corrected_fields`, `window_hours`.

### Gap #8 — Teams card update after action
See Gap #1 above.

### Gap #10 — exception_code/severity first-class columns
- **database.py:** Added `_ensure_column()` calls in `initialize()` for:
  - `ap_items.exception_code TEXT`
  - `ap_items.exception_severity TEXT`
  Existing rows default to NULL; populated on next state write.
- **ap_store.py:** Added `exception_code` and `exception_severity` to `_AP_ITEM_ALLOWED_COLUMNS`
  whitelist. Updated `build_worklist_item()` to read from columns first, fall back to metadata.

### Gap #11 — Teams channel_threads table
- **database.py:** Added `channel_threads` table creation in `initialize()`.
  Schema: `(id, ap_item_id, channel, conversation_id, message_id, activity_id, service_url,
  state, last_action, updated_by, reason, created_at, updated_at)`.
- **teams_invoices.py:** `_upsert_teams_metadata()` now writes to `channel_threads` table (via
  `db.upsert_channel_thread()`) instead of AP item metadata JSON blob.

### Gap #12 — Runbooks
See `docs/RUNBOOKS.md`.

### Gap #13 — Resubmission flow
Verified: `POST /api/ap/items/{ap_item_id}/resubmit` exists in `clearledgr/api/ap_items.py`.
Creates new AP item with `supersedes_ap_item_id` linkage. Original item gets
`superseded_by_ap_item_id` pointer. Both source and new item get audit events
(`ap_item_resubmitted`, `ap_item_resubmission_created`). Idempotency: second call returns
`already_resubmitted` with existing child. Covered by `test_ap_items_merge_and_audit_guardrails.py`.

### Gap #14 — Gmail worklist auth
- **gmail_extension.py:** Added `user: TokenData = Depends(get_current_user)` parameter to
  `get_extension_worklist()`. Org resolution now enforces `user.organization_id` unless admin role.

### Gap #15 — Override context object
- **ap_states.py:** Added `OverrideContext` dataclass with fields: `override_type`, `justification`,
  `actor_id`, `policy_version` (default `"v1"`), `confidence_threshold_used` (optional float),
  `extra` (extensibility dict). Constants `OVERRIDE_TYPE_BUDGET`, `OVERRIDE_TYPE_CONFIDENCE`,
  `OVERRIDE_TYPE_PO_EXCEPTION`, `OVERRIDE_TYPE_MULTI`.
- **invoice_workflow.py:** Added `override_context: Optional[OverrideContext] = None` to
  `approve_invoice()`. When present, `override_context.to_dict()` is merged into the
  `confidence_override_used` audit event metadata — adding `policy_version`, `override_type`,
  and `confidence_threshold_used` to the audit record.
- **gmail_extension.py:** `approve_and_post` now constructs `OverrideContext(OVERRIDE_TYPE_MULTI)`
  when `request.override=True`. `budget_decision` constructs `OverrideContext(OVERRIDE_TYPE_BUDGET)`
  for the `approve_override` decision path.
- The `on_approval()` orchestrator method already passes `**kwargs` to `approve_invoice()`, so
  `override_context` flows through without changes to the orchestrator.

### Gap #17 — Gmail watch expiry health check
- **admin_console.py:** `_gmail_status_for_org()` now reads `watch_expiration` from
  `gmail_autopilot_state`. If expiry is within 24h or already past, adds
  `{"code": "renew_gmail_watch", "message": "Gmail watch expiring soon — renew to maintain push notifications"}`
  to `required_actions`.
