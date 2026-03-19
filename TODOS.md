# Clearledgr — Deferred Work

## P1 — Security & Reliability

### ~~Auth refresh circuit breaker (workspace shell)~~ ✓
- **Status:** Done (2026-03-13) — circuit breaker flags in `refreshAdminSession()`, 401 interceptor in `api()`

### ~~Claude API retry with backoff (agent runtime)~~ ✓
- **Status:** Done (2026-03-13) — exponential backoff for 429/500/502/503 + network errors, 3 retries

### ~~DB checkpoint error handling (agent runtime)~~ ✓
- **Status:** Done (2026-03-13) — try/except around both pre-exec and post-exec `update_task_run_step()` calls

### ~~Stable dev secret~~ ✓
- **Status:** Done (2026-03-13) — `require_secret()` uses `sha256(hostname:name)` instead of random

## P2 — Feature Completion

### ~~Wire NotificationObserver into workflow~~ ✓
- **Status:** Done (2026-03-13) — registered in `InvoiceWorkflowService.__init__`; DB table, `enqueue_notification()`, and retry queue already existed

## P1 — Design & UX

### Workspace shell visual redesign
- **What:** Full visual redesign of the workspace shell to match Fyxer/Mixmax quality bar — warm palette, generous whitespace, card-based layout, setup wizard with progress bar, dense data tables with avatars and status pills, professional typography
- **Why:** Current UI is functional MVP-level; not production-quality for customer-facing use
- **Effort:** L
- **Depends on:** Preact component architecture (DONE), design tokens (DONE)
- **References:** app.fyxer.com (warm minimal), app.mixmax.com (dense productivity)

### Gmail extension sidebar visual polish
- **What:** Apply same visual redesign to extension sidebar — match the workspace shell quality bar
- **Effort:** M
- **Depends on:** Workspace shell redesign (establish patterns first)

## P2 — Operational

### Operational health dashboard
- **What:** `/ops/health` page in the workspace shell — AP pipeline latency p50/p95, agent task success rate, Claude API error rate, notification delivery rate
- **Why:** No real-time visibility into system health; operators rely on logs to detect degradation
- **Effort:** M
- **Depends on:** Metrics already collected via `_ap_ops_metrics` table; needs frontend rendering

### Pre-commit hook for secret prevention
- **What:** Git pre-commit hook that blocks commits containing patterns matching API keys, tokens, or credentials
- **Why:** `.env` and token files are gitignored but accidental inline secrets in source code have no guardrail
- **Effort:** S
- **Depends on:** Nothing — standalone hook script

## P3 — Operational

### Staging E2E drill automation
- **What:** Automated end-to-end test: Gmail webhook → parse → route → approve → ERP post → verify
- **Why:** Manual staging drills don't catch integration regressions; needed before production launch
- **Effort:** L
- **Depends on:** Real ERP sandbox credentials, staging environment provisioned
