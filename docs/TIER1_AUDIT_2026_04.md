# Tier 1 Audit — User-Facing Silent Failures

Date: 2026-04-02
Auditor: Claude Opus 4.6
Scope: Slack/Teams approval delivery, ERP posting & retry, invoice extraction pipeline, subscription enforcement
Total issues: 43 (12 critical, 13 high, 14 medium)

---

## A. Slack/Teams Approval Delivery (11 issues)

### A1. [CRITICAL] Slack response_url failure swallowed
**File:** `clearledgr/api/slack_invoices.py:131-148`
**What happens:** User clicks Approve in Slack. Approval is recorded in DB. Background task posts result to Slack via response_url. If the POST fails AND the retry enqueue also fails, the error is swallowed with only a log line.
**User impact:** User sees stale action buttons in Slack. May click Approve again, creating duplicate processing.
**Fix:** Make response_url POST blocking (not background), or add dead letter alerting.

### A2. [CRITICAL] Teams card update failure swallowed
**File:** `clearledgr/api/teams_invoices.py:496-527`
**What happens:** After approval in Teams, the system tries to update the original card. If the update fails and retry enqueue also fails, error is swallowed.
**User impact:** Teams card still shows action buttons. User has no confirmation their action worked.
**Fix:** Same as A1 — make card update blocking or add alerting.

### A3. [CRITICAL] No Slack API response validation
**File:** `clearledgr/services/slack_api.py:118-162`
**What happens:** Slack returns `{"ok": true}` but without `ts` field (malformed response). `SlackMessage.ts` becomes empty string. Later attempts to update this message using empty `ts` fail silently.
**User impact:** Approval card is sent but can never be updated with the decision result.
**Fix:** Validate `ts` field is non-empty before returning SlackMessage. Raise if missing.

### A4. [CRITICAL] No block validation before Slack send
**File:** `clearledgr/services/approval_card_builder.py:270-599`
**What happens:** Block Kit structures are built without validation. If invoice data is malformed (e.g., gmail_id is None), action_ids contain None, and Slack silently rejects the blocks.
**User impact:** Approval message is never sent. No error returned to caller. Invoice waits for approval that never arrives.
**Fix:** Validate all action_ids are non-empty strings before returning blocks.

### A5. [CRITICAL] Notification enqueue can fail silently
**File:** `clearledgr/services/slack_notifications.py:154-176`
**What happens:** Slack send fails, then DB enqueue for retry also fails. Function returns False. Caller may not check the return value.
**User impact:** Approval notification is permanently lost. Invoice waits for approval that was never sent.
**Fix:** Raise an exception when both send and enqueue fail, so callers are forced to handle it.

### A6. [CRITICAL] AP state commits before Slack response
**File:** `clearledgr/api/slack_invoices.py:484-521`
**What happens:** Approval action updates AP state synchronously, then posts Slack response as a background task. If background task fails, AP state is updated but user sees "Processing..." forever.
**User impact:** User doesn't know if their action succeeded. May click again.
**Fix:** Return the Slack response inline instead of as background task. Accept slightly higher latency for reliability.

### A7. [CRITICAL] Slack rate limit exhaustion loses approvals
**File:** `clearledgr/services/slack_api.py:87-110`
**What happens:** Slack rate limits exhausted after max retries. SlackAPIError raised. Caller catches as generic Exception, logs warning, tries webhook fallback. If fallback also fails, returns False.
**User impact:** Approval notification never sent, never enqueued for retry.
**Fix:** Always enqueue for retry when send fails, regardless of error type.

### A8. [HIGH] Teams deadletter uses wrong org_id
**File:** `clearledgr/api/teams_invoices.py:496-527`
**What happens:** Teams card update retry is enqueued with `organization_id="system"` instead of the actual org.
**User impact:** Retry job runs in wrong org context. May fail or update wrong data.
**Fix:** Pass actual `organization_id` from the invoice context.

