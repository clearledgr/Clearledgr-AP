# Clearledgr Agent Design Specification

**How the Clearledgr agent thinks, acts, and recovers** · Internal engineering reference

---

This document defines the Clearledgr agent from the inside: its architecture, event system, formal action space, planning and execution layers, state management, LLM/deterministic boundary, invoice and vendor onboarding lifecycles, waiting and resumption model, error handling, and context window management. The Product Design Thesis describes what the agent does from the outside. This document is how it actually works.

> Confidential — Clearledgr Ltd · Engineering team only

---

## 1. Architecture Overview

The Clearledgr agent is an event-driven, stateful execution system with two primary layers: a planning engine that decides what needs to happen given an event and current Box state, and an execution engine that carries out the plan one action at a time, recording each action to the Box timeline before executing it.

The agent is not a single LLM call. It is an orchestration system that calls Claude for specific, bounded tasks — classification, extraction, natural language generation — and delegates everything else to deterministic rules. The intelligence is in knowing exactly where the boundary is. The audit trail is in recording every step before it happens.

**The fundamental design principle:** the agent never acts on what it thinks. It acts on what the rules permit, informed by what Claude extracted. Rules decide. Claude describes. This is what makes a finance agent auditable.

### 1.1 Top-Level Architecture

| Component | Purpose and responsibility |
|-----------|---------------------------|
| **Gmail Pub/Sub Listener** | Receives push notifications from Gmail when new emails arrive in watched inboxes. Validates the notification, fetches the message content, and enqueues an event. This is the entry point for all invoice and vendor email processing. |
| **Event Queue** | A durable, ordered queue (Redis Streams or equivalent) that holds all incoming events pending agent processing. Events are not lost if the agent is unavailable — they wait. The queue decouples event receipt from agent processing. |
| **Planning Engine (AgentPlanningEngine)** | Given an event and the current Box state, produces a Plan — an ordered sequence of Actions the agent intends to take. The plan is produced deterministically for known event types. Claude is called within the planning engine only for classification and extraction, where the input is unstructured. |
| **Execution Engine (FinanceAgentRuntime)** | Executes the Plan produced by the planning engine, one Action at a time. Before executing each Action, writes a timeline entry recording what is about to happen and why. If an Action fails, records the failure and invokes the error handler. The execution engine never skips steps and never silently succeeds. |
| **Box State Store** | A PostgreSQL database table holding the current state of every Box: stage, all extracted fields, match result, pending plan (if interrupted), waiting condition (if the agent is paused), retry counts, and timestamps. This is the agent's memory across restarts and async waits. |
| **Job Scheduler** | A scheduled job queue (Celery Beat or equivalent) that handles time-based resumption: checking GRN status in 4 hours, resending a vendor chase email in 48 hours, escalating a stalled approval. When the agent decides to wait, it enqueues a future job rather than blocking. |
| **ERP Connector Layer** | A set of typed connector classes, one per supported ERP (NetSuite, SAP, Xero, QuickBooks), each implementing the same interface: `lookup_po()`, `lookup_grn()`, `lookup_vendor()`, `post_bill()`, `schedule_payment()`. The execution engine calls the interface; the connector handles ERP-specific authentication, rate limiting, and error translation. |
| **LLM Gateway** | A thin wrapper around the Claude API that manages prompt construction, token counting, retry logic, and response validation. All Claude calls in the system go through this gateway. It logs every call with input tokens, output tokens, latency, and cost to the cost tracking database. |
| **Gmail Action Client** | Handles all outbound Gmail API operations: applying labels, sending emails, splitting threads, watching inboxes. Separate from the Pub/Sub listener which handles inbound only. |
| **Slack/Teams Client** | Posts structured messages, interactive approvals, and digests to Slack or Teams. Handles button callbacks — when an AP Manager clicks Approve in Slack, the callback arrives here and re-enters the event queue as an `approval_received` event. |

### 1.2 The Two Non-Negotiable Rules

**Rule 1:** Every action is recorded to the Box timeline before it executes, not after. If the system crashes between the timeline write and the action execution, the timeline shows "about to post to ERP" — which is enough to reconstruct state and retry safely. An action that executes without a prior timeline entry is a bug.

**Rule 2:** The execution engine never assumes success. Every external call — ERP write, Slack post, Gmail API — must return a confirmation before the Box stage advances. If the confirmation is not received, the Box stage does not change and the action is marked as pending. The agent retries on the next resume, it does not skip forward.

---

## 2. The Event System

### 2.1 Gmail Pub/Sub Watch

The customer's Gmail inbox (the shared ap@ address and any individual addresses configured during onboarding) is watched via the Gmail API `watch()` method. Gmail pushes notifications to Clearledgr's registered Pub/Sub topic when new messages arrive. The notification contains the history ID and the email address — it does not contain the message content. The listener fetches the message content separately using the history ID to list changes since the last known history ID.

| Watch lifecycle step | Implementation detail |
|----------------------|----------------------|
| **Initial watch setup** | Called during onboarding when the customer connects their Gmail. Creates the watch subscription and stores the returned `historyId` as the cursor for this mailbox. Watch subscriptions expire after exactly 7 days — never less, never more. |
| **Watch renewal** | A scheduled job runs every 6 days (with 1 day buffer before expiry) and renews all active watches via a fresh `watch()` call. The new `historyId` is stored as the new cursor. If renewal fails, the mailbox is marked as degraded and the CS team is alerted via the Backoffice ERP connector health dashboard. |
| **Notification receipt** | The Pub/Sub push endpoint validates the Google-signed JWT on every notification. Invalid signatures are rejected immediately and logged. Valid notifications are acknowledged immediately — Gmail requires acknowledgment within 30 seconds or it retries. |
| **Message fetch** | After acknowledging the notification, the listener calls `gmail.users.history.list()` with the stored cursor to get all message changes since the last notification. Each new message ID is enqueued as an `email_received` event. The cursor is updated to the new `historyId`. |
| **Deduplication** | The event queue uses the Gmail message ID as the idempotency key. If the same message ID appears twice (Gmail occasionally delivers duplicate notifications), the second enqueue is silently dropped. |
| **Backfill on reconnection** | If the watch lapses (renewal failure, OAuth token expiry), the listener fetches all messages since the last known cursor when the watch is restored. This ensures no invoices are missed during an outage window. |

### 2.2 Event Types

Every event that enters the system has a type, a source, and a payload. The planning engine dispatches on event type. Adding a new event type means adding a handler to the planning engine — no other part of the system changes.

| Event type | Trigger and payload |
|------------|---------------------|
| `email_received` | Gmail Pub/Sub notification for a new message. Payload: `{message_id, thread_id, mailbox, received_at}` |
| `approval_received` | AP Manager clicks Approve or Reject in Slack or Teams. Payload: `{box_id, decision, actor_email, timestamp, override_reason?}` |
| `erp_grn_confirmed` | Scheduled job detects that a GRN for a waiting invoice has been confirmed in the ERP. Payload: `{box_id, grn_reference, confirmed_at}` |
| `vendor_response_received` | A vendor replies to an onboarding or chase email. Payload: `{message_id, thread_id, vendor_id, response_type}` |
| `kyc_document_received` | A vendor submits a KYC document via the onboarding portal. Payload: `{vendor_id, document_type, document_url}` |
| `payment_confirmed` | ERP or payment rail confirms that a scheduled payment has settled. Payload: `{box_id, payment_reference, settled_at, amount}` |
| `timer_fired` | A scheduled job's time condition has been met. Payload: `{box_id, timer_type, original_scheduled_at}`. Used for: GRN polling, vendor chase escalation, approval timeout. |
| `manual_classification` | AP Manager manually classifies a Clearledgr/Review Required email. Payload: `{message_id, classification, classified_by}` |
| `iban_change_submitted` | A vendor submits a new IBAN via the onboarding portal. Payload: `{vendor_id, new_iban_token, submitted_at}`. Always triggers the three-factor verification flow regardless of source. |
| `override_window_expired` | A timer confirming the override window for an autonomous action has closed. Payload: `{box_id, action_type, action_timestamp}`. The action is now irreversible. |

