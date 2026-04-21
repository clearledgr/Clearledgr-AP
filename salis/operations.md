# Operations

Runbooks. How to deploy, how to roll back, how to investigate when a Box is stuck or an ERP post failed.

*Last verified: 2026-04-21 against commit `94c98eb`.*

---

## Deploy

We run on Railway. Three services + two plugins:

| Service | What | Scaling |
|---|---|---|
| `api` | gunicorn + uvicorn serving HTTP | 1–N |
| `worker` | Celery worker consuming Redis Streams events | 2–50 |
| `beat` | Celery beat firing periodic timers | Exactly 1 |

Plugins: Postgres (all state), Redis (event queue + rate limits + locks).

**Deploy mechanism:** push to `main`. Railway auto-deploys `api` on push. Worker + beat deploy via [`.github/workflows/railway-deploy-workers.yml`](../.github/workflows/railway-deploy-workers.yml) which triggers a new Railway service deploy.

**Checks before shipping:**

```bash
python -m pytest tests/ -q                    # 2349 passing
cd ui/gmail-extension && npm test             # 100 passing
cd ui/gmail-extension && npm run build        # dist/ produced
```

Anything red is a block. If the check is genuinely flaky (test_ordering, external service dependency), quarantine it in a follow-up PR — don't merge red.

**What to watch post-deploy:**

- `api` logs for 5xx spikes — Railway UI → api → Logs.
- `worker` logs for repeated `_classify_failure: TRANSIENT` on the same action — that's an external system going bad, not our bug.
- `beat` heartbeat (see below) — if it stops, the timers don't fire and Boxes stall.
- Redis memory on the plugin — event queue grows if workers fall behind.

---

## Rollback

**Fast path:** Railway UI → api (or worker) → Deployments → click "Redeploy" on the previous good build. Takes 30-60 seconds.

**Code-level rollback:** `git revert <bad-commit>` + push. The forward history stays clean; the revert commit is auditable.

**Migration rollback:** migrations are one-way. If a schema change is broken, the fix is a new migration that repairs it, not an attempt to reverse it. Our migration framework doesn't support down-migrations by design — rolling production schema is too scary with live customer data.

**When not to roll back:**

- A customer-visible bug in a feature that's behind a flag. Just flip the flag.
- A bug that only affects a specific tenant. Triage it per-tenant before reaching for the big lever.

---

## Stuck Box — investigation

A Box is "stuck" when it hasn't moved for longer than its state's threshold. Signals:

- Admin console shows it in `stuck` column.
- `MetricsStore.get_box_health` reports `{"stuck": [ap_id, ...]}`.
- A customer flags it.

**Recipe:**

```python
# Get the full Box contract
GET /api/ap_items/{id}/box

# You'll see:
# - state: e.g. "ready_to_post"
# - timeline: the audit_events rows
# - exceptions: box_exceptions rows (resolved + unresolved)
# - outcome: the terminal outcome if reached, else None
```

Map the `state` to what should come next:

| If state is... | The agent is waiting for... |
|---|---|
| `received` | The classification handler. Check llm_call_log for `classify_email` rows. |
| `validated` | The AP decision + approval routing. Check if Slack card got sent. |
| `needs_approval` | A Slack button click. Check Slack channel. Did we get the callback? |
| `needs_info` | A vendor reply. Check Gmail for the thread. `vendor_response_received` event hits when reply arrives. |
| `approved` | The `ready_to_post` transition. Automatic. If stuck here, coordination engine crashed mid-plan. |
| `ready_to_post` | The ERP post action. Check `llm_call_log` + `audit_events` for `post_bill` attempts. |
| `posted_to_erp` | Payment confirmation. Could take days, not a bug. |

**If the audit timeline ends with a `*_failed` event** — read the failure reason. Check whether the ERP / Gmail / Slack actually rejected our call or we hit a retryable error.

**If the timeline ends mid-plan with no failure** — the coordinator crashed mid-action. `task_runs` table has the pending task; `FinanceAgentRuntime.resume_pending_agent_tasks` runs on every process start and should re-pick it up.

