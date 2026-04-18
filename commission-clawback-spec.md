**CLEARLEDGR**

**Commission Clawback Agent**

**Design Specification**

How the commission clawback agent detects refunds, calculates clawback
amounts, drafts reversal journal entries, and posts to ERP · Internal
engineering reference

> *Confidential --- Clearledgr Ltd · Engineering team only*

**1. Overview**

Commission Clawback is the second workflow class the Clearledgr coordination layer handles, extending the agent system beyond AP into a non-accounts-payable workflow. It is the first real test of the platform thesis: that the Box model, agent architecture, rules-decide-LLM-describes discipline, and ERP write layer all generalise to workflows the product wasn't originally designed for. It is relevant to any business that pays commissions to partners, agents, or intermediaries and needs to reclaim them when a transaction is cancelled or refunded. Travel, hospitality, marketplaces, insurance, SaaS with reseller channels, and financial services all face this problem. The current process is always manual --- finance teams detect the refund event, look up the original booking or transaction, calculate the clawback amount, and post a reversal journal entry to the ERP.

Clawback is structurally the same problem AP solves, running in reverse: where AP drives the cash *out* against a matched obligation, clawback drives the cash *back in* against a matched reversal. The same Box model applies: an event (the refund) kicks off a workflow instance, the agent reconstructs the commercial relationship (the original commission payment), deterministic calculation produces the reversal, a human approves on exceptions, the agent posts to the ERP, and the Box carries the full attributable history. The fact that Clearledgr's architecture generalises from AP to clawback without modification is the direct validation of the coordination-layer thesis: each new workflow is a new Box type running on the same platform, not a separate product.

The agent automates this end-to-end. It sits inside Gmail and Slack, reads refund and cancellation events, looks up the original booking record and commission paid, calculates the clawback amount, drafts the reversal journal entry, routes for approval, and posts to the ERP. No new tool. No migration. The finance team stays in their inbox.

This spec extends the core Clearledgr Agent Design Specification. All architectural components --- the event system, planning engine, coordination engine, state management, LLM/deterministic boundary, and error handling --- are inherited without modification. This document defines only what is new: the commission clawback event types, the extended action space, the new pipeline, the planning logic specific to clawback, and the complete lifecycle.

> *The fundamental design principle is unchanged: rules decide, Claude
> describes. The clawback calculation is always deterministic. Claude is
> called only to classify refund events and generate human-readable
> summaries. The reversal journal entry is constructed by rule, not by
> language model.*

**1.1 v1 Scope Boundary**

The v1 pipeline assumes a simple relationship: one booking record
corresponds to one commission record. The following cases are
explicitly out of scope for v1 and route to the AP Manager for manual
handling:

-   **Multi-leg bookings** where only a subset of legs is refunded
    (packages, multi-room, multi-night with partial date changes).
    The correct clawback denominator is the commission attributable
    to the refunded leg, not the whole booking; this requires leg-
    level commission attribution which v1 does not implement.

-   **Incremental commissions on post-booking adjustments** where a
    booking has been upgraded or modified and received additional
    commission after the original payment. v1 reverses a single
    commission record; bookings with multiple commission records
    surface as duplicates and escalate.

-   **Partner netting** where the partner is both a commission
    recipient and an AP vendor with open invoices. v1 posts the
    clawback to partner payables as a standalone reversal; it does
    not net against open AP invoices to the same partner. Netting is
    left to the customer\'s AP team downstream of the posted entry.

These are v2 candidates. They are flagged in the Booking.com pilot
plan so the pilot team knows which cases drop to manual review on day
one, not in week two of production.

**1.1.1 Detection rules for out-of-scope cases**

The agent must recognise these cases and route to manual rather than
silently attempting a wrong calculation. Detection happens during the
lookup stage, immediately after lookup_original_booking and
lookup_commission_record return:

