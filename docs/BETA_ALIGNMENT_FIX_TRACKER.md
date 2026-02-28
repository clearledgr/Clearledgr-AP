# Beta Alignment Fix Tracker (Current Cycle)

Date opened: 2026-02-28  
Source of truth: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md`

## Scope and guardrails
- Preserve Clearledgr as one finance agent runtime with AP as Skill #1.
- Do not de-scope agentic behavior; harden auth, tenancy, and runtime integrity.
- `DONE` means code + test coverage + validation command evidence recorded.

## Status summary

| ID | Priority | Category | Status | Type |
|---|---|---|---|---|
| B01 | P0 | agentic-runtime | DONE | broken |
| B02 | P0 | agent-session-security | DONE | broken |
| B03 | P0 | extension-org-scope | DONE | partial |
| B04 | P0 | gmail-activities-runtime | DONE | missing |
| B05 | P0 | gmail-auth-boundary | DONE | missing |
| B06 | P0 | gmail-webhook-security | DONE | fragile |
| B07 | P0 | tenant-isolation | DONE | broken |
| B08 | P1 | runtime-contract-clarity | DONE | fragile |
| B09 | P1 | ap-v1-surface-scope | DONE | partial |
| B10 | P2 | e2e-confidence | DONE | partial |
| B11 | P0 | agent-intents-tenant-scope | DONE | broken |
| B12 | P0 | ap-retry-post-canonical-path | DONE | placeholder |
| B13 | P0 | gmail-oauth-state-and-push-hardening | DONE | fragile |
| B14 | P1 | deployment-config-parity | DONE | partial |

## Open and completed items

### B01
- Priority: `P0`
- Category: `agentic-runtime`
- Status: `DONE`
- Plan refs: `PLAN.md` Agent runtime doctrine, AP skill execution path
- Problem: AP decision tool previously awaited a sync method path and degraded.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/skills/ap_skill.py` (`_handle_get_ap_decision`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_agent_runtime.py` (`test_ap_skill_get_ap_decision_handles_sync_decider_without_fallback`)
- Acceptance criteria:
  - Sync and async AP decision backends both execute without fallback error shape.
- Validation/tests:
  - `PYTHONPATH=. pytest tests/test_agent_runtime.py::test_ap_skill_get_ap_decision_handles_sync_decider_without_fallback -q`

### B02
- Priority: `P0`
- Category: `agent-session-security`
- Status: `DONE`
- Plan refs: `PLAN.md` auth boundary + tenant isolation
- Problem: Browser-agent session APIs had cross-tenant risk.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py` (`_load_session_for_user`, `_assert_org_access`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` (`test_agent_session_endpoints_enforce_org_scope`)
- Acceptance criteria:
  - Session read/command/preview/macro/result/complete endpoints deny org mismatch.
- Validation/tests:
  - `PYTHONPATH=. pytest tests/test_browser_agent_layer.py::test_agent_session_endpoints_enforce_org_scope -q`

### B03
- Priority: `P0`
- Category: `extension-org-scope`
- Status: `DONE`
- Plan refs: `PLAN.md` auth boundary and org scoping
- Problem: Extension endpoints previously accepted caller-supplied org without consistent enforcement.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py` (`_resolve_org_id_for_user`, `_assert_user_org_access`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_sensitive_extension_endpoints_enforce_org_scope`)
- Acceptance criteria:
  - Authenticated users cannot read/write other org data through extension routes.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B04
- Priority: `P0`
- Category: `gmail-activities-runtime`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` Gmail AP flow integrity
- Problem: `clearledgr.workflows.gmail_activities` did not exist, causing runtime 500s.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/workflows/gmail_activities.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_extension_match_endpoints_return_results_for_authorized_user`)
- Acceptance criteria:
  - `/extension/match-bank`, `/extension/match-erp`, inline extraction/classification imports execute without `ModuleNotFoundError`.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B05
- Priority: `P0`
- Category: `gmail-auth-boundary`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` auth boundary requirements
- Problem: `/gmail/status/{user_id}` and `/gmail/disconnect` were public.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py` (`gmail_status`, `gmail_disconnect`, `_assert_user_owns_gmail_identity`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_gmail_status_requires_auth`, `test_gmail_disconnect_requires_auth`, `test_gmail_disconnect_blocks_cross_user_access`)
- Acceptance criteria:
  - Unauthenticated access returns `401`; cross-user access returns `403`.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B06
- Priority: `P0`
- Category: `gmail-webhook-security`
- Status: `DONE`
- Type: `fragile`
- Plan refs: `PLAN.md` callback security/verification failures
- Problem: `/gmail/push` accepted arbitrary payloads and lacked callback verifier.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py` (`_validate_push_payload`, `_enforce_push_verifier`, `gmail_push_notification`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_gmail_push_rejects_invalid_payload`, `test_gmail_push_requires_shared_secret_when_configured`)
- Locked decision:
  - Use `GMAIL_PUSH_SHARED_SECRET` verifier when configured; enforce payload schema always.
- Acceptance criteria:
  - Invalid payloads rejected with `400`; secret mismatch rejected with `401`.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B07
- Priority: `P0`
- Category: `tenant-isolation`
- Status: `DONE`
- Type: `broken`
- Plan refs: `PLAN.md` org scoping and auditability
- Problem: webhook invoice processing wrote `organization_id="default"` instead of tenant-resolved org.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py` (`_resolve_user_org_id`, `process_gmail_notification`, `process_single_email`, `process_invoice_email`, `process_payment_request_email`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_process_single_email_propagates_org_to_invoice_handler`)
- Acceptance criteria:
  - Gmail webhook processing propagates resolved org through AP and payment-request handlers.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B08