---

## 3. The Formal Action Space

The agent can take exactly the actions listed below. No more. Every action is either deterministic (DET) or LLM-assisted (LLM). LLM actions call Claude. Deterministic actions execute rules or API calls without a language model. The Layer column is non-negotiable — a deterministic action that is reimplemented as an LLM call is a regression, not an improvement.

The action space is closed. If a new capability is needed that is not expressible as a composition of these actions, the action space is extended through a deliberate engineering decision — not by having the agent improvise. An agent that can do anything can be made to do anything wrong.

### Email and Inbox Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `read_email(message_id)` | DET | Fetch full email content from Gmail API: headers, plain text body, HTML body, attachment metadata. Returns a structured `EmailContent` object. |
| `fetch_attachment(message_id, attachment_id)` | DET | Download a specific attachment from Gmail. Returns raw bytes. Called only for attachments identified as likely invoices by the classification step. |
| `apply_label(thread_id, label)` | DET | Apply a Clearledgr Gmail label to the thread via Gmail API. Idempotent — applying a label that is already present is a no-op. Removes conflicting stage labels before applying the new one. |
| `remove_label(thread_id, label)` | DET | Remove a specific label from a thread. Used when an invoice moves stages. |
| `split_thread(thread_id, message_id)` | DET | Split a Gmail thread at a specific message, creating a new thread from that message forward. Used when a vendor sends a new invoice as a reply to an existing thread. |
| `send_email(to, subject, body, thread_id?)` | DET | Send an email from the AP inbox via Gmail API. Optional `thread_id` to reply within an existing thread. All outbound emails are logged to the Box timeline. |
| `watch_thread(thread_id, box_id)` | DET | Register a thread for monitoring. New replies to watched threads trigger `email_received` events routed directly to the relevant Box, bypassing the classification step. |

### Classification and Extraction Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `classify_email(email_content)` | LLM | Call Claude to classify the email as one of: `invoice`, `credit_note`, `payment_query`, `vendor_statement`, `onboarding_response`, `contract_renewal`, `irrelevant`, `unclassifiable`. Returns `{type, confidence, reasoning}`. If confidence < 0.80, returns `unclassifiable` regardless of the top prediction. |
| `extract_invoice_fields(email_content, attachments)` | LLM | Call Claude to extract structured invoice fields: `vendor_name`, `invoice_reference`, `invoice_date`, `due_date`, `currency`, `line_items[]`, `total_amount`, `po_reference`, `payment_terms`, `iban` (if present). Returns `{fields, extraction_confidence_per_field, raw_ocr_text}`. |
| `run_extraction_guardrails(extracted_fields, email_content)` | DET | Apply all five extraction guardrails deterministically against the extracted fields. Returns `{passed: bool, failures: [{guardrail, expected, actual}]}`. Called immediately after every `extract_invoice_fields` call before any further processing. |
| `generate_exception_reason(match_result, invoice, po, grn)` | LLM | Given a failed match result with specific rule failures, generate a plain-language explanation for the AP Manager. Input is structured data. Output is a single paragraph in the DID-WHY-NEXT format. The match result itself is already computed deterministically — this action only generates the human-readable description of it. |
| `classify_vendor_response(email_content, vendor_id)` | LLM | Classify a vendor's reply to an onboarding or chase email: `document_submitted`, `question_asked`, `refused`, `out_of_office`, `incorrect_contact`, `unclassifiable`. Returns `{type, confidence, extracted_info}`. |

### ERP Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `lookup_vendor_master(domain, name, erp)` | DET | Query the ERP vendor master for a match by email domain and/or name. Returns `{found: bool, vendor_record?, match_confidence}`. Does not create — only reads. |
| `lookup_po(po_reference, erp)` | DET | Fetch a specific Purchase Order from the ERP. Returns `{found: bool, po_record?, status}`. Status includes: `open`, `closed`, `partially_receipted`, `fully_receipted`. A fully_receipted PO cannot receive further invoices. |
| `lookup_grn(po_reference, erp)` | DET | Fetch Goods Receipt Notes associated with a PO reference. Returns `{grns: GRN[], total_received_quantity, total_received_amount}`. May return multiple GRNs for partial receipts. |
| `run_three_way_match(invoice, po, grn)` | DET | Execute the deterministic 3-way match algorithm. Compares invoice amount vs GRN amount within configured tolerance, invoice PO reference vs ERP PO record, GRN confirmation date vs invoice date, payment terms. Returns `{passed: bool, match_score, failures: [{rule, expected, actual, delta}]}`. Never calls Claude. |
| `post_bill(invoice_data, erp)` | DET | Write the invoice as a bill to the ERP. Requires pre-post validation to pass first. Returns `{success: bool, erp_bill_id?, error?}`. On success, returns the ERP-assigned bill ID which is stored in the Box record. On failure, returns the specific ERP error without retrying automatically. |
| `pre_post_validate(box_id, erp)` | DET | Re-read the Box's extracted data and validate against current ERP state before posting: PO still open, GRN still unmatched, vendor still active, no duplicate bill with same reference in trailing 90 days. Returns `{valid: bool, failures: []}`. Must pass before any `post_bill` call. |
| `schedule_payment(vendor, amount, due_date, currency, erp)` | DET | Create a payment schedule entry in the ERP for a confirmed invoice. Returns `{scheduled: bool, payment_reference?}`. Does not execute the payment — schedules it for the ERP's payment run. |
| `reverse_erp_post(erp_bill_id, reason, erp)` | DET | Reverse a previously posted bill in the ERP. Used during disaster recovery. Returns `{reversed: bool, reversal_reference?}`. Requires CFO-role authorization stored in the action context. |

### Box and State Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `create_box(pipeline, initial_fields)` | DET | Create a new Box in the specified pipeline with initial field values. Returns `{box_id}`. The Box is created in the first stage of the pipeline (Received for AP Invoices). |
| `update_box_fields(box_id, fields)` | DET | Update specific fields on a Box record. Partial update — only the named fields are changed. Returns `{success: bool}`. |
| `move_box_stage(box_id, target_stage, reason)` | DET | Advance or revert a Box to a specific pipeline stage. Validates that the transition is permitted by the stage transition rules before executing. Returns `{success: bool, previous_stage}`. Blocked transitions are logged as errors. |
| `post_timeline_entry(box_id, entry)` | DET | Write an entry to the Box timeline. Entry types: `agent_action` (what the agent did and why), `human_action` (what a person did), `exception` (a flag requiring human attention), `system` (infrastructure events). This is the pre-execution record — it is written before the action it describes executes. |
| `link_vendor_to_box(box_id, vendor_id)` | DET | Associate a Vendor record with an invoice Box. Enables vendor-level duplicate detection and history lookup. |
| `set_waiting_condition(box_id, condition)` | DET | Record that the agent is waiting for a specific condition before proceeding: `grn_confirmation`, `approval_response`, `vendor_onboarding_completion`, `iban_verification`. Stores the condition and schedules the appropriate timer job. |
| `clear_waiting_condition(box_id)` | DET | Clear the waiting condition on a Box when the condition is met. The planning engine then re-evaluates the Box state and continues the plan. |
| `set_pending_plan(box_id, plan)` | DET | Persist the current plan to the Box state so it can be resumed after an interruption or restart. The plan is stored as a JSON array of remaining actions with their parameters. |

