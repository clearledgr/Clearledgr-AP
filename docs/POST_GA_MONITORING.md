# Post-GA Monitoring Plan

Clearledgr v1 — Live operations monitoring. Covers thresholds, cadence, ownership, and escalation paths.

---

## 1. Metrics and Thresholds

Four metrics are evaluated by `_evaluate_monitoring_thresholds()` in `clearledgr/api/ops.py`. All are configurable via environment variables.

| Metric | Env Var | Default | Alert Severity | Meaning |
|---|---|---|---|---|
| ERP post failure rate | `AP_ALERT_POST_FAILURE_RATE_PCT` | 20% | **critical** | `failed_posts / attempted_posts × 100` over the window |
| Exception / failed rate | `AP_ALERT_EXCEPTION_RATE_PCT` | 15% | warning | `items in exception or failed states / total active × 100` |
| Extraction correction rate | `AP_ALERT_CORRECTION_RATE_PCT` | 10% | warning | `operator corrections / total items × 100` |
| Duplicate post count | `AP_ALERT_DUPLICATE_POST_COUNT` | 1 | **critical** | Count of `duplicate_post_detected` / `idempotency_key_collision` events |

Thresholds are read at evaluation time — changing the env var takes effect without a restart.

---

## 2. Alert Channel

Alerts are pushed to Slack via:

```
POST /api/ops/monitoring-thresholds/check?push_slack=true&organization_id=<org>
```

Target channel: env var `AP_OPS_SLACK_CHANNEL` (default `#ap-ops-alerts`).

The endpoint is designed to be called from a cron job or the durable retry worker loop. It returns the full evaluation payload regardless of `push_slack` — Slack posting is additive.

**Manual check** (no Slack push):
```
GET /api/ops/monitoring-thresholds?organization_id=<org>&window_hours=24
```

---

## 3. Ops Endpoint Reference

| Endpoint | Use |
|---|---|
| `GET /api/ops/monitoring-thresholds` | Current metric values + threshold comparison |
| `POST /api/ops/monitoring-thresholds/check?push_slack=true` | Evaluate + push Slack alert if triggered |
| `GET /api/ops/extraction-quality` | Per-field confidence breakdown + correction rate |
| `GET /api/ops/retry-queue` | Dead-letter queue depth + oldest item age |
| `GET /api/ops/health` | Basic liveness |

All endpoints require a valid operator Bearer token or `X-API-Key` header.

---

## 4. Ownership Matrix

| Alert Type | Primary Owner | Escalation |
|---|---|---|
| `post_failure_rate` (critical) | Engineering | Engineering lead → disable `erp_posting` feature flag |
| `exception_rate` (warning) | AP Ops | Engineering if > 48h unresolved |
| `correction_rate` (warning) | Product / AI | Trigger extraction model review |
| `duplicate_post` (critical) | Engineering | Immediate — halt ERP posting, open incident |

---

## 5. Two-Week Post-Launch Cadence

### Daily (automated)
- Cron calls `POST /api/ops/monitoring-thresholds/check?push_slack=true` every hour.
- Any `critical` alert wakes on-call immediately.
- `warning` alerts accumulate in `#ap-ops-alerts` for daily review.

### Daily ops review (human)
Check the following each morning during Week 1–2:
- `GET /api/ops/retry-queue` — ensure queue depth is not growing
- `GET /api/ops/extraction-quality` — correction rate trend
- `GET /api/ops/monitoring-thresholds?window_hours=24` — overnight snapshot

### Weekly ops review (Fridays, Weeks 1–2)
Use this template:

```
AP Weekly Ops Review — <date>
──────────────────────────────
ERP post failure rate (7d):
Exception rate (7d):
Correction rate (7d):
Duplicate posts (7d):

Open alerts this week:
Action items:
Threshold adjustments needed:
```

Run with `window_hours=168` (7 days):
```
GET /api/ops/monitoring-thresholds?organization_id=<org>&window_hours=168
```

---

## 6. Escalation Paths

### Warning alerts (`exception_rate`, `correction_rate`)
1. AP Ops team reviews within 4 business hours.
2. If unresolved after 48h → escalate to Engineering.
3. Engineering root-causes and resolves within 5 business days.
4. Post-mortem added to `docs/archive/` if > 24h impact.

### Critical alerts (`post_failure_rate`, `duplicate_post`)
1. Engineering on-call paged immediately.
2. Within 15 minutes: assess whether to disable `erp_posting` via launch controls:
   - `POST /api/admin/controls/erp_posting` with `{"enabled": false}`
3. Root cause identified within 2 hours.
4. Fix deployed and posting re-enabled after 1h clean run.
5. Incident post-mortem required within 48h.

---

## 7. Rollback Triggers

Conditions that should trigger the rollback procedure in `RUNBOOKS.md`:

| Condition | Action |
|---|---|
| `post_failure_rate` > 20% for > 1h | Disable ERP posting; rollback if deploy-correlated |
| `duplicate_post_count` ≥ 1 in any window | Halt posting immediately |
| `exception_rate` > 40% | Disable auto-approve; route all items to manual review |
| Correction rate > 25% over 24h | Freeze extraction model; switch to manual field entry |

Rollback command reference: see `RUNBOOKS.md §4 — Emergency Rollback`.

---

## 8. Adjusting Thresholds

Set env vars in `render.yaml` (or your deployment environment) and redeploy:

```yaml
# render.yaml excerpt
envVars:
  - key: AP_ALERT_POST_FAILURE_RATE_PCT
    value: "15"          # tighten from default 20%
  - key: AP_ALERT_EXCEPTION_RATE_PCT
    value: "10"          # tighten from default 15%
  - key: AP_ALERT_CORRECTION_RATE_PCT
    value: "10"          # keep default
  - key: AP_ALERT_DUPLICATE_POST_COUNT
    value: "1"           # keep default — zero tolerance
  - key: AP_OPS_SLACK_CHANNEL
    value: "#ap-ops-alerts"
```

Thresholds are intentionally loose at GA. Tighten after 2 weeks of baseline data.