- Priority: `P1`
- Category: `runtime-contract-clarity`
- Status: `DONE`
- Type: `fragile`
- Plan refs: `PLAN.md` one-runtime contract
- Problem: Planner failure-mode behavior can still diverge depending flags (`AGENT_PLANNING_LOOP`, `AGENT_LEGACY_FALLBACK_ON_ERROR`).
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/agent_orchestrator.py` (`_runtime_execution_contract`, `_planning_loop_enabled`, `_legacy_fallback_on_planner_error`, `runtime_status`, `process_invoice`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_agent_orchestrator_durable_retry.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py`
- Locked decision:
  - In production, planner opt-out is forced back on and legacy fallback is forced off.
  - Requested vs effective runtime flags are exposed in ops/runtime status.
- Acceptance criteria:
  - Production mode cannot silently execute legacy opt-out path.
  - Production mode ignores `AGENT_LEGACY_FALLBACK_ON_ERROR=true`.
  - Runtime status exposes execution contract (`requested` vs `effective`) for observability.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B09
- Priority: `P1`
- Category: `ap-v1-surface-scope`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` AP-v1 focus doctrine
- Problem: Strict profile is runtime-filtered; legacy route definitions are still compiled into app.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (`_runtime_surface_contract`, `_apply_runtime_surface_profile`, `legacy_get`, `legacy_post`, `legacy_patch`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py` (`_resolve_runtime_surface_contract`, `get_autopilot_status`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py`
- Locked decision:
  - Production ignores `CLEARLEDGR_ENABLE_LEGACY_SURFACES=true` unless `AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION=true`.
  - Runtime surface contract (`requested` vs `effective`) is exposed in `/api/ops/autopilot-status`.
- Acceptance criteria:
  - Strict profile keeps non-canonical legacy routes unmounted/blocked by default in production.
  - Legacy surfaces can only be re-enabled in production via explicit allow flag.
  - Runtime diagnostics expose surface contract and override warnings.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B10
- Priority: `P2`
- Category: `e2e-confidence`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` runtime E2E validation expectations
- Problem: Real Gmail/Chrome runtime tests remain opt-in due environment prerequisites.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.browser-harness.test.cjs`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/scripts/gmail-e2e-runner-preflight.cjs`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/package.json`
  - `/Users/mombalam/Desktop/Clearledgr.v1/.github/workflows/gmail-extension-browser-harness.yml`
  - `/Users/mombalam/Desktop/Clearledgr.v1/.github/workflows/gmail-runtime-smoke-nightly.yml`
  - `/Users/mombalam/Desktop/Clearledgr.v1/README.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/README.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/GMAIL_RUNTIME_RUNNER_SETUP.md`
- Locked decision:
  - Browser harness has a required-browser CI mode (`GMAIL_BROWSER_HARNESS_REQUIRE_BROWSER=1`) to prevent silent skip in deterministic CI.
  - Real Gmail runtime smoke runs nightly on a controlled self-hosted runner with authenticated profile secret (`GMAIL_E2E_PROFILE_DIR`).
- Acceptance criteria:
  - Extension-change PRs/pushes run deterministic browser harness in CI.
  - Nightly workflow executes authenticated Gmail runtime smoke and publishes evidence artifacts.
- Validation/tests:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && node --test tests/inboxsdk-layer.browser-harness.test.cjs tests/inboxsdk-layer.e2e-smoke.test.cjs` (opt-in guards verified)
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && RUN_GMAIL_BROWSER_HARNESS=1 GMAIL_BROWSER_HARNESS_REQUIRE_BROWSER=1 node --test tests/inboxsdk-layer.browser-harness.test.cjs` (required-mode fail-fast verified when browser prerequisites unavailable)
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && GMAIL_E2E_PREFLIGHT_SKIP_BROWSER_LAUNCH=1 node scripts/gmail-e2e-runner-preflight.cjs --profile-dir <profile_dir>` (runner preflight contract verified)
  - Workflow YAML + script references verified in repo.

### B11
- Priority: `P0`
- Category: `agent-intents-tenant-scope`
- Status: `DONE`
- Type: `broken`
- Plan refs: `PLAN.md` auth boundary + tenant isolation controls
- Problem: `/api/agent/intents/*` accepted caller-provided `organization_id` without org-access enforcement.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_intents.py` (`_resolve_org_id_for_user`, `preview_intent`, `execute_intent`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_preview_intent_endpoint_blocks_cross_org_request`, `test_execute_intent_endpoint_blocks_cross_org_request`, `test_execute_intent_endpoint_allows_admin_cross_org_request`)
- Acceptance criteria:
  - Non-admin authenticated caller receives `403 org_mismatch` for cross-org preview/execute request.
  - Admin caller can target another org.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B12
