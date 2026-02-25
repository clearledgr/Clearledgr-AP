# PLAN Implementation Gap Tracker (Pre-Fix Baseline)

Last updated: 2026-02-25
Assessment mode: read-only analysis (no code changes)
Source of truth: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md` (canonical)

Purpose: track implementation gaps against `PLAN.md` while fixes are made.

Usage rules:
- Do not change `PLAN.md` requirements in this file. This file tracks implementation only.
- Keep gap IDs stable (`G01`-`G13`) so PRs/tests can reference them.
- Mark closure only when code and validation/tests both pass.

Status legend:
- `OPEN`: not fixed
- `IN_PROGRESS`: fix underway
- `BLOCKED`: waiting on dependency/decision
- `DONE`: implemented and validated

## Gap Tracker (13 items)

### G01 - Runtime workflow bypasses canonical AP state progression
- Priority: `P0`
- Status: `DONE`
- Classification: `Implemented but deviates from plan`
- Plan references:
  - `PLAN.md` -> `### 4.1 Canonical AP state machine (server-enforced)`
  - `PLAN.md` -> `### 4.6 ERP posting contract (workflow-level)`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `InvoiceWorkflowService.process_new_invoice` (starts at legacy `new` and routes directly)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `InvoiceWorkflowService._auto_approve_and_post`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `InvoiceWorkflowService.approve_invoice`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/ap_workflow.py` -> declarative workflow map (non-executable)
- Current behavior:
  - Runtime flow skips explicit persisted `validated` and `ready_to_post` states in the normal execution path.
  - Items are approved and posted directly via legacy status flow.
- Why this violates plan:
  - Plan requires canonical, server-enforced legal transitions as actual workflow behavior, not only DB primitive support.
- Done criteria:
  - Runtime paths persist canonical transitions in order (`received -> validated -> needs_approval -> approved -> ready_to_post -> posted_to_erp -> closed`) or documented legal exception paths.
- Validation/tests to add:
  - Service-level integration test covering `process_new_invoice` -> approval -> ERP post with persisted state assertions and audit events.
  - Regression test proving no direct `approved -> posted_to_erp` post without `ready_to_post`.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: Runtime flow now advances to `validated` before routing in `process_new_invoice`, and both human/auto approval paths explicitly transition through `ready_to_post` before ERP posting in `invoice_workflow.py`.
  - Validation: `pytest -q tests/test_invoice_workflow_runtime_state_transitions.py tests/test_invoice_workflow_controls.py` and `pytest -q tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py`

### G02 - ERP post failure path reverts to non-canonical state (`pending_approval`) instead of `failed_post`
- Priority: `P0`
- Status: `DONE`
- Classification: `Implemented but deviates from plan`
- Plan references:
  - `PLAN.md` -> `### 4.1 Canonical AP state machine (server-enforced)` (`ready_to_post -> failed_post`)
  - `PLAN.md` -> `### 4.6 ERP posting contract (workflow-level)` (deterministic failure transition)
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `InvoiceWorkflowService.approve_invoice` (failure branch after ERP post)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `InvoiceWorkflowService._auto_approve_and_post` (no explicit canonical fail transition)
- Current behavior:
  - `approve_invoice()` reverts to `pending_approval` on ERP failure.
  - Auto-post path does not explicitly move to `failed_post` on failure.
- Why this violates plan:
  - Plan requires `ready_to_post -> failed_post` and explicit retry path `failed_post -> ready_to_post`.
- Done criteria:
  - All ERP posting failures produce `failed_post` with audit and retry semantics from `failed_post`.
- Validation/tests to add:
  - Approval-post failure integration test asserting final state `failed_post`.
  - Retry test asserting `failed_post -> ready_to_post -> posted_to_erp`.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `approve_invoice()` and `_auto_approve_and_post()` now transition failed ERP post attempts to canonical `failed_post` (with `last_error`/`post_attempted_at`) instead of reverting to `pending_approval`.
  - Validation: `tests/test_invoice_workflow_runtime_state_transitions.py` covers approve and auto-approve ERP failure paths; canonical state-machine suites also pass.