### Communication Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `send_slack_approval(box_id, approver, invoice_summary)` | DET | Send a structured interactive approval message to the specified approver's Slack DM. The message is constructed deterministically from the Box state — no LLM call. Stores the message timestamp for editing on resolution. |
| `send_slack_exception(box_id, channel, exception_summary)` | DET | Post an exception notification to the configured AP Slack channel. Includes the LLM-generated exception reason from `generate_exception_reason`. Includes resolution option buttons. |
| `send_slack_override_window(box_id, action_type, close_time)` | DET | Post the override window notification to the AP Manager's DM after an autonomous action. Includes a live Undo button that remains active until `close_time`. |
| `send_slack_digest(workspace_id)` | DET | Assemble and post the conditional digest to the AP channel. Fires only if there is at least one item requiring attention. Digest content is assembled from Box state — no LLM call. |
| `send_vendor_email(vendor_id, template, params)` | DET | Send a templated email to a vendor using the AP inbox. Templates cover: onboarding invitation, document request, IBAN verification request, payment query response, chase. Template is selected deterministically; parameters are filled from Box and Vendor state. |
| `draft_vendor_response(email_content, vendor_id, query_type)` | LLM | For non-templated vendor queries where the agent needs to compose a contextual reply, call Claude to draft a response based on the query content and available ERP data. Always staged for AP Manager review — never sent autonomously. |
| `send_teams_approval(box_id, approver, invoice_summary)` | DET | Microsoft Teams equivalent of `send_slack_approval`. Same logic, Teams Graph API. |
| `post_gmail_notification(workspace_id, event_type, box_id)` | DET | Trigger a Gmail-native desktop notification for a specific event. Used for in-inbox notification parallel to Slack. Implemented via the Google Workspace Notifications API. |

### Vendor Onboarding Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `create_vendor_record(vendor_data)` | DET | Create a new Vendor record in the Clearledgr vendor master with status: `pending_onboarding`. Does not activate the vendor in the ERP. |
| `enrich_vendor(vendor_id)` | DET | Call external enrichment APIs (Companies House, HMRC VAT register, open corporate registries) to pre-populate the Vendor record with public registration data. Stores source and timestamp per enriched field. |
| `run_adverse_media_check(vendor_id)` | DET | Run an adverse media check against the vendor's registered directors and company name. Returns `{clear: bool, flags: []}`. A flagged check requires AP Manager review before the vendor can be activated. |
| `initiate_micro_deposit(vendor_id, iban)` | DET | Send a micro-deposit to the vendor's provided IBAN to verify bank account ownership. Stores the deposit amount (known only to Clearledgr) for verification. Does not reveal the amount to the vendor. |
| `verify_micro_deposit(vendor_id, amount_claimed)` | DET | Compare the amount claimed by the vendor against the stored deposit amount. Returns `{verified: bool}`. Three failed attempts locks the IBAN and requires AP Manager intervention. |
| `activate_vendor_in_erp(vendor_id, erp)` | DET | Create or activate the vendor in the ERP vendor master with the verified bank details. Requires both KYC complete and IBAN verified flags on the Vendor record. Returns `{activated: bool, erp_vendor_id?}`. |
| `freeze_vendor_payments(vendor_id, reason)` | DET | Apply a payment hold on all invoices from a vendor. New invoices from the vendor are processed but not scheduled for payment until the hold is lifted. Triggered automatically on IBAN change detection. |

### Fraud Control Actions