- Priority: `P0`
- Category: `ap-retry-post-canonical-path`
- Status: `DONE`
- Type: `placeholder`
- Plan refs: `PLAN.md` AP state machine legal retry path and durable retry semantics
- Problem: `/api/ap/items/{id}/retry-post` used connector placeholder/import fallback instead of canonical workflow recovery path.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` (`retry_erp_post`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`TestAPRetryPostEndpoint`)
- Acceptance criteria:
  - Retry endpoint delegates to `InvoiceWorkflowService.resume_workflow()` and returns canonical recovered/still-failing/not-resumable outcomes.
  - Placeholder `ImportError` path is removed.
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B13
- Priority: `P0`
- Category: `gmail-oauth-state-and-push-hardening`
- Status: `DONE`
- Type: `fragile`
- Plan refs: `PLAN.md` callback verifier + auth boundary + secure callback contracts
- Problem: OAuth state was unsigned/tamperable and production `/gmail/push` could run without callback verifier.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py` (`_sign_oauth_state`, `_unsign_oauth_state`, `_enforce_push_verifier`, `gmail_authorize`, `gmail_callback`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (`test_gmail_push_prod_requires_verifier_secret_by_default`, `test_gmail_push_prod_can_allow_unverified_with_explicit_flag`, `test_gmail_authorize_uses_signed_state`, `test_gmail_callback_requires_oauth_state`, `test_gmail_callback_rejects_tampered_oauth_state`)
- Acceptance criteria:
  - OAuth callback rejects missing/tampered state (`400`) and uses signed+ttl-bounded state.
  - Production-like env requires push verifier secret by default (unless explicitly overridden with allow flag).
- Validation/tests:
  - Included in combined command listed in Evidence section.

### B14
- Priority: `P1`
- Category: `deployment-config-parity`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` deployment/runtime truth-in-claims and security config readiness
- Problem: config templates omitted required runtime vars for Teams verifier, Gmail push verifier, OAuth state TTL, and production strict-surface guardrails.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/env.example`
  - `/Users/mombalam/Desktop/Clearledgr.v1/render.yaml`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docker-compose.yml`
- Acceptance criteria:
  - Required variables are documented and present in deploy templates with secure defaults.
- Validation/tests:
  - Configuration parity verified by direct file inspection and regression tests in Evidence section.

## Evidence (this cycle)
- Command:
  - `PYTHONPATH=. pytest tests/test_api_endpoints.py::TestGmailWebhooks tests/test_api_endpoints.py::TestExtensionEndpoints::test_extension_match_endpoints_return_results_for_authorized_user -q`
  - Result: `9 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_agent_runtime.py::test_ap_skill_get_ap_decision_handles_sync_decider_without_fallback tests/test_browser_agent_layer.py::test_agent_session_endpoints_enforce_org_scope -q`
  - Result: `2 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_api_endpoints.py::TestGmailWebhooks tests/test_api_endpoints.py::TestExtensionEndpoints::test_extension_match_endpoints_return_results_for_authorized_user tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_forces_agentic_mode_in_production_when_opt_out_requested tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_ignores_legacy_fallback_flag_in_production tests/test_agent_orchestrator_durable_retry.py::test_runtime_status_exposes_execution_contract tests/test_browser_agent_layer.py::test_autopilot_status_includes_agent_runtime_truth_claims tests/test_browser_agent_layer.py::test_autopilot_status_keeps_durable_retry_enabled_in_production -q`
  - Result: `14 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_api_endpoints.py::TestGmailWebhooks tests/test_api_endpoints.py::TestExtensionEndpoints::test_extension_match_endpoints_return_results_for_authorized_user tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_forces_agentic_mode_in_production_when_opt_out_requested tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_ignores_legacy_fallback_flag_in_production tests/test_agent_orchestrator_durable_retry.py::test_runtime_status_exposes_execution_contract tests/test_browser_agent_layer.py::test_autopilot_status_includes_agent_runtime_truth_claims tests/test_browser_agent_layer.py::test_autopilot_status_keeps_durable_retry_enabled_in_production tests/test_runtime_surface_scope.py -q`
  - Result: `18 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_api_endpoints.py::TestGmailWebhooks tests/test_api_endpoints.py::TestAgentIntentEndpoints tests/test_api_endpoints.py::TestAPRetryPostEndpoint -q`
  - Result: `22 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_forces_agentic_mode_in_production_when_opt_out_requested tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_ignores_legacy_fallback_flag_in_production tests/test_agent_orchestrator_durable_retry.py::test_runtime_status_exposes_execution_contract tests/test_browser_agent_layer.py::test_autopilot_status_includes_agent_runtime_truth_claims tests/test_browser_agent_layer.py::test_autopilot_status_keeps_durable_retry_enabled_in_production tests/test_runtime_surface_scope.py -q`
  - Result: `9 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_api_endpoints.py::TestGmailWebhooks tests/test_api_endpoints.py::TestExtensionEndpoints::test_extension_match_endpoints_return_results_for_authorized_user tests/test_api_endpoints.py::TestAgentIntentEndpoints tests/test_api_endpoints.py::TestAPRetryPostEndpoint tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_forces_agentic_mode_in_production_when_opt_out_requested tests/test_agent_orchestrator_durable_retry.py::test_process_invoice_ignores_legacy_fallback_flag_in_production tests/test_agent_orchestrator_durable_retry.py::test_runtime_status_exposes_execution_contract tests/test_browser_agent_layer.py::test_autopilot_status_includes_agent_runtime_truth_claims tests/test_browser_agent_layer.py::test_autopilot_status_keeps_durable_retry_enabled_in_production tests/test_runtime_surface_scope.py -q`
  - Result: `32 passed`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && node --test tests/inboxsdk-layer.browser-harness.test.cjs tests/inboxsdk-layer.e2e-smoke.test.cjs`
  - Result: `2 passed, 2 skipped`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && RUN_GMAIL_BROWSER_HARNESS=1 GMAIL_BROWSER_HARNESS_REQUIRE_BROWSER=1 node --test tests/inboxsdk-layer.browser-harness.test.cjs`
  - Result: `1 failed (expected in environment without installed launchable browser); verifies required-mode fail-fast behavior`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && GMAIL_E2E_PREFLIGHT_SKIP_BROWSER_LAUNCH=1 node scripts/gmail-e2e-runner-preflight.cjs --profile-dir <tmp_profile>`
  - Result: `status=ok` with validated profile-dir contract in this environment.
- Command:
  - `gh workflow run gmail-runtime-smoke-nightly.yml --repo clearledgr/Clearledgr-AP --ref main -f release_id=activation-20260228-run122837` then `gh run watch 22520751038 --repo clearledgr/Clearledgr-AP --exit-status`
  - Result: `success` (`https://github.com/clearledgr/Clearledgr-AP/actions/runs/22520751038`) with uploaded artifact bundle including `GMAIL_RUNTIME_E2E.md`, `gmail-e2e-evidence.json`, `gmail-e2e-screenshot.png`.

## Change log
- 2026-02-28:
  - Added Gmail activity module used by extension/webhook flows.
  - Hardened Gmail status/disconnect auth boundaries.
  - Added push payload validation + optional shared-secret verifier.
  - Propagated tenant org in webhook invoice/payment-request processing.
  - Added/updated regression tests for all above paths.
  - Enforced production runtime contract (agentic mode forced on, legacy fallback forced off) with explicit ops visibility.
  - Completed B09 AP-v1 surface contract hardening with production legacy-override guard and strict-surface diagnostics in ops status.
  - Completed B10 with deterministic browser-harness CI workflow and nightly controlled Gmail runtime smoke workflow with evidence upload.
  - Added Gmail runtime runner setup guide + runner preflight script for nightly workflow operational readiness checks.
  - Activation completed: self-hosted runner `clearledgr-gmail-e2e-mac` is online, required secret/vars configured, and manual nightly smoke dispatch succeeded with passing evidence.
  - Closed B11 by enforcing org scoping on `/api/agent/intents/*` with explicit non-admin cross-org denial tests.
  - Closed B12 by replacing `/api/ap/items/{id}/retry-post` placeholder ERP import path with canonical `resume_workflow()` recovery path and response mapping.
  - Closed B13 by adding signed+ttl-bounded Gmail OAuth state validation and production push-verifier enforcement defaults.
  - Closed B14 by aligning `env.example`, `render.yaml`, and `docker-compose.yml` with required Teams/Gmail/runtime security flags.

## Archive protocol
- Keep this file as the live tracker for current-cycle items.
- Move to `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/` only when all `OPEN` items are `DONE` or explicitly marked accepted risk with owner + expiry.
