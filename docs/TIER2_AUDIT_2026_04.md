# Tier 2 Audit — Background Services That Could Silently Stop Working

Date: 2026-04-02
Auditor: Claude Opus 4.6
Scope: Agent retry jobs, background task loop, auto follow-up, notification retry queue, learning/calibration, state observers
Total issues: 22 (4 critical, 5 high, 10 medium, 3 low)

---

## E. Background Job & Task Infrastructure (10 issues)

### E1. [CRITICAL] Background loop can crash without restart
**File:** `clearledgr/services/agent_background.py:214-250`
**What happens:** The background loop (`_run_loop()`) is created once at startup as an asyncio task. If an unhandled exception occurs, the task dies. No monitoring, no heartbeat, no restart logic. The app continues running but all periodic tasks stop: overdue checks, approval reminders, anomaly detection, retry job drains.
**User impact:** Finance team stops receiving reminders. Overdue invoices not flagged. Stale sessions accumulate.
**Fix:** Add watchdog timer that checks if `_background_task` is still alive. Auto-restart on crash.

### E2. [CRITICAL] Retry jobs can hang forever (no timeout)
**File:** `clearledgr/services/agent_retry_jobs.py:70-71`
**What happens:** `workflow.resume_workflow(ap_item_id)` is awaited with no timeout. If it hangs, the job stays in "running" status with `locked_by` set forever. No stale lock detection exists. New workers skip locked jobs.
**User impact:** Specific AP items stuck in pending retry forever. No automatic recovery. Only manual DB intervention helps.
**Fix:** Add `asyncio.wait_for(workflow.resume_workflow(...), timeout=120)`. Add stale lock detection: release jobs locked > 15 minutes.

### E3. [CRITICAL] Auto follow-up drafts stored in-memory only
**File:** `clearledgr/services/auto_followup.py:152, 256`
**What happens:** Follow-up drafts stored in `self._drafts` dict (in-memory). App restart = all pending drafts lost. No database persistence. No indication to user that drafts disappeared.
**User impact:** Vendor follow-up emails that finance drafted are silently lost on any deploy or restart.
**Fix:** Persist drafts to a database table. Load on service initialization.

### E4. [HIGH] Task scheduler is orphaned (never runs)
**File:** `clearledgr/services/task_scheduler.py:100-153`
**What happens:** `run_overdue_check()` and `run_all_checks()` are defined but never called from the background loop or any scheduler. The functions say "should be called periodically" but nothing calls them.
**User impact:** Overdue task reminders, stale task detection, and approaching deadline alerts never fire.
**Fix:** Integrate into `agent_background.py` loop or remove orphaned code.

### E5. [MEDIUM] ERP reconciliation only runs at startup
**File:** `clearledgr/services/erp_follow_on_reconciliation.py:38-179`, `app_startup.py:64-74`
**What happens:** `run_erp_follow_on_reconciliation_check()` runs once during app startup. Split-brain mismatches between AP item state and ERP status accumulate after that.
**User impact:** AP items show as posted but ERP doesn't have them (or vice versa). Discrepancies grow over time.
**Fix:** Schedule to run hourly in the background loop.

### E6. [MEDIUM] Browser fallback sessions can hang for 4 hours
**File:** `clearledgr/services/erp_follow_on_session_reaper.py:46-52`
**What happens:** Default TTL is 4 hours. If a browser session is dispatched but the connection drops, it sits in "running" state until TTL expires. Reaper does run in background loop (good), but TTL is too long.
**User impact:** Invoice posting appears "in progress" for hours with no feedback.
**Fix:** Reduce default TTL to 30 minutes. Add progress heartbeat.

### E7. [MEDIUM] Sync database calls block the event loop
**File:** `clearledgr/services/agent_background.py:170-318`
**What happens:** `_collect_org_overdue_and_stale_tasks()` is synchronous but called from async context. Database I/O blocks the event loop. Timeouts may not apply properly.
**User impact:** Background tasks for other orgs are blocked while one org's slow DB query runs.
**Fix:** Wrap sync calls with `asyncio.get_event_loop().run_in_executor()`.

### E8. [MEDIUM] Email tasks database integrity not verified
**File:** `clearledgr/services/email_tasks.py:28-50, 430`
**What happens:** Database initialized at import time. If the file path is wrong or permissions are bad, first operation fails. No startup verification.
**User impact:** Email task tracking silently fails until first task operation.
**Fix:** Add connectivity check at startup.

### E9. [LOW] Org enumeration silently degrades to "default"
**File:** `clearledgr/services/agent_background.py:34-75`
**What happens:** If all DB queries for org IDs fail, falls back to `["default"]`. Multi-org customers silently lose background processing for non-default orgs.
**User impact:** Secondary organizations miss all background tasks. Warnings are logged but not alerted.
**Fix:** Alert when fallback to default occurs.

### E10. [MEDIUM] No database connection health check
**File:** `clearledgr/core/database.py:1180-1184`
**What happens:** DB connection is lazy singleton. If database becomes unavailable after startup (network outage, crash), all services fail. No reconnection or failover logic.
**User impact:** All background services fail silently after DB outage. Manual app restart required.
**Fix:** Add periodic connection health check. Implement reconnection logic.

---

## F. Notification Retry Queue & Learning Services (12 issues)

### F1. [CRITICAL] Notification retry queue is never drained
**File:** `clearledgr/services/slack_notifications.py:231`
**What happens:** `process_retry_queue()` exists but is NEVER called in production. No scheduled task, no cron job, no background processor calls it. The `pending_notifications` table grows unbounded. Failed Slack/Teams notifications sit in the queue forever.
**User impact:** Approval notifications that failed to send are never retried. Invoices stuck waiting for approvals that were never delivered.
**Fix:** Add `process_retry_queue()` call to the background loop in `agent_background.py`.