### G03 - Teams callback security not enforced; Slack handler uses permissive verifier path
- Priority: `P0`
- Status: `DONE`
- Classification: `Implemented but deviates from plan`
- Plan references:
  - `PLAN.md` -> `### 5.2 Slack contract (approval and exception decisions)`
  - `PLAN.md` -> `### 5.3 Teams contract (approval and exception decisions)`
  - `PLAN.md` -> `### 7.3 Idempotent ERP posting and action handling`
  - `PLAN.md` -> `### 7.7 Failure mode behavior (must be defined and tested)`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` -> `handle_invoice_interactive` (imports verifier from `services.slack_api`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py` -> `verify_slack_signature` (returns `True` when no signing secret configured)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/slack_verify.py` -> `verify_slack_signature`, `require_slack_signature` (stricter verifier, replay protection)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` -> `handle_teams_interactive` (no token verification call)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/teams_verify.py` -> `verify_teams_token`
- Current behavior:
  - Teams interactive callback accepts payload without JWT validation.
  - Slack interactive callback uses a verifier that can bypass validation in dev-like misconfiguration.
- Why this violates plan:
  - Plan requires verified callback/security model and replay protection for GA channels.
- Done criteria:
  - Slack and Teams interactive handlers enforce production-safe verification at entry.
  - Invalid/unauthorized callbacks are rejected and audited.
- Validation/tests to add:
  - Handler tests for missing/invalid Slack signatures and Teams bearer tokens.
  - Replay/duplicate callback tests where applicable.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` now verifies Slack interactive callbacks via `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/slack_verify.py` `require_slack_signature` at handler entry (replacing the permissive `services.slack_api.verify_slack_signature` path), and audits unauthorized Slack callbacks as `channel_callback_unauthorized`; `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now calls `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/teams_verify.py` `verify_teams_token` at handler entry and audits unauthorized Teams callbacks as `channel_callback_unauthorized`.
  - Validation: `pytest -q tests/test_channel_approval_contract.py tests/test_v1_core_completion.py`; `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py`

### G04 - Common Slack/Teams approval action contract (Section 5.4) is not implemented
- Priority: `P0`
- Status: `DONE`
- Classification: `Missing`
- Plan references:
  - `PLAN.md` -> `### 5.4 Common Slack/Teams approval action contract (canonical)`
  - `PLAN.md` -> `### B. Approval action interfaces`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` -> `handle_invoice_interactive`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` -> `handle_teams_interactive`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `approve_invoice`, `reject_invoice`
- Current behavior:
  - Slack and Teams callbacks use transport-specific payload parsing and ad hoc action names.
  - Canonical fields such as `run_id`, `request_ts`, `idempotency_key`, `actor_display` are not normalized/enforced end-to-end.
- Why this violates plan:
  - Plan requires one shared approval action contract across Slack and Teams with duplicate-safe semantics.
- Done criteria:
  - A normalized action envelope exists and is used by both handlers before calling workflow logic.
  - Duplicate callbacks, invalid actions, and stale actions follow common behavior and audit rules.
- Validation/tests to add:
  - Shared contract parser/unit tests.
  - Slack/Teams parity matrix tests for approve/reject/request_info + duplicates + invalid actions.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: Added shared normalized contract in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/approval_action_contract.py` (`NormalizedApprovalAction`, `normalize_slack_action`, `normalize_teams_action`, `is_stale_action`) with canonical fields including `run_id`, `action` (`approve`/`reject`/`request_info`), `actor_id`, `actor_display`, `reason`, `source_channel`, `source_message_ref`, `request_ts`, and `idempotency_key`; both `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now normalize callbacks through this contract before dispatching workflow methods, reject/ audit invalid actions, return explicit stale responses, and ignore duplicate callbacks at the callback-handler layer. (`G07` remains open for stronger end-to-end/ERP idempotency race guarantees.)
  - Validation: `tests/test_channel_approval_contract.py` covers common `request_info`, duplicate callbacks, invalid action rejection/audit, and stale handling across Slack/Teams; regression suites above remain green.

### G05 - Critical-field confidence gate is not enforced server-side (field-level blockers missing)
- Priority: `P0`
- Status: `DONE`
- Classification: `Partially implemented`
- Plan references:
  - `PLAN.md` -> `### 4.3 Extraction confidence gating (launch-critical)`
  - `PLAN.md` -> `### 7.1 Extraction confidence gates`
  - `PLAN.md` -> `### Extraction and validation defaults` (95% default, override justification + audit)
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` -> `verify_confidence` (invoice-level threshold + mismatch list)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` -> approve override prompt below 95%
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `_evaluate_deterministic_validation` (no field-level critical confidence gate)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` -> `build_worklist_item` (does not set review blocker fields)
- Current behavior:
  - UI shows threshold and prompts for override justification.
  - Server endpoint computes coarse `can_post`.
  - Workflow does not enforce critical field gating at posting stage with field-level blockers.
- Why this violates plan:
  - Plan requires critical-field confidence gate to be active server-side and expose `requires_field_review` + `confidence_blockers`.
- Done criteria:
  - Posting is blocked server-side for low-confidence critical fields (policy-adjustable threshold, default 95%).
  - Overrides require explicit justification and audit event.
  - Blockers are surfaced in APIs/worklist.
- Validation/tests to add:
  - Server-side workflow tests for low-confidence vendor/invoice_number/amount/due_date.
  - API contract tests for blocker fields in worklist/context.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: Added shared confidence-gate evaluator in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/ap_confidence.py`; workflow now computes/persists `confidence_gate` and `confidence_blockers` during `process_new_invoice()` and injects `confidence_field_review_required` into deterministic validation when needed; `approve_invoice()` blocks posting on low-confidence critical fields unless `allow_confidence_override=True` with justification, and appends `confidence_override_used` audit events; `/extension/verify-confidence` now returns `requires_field_review` and `confidence_blockers` from the same server-side logic.
  - Validation: `pytest -q tests/test_invoice_workflow_runtime_state_transitions.py tests/test_v1_core_completion.py`; `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py`