**If no timeline progress at all** — the event never entered the queue, or the worker isn't consuming. Check:
- Redis event queue length (`redis-cli XLEN clearledgr:events`).
- Worker logs for "No workers available" or rebalancing events.

**Emergency lever:** force-advance a Box's state from psql. Rarely needed — the audit trail will miss the transition, so prefer to triage the real problem.

---

## Failed ERP post — investigation

Signals:

- `ap_item.state == 'failed_post'` or `box_exceptions` row with `exception_type='erp_post_failed'`.
- Customer reports "bill didn't appear in QBO".

**Recipe:**

```sql
-- Find the attempt
SELECT * FROM audit_events
WHERE box_id = '<ap_item_id>'
  AND event_type LIKE 'post_bill%'
ORDER BY ts DESC
LIMIT 5;

-- Read the failure payload
SELECT payload_json FROM audit_events
WHERE id = '<audit_id>';
```

The payload should have the ERP's error response verbatim (minus the raw body which we don't log, but the `reason` and `erp` fields are there).

**Common causes:**

- **`auth_failed`** → token expired and refresh failed. Customer needs to reconnect their ERP. Show the reconnect prompt in the admin console.
- **`sap_validation_failed`** → `company_code` missing from the `ERPConnection`. Check `integration_store` for the row; fix the config and retry.
- **`vendor_not_found`** → ERP has no vendor record matching our `vendor_name`. Either create the vendor in the ERP or mark the Box for manual handling.
- **`duplicate_invoice`** → ERP says we already posted this one. Check if `ap_items.erp_reference` is populated — if yes, we did post it and the state machine is wrong. If no, the vendor sent a duplicate and we need to reject the Box.
- **`schema_mismatch`** → our payload shape doesn't match what the ERP expects. This is a code bug; file it, don't hack around it.

**Retry:** for transient / auth failures, `POST /api/ap/items/{id}/retry-post` forces a re-attempt. Deliberately gated to admin role.

---

## Stuck vendor onboarding

Same shape as stuck AP Box, but the states are different. See `clearledgr/core/vendor_onboarding_states.py` for the state machine.

Most common cause today: KYC / open-banking provider stubs return instant success, but the actual "real" path isn't wired yet. So any session that would route to those providers is stuck in `kyc` or `bank_verify` waiting for a producer that doesn't exist.

Until we contract with real providers, this is manual intervention — admin clicks "mark KYC passed" and moves the session forward.

---

## Rotate secrets

**What needs rotating periodically:**

| Secret | Where it lives | How to rotate |
|---|---|---|
| `CLEARLEDGR_SECRET_KEY` | Railway env | Rotate then restart all services. Invalidates JWTs; every user re-logs in. |
| `TOKEN_ENCRYPTION_KEY` | Railway env | Tricky — it encrypts Gmail + ERP tokens at rest. Rotating requires re-encrypting every existing token with the new key. Don't do this without a migration script. |
| `ANTHROPIC_API_KEY` | Railway env | Rotate in Anthropic console, update Railway env, restart. |
| `SLACK_SIGNING_SECRET` | per-workspace in `slack_installs` | Rotate via Slack app config; update the row. |
| ERP OAuth client secrets | Railway env per ERP | Rotate with the ERP vendor first, update env, restart. Existing tokens in `integration_store` keep working until they expire. |
| Per-customer webhook secrets | `webhook_subscriptions.secret` | Customer-managed. They can DELETE + POST a new subscription to rotate. |

**If a secret is leaked:**

1. Rotate immediately.
2. Check `llm_call_log` + `audit_events` for anomalous activity in the window between leak and rotation.
3. If OAuth tokens might be compromised, revoke them in the third-party's admin (Google → OAuth access, Slack → app installs, ERP → token management).

---

## Monitoring + alerts

Today's story is honest: we have structured logs + Railway's built-in log viewer, plus `/api/ops/monitoring-health` endpoint that returns a set of check results. No alerting pipeline wired to paging — we ship visibility first, alerting later.