### F2. [HIGH] Learning calibration auto-applies threshold changes without rollback
**File:** `clearledgr/services/learning_calibration.py:255-298`
**What happens:** Calibration service automatically adjusts `auto_approve_confidence_threshold`. If disagreement_rate >= 0.3, tightens threshold. If stable, relaxes toward 0.95. No rollback mechanism. No circuit breaker. Exceptions swallowed at line 293.
**User impact:** Auto-approval confidence drifts silently. Could approve risky invoices or block legitimate ones. No audit trail of why thresholds changed.
**Fix:** Add threshold change audit trail. Add bounds checking. Add circuit breaker if signal is noisy.

### F3. [HIGH] Correction learning vendor preferences not persisted
**File:** `clearledgr/services/correction_learning.py:113`
**What happens:** Vendor preferences stored in-memory only (`self._vendor_preferences`). Never written to DB. Lost on process restart. Multi-process deployments don't share preferences.
**User impact:** GL code suggestions reset to defaults after restart. Finance team has to re-teach the system.
**Fix:** Persist vendor preferences to database alongside learned rules.

### F4. [HIGH] Correction learning DB write failures swallowed
**File:** `clearledgr/services/correction_learning.py:329-330, 357-358, 563`
**What happens:** `_persist_correction()`, `_persist_rule()`, and `_persist_normalized_correction_event()` all catch exceptions and only log. No indication to the caller that the learning was not saved.
**User impact:** Finance team corrects an extraction, system acknowledges it, but the correction is silently lost. Same mistake will recur.
**Fix:** Return success/failure from persist methods. Alert on repeated failures.

### F5. [HIGH] Corrections loaded only at init, never refreshed
**File:** `clearledgr/services/correction_learning.py:111`
**What happens:** Corrections loaded at `__init__` into memory. Never refreshed during service lifetime. One process won't see corrections from another process.
**User impact:** In multi-process deployment, corrections are fragmented across workers.
**Fix:** Refresh from DB periodically (every 5 minutes) or on every `suggest()` call.

### F6. [MEDIUM] Compounding learning pattern confidence can drift unbounded
**File:** `clearledgr/services/compounding_learning.py:607-609`
**What happens:** `new_confidence = 0.5 + (pattern.success_rate * 0.45)`. If success_rate exceeds 1.0 (possible with counting bugs), confidence exceeds 1.0. No bounds check.
**User impact:** Patterns with >100% confidence used for suggestions. Could override human corrections.
**Fix:** Clamp confidence to [0.0, 1.0].

### F7. [MEDIUM] Compounding learning DB write failures not handled
**File:** `clearledgr/services/compounding_learning.py:499-500`
**What happens:** `conn.execute(...)` in pattern reinforcement has no exception handling. DB write failure crashes the reinforcement without rollback.
**User impact:** Cache says pattern was reinforced, DB says it wasn't. Inconsistency.
**Fix:** Wrap DB writes in try/except. Invalidate cache on failure.

### F8. [MEDIUM] Pattern cache loaded once, never refreshed
**File:** `clearledgr/services/compounding_learning.py:98-100, 146-167`
**What happens:** Patterns loaded at `__init__`, filtered to confidence > 0.3. Never refreshed. Multi-process deployments have independent caches.
**User impact:** Each worker learns independently. No shared intelligence.
**Fix:** Same as F5 — periodic DB refresh.

### F9. [MEDIUM] State observer failures swallowed silently
**File:** `clearledgr/services/state_observers.py:70-81`
**What happens:** Observer dispatch catches all exceptions and only logs. If NotificationObserver fails to enqueue, VendorFeedbackObserver fails to update profile, or GmailLabelObserver fails to sync labels — all swallowed.
**User impact:** Notifications not sent, vendor profiles not updated, Gmail labels not synced — all silently.
**Fix:** Log at ERROR level. Add metric counter for observer failures. Alert on repeated failures.

### F10. [MEDIUM] Task notification import failures silently disable features
**File:** `clearledgr/services/task_notifications.py:14-26`
**What happens:** If Slack or Teams SDK import fails, `SLACK_AVAILABLE` / `TEAMS_AVAILABLE` is set to False. No logging. Feature silently disabled.
**User impact:** Task notifications appear to work (function is callable) but silently do nothing.
**Fix:** Log WARNING at import time if SDK unavailable.

### F11. [LOW] Task notifications use `print()` instead of logger
**File:** `clearledgr/services/task_notifications.py:74, 89`
**What happens:** Uses `print()` for output instead of Python `logging`. Messages don't appear in log files.
**User impact:** Debugging task notification failures impossible from logs.
**Fix:** Replace `print()` with `logger.info()` / `logger.warning()`.

### F12. [LOW] Task notifications create new event loop per call
**File:** `clearledgr/services/task_notifications.py:65-70, 81-85`
**What happens:** Creates a new asyncio event loop for each notification send. Potential resource leak.
**User impact:** Memory leak over time if notifications are sent frequently.
**Fix:** Use `asyncio.run()` or the existing event loop.

---

## Priority Matrix

### Fix immediately (blocks pilot)
- E1 — Background loop crash recovery
- E2 — Retry job timeout and stale lock detection
- E3 — Persist auto follow-up drafts
- F1 — Drain notification retry queue in background loop

### Fix before enterprise (blocks scaling)
- E4 — Wire task scheduler into background loop
- F2 — Calibration rollback and audit trail
- F3, F4, F5 — Correction learning persistence and refresh
- F9 — State observer failure alerting

### Fix when time permits (polish)
- E5, E6, E7, E8 — Background service hardening
- E9, E10 — Resilience improvements
- F6, F7, F8 — Learning service edge cases
- F10, F11, F12 — Task notification cleanup