### A9. [HIGH] Teams webhook POST failures not retried
**File:** `clearledgr/services/teams_api.py:51-71`
**What happens:** Teams webhook POST fails (timeout, 500). Returns error dict. Caller doesn't check the dict status.
**User impact:** Approval notification lost for Teams users.
**Fix:** Add retry logic matching the Slack pattern.

### A10. [HIGH] No atomicity between approval and notification
**File:** `clearledgr/api/slack_invoices.py:131-148, 484-521`
**What happens:** Approval is Phase 1 (sync), notification is Phase 2 (async background). If Phase 2 fails, approval succeeded but user doesn't know.
**User impact:** Inconsistent UX — action succeeded but no feedback.
**Fix:** Either make both phases atomic (single response) or add reliable notification delivery guarantee.

### A11. [HIGH] Dead letter notifications have no alerting
**File:** `clearledgr/core/stores/ap_store.py:585-620`
**What happens:** Notification fails 5 retries over ~3 hours. Moves to dead_letter status. No alert sent.
**User impact:** Approval notification permanently lost. Only discoverable by querying dead_letter status manually.
**Fix:** Send Slack/email alert to ops when a notification enters dead_letter.

---

## B. ERP Posting & Retry (9 issues)

### B1. [HIGH] Token expired (401) not refreshed in posting path
**File:** `clearledgr/integrations/erp_router.py:340-343, 437-439, 558-559`
**What happens:** ERP returns 401 (token expired). Code logs "Token expired, needs refresh" and returns error. Does NOT call the existing refresh functions. Invoice transitions to failed_post.
**User impact:** Invoice stuck in failed_post. Retry loop hits same 401 forever. Requires manual credential refresh.
**Fix:** Call refresh_quickbooks_token/refresh_xero_token on 401, retry once.

### B2. [HIGH] Realm_id / tenant_id mismatch gives generic error
**File:** `clearledgr/integrations/erp_router.py:300-301, 403-404`
**What happens:** ERP company was reconfigured (new realm_id). Old realm_id in DB passes null check. API call fails with generic 404. Classified as recoverable, retried infinitely.
**User impact:** Invoice stuck in retry loop with no clear error message. Operator doesn't know credentials need updating.
**Fix:** Map ERP 404 responses to "erp_realm_id_invalid" error code. Classify as non-recoverable.

### B3. [CRITICAL] Both API and browser fallback fail — invoice lost
**File:** `clearledgr/services/invoice_posting.py:463-475`, `clearledgr/services/erp_api_first.py:1352-1380`
**What happens:** API posting fails. Browser fallback queued. Browser also fails. AP item in failed_post. Retry job re-attempts API (same error). After max retries, dead letter queue. No alert.
**User impact:** Invoice is permanently lost in dead letter queue. No escalation to operator.
**Fix:** Alert ops when posting enters dead letter. Add "exhausted_all_channels" error state.

### B4. [HIGH] Idempotency lost on retry — duplicate ERP posts
**File:** `clearledgr/services/invoice_posting.py:333-340`, `clearledgr/services/erp_api_first.py:1166-1170`
**What happens:** First post attempt times out but ERP actually processed it (slow response). Retry generates new idempotency key. Second post succeeds. Two bills in ERP.
**User impact:** Duplicate bill posted to ERP. Financial impact.
**Fix:** Always reuse the original idempotency key on retry. Check ERP for existing bill before posting.

### B5. [MEDIUM] posted_to_erp state set optimistically
**File:** `clearledgr/services/erp_api_first.py:553-564`
**What happens:** Browser fallback reconciliation sets state to posted_to_erp immediately on webhook callback, before confirming the macro actually completed.
**User impact:** UI shows "posted" but ERP may not have the invoice yet.
**Fix:** Only set posted_to_erp after verifying the ERP reference exists.

