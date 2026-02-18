# Clearledgr AP v1

Clearledgr AP v1 is an embedded Accounts Payable execution system inside Gmail. It detects invoice emails, extracts metadata, validates, routes approvals in Slack and Teams, posts approved items to ERP (NetSuite first, with mock mode for local dev), and writes an immutable audit trail. There is no standalone dashboard or navigation surface.

## Scope
- Gmail embedded UI for AP workflow
- Slack and Teams approvals with approve and reject actions
- ERP posting (NetSuite first) with reference capture
- Durable audit trail
- Browser-native agent action runtime (policy-gated, audited, human-approved for sensitive actions)

Out of scope for v1: reconciliation, reporting, dashboards, payment execution.

## Quick Start

### Backend
1. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
2. Configure environment
   ```bash
   cp env.example .env
   ```
3. Run API (persistent supervisor)
   ```bash
   ./scripts/dev-backend.sh start
   ```
4. Check status or logs
   ```bash
   ./scripts/dev-backend.sh status
   ./scripts/dev-backend.sh logs
   ```
5. Temporal runtime
   - Production AP runs require Temporal.
   - Set `TEMPORAL_ADDRESS`, keep `AP_TEMPORAL_ENABLED=true`, and run a worker.
   - For local fallback only, set `AP_TEMPORAL_ENABLED=false`.

### Gmail Extension
1. Build the InboxSDK bundle
   ```bash
   cd ui/gmail-extension
   npm install
   npm run build
   ```
2. Create a dev build
   ```bash
   ./build.sh dev
   ```
3. Load unpacked extension from `ui/gmail-extension/build` in `chrome://extensions`.

## Core Endpoints
- `POST /extension/triage` Create or update AP item from email content
- `POST /extension/submit-for-approval` Request Slack/Teams approval
- `GET /extension/pipeline` AP items for embedded queue
- `POST /api/slack/actions` Slack interactive callbacks (verified signatures)
- `POST /api/teams/actions` Teams interactive callbacks (verified signatures)
- `POST /api/audit/events` Append immutable audit event
- `POST /api/agent/sessions` Create browser-agent session for an AP item
- `POST /api/agent/sessions/{session_id}/commands` Enqueue typed browser actions
- `POST /api/agent/sessions/{session_id}/results` Persist command execution results
- `GET /api/agent/sessions/{session_id}` Fetch session status and pending approvals
- `GET /api/agent/policies/browser` Fetch browser action policy
- `PUT /api/agent/policies/browser` Update browser action policy
- `GET /api/ap/items/{ap_item_id}` Fetch AP item
- `GET /api/ap/items/{ap_item_id}/audit` Fetch AP item audit trail
- `GET /api/ap/items/by-thread/{thread_id}` Fetch AP items for a Gmail thread
- `GET /api/ap/items/by-thread/{thread_id}/audit` Audit events by Gmail thread
- `POST /api/ap/items/{ap_item_id}/retry-post` Retry ERP posting from `failed_post`
- `GET /api/ops/tenant-health` Per-tenant AP operational health metrics
- `GET /api/ops/tenant-health/all` Per-tenant health metrics for all organizations

## Environment Variables
- `SLACK_BOT_TOKEN` Slack bot token for approvals
- `SLACK_SIGNING_SECRET` Slack signing secret for interactive verification
- `SLACK_APPROVAL_CHANNEL` Default Slack channel for approvals
- `TEAMS_WEBHOOK_URL` Teams incoming webhook URL for approval requests
- `TEAMS_SIGNING_SECRET` Teams callback signing secret
- `TEAMS_ACTION_CALLBACK_URL` Teams callback endpoint URL
- `TEAMS_BOT_APP_ID` Teams Bot Framework app ID for JWT callback verification
- `TEAMS_LEGACY_HMAC_ALLOWED` Keep `false` in production
- `AP_APPROVAL_CHANNELS` Approval channels (`slack,teams`)
- `AP_APPROVAL_SURFACE` Approval surface policy (`slack`, `teams`, `gmail`, `hybrid`)
- `AP_APPROVAL_THRESHOLD_CHAT_ONLY` Hybrid routing threshold for chat-only approvals
- `AP_APPROVAL_THRESHOLD` Approval threshold amount
- `AP_APPROVAL_SLA_MINUTES` Approval SLA threshold before escalation
- `AP_WORKFLOW_STUCK_MINUTES` Workflow stuck threshold for ops health monitoring
- `AP_REQUIRE_ATTACHMENT` Require invoice attachment for validation
- `AP_VENDOR_RULES_JSON` JSON vendor policy overrides
- `AP_AMOUNT_ANOMALY_THRESHOLD` Ratio threshold for vendor amount anomalies
- `ERP_MODE` Set to `mock` for development, `live` for configured ERP
- `DATABASE_URL` Postgres DSN for production source of truth
- `CLEARLEDGR_DB_PATH` Local SQLite path fallback when `DATABASE_URL` is unset
- `AP_TEMPORAL_ENABLED` Enable Temporal orchestration path
- `AP_TEMPORAL_REQUIRED` Require Temporal when enabled
- `TEMPORAL_ADDRESS` Temporal endpoint for workflow orchestration
- `TEMPORAL_AP_TASK_QUEUE` AP workflow task queue
- `AP_AGENT_MODE` `mock` for local, `live` for foundation model calls
- `ANTHROPIC_API_KEY` Anthropic primary model key
- `OPENAI_API_KEY` OpenAI fallback model key
- `GMAIL_POLL_CONCURRENCY` Parallel inbox triage worker count
- `AP_BROWSER_AGENT_ENABLED` Enable browser-native action orchestration

## Product Rules
- Deterministic AP state machine
- Immutable audit log for every transition
- Approval decisions are captured in Slack/Teams before ERP posting
- Queue updates are backend-driven and autopilot-first (no manual rescan requirement)
- Browser actions are policy-gated and sensitive actions require explicit approval
- No dashboards or navigation UI in Gmail