### G06 - Gmail worklist API misses required canonical fields (`next_action`, `requires_field_review`, `confidence_blockers`)
- Priority: `P0`
- Status: `DONE`
- Classification: `Partially implemented`
- Plan references:
  - `PLAN.md` -> `### 5.1 Gmail contract (primary operator surface)`
  - `PLAN.md` -> `#### Gmail worklist/workspace contract (backend API)`
  - `PLAN.md` -> `### A. Gmail extension worklist/workspace APIs`
  - `PLAN.md` -> `### D. AP item type requirements (minimum fields)`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` -> `get_extension_worklist`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` -> `build_worklist_item`
- Current behavior:
  - Worklist endpoint exists and returns many useful fields.
  - Canonical fields required by plan are not included or derived.
- Why this violates plan:
  - Plan defines these fields as minimum backend contract for Gmail worklist/workspace.
- Done criteria:
  - `GET /extension/worklist` returns all required minimum fields from plan Section 5.1/A/D.
- Validation/tests to add:
  - API schema/contract tests for all required fields.
  - UI integration test ensuring next-action and blocker visibility works from backend payload only.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` `build_worklist_item()` now derives/populates `confidence_gate`, `requires_field_review`, `confidence_blockers`, and canonical `next_action` (with metadata-first fallback computation via shared confidence helper); `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` `/extension/worklist` continues to return normalized items from that backend contract.
  - Validation: `pytest -q tests/test_v1_core_completion.py` (worklist assertions for `next_action`, `requires_field_review`, `confidence_blockers`); included in `pytest -q tests/test_invoice_workflow_runtime_state_transitions.py tests/test_v1_core_completion.py`

### G07 - Idempotency protections are partial and not consistently applied in approval callbacks/ERP posting
- Priority: `P0`
- Status: `DONE`
- Classification: `Partially implemented`
- Plan references:
  - `PLAN.md` -> `### 4.5 Idempotency and dedupe (required)`
  - `PLAN.md` -> `### 7.3 Idempotent ERP posting and action handling`
  - `PLAN.md` -> `### C. ERP posting interfaces`
  - `PLAN.md` -> `### E. Audit event type requirements`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py` -> `audit_events`, `approvals`, `browser_action_events` schema + unique indexes
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` -> `append_ap_audit_event`, `save_approval`, `get_approval_by_decision_key`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `_record_approval_snapshot` (does not pass `decision_idempotency_key`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/integrations/erp_router.py` -> `post_bill` (idempotency guard based on persisted `erp_reference`)
- Current behavior:
  - Schema and store support idempotency fields.
  - Workflow/channel paths do not consistently supply canonical idempotency keys.
  - ERP duplicate protection depends on prior persistence of `erp_reference`.
- Why this violates plan:
  - Plan requires idempotent approval actions and ERP posting with auditable idempotency keys and safe retries.
- Done criteria:
  - All channel actions produce and persist deterministic decision idempotency keys.
  - ERP posting requires and propagates idempotency key through connectors/normalized response.
  - Duplicate callback races cannot create duplicate ERP transactions.
- Validation/tests to add:
  - Duplicate Slack/Teams callback integration tests.
  - Concurrency test around ERP post retries before `erp_reference` persistence.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now pass canonical `decision_idempotency_key` / `action_run_id` / `decision_request_ts` into workflow action calls; `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` now persists `decision_idempotency_key` in approvals (`_record_approval_snapshot`), detects duplicate decisions via `get_approval_by_decision_key`, and acquires an auditable per-decision lock (`approval_action_lock:*`) before ERP posting or state mutation; `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/integrations/erp_router.py` now accept/propagate ERP posting idempotency keys into normalized responses and audit attempt keys.
  - Validation: `pytest -q tests/test_invoice_workflow_runtime_state_transitions.py tests/test_channel_approval_contract.py tests/test_erp_api_first.py` (includes duplicate approval no-repost, channel key propagation, ERP idempotency-key propagation); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py` (`59 passed`)

### G08 - ERP browser fallback path does not implement explicit preview + confirmation workflow at orchestration level
- Priority: `P1`
- Status: `DONE`
- Classification: `Implemented but deviates from plan`
- Plan references:
  - `PLAN.md` -> `### 4.6 ERP posting contract (workflow-level)`
  - `PLAN.md` -> `### 6.7 Fallback policy (API-first, gated browser fallback)`
  - `PLAN.md` -> `### ERP defaults` (preview + confirmation + audit + policy)
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` -> `_dispatch_browser_fallback`, `post_bill_api_first`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py` -> `preview_command`, `dispatch_macro`, `enqueue_command`
- Current behavior:
  - Browser-agent primitives support preview/policy/confirmation and audit.
  - ERP fallback orchestration directly dispatches macro with `dry_run=False`.
- Why this violates plan:
  - Plan requires fallback to be gated by preview + confirmation + policy + audit as part of workflow-level behavior.
- Done criteria:
  - ERP fallback path explicitly emits preview, records/captures human confirmation, then executes.
  - Planned and executed fallback actions are auditable from ERP flow.
- Validation/tests to add:
  - ERP fallback tests asserting preview payload creation, confirmation capture, and audit sequence.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` `_dispatch_browser_fallback` now performs an explicit browser fallback sequence at the ERP orchestration layer: create session, generate preview via `dispatch_macro(..., dry_run=True)`, audit `erp_api_fallback_preview_created`, dispatch execution via `dispatch_macro(..., dry_run=False)`, capture/submit confirmations for blocked commands via `enqueue_command(..., confirm=True, confirmed_by=actor_id)`, and audit `erp_api_fallback_confirmation_captured`; fallback responses now include structured `preview` and `confirmation` payloads and return reason `fallback_preview_confirmed_and_dispatched`.
  - Validation: `pytest -q tests/test_channel_approval_contract.py tests/test_erp_api_first.py` (includes `test_dispatch_browser_fallback_emits_preview_and_confirmation_audit_sequence`); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py` (`62 passed`)

### G09 - Merge endpoint uses non-canonical AP state `merged`
- Priority: `P1`
- Status: `DONE`
- Classification: `Implemented but deviates from plan`
- Plan references:
  - `PLAN.md` -> `### 4.1 Canonical AP state machine (server-enforced)`
  - `PLAN.md` -> `### 4.5 Idempotency and dedupe (required)` (link/merge semantics)
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` -> `merge_ap_items` (`db.update_ap_item(source["id"], state="merged", ...)`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/ap_states.py` -> `APState` (no `merged` state)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` -> `update_ap_item` (transition enforcement)
- Current behavior:
  - Merge path attempts to write a state not present in canonical state machine.
- Why this violates plan:
  - Canonical state machine is closed; merge/linking should use metadata/linkage semantics, not ad hoc state.
- Done criteria:
  - Merge/split/link operations use plan-consistent metadata and valid state transitions only.
- Validation/tests to add:
  - Merge API tests verifying no illegal state writes and correct source-link migration + audit.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` `merge_ap_items` no longer writes illegal `state="merged"`; merge semantics are now represented via source metadata (`merged_into`, `merge_status`, `merged_at`, `merged_by`, `suppressed_from_worklist`) and explicit audit events (`ap_item_merged` on target, `ap_item_merged_into` on source). `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` `build_worklist_item` and `_derive_next_action` now expose `merged_into` / `is_merged_source` and suppress merged-source next actions (`next_action="none"`) without inventing a non-canonical AP state.
  - Validation: `pytest -q tests/test_ap_items_merge_and_audit_guardrails.py tests/test_channel_approval_contract.py` (includes `test_merge_ap_items_uses_metadata_linkage_without_illegal_state`); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_ap_items_merge_and_audit_guardrails.py` (`66 passed`)

### G10 - Audit completeness/immutability is partial (channel invalid actions and append-only hardening not fully covered)
- Priority: `P1`
- Status: `DONE`
- Classification: `Partially implemented`
- Plan references:
  - `PLAN.md` -> `### 4.7 Audit coverage contract (non-negotiable)`
  - `PLAN.md` -> `### 7.4 Audit completeness and immutability`
  - `PLAN.md` -> `### E. Audit event type requirements`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` -> `update_ap_item` (atomic transition audit), `append_ap_audit_event`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` -> `handle_invoice_interactive` (returns errors/results without explicit audit for invalid/unauthorized paths)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` -> `handle_teams_interactive`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py` -> `audit_events` schema (append table exists; no visible DB-level immutability guard)
- Current behavior:
  - Many state transitions and external actions are audited.
  - Invalid/unauthorized/stale callback handling audit coverage is not clearly implemented.
  - Immutability is not clearly enforced beyond convention/store API usage.
- Why this violates plan:
  - Audit completeness and append-only protection are explicit GA launch gates.
- Done criteria:
  - Required action paths and failure paths audit all outcomes.
  - Audit mutation protections are enforced/documented and tested.
- Validation/tests to add:
  - Audit coverage matrix tests for validation, approval, override, ERP attempt/result, retries, escalations, invalid callbacks.
  - Append-only tests (or DB trigger tests / denied mutation-path tests).
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now audit malformed/invalid callback payloads (`channel_action_invalid`) in addition to unauthorized/stale/duplicate/processed paths, and callback audit helpers persist no-item callback events by writing a channel callback sentinel `ap_item_id` when invoice context cannot be resolved. `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py` now enforces append-only semantics for `audit_events` (and `ap_policy_audit_events`) in SQLite via `BEFORE UPDATE/DELETE` triggers that abort mutation attempts.
  - Validation: `pytest -q tests/test_ap_items_merge_and_audit_guardrails.py tests/test_channel_approval_contract.py` (includes malformed Slack/Teams callback audit persistence checks and `test_audit_events_table_is_append_only`); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_ap_items_merge_and_audit_guardrails.py` (`66 passed`)

### G11 - Slack/Teams parity incomplete (`request_info`, stale action handling, canonical result semantics)
- Priority: `P1`
- Status: `DONE`
- Classification: `Partially implemented`
- Plan references:
  - `PLAN.md` -> `### 3.4 Slack and Teams approval card doctrine`
  - `PLAN.md` -> `### 5.2 Slack contract`
  - `PLAN.md` -> `### 5.3 Teams contract`
  - `PLAN.md` -> `### 9.3 Channel parity validation (Slack + Teams)`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py` -> `build_approval_blocks` (Approve/Reject only)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/teams_api.py` -> `TeamsAPIClient.build_invoice_budget_card` (Approve/Reject + budget actions, no canonical `request_info`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` -> `handle_invoice_interactive`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` -> `handle_teams_interactive`
- Current behavior:
  - Channels are functional for common approve/reject paths and budget-specific flows.
  - Canonical parity action set and stale/expired action behaviors are incomplete.
- Why this violates plan:
  - Plan requires both channels to pass the common approval action contract test matrix before GA.
- Done criteria:
  - Slack and Teams both support `approve`, `reject`, `request_info`, duplicate safety, stale action handling, and clear result feedback under common semantics.
- Validation/tests to add:
  - Channel parity matrix (same test cases executed against both handlers).
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/slack_api.py` `build_approval_blocks`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/teams_api.py` `build_invoice_budget_card`, and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` Slack approval-block generation now expose canonical `request_info` actions in both standard and budget paths; common Slack/Teams callback semantics for `request_info`, duplicate safety, stale/invalid handling, and normalized results are enforced via the shared approval action contract already wired in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py`.
  - Validation: `pytest -q tests/test_channel_approval_contract.py tests/test_erp_api_first.py` (includes Slack/Teams builder parity and `request_info` contract tests); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py` (`62 passed`)

### G12 - GA parity evidence, runbooks, signed readiness checklists, and rollback controls are not implemented/verifiable in code
- Priority: `P2`
- Status: `DONE`
- Classification: `Ambiguous / cannot verify from code`
- Plan references:
  - `PLAN.md` -> `### 6.6 Connector readiness checklist (per ERP, required before GA)`
  - `PLAN.md` -> `### 6.8 GA parity evidence requirements`
  - `PLAN.md` -> `### 8.4 GA launch acceptance criteria (minimum)`
  - `PLAN.md` -> `### 8.5 Post-GA monitoring and rollback triggers`
  - `PLAN.md` -> `### 9.4 ERP parity validation`
  - `PLAN.md` -> `### 9.5 GA launch`
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/admin_console.py` -> admin setup/health surfaces
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py` -> ops metrics/routing visibility
- Current behavior:
  - Connectors and ops/admin APIs exist.
  - No clear in-repo implementation/evidence store for readiness signoff, runbooks, parity matrix artifacts, or rollback-control endpoints matching plan.
- Why this matters:
  - These are launch gates; connector existence is not sufficient per `PLAN.md`.
- Done criteria:
  - Either implemented in-app tracking/controls or documented external source-of-record with explicit links/process.
- Validation/tests to add:
  - Endpoint tests for rollback controls if implemented in app.
  - Artifact presence/validation checks if stored in repo.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: Added typed in-app launch-control helpers in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/launch_controls.py` for normalized `rollback_controls` and `ga_readiness` evidence (connector checklists, runbooks, parity evidence, signoffs, source-of-record metadata) plus readiness summarization. Added explicit admin endpoints in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/admin_console.py` for `GET/PUT /api/admin/rollback-controls` and `GET/PUT /api/admin/ga-readiness`, and projected launch-control state into `/api/admin/health`. Rollback controls are now enforced at runtime in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py`, and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` (channel actions can be blocked per-channel; ERP posting/browser fallback can be disabled with auditable blocked results).
  - Validation: `pytest -q tests/test_invoice_workflow_runtime_state_transitions.py tests/test_channel_approval_contract.py tests/test_erp_api_first.py tests/test_admin_launch_controls.py` (includes admin launch-controls endpoint tests and runtime rollback-control enforcement tests); `PYTHONPATH=. pytest -q tests/test_invoice_workflow_controls.py tests/test_plan_acceptance.py tests/test_e2e_ap_flow.py tests/test_channel_approval_contract.py tests/test_v1_core_completion.py tests/test_erp_api_first.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_ap_items_merge_and_audit_guardrails.py tests/test_admin_launch_controls.py` (`72 passed`)

### G13 - Slack rejection feedback propagation path appears broken (thread update API misuse)
- Priority: `P2`
- Status: `DONE`
- Classification: `Implemented but deviates from plan` (correctness bug affecting contract behavior)
- Plan references:
  - `PLAN.md` -> `### 3.4 Slack and Teams approval card doctrine` (clear result feedback)
  - `PLAN.md` -> `### 5.2 Slack contract` (result feedback / action blocked outcomes)
- Code references:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` -> `reject_invoice`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` -> `get_slack_thread`, `update_slack_thread_status`
- Current behavior:
  - `reject_invoice()` uses `thread["id"]` even though `get_slack_thread()` returns `channel_id`, `thread_ts`, `thread_id`.
  - Calls `update_slack_thread_status(thread_id=...)` but store method expects `gmail_id`.
- Why this matters:
  - Can break Slack-side state/result propagation and operator feedback reliability.
- Done criteria:
  - Slack thread status update path uses correct identifiers and is covered by tests.
- Validation/tests to add:
  - Reject flow test asserting Slack metadata/thread fields are updated correctly.
- Tracking notes:
  - Owner:
  - PR:
  - Evidence: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` `reject_invoice` now calls `update_slack_thread_status` with the correct identifier contract (`gmail_id` + mapped Slack thread fields) instead of incorrectly passing `thread_id` as the primary identifier, and `_send_for_approval` no longer reads nonexistent `existing_thread["id"]` when reusing an existing Slack thread. `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` `update_slack_thread_status` now accepts transport-style aliases (`channel_id`, `thread_ts`, `thread_id`) and maps them to AP item Slack columns.
  - Validation: `tests/test_invoice_workflow_runtime_state_transitions.py::test_reject_invoice_updates_slack_thread_with_gmail_id`; broader regression suite also passes (`72 passed`)

## Architecture Mismatches

### A01 - Canonical state machine exists in storage layer, but runtime orchestration still follows legacy shortcuts
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 4.1 Canonical AP state machine (server-enforced)`
  - `PLAN.md` -> `### 7.2 Deterministic state enforcement`
- Evidence:
  - Canonical transitions and enforcement: `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/ap_states.py` (`APState`, `VALID_TRANSITIONS`), `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` (`update_ap_item`)
  - Runtime orchestration now follows canonical transitions in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`: `process_new_invoice` persists `received -> validated`, `_send_for_approval` routes to `needs_approval`, `_auto_approve_and_post` and `approve_invoice` transition through `approved -> ready_to_post -> posted_to_erp` (or `failed_post`) via `_transition_invoice_state`.
- Tracking notes:
  - Related gaps: `G01`, `G02` (both `DONE`)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py` asserts `received -> validated`, `approved -> ready_to_post -> posted_to_erp`, and `ready_to_post -> failed_post` in runtime service paths; regression suite currently passes (`72 passed`).

### A02 - Declarative workflow map is documentation-only, not executable orchestration
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 4 AP Execution Contract`
  - `PLAN.md` -> `### 7 Reliability and Trust Requirements`
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/ap_workflow.py` now includes an executable orchestration adapter (`APWorkflowExecutor`), binding validation (`validate_workflow_bindings`), and runtime dispatch (`dispatch_step`, `run_invoice_entry_workflow`) that dispatchs via the declarative `AP_WORKFLOW_STEPS` map rather than treating it as documentation-only.
- Tracking notes:
  - Related gaps: `G01` (`DONE`)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow_runtime.py` verifies workflow bindings and executes the declared `received` step through `dispatch_step`; broader regression suite currently passes (`75 passed`).

### A03 - Durability/orchestration runtime is stubbed (Temporal disabled), but plan-level reliability implies durable orchestration expectations
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 7.7 Failure mode behavior (must be defined and tested)`
  - `PLAN.md` -> `### 9.1 Internal validation`
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/temporal_runtime.py` is now a functional local DB-backed durable runtime (`TemporalRuntime`) with `start_workflow`, `start_invoice`, `start_reconciliation`, and `get_status` methods that persist workflow runs instead of raising `RuntimeError`.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py` now define/persist `workflow_runs` for durable execution state (`create_workflow_run`, `update_workflow_run`, `get_workflow_run`).
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` `/extension/workflow/{workflow_id}` now polls the runtime and supports local durable runs without requiring the Temporal feature flag.
- Tracking notes:
  - Related gaps: `G01`, `G12` (runtime status/launch controls now tracked and implemented)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow_runtime.py` verifies durable run persistence, AP item `workflow_id`/`run_id` linkage, and `/extension/workflow/{id}` polling via the local runtime; broader regression suite currently passes (`75 passed`).

### A04 - Channel handlers are transport-specific adapters, not normalized contract adapters
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 5.4 Common Slack/Teams approval action contract (canonical)`
  - `PLAN.md` -> `### B. Approval action interfaces`
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/approval_action_contract.py` now defines the shared normalized callback envelope (`NormalizedApprovalAction`) plus channel normalizers (`normalize_slack_action`, `normalize_teams_action`) and shared stale detection (`is_stale_action`) used as the canonical adapter contract.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` now acts as a transport adapter: verify signature (`require_slack_signature`), decode Slack form payload, normalize via `normalize_slack_action`, then apply shared blocked/stale/duplicate/idempotent handling and dispatch via `_dispatch_slack_action` using canonical action metadata (`decision_idempotency_key`, `action_run_id`, `decision_request_ts`).
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now follows the same adapter pattern: verify token (`verify_teams_token`), parse Teams payload, normalize via `normalize_teams_action`, then apply the same blocked/stale/duplicate/idempotent flow before `_dispatch_teams_action`.
- Tracking notes:
  - Related gaps: `G03`, `G04`, `G07`, `G11` (all `DONE`)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_channel_approval_contract.py` covers common-contract normalization behavior, `request_info` parity, duplicate/stale handling, invalid payload auditing, and rollback blocking across Slack and Teams; focused suite passes (`10 passed`).

### A05 - Browser-agent has strong preview/policy/confirmation primitives, but ERP fallback does not use them as first-class workflow stages
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 4.6 ERP posting contract (workflow-level)`
  - `PLAN.md` -> `### 6.7 Fallback policy (API-first, gated browser fallback)`
- Evidence:
  - Browser agent primitives remain available in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/browser_agent.py` (`preview_command`, `dispatch_macro`, `enqueue_command`, `submit_result`) and are now exercised by the ERP fallback path instead of bypassed.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` `_dispatch_browser_fallback` now performs explicit fallback stages:
    - creates a browser session,
    - dispatches a `dry_run=True` macro preview,
    - inspects preview decisions for `requires_confirmation`,
    - dispatches execution,
    - confirms blocked commands via `enqueue_command(confirm=True, confirmed_by=actor_id)`,
    - emits `erp_api_fallback_preview_created` and `erp_api_fallback_confirmation_captured` audit events,
    - returns structured `preview` and `confirmation` summaries in the fallback result contract.
- Tracking notes:
  - Related gaps: `G08` (`DONE`)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py::test_dispatch_browser_fallback_emits_preview_and_confirmation_audit_sequence` asserts preview-first dispatch, confirmation capture, and audit sequence; focused ERP/worklist suite passes (`8 passed`).

### A06 - AP item/worklist server contract is missing key decision fields, pushing decision semantics into UI heuristics
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `#### Gmail worklist/workspace contract (backend API)`
  - `PLAN.md` -> `### D. AP item type requirements (minimum fields)`
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` now derives and emits server-side decision semantics in `build_worklist_item`, including:
    - confidence gate derivation (`_derive_confidence_gate`)
    - canonical `next_action` derivation (`_derive_next_action`)
    - `requires_field_review`
    - `confidence_blockers`
    - exception/budget context and merged-source suppression semantics
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` `/extension/worklist` uses `build_worklist_item` as the backend normalization contract for the Gmail embedded worklist.
- Tracking notes:
  - Related gaps: `G05`, `G06`, `G09` (all `DONE`)
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_v1_core_completion.py::test_worklist_derives_budget_exception_and_teams_interactive` asserts `next_action`, `requires_field_review`, and `confidence_blockers` are present and correctly derived from server-side state/metadata; focused ERP/worklist suite passes (`8 passed`).

### A07 - Gmail embedded surface is largely compliant, but sidebar feature accretion risks drifting toward a dashboard-in-Gmail
- Status: `DONE`
- Plan references:
  - `PLAN.md` -> `### 3.1 Gmail thread card doctrine`
  - `PLAN.md` -> `### 3.3 Gmail UI anti-patterns (must avoid)`
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` preserves the focused thread workspace (`Decision workspace`) and hides empty sections via `setSectionVisibility(...)`.
  - `renderKpiSummary()` now hides the KPI section unless the debug UI flag is enabled (`queueManager.isDebugUiEnabled()`), preventing dashboard-style KPI tiles in normal operator mode.
  - `renderAgentActions()` keeps thread-scoped execution status and approval preview/history, but generic macro launch controls are now moved behind a collapsed `Debug agent tools` block and only rendered in debug mode.
  - Sidebar section label updated from `Workflow assistant` to `Execution status` to reflect thread-scoped execution context rather than a generic automation console.
- Tracking notes:
  - Related gaps: `G06` (`DONE`) and `A05` (`DONE`) (thread-scoped next action + browser preview/confirmation now surfaced without exposing a dashboard-like control surface)
  - Validation evidence: `node --check /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` passes (syntax). No dedicated frontend test harness exists for this InboxSDK sidebar module in the current repo.

## Fake Completion Risks

### F01 - DB-level transition tests can overstate real workflow compliance
- Status: `DONE`
- Risk:
  - DB-direct tests can be misread as runtime-orchestration proof if their scope is not explicit.
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_plan_acceptance.py` and `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_e2e_ap_flow.py` are now explicitly labeled as DB/storage-contract tests and document where runtime proof lives.
  - Runtime/service-path proof is covered in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py` (canonical runtime transitions, failure semantics, idempotency, confidence gate behavior).
  - Handler-path proof is covered in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_channel_approval_contract.py` (Slack/Teams callback normalization, stale/duplicate handling, dispatch kwargs).
- Mitigation tracking:
  - Completed: service-level and handler-level runtime tests were added (`G01`, `G02`, `G03`, `G04`, `G07`, `G11`) and DB-direct modules now state their scope explicitly.
  - Validation evidence: focused regression suite spanning DB-direct, service runtime, handler contract, and workflow runtime tests passes (`57 passed`).

### F02 - `ap_workflow.py` can be mistaken for implemented orchestration
- Status: `DONE`
- Risk:
  - A declarative workflow map can look implemented even when it is only documentation.
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/ap_workflow.py` now documents itself as a declarative + executable workflow contract and dispatch layer (not a “future hook”).
  - The same module contains executable bindings (`APWorkflowExecutor`, `dispatch_step`, `run_invoice_entry_workflow`), and runtime coverage exists in `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow_runtime.py`.
- Mitigation tracking:
  - Completed via `A02`/`A03`: declarative map is executable and exercised by the local durable runtime, with docstrings updated to match actual behavior.
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow_runtime.py` (plus broader focused suite) passes; `python3 -m py_compile /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/ap_workflow.py` passes.

### F03 - UI confidence override flow can look compliant while server-side gating is incomplete
- Status: `DONE`
- Risk:
  - UI affordances can appear compliant if server-side field-level confidence gating is not the source of truth.
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` `_evaluate_deterministic_validation` now includes a server-enforced critical-field confidence gate (`confidence_gate`) and persists `requires_field_review` / `confidence_blockers` metadata during `process_new_invoice`.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` `approve_invoice` blocks posting with `status=\"needs_field_review\"` unless `allow_confidence_override=True` and a justification is provided; confidence overrides write `confidence_override_used` audit events.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` `verify_confidence` returns server-derived `requires_field_review` and `confidence_blockers`, and `/extension/approve-and-post` passes `allow_confidence_override` to the workflow.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` `build_worklist_item` derives and emits `requires_field_review`, `confidence_blockers`, and `next_action` server-side for Gmail worklist/workspace UX.
- Mitigation tracking:
  - Completed via `G05`/`G06`: server workflow is the gating source of truth and APIs expose blockers/next-action semantics.
  - Validation evidence: `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py` covers low-confidence routing, block-without-override, override-with-justification, and `confidence_override_used` audit emission; `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_v1_core_completion.py` asserts worklist `requires_field_review` / `confidence_blockers` / `next_action` fields. Focused suite passes (`18 passed`).

### F04 - ERP API-first fallback tests validate routing/audits, not the full plan-required preview/confirmation sequence
- Status: `DONE`
- Risk:
  - ERP fallback tests can overstate compliance if they only prove routing/audits and not preview -> confirmation -> execution sequencing.
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` `_dispatch_browser_fallback` now executes explicit sequencing: preview macro dispatch (`dry_run=True`), confirmation capture for blocked commands via `enqueue_command(confirm=True, ...)`, then execution result summarization with `preview` and `confirmation` payloads.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py` emits `erp_api_fallback_preview_created` and `erp_api_fallback_confirmation_captured` audit events before returning `fallback_preview_confirmed_and_dispatched`.
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py::test_dispatch_browser_fallback_emits_preview_and_confirmation_audit_sequence` asserts preview-first dispatch, confirmation of blocked commands, and audit sequence.
- Mitigation tracking:
  - Completed via `G08`: fallback sequencing implementation and tests now cover preview -> confirmation -> execution -> audit behavior.
  - Validation evidence: focused suite including `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_api_first.py` passes (`18 passed`).

### F05 - Channel handlers can appear production-ready despite incomplete security/parity guarantees
- Status: `DONE`
- Risk:
  - Channel handlers can look production-ready on happy paths if strict callback verification and canonical action normalization are not enforced at handler entry.
- Evidence:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py` now verifies callbacks with `clearledgr.core.slack_verify.require_slack_signature` at handler entry, normalizes actions via `normalize_slack_action`, and applies shared invalid/stale/duplicate/audit behavior before workflow dispatch.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py` now verifies callbacks with `verify_teams_token` at handler entry, normalizes actions via `normalize_teams_action`, and applies the same invalid/stale/duplicate/audit semantics before workflow dispatch.
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/approval_action_contract.py` defines the canonical normalized action envelope and Slack/Teams normalization logic (including `request_info` parity and stale detection).
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/slack_verify.py` and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/teams_verify.py` provide strict security verification paths used by the handlers (instead of permissive service-layer helper behavior).
- Mitigation tracking:
  - Completed via `G03`, `G04`, `G11` (and `G07` for idempotent callback handling): handler entry security, common contract normalization, parity actions (`request_info`), and duplicate/stale behavior are implemented and audited.
  - Validation evidence:
    - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_channel_approval_contract.py` covers Slack/Teams parity, `request_info`, unauthorized callback auditing, invalid payload auditing, stale/duplicate handling, and rollback blocking.
    - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_plan_acceptance.py` includes Slack signature verification/replay tests for `clearledgr.core.slack_verify.verify_slack_signature`.
    - Focused suite passes (`38 passed`): `tests/test_channel_approval_contract.py` + `tests/test_plan_acceptance.py`.

## Fix Sequencing (Initial)

This sequence mirrors the risk profile from the read-only assessment.

1. `P0` Canonical runtime state progression and failure semantics (`G01`, `G02`)
2. `P0` Confidence gate server enforcement + Gmail contract fields (`G05`, `G06`)
3. `P0` Channel security and common action contract (`G03`, `G04`, `G07`)
4. `P1` Browser fallback workflow gating (`G08`)
5. `P1` Merge/link canonical state compliance + audit hardening + channel parity (`G09`, `G10`, `G11`)
6. `P2` GA readiness evidence + rollback controls + residual bugs (`G12`, `G13`)

## Change Log

- 2026-02-25: Initial tracker created from read-only implementation-vs-`PLAN.md` assessment.