### B6. [HIGH] Dead letter queue has no alerting
**File:** `clearledgr/services/agent_retry_jobs.py:101-112`
**What happens:** Failed posting job hits max retries (3). Moves to dead letter status. No alert sent.
**User impact:** Invoice permanently stuck. Only discoverable by manual DB query.
**Fix:** Send Slack/email alert when posting job enters dead letter.

### B7. [MEDIUM] Retry job can get stuck in "claimed" state forever
**File:** `clearledgr/services/agent_retry_jobs.py:49-82`
**What happens:** Worker claims job, starts resume_workflow(), which hangs or crashes. Job stays in "claimed" state forever. Next drain tick skips it.
**User impact:** Invoice stuck in retry with no automatic recovery.
**Fix:** Add claimed_at timestamp. Force-release jobs claimed > 15 minutes without completion.

### B8. [HIGH] ready_to_post infinite loop after max retries
**File:** `clearledgr/core/ap_states.py:51-53`
**What happens:** Invoice cycles between ready_to_post and failed_post. After 3 retries, dead letter. But invoice is still in failed_post state in AP items table — no transition to "stuck" or "escalation_required".
**User impact:** Invoice disappears from active retry queue but still shows as failed_post. Operator can see it but has no clear next action.
**Fix:** After max retries, transition to a "posting_exhausted" exception state visible in the pipeline.

### B9. [MEDIUM] Browser fallback completion race condition
**File:** `clearledgr/services/erp_api_first.py:517-535`
**What happens:** Two webhooks arrive for the same session (retry/resubmission). First webhook hasn't written audit event yet when second arrives. Both process the completion.
**User impact:** Conflicting state updates. AP item may end up in inconsistent state.
**Fix:** Use SELECT FOR UPDATE or advisory lock on ap_item_id during completion.

---

## C. Invoice Extraction Pipeline (13 issues)

### C1. [CRITICAL] NOISE classification drops invoice forever
**File:** `clearledgr/services/gmail_triage_service.py:48-53`
**What happens:** Classifier labels email as "NOISE". Email is silently skipped with no audit trail and no recovery mechanism.
**User impact:** If classifier has a false negative, a real invoice is permanently lost.
**Fix:** Log all NOISE classifications to an audit table. Allow operator to review and reclassify.

### C2. [CRITICAL] DB save_invoice_status failure not caught
**File:** `clearledgr/services/invoice_workflow.py:377-389`
**What happens:** `save_invoice_status()` fails (DB down, disk full). Exception propagates. Invoice state not persisted. Downstream code may continue with stale state.
**User impact:** Invoice partially processed. May be reprocessed on next autopilot tick, creating duplicates.
**Fix:** Wrap in try/except. If save fails, abort processing and log error with full context.

### C3. [HIGH] OCR unavailable means scanned invoices lost
**File:** `clearledgr/services/email_parser.py:1626-1628`
**What happens:** pytesseract not installed. Scanned PDFs (image-only) return None text. Claude gets empty input. Extraction fails silently.
**User impact:** Scanned invoices are effectively invisible to the system.
**Fix:** Log WARNING at startup if OCR unavailable. Flag scanned PDFs as "requires_ocr" so operators know.

### C4. [HIGH] State transition failures don't raise exceptions
**File:** `clearledgr/services/invoice_workflow.py:421-426`
**What happens:** `_transition_invoice_state()` returns False (invalid transition). No exception raised. Code continues as if transition succeeded.
**User impact:** Invoice stuck in wrong state. Downstream operations operate on stale state assumption.
**Fix:** Raise exception when state transition fails. Caller must handle.

### C5. [HIGH] Partial ERP post leaves DB inconsistent
**File:** `clearledgr/services/invoice_workflow.py:677-715`
**What happens:** ERP post succeeds but DB state transition fails. Invoice is posted in ERP but shows as "ready_to_post" in DB.
**User impact:** Operator sees invoice as "ready to post" and may trigger another post. Duplicate in ERP.
**Fix:** Wrap ERP post + DB transition in a single transaction. If DB fails after ERP success, log the ERP reference and set exception state.