| Action | Layer | Description |
|--------|-------|-------------|
| `check_iban_change(vendor_id, new_iban)` | DET | Detect whether a submitted IBAN differs from the vendor's current active IBAN. If different: immediately calls `freeze_vendor_payments`, flags the change for three-factor verification, and alerts the AP Manager. Returns `{changed: bool, frozen: bool}`. |
| `check_domain_match(sender_email, vendor_id)` | DET | Validate that the invoice sender's email domain matches the registered domain for the vendor in the vendor master. Returns `{matched: bool, registered_domain, sender_domain}`. A domain mismatch blocks all processing. |
| `check_velocity(vendor_id, window_days)` | DET | Count invoices received from this vendor in the trailing window. Returns `{count, threshold, exceeded: bool}`. If exceeded, all additional invoices from this vendor in the window are flagged. |
| `check_duplicate(vendor_id, reference, amount, window_days)` | DET | Check the trailing window for any Box with matching vendor + reference + amount combination. Returns `{duplicate_found: bool, existing_box_id?}`. A found duplicate blocks processing and flags for human review. |
| `flag_internal_instruction(email_content, sender)` | DET | Detect emails from internal sender addresses (matching the customer's domain) that instruct the agent to take payment or configuration actions. Returns `{flagged: bool, instruction_type?}`. Flagged emails are classified as Finance/Query and never actioned autonomously. |
| `check_amount_ceiling(vendor_id, amount, currency)` | DET | Validate that the invoice amount does not exceed the per-vendor or global payment ceiling configured in Settings. Returns `{within_ceiling: bool, ceiling, currency}`. Exceeding the ceiling routes to CFO approval regardless of other match results. |

---

## 4. The Planning Engine

The planning engine takes an event and the current Box state as inputs and produces a Plan — an ordered sequence of Actions. The planning engine is the agent's decision logic. It is invoked once per event. It does not execute anything — that is the execution engine's job.

### 4.1 Planning for `email_received`

This is the most complex planning path — it handles all incoming emails and must produce the correct plan for twelve distinct classification outcomes.

| Planning step | Logic |
|---------------|-------|
| **1. Fetch and check** | `read_email(message_id)`. Check if `thread_id` is already watched (linked to an existing Box). If yes: skip classification, route directly to the box's current state handler. If no: continue to classification. |
| **2. Classify** | `classify_email(email_content)`. If confidence < 0.80: plan = `[apply_label(Review Required), create_box(AP Invoices, {stage: needs_classification}), post_timeline_entry('Low confidence classification — awaiting AP Manager review'), send_gmail_notification(workspace, classification_needed, box_id)]`. Stop. |
| **3. Route by type** | Branch on classification type. Invoice → invoice plan. Credit note → credit note plan. Payment query → query plan. Vendor statement → statement plan. Onboarding response → onboarding plan. Irrelevant → `apply_label(Not Finance)`, archive. Unclassifiable → `apply_label(Review Required)`, flag. |
| **4. Invoice plan — vendor check** | `lookup_vendor_master(sender_domain, sender_name, erp)`. If not found: plan includes `apply_label(Review Required)`, flag with reason "Unknown vendor", `send_slack_exception`. Stop — do not extract or match for unknown vendors. |
| **5. Invoice plan — duplicate check** | `check_duplicate(vendor_id, reference_if_extractable, amount_if_extractable)`. If duplicate found: `apply_label(Review Required)`, link to existing Box, alert AP Manager with both Box references. |
| **6. Invoice plan — extraction** | `extract_invoice_fields`, then `run_extraction_guardrails`. If any guardrail fails: plan = `[apply_label(Review Required), post_timeline_entry with specific guardrail failures, send_slack_exception with exact failure details]`. Stop — no partial extraction proceeds. |
| **7. Invoice plan — fraud checks** | `check_domain_match`, `check_velocity`, `check_amount_ceiling`. Any failure triggers the appropriate fraud control response and stops the plan. |
| **8. Invoice plan — ERP lookups** | `lookup_po(po_reference)`. If not found: flag as no-PO exception, route to procurement contact. If found: `lookup_grn(po_reference)`. If GRN not yet confirmed: `set_waiting_condition(grn_confirmation)`, schedule `timer_fired` in 4 hours. If GRN confirmed: continue. |
| **9. Invoice plan — matching** | `run_three_way_match`. If passed: plan includes `move_box_stage(Matched)`, `apply_label(Matched)`, route for approval. If failed: `generate_exception_reason`, plan includes `move_box_stage(Exception)`, `apply_label(Exception)`, `send_slack_exception`. |
| **10. Invoice plan — approval routing** | If within auto-approve threshold (and autonomy tier is Autonomous for this action type): plan includes `post_bill` after `pre_post_validate`, then `schedule_payment`, then `send_slack_override_window`. If manual approval required: `send_slack_approval` to configured approver, `set_waiting_condition(approval_response)`, schedule approval timeout timer. |

### 4.2 Planning for `approval_received`

| Decision | Plan |
|----------|------|
| **Approved, box in Matched stage** | `pre_post_validate` → `post_bill` → `move_box_stage(Approved)` → `apply_label(Approved)` → `schedule_payment` → `post_timeline_entry(DID-WHY-NEXT)` → `send_slack_override_window` → `watch_thread` for payment confirmation. |
| **Approved with override (exception box)** | Same as above but with `override_reason` logged to timeline and override event sent to Backoffice quality dashboard. |
| **Rejected** | `move_box_stage(Exception)` → `apply_label(Exception)` → `send_vendor_email(payment_query_response template with rejection reason)` → `post_timeline_entry` → notify AP Manager of rejection logged. |
| **Approval timeout (`timer_fired: approval_timeout`)** | Escalate to the next approver in the hierarchy. If already at CFO level: `send_slack_exception` with urgency flag indicating payment due date. Do not auto-approve. Do not let it silently stall. |

### 4.3 Planning for `timer_fired`

| Timer type | Plan on firing |
|------------|----------------|
| `grn_check` | `lookup_grn(po_reference)`. If confirmed: `clear_waiting_condition`, resume from step 8 of invoice plan. If still pending: check against invoice due date. If due within 48 hours: escalate to AP Manager. Otherwise: reschedule check in 4 hours. Maximum 10 retries before mandatory escalation. |
| `vendor_chase` | Check vendor response received. If not received: `send_vendor_email(chase template)`. Post preview to Slack channel with 30-minute hold window. Increment chase count on Vendor record. |
| `approval_timeout` | Escalate approval to next tier in hierarchy. Log escalation to Box timeline. |
| `override_window_close` | `post_timeline_entry('Override window closed — action confirmed')`. Mark `override_window_expired` event. Action is now irreversible. |
| `vendor_iban_verification_deadline` | If three-factor IBAN verification not complete within 5 business days: `freeze_vendor_payments` until complete. Alert AP Manager. |

---

## 5. The Execution Engine

The execution engine takes a Plan from the planning engine and executes it, one action at a time. Its only job is faithful, recorded execution. It adds no intelligence — the planning engine has already decided what to do. The execution engine makes sure what was decided actually happens, in order, with a record of every step.

### 5.1 The Execution Loop

| Step | What the execution engine does |
|------|-------------------------------|
| **1. Load plan** | Read the current plan from Box state (`set_pending_plan` stored by the planning engine). If a partial plan exists from a previous interrupted execution, resume from the first incomplete action. |
| **2. Take next action** | Dequeue the next Action from the plan. |
| **3. Pre-execution timeline write** | `post_timeline_entry` with type: `agent_action`, action: `{action_name, parameters}`, status: `executing`. This is the record that the action is about to happen. It is written before the action executes. |
| **4. Execute the action** | Call the action with its parameters. This may be an API call, a database write, or a Claude call. Set a timeout appropriate to the action type (LLM calls: 30s, ERP calls: 10s, Gmail API: 5s). |
| **5. Handle the result** | If success: update the pre-execution timeline entry with status: `completed` and result summary. Advance to the next action. If failure: see Error Handling (§5.2). |
| **6. Check for async wait** | If the action returns a waiting condition (e.g., `set_waiting_condition`): persist the remaining plan to Box state via `set_pending_plan`, enqueue the appropriate timer job, exit the execution loop. The Box is now waiting. |
| **7. Complete or continue** | If all actions in the plan are complete: mark the plan as done, clear the pending plan from Box state. If more actions remain: loop back to step 2. |

### 5.2 Error Handling

Every action can fail in one of four ways. The execution engine's response depends on which type of failure occurred.

| Failure type | Execution engine response |
|--------------|--------------------------|
| **Transient** (network timeout, rate limit, temporary API unavailability) | Retry with exponential backoff. Maximum 3 retries with delays of 5s, 30s, 2min. If all retries fail: treat as persistent failure. |
| **Persistent** (permission error, invalid data, ERP validation rejection) | Do not retry. Post a timeline entry with status: `failed` and the specific error. Move the Box to Exception stage. Send a Slack exception alert with the specific error message. A human must resolve before the agent can continue. |
| **External dependency unavailable** (ERP offline, Slack API down) | Pause execution. Set `waiting_condition: external_dependency_unavailable`. Schedule a check in 15 minutes. Alert the CS team via Backoffice monitoring if the dependency has been unavailable for more than 30 minutes. |
| **LLM failure** (Claude returns an error, safety refusal, malformed output) | For classification failures: treat as unclassifiable. For extraction failures: treat as extraction guardrail failure (surface to AP Manager). For generation failures (exception reason, vendor email draft): use a default template instead. Never surface a Claude error message directly to the AP team. |

The execution engine never partially succeeds. If an action fails after the pre-execution timeline entry has been written, the timeline shows "executing" — which is exactly the information needed to investigate and retry. A partially-executed plan is recoverable. A silently-failed plan is not.

---

## 6. State Management

The agent's memory is the Box state stored in PostgreSQL. The agent has no in-process memory between events — it reads full Box state at the start of every event handler and writes any state changes before they take effect. This makes the agent stateless at the process level and stateful at the data level.

### 6.1 The Box State Object

| Field | Type and purpose |
|-------|-----------------|
| `box_id` | UUID. Primary key. |
| `pipeline` | Enum: `ap_invoices`, `vendor_onboarding`. The pipeline this Box belongs to. |
| `stage` | Enum: `received`, `matching`, `exception`, `approved`, `paid` (AP) / `invited`, `kyc`, `bank_verify`, `active` (Vendor). Current stage. |
| `thread_id` | String. Gmail thread ID. Used to route new replies to this Box. |
| `vendor_id` | UUID FK. The Vendor record associated with this invoice. |
| `extracted_fields` | JSONB. All fields extracted from the invoice: reference, amount, currency, due_date, po_reference, payment_terms, line_items. Updated on successful extraction. Null until extraction completes. |
| `extraction_confidence` | JSONB. Per-field confidence scores from the LLM extraction call. Stored for quality monitoring. |
| `match_result` | JSONB. Output of `run_three_way_match`: passed, match_score, individual rule results. Null until matching completes. |
| `erp_bill_id` | String. The bill ID assigned by the ERP after a successful post. Null until `post_bill` succeeds. |
| `payment_reference` | String. The payment reference from the ERP after payment scheduling. Null until `schedule_payment` succeeds. |
| `pending_plan` | JSONB. The remaining actions in the current plan, serialised. Null when the agent is not mid-execution. Populated by `set_pending_plan` when the agent needs to wait or was interrupted. |
| `waiting_condition` | JSONB. The current waiting condition if the agent is paused: `{type, expected_by, context}`. Null when the agent is not waiting. |
| `retry_counts` | JSONB. Per-action retry counts: `{action_name: count}`. Prevents infinite retry loops. |
| `fraud_flags` | JSONB. Active fraud flags on this Box: `{flag_type, detected_at, resolved_at?, resolved_by?}`. |
| `override_window_closes_at` | Timestamp. When the override window for the most recent autonomous action closes. Null if no override window is active. |
| `created_at`, `updated_at` | Timestamps. Standard audit fields. |

### 6.2 State Transitions and the Transition Rules

Stage transitions are deterministic and validated. The execution engine calls `move_box_stage` only after confirming the transition is permitted. Attempting an invalid transition logs an error and does not move the Box.

| From stage | Permitted next stages and conditions |
|------------|--------------------------------------|
| `received` | → `matching` (extraction passed, vendor known, fraud checks passed). → `exception` (extraction guardrail failed, unknown vendor, fraud flag). → `needs_classification` (low confidence classification). |
| `matching` | → `exception` (match failed). → `approved` (match passed, auto-approve threshold). → `awaiting_approval` (match passed, manual approval required). |
| `awaiting_approval` | → `approved` (approval received). → `exception` (rejected). → `awaiting_approval` (escalated to next approver — stays in stage, approver changes). |
| `exception` | → `matching` (AP Manager resolved exception and approved re-match). → `approved` (AP Manager override approved). → `closed` (AP Manager rejected invoice — vendor notified). |
| `approved` | → `paid` (payment confirmed by ERP). |
| `paid` | Terminal. No further transitions. Box is archived after configured retention trigger. |

---

## 7. The LLM/Deterministic Boundary

This section defines precisely which actions call Claude, what they pass as input, what they receive as output, and what constraints apply to that output before it is used. The boundary is enforced in code — the LLM gateway rejects calls from deterministic actions and logs them as bugs.

### 7.1 When Claude Is Called

| LLM action | Input to Claude | Output from Claude — and how it is used |
|------------|-----------------|------------------------------------------|
| `classify_email` | Email headers, plain text body (first 2000 tokens), attachment type list. System prompt instructs Claude to return JSON only: `{type, confidence, reasoning}`. | JSON parsed and validated against the classification enum. If parsing fails or confidence < 0.80: treated as unclassifiable. The classification type is used by the planning engine to route. The reasoning is stored in the Box timeline entry but never shown directly to the AP team. |
| `extract_invoice_fields` | Email headers, plain text body, OCR text from attachments (up to 4000 tokens total). System prompt instructs Claude to return JSON only with the invoice field schema. | JSON parsed and validated against the invoice field schema. Every numeric field is re-validated against the extraction guardrails before use. If schema validation fails: treated as extraction failure. Extracted fields are never used for any ERP action before guardrails pass. |
| `generate_exception_reason` | The structured match result (which rule failed, expected value, actual value, delta). The invoice extracted fields. The PO and GRN data from the ERP. System prompt instructs Claude to write one paragraph in DID-WHY-NEXT format, factual and precise. | Plain text, maximum 150 words. Validated for length. The match result (pass/fail) was already determined by the rule engine before this call — Claude is only generating the human-readable description of an already-computed result. If this call fails, a template message is used instead. |
| `classify_vendor_response` | The vendor's reply email content and the context of what was requested (which document, which onboarding step). System prompt instructs Claude to return JSON: `{type, confidence, extracted_info}`. | JSON parsed and validated. Used by the planning engine to determine the next onboarding action. If confidence < 0.75: treated as unclassifiable, surface to AP Manager. |
| `draft_vendor_response` | The vendor's query, the relevant invoice or payment data from the Box state, and any ERP data that is relevant to answering the query. System prompt instructs Claude to draft a professional, factual reply. | Plain text. Always staged for AP Manager review via the Slack approval mechanism. Never sent directly. AP Manager can edit before sending. |

### 7.2 The System Prompt Architecture

Every Claude call uses a structured system prompt with four fixed sections. The sections are constant across calls for the same action type — only the user content changes. This makes prompt behaviour consistent, testable, and auditable.

| System prompt section | Content and purpose |
|-----------------------|---------------------|
| **Role** | "You are a precise finance data extraction and reasoning assistant. You process accounts payable documents for professional finance teams. Your outputs are used in automated financial workflows where accuracy is critical." |
| **Output format** | Exact JSON schema for this action type, including field names, types, and constraints. "Return only valid JSON. No preamble. No explanation outside the JSON. No markdown formatting." |
| **Constraints** | Specific constraints for this action: "Do not infer values that are not present in the document. If a field is not found, return null for that field rather than guessing. Do not convert currencies. Return amounts exactly as they appear." |
| **Guardrail reminder** | "If you are uncertain about any numeric value, set the confidence for that field to below 0.5 rather than returning a value you are not confident in. A low-confidence extraction that surfaces to a human is safer than a high-confidence incorrect extraction." |

### 7.3 Token Budget Management

Every LLM call has a fixed token budget enforced by the LLM Gateway. Inputs that exceed the budget are truncated, with the truncation logged as a quality signal. Outputs that exceed the expected size are treated as malformed and not used.

| LLM action | Input token budget |
|------------|-------------------|
| `classify_email` | 2,000 tokens |
| `extract_invoice_fields` | 4,000 tokens |
| `generate_exception_reason` | 1,000 tokens |
| `classify_vendor_response` | 2,000 tokens |
| `draft_vendor_response` | 3,000 tokens |

---

## 8. Context Window Management

An invoice Box that has been open for several weeks may have a timeline with 50+ entries. Loading all of this into a Claude call is wasteful, slow, and often unnecessary — most of what the agent needs for the current action is available in the structured Box state, not the timeline narrative.

The agent never loads the full Box timeline into a Claude call. It loads exactly what is needed for the specific action being taken.

| LLM action | What context is loaded |
|------------|----------------------|
| `extract_invoice_fields` | The email content and attachments for this specific invoice email. No Box history. The extraction task is stateless — it only needs the document. |
| `generate_exception_reason` | The structured match result and the invoice/PO/GRN data. The Box timeline is not loaded — the match result itself contains all the information Claude needs to explain the failure. |
| `draft_vendor_response` | The vendor's query email. The Box's extracted fields (invoice reference, amount, payment status). The last 3 timeline entries for context. Maximum 3,000 tokens total. |
| `classify_vendor_response` | The vendor's response email and a summary of the current onboarding stage. No full timeline. |

### 8.1 The Box Summary Object

The planning engine maintains a structured summary of each Box alongside the full timeline. The summary is updated after every state change. When the agent needs to reason about a Box that has a long history, it uses the summary rather than the timeline.

| Summary field | Content |
|---------------|---------|
| `current_stage` | The Box's current pipeline stage. |
| `key_fields` | The five most important extracted fields for this Box type: vendor, reference, amount, due_date, po_reference. |
| `match_result_summary` | One line: "Matched — passed within 0.3% tolerance" or "Exception — GRN delta £422". |
| `last_3_actions` | The last three timeline entries, condensed to one line each. |
| `open_issues` | Any unresolved flags: missing document, failed guardrail, outstanding approval. |
| `waiting_since` | If the Box is in a waiting state: what it is waiting for and since when. |

---

## 9. The Complete Invoice Lifecycle

This section traces a single invoice from email arrival to payment confirmation, showing the exact sequence of planning engine decisions and execution engine actions at each step. This is the canonical reference for how a standard invoice moves through the system.

### 9.1 Standard Invoice — Happy Path

1. Email arrives in ap@ inbox. Gmail Pub/Sub notification fires.
2. Listener fetches email content. Enqueues `email_received` event.
3. Planning engine receives event. Thread is not watched — this is a new thread.
4. **Plan step 1:** `read_email(message_id)`. Execution engine writes pre-execution timeline entry. Fetches full email content and attachment.
5. **Plan step 2:** `classify_email(email_content)`. Claude returns `{type: 'invoice', confidence: 0.94}`. Confidence passes.
6. **Plan step 3:** `apply_label(thread_id, 'Clearledgr/Invoice/Received')`.
7. **Plan step 4:** `create_box('ap_invoices', {thread_id, mailbox, received_at})`. Box ID assigned.
8. **Plan step 5:** `check_domain_match(sender_email, vendor_id)`. Vendor looked up by domain. Domain matches — vendor is known and Active.
9. **Plan step 6:** `check_duplicate(vendor_id, null, null, 90)`. No reference extracted yet — duplicate check is partial at this stage. Full check runs after extraction.
10. **Plan step 7:** `extract_invoice_fields(email_content, attachments)`. Claude returns structured JSON with all fields.
11. **Plan step 8:** `run_extraction_guardrails(fields, email_content)`. All five guardrails pass.
12. **Plan step 9:** `check_duplicate(vendor_id, reference, amount, 90)`. No duplicate found.
13. **Plan step 10:** `check_amount_ceiling(vendor_id, amount, currency)`. Within ceiling.
14. **Plan step 11:** `check_velocity(vendor_id, 7)`. Within threshold.
15. **Plan step 12:** `update_box_fields(box_id, extracted_fields)`.
16. **Plan step 13:** `lookup_po(po_reference, erp)`. PO found. Status: open.
17. **Plan step 14:** `lookup_grn(po_reference, erp)`. GRN found and confirmed. Total received amount matches invoice within tolerance.
18. **Plan step 15:** `run_three_way_match(invoice, po, grn)`. Result: passed. Match score: 0.997.
19. **Plan step 16:** `apply_label(thread_id, 'Clearledgr/Invoice/Matched')`.
20. **Plan step 17:** `move_box_stage(box_id, 'awaiting_approval')`.
21. **Plan step 18:** `send_slack_approval(box_id, ap_manager, invoice_summary)`. Structured message with match result and Approve/Reject buttons sent to AP Manager DM.
22. **Plan step 19:** `set_waiting_condition(box_id, {type: 'approval_response', timeout: 4h})`. Approval timeout timer scheduled.
23. Agent exits. Box is in `awaiting_approval` stage with waiting condition set.

**Post-approval flow:**

1. AP Manager clicks Approve in Slack. Slack callback fires. `approval_received` event enqueued.
2. Planning engine receives `approval_received` event. Box is in `awaiting_approval`. Decision: approved.
3. **Plan step 1:** `clear_waiting_condition(box_id)`.
4. **Plan step 2:** `pre_post_validate(box_id, erp)`. PO still open. GRN still unmatched. No duplicate bill. Vendor active. All clear.
5. **Plan step 3:** `post_bill(invoice_data, erp)`. ERP returns confirmation with bill ID.
6. **Plan step 4:** `move_box_stage(box_id, 'approved')`. `apply_label(thread_id, 'Clearledgr/Invoice/Approved')`.
7. **Plan step 5:** `schedule_payment(vendor, amount, due_date, currency, erp)`. Payment scheduled in ERP.
8. **Plan step 6:** `post_timeline_entry` — DID: "Posted INV-2841 to NetSuite and scheduled SEPA payment of £12,400 due 14 April." WHY: "Three-way match passed within 0.3% tolerance. AP Manager approved 09:41." NEXT: "Payment executes on 14 April. Override window open until 09:56."
9. **Plan step 7:** `send_slack_override_window(box_id, 'payment_scheduled', close_time)`. AP Manager sees confirmation with live Undo button.
10. **Plan step 8:** `watch_thread(thread_id, box_id)`. Further replies from vendor are routed directly to this Box.
11. Agent exits. Box is in `approved` stage. Override window is active.

**Payment confirmation:**

1. ERP payment run executes. Payment confirmation arrives as `payment_confirmed` event.
2. Plan: `move_box_stage(box_id, 'paid')`. `apply_label(thread_id, 'Clearledgr/Invoice/Paid')`. `post_timeline_entry('Payment of £12,400 settled 14 April. Reference PAY-8821.')`. Thread archived per configured period.

### 9.2 Invoice with GRN Wait

Same as the happy path through step 13. At step 14: `lookup_grn` returns no confirmed GRN.

- Planning engine: `set_waiting_condition(box_id, {type: 'grn_check', po_reference, check_interval: 4h})`.
- Job scheduler enqueues `timer_fired` event in 4 hours.
- Agent exits. Box is in `matching` stage with waiting condition.
- 4 hours later: `timer_fired` event. Planning engine: `lookup_grn` again. If still pending and invoice due within 48 hours: escalate to AP Manager. Otherwise: re-schedule for 4 hours. Maximum 10 retries (40 hours) before mandatory escalation.
- On GRN confirmation: `clear_waiting_condition`, resume from step 14 with the confirmed GRN data. Normal flow continues.

### 9.3 Invoice with Match Exception

Same as happy path through step 15. At step 15: `run_three_way_match` returns failed.

- Planning engine: `generate_exception_reason(match_result, invoice, po, grn)`. Claude returns plain-language explanation.
- `apply_label(thread_id, 'Clearledgr/Invoice/Exception')`.
- `move_box_stage(box_id, 'exception')`.
- `send_slack_exception(box_id, ap_channel, {exception_summary, resolution_buttons})`.
- AP Manager selects resolution in Slack: [Override and approve] / [Request credit note] / [Reject invoice].
- `approval_received` event with decision and `override_reason`. Normal approval flow resumes (if override) or invoice is closed (if reject).

---

## 10. The Vendor Onboarding Lifecycle

The vendor onboarding pipeline follows a parallel event-driven architecture to the invoice pipeline. The same planning engine handles onboarding events. The same execution engine runs onboarding actions. The same state model tracks onboarding Box state.

### 10.1 The Four Stages and Their Agent Actions

| Stage | What the agent does autonomously — and where it stops |
|-------|-------------------------------------------------------|
| **Invited** | AP Manager initiates onboarding from the Vendor Onboarding pipeline. Agent: `create_vendor_record(vendor_data)`. `enrich_vendor(vendor_id)` — fetches Companies House and VAT register data, pre-populates the record. `run_adverse_media_check(vendor_id)` — flags if any director names return adverse results. `send_vendor_email(vendor_id, 'onboarding_invitation', {portal_link, required_documents})`. `watch_thread` for vendor replies. Agent stops. Ball is in the vendor's court. |
| **KYC** | Vendor submits documents via the onboarding portal. `kyc_document_received` event fires for each submission. Agent: `classify_vendor_response` — determine which document was submitted and whether it is readable. Validate document against the requirements checklist: certificate of incorporation, proof of address, VAT registration, director identification. If all required documents received and validated: move to `bank_verify` stage. If document is unreadable or wrong document type: `send_vendor_email` with specific correction request. If vendor has not responded in 48 hours: schedule `vendor_chase` timer. |
| **Bank Verify** | All KYC documents received. Agent: `initiate_micro_deposit(vendor_id, iban)`. Post instruction to vendor: "A small deposit has been made to your account. Please confirm the exact amount to verify your bank details." Monitor for vendor reply with amount claimed. `verify_micro_deposit(vendor_id, amount_claimed)`. If verified: move to `active` stage. If incorrect (up to 3 attempts): request reconfirmation. If 3 failed attempts: freeze and alert AP Manager. |
| **Active** | KYC complete, IBAN verified. Agent: `activate_vendor_in_erp(vendor_id, erp)`. Post confirmation to AP Manager in Slack: "Vendor [Name] is now active. Bank details verified. Ready to process invoices." Move Box to Active stage. Future invoices from this vendor bypass the unknown-vendor check. |

---

## 11. Performance Requirements

The thesis commits to specific SLAs. This section translates those SLAs into engineering requirements for each component of the agent pipeline.

| Stage | Starter SLA | Enterprise SLA |
|-------|-------------|----------------|
| Email receipt to event queue | < 30 seconds | < 30 seconds |
| Event queue to planning engine start | < 60 seconds | < 15 seconds |
| Classification (LLM call) | < 5 seconds | < 3 seconds |
| Extraction (LLM call) | < 10 seconds | < 6 seconds |
| Extraction guardrails (deterministic) | < 500ms | < 500ms |
| ERP lookup (per call) | < 3 seconds | < 2 seconds |
| 3-way match (deterministic) | < 100ms | < 100ms |
| ERP post | < 5 seconds | < 5 seconds |
| Slack message delivery | < 3 seconds | < 3 seconds |
| **Total: receipt to Slack approval request** | **< 5 minutes (target)** | **< 2 minutes (target)** |

These SLAs assume: no GRN wait (invoice immediately matchable), no extraction guardrail failures, ERP API responding within stated limits. GRN waits are outside the SLA clock — the clock stops when the agent sets a waiting condition and restarts when the condition is cleared.

### 11.1 Rate Limit Management

Every external API has rate limits that must be managed at the connector level, not the planning level. Rate limit management is the ERP Connector Layer's responsibility.

| API | Rate limit approach |
|-----|---------------------|
| **NetSuite REST API** | OAuth token per workspace. Rate limits are per-token. At high volume (>1000 invoices/month), implement a request queue per workspace that batches lookups and introduces delays when approaching the limit. Burst capacity is absorbed by the queue. |
| **SAP Business One Service Layer** | Per-session rate limits. The connector maintains a session pool (max 3 concurrent sessions per workspace) and queues requests across the pool. Session refresh is handled transparently. |
| **Xero API** | Hard limit of 60 requests per minute per Xero organisation. The connector implements a token bucket rate limiter per workspace. Requests that would exceed the limit are queued and execute when the bucket refills. |
| **QuickBooks Online API** | Similar to Xero — per-app, per-realm rate limits. Token bucket implementation per workspace. |
| **Gmail API** | Per-user quotas. The connector respects Gmail's sendEmail quota (250 per day for Google Workspace users) and API call quotas. High-volume operations (bulk label application during migration) are spread over time. |
| **Claude API** | Per-organisation tier limits. All Claude calls go through the LLM Gateway which tracks token usage and enforces a per-workspace monthly budget. Workspaces approaching their budget trigger a Backoffice alert. The LLM Gateway queues requests to stay within rate limits rather than failing them. |

### 11.2 Concurrency Model

The planning and execution engines are stateless — they read all state from PostgreSQL and Redis on every invocation and write results back before exiting. This is the prerequisite for horizontal scaling. Any worker can handle any workspace's events. No worker holds in-memory state between events.

The gap this section closes: a finance team receiving 50 invoices on the first of the month creates 50 simultaneous events. Without a defined concurrency model, those 50 events process sequentially on a single worker, violating the Enterprise 2-minute SLA. With the model defined here, they process in parallel across a worker fleet — bounded by workspace concurrency limits and ERP rate limits — completing the full batch well within SLA.

#### 11.2.1 Worker Pool Architecture

Clearledgr runs a fleet of Celery workers, each capable of handling any event for any workspace. Workers are identical and interchangeable. The number of workers is the primary scaling lever — doubling workers doubles throughput. Workers are deployed as a Kubernetes Deployment with horizontal pod autoscaling based on Redis Stream queue depth.

| Component | Role and configuration |
|-----------|----------------------|
| **Celery workers** | Stateless Python processes, each running instances of `AgentPlanningEngine` and `FinanceAgentRuntime`. Workers pull events from Redis Streams via consumer groups. Each worker handles one event at a time per process — concurrency within a worker is via asyncio, not threads. |
| **Redis Streams (event queue)** | Two streams: `high_priority` (Enterprise tier) and `standard` (Professional, Starter). Workers poll `high_priority` first, fall back to `standard` if empty. This ensures Enterprise SLAs are not impacted by high-volume Starter workspaces. |
| **Consumer groups** | Redis consumer group per stream, group name `clearledgr-workers`. Redis ensures each event is delivered to exactly one worker. If a worker dies mid-processing, the event is reclaimed after a 60-second visibility timeout and redelivered. |
| **Workspace concurrency semaphore** | A Redis-based counting semaphore per workspace. Before processing any event, a worker acquires a slot from the workspace's semaphore. If the workspace is at its concurrency limit, the worker nacks the event and requeues it with a 5-second backoff. The semaphore is released when the event finishes processing — success or failure. |
| **Celery Beat (scheduler)** | A single Celery Beat process manages all `timer_fired` events: GRN checks, approval timeouts, vendor chases, override window closings. Beat enqueues timer events into the appropriate Redis Stream at the scheduled time. Beat does not process events itself. |
| **Autoscaler** | Kubernetes HPA monitors Redis Stream pending message count. Scale-out threshold: > 20 pending messages on `high_priority` stream, or > 100 on `standard` stream. Scale-in threshold: < 5 messages sustained for 3 minutes. Min workers: 2 (always-on). Max workers: 50 (hard limit to control ERP API consumption). |

#### 11.2.2 Workspace Concurrency Limits

Per-workspace concurrency limits prevent a single high-volume workspace from consuming the entire worker fleet, starving other workspaces. Limits are enforced at the semaphore level — a workspace that hits its limit queues additional events rather than blocking workers.

| Tier | Max concurrent boxes | Rationale |
|------|---------------------|-----------|
| **Starter** | 5 concurrent boxes | Matches Xero/QuickBooks 60 RPM rate limit. 5 concurrent boxes each making ~10 ERP calls = 50 RPM — safely within limits with headroom. |
| **Professional** | 15 concurrent boxes | Balanced throughput for mid-market volume. Matches typical ERP API tier limits for Professional customers (NetSuite, QuickBooks Advanced). |
| **Enterprise** | 50 concurrent boxes | Full throughput for high-volume Enterprise. ERP rate limits at this tier are typically negotiated per-customer. The ERP proxy's rate limiter handles the actual throttling. |

Concurrency limits are enforced per workspace, not per tier globally. Two Starter workspaces each get 5 concurrent slots — they do not share a single pool of 5.

#### 11.2.3 The 50-Invoice Scenario

A Professional customer's AP team sends 50 invoices on the first of the month, all arriving within a 2-minute window. Here is exactly what happens.

| Step | What happens |
|------|-------------|
| **1. Events enter the queue** | 50 Gmail Pub/Sub notifications arrive. The listener fetches each message and writes 50 `email_received` events to the standard Redis Stream. All 50 are written within seconds. No processing has started yet. |
| **2. Workers claim events** | Available workers pull events from the stream via the consumer group. Each worker claims one event and attempts to acquire a slot from this workspace's concurrency semaphore (limit: 15). |
| **3. First 15 process immediately** | The first 15 workers acquire semaphore slots and begin processing. Each runs the full planning + execution cycle: classify, extract, fraud check, ERP lookup, match, route for approval. |
| **4. Remaining 35 queue** | Workers attempting to claim events 16-50 find the semaphore full. They nack their event back to the stream with a 5-second requeue delay. The events return to the stream and will be claimed again when slots free up. |
| **5. Slots free as boxes complete** | As each of the first 15 invoices completes its synchronous processing steps (or sets a waiting condition and exits), the semaphore slot is released. The next event from the queue immediately claims the freed slot. |
| **6. Rolling throughput** | With an average processing time of 45 seconds per invoice (classification + extraction + ERP lookups + match), the workspace sustains ~20 invoices per minute. All 50 invoices complete initial processing within approximately 3 minutes. |
| **7. Approval requests in parallel** | All 50 approval requests are sent to Slack simultaneously — there is no queuing at the Slack layer. AP Manager sees all 50 in the digest or inbox, can batch-approve. |
| **8. ERP post throttled per rate limit** | When approvals arrive, the ERP proxy rate limiter ensures posts stay within the ERP's API limits (e.g. 60 RPM for Xero). Posts are queued and spaced within the rate limiter, not dropped. |

#### 11.2.4 Queue Depth and Back-Pressure

The system monitors queue depth continuously. Sustained high queue depth indicates either insufficient workers or an ERP connectivity issue causing processing delays. Back-pressure is handled at two levels.

| Condition | Response |
|-----------|----------|
| **High queue depth** (>100 standard, >20 high-priority) | Kubernetes autoscaler adds workers. Backoffice dashboard shows queue depth spike. If depth does not reduce within 5 minutes of scaling, alert is raised — likely an ERP connectivity issue rather than a capacity issue. |
| **ERP unavailable** (all events failing at ERP call step) | The execution engine sets `waiting_condition: erp_unavailable` on each Box. Events are not requeued for immediate retry — they wait for the ERP connectivity check timer. Queue depth drops as events complete the synchronous steps (classification, extraction) and park at the ERP step. |
| **Workspace at concurrency limit for > 5 minutes** | Alert raised in Backoffice. May indicate the concurrency limit is too low for this workspace's volume, or that long-running boxes are not releasing their semaphore slots (a bug condition). |
| **Worker memory pressure** | Each worker process handles one event at a time with asyncio. Memory consumption per worker is bounded by the LLM gateway's token budget limits (maximum ~4,000 input tokens per Claude call). Workers are restarted by Kubernetes if memory exceeds 512MB — the event is redelivered to another worker after the visibility timeout. |

#### 11.2.5 Preventing Race Conditions on Box State

When multiple workers process events for the same workspace simultaneously, they may attempt to read and write the same Box record. Three mechanisms prevent corruption.

| Mechanism | How it works |
|-----------|-------------|
| **UNIQUE constraint on (workspace_id, thread_id, pipeline)** | The boxes table has a unique constraint on this combination. If two workers simultaneously receive events for the same Gmail thread (possible if Gmail delivers a duplicate notification), only one can create the Box. The second gets a unique constraint violation and treats the thread as an existing Box, routing to the continuation handler. |
| **Optimistic locking on Box updates** | Box state updates include an `updated_at` check: `UPDATE boxes SET ... WHERE box_id = $1 AND updated_at = $2`. If another worker updated the Box between the read and the write, the update returns 0 rows. The worker re-reads the Box state and re-evaluates before retrying. This prevents the classic lost-update problem. |
| **Workspace semaphore as the primary guard** | The concurrency semaphore is the strongest protection. If a workspace's limit is set appropriately for its ERP rate limits, the probability of two workers racing on the same Box is low — there are far more Boxes than workers processing them simultaneously. |

One edge case requires explicit handling: a Box that is in a `waiting_condition` state receives a new event (e.g. the vendor sends a reply email while the Box is waiting for a GRN). The event handler must check the waiting condition before processing the new event — it should not start a new plan for a Box that is already mid-execution. The planning engine's `_plan_thread_continuation` handler does this check by reading the current Box stage and `waiting_condition` before building any plan.

#### 11.2.6 SLA Impact of the Concurrency Model

| Metric | Single-worker (before) | Worker fleet (after) |
|--------|----------------------|---------------------|
| Time to first approval request | ~40 min (50 x 48s sequential) | ~3 min (15 concurrent, rolling) |
| Time to last approval request | ~40 min | ~4 min |
| Enterprise SLA compliance (2 min target per invoice) | Violated after invoice 3 | Met for >90% of invoices |
| ERP API calls per minute (Xero, 60 RPM limit) | ~8 RPM (sequential, safe) | ~45 RPM (15 concurrent x 3 calls, within limit) |
| Worker count needed for this scenario | 1 | 3-5 (autoscaler manages) |

---

## 12. Recovery and Resumption

The agent is designed to be interrupted at any point and resume correctly. This section defines exactly what "resume correctly" means for every failure scenario.

### 12.1 Agent Process Restart

If the agent process restarts while executing a plan (server crash, deployment, OOM kill), on restart it:

1. Finds all Boxes with status: `pending_plan` in the Box state store.
2. For each pending Box: reads the serialised plan and the last timeline entry.
3. The last timeline entry shows the last action that was recorded as "executing". The execution engine checks whether that action completed (by querying the ERP or Gmail API for evidence of the action) or not.
4. If completed: marks it done in the plan, continues from the next action.
5. If not completed: re-executes the action (all actions are idempotent — applying a label that already exists is a no-op, creating a bill that already has an ERP ID is caught by `pre_post_validate`).
6. All actions are designed to be idempotent. The worst case of a restart is a duplicate API call that returns the same result. It is never a duplicate ERP post, because `pre_post_validate` catches existing bills.

### 12.2 ERP Connectivity Loss

If the ERP connector returns a connectivity error during execution:

1. The execution engine pauses the plan: `set_waiting_condition(box_id, {type: 'erp_unavailable'})`.
2. Schedules a `timer_fired` check in 15 minutes.
3. Alerts the Backoffice ERP connector health dashboard.
4. If the ERP has been unavailable for more than 30 minutes: alerts the CS team to contact the customer.
5. When connectivity is restored: `clear_waiting_condition`, resume the plan from the paused action.
6. No invoice is lost during an ERP outage. Invoices that arrive during the outage are classified and extracted. The matching and posting steps wait for ERP restoration.

### 12.3 Idempotency Requirements

Every action in the action space must be idempotent. Idempotency is verified by the test suite — each action is called twice in sequence and the result must be identical. Specific implementations:

- **`apply_label`:** Gmail API apply is idempotent natively.
- **`create_box`:** use upsert on `(thread_id, pipeline)` — same thread can only have one Box per pipeline.
- **`post_bill`:** `pre_post_validate` checks for an existing bill with the same reference before posting. If found: return the existing bill ID, do not post again.
- **`send_slack_approval`:** store the message timestamp after sending. If a message timestamp already exists for this Box's current approval cycle: update the existing message rather than sending a new one.
- **`initiate_micro_deposit`:** idempotent by `vendor_id` + IBAN token — same deposit is not initiated twice within a 7-day window.

---

## 13. The Agent's Relationship to the Surfaces

The surfaces described in the Product Design Thesis are the agent's communication channels — not its architecture. The agent never directly manipulates the InboxSDK injection layer. It manipulates data and APIs; the InboxSDK layer reads that data and presents it. The relationship is strictly one-directional: agent writes state, surfaces read state.

| Surface | How it reads agent state — and what the agent writes to make it work |
|---------|----------------------------------------------------------------------|
| **Gmail inbox stage labels (InboxSDK Lists)** | The extension reads the Clearledgr Gmail labels applied by `apply_label`. The label presence is the data. InboxSDK renders the visual stage badge from the label. The agent does not know InboxSDK exists. |
| **Thread sidebar (InboxSDK Conversations)** | The sidebar reads the Box record and Box timeline for the current thread from the Clearledgr backend API. The agent writes to the Box record and timeline. The sidebar presents whatever it finds there. |
| **Thread toolbar buttons (InboxSDK Toolbars)** | The toolbar reads the Box stage and match result from the backend API to decide which buttons to show. The agent writes the stage and match result. The toolbar is driven by that data. |
| **Pipeline Kanban (InboxSDK Router)** | The Kanban reads all Boxes in the pipeline with their current stage from the backend API. The agent writes Box stages. The Kanban reflects whatever stages exist. |
| **Clearledgr Home (InboxSDK Router)** | Home reads: exception count, awaiting approval count, due this week count, last 10 agent actions, vendor onboarding blockers — all from structured queries on the Box state store. The agent writes to the state store. Home reads from it. |
| **Slack / Teams messages** | The agent writes to Slack and Teams directly via the communication actions. These are outbound writes, not reads. Slack button callbacks arrive as events and re-enter the event queue. |
| **Google Workspace Add-on** | The Add-on reads Box stage and match result from the backend API — same API as the sidebar. Approve/Reject button taps create `approval_received` events via the backend webhook. |

The agent is backend infrastructure. The surfaces are frontend presentations of backend state. They are designed independently and tested independently. A bug in InboxSDK rendering does not affect the agent's ability to process invoices. An agent failure does not crash the Gmail extension. Decoupling is intentional.

---

> This document is the engineering reference for the Clearledgr agent. Section owners: §1-§5 CTO, §6 Backend Lead, §7 ML Lead, §8 Backend Lead, §9-§10 Product Engineering Lead, §11-§12 Infrastructure Lead, §13 Full-Stack Lead. Review quarterly or on any significant architectural change. Changes to the LLM boundary (§7) require CTO sign-off. Changes to the action space (§3) require both CTO and product sign-off.