-   **Multi-leg detection.** If the booking record returns more than
    one line item with its own commissionable amount, or if the refund
    amount matches a single line item rather than the booking total,
    the Box is flagged as multi-leg. apply_label(\'Review Required\'),
    send_slack_exception with the booking structure summary, and stop.
    Do not attempt pro-rata calculation.

-   **Multiple commission records per booking.** If
    lookup_commission_record returns more than one commission record
    for a single booking reference (indicating incremental commissions
    from post-booking adjustments), the Box is flagged as incremental-
    commission. apply_label(\'Review Required\'), send_slack_exception
    with all commission records listed, and stop.

-   **Partner netting flag.** If the partner_id on the commission
    record also appears as an active vendor in the AP master with
    open invoices, the Box posts a timeline note flagging the netting
    opportunity but proceeds with the standard reversal. Netting is
    handled downstream by the AP team, not by the clawback agent.

**2. The Commission Clawback Pipeline**

The commission clawback pipeline runs alongside the existing AP Invoices
and Vendor Onboarding pipelines. It has five stages. A Box enters the
pipeline when a refund or cancellation event is detected and exits when
the reversal journal entry has been posted to the ERP and the partner
has been notified.

**2.1 Stages**

  -----------------------------------------------------------------------
  **Stage**                **Description**
  ------------------------ ----------------------------------------------
  **detected**             A refund or cancellation event has been
                           identified. The original booking reference and
                           refund amount have been extracted. The agent
                           has not yet looked up the commission record.

  **lookup**               The agent is querying the ERP for the original
                           booking record, the commission amount paid,
                           and the applicable commission rate. May be
                           waiting for ERP data.

  **calculated**           The clawback amount has been calculated
                           deterministically. A draft reversal journal
                           entry has been constructed. The Box is ready
                           for approval routing.

  **awaiting_approval**    The draft reversal entry has been sent to the
                           AP Manager or Controller for review. The agent
                           is waiting for an approval decision.

  **posted**               The reversal journal entry has been posted to
                           the ERP. The clawback is recorded. The partner
                           has been notified.

  **disputed**             The partner has responded to the clawback
                           notification with a dispute. Human review
                           required before any further action.

  **closed**               Terminal stage. Either fully resolved (posted
                           and partner notified without dispute) or
                           manually closed by AP Manager.
  -----------------------------------------------------------------------

**2.2 Stage Transition Rules**

  -----------------------------------------------------------------------
  **Transition**           **Condition**
  ------------------------ ----------------------------------------------
  **detected → lookup**    Event classified as refund/cancellation with
                           confidence ≥ 0.85. Booking reference
                           extracted. Refund amount extracted and passes
                           guardrails.

  **detected → closed**    Event classified as irrelevant or
                           unclassifiable. No booking reference found. AP
                           Manager manually closes.

  **lookup → calculated**  Original booking record found. Commission
                           record found. Clawback calculation complete.
                           All validation rules pass.

  **lookup → disputed**    Commission record found but already reversed
                           (duplicate clawback attempt detected).
                           Surfaces to AP Manager.

  **calculated →           Draft reversal journal entry constructed and
  awaiting_approval**      validated. Approval routing decision made by
                           planning engine.

  **calculated → posted**  Clawback amount below auto-approve threshold
                           and autonomy tier permits. Pre-post validation
                           passes. Agent posts without manual approval.

  **awaiting_approval →    Approval received. Pre-post validation passes.
  posted**                 Reversal journal entry posted to ERP.

  **awaiting_approval →    AP Manager rejects --- identifies the clawback
  disputed**               as incorrect (wrong amount, wrong partner,
                           already settled).

  **posted → disputed**    Partner responds to notification contesting
                           the clawback. Requires human review.

  **posted → closed**      No partner dispute within the configured
                           dispute window (default: 14 days). Box is
                           closed and archived.

  **disputed → closed**    AP Manager resolves the dispute: either
                           reverses the clawback post or confirms it.
                           Resolution logged to timeline.
  -----------------------------------------------------------------------

**3. New Event Types**

The following event types are added to the event system. All other event
types defined in the core spec are unchanged. The planning engine
dispatches on these new types in addition to the existing types.

  --------------------------------------------------------------------------------
  **Event type**                    **Trigger and payload**
  --------------------------------- ----------------------------------------------
  **refund_detected**               A refund or cancellation signal has been
                                    identified, either from an inbound email
                                    (partner notification, internal finance email)
                                    or from an ERP webhook (system-generated
                                    cancellation record). Payload: {source,
                                    message_id?, erp_record_id?,
                                    booking_reference?, refund_amount?, currency,
                                    received_at}.

  **clawback_approval_received**    An AP Manager or Controller has approved or
                                    rejected a draft reversal journal entry via
                                    Slack or the Gmail extension. Payload:
                                    {box_id, decision, actor_email, timestamp,
                                    override_reason?}.

  **partner_dispute_received**      A partner has responded to a clawback
                                    notification email contesting the clawback
                                    amount or basis. Payload: {box_id, message_id,
                                    thread_id, dispute_reason?}.

  **clawback_posted**               The ERP has confirmed that the reversal
                                    journal entry has been posted. Payload:
                                    {box_id, erp_entry_id, posted_at, amount,
                                    currency}.

  **dispute_window_expired**        The configured dispute window has closed
                                    without a partner dispute. The clawback is
                                    considered settled. Payload: {box_id,
                                    closed_at}.

  **erp_commission_record_found**   A scheduled ERP lookup (triggered when the
                                    commission record was not immediately
                                    available) has found the record. Payload:
                                    {box_id, commission_record_id,
                                    commission_amount, commission_rate}.
  --------------------------------------------------------------------------------

**4. Extended Action Space**

The following actions are added to the formal action space. The complete
action space is the union of the actions defined in the core spec and
the actions defined here. The two non-negotiable rules apply to all new
actions: every action is recorded to the Box timeline before it
executes, and the coordination engine never assumes success.

Build status is indicated for each action: NEW means a net-new
component, EXISTING means the action reuses the current architecture
with new parameters, and ADAPTED means the existing implementation
requires modification for the new business logic.

**4.1 Detection and Classification Actions**

  -----------------------------------------------------------------------------------------------------------
  **Action**                                             **Layer**   **Description**
  ------------------------------------------------------ ----------- ----------------------------------------
  **classify_refund_event(email_content, erp_record?)**  **LLM**     Call Claude to classify an incoming
                                                                     signal as one of: full_refund,
                                                                     partial_refund, cancellation_no_charge,
                                                                     cancellation_with_fee,
                                                                     commission_adjustment, irrelevant,
                                                                     unclassifiable. Returns {type,
                                                                     confidence, booking_reference?,
                                                                     refund_amount?, currency?}. Confidence
                                                                     threshold: 0.85. Below threshold:
                                                                     unclassifiable. NEW.

  **extract_refund_fields(email_content, erp_record?)**  **LLM**     Call Claude to extract structured fields
                                                                     from a refund or cancellation signal:
                                                                     booking_reference, cancellation_date,
                                                                     refund_amount, refund_currency,
                                                                     cancellation_reason, partner_id (if
                                                                     present), original_booking_amount.
                                                                     Returns {fields,
                                                                     extraction_confidence_per_field}. Same
                                                                     guardrail framework as
                                                                     extract_invoice_fields. NEW.

  **run_refund_extraction_guardrails(extracted_fields,   **DET**     Apply deterministic guardrails to
  source_content)**                                                  extracted refund fields. Guardrails: (1)
                                                                     refund_amount is a valid positive
                                                                     number, (2) refund_currency is a
                                                                     supported ISO 4217 code, (3)
                                                                     cancellation_date is not in the future,
                                                                     (4) booking_reference format matches ERP
                                                                     booking ID format, (5) refund_amount
                                                                     does not exceed a configurable maximum
                                                                     single-clawback ceiling. Returns
                                                                     {passed, failures}. ADAPTED from invoice
                                                                     guardrails.
  -----------------------------------------------------------------------------------------------------------

**4.2 ERP Lookup Actions**

  ----------------------------------------------------------------------------------------------------
  **Action**                                      **Layer**   **Description**
  ----------------------------------------------- ----------- ----------------------------------------
  **lookup_original_booking(booking_reference,    **DET**     Query the ERP for the original booking
  erp)**                                                      record by reference. Returns {found,
                                                              booking_record?, status}. Status:
                                                              active, cancelled, partially_refunded,
                                                              fully_refunded. A fully_refunded booking
                                                              whose commission has already been
                                                              reversed triggers a duplicate clawback
                                                              flag. EXISTING connector interface, new
                                                              query type.

  **lookup_commission_record(booking_reference,   **DET**     Fetch the commission record associated
  erp)**                                                      with a booking from the ERP. Returns
                                                              {found, commission_record?, amount_paid,
                                                              commission_rate, payment_date,
                                                              currency}. If not found: sets
                                                              waiting_condition and schedules
                                                              erp_commission_record_found check.
                                                              EXISTING connector interface, new query
                                                              type.

  **check_clawback_duplicate(booking_reference,   **DET**     Check whether a reversal journal entry
  erp)**                                                      for this booking reference already
                                                              exists in the ERP or in the Clearledgr
                                                              Box state store (trailing 90 days).
                                                              Returns {duplicate_found,
                                                              existing_entry_id?}. A found duplicate
                                                              blocks processing and surfaces to AP
                                                              Manager. ADAPTED from check_duplicate.

  **lookup_commission_rate_schedule(partner_id,   **DET**     Fetch the applicable commission rate
  booking_date, erp)**                                        schedule for a partner at the time of
                                                              the original booking. Some partners have
                                                              tiered or time-variable rates. Required
                                                              for partial refund calculations where
                                                              the clawback rate differs from a flat
                                                              rate. Returns {rate_schedule,
                                                              applicable_rate, effective_from,
                                                              effective_to}. NEW.
  ----------------------------------------------------------------------------------------------------

**4.3 Calculation Actions**

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **calculate_clawback_amount(commission_record,   **DET**     Deterministic clawback calculation.
  refund_details, rate_schedule)**                             Inputs: commission amount paid, refund
                                                               amount, refund type (full/partial),
                                                               applicable rate schedule, cancellation
                                                               policy (if retrievable from ERP). Logic:
                                                               (1) For full refunds: clawback =
                                                               commission_amount_paid. (2) For partial
                                                               refunds: clawback = (refund_amount /
                                                               original_booking_amount) ×
                                                               commission_amount_paid, adjusted by rate
                                                               schedule if tiered. (3) For cancellation
                                                               with fee: clawback =
                                                               commission_amount_paid -
                                                               (cancellation_fee × commission_rate).
                                                               Returns {clawback_amount,
                                                               clawback_currency, calculation_basis,
                                                               inputs_used}. Never calls Claude. NEW.

  **validate_clawback_rules(clawback_result,       **DET**     Apply business validation rules to the
  commission_record)**                                         calculated clawback. Rules: (1)
                                                               clawback_amount cannot exceed
                                                               commission_amount_paid, (2)
                                                               clawback_amount must be positive, (3)
                                                               clawback_currency at point of calculation
                                                               must match commission_currency (FX
                                                               translation to reversal_currency is
                                                               applied downstream by
                                                               apply_fx_adjustment, not at this stage),
                                                               (4) clawback_amount
                                                               above the configured CFO approval
                                                               ceiling routes to CFO regardless of
                                                               other rules, (5) clawback_amount within
                                                               the configured de minimis threshold is
                                                               written off to a de minimis memo account
                                                               without a reversal entry to partner
                                                               payables. Default threshold is 0 (no
                                                               write-off — every clawback posts a
                                                               reversal). Customers opt in to a
                                                               non-zero threshold explicitly and must
                                                               configure a de minimis memo GL account
                                                               for the aggregate accrual. Written-off
                                                               amounts are captured to the memo account
                                                               and surfaced in a monthly reconciliation
                                                               report so written-off clawbacks never
                                                               leave the ledger unaccounted. Returns
                                                               {valid, failures, disposition:
                                                               \'proceed\' \| \'write_off\' \|
                                                               \'cfo_approval_required\'}. NEW.

  **apply_fx_adjustment(clawback_amount,           **DET**     If the original commission was paid in a
  original_currency, reversal_currency, erp)**                 different currency than the reversal
                                                               currency, fetch the ERP's configured
                                                               exchange rate and apply it. Returns
                                                               {adjusted_amount, adjusted_currency,
                                                               rate_used, rate_date}. Uses the ERP's
                                                               own rate, not an external source, to
                                                               ensure the reversal matches the ERP's
                                                               ledger. EXISTING connector interface,
                                                               new call. ADAPTED.
  -----------------------------------------------------------------------------------------------------

**4.4 Journal Entry Actions**

  ------------------------------------------------------------------------------------------------------
  **Action**                                        **Layer**   **Description**
  ------------------------------------------------- ----------- ----------------------------------------
  **draft_reversal_journal_entry(clawback_result,   **DET**     Construct the reversal journal entry
  booking_record, commission_record, erp_schema)**              from the calculated clawback. The entry
                                                                structure is determined by the ERP
                                                                schema for the workspace (SAP, NetSuite,
                                                                Xero, QuickBooks each have different
                                                                journal entry formats). Standard
                                                                structure: debit the commission
                                                                income/liability account for
                                                                clawback_amount, credit the partner
                                                                payable or suspense account. Returns
                                                                {journal_entry_draft, debit_lines,
                                                                credit_lines, narrative, erp_format}.
                                                                Narrative is constructed from structured
                                                                data --- no LLM call. NEW.

  **validate_journal_entry(journal_entry_draft,     **DET**     Validate the draft journal entry before
  erp)**                                                        presenting for approval: (1) debits
                                                                equal credits, (2) GL account codes
                                                                exist and are active in the ERP, (3)
                                                                cost centre codes are valid if required
                                                                by the ERP, (4) period is open for
                                                                posting, (5) entry does not violate any
                                                                ERP-level posting rules. Returns {valid,
                                                                failures}. Must pass before the entry is
                                                                sent for approval. NEW.

  **post_reversal_entry(journal_entry, erp)**       **DET**     Post the approved reversal journal entry
                                                                to the ERP. Requires
                                                                validate_journal_entry to have passed
                                                                and approval to have been received.
                                                                Returns {success, erp_entry_id?,
                                                                error?}. On success: stores the ERP
                                                                entry ID in the Box record. On failure:
                                                                does not retry automatically --- marks
                                                                Box as exception and alerts AP Manager
                                                                with specific ERP error. ADAPTED from
                                                                post_bill.

  **pre_post_validate_clawback(box_id, erp)**       **DET**     Re-validate before posting: commission
                                                                record still present and not already
                                                                reversed, approval still valid and
                                                                within approval window, GL period still
                                                                open, ERP entry does not already exist
                                                                for this booking reference. Returns
                                                                {valid, failures}. Must pass immediately
                                                                before post_reversal_entry. ADAPTED from
                                                                pre_post_validate.

  **generate_clawback_summary(clawback_result,      **LLM**     Generate a plain-language summary of the
  booking_record, journal_entry)**                              clawback for the AP Manager's approval
                                                                message and the partner notification.
                                                                Input is fully structured --- Claude
                                                                generates human-readable text, not the
                                                                calculation or entry. DID-WHY-NEXT
                                                                format. Maximum 200 words. If this call
                                                                fails, a template message is used.
                                                                ADAPTED from generate_exception_reason.
  ------------------------------------------------------------------------------------------------------

**4.5 Communication Actions**

  -------------------------------------------------------------------------------------------------------
  **Action**                                         **Layer**   **Description**
  -------------------------------------------------- ----------- ----------------------------------------
  **send_slack_clawback_approval(box_id, approver,   **DET**     Send a structured interactive approval
  clawback_summary, journal_entry_preview)**                     message to the configured approver's
                                                                 Slack DM. Includes: booking reference,
                                                                 refund type, original commission amount,
                                                                 calculated clawback amount, currency,
                                                                 calculation basis, and a preview of the
                                                                 journal entry debit/credit lines.
                                                                 Approve / Reject / Request clarification
                                                                 buttons. Constructed deterministically
                                                                 from Box state. ADAPTED from
                                                                 send_slack_approval.

  **send_partner_clawback_notification(partner_id,   **DET**     Send a templated email to the partner
  clawback_details, template)**                                  notifying them of the commission
                                                                 clawback. Template includes: booking
                                                                 reference, refund date, original
                                                                 commission amount, clawback amount and
                                                                 basis, dispute window and process. Sent
                                                                 from the AP inbox. Thread is watched for
                                                                 dispute responses. Thread matching is
                                                                 idempotent: incoming replies are matched
                                                                 to a box_id by thread_id + partner-email
                                                                 allowlist (partner primary contact,
                                                                 known partner-ops and partner-finance
                                                                 aliases, plus any new sender on the
                                                                 thread flagged for AP Manager review).
                                                                 Replies from senders outside the
                                                                 allowlist surface as exceptions rather
                                                                 than auto-classifying as disputes.
                                                                 ADAPTED from send_vendor_email.

  **send_slack_clawback_posted(box_id, channel,      **DET**     Post a confirmation to the AP channel
  posting_summary)**                                             when a clawback entry has been posted.
                                                                 Includes ERP entry ID, amount, partner,
                                                                 and override window if applicable.
                                                                 ADAPTED from send_slack_override_window.

  **send_slack_dispute_alert(box_id, channel,        **DET**     Alert the AP Manager when a partner
  dispute_summary)**                                             dispute is received. Includes the
                                                                 dispute reason (if extracted), the
                                                                 original clawback details, and
                                                                 resolution options. ADAPTED from
                                                                 send_slack_exception.

  **draft_dispute_response(dispute_email,            **LLM**     For complex partner disputes requiring a
  clawback_details, resolution)**                                contextual reply, call Claude to draft a
                                                                 response. Always staged for AP Manager
                                                                 review before sending. Never sent
                                                                 autonomously. ADAPTED from
                                                                 draft_vendor_response.
  -------------------------------------------------------------------------------------------------------

**5. Planning Engine --- Commission Clawback**

The planning engine handles commission clawback events using the same
dispatch architecture as the core spec. New event types map to new
planning handlers. The planning logic for existing event types
(approval_received, timer_fired) is extended with clawback-specific
branches without modifying the existing handlers.

**5.1 Planning for refund_detected**

  -----------------------------------------------------------------------
  **Step**                 **Planning logic**
  ------------------------ ----------------------------------------------
  **1. Classify the        classify_refund_event(source_content). If
  signal**                 confidence \< 0.85 or type is irrelevant:
                           apply_label(\'Review Required\'),
                           create_box(\'commission_clawback\', {stage:
                           \'detected\', needs_review: true}),
                           send_gmail_notification. Stop.

  **2. Extract fields**    extract_refund_fields(source_content).
                           run_refund_extraction_guardrails. If any
                           guardrail fails: apply_label(\'Review
                           Required\'), post_timeline_entry with specific
                           failures, send_slack_exception. Stop.

  **3. Duplicate check**   check_clawback_duplicate(booking_reference,
                           erp). If duplicate found: apply_label(\'Review
                           Required\'), alert AP Manager with both entry
                           references. Stop.

  **4. Lookup original     lookup_original_booking(booking_reference,
  booking**                erp). If not found: flag as \'Unknown booking
                           reference\', apply_label(\'Review Required\'),
                           send_slack_exception. Stop. If found:
                           continue.

  **5. Lookup commission   lookup_commission_record(booking_reference,
  record**                 erp). If not found:
                           set_waiting_condition({type:
                           \'erp_commission_record_found\',
                           retry_interval: 2h}). Schedule timer. Exit. If
                           found: continue.

  **6. Lookup rate         If refund_type is partial_refund:
  schedule (if partial)**  lookup_commission_rate_schedule(partner_id,
                           booking_date, erp). Required for tiered rate
                           calculation. If full refund: skip.

  **7. Calculate           calculate_clawback_amount(commission_record,
  clawback**               refund_details, rate_schedule).
                           validate_clawback_rules(clawback_result,
                           commission_record). If disposition is
                           write_off: post_timeline_entry(\'De minimis
                           --- clawback written off\'),
                           move_box_stage(\'closed\'). Stop. If
                           disposition is cfo_approval_required: route to
                           CFO approval tier regardless of other rules.

  **8. Apply FX            If clawback_currency differs from
  adjustment**             reversal_currency: apply_fx_adjustment.
                           Otherwise: skip.

  **9. Draft journal       draft_reversal_journal_entry.
  entry**                  validate_journal_entry. If validation fails:
                           move_box_stage(\'detected\'),
                           send_slack_exception with specific GL
                           validation failures. Stop.

  **10. Generate summary** generate_clawback_summary(clawback_result,
                           booking_record, journal_entry). Used in the
                           approval message.

  **11. Route for          If clawback_amount is within auto-approve
  approval**               threshold and autonomy tier permits:
                           pre_post_validate_clawback →
                           post_reversal_entry →
                           move_box_stage(\'posted\') →
                           send_slack_clawback_posted →
                           send_partner_clawback_notification → set
                           dispute window timer. If manual approval
                           required: send_slack_clawback_approval →
                           move_box_stage(\'awaiting_approval\') →
                           set_waiting_condition(approval_response) →
                           schedule approval timeout.
  -----------------------------------------------------------------------

**5.2 Planning for clawback_approval_received**

  -----------------------------------------------------------------------
  **Decision**             **Plan**
  ------------------------ ----------------------------------------------
  **Approved, box in       pre_post_validate_clawback →
  awaiting_approval**      post_reversal_entry →
                           move_box_stage(\'posted\') →
                           apply_label(\'Clawback/Posted\') →
                           post_timeline_entry(DID-WHY-NEXT) →
                           send_slack_clawback_posted →
                           send_partner_clawback_notification → set
                           dispute window timer (dispute_window_expired
                           in 14 days by default). watch_thread for
                           partner dispute responses.

  **Approved with          Same as above. override_reason logged to
  override**               timeline and sent to the Operations Console
                           quality dashboard (internal review).

  **Rejected**             move_box_stage(\'disputed\') →
                           post_timeline_entry with rejection reason →
                           alert AP Manager that the clawback has been
                           voided internally. No partner notification
                           sent --- AP Manager determines next steps.

  **Approval timeout**     Escalate to next approver tier. If at CFO
                           level: send_slack_exception with urgency flag
                           and clawback due date. Do not auto-approve. Do
                           not silently stall.
  -----------------------------------------------------------------------

**5.3 Planning for partner_dispute_received**

  -----------------------------------------------------------------------
  **Decision**             **Plan**
  ------------------------ ----------------------------------------------
  **Box in posted stage**  classify_vendor_response(dispute_email,
                           box_id) → move_box_stage(\'disputed\') →
                           send_slack_dispute_alert → post_timeline_entry
                           with dispute reason → watch_thread for further
                           partner messages. Agent parks. AP Manager
                           resolves.

  **Box in closed stage**  Dispute received after dispute window. Log to
                           timeline. Alert AP Manager. AP Manager
                           determines whether to reopen. Agent does not
                           reopen automatically.

  **AP Manager resolution: post_timeline_entry(\'Clawback confirmed over
  confirmed**              partner dispute. Dispute resolved.\') →
                           move_box_stage(\'closed\'). No further ERP
                           action --- the posted entry stands.

  **AP Manager resolution: reverse_erp_post(erp_entry_id, reason, erp) →
  reversed**               post_timeline_entry →
                           send_partner_clawback_notification (template:
                           dispute_resolved_reversed) →
                           move_box_stage(\'closed\').
  -----------------------------------------------------------------------

**5.4 Planning for timer_fired (Clawback-specific)**

  --------------------------------------------------------------------------------
  **Timer type**                    **Plan on firing**
  --------------------------------- ----------------------------------------------
  **erp_commission_record_check**   lookup_commission_record(booking_reference,
                                    erp). If found: clear_waiting_condition,
                                    resume from step 5 (lookup commission). If not
                                    found: check clawback due date. If past due:
                                    escalate to AP Manager. Otherwise: reschedule
                                    with exponential backoff (starting at 30
                                    minutes, doubling to a 4-hour ceiling).
                                    Maximum 5 retries (approximately 12 hours
                                    total) before mandatory escalation. In
                                    addition, a pipeline-level circuit breaker
                                    monitors the rolling 1-hour window: if more
                                    than 20% of commission record lookups return
                                    not_found across the workspace, the pipeline
                                    pauses individual retries, raises a pipeline
                                    exception to the AP Manager, and waits for
                                    operator clearance. This prevents a bad ERP
                                    data window from generating thousands of
                                    repeated lookups.

  **clawback_approval_timeout**     Escalate to next approval tier. Log to
                                    timeline. If at CFO level:
                                    send_slack_exception with urgency.

  **dispute_window_expired**        If no dispute received:
                                    move_box_stage(\'closed\').
                                    post_timeline_entry(\'Dispute window closed.
                                    Clawback settled.\'). Box archived per
                                    configured retention policy. The dispute
                                    window length is a material legal /
                                    contractual policy, not a silent
                                    configuration. It must be explicitly set per
                                    workspace during onboarding, with the default
                                    (14 days) surfaced in the setup flow. The
                                    assumption is that partner silence during
                                    the window constitutes consent. This
                                    assumption must be supported by the
                                    customer\'s partner agreements; Clearledgr
                                    does not assert the legal basis on behalf
                                    of the customer.
  --------------------------------------------------------------------------------

**6. The Complete Commission Clawback Lifecycle**

This section traces a single full-refund clawback from detection to
settlement, showing the exact sequence of planning and execution steps.
This is the canonical reference for how a standard clawback moves
through the system.

**6.1 Full Refund --- Happy Path**

  -----------------------------------------------------------------------
  **Step**                 **What happens**
  ------------------------ ----------------------------------------------
  **1**                    Cancellation notification email arrives in ap@
                           inbox. Gmail Pub/Sub fires. Listener fetches
                           content. Enqueues refund_detected event.

  **2**                    Planning engine receives event.
                           classify_refund_event returns {type:
                           \'full_refund\', confidence: 0.91,
                           booking_reference: \'BK-2024-88441\',
                           refund_amount: 4200, currency: \'EUR\'}.

  **3**                    extract_refund_fields extracts all fields.
                           run_refund_extraction_guardrails passes all
                           five guardrails.

  **4**                    check_clawback_duplicate: no existing reversal
                           entry found.

  **5**                    lookup_original_booking(\'BK-2024-88441\'):
                           booking found, status \'cancelled\'.
                           original_booking_amount: EUR 4,200.

  **6**                    lookup_commission_record(\'BK-2024-88441\'):
                           commission record found. amount_paid: EUR 756
                           (18% rate). payment_date: 12 Nov 2024.

  **7**                    calculate_clawback_amount: full refund ---
                           clawback = EUR 756. validate_clawback_rules:
                           passes all rules. Disposition: proceed.

  **8**                    FX adjustment: clawback currency matches
                           reversal currency (EUR). Skip.

  **9**                    draft_reversal_journal_entry: Debit Commission
                           Income EUR 756, Credit Partner Payables EUR
                           756. Narrative: \'Commission reversal ---
                           BK-2024-88441 fully refunded 03 Apr 2026.\'
                           validate_journal_entry: GL accounts active,
                           period open, debits equal credits.

  **10**                   generate_clawback_summary: summary generated
                           for approval message.

  **11**                   Clawback amount EUR 756 is above auto-approve
                           threshold. send_slack_clawback_approval to AP
                           Controller.
                           move_box_stage(\'awaiting_approval\').
                           set_waiting_condition(approval_response).
                           Approval timeout scheduled (4 hours).

  **12**                   AP Controller approves in Slack.
                           clawback_approval_received event enqueued.

  **13**                   pre_post_validate_clawback: commission record
                           present and not reversed, approval valid, GL
                           period open, no duplicate entry. All clear.

  **14**                   post_reversal_entry: ERP confirms posting.
                           Entry ID: JNL-2026-04412.

  **15**                   move_box_stage(\'posted\').
                           apply_label(\'Clawback/Posted\').
                           post_timeline_entry --- DID: \'Posted reversal
                           entry JNL-2026-04412 to SAP. Commission
                           clawback EUR 756 from partner
                           BK-Partner-991.\' WHY: \'Full refund of
                           booking BK-2024-88441. AP Controller approved
                           14:22.\' NEXT: \'Partner notification sent.
                           Dispute window open until 17 Apr 2026.\'

  **16**                   send_partner_clawback_notification to partner.
                           Thread watched for dispute responses.
                           dispute_window_expired timer scheduled for 14
                           days.

  **17**                   14 days later: dispute_window_expired fires.
                           No dispute received.
                           move_box_stage(\'closed\'). Box archived.
  -----------------------------------------------------------------------

**6.2 Partial Refund with FX Adjustment**

Same as the happy path through step 4. At step 5: booking found with
original_booking_amount: GBP 3,200. Refund amount: GBP 1,600 (50%
partial refund).

-   lookup_commission_rate_schedule: tiered rate schedule found. Rate
    applicable at booking date: 15%.

-   lookup_commission_record: amount_paid: GBP 480 (15% of GBP 3,200).

-   calculate_clawback_amount (partial): clawback = (1600 / 3200) × 480
    = GBP 240.

-   apply_fx_adjustment: reversal currency is EUR, clawback currency is
    GBP. ERP rate: 1.17. Adjusted clawback: EUR 280.80.

-   draft_reversal_journal_entry: Debit Commission Income EUR 280.80,
    Credit Partner Payables EUR 280.80. Narrative includes FX rate and
    original GBP amount.

-   Remaining flow identical to happy path.

**6.3 De Minimis Clawback**

Same as the happy path through step 7. At step 7:
calculate_clawback_amount returns EUR 4.20. validate_clawback_rules:
disposition is write_off (below the EUR 10 de minimis threshold the
workspace has explicitly opted into, with a configured de minimis memo
GL account).

-   draft_reversal_journal_entry is skipped. Instead, a de minimis memo
    entry is posted: Debit Commission Income EUR 4.20, Credit De
    Minimis Clawback Accrual EUR 4.20. Narrative: \'De minimis
    clawback accrual --- BK-2024-99102. Below workspace threshold
    (EUR 10).\'

-   post_timeline_entry: \'Clawback of EUR 4.20 for BK-2024-99102
    below de minimis threshold (EUR 10). Posted to memo account. No
    partner payable entry. No partner notification.\'

-   move_box_stage(\'closed\'). No partner notification sent. The
    accrual is included in the monthly de minimis reconciliation
    report and cleared on a scheduled cadence (workspace-configured).

-   Box archived. If the workspace has not configured a de minimis
    threshold (default), this path never fires --- every clawback
    posts a full reversal regardless of amount.

**6.4 Commission Record Not Found**

Same as the happy path through step 5. At step 6:
lookup_commission_record returns not found.

-   set_waiting_condition({type: \'erp_commission_record_check\',
    booking_reference, backoff: \'exponential\', initial_interval:
    30m, max_interval: 4h, max_retries: 5}).

-   Timer scheduled. Agent exits. Box in \'lookup\' stage with waiting
    condition.

-   First retry 30 minutes later: timer_fired.
    lookup_commission_record again. If still not found and clawback
    is time-sensitive: escalate to AP Manager. Otherwise: reschedule
    with backoff.

-   Pipeline-level circuit breaker checks the rolling not_found rate
    before each individual retry. If the workspace is above the 20%
    not-found threshold, the retry is suppressed and the pipeline
    flags the data-quality issue.

-   On record found: clear_waiting_condition. Resume from step 6.
    Normal flow continues.

-   On max retries reached (approximately 12 hours):
    apply_label(\'Review Required\'), send_slack_exception to AP
    Manager, move_box_stage(\'detected\') with needs_review flag.

**6.5 Partner Dispute**

Same as the happy path through step 16. 6 days after partner
notification, a reply arrives: \'The cancellation was made due to force
majeure --- please waive the commission clawback.\'

-   Email arrives. Thread is watched. partner_dispute_received event
    enqueued.

-   classify_vendor_response: type \'dispute\', confidence 0.88.

-   move_box_stage(\'disputed\'). send_slack_dispute_alert to AP Manager
    with dispute reason and resolution options.

-   AP Manager reviews. Options: \[Confirm clawback\] / \[Reverse
    clawback\].

-   AP Manager selects \'Reverse clawback\'.
    reverse_erp_post(JNL-2026-04412, \'Force majeure dispute accepted\',
    erp). ERP confirms reversal.

-   post_timeline_entry. send_partner_clawback_notification
    (dispute_resolved_reversed template). move_box_stage(\'closed\').

**7. LLM/Deterministic Boundary --- Clawback Extension**

The boundary defined in the core spec applies without modification. This
section specifies the four Claude calls specific to the clawback
pipeline.

  ------------------------------------------------------------------------------
  **LLM action**                  **Inputs, constraints, budget**
  ------------------------------- ----------------------------------------------
  **classify_refund_event**       Input: email headers, plain text body (first
                                  2,000 tokens), ERP record summary (if source
                                  is ERP). System prompt: return JSON only with
                                  type enum and confidence. Initial confidence
                                  threshold: 0.85 (stricter than invoice
                                  classification --- clawback errors are more
                                  costly). This threshold is an initial working
                                  value, to be calibrated against a sample of
                                  at least 200 anonymised Booking.com refund
                                  emails before go-live. Calibration measures
                                  precision and recall at 0.75, 0.85, 0.90,
                                  and 0.95 and selects the threshold that
                                  balances false-positive cost (a wrong
                                  clawback drafted) against false-negative
                                  cost (human review load). Token budget:
                                  2,000 tokens input.

  **extract_refund_fields**       Input: email or ERP record content (up to
                                  3,000 tokens). System prompt: return JSON with
                                  refund field schema. Do not infer values not
                                  present. Do not convert currencies. Return
                                  amounts exactly as they appear. Token budget:
                                  3,000 tokens input.

  **generate_clawback_summary**   Input: fully structured clawback result,
                                  booking record, journal entry draft. System
                                  prompt: write one paragraph in DID-WHY-NEXT
                                  format. Maximum 200 words. Factual and
                                  precise. No speculation. Token budget: 1,000
                                  tokens input. If this call fails: use a
                                  template message constructed from structured
                                  data.

  **draft_dispute_response**      Input: dispute email content, clawback
                                  details, and the AP Manager's stated
                                  resolution direction. System prompt: draft a
                                  professional, factual reply to the partner.
                                  Maximum 300 words. Always staged for AP
                                  Manager review. Never sent autonomously. Token
                                  budget: 3,000 tokens input.
  ------------------------------------------------------------------------------

> *The clawback calculation (calculate_clawback_amount), the journal
> entry construction (draft_reversal_journal_entry), and all validation
> steps (validate_clawback_rules, validate_journal_entry,
> pre_post_validate_clawback) are always deterministic. Claude never
> touches these steps. This is non-negotiable.*

**8. Box State --- Clawback-Specific Fields**

The following fields extend the Box state object defined in the core
spec. All existing fields apply to the commission_clawback pipeline
without modification.

  -----------------------------------------------------------------------------
  **Field**                      **Type and purpose**
  ------------------------------ ----------------------------------------------
  **pipeline:                    Identifies this Box as a clawback pipeline
  \'commission_clawback\'**      Box.

  **refund_type**                Enum: full_refund, partial_refund,
                                 cancellation_no_charge, cancellation_with_fee,
                                 commission_adjustment. Set by
                                 classify_refund_event.

  **booking_reference**          The ERP booking reference extracted from the
                                 refund signal.

  **refund_amount**              The refund amount extracted from the signal,
                                 in refund_currency.

  **refund_currency**            ISO 4217 currency code of the refund amount.

  **original_booking_record**    JSONB. The full booking record retrieved from
                                 the ERP.

  **commission_record**          JSONB. The commission record retrieved from
                                 the ERP: amount_paid, commission_rate,
                                 payment_date, currency.

  **clawback_result**            JSONB. Output of calculate_clawback_amount:
                                 clawback_amount, clawback_currency,
                                 calculation_basis, inputs_used. Null until
                                 calculation completes.

  **fx_adjustment**              JSONB. Output of apply_fx_adjustment if
                                 applied: original_amount, original_currency,
                                 adjusted_amount, adjusted_currency, rate_used,
                                 rate_date. Null if no FX adjustment needed.

  **journal_entry_draft**        JSONB. The draft reversal journal entry:
                                 debit_lines, credit_lines, narrative,
                                 erp_format. Null until
                                 draft_reversal_journal_entry completes.

  **erp_entry_id**               String. The ERP's assigned ID for the posted
                                 reversal entry. Null until post_reversal_entry
                                 succeeds.

  **dispute_received_at**        Timestamp. When the partner dispute was
                                 received. Null if no dispute.

  **dispute_window_closes_at**   Timestamp. When the dispute window for this
                                 clawback closes. Set after post_reversal_entry
                                 succeeds.

  **dispute_resolution**         Enum: confirmed, reversed. Set by AP Manager
                                 on dispute resolution. Null if no dispute or
                                 dispute not yet resolved.

  **write_off_reason**           String. Populated when disposition is
                                 write_off. Records the de minimis threshold
                                 and amount for audit purposes.
  -----------------------------------------------------------------------------

**9. ERP Connector Implementations**

The commission clawback pipeline introduces SAP as the primary ERP
target for the Booking.com design partnership. The existing ERP
Connector Layer interface is unchanged --- all new actions call the same
interface methods. The SAP connector implementation requires the
following additions.

  ------------------------------------------------------------------------------------
  **ERP operation**                     **Implementation detail**
  ------------------------------------- ----------------------------------------------
  **lookup_original_booking**           Queries the ERP for the original booking/order
                                        record by reference. SAP: SD module via
                                        Service Layer GET /Orders or S/4HANA OData.
                                        NetSuite: SuiteQL query on Transaction
                                        records. Xero: GET /Invoices filtered by
                                        reference. QuickBooks: Query API on Invoice
                                        entity. Returns the full order document: line
                                        items, amounts, cancellation status.

  **lookup_commission_record**          Queries the ERP for the commission journal
                                        entry associated with a booking reference. The
                                        connector configuration stores the commission
                                        GL account code per workspace. SAP: journal
                                        entry query API filtered by reference and
                                        account. NetSuite: JournalEntry search by
                                        memo/reference. Xero: GET /ManualJournals
                                        filtered by narration. QuickBooks:
                                        JournalEntry query by reference. Returns:
                                        amount_paid, commission_rate, payment_date,
                                        currency.

  **lookup_commission_rate_schedule**   Queries the ERP for the applicable commission
                                        rate schedule for a partner at the time of the
                                        original booking. SAP: condition records via
                                        KONP/KONV tables. NetSuite: custom rate
                                        schedule records. Xero/QuickBooks: rate
                                        schedules are typically maintained in
                                        Clearledgr configuration for these ERPs, with
                                        ERP lookup as a fallback. The connector maps
                                        the result to the Clearledgr rate_schedule
                                        object.

  **post_reversal_entry**               Posts the reversal journal entry to the ERP.
                                        SAP: POST /JournalEntries via Service Layer,
                                        or BAPI_ACC_DOCUMENT_POST equivalent via
                                        S/4HANA OData. NetSuite: POST /journalentries
                                        via REST API. Xero: POST /ManualJournals.
                                        QuickBooks: POST /journalentries via QBO API.
                                        Each connector translates the
                                        journal_entry_draft into the ERP-native format
                                        before posting. Returns the ERP-assigned entry
                                        ID.

  **reverse_erp_post**                  Reverses a previously posted journal entry.
                                        SAP: built-in document reversal via POST
                                        /JournalEntries with reversal reason code.
                                        NetSuite: reversal journal entry via REST API.
                                        Xero: voiding the manual journal and posting a
                                        new reversal. QuickBooks: journal entry
                                        reversal via QBO API. Each connector uses the
                                        ERP\'s native reversal mechanism to ensure the
                                        ledger remains balanced. Returns the reversal
                                        document reference. Accepts a structured
                                        reversal_reason_code from a fixed enum
                                        (force_majeure_dispute, contractual_waiver,
                                        calculation_error, duplicate_post,
                                        partner_confirmed_error, ap_manager_override,
                                        other) plus an optional free-text note. The
                                        enum value is mapped to the ERP\'s native
                                        reason code taxonomy in the connector layer
                                        and stored on the Box so reversal reporting
                                        (\"show me all force_majeure reversals YTD\")
                                        is queryable without free-text parsing.
  ------------------------------------------------------------------------------------

> *First production implementation: SAP (Booking.com design
> partnership). OAuth 2.0 via SAP BTP. The SAP connector maintains a
> token pool per workspace. Sandbox credentials must be provisioned by
> the customer\'s SAP admin team before live testing. All other ERP
> connectors follow the same interface --- the connector handles
> ERP-specific authentication, rate limiting, and document format
> translation.*

**9.1 Audit Trail Export**

Every clawback Box carries the full chain-of-custody on its timeline
by design. The audit export feature surfaces this chain as a
structured deliverable for internal audit, external audit, and
regulator requests. It is a first-class feature, not a byproduct of
logging.

  ---------------------------------------------------------------------------
  **Field**                       **Content**
  ------------------------------- ------------------------------------------
  **Trigger**                     Source type (email, ERP webhook),
                                  message_id or erp_record_id, received_at
                                  timestamp, raw subject line and sender
                                  (email) or ERP user (webhook).

  **Classification**              Claude classification result: type,
                                  confidence, model version, prompt
                                  version, token usage.

  **Extraction**                  Extracted fields with
                                  per-field confidence, guardrail results
                                  (passed/failed per guardrail), raw
                                  source content hash for tamper-evidence.

  **Lookup results**              ERP lookup payloads: original booking
                                  record, commission record, rate
                                  schedule. Timestamps and ERP call
                                  durations. Retry attempts if any.

  **Calculation**                 Deterministic calculation inputs,
                                  applied formula, calculation_basis,
                                  FX rate and rate_date if applied.

  **Approval**                    Approver email, approval channel
                                  (Slack/Gmail), decision, decision
                                  timestamp, override reason if any,
                                  approval escalation path if escalated.

  **Posting**                     Journal entry structure (debit/credit
                                  lines, narrative, GL codes), ERP
                                  entry_id, posted_at timestamp,
                                  pre-post validation result.

  **Partner notification**        Notification sent_at, template used,
                                  recipient addresses, thread_id.

  **Dispute and resolution**      Dispute received_at, classification,
                                  allowlist match result, AP Manager
                                  resolution, resolution timestamp,
                                  reversal_reason_code if reversed.

  **Export format**               PDF (narrative form for auditor
                                  review) and JSONL (structured form
                                  for integration with GRC tools).
                                  Both are generated from the same Box
                                  timeline --- the PDF is a rendering of
                                  the JSONL, not a separate record.
  ---------------------------------------------------------------------------

Audit exports are scoped to a time range and an optional filter
(partner, reversal_reason_code, amount band, disposition). The JSONL
export is signed with a per-workspace signing key so auditors can
verify it was generated by Clearledgr and not modified after export.
Audit exports are themselves logged to a workspace-level audit log
(who ran the export, what filter, when) so Clearledgr\'s own export
activity is traceable.

**10. Performance Requirements**

  -----------------------------------------------------------------------------------
  **Stage**                            **Target**
  ------------------------------------ ----------------------------------------------
  **Refund detection to first Slack    \< 3 minutes (target). This is the time from
  notification**                       email receipt to AP Manager seeing the
                                       clawback summary and approval buttons.

  **classify_refund_event (LLM)**      \< 5 seconds

  **extract_refund_fields (LLM)**      \< 8 seconds

  **run_refund_extraction_guardrails   \< 500ms
  (DET)**                              

  **ERP lookup per call                \< 3 seconds (Xero, QuickBooks, NetSuite). \<
  (lookup_original_booking,            10 seconds (SAP). Booking.com-scale S/4HANA
  lookup_commission_record)**          instances with the table volumes implied by
                                       KONV/KONP joins can routinely reach the upper
                                       bound. The SAP connector uses a 10-second
                                       timeout with exponential backoff (2 retries,
                                       initial delay 1s, doubling). Queries
                                       exceeding the timeout after retries are moved
                                       to async processing rather than blocking the
                                       pipeline. Other connectors use a 3-second
                                       timeout with 2 retries. SAP SLAs must be
                                       pressure-tested against the Booking.com SAP
                                       BTP team before go-live; the 10-second
                                       target is a working assumption to be
                                       calibrated against production data
                                       characteristics.

  **calculate_clawback_amount (DET)**  \< 100ms

  **validate_clawback_rules (DET)**    \< 100ms

  **draft_reversal_journal_entry       \< 500ms
  (DET)**                              

  **validate_journal_entry (DET)**     \< 2 seconds. Includes one ERP GL account
                                       lookup to validate account codes. SAP lookups
                                       may reach the upper bound.

  **post_reversal_entry (ERP)**        \< 5 seconds (Xero, QuickBooks, NetSuite). \<
                                       12 seconds (SAP). SAP journal entry posting
                                       is synchronous and slower than cloud-native
                                       ERPs, and production S/4HANA instances at
                                       Booking.com scale can push the upper bound.
                                       All connectors wait for the posted document
                                       number before confirming success. The
                                       Booking.com SAP BTP team should validate
                                       posting-side SLAs during sandbox testing.
  -----------------------------------------------------------------------------------

The commission record not-found scenario (section 6.4) is outside the
SLA clock. The clock stops when the agent sets waiting_condition and
restarts when the condition is cleared.

**11. Build Plan**

The following components require engineering work. Components are listed
in dependency order --- each depends on the components above it being
complete. The calculation engine, classification logic, and
communication actions are ERP-agnostic and testable without any ERP
connection. ERP connector work requires sandbox credentials from the
customer. First production target: SAP (Booking.com).

  ---------------------------------------------------------------------------
  **Component**       **Estimate**   **Notes**
  ------------------- -------------- ----------------------------------------
  **ERP connectors    **2--3 days    lookup_original_booking,
  --- booking and     per ERP**      lookup_commission_record,
  commission                         lookup_commission_rate_schedule.
  lookups**                          Existing connector interface. New query
                                     implementations per ERP. First
                                     implementation: SAP (Booking.com).
                                     Subsequent ERPs follow the same
                                     interface with ERP-specific query logic.

  **Refund event      **2--3 days**  classify_refund_event,
  classification and                 extract_refund_fields,
  extraction**                       run_refund_extraction_guardrails.
                                     Adapted from invoice classification and
                                     extraction. New prompts and guardrail
                                     logic. Testable with anonymised email
                                     samples.

  **Clawback          **1--2 days**  calculate_clawback_amount,
  calculation                        validate_clawback_rules,
  engine**                           apply_fx_adjustment. Purely
                                     deterministic. Full test coverage
                                     required --- full refund, partial
                                     refund, tiered rate, de minimis, FX
                                     adjustment, ceiling exceeded. No
                                     external dependencies.

  **Journal entry     **2--3 days**  draft_reversal_journal_entry,
  construction and                   validate_journal_entry. ERP-agnostic
  validation**                       journal entry builder with per-ERP
                                     format translation in the connector
                                     layer. GL account validation requires an
                                     ERP connection. Can be prototyped with
                                     hardcoded GL codes before sandbox
                                     access.

  **ERP connectors    **2--3 days    post_reversal_entry,
  --- posting and     per ERP**      pre_post_validate_clawback,
  reversal**                         reverse_erp_post. Requires ERP sandbox.
                                     SAP is the most complex --- document
                                     posting is stateful and requires precise
                                     error handling. Xero and QuickBooks are
                                     simpler. NetSuite sits in between.

  **Planning engine   **1--2 days**  New handlers for refund_detected,
  extension**                        clawback_approval_received,
                                     partner_dispute_received, and
                                     clawback-specific timer_fired cases.
                                     Extends existing planning engine without
                                     modifying existing handlers.

  **Communication     **1 day**      send_slack_clawback_approval,
  actions**                          send_partner_clawback_notification,
                                     send_slack_clawback_posted,
                                     send_slack_dispute_alert. Adapted from
                                     existing Slack and email communication
                                     actions. New message templates and
                                     content.

  **Box state and     **1 day**      commission_clawback pipeline definition.
  pipeline                           New Box fields. Stage transition rules.
  registration**                     Migration script for new table columns.

  **End-to-end        **2--3 days**  Full lifecycle tests per ERP: happy
  testing**                          path, partial refund, de minimis
                                     write-off, commission record not found,
                                     partner dispute, multi-leg detection,
                                     multiple commission records detection.
                                     Requires at least one ERP sandbox for
                                     posting tests. SAP sandbox required for
                                     Booking.com go-live.

  **Classifier       **1--2 days**   Run classify_refund_event against ≥200
  calibration**                      anonymised Booking.com refund emails.
                                     Measure precision and recall at
                                     thresholds 0.75, 0.85, 0.90, 0.95.
                                     Set the production threshold based on
                                     the measured false-positive /
                                     false-negative trade-off. Gate:
                                     anonymised sample from Booking.com
                                     must be available by week 2.

  **Audit export**   **1--2 days**   JSONL and PDF export of the full Box
                                     timeline, scoped by time range and
                                     filters (partner, reason code, amount
                                     band, disposition). Per-workspace
                                     signing key. Export activity log.
                                     ERP-agnostic; reads from Box timeline
                                     only.
  ---------------------------------------------------------------------------

**Total estimate:** 4--5 weeks for a working prototype on the first ERP
(previously 3--4 weeks; classifier calibration and audit export add
2--4 days).
Each additional ERP connector adds 2--3 days once the core pipeline is
live. This assumes: first ERP endpoint documentation available from week
1, sandbox access from week 3, anonymised test data for classification
and extraction validation.

> *The critical path is ERP connector access. The calculation engine,
> classification logic, and planning engine are ERP-agnostic and
> testable from week 1. ERP connector work in weeks 3--4 is what makes
> the prototype demoable on real data. A working demo is possible with a
> representative ERP schema and anonymised data before live sandbox
> access is confirmed.*

**12. Codebase Starting State**

The architecture in §3--§5 is accurate --- every primitive the spec
assumes ("inherited without modification") already exists in the
repository. This section maps each spec-defined action to its concrete
starting point in the current codebase so nothing gets built twice and
nothing gets missed.

**12.1 Reuse and adapt (≈60% of the action space)**

Most actions the spec marks as EXISTING or ADAPTED have a concrete
pattern already in the coordination engine, LLM gateway, ERP router, or
Slack service. Build them as small extensions of the existing files,
not as new modules.

  ----------------------------------------------------------------------------------------
  **Spec action**                            **Starting point in current codebase**
  ------------------------------------------ ---------------------------------------------
  **classify_refund_event (LLM)**            Same shape as `classify_email` /
                                             `classify_vendor_response` in
                                             `clearledgr/core/execution_engine.py`. New
                                             prompt only, new confidence threshold.

  **extract_refund_fields (LLM)**            Same shape as `extract_invoice_fields`.
                                             Same guardrail framework.

  **run_refund_extraction_guardrails**       `run_extraction_guardrails` is the template.
                                             Five new rules; existing dispatch works.

  **check_clawback_duplicate**               `check_duplicate` already checks 90-day AP
                                             window; clone the query shape, swap table.

  **pre_post_validate_clawback**             Fork carefully from `pre_post_validate`.
                                             That helper absorbed TOCTOU fixes in
                                             commit 5af7496. Do not re-regress the
                                             state-guard --- pre-post re-reads state,
                                             checks for existing ERP entry, and holds
                                             across the post call.

  **post_reversal_entry**                    `post_journal_entry` already exists in
                                             `clearledgr/integrations/erp_router.py` and
                                             routes to QB / Xero / NetSuite / SAP with
                                             rate limits and retry. Extend, do not
                                             parallel-build. See §13.3.

  **reverse_erp_post**                       `reverse_bill` exists per connector
                                             (`erp_sap.py`, `erp_quickbooks.py`,
                                             `erp_xero.py`, `erp_netsuite.py`). Takes a
                                             free-text reason today. New work: the
                                             structured `reversal_reason_code` enum
                                             and per-ERP native mapping. See §13.4.

  **send_slack_clawback_approval**           `build_approval_blocks` in
                                             `clearledgr/services/slack_api.py` is the
                                             block builder; the clawback variant is a
                                             templated wrapper.

  **send_partner_clawback_notification**     `send_vendor_email` handler covers the
                                             dispatch; new work is the notification
                                             template and thread-allowlist logic from
                                             §4.5.

  **watch_thread**                           `_handle_watch_thread` already exists and
                                             dispatches `vendor_response_received`
                                             events. Partner-dispute monitoring rides
                                             this; route to
                                             `partner_dispute_received` based on
                                             pipeline.

  **Dispute window timer**                   `clearledgr/services/override_window.py`
                                             was built with `action_type`
                                             polymorphism for exactly this. Current
                                             types: `erp_post`. Add
                                             `clawback_dispute_window`. The reaper,
                                             audit, and Rule-1 pre-write carry over.

  **Pipelines table + Kanban stages**        `ap_invoices` and `vendor_onboarding`
                                             already registered.
                                             `commission_clawback` is one
                                             INSERT into the `pipelines` table plus
                                             `pipeline_stages` rows per stage in §2.1.

  **Audit export**                           `export_audit_trail` endpoint exists in
                                             `clearledgr/api/ap_items_read_routes.py`.
                                             Returns JSON today; new work is
                                             signing-key, PDF rendering, and filter
                                             support (partner, reason code, amount
                                             band, disposition).

  **Frontend pipeline page**                 `ui/gmail-extension/src/routes/pages/
                                             PipelinePage.js` is pipeline-agnostic ---
                                             it reads `pipeline_stages` rows and
                                             renders the Kanban. ClawbackPipelinePage
                                             is the same component with a different
                                             `pipeline_id`.
  ----------------------------------------------------------------------------------------

**12.2 Genuinely new work**

These have no direct precedent in the codebase and are real
engineering line items, not "adapted from existing."

  ------------------------------------------------------------------------------------
  **Component**                          **Why it's new**
  -------------------------------------- --------------------------------------------
  **SAP SD connector** (booking +        The existing SAP connector
  commission + rate-schedule lookups)    (`clearledgr/integrations/erp_sap.py`) only
                                         covers FI / AP bill posting and reversal. SD
                                         (Sales & Distribution) via S/4HANA OData is
                                         a distinct surface: different endpoints
                                         (`/Orders`, KONP / KONV for condition
                                         records), often separate auth context, and
                                         sometimes a different BTP destination. The
                                         spec §9 underplays this --- it's closer to
                                         "new connector module" than "new query
                                         type."

  **Clawback calculation engine**        `calculate_clawback_amount`,
                                         `validate_clawback_rules`,
                                         `apply_fx_adjustment`. Pure deterministic,
                                         no external dependencies. Testable in
                                         isolation from day one. 1--2 days.

  **draft_reversal_journal_entry**       Journal-entry structure (debit Commission
                                         Income, credit Partner Payables) is
                                         unlike AP bills (which debit expense
                                         accounts). The ERP-agnostic draft builder
                                         is new; the per-ERP format translation
                                         reuses the translation logic already in
                                         `post_journal_entry`.

  **Refund classification + extraction   LLM calls themselves follow existing
  prompts, and 200-sample calibration**  patterns --- but the prompts are new and
                                         the spec-mandated calibration (§7, §11)
                                         is a customer data dependency. If
                                         Booking.com cannot deliver anonymised
                                         samples, the 0.85 threshold ships
                                         uncalibrated. Flag this as a customer-side
                                         critical-path item during the pilot kick-off.

  **De minimis + memo account handling** Per-workspace threshold config,
                                         opt-in memo GL account, monthly
                                         reconciliation report. New config fields,
                                         new validator disposition, new report
                                         surface.

  **Out-of-scope detection rules**       Multi-leg, multiple commission records,
                                         and partner-netting flags from §1.1.1.
                                         New planner logic. Small in LOC but
                                         high-value: this is the "don't silently
                                         miscalculate" gate. Must be explicit in
                                         the planning engine, not implicit in the
                                         lookup connectors.

  **Audit export signing and PDF**       Existing `export_audit_trail` returns
                                         JSON. New work: per-workspace signing key
                                         (with workspace-level key-rotation story),
                                         JSONL format, PDF narrative renderer, and
                                         the workspace-level audit-of-the-audit log
                                         (§9.1) so export activity is itself
                                         traceable.

  **Frontend ClawbackDetailPage**        `InvoiceDetailPage.js` is the pattern.
                                         Clawback-specific panels: journal entry
                                         preview (debit / credit lines), calculation
                                         basis display, dispute resolution buttons.
                                         ~3 days. Reuses auth, Box timeline, and
                                         audit components wholesale.

  **Not-found circuit breaker**          The 20% rolling-window pipeline-level
                                         breaker in §6.4 is new. There is an
                                         existing circuit breaker service
                                         (`clearledgr/services/circuit_breaker.py`)
                                         but the commission-record-not-found signal
                                         and workspace-scoped threshold are new
                                         wiring.
  ------------------------------------------------------------------------------------

**13. Implementation Gotchas**

These are concerns the spec does not flag that will bite during build.
They are captured here so the planner who picks this up has them in
one place rather than having to re-derive them from the codebase.

**13.1 Box table shape --- new table, not new column**

§8 says "Box state extends with clawback-specific fields." The implicit
assumption is that clawback boxes live on the existing `ap_items`
table. Today, `ap_items` has no `pipeline` column and ~40
AP-invoice-specific columns that don't apply to clawbacks. Vendor
onboarding avoided this by using a separate `vendor_onboarding_sessions`
table.

Follow the vendor-onboarding precedent: create a new `clawback_boxes`
table keyed by `id`. The timeline and audit primitives key on a generic
`box_id` / `ap_item_id` string so the same machinery works against any
box table. The `pipelines` table was designed for exactly this --- it
has a `source_table` column that lets each pipeline point at its own
storage.

Do not add a `pipeline TEXT` column to `ap_items` and mix the two box
types in one table. The column sprawl and the WHERE-clause noise in
every existing AP query would not be worth the "one-table" convenience.

**13.2 SAP SD sandbox is the real critical path**

§11's week-3 sandbox estimate assumes Booking.com's SAP BTP team
delivers SD credentials on schedule. That is a customer-side
dependency with no contractual lever from our side. De-risk it:

-   Build the full classification, calculation, journal-entry-builder,
    and planning-engine layers against a mocked SAP SD schema in
    weeks 1--2. Representative fixtures (one booking, one commission
    record, one rate schedule) are enough for tests.

-   Stand up an end-to-end demo that runs against the mock in week 2
    so the pilot is demoable before sandbox access is confirmed.

-   Treat sandbox access as the gate on weeks 3--4, not week 1. If
    sandbox slips by two weeks, week-2 demo still ships.

-   Sandbox auth is the first thing to test once credentials land ---
    SD via OData often requires a different service user than
    FI / AP. Do not assume our existing SAP OAuth token works for the
    SD endpoints.

**13.3 Extend `post_journal_entry`, do not parallel-build**

`clearledgr/integrations/erp_router.py:518` already has
`post_journal_entry(organization_id, entry)` that routes to the right
ERP, enforces rate limits, handles retries, and emits audit events.
The spec's `post_reversal_entry` is the same function with a different
payload shape.

Build `post_reversal_entry` as a thin wrapper over `post_journal_entry`
that:

-   accepts a `journal_entry_draft` object built by
    `draft_reversal_journal_entry`
-   serializes it into the `entry` payload the existing router expects
-   sets a `"reversal": true` / `"reversal_reason_code": ...` flag the
    per-connector handlers can branch on

Do not duplicate the router's rate-limit, retry, and audit logic. Every
bug we fix in one path has to be re-fixed in the other, and ERP
posting is the exact surface where we cannot afford divergent code
paths.

**13.4 Reversal reason code: enum migration is per-connector**

§9 introduces a fixed enum (`force_majeure_dispute`,
`contractual_waiver`, `calculation_error`, `duplicate_post`,
`partner_confirmed_error`, `ap_manager_override`, `other`). Today
`reverse_bill` takes a free-text reason string.

The migration is three parts, one per ERP:

-   **SAP**: map to reversal reason codes in BKPF-STGRD. There is a
    standard SAP table of reversal reasons; the mapping must be
    configured per customer SAP instance during onboarding, not
    hardcoded.

-   **NetSuite**: reversal reason is a custom field on the journal
    entry --- the customer's NetSuite admin typically defines the
    picklist. Map the Clearledgr enum to the customer's picklist at
    connector config time.

-   **Xero** / **QuickBooks**: both store reversal reasons as
    free-text narration. The Clearledgr enum value is written into the
    narration prefix (`"[force_majeure_dispute] Partner dispute
    accepted --- ..."`) so downstream reporting can grep.

Store the Clearledgr enum value on the Box record regardless of ERP
(in the audit timeline and as a queryable field). That gives the
"show me all force_majeure reversals YTD" report without free-text
parsing, even on Xero / QuickBooks.

**13.5 Dispute window piggybacks on OverrideWindowService**

`clearledgr/services/override_window.py` was built with `action_type`
polymorphism. Current usage: `"erp_post"`. The clawback dispute window
from §5.2 is a second `action_type`.

What this gives us for free:

-   per-workspace configurable duration
    (`settings_json["workflow_controls"]["override_window_minutes"]`
    already supports a dict keyed by action_type)
-   the reaper (`reap_expired_override_windows` in
    `agent_background.py`) that wakes up every 60s
-   Rule-1-compliant audit for every expiry and reversal
-   Slack card update on expiry

What we must add:

-   new `action_type` string constant
    (`"clawback_dispute_window"`)
-   default duration (14 days per §5.4) in the config resolver's
    fallback chain
-   a reaper hook that emits `dispute_window_expired` instead of
    `override_window_expired` for this action_type

Do not build a second reaper. The existing one is correct and was
stress-tested during the AP override-window rollout.

Commission Clawback Agent Design Specification · Clearledgr Ltd ·
Engineering team only · Review with CTO before implementation