**The monitoring endpoint** ([`services/monitoring.py`](../clearledgr/services/monitoring.py)) checks:

- Redis reachable
- Postgres reachable
- Beat heartbeat age (staleness threshold configurable via `MONITOR_THRESHOLD_BEAT_STALE_HOURS`)
- Dead-letter queue size (configurable via `MONITOR_THRESHOLD_DEAD_LETTER_MAX`)
- Posting failure rate (configurable via `MONITOR_THRESHOLD_POSTING_FAILURE_RATE_PCT`)
- Auth failure count (configurable via `MONITOR_THRESHOLD_AUTH_FAILURE_MAX`)
- Stale poll cycles (configurable via `MONITOR_THRESHOLD_STALE_POLL_HOURS`)
- Overdue invoices max count (configurable via `MONITOR_THRESHOLD_OVERDUE_INVOICES_MAX`)

The `HealthPage` in the Gmail extension polls this endpoint and shows a traffic-light status. For production monitoring, the intent is to pipe this to a paging tool once we have customers.

---

## Database operations

**Connecting to prod Postgres:** `railway run --service api psql $DATABASE_URL` — Railway injects the connection string.

**Reading a Box end to end:**

```sql
-- State
SELECT id, state, vendor_name, amount, created_at, updated_at
FROM ap_items
WHERE id = '<id>';

-- Timeline
SELECT ts, event_type, prev_state, new_state, actor_id, payload_json
FROM audit_events
WHERE box_type = 'ap_item' AND box_id = '<id>'
ORDER BY ts;

-- Exceptions
SELECT * FROM box_exceptions WHERE box_type = 'ap_item' AND box_id = '<id>';

-- Outcome
SELECT * FROM box_outcomes WHERE box_type = 'ap_item' AND box_id = '<id>';
```

**Never write to these tables directly in prod.** Use the admin API routes. Direct SQL bypasses the audit funnel + the webhook emission + the state machine enforcement — all of which customers rely on.

**Migration runs** on every process start. Migration order is deterministic (integer versions). Failed migrations abort startup. `migrations.py` is the source of truth.

---

## Rate limits — when they fire

- **Extension API**: `rate_limit.py` sliding window, in-memory today (single-process limit).
- **ERP calls**: `erp_rate_limiter.py` per-tenant-per-ERP. Prevents one customer's large batch from blocking another.
- **LLM calls**: `llm_gateway.py` applies a budget per-org — you can see per-action cost in `llm_call_log`.

If rate limits fire in production: `429` responses, `RATE_LIMIT` failure classification, retry with delay. None of this pages anyone; if it's sustained, check logs.

---

## Incident framework

For anything customer-visible:

1. Acknowledge in Slack `#eng` within 5 minutes.
2. Triage: is it a single tenant or multi-tenant?
3. Stop the bleeding — feature-flag the broken path, or revert the offending commit.
4. Diagnose the root cause. Read logs + the audit trail for affected Boxes.
5. Ship the fix on a branch, test, merge, deploy.
6. Post-mortem — what made this undetectable, what test would have caught it.

We're small. Steps 1–5 may compress into 30 minutes. Step 6 matters anyway.

---

## Scripts worth knowing

- `scripts/start-api.sh` — local dev server with sensible defaults.
- `scripts/run-migrations.sh` — just runs migrations (useful if you added one and want to verify).
- `scripts/seed-test-org.sh` — if it exists at handover, it creates a dev tenant with a sample integration set for local testing. If not, write one.

`scripts/` is unreviewed ops territory. Read scripts before running in prod.

---

## The "is the product up?" checklist

```bash
# API responsive
curl https://<api-host>/health
# expect: {"status":"healthy", ...}

# Gmail push working
# - create a test invoice in a connected mailbox
# - watch gmail_webhooks logs

# Slack callbacks working  
# - click approve on a test invoice card
# - watch slack_invoices logs

# ERP connectivity
curl https://<api-host>/api/ops/erp-routing-strategy  # admin-auth required
# expect: 200 with per-org ERP config
```

If any of these fail, go back to "deploy rollback" and pick the last known good build.
