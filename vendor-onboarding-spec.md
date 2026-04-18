**CLEARLEDGR**

**Vendor Onboarding Agent**

**Design Specification**

How the vendor onboarding agent invites vendors, runs KYC, verifies
bank details via open banking, and writes to the ERP vendor master ·
Internal engineering reference

> *Confidential --- Clearledgr Ltd · Engineering team only*

**1. Overview**

Vendor Onboarding is a foundational sub-workflow of the Accounts Payable pipeline --- it runs alongside AP Invoices and ensures that every vendor who sends an invoice is a trusted, verified, ERP-registered party before an AP Invoice Box is ever opened. It is the first workflow customers see when they begin using Clearledgr for AP, because every new invoice from an unknown sender triggers an onboarding Box that must complete before AP processing can continue. The two workflows are structurally entwined.

Vendor onboarding automates the journey from first contact with a new vendor to that vendor being written to the ERP vendor master with AP-enabled status --- a journey that today takes most finance teams two to six weeks, multiple email threads, a shared spreadsheet, and manual ERP data entry.

The problem is not that vendor onboarding is complicated. It is that it is fragmented. A finance team invites a vendor by email, the vendor replies with documents attached, someone downloads them, someone else emails back asking for the missing certificate, the vendor eventually sends it, someone initiates micro-deposits or requests a bank letter, someone validates the response, someone types the record into the ERP, someone tells the AP team the vendor is live. Each step is cheap. The coordination is expensive. Most teams track this in a spreadsheet because no tool owns the whole journey.

The Clearledgr thesis is that vendor onboarding and AP invoicing are not two separate products --- they are two Box types in the same coordination layer. The data compounds: the KYC record, bank verification state, and vendor classification built during onboarding become inputs the AP pipeline consumes the first time an invoice arrives from that vendor. The coordination compounds: the Box from onboarding carries forward as an attributable source of truth for every subsequent AP decision involving that vendor. A finance team that trusts Clearledgr to onboard a vendor also trusts Clearledgr to process invoices from that vendor, because the same Box-level attributable history is visible across both.

The agent automates the whole journey end-to-end. It sits inside Gmail and Slack, dispatches the onboarding invitation, runs a hosted vendor-facing portal for document submission and bank verification, performs KYC checks at the depth the workspace has configured, verifies bank details via open banking, drafts the vendor master record, routes for approval where required, and writes to the ERP. No spreadsheet. No side channel. The finance team stays in their inbox.

This spec extends the core Clearledgr Agent Design Specification. All architectural components --- the event system, planning engine, coordination engine, state management, LLM/deterministic boundary, and error handling --- are inherited without modification. This document defines only what is new: the vendor onboarding event types, the extended action space, the four-stage pipeline, the planning logic, the vendor-facing portal surface, the open banking integration, and the complete lifecycle across the range of KYC configurations.

> *The fundamental design principle is unchanged: rules decide,
> Claude describes. KYC disposition, bank verification disposition,
> and the vendor master record are always deterministic. Claude is
> called only to classify vendor-submitted content, extract
> structured fields from free-text documents, and generate
> human-readable summaries. The decision to activate a vendor is
> constructed by rule, not by language model.*

**1.1 v1 Scope Boundary**

The v1 pipeline assumes a standard vendor onboarding journey: one
vendor, one business entity, one bank account, one ERP workspace.
The following cases are explicitly out of scope for v1 and route to
the AP Manager for manual handling:

-   **Multi-entity vendors** where a single onboarding request
    covers multiple legal entities (parent + subsidiaries) to be
    added under a shared vendor group. v1 treats each legal entity
    as a separate onboarding Box.

-   **Multi-currency / multi-bank vendors** where a vendor needs to
    be paid across several bank accounts or currencies. v1 verifies
    one bank account per vendor and flags requests for additional
    accounts for manual handling.

-   **Country-unsupported open banking.** Open banking coverage is
    meaningful in the EU, UK, and a growing set of other markets
    but does not cover every country a Booking.com or enterprise
    customer will have vendors in. Vendors in unsupported countries
    are flagged at the bank verification stage for manual bank
    letter verification by the AP team. Clearledgr does not attempt
    a fallback verification method in v1.

-   **Beneficial ownership resolution beyond a configurable depth.**
    v1 supports UBO resolution to the depth offered by the
    configured KYC provider (typically direct shareholders +
    declared UBOs). Deep ownership chains requiring manual
    investigation are flagged, not resolved by the agent.

These are v2 candidates. They are flagged in the pilot plan so the
team knows which cases drop to manual review on day one, not in
week two of production.

**1.1.1 Detection rules for out-of-scope cases**

The agent must recognise these cases and route to manual rather
than attempting to proceed incorrectly. Detection happens at
specific points in the pipeline:

-   **Multi-entity detection.** At invitation, if the vendor contact
    indicates in the invitation reply or portal submission that
    multiple entities are involved, apply_label(\'Review Required\'),
    send_slack_exception with the entity list, and stop. The agent
    does not attempt to split a single onboarding into multiple
    Boxes automatically.

-   **Multi-bank detection.** At bank verification, if the vendor
    submits more than one IBAN or account number through the
    portal, the portal rejects the input and requests one account
    for v1 onboarding. The agent does not proceed with the first
    account.

-   **Country-unsupported detection.** Before dispatching open
    banking verification, the agent checks the bank\'s country
    against the configured open banking provider\'s coverage list.
    If unsupported: apply_label(\'Review Required\'),
    send_slack_exception with the country and an instruction for
    the AP team to handle bank verification via their existing
    process. The rest of the onboarding (KYC, ERP write) continues
    once the AP team confirms verification externally.

**2. The Vendor Onboarding Pipeline**

The vendor onboarding pipeline has four stages. A Box enters the
pipeline when an onboarding is initiated (by an AP Manager action,
a new-vendor invoice arriving from an unknown sender, or a vendor
self-registration via a shared portal link) and exits when the
vendor has been written to the ERP vendor master with AP-enabled
status.

**2.1 Stages**

  -----------------------------------------------------------------------
  **Stage**                **Description**
  ------------------------ ----------------------------------------------
  **invited**              The onboarding invitation has been dispatched
                           to the vendor contact. The agent is waiting
                           for the vendor to access the onboarding
                           portal.

  **kyc**                  The vendor has accessed the portal and has
                           begun submitting documents and business
                           details. The agent is validating submissions
                           and running KYC checks at the workspace-
                           configured depth.

  **bank_verify**          KYC has passed. The agent has dispatched an
                           open banking verification link to the vendor.
                           The agent is waiting for the vendor to
                           complete the open banking flow.

  **active**               All checks have passed. The vendor record has
                           been written to the ERP vendor master with
                           AP-enabled status. Terminal stage on the
                           happy path.

  **blocked**              A specific blocker has been identified:
                           missing document, KYC check failure, bank
                           verification failure, or AP Manager rejection.
                           The blocker is named specifically. The agent
                           chases the vendor for resolution.

  **closed_unsuccessful**  Terminal stage. The onboarding did not
                           complete: the vendor did not respond to
                           chases, the AP Manager withdrew the
                           invitation, or a KYC check produced a
                           disposition that requires onboarding to be
                           abandoned.
  -----------------------------------------------------------------------

**2.2 Stage Transition Rules**

  -----------------------------------------------------------------------
  **Transition**             **Condition**
  -------------------------- --------------------------------------------
  **(new) → invited**        Onboarding initiated. Vendor contact email
                              extracted and validated. Invitation
                              dispatched and delivery confirmed.

  **invited → kyc**          Vendor has accessed the portal and
                              submitted the first required field or
                              document.

  **invited →                No portal access after 72 hours despite
  closed_unsuccessful**      two automated chases (24h, 48h). AP Manager
                              elects to close or withdraw. Alternatively,
                              vendor explicitly declines via reply.

  **kyc → bank_verify**      All required KYC documents submitted and
                              validated. KYC checks complete. KYC
                              disposition: proceed.

  **kyc → blocked**          One or more KYC checks returned a blocker:
                              missing or invalid document, sanctions
                              hit, PEP match requiring review, adverse
                              media above threshold, UBO resolution
                              incomplete, or field validation failure.

  **bank_verify → active**   Open banking verification succeeded: bank
                              account ownership confirmed against the
                              legal entity submitted in KYC. Account
                              details written to vendor record. ERP
                              vendor master write succeeded.

  **bank_verify → blocked**  Open banking verification failed: name
                              mismatch between KYC legal entity and
                              bank account holder, open banking timeout
                              with no retry success, country
                              unsupported, or vendor abandoned the
                              verification flow.

  **blocked → kyc**          KYC blocker resolved by vendor (new
                              document submitted, missing field
                              completed) or by AP Manager override
                              with recorded reason.

  **blocked → bank_verify**  Bank verification blocker resolved (new
                              account submitted, country resolved via
                              external process).

  **blocked →                Blocker is a hard stop (sanctions match
  closed_unsuccessful**      confirmed, AP Manager rejects the vendor,
                              vendor abandons with no response to
                              chases).
  -----------------------------------------------------------------------

**3. New Event Types**

The following event types are added to the event system. All other
event types defined in the core spec are unchanged. The planning
engine dispatches on these new types in addition to the existing
types.

  -------------------------------------------------------------------------------
  **Event type**                      **Trigger and payload**
  ----------------------------------- -------------------------------------------
  **onboarding_initiated**            An AP Manager has triggered a new vendor
                                      onboarding, either from the Gmail
                                      extension (forwarding an introductory
                                      email or an unknown-sender invoice) or
                                      from the Gmail Settings > Vendors route
                                      (manual initiation). Payload:
                                      {source, initiator_email, vendor_email,
                                      vendor_name_hint?, trigger_message_id?}.

  **vendor_portal_accessed**          The vendor has opened the onboarding
                                      portal link for the first time. Payload:
                                      {box_id, accessed_at, ip_address_hash,
                                      user_agent_hash}. The hashes are stored
                                      for audit but not the raw values.

  **vendor_submission_received**      The vendor has submitted data through
                                      the portal: a KYC field, a document
                                      upload, or a bank account entry. Payload:
                                      {box_id, submission_type, field_or_doc,
                                      submitted_at}.

  **kyc_check_completed**             A KYC check initiated by the agent
                                      (registry lookup, sanctions screen, PEP
                                      check, adverse media, UBO resolution)
                                      has returned. Payload: {box_id,
                                      check_type, result, provider_reference,
                                      completed_at}.

  **open_banking_verification_       The open banking provider has confirmed
  completed**                         the result of a bank verification.
                                      Payload: {box_id, provider,
                                      provider_reference, result, account_
                                      holder_name, verified_at}.

  **vendor_chase_due**                A scheduled chase timer has fired. The
                                      agent re-evaluates outstanding vendor
                                      submissions and dispatches the
                                      appropriate chase message. Payload:
                                      {box_id, chase_stage, attempt_number}.

  **ap_manager_decision_received**   An AP Manager has approved, overridden,
                                      or rejected a blocker through Slack or
                                      the Gmail extension. Payload: {box_id,
                                      decision, actor_email, timestamp,
                                      override_reason?}.

  **vendor_activated**                The vendor has been successfully written
                                      to the ERP vendor master. Payload:
                                      {box_id, erp_vendor_id, activated_at,
                                      ap_enabled: true}.
  -------------------------------------------------------------------------------

**4. Extended Action Space**

The following actions are added to the formal action space. The
complete action space is the union of the actions defined in the
core spec and the actions defined here. The two non-negotiable rules
apply to all new actions: every action is recorded to the Box
timeline before it executes, and the coordination engine never assumes
success.

Build status is indicated for each action: NEW means a net-new
component, EXISTING means the action reuses the current architecture
with new parameters, and ADAPTED means the existing implementation
requires modification for the new business logic.

**4.1 Invitation and Portal Actions**

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **dispatch_onboarding_invitation(vendor_email,   **DET**     Send the onboarding invitation email
  initiator, workspace_policy)**                               with a personalised portal link. The
                                                               link is signed and scoped to this
                                                               Box_id. Invitation content is a
                                                               deterministic template filled with
                                                               workspace fields (company name, AP
                                                               contact, required documents). Delivery
                                                               is confirmed via the Gmail send API.
                                                               ADAPTED from send_vendor_email.

  **generate_portal_link(box_id, expiry)**         **DET**     Generate a signed URL for the vendor
                                                               portal scoped to a single Box. Token
                                                               includes box_id, workspace_id, expiry
                                                               (default 14 days), and a HMAC
                                                               signature. Regenerated on each chase
                                                               with refreshed expiry. NEW.

  **monitor_portal_access(box_id)**                **DET**     Webhook handler that fires when the
                                                               portal is accessed. Converts the
                                                               access event into a
                                                               vendor_portal_accessed event. NEW.

  **classify_vendor_reply(email_content,           **LLM**     When a vendor replies to the
  box_id)**                                                    onboarding email rather than accessing
                                                               the portal, classify the reply as one
                                                               of: portal_access_issue, questions,
                                                               declines_onboarding, documents_
                                                               attached, scope_outside_v1 (e.g.
                                                               multi-entity request), other. Returns
                                                               {type, confidence, extracted_notes}.
                                                               Confidence threshold: 0.80. ADAPTED
                                                               from classify_vendor_response.
  -----------------------------------------------------------------------------------------------------

**4.2 KYC Actions**

The KYC action set is configurable per workspace. The workspace KYC
policy determines which actions are dispatched. The default policy
is document completeness only; deeper policies opt in to additional
checks.

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **classify_submitted_document(doc_content,      **LLM**      Classify a vendor-submitted document as
  doc_filename)**                                              one of: certificate_of_incorporation,
                                                               director_id, proof_of_address,
                                                               tax_registration, bank_letter,
                                                               ownership_chart, other, unreadable.
                                                               Returns {type, confidence, extracted_
                                                               jurisdiction?, extracted_identifier?}.
                                                               Confidence threshold: 0.85. NEW.

  **extract_vendor_fields(portal_submissions,     **LLM**      Extract structured fields from vendor
  documents)**                                                 portal submissions and document
                                                               content: legal_entity_name,
                                                               registration_number, jurisdiction,
                                                               director_names, registered_address,
                                                               tax_id, declared_UBOs. Returns
                                                               {fields, extraction_confidence_per_
                                                               field}. NEW.

  **validate_document_completeness(submitted_      **DET**     Deterministic check: are all required
  documents, required_set)**                                   documents present per workspace
                                                               policy? Returns {complete, missing:
                                                               [specific_document_names]}. Used to
                                                               generate the named-blocker chase
                                                               message. NEW.

  **run_company_registry_lookup(legal_entity_      **DET**     Query the jurisdiction\'s company
  name, registration_number, jurisdiction)**                   registry (via the configured KYC
                                                               provider) for an active company with
                                                               this name and number. Returns {found,
                                                               status, registered_address,
                                                               directors, provider_reference}.
                                                               Opt-in check (tier: basic or deeper).
                                                               NEW.

  **run_sanctions_screen(legal_entity_name,        **DET**     Screen the legal entity and declared
  director_names, jurisdiction)**                              directors against OFAC, UN, EU, UK HM
                                                               Treasury, and other configured
                                                               sanctions lists. Returns {hits, hit_
                                                               details, provider_reference,
                                                               screened_at}. Opt-in check (tier:
                                                               basic or deeper). NEW.

  **run_pep_check(director_names,                  **DET**     Screen declared directors and UBOs
  declared_UBOs)**                                             against politically exposed persons
                                                               databases. Returns {hits, hit_details,
                                                               provider_reference}. Opt-in check
                                                               (tier: full). NEW.

  **run_adverse_media_screen(legal_entity_name,    **DET**     Screen the legal entity and declared
  director_names)**                                            individuals against adverse media
                                                               sources via the configured provider.
                                                               Returns {hits, hit_summary, severity_
                                                               score, provider_reference}. Opt-in
                                                               check (tier: full). NEW.

  **resolve_ubo(legal_entity_name,                 **DET**     Resolve ultimate beneficial ownership
  registration_number, declared_UBOs,                          to the depth offered by the KYC
  provider_config)**                                           provider. Returns {ubo_chain, depth_
                                                               resolved, unresolved_branches,
                                                               provider_reference}. Opt-in check
                                                               (tier: full). If depth is insufficient,
                                                               Box is flagged for manual review.
                                                               NEW.

  **evaluate_kyc_disposition(kyc_results,          **DET**     Consolidate all KYC check results
  workspace_policy)**                                          against the workspace KYC policy.
                                                               Logic: (1) any sanctions hit →
                                                               disposition: hard_block, (2) any
                                                               confirmed PEP match → disposition:
                                                               ap_manager_review, (3) adverse media
                                                               severity above configured threshold
                                                               → disposition: ap_manager_review,
                                                               (4) UBO resolution incomplete beyond
                                                               configured depth → disposition:
                                                               manual_resolution, (5) all checks
                                                               clear or within tolerance →
                                                               disposition: proceed. Returns
                                                               {disposition, triggering_results,
                                                               required_actions}. Never calls
                                                               Claude. NEW.
  -----------------------------------------------------------------------------------------------------

**4.3 Bank Verification Actions**

Bank verification in v1 uses open banking exclusively. Micro-
deposits are not implemented in v1. This is a deliberate simplification:
open banking provides instant verification with name-match confidence,
whereas micro-deposits take 1-3 business days and require the vendor
to log back in to confirm amounts --- a coordination cost that
compounds in an already-multi-step onboarding.

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **check_open_banking_coverage(bank_country,      **DET**     Check whether the vendor\'s stated
  bank_identifier?)**                                          bank country is covered by the
                                                               configured open banking provider.
                                                               Returns {covered, provider,
                                                               unsupported_reason?}. If not covered:
                                                               Box is flagged for external bank
                                                               verification and exits the bank_verify
                                                               stage via the country-unsupported
                                                               branch. NEW.

  **dispatch_open_banking_link(box_id, vendor_     **DET**     Dispatch an open banking verification
  email, provider)**                                           link to the vendor via the portal. The
                                                               link routes through the configured
                                                               provider (e.g. TrueLayer, Plaid,
                                                               Tink) and on success returns account
                                                               holder name, account number / IBAN,
                                                               and a provider-signed confirmation.
                                                               Returns {link_dispatched, provider_
                                                               reference}. NEW.

  **evaluate_name_match(kyc_legal_entity_name,     **DET**     Compare the KYC-submitted legal entity
  open_banking_account_holder)**                               name against the open banking account
                                                               holder name. Uses a fuzzy match with a
                                                               workspace-configurable threshold
                                                               (default: 85% token-sort similarity).
                                                               Common abbreviations (Ltd, LLC, Inc,
                                                               GmbH) are normalised before
                                                               comparison. Returns {match_score,
                                                               passed, normalisation_applied,
                                                               manual_review_required}. NEW.

  **evaluate_bank_verification_disposition(ob_     **DET**     Consolidate the open banking
  result, name_match_result)**                                 verification result and the name match
                                                               result. Disposition: proceed (name
                                                               match passed, account ownership
                                                               confirmed), ap_manager_review (name
                                                               match score between configured manual-
                                                               review band and auto-pass threshold),
                                                               hard_block (open banking returned
                                                               failure), retry (open banking timeout
                                                               or provider error, retry eligible).
                                                               NEW.
  -----------------------------------------------------------------------------------------------------

**4.4 ERP Vendor Master Actions**

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **check_vendor_duplicate(legal_entity_name,      **DET**     Check whether an active vendor already
  registration_number, tax_id, erp)**                          exists in the ERP vendor master
                                                               matching this legal entity. Match on
                                                               registration number (strong), tax ID
                                                               (strong), or legal entity name (weak,
                                                               requires human confirmation). Returns
                                                               {duplicate_found, match_strength,
                                                               existing_vendor_id?}. Duplicate
                                                               blocks onboarding and surfaces to AP
                                                               Manager. ADAPTED from
                                                               check_duplicate.

  **draft_vendor_master_record(kyc_fields,        **DET**     Construct the vendor master record from
  bank_fields, erp_schema)**                                   validated KYC and bank fields, mapped
                                                               to the target ERP\'s vendor schema.
                                                               Returns {vendor_record_draft, erp_
                                                               format, warnings}. Warnings include
                                                               any fields where the ERP requires a
                                                               value that KYC did not capture (e.g.
                                                               payment terms, GL code defaults) and
                                                               the workspace default is applied.
                                                               NEW.

  **validate_vendor_master_record(vendor_draft,    **DET**     Validate the draft: (1) all required
  erp)**                                                       ERP fields populated, (2) GL and cost
                                                               centre codes valid, (3) tax codes
                                                               valid, (4) bank account format matches
                                                               the country (IBAN format, SWIFT
                                                               format, ACH routing format where
                                                               applicable). Returns {valid, failures}.
                                                               Must pass before writing. NEW.

  **write_vendor_to_erp(vendor_record, erp)**      **DET**     Write the validated vendor record to
                                                               the ERP vendor master with AP-enabled
                                                               status. Returns {success, erp_vendor_
                                                               id?, error?}. On success: stores the
                                                               ERP vendor ID in the Box record. On
                                                               failure: does not retry automatically
                                                               --- marks Box as exception and alerts
                                                               AP Manager with specific ERP error.
                                                               ADAPTED from post_bill.

  **pre_write_validate_vendor(box_id, erp)**       **DET**     Re-validate before writing: KYC
                                                               disposition still proceed, bank
                                                               verification still proceed, duplicate
                                                               check still clear, approval still
                                                               valid. Returns {valid, failures}. Must
                                                               pass immediately before
                                                               write_vendor_to_erp. ADAPTED from
                                                               pre_post_validate.
  -----------------------------------------------------------------------------------------------------

**4.5 Communication Actions**

  -----------------------------------------------------------------------------------------------------
  **Action**                                       **Layer**   **Description**
  ------------------------------------------------ ----------- ----------------------------------------
  **send_vendor_chase(box_id, vendor_email,        **DET**     Send a templated chase email to the
  chase_stage, missing_items)**                                vendor. Content is constructed
                                                               deterministically from the Box state
                                                               --- specifically names the missing
                                                               documents or fields. Chase stages:
                                                               first_chase (24h), second_chase (48h),
                                                               final_chase (72h pre-escalation).
                                                               ADAPTED from send_vendor_email.

  **send_slack_onboarding_exception(box_id,        **DET**     Alert the AP Manager to a blocker
  channel, blocker_summary)**                                  requiring human resolution. Includes
                                                               the specific blocker (named document,
                                                               check type, error reason) and
                                                               resolution options. ADAPTED from
                                                               send_slack_exception.

  **send_slack_vendor_activated(box_id, channel,   **DET**     Post a confirmation to the AP channel
  activation_summary)**                                        when a vendor has been activated in
                                                               the ERP. Includes ERP vendor ID,
                                                               entity name, AP-enabled status.
                                                               ADAPTED from send_slack_override_
                                                               window.

  **generate_vendor_summary(kyc_fields,           **LLM**      Generate a plain-language summary of
  bank_fields, ap_manager_review_reason)**                     the onboarding for the AP Manager\'s
                                                               approval message when a blocker
                                                               requires review (PEP match, adverse
                                                               media, name-match score in review
                                                               band). Input is fully structured ---
                                                               Claude generates human-readable text,
                                                               not the decision. DID-WHY-NEXT format.
                                                               Maximum 200 words. If this call
                                                               fails, a template message is used.
                                                               ADAPTED from generate_exception_
                                                               reason.

  **draft_vendor_clarification_response(vendor_    **LLM**     For complex vendor questions requiring
  message, box_state)**                                        a contextual reply (e.g. "what format
                                                               should the ownership chart be in?"),
                                                               call Claude to draft a response.
                                                               Always staged for AP Manager review
                                                               before sending. Never sent
                                                               autonomously. ADAPTED from
                                                               draft_vendor_response.
  -----------------------------------------------------------------------------------------------------

**5. Planning Engine --- Vendor Onboarding**

The planning engine handles vendor onboarding events using the same
dispatch architecture as the core spec. New event types map to new
planning handlers. The planning logic for existing event types is
extended with onboarding-specific branches without modifying the
existing handlers.

**5.1 Planning for onboarding_initiated**

  -----------------------------------------------------------------------
  **Step**                  **Planning logic**
  ------------------------- ---------------------------------------------
  **1. Create Box**         create_box(\'vendor_onboarding\', {stage:
                            \'invited\', initiator_email, vendor_email}).

  **2. Duplicate check**    check_vendor_duplicate (early, using any
                            hints from the trigger message). If strong
                            duplicate: apply_label(\'Review Required\'),
                            send_slack_onboarding_exception, stop.

  **3. Dispatch             dispatch_onboarding_invitation.
  invitation**              generate_portal_link.

  **4. Schedule chases**    Set chase timers: first_chase in 24h,
                            second_chase in 48h, final_chase in 72h.

  **5. Move to invited      move_box_stage(\'invited\').
  stage**                   set_waiting_condition(vendor_portal_accessed).
  -----------------------------------------------------------------------

**5.2 Planning for vendor_portal_accessed**

  -----------------------------------------------------------------------
  **Step**                  **Planning logic**
  ------------------------- ---------------------------------------------
  **1. Clear chase          Cancel outstanding chase timers. The vendor
  timers**                  has engaged. Replace with per-stage chase
                            schedules that fire only if a stage stalls.

  **2. Move to kyc stage**  move_box_stage(\'kyc\'). post_timeline_entry
                            with access timestamp.

  **3. Wait for             set_waiting_condition(vendor_submission_
  submissions**             received). Set a submission-stall timer:
                            fires if no new submission received within
                            48h of last submission.
  -----------------------------------------------------------------------

**5.3 Planning for vendor_submission_received**

  -----------------------------------------------------------------------
  **Step**                  **Planning logic**
  ------------------------- ---------------------------------------------
  **1. Classify             If submission is a document:
  submission**              classify_submitted_document. If classification
                            confidence below threshold or type is
                            unreadable: post_timeline_entry,
                            send_vendor_chase requesting a re-upload with
                            specific guidance. Stop.

  **2. Extract fields**     extract_vendor_fields (running on both portal
                            field submissions and document OCR).

  **3. Check completeness** validate_document_completeness against the
                            workspace KYC policy\'s required set. If
                            incomplete: post_timeline_entry with named
                            missing items. Agent waits for further
                            submissions --- chase fires per timer.

  **4. Run KYC checks (on   Dispatch all KYC checks permitted by the
  completeness)**           workspace policy tier:
                              Tier: completeness → skip all checks.
                              Tier: basic → run_company_registry_lookup,
                                run_sanctions_screen.
                              Tier: full → basic + run_pep_check,
                                run_adverse_media_screen, resolve_ubo.
                            Each dispatched check returns asynchronously
                            as a kyc_check_completed event.
  -----------------------------------------------------------------------

**5.4 Planning for kyc_check_completed**

  -----------------------------------------------------------------------
  **Step**                  **Planning logic**
  ------------------------- ---------------------------------------------
  **1. Check completeness   Store the check result on the Box. If any
  of check set**            configured check is still outstanding: wait.
                            If all checks complete: proceed.

  **2. Evaluate             evaluate_kyc_disposition. Disposition:
  disposition**             proceed, hard_block, ap_manager_review, or
                            manual_resolution.

  **3a. disposition:         check_vendor_duplicate again with validated
  proceed**                 KYC fields. If clear: move_box_stage(\'bank_
                            verify\'). check_open_banking_coverage.

  **3b. disposition:         post_timeline_entry with triggering check
  hard_block**              result (e.g. sanctions hit). move_box_stage
                            (\'closed_unsuccessful\'). Alert AP Manager
                            with a final summary. No chase. No retry.

  **3c. disposition:         generate_vendor_summary. send_slack_
  ap_manager_review**       onboarding_exception with Approve / Override
                            / Reject buttons and the triggering check
                            details. move_box_stage(\'blocked\').
                            set_waiting_condition(ap_manager_decision_
                            received).

  **3d. disposition:         post_timeline_entry. send_slack_onboarding_
  manual_resolution**       exception asking the AP Manager or compliance
                            team to complete the check (e.g. UBO chain
                            branch manually resolved). move_box_stage
                            (\'blocked\').
  -----------------------------------------------------------------------

**5.5 Planning for bank_verify branch**

  -----------------------------------------------------------------------
  **Step**                  **Planning logic**
  ------------------------- ---------------------------------------------
  **1. Coverage check**     check_open_banking_coverage. If not covered:
                            post_timeline_entry (\"bank country X not
                            covered by provider Y\"), send_slack_
                            onboarding_exception for manual bank
                            verification, move_box_stage(\'blocked\')
                            with resolution path documented.

  **2. Dispatch link**      dispatch_open_banking_link. Portal displays
                            the link to the vendor. Agent waits for
                            open_banking_verification_completed event.

  **3. On completion**      evaluate_name_match between KYC legal entity
                            name and open banking account holder.
                            evaluate_bank_verification_disposition.

  **4a. disposition:         pre_write_validate_vendor. draft_vendor_
  proceed**                 master_record. validate_vendor_master_record.
                            write_vendor_to_erp. move_box_stage
                            (\'active\'). send_slack_vendor_activated.

  **4b. disposition:         generate_vendor_summary including the name-
  ap_manager_review**       match score and the two names being
                            compared. send_slack_onboarding_exception
                            with Approve / Reject. move_box_stage
                            (\'blocked\').

  **4c. disposition:         post_timeline_entry. apply_label(\'Review
  hard_block**              Required\'). send_slack_onboarding_exception.
                            move_box_stage(\'blocked\'). AP Manager
                            determines next steps (re-invitation with a
                            different account, close unsuccessful).

  **4d. disposition:         Re-dispatch the open banking link up to 2
  retry**                   times with 4h spacing. On third failure:
                            treat as hard_block.
  -----------------------------------------------------------------------

**5.6 Planning for ap_manager_decision_received**

  -----------------------------------------------------------------------
  **Decision**              **Plan**
  ------------------------- ---------------------------------------------
  **Approve (KYC review     Override the KYC disposition with the AP
  blocker)**                Manager\'s approval. Log override_reason to
                            timeline. Route to bank_verify. Same flow as
                            kyc disposition: proceed.

  **Approve (bank verify    Override the name-match disposition. Log
  review blocker)**         override_reason. Proceed to ERP write.

  **Override with recorded  Same as Approve. override_reason logged to
  reason**                  timeline and sent to the Operations Console
                            quality dashboard (internal review).

  **Reject**                move_box_stage(\'closed_unsuccessful\').
                            post_timeline_entry with rejection reason.
                            Vendor is notified with a standard
                            unsuccessful-onboarding email.
  -----------------------------------------------------------------------

**5.7 Planning for vendor_chase_due (timer_fired)**

  -----------------------------------------------------------------------
  **Timer**                 **Plan on firing**
  ------------------------- ---------------------------------------------
  **first_chase (24h no     send_vendor_chase(first_chase). Chase names
  portal access)**          any outstanding items. Reset timer for
                            second_chase in 24h.

  **second_chase (48h no    send_vendor_chase(second_chase). Chase is
  portal access)**          more direct. Reset for final_chase in 24h.

  **final_chase (72h no     send_slack_onboarding_exception to AP
  portal access)**          Manager. AP Manager decides: extend,
                            withdraw, or escalate. Agent does not auto-
                            close; a human decides whether to continue
                            pursuing this vendor.

  **kyc_submission_stall    Fires if 48h pass with no new submission
  (48h)**                   after a partial submission. Chase names the
                            specific outstanding items.

  **bank_verify_stall       Fires if 48h pass after the open banking
  (48h)**                   link was dispatched with no completion.
                            Chase the vendor through the portal.
  -----------------------------------------------------------------------

**6. The Complete Vendor Onboarding Lifecycle**

This section traces a single onboarding from initiation to
activation, and then shows the variants for the main branches. This
is the canonical reference for how the pipeline moves.

**6.1 Happy Path (KYC tier: completeness, EU vendor, open banking
supported)**

  -----------------------------------------------------------------------
  **Step**                 **What happens**
  ------------------------ ----------------------------------------------
  **1**                    AP Manager forwards an introductory email
                           from new-vendor@example.com to onboard@
                           workspace. Gmail Pub/Sub fires. Listener
                           enqueues onboarding_initiated event.

  **2**                    Planning engine receives event.
                           check_vendor_duplicate: no existing vendor
                           matches.

  **3**                    dispatch_onboarding_invitation: invitation
                           sent with portal link. Portal link valid
                           14 days. Chase timers scheduled.
                           move_box_stage(\'invited\').

  **4**                    3 hours later: vendor opens portal link.
                           vendor_portal_accessed event fired. Chase
                           timers cancelled. move_box_stage(\'kyc\').

  **5**                    Vendor submits: legal entity name, company
                           number, jurisdiction, director name,
                           certificate of incorporation PDF.
                           vendor_submission_received event fires per
                           submission.

  **6**                    classify_submitted_document: identifies the
                           PDF as certificate_of_incorporation
                           (confidence 0.94). extract_vendor_fields
                           pulls structured fields from portal and
                           document.

  **7**                    validate_document_completeness: all required
                           documents present per the completeness
                           policy (COI + director ID).

  **8**                    Vendor has also submitted director ID.
                           Completeness check passes. Workspace policy
                           tier: completeness --- no further KYC
                           checks dispatched.

  **9**                    evaluate_kyc_disposition: proceed.
                           check_vendor_duplicate with validated
                           fields: still clear.

  **10**                   check_open_banking_coverage: EU vendor, bank
                           in Germany. Covered by TrueLayer.
                           move_box_stage(\'bank_verify\').

  **11**                   dispatch_open_banking_link. Portal displays
                           verification link to vendor.

  **12**                   Vendor completes open banking flow. Provider
                           returns: account_holder_name \"Muster GmbH\",
                           IBAN DE89 3704 0044 0532 0130 00, provider_
                           reference TL-2026-77421. open_banking_
                           verification_completed event fires.

  **13**                   evaluate_name_match: KYC entity \"Muster
                           GmbH\" vs bank holder \"Muster GmbH\". Match
                           score 1.00 after GmbH normalisation. Passed.

  **14**                   evaluate_bank_verification_disposition:
                           proceed.

  **15**                   pre_write_validate_vendor: all conditions
                           clear.

  **16**                   draft_vendor_master_record: vendor record
                           assembled in the target ERP\'s format.
                           validate_vendor_master_record: passes.

  **17**                   write_vendor_to_erp: ERP confirms write.
                           Vendor ID: VND-2026-04412. AP-enabled: true.

  **18**                   move_box_stage(\'active\'). post_timeline_
                           entry --- DID: \"Wrote vendor VND-2026-04412
                           to ERP. AP-enabled.\" WHY: \"KYC
                           completeness passed. Open banking
                           verification passed (Muster GmbH account
                           holder matched KYC entity, TrueLayer
                           reference TL-2026-77421).\" NEXT: \"Vendor
                           capable of submitting invoices for
                           processing.\"

  **19**                   send_slack_vendor_activated to AP channel.
                           Box archived.
  -----------------------------------------------------------------------

**6.2 Full KYC Tier with Adverse Media Flag**

Same as the happy path through step 7. Workspace policy tier: full.
At step 8: in parallel the agent dispatches run_company_registry_
lookup, run_sanctions_screen, run_pep_check, run_adverse_media_
screen, resolve_ubo.

-   Company registry: active. Sanctions: no hits. PEP: no hits. UBO:
    resolved to direct shareholders, one declared UBO confirmed.

-   Adverse media: 2 hits returned. Severity score 0.72, above the
    workspace threshold (0.60). Hit summaries include regulatory
    action against a former director.

-   evaluate_kyc_disposition: ap_manager_review.

-   generate_vendor_summary: summary includes the hits and their
    severity scoring.

-   send_slack_onboarding_exception to AP Manager with Approve /
    Override / Reject buttons and the adverse media excerpts.
    move_box_stage(\'blocked\').

-   AP Manager reviews, determines the hit relates to a former
    director no longer with the company, approves with
    override_reason \"Former director not in current submission.\"

-   Override recorded to timeline and quality dashboard. Flow
    continues from bank_verify.

**6.3 Bank Country Not Supported**

Same as the happy path through step 9. At step 10: vendor is in a
country where the configured open banking provider has no coverage.

-   check_open_banking_coverage returns {covered: false, unsupported_
    reason: \"country X not covered by TrueLayer\"}.

-   post_timeline_entry: \"Bank verification must be completed
    externally. Country X not covered by configured open banking
    provider.\"

-   send_slack_onboarding_exception to AP Manager with guidance.
    move_box_stage(\'blocked\').

-   AP Manager runs their existing bank verification process
    (typically bank letter). Once complete, AP Manager uses the
    Gmail extension to mark bank verification as externally_
    confirmed with reason and reference. Flow resumes from step 15
    with a manually_verified flag on the Box.

**6.4 Vendor Fails to Respond**

Same as step 1--3. At step 4: 24h passes with no portal access.
first_chase fires. At 48h: second_chase fires. At 72h:
send_slack_onboarding_exception to AP Manager.

-   AP Manager elects to close unsuccessful.

-   move_box_stage(\'closed_unsuccessful\'). post_timeline_entry
    with close reason. Invitation token invalidated.

**6.5 Name Mismatch at Bank Verification**

Same as happy path through step 12. At step 13: KYC entity \"Muster
GmbH\", open banking account holder \"J Muster\" (personal account
detected).

-   evaluate_name_match: match score 0.42 after normalisation.
    Below review band (0.70). Hard block.

-   evaluate_bank_verification_disposition: hard_block.

-   apply_label(\'Review Required\'). send_slack_onboarding_exception
    with the two names side by side. move_box_stage(\'blocked\').

-   AP Manager contacts vendor, requests business account
    verification. Vendor submits new open banking verification with
    business account. Flow resumes from step 13.

**7. LLM/Deterministic Boundary --- Onboarding Extension**

The boundary defined in the core spec applies without modification.
This section specifies the Claude calls specific to the vendor
onboarding pipeline.

  ------------------------------------------------------------------------------
  **LLM action**                    **Inputs, constraints, budget**
  --------------------------------- --------------------------------------------
  **classify_vendor_reply**         Input: email headers, plain text body (first
                                    2,000 tokens), box context summary. System
                                    prompt: return JSON only with type enum and
                                    confidence. Confidence threshold: 0.80.
                                    Token budget: 2,000 tokens input.

  **classify_submitted_document**   Input: document OCR text (up to 3,000
                                    tokens) plus filename. System prompt: return
                                    JSON only with document type enum,
                                    confidence, and extracted jurisdiction /
                                    identifier if present. Initial confidence
                                    threshold: 0.85. To be calibrated against a
                                    sample of ≥300 anonymised onboarding
                                    documents across jurisdictions before
                                    go-live. Token budget: 3,000 tokens input.

  **extract_vendor_fields**         Input: portal submissions (structured)
                                    plus document OCR (unstructured), up to
                                    4,000 tokens combined. System prompt:
                                    return JSON with the vendor field schema.
                                    Do not infer values not present. Do not
                                    normalise jurisdiction names. Return values
                                    exactly as they appear. Token budget: 4,000
                                    tokens input.

  **generate_vendor_summary**       Input: fully structured KYC results, bank
                                    verification result, and triggering blocker
                                    details. System prompt: write one paragraph
                                    in DID-WHY-NEXT format. Maximum 200 words.
                                    Factual and precise. No speculation. Token
                                    budget: 1,000 tokens input. If this call
                                    fails: use a template message constructed
                                    from structured data.

  **draft_vendor_clarification_     Input: vendor message, box state, AP
  response**                        Manager direction. System prompt: draft a
                                    professional, factual reply. Maximum 300
                                    words. Always staged for AP Manager review.
                                    Never sent autonomously. Token budget:
                                    3,000 tokens input.
  ------------------------------------------------------------------------------

> *The KYC disposition (evaluate_kyc_disposition), the bank
> verification disposition (evaluate_bank_verification_disposition),
> the vendor master record construction (draft_vendor_master_
> record), and all validation steps are always deterministic. Claude
> never touches these steps. This is non-negotiable.*

**8. The Vendor Portal**

The vendor portal is the surface the vendor interacts with. It is
the only non-Gmail, non-Slack surface in the Clearledgr ecosystem.
It exists because the vendor is not a Clearledgr customer, does not
have the Gmail extension installed, and will not tolerate account
creation to complete an onboarding. The portal must be frictionless,
familiar, and minimal.

**8.1 Portal Properties**

-   **Link-authenticated.** The vendor accesses the portal via a
    signed link received in the onboarding invitation and in each
    chase. No password. No account creation. The token is scoped
    to a single Box and expires after 14 days (configurable).

-   **Progressive disclosure.** The portal shows the vendor exactly
    what is needed at each step. Document requirements are derived
    from the workspace KYC policy. Fields are derived from the ERP
    vendor master schema. Nothing is asked that the agent cannot use.

-   **Upload-safe.** Documents are uploaded directly to encrypted
    object storage. The portal never stores documents on the
    application layer. OCR and classification happen asynchronously
    --- the vendor is not blocked waiting for a classification
    result.

-   **Open banking handoff.** When the bank verification stage is
    reached, the portal renders the open banking provider\'s
    embedded flow inline. The vendor does not leave the portal
    context. The provider returns a signed confirmation that the
    agent consumes.

-   **Status transparency.** The vendor sees exactly which items
    are complete, which are outstanding, and what the next action
    is. No hidden state. If a KYC check is running, the portal
    says so.

-   **Mobile-first.** Most vendors access this from a phone. The
    portal is responsive and tested on phones.

**8.2 Portal Surfaces**

  -----------------------------------------------------------------------
  **Surface**               **Purpose**
  ------------------------- ---------------------------------------------
  **Welcome**               Identifies the customer (\"You\'re being
                            onboarded as a vendor by {Workspace}\"),
                            shows the expected time commitment, lists
                            the documents the vendor should have ready.

  **Business details**      Form for legal entity name, registration
                            number, jurisdiction, registered address,
                            tax ID, director names, declared UBOs
                            (where KYC tier requires).

  **Document uploads**      Named upload slots per required document.
                            Shows acceptance state (validating /
                            accepted / rejected with reason) per
                            document.

  **Bank verification**     Embedded open banking flow. Post-completion
                            shows: verified account holder, masked
                            account number, verification timestamp.

  **Status**                Shows current stage, outstanding items,
                            last update, and (once active) confirmation
                            that the vendor is set up and can submit
                            invoices.
  -----------------------------------------------------------------------

**9. Box State --- Onboarding-Specific Fields**

The following fields extend the Box state object defined in the core
spec. All existing fields apply to the vendor_onboarding pipeline
without modification.

  -----------------------------------------------------------------------------
  **Field**                      **Type and purpose**
  ------------------------------ ----------------------------------------------
  **pipeline:                    Identifies this Box as a vendor onboarding
  \'vendor_onboarding\'**        pipeline Box.

  **vendor_contact_email**       The email address the onboarding was
                                 dispatched to.

  **vendor_legal_entity_name**   Set after extract_vendor_fields. The
                                 canonical legal entity name.

  **vendor_registration_          Set after extract_vendor_fields.
  number**                       

  **vendor_jurisdiction**        Set after extract_vendor_fields.

  **kyc_policy_tier_applied**    Enum: completeness, basic, full. Derived
                                 from the workspace policy at the time the
                                 Box was created.

  **submitted_documents**        JSONB. Array of {document_type, filename,
                                 storage_reference, classification_
                                 confidence, validation_state}.

  **kyc_check_results**          JSONB. Per-check results:
                                 registry_lookup, sanctions, pep,
                                 adverse_media, ubo. Each with timestamp,
                                 provider reference, and outcome.

  **kyc_disposition**            Enum: proceed, hard_block,
                                 ap_manager_review, manual_resolution.
                                 Null until evaluate_kyc_disposition has
                                 run.

  **open_banking_result**        JSONB. Provider, provider reference,
                                 account_holder_name, account_identifier,
                                 verification timestamp.

  **name_match_result**          JSONB. kyc_entity_name,
                                 open_banking_holder_name, match_score,
                                 normalisation_applied, disposition.

  **bank_verification_           Enum: proceed, ap_manager_review,
  disposition**                  hard_block, retry, externally_confirmed.

  **ap_manager_overrides**       JSONB array. Each override:
                                 {override_type, actor_email, reason,
                                 timestamp}. Used for audit and quality
                                 dashboard.

  **vendor_master_draft**        JSONB. The draft record ready for write
                                 to ERP.

  **erp_vendor_id**              String. The ERP\'s assigned vendor ID.
                                 Null until write_vendor_to_erp succeeds.

  **closed_unsuccessful_         String. Populated when Box ends in
  reason**                       closed_unsuccessful. Records the reason
                                 for audit purposes.
  -----------------------------------------------------------------------------

**10. ERP Connector Implementations**

The vendor onboarding pipeline is specified ERP-agnostic. The
existing ERP Connector Layer interface is unchanged --- all new
actions call the same interface methods. Each ERP connector
implements the following operations against its ERP-native APIs.

  ------------------------------------------------------------------------------------
  **ERP operation**                     **Implementation detail**
  ------------------------------------- ----------------------------------------------
  **check_vendor_duplicate**            Queries the ERP vendor master for an
                                        existing vendor matching on registration
                                        number, tax ID, or legal entity name.
                                        SAP: Business Partner search via S/4HANA
                                        OData. NetSuite: SuiteQL query on Vendor
                                        records. Xero: GET /Contacts filtered by
                                        TaxNumber or Name. QuickBooks: Query API
                                        on Vendor entity. Returns the match
                                        strength (registration number = strong,
                                        tax ID = strong, name = weak).

  **write_vendor_to_erp**               Writes the new vendor record to the ERP
                                        vendor master with AP-enabled status.
                                        SAP: Business Partner create via S/4HANA
                                        OData. NetSuite: POST /vendor via REST
                                        API. Xero: POST /Contacts with IsSupplier
                                        = true. QuickBooks: POST /vendor via QBO
                                        API. Each connector translates the
                                        vendor_master_draft into the ERP-native
                                        format. Returns the ERP-assigned vendor
                                        ID.

  **lookup_vendor_schema**              Returns the ERP\'s required fields and
                                        value constraints for vendor master
                                        records. Used by draft_vendor_master_
                                        record to know which fields are required
                                        and which defaults apply per workspace.
                                        Each ERP connector exposes its vendor
                                        schema in a normalised form.
  ------------------------------------------------------------------------------------

> *v1 ships the vendor onboarding pipeline ERP-agnostic across SAP,
> NetSuite, Xero, and QuickBooks. No single ERP is the lead. The
> pipeline is testable against any configured ERP sandbox. The
> customer\'s choice of ERP determines which connector is activated
> for their workspace.*

**11. KYC Provider Integration**

KYC checks beyond document completeness are delegated to a
configured KYC provider. v1 targets providers with the following
capabilities: company registry lookup (or a meta-provider that
covers major jurisdictions), sanctions screening (OFAC, UN, EU,
UK HM Treasury minimum), PEP screening, adverse media, and UBO
resolution.

Candidate providers to evaluate pre-build: ComplyAdvantage,
Trulioo, Sumsub, Veriff (document KYC only), Middesk (US-focused),
Persona. The build assumes a single provider per workspace,
configurable during onboarding. The provider interface is
standardised; swapping providers is a configuration change, not a
code change.

  ------------------------------------------------------------------------------------
  **KYC operation**                     **Provider capability required**
  ------------------------------------- ----------------------------------------------
  **company_registry_lookup**           Active-company lookup by
                                        name + registration number + jurisdiction.
                                        Returns registered address, directors,
                                        status.

  **sanctions_screen**                  Entity and individual screening against
                                        OFAC, UN, EU, UK HM Treasury, and
                                        additional lists configured per workspace.

  **pep_check**                         Individual screening against PEP
                                        databases with severity scoring.

  **adverse_media_screen**              Entity and individual screening against
                                        adverse media sources with severity
                                        scoring.

  **ubo_resolution**                    Beneficial ownership chain resolution to
                                        the depth offered by the provider. Deep
                                        chains are flagged as unresolved, not
                                        inferred.
  ------------------------------------------------------------------------------------

**12. Open Banking Provider Integration**

v1 uses a single open banking provider configurable per workspace.
Candidate providers: TrueLayer, Tink (EU + UK), Plaid (US + CA +
EU), GoCardless (EU/UK bank verification). The build assumes an
abstracted provider interface; swapping providers is a configuration
change.

  ------------------------------------------------------------------------------------
  **Open banking operation**            **Provider capability required**
  ------------------------------------- ----------------------------------------------
  **check_coverage**                    Return whether a given country and bank
                                        identifier is supported.

  **initiate_verification**             Generate a verification session / link
                                        that the vendor completes to confirm
                                        account ownership.

  **verification_result**               Webhook delivering the verification
                                        result: account holder name, account
                                        identifier, signed confirmation.

  **result_signature_verification**     Verify the provider\'s signature on the
                                        verification result to prevent
                                        tampering.
  ------------------------------------------------------------------------------------

**13. Performance Requirements**

  -----------------------------------------------------------------------------------
  **Stage**                            **Target**
  ------------------------------------ ----------------------------------------------
  **onboarding_initiated to            \< 2 minutes (target). Invitation dispatched
  invitation delivered**               and delivery confirmed.

  **classify_submitted_document       \< 8 seconds
  (LLM)**                             

  **extract_vendor_fields (LLM)**      \< 10 seconds (combined portal + document
                                       OCR content)

  **validate_document_completeness    \< 200ms
  (DET)**                             

  **run_company_registry_lookup**      \< 10 seconds (provider-dependent)

  **run_sanctions_screen**             \< 5 seconds

  **run_pep_check**                    \< 5 seconds

  **run_adverse_media_screen**         \< 15 seconds

  **resolve_ubo**                      \< 30 seconds (provider-dependent; deep
                                       chains take longer)

  **evaluate_kyc_disposition (DET)**   \< 100ms

  **check_open_banking_coverage**      \< 1 second

  **dispatch_open_banking_link**       \< 3 seconds

  **open banking completion            No SLA --- waits on vendor. Timer stops
  (vendor)**                           during wait.

  **evaluate_name_match (DET)**        \< 200ms

  **evaluate_bank_verification_        \< 100ms
  disposition (DET)**                 

  **draft_vendor_master_record        \< 500ms
  (DET)**                             

  **validate_vendor_master_record     \< 2 seconds (includes ERP GL / cost
  (DET)**                              centre validation)

  **write_vendor_to_erp**              \< 5 seconds (cloud ERPs). \< 10 seconds
                                       (SAP).
  -----------------------------------------------------------------------------------

Stages waiting on external input (vendor submissions, provider
returns) are outside the SLA clock. The clock stops when a
waiting_condition is set and restarts when the condition is cleared.

**14. Audit Trail Export**

Every onboarding Box carries the full chain-of-custody on its
timeline by design. The audit export feature surfaces this chain as
a structured deliverable for internal audit, external audit, and
regulator requests. Onboarding audit is especially material because
KYC records are typically subject to statutory retention periods
(5--10 years depending on jurisdiction).

  ---------------------------------------------------------------------------
  **Field**                       **Content**
  ------------------------------- ------------------------------------------
  **Trigger**                     Source type, initiator, trigger message
                                  ID, received_at.

  **Vendor-submitted data**       Every portal submission with timestamp,
                                  field or document, and (for documents)
                                  storage reference and content hash for
                                  tamper-evidence.

  **KYC checks**                  Per-check: provider, timestamp, inputs,
                                  outputs, provider reference, raw
                                  response hash.

  **KYC disposition**             Deterministic rule applied,
                                  disposition, triggering results.

  **Bank verification**           Open banking provider, provider
                                  reference, account holder name,
                                  name-match score and comparison
                                  inputs, disposition.

  **Approvals and overrides**     Every AP Manager decision with actor,
                                  timestamp, reason, override type.

  **ERP write**                   Vendor record written, ERP vendor ID,
                                  write timestamp, writer (agent +
                                  approving human), pre-write validation
                                  result.

  **Close reason (if              Close reason, actor, timestamp.
  unsuccessful)**                 

  **Export format**               PDF (narrative form for auditor
                                  review) and JSONL (structured form
                                  for integration with GRC tools).
                                  Both are generated from the same Box
                                  timeline --- the PDF is a rendering
                                  of the JSONL, not a separate record.
  ---------------------------------------------------------------------------

Audit exports are scoped to a time range and an optional filter
(vendor, disposition, close reason, KYC tier). The JSONL export is
signed with a per-workspace signing key so auditors can verify it
was generated by Clearledgr and not modified after export. Audit
exports are themselves logged to a workspace-level audit log.

**15. Build Plan**

The following components require engineering work. Components are
listed in dependency order. The portal, KYC orchestration,
calculation / evaluation logic, and ERP-agnostic write layer are
testable without any external provider connection. Provider
integrations require sandbox credentials from the selected KYC and
open banking providers.

  ---------------------------------------------------------------------------
  **Component**       **Estimate**   **Notes**
  ------------------- -------------- ----------------------------------------
  **Vendor portal**   **5--7 days**  Hosted web portal: link auth,
                                     progressive disclosure, document
                                     uploads to encrypted storage, embedded
                                     open banking flow, mobile-first. Built
                                     on existing Clearledgr web stack.
                                     Design alignment with Gmail extension
                                     required. Highest design surface in
                                     the product.

  **Document          **2--3 days**  classify_submitted_document,
  classification and                 extract_vendor_fields, validate_
  field extraction**                 document_completeness. Adapted from
                                     invoice classification and extraction.
                                     New prompts and schema. Testable with
                                     anonymised document samples.

  **KYC orchestration **3--4 days**  run_company_registry_lookup,
  layer**                            run_sanctions_screen, run_pep_check,
                                     run_adverse_media_screen, resolve_ubo.
                                     Provider-abstracted interface. Single
                                     provider implementation in v1 (chosen
                                     from ComplyAdvantage / Sumsub /
                                     similar). Unit tests with mocked
                                     provider responses.

  **KYC disposition   **1--2 days**  evaluate_kyc_disposition. Purely
  engine**                           deterministic. Full test coverage
                                     required --- all disposition paths,
                                     edge cases (missing results, partial
                                     results, threshold boundary).

  **Open banking      **3--4 days**  check_open_banking_coverage,
  integration**                      dispatch_open_banking_link,
                                     verification result webhook, signature
                                     verification. Single provider in v1.
                                     Requires provider sandbox.

  **Name-match and    **1--2 days**  evaluate_name_match (with entity
  bank disposition                   normalisation rules), evaluate_bank_
  engine**                           verification_disposition. Purely
                                     deterministic. Full test coverage
                                     across normalisation cases.

  **ERP vendor        **2--3 days    check_vendor_duplicate,
  master              per ERP**      draft_vendor_master_record,
  operations**                       validate_vendor_master_record,
                                     write_vendor_to_erp,
                                     pre_write_validate_vendor. ERP-agnostic
                                     core with per-ERP format translation
                                     in the connector layer. Four ERPs in
                                     v1 (SAP, NetSuite, Xero, QuickBooks).

  **Planning engine   **1--2 days**  New handlers for onboarding_initiated,
  extension**                        vendor_portal_accessed,
                                     vendor_submission_received,
                                     kyc_check_completed,
                                     open_banking_verification_completed,
                                     vendor_chase_due,
                                     ap_manager_decision_received.

  **Communication     **1 day**      send_vendor_chase, send_slack_
  actions**                          onboarding_exception,
                                     send_slack_vendor_activated,
                                     draft_vendor_clarification_response.

  **Box state and     **1 day**      vendor_onboarding pipeline definition.
  pipeline                           New Box fields. Stage transition rules.
  registration**                     Migration script for new table
                                     columns.

  **Provider          **1--2 days**  Calibration of document classifier
  calibration**                      against ≥300 anonymised documents
                                     across jurisdictions. Calibration of
                                     name-match threshold against labelled
                                     name-pair dataset.

  **Audit export**    **1--2 days**  JSONL and PDF export of the full Box
                                     timeline. Per-workspace signing.
                                     Export activity log.

  **End-to-end        **3--4 days**  Full lifecycle tests per ERP: happy
  testing**                          path (completeness tier), happy path
                                     (full tier), adverse media review,
                                     country unsupported, vendor non-
                                     response, name mismatch hard block,
                                     ERP duplicate detection.
  ---------------------------------------------------------------------------

**Total estimate:** 6--8 weeks for a working prototype across all
four target ERPs. The portal is the critical path --- no other work
can be meaningfully demoed without it. Provider integrations run in
parallel with portal work from week 2 once sandbox credentials are
available.

> *The critical dependency outside the engineering team is KYC
> provider selection and contracting. The spec is provider-agnostic
> but v1 ships with a single chosen provider. Selection must be
> resolved by week 1 so the integration can start by week 2.
> Likewise the open banking provider: selection by week 1,
> integration from week 2.*

Vendor Onboarding Agent Design Specification · Clearledgr Ltd ·
Engineering team only · Review with CTO before implementation
