# Clearledgr Operator Runbooks

Operational procedures for common failure scenarios and maintenance tasks.

---

## Table of Contents

1. [ERP Auth Expiry](#1-erp-auth-expiry)
2. [Slack Callback Failures](#2-slack-callback-failures)
3. [Teams Callback Failures](#3-teams-callback-failures)
4. [ERP Posting Failure Recovery](#4-erp-posting-failure-recovery)
5. [Gmail Watch Renewal](#5-gmail-watch-renewal)
6. [Per-Tenant Rollback Procedures](#6-per-tenant-rollback-procedures)
7. [AP Item Resubmission](#7-ap-item-resubmission)
8. [Database Maintenance](#8-database-maintenance)

---

## 1. ERP Auth Expiry

### QuickBooks Online (QBO)

**Symptoms:**
- `erp_post_failed` audit events with `error_code: qbo_auth_expired` or `401` HTTP status
- Worklist items stuck in `failed_post` state
- Logs: `QuickBooksAPIError: invalid_grant`

**Steps:**
1. Log in to Admin Console → Integrations → QuickBooks → "Reconnect"
2. Complete the OAuth2 flow — a new access + refresh token pair will be stored
3. Trigger retry for stuck items: `POST /api/ap/items/{id}/retry-post` for each `failed_post` item, or bulk via the worklist "Retry Failed" action
4. Confirm `erp_post_success` audit events appear
5. Check `GET /api/ops/tenant-health?organization_id=<org>` — `erp_health` should be `ok`

**Prevention:**
- Set `QB_TOKEN_REFRESH_BUFFER_HOURS=48` env var (default) so tokens are refreshed 48 h before expiry
- Monitor `GET /api/admin/health` for `required_actions` containing `reconnect_erp`

### Xero

**Symptoms:** Same pattern — `401` from Xero, `xero_auth_expired` error code

**Steps:** Same as QBO — Admin Console → Integrations → Xero → "Reconnect"

### NetSuite

**Symptoms:** `netsuite_auth_failed` — credentials rejected or IP allowlist blocking

**Steps:**
1. Rotate TBA credentials in NetSuite: Setup → Company → OAuth → Token-Based Authentication
2. Update encrypted credentials via `PUT /api/erp/netsuite/credentials`
3. Confirm connectivity: `GET /api/ops/erp-routing-strategy?organization_id=<org>`

### SAP

**Symptoms:** `sap_bearer_expired` — OData calls return 401

**Steps:**
1. Obtain a new Bearer token from SAP service layer authentication endpoint
2. Update the token via environment variable `SAP_BEARER_TOKEN` or credential store
3. Restart the service (or hot-reload credentials if supported)
4. Verify: `GET /api/erp/sap/gl-accounts` returns account list

---

## 2. Slack Callback Failures

### Symptom: Slack interaction webhook returns 4xx/5xx

**Possible causes and remedies:**

| Code | Cause | Remedy |
|------|-------|--------|
| 401 | HMAC signature mismatch | Verify `SLACK_SIGNING_SECRET` matches the value in Slack App settings |
| 400 `stale_action` | Action arrived > 5 min after being sent | Expected for very delayed interactions — no action needed; Slack message should be updated |
| 400 `invalid_payload` | Malformed Slack payload | Check Slack App configuration → Request URL matches `/slack/invoices/interactive` |
| 404 `email_not_found` | AP item deleted or wrong org | Verify the AP item still exists; check `organization_id` in the callback |
| 400 `duplicate_callback` | Same action replayed | Idempotency guard fired — safe to ignore; audit event `channel_action_duplicate` was written |

**Checking Slack delivery logs:**
- Slack API dashboard → Your App → Event Subscriptions → Delivery Status
- Internal: query `audit_events` where `event_type IN ('channel_callback_unauthorized', 'channel_action_invalid', 'channel_action_blocked')`

**HMAC signing secret mismatch recovery:**
1. Go to api.slack.com → Your App → Basic Information → Signing Secret → Regenerate
2. Update `SLACK_SIGNING_SECRET` env var and redeploy
3. All future Slack interactions will use the new secret

---

## 3. Teams Callback Failures

### Symptom: Teams approval card does not update after action

**Possible causes:**
- `TEAMS_APP_ID` or `TEAMS_APP_PASSWORD` not set → Bot Framework token cannot be acquired
- `serviceUrl` not captured in callback payload → card update skipped
- Bot Framework token cached token expired (cache TTL = 50 min, token TTL = 60 min)

**Remediation steps:**
1. Verify env vars: `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `TEAMS_TENANT_ID` (or `common` for multi-tenant)
2. Check logs for `Non-fatal: Teams card update failed:` — the underlying action still processed
3. If token acquisition fails, confirm the App Registration in Azure AD has `Application.ReadWrite.All` or `GroupMember.Read.All` and client secret is not expired

### Symptom: 401 `replay_attack` from Teams interactive endpoint

**Cause:** Teams JWT token `iat` claim is > 5 min old (replay protection gate, Gap #1 fix)

**Remedy:** This is expected for delayed deliveries. If Teams is consistently late (> 5 min to deliver), investigate Teams service health or adjust `_MAX_TOKEN_AGE_SECONDS` in `clearledgr/core/teams_verify.py` — do **not** disable the check.

---

## 4. ERP Posting Failure Recovery

### Items stuck in `failed_post`

**Check:**
```
GET /api/ops/tenant-health?organization_id=<org>
```
Look at `failed_post_count` in the response.

**Identify root cause:**
```sql
SELECT ap_item_id, actor_id, reason, metadata, ts
FROM audit_events
WHERE event_type = 'erp_post_failed'
AND organization_id = '<org>'
ORDER BY ts DESC
LIMIT 50;
```

**Single-item retry:**
```
POST /api/ap/items/{ap_item_id}/retry-post
```
Requires `admin` or `owner` role. The state machine transitions `failed_post → ready_to_post` before re-attempting.

**Bulk retry (all failed items for an org):**
- Use the Admin Console Worklist → filter by state `failed_post` → "Retry All"
- Or call the endpoint in a loop over items from `GET /api/ap/items?state=failed_post&organization_id=<org>`

**If ERP is unavailable:**
- Set `AP_ERP_POSTING_ENABLED=false` env var to pause automatic posting
- Items stay in `ready_to_post`; re-enable when ERP recovers
- Alternatively use per-tenant rollback controls: `PUT /api/admin/rollback-controls`

---

## 5. Gmail Watch Renewal

### Background
Gmail push notifications use a watch that must be renewed every 7 days (Google's maximum). The `GET /api/admin/health` endpoint surfaces `renew_gmail_watch` in `required_actions` when the watch is expiring within 24 hours.

### Renewing the watch

**Via API:**
```
POST /api/gmail/watch/renew
Authorization: Bearer <admin-token>
Content-Type: application/json

{"organization_id": "<org>"}
```

**Manual verification (if auto-renew fails):**
1. Call `GET /api/admin/health` — check `integrations.gmail.watch_status` and `watch_expiration`
2. If `watch_status` is not `active`, trigger re-auth: Admin Console → Integrations → Gmail → "Reconnect"
3. After reconnect, `POST /api/gmail/watch/register` to re-register the push subscription

**Prevention:**
- Set `GMAIL_WATCH_RENEW_BUFFER_HOURS=24` (default) so the background task renews before expiry
- Monitor the `renew_gmail_watch` required_action in `GET /api/admin/health` via your alerting system

---

## 6. Per-Tenant Rollback Procedures

### Disabling AP processing for a tenant

Used when a bug is discovered or a tenant needs to be paused.

```
PUT /api/admin/rollback-controls
Authorization: Bearer <admin-token>

{
  "organization_id": "<org>",
  "updated_by": "ops@clearledgr.com",
  "controls": {
    "ap_processing_enabled": false,
    "slack_actions_enabled": false,
    "teams_actions_enabled": false,
    "erp_posting_enabled": false
  }
}
```

**Verify the block is active:**
```
GET /api/admin/rollback-controls?organization_id=<org>
```

**Effect:** All channel action endpoints return `{"status": "blocked"}` with `reason` set to the control name. No state transitions occur while blocked.

**Re-enabling:**
Same `PUT` with all controls set to `true`.

### Per-channel blocking only (e.g. Teams has a bad deployment)

```json
{
  "controls": {
    "teams_actions_enabled": false
  }
}
```
Slack and Gmail extension continue to function; Teams interactions return `blocked`.

---

## 7. AP Item Resubmission

### When to use
An invoice was rejected (state = `rejected`) and the underlying issue has been corrected (e.g., vendor sent a corrected invoice, budget code changed).

### Steps

1. Confirm the item is in `rejected` state:
   ```
   GET /api/ap/items/{ap_item_id}
   ```
   Check `state == "rejected"` in the response.

2. Submit resubmission request:
   ```
   POST /api/ap/items/{ap_item_id}/resubmit
   Authorization: Bearer <token>

   {
     "actor_id": "finance@acme.com",
     "reason": "corrected_invoice_received",
     "initial_state": "validated",
     "vendor_name": "Acme Vendor Inc",
     "amount": 1500.00,
     "copy_sources": true
   }
   ```
   Pass `initial_state: "validated"` if the invoice data is already confirmed; `"received"` if it should go through extraction again.

3. The response includes `new_ap_item_id` — share this with the approver.

4. Audit events `ap_item_resubmitted` and `ap_item_resubmission_created` are written automatically.

5. The original rejected item gets `superseded_by_ap_item_id` set; it will no longer appear in the active worklist.

**Idempotency:** If the same item is resubmitted twice, the second call returns `{"status": "already_resubmitted"}` with the existing child item — no duplicate is created.

---

## 8. Database Maintenance

### Checking DB health (SQLite — dev/staging)

```bash
sqlite3 state.sqlite3 "PRAGMA integrity_check;"
sqlite3 state.sqlite3 "SELECT COUNT(*) FROM ap_items WHERE state = 'failed_post';"
sqlite3 state.sqlite3 "SELECT COUNT(*) FROM audit_events WHERE ts > datetime('now', '-24 hours');"
```

### Audit event retention

Audit events are append-only and protected by DB triggers — they cannot be updated or deleted by application code. Retention policy should be set at the infrastructure layer (pg_partman for Postgres, or periodic archival for SQLite).

### Schema migrations

New columns are added via `_ensure_column()` in `clearledgr/core/database.py` — these are backward-compatible and run automatically on startup. No manual migration steps are required for columns added in v1.

### Backup procedure (SQLite)

```bash
# Atomic online backup (safe while server is running)
sqlite3 state.sqlite3 ".backup backup_$(date +%Y%m%d_%H%M%S).sqlite3"
```

For Postgres (production): use `pg_dump` with `--no-owner --no-acl` and store in encrypted object storage.

---

_Last updated: 2026-02-25_
_Owner: Engineering / AP Platform team_