### C6. [HIGH] Claude hallucination not validated
**File:** `clearledgr/services/llm_email_parser.py:73-79`
**What happens:** Claude extracts vendor field but hallucinated it (e.g., returns "Stripe" when actual merchant is different). No validation against sender domain or email body.
**User impact:** Wrong vendor assigned. GL coding, routing, and approval all based on wrong vendor.
**Fix:** Cross-check extracted vendor against sender email domain and known vendor aliases.

### C7. [MEDIUM] No extraction result validation
**File:** `clearledgr/services/gmail_triage_service.py:55-69`
**What happens:** Claude returns null for critical fields (vendor, amount). Extraction result is passed downstream without validation.
**User impact:** AP item created with null vendor/amount. Fails at validation gate (caught, but wasteful).
**Fix:** Validate extraction result before proceeding. If critical fields are null, flag as "extraction_failed".

### C8. [MEDIUM] Corrupted base64 attachment fails silently
**File:** `clearledgr/services/email_parser.py:1717-1731`
**What happens:** PDF base64 decode fails. Returns None. Code continues as if extraction succeeded but with no text.
**User impact:** Invoice processed without attachment data. Low confidence, routed for review. Operator may not know the attachment was unreadable.
**Fix:** Return explicit "attachment_corrupted" status so operators know.

### C9. [MEDIUM] Regex JSON fallback matches garbage
**File:** `clearledgr/services/llm_email_parser.py:206-220`
**What happens:** Claude returns malformed JSON. Greedy regex `r"\{[\s\S]+\}"` matches from first `{` to last `}` including non-JSON content between them.
**User impact:** Garbage extraction used as fallback. Wrong data in AP item.
**Fix:** Use non-greedy regex. Validate parsed result has expected fields.

### C10. [MEDIUM] No size limit on PDF processing
**File:** `clearledgr/services/llm_multimodal.py:101-105`
**What happens:** 100MB PDF sent to Claude. Memory exhaustion or timeout.
**User impact:** Server hangs or crashes. Other requests blocked.
**Fix:** Reject PDFs over 25MB before sending to Claude.

### C11. [MEDIUM] Password-protected PDFs fail silently
**File:** `clearledgr/services/email_parser.py:1762-1778`
**What happens:** PyPDF2 raises exception on password-protected PDF. Caught silently. Returns None.
**User impact:** Invoice attachment unreadable. No indication to operator.
**Fix:** Return "attachment_password_protected" status.

### C12. [MEDIUM] Forwarded emails cause duplicate extraction
**File:** `clearledgr/services/email_parser.py:219-244`
**What happens:** Forwarded emails contain nested headers. Parser extracts from both outer and inner headers. May get conflicting vendor/amount data.
**User impact:** Wrong vendor or amount extracted from forwarded invoice.
**Fix:** Detect forwarded email markers. Prefer inner email headers for extraction.

### C13. [MEDIUM] Cascade failures in triage (policy, budget, agent)
**File:** `clearledgr/services/gmail_triage_service.py:98-202`
**What happens:** Policy compliance check, budget check, and agent reasoning calls have no try/except. If any service is down, entire triage fails.
**User impact:** Invoice processing fails completely instead of degrading gracefully.
**Fix:** Wrap each service call in try/except. Continue with reduced context rather than failing entirely.

---

## D. Subscription Enforcement (10 issues)

### D1. [CRITICAL] Gmail autopilot bypasses subscription limits
**File:** `clearledgr/services/gmail_autopilot.py:371+`
**What happens:** Autopilot polls Gmail and processes emails with zero subscription awareness. A free-tier org (25 invoices/month limit) can have unlimited invoices processed automatically.
**User impact:** Free-tier users get unlimited processing. Revenue leakage. No plan differentiation.
**Fix:** Add check_limit() call in the autopilot processing path before creating AP items.

