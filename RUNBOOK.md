# Clearledgr AP v1 Runbook

## 1. Start backend

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1
cp -n env.example .env
./scripts/dev-backend.sh start
```

Useful process commands:

```bash
./scripts/dev-backend.sh status
./scripts/dev-backend.sh restart
./scripts/dev-backend.sh stop
./scripts/dev-backend.sh logs
```

Required environment variables:
- `DATABASE_URL` (Postgres for production)
- `AP_TEMPORAL_ENABLED=true`
- `AP_TEMPORAL_REQUIRED=true`
- `TEMPORAL_ADDRESS`
- `TEMPORAL_AP_TASK_QUEUE`
- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_APPROVAL_CHANNEL`
- `TEAMS_WEBHOOK_URL`
- `TEAMS_SIGNING_SECRET`
- `TEAMS_BOT_APP_ID`
- `TEAMS_LEGACY_HMAC_ALLOWED=false`
- `AP_APPROVAL_SLA_MINUTES`
- `AP_WORKFLOW_STUCK_MINUTES`
- `AP_REQUIRE_ATTACHMENT`
- `AP_AMOUNT_ANOMALY_THRESHOLD`
- `AP_VENDOR_RULES_JSON` (optional JSON policy overrides)
- `ERP_MODE=mock` for local
- `CLEARLEDGR_DB_PATH` (local SQLite path)
- `CLEARLEDGR_DB_FALLBACK_SQLITE=true` (recommended for local when Postgres is not running)

## 2. Build and load Gmail extension

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm install
./build.sh dev
```

In `chrome://extensions`:
1. Enable Developer mode.
2. Remove old Clearledgr unpacked extension.
3. Load unpacked from:
   `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/build`

If you already had a prior Clearledgr unpacked extension loaded, remove it first to avoid stale content-script bundles.

## 3. One-time Gmail auth for autopilot

Autopilot requires one valid Gmail OAuth token per user.
The extension requests Gmail auth automatically when backend status is `auth_required`.
After auth is granted once, queue ingestion runs automatically without manual refresh or rescan actions.

Expected sidebar states:
- `Autopilot initializing.`
- `Autopilot scanning inbox.`
- `Autopilot active. Last scan HH:MM`

If backend is down, status shows backend unreachable.

## 4. End-to-end AP flow check

1. Send/open a real invoice email in Gmail.
2. Confirm AP item appears in sidebar queue.
3. Click `Request approval`.
4. Approve or reject in Slack or Teams interactive action.
5. On approval, confirm:
   - AP item reaches `closed`.
   - ERP reference is stored.
   - Gmail thread is labeled `Clearledgr/Posted`.
   - Thread note includes `Posted to ERP: <reference>`.
6. On reject, confirm:
   - AP item reaches `rejected`.
   - Gmail thread label `Clearledgr/Rejected`.
   - audit trail contains reject metadata and actor.

## 5. Browser agent flow check

1. Create an AP item (triage email).
2. Open sidebar and confirm `Agent actions` section appears.
3. Confirm queued read-only browser actions execute automatically.
4. Confirm sensitive actions appear as `awaiting approval`.
5. Click `Approve action` and confirm action transitions to queued then completed.
6. Confirm session state is visible via API:

```bash
curl "http://127.0.0.1:8000/api/agent/sessions/<SESSION_ID>"
```

## 6. Audit verification

Fetch AP item audit:

```bash
curl "http://127.0.0.1:8000/api/ap/items/<AP_ITEM_ID>/audit"
```

Expected event types include:
- `invoice_detected`
- `fields_extracted`
- `validation_passed` or `validation_failed`
- `approval_requested`
- `approved` or `rejected`
- `erp_post_attempted`
- `erp_post_succeeded` or `erp_post_failed`
- `thread_updated`
- `browser_command_enqueued`
- `browser_command_result`

## 7. Run tests

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1
pytest -q tests/test_ap_v1_flow.py tests/test_temporal_ap_workflow.py tests/test_teams_jwt_verification.py tests/test_browser_agent_layer.py
```

Core tests cover:
- state transition legality
- rejection and resubmission behavior
- Slack signature validation
- approval idempotency
- ERP reference persistence
- audit persistence and refs
- policy-driven validation to `needs_info`
- SLA escalation idempotency
- tenant operational health metrics endpoint

## 8. Operational health checks

Per-tenant health:

```bash
curl "http://127.0.0.1:8000/api/ops/tenant-health?organization_id=default"
```

All tenant health:

```bash
curl "http://127.0.0.1:8000/api/ops/tenant-health/all"
```