### D2. [CRITICAL] Gmail webhooks/Pub/Sub bypass subscription limits
**File:** `clearledgr/api/gmail_webhooks.py:1046+`
**What happens:** `process_single_email()` and `process_invoice_email()` create AP items without checking subscription limits.
**User impact:** Same as D1 — unlimited processing via push notification path.
**Fix:** Add check_limit() at the start of process_invoice_email().

### D3. [HIGH] /extension/triage endpoint not gated
**File:** `clearledgr/api/gmail_extension.py:602`
**What happens:** Creates AP items via EmailTriageWorkflow without checking subscription limits. Users can call this directly to bypass /process limits.
**User impact:** Limit bypass via alternative endpoint.
**Fix:** Add check_limit() before triage processing.

### D4. [HIGH] /extension/repair-historical-invoices not gated
**File:** `clearledgr/api/gmail_extension.py:896`
**What happens:** Can process up to 500 invoices in one call without checking limits.
**User impact:** Massive quota bypass. Ops user can process entire backlog on free tier.
**Fix:** Add check_limit() with batch count before processing.

### D5. [HIGH] /api/agent/intents/execute not gated
**File:** `clearledgr/api/agent_intents.py:145`
**What happens:** Agent intent execution creates AP items without subscription checks.
**User impact:** Limit bypass via agent API.
**Fix:** Add check_limit() for invoice-creating intents.

### D6. [MEDIUM] /extension/submit-for-approval not gated
**File:** `clearledgr/api/gmail_extension.py:1765`
**What happens:** Calls execute_ap_invoice_processing() without checking limits.
**User impact:** Can submit unlimited invoices for approval.
**Fix:** Add check_limit() before processing.

### D7. [MEDIUM] /extension/post-to-erp not gated
**File:** `clearledgr/api/gmail_extension.py:1355`
**What happens:** Posts to ERP without tracking ERP-posting quota.
**User impact:** Unlimited ERP posts regardless of plan tier.
**Fix:** Add increment_usage() for ERP posting operations.

### D8. [MEDIUM] AP item split creates items without quota tracking
**File:** `clearledgr/api/ap_items_action_routes.py:734`
**What happens:** Splitting an invoice creates N new AP items. None count against quota.
**User impact:** Quota bypass by splitting invoices.
**Fix:** increment_usage() by split count after split.

### D9. [MEDIUM] Finance runtime processing doesn't track usage
**File:** `clearledgr/services/finance_runtime_invoice_processing.py:12+`
**What happens:** The canonical invoice processing entry point has zero quota tracking. All 5+ callers bypass limits.
**User impact:** All invoice creation paths miss usage tracking.
**Fix:** Add increment_usage() in the finance runtime after successful AP item creation. This is the single chokepoint — fix here covers all callers.

### D10. [MEDIUM] Usage counters never reset monthly
**File:** `clearledgr/services/subscription.py`
**What happens:** `last_reset` timestamp exists but is never used. Once an org hits the monthly limit, they're locked out permanently until manual reset.
**User impact:** Paying customers locked out at the start of a new month.
**Fix:** Check `last_reset` before every check_limit(). If month has changed, reset counters.

---

## Priority Matrix

### Fix immediately (blocks pilot)
- A1, A2, A5, A6 — Approval delivery reliability
- B1 — ERP token refresh on 401
- B3, B6 — Dead letter alerting
- C1 — NOISE classification audit trail
- D1, D2 — Autopilot/webhook subscription enforcement

### Fix before enterprise (blocks scaling)
- A3, A4, A7 — Slack message reliability
- B2, B4, B8 — ERP error classification and idempotency
- C2, C4, C5 — State machine reliability
- D3, D4, D5, D9, D10 — Full subscription enforcement

### Fix when time permits (polish)
- A8, A9, A10, A11 — Teams parity with Slack
- B5, B7, B9 — Edge case hardening
- C3, C6-C13 — Extraction quality improvements
- D6, D7, D8 — Minor quota gaps
