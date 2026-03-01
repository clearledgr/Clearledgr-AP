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
| B15 | P1 | gmail-sidebar-work-only | DONE | partial |
| B16 | P1 | inline-reason-sheet-migration | DONE | missing |
| B17 | P1 | work-panel-action-first-compression | DONE | partial |
| B18 | P1 | ops-console-telemetry-relocation | DONE | partial |
| B19 | P1 | gmail-sidebar-regression-realignment | DONE | missing |
| B20 | P0 | codebase-scope-audit | DONE | missing |
| B21 | P0 | repository-hygiene | DONE | partial |
| B22 | P1 | legacy-route-retirement | DONE | missing |
| B23 | P1 | off-plan-module-deprecation | DONE | partial |
| B24 | P1 | legacy-test-suite-retirement | DONE | partial |
| B25 | P1 | audit-operator-contract | DONE | missing |
| B26 | P0 | runtime-canonical-contracts | DONE | missing |
| B27 | P1 | agent-intents-canonical-api | DONE | missing |
| B28 | P1 | erp-adapter-contract-seam | DONE | partial |
| B29 | P1 | runtime-contract-regression-coverage | DONE | missing |
| B30 | P1 | skill-package-manifest-readiness | DONE | missing |
| B31 | P1 | connector-readiness-hardening | DONE | partial |
| B32 | P1 | learning-calibration-pipeline | DONE | missing |
| B33 | P1 | additional-skill-launch | DONE | missing |

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
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (`_runtime_surface_contract`, `_apply_runtime_surface_profile`, strict allowlist contract)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py` (`_resolve_runtime_surface_contract`, `get_autopilot_status`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py`
- Locked decision:
  - AP-v1 runtime surface is strict-only across environments.
  - Runtime surface contract and ignored legacy-flag warnings are exposed in `/api/ops/autopilot-status`.
- Acceptance criteria:
  - Strict profile keeps non-canonical legacy routes unmounted/blocked by default in every environment.
  - Legacy surface env flags do not re-enable deleted or non-canonical route families.
  - Runtime diagnostics expose strict contract and ignored-flag warnings.
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

### B15
- Priority: `P1`
- Category: `gmail-sidebar-work-only`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `3.1` (Gmail operator surface), section `3.2` (progressive disclosure), section `3.3` (UI anti-patterns)
- Problem: mixed Gmail surface forced operator decisions, KPI telemetry, batch controls, and deep audit into one cluttered panel.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (`initializeSidebar`, `renderWorkModeThreadContext`, `renderThreadContext`, `getWorkPrimaryAction`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.integration.test.cjs` (`single Gmail panel + action-first assertions`)
- Acceptance criteria:
  - Gmail renders only `Clearledgr AP` as default operator surface.
  - Work panel no longer renders KPI, batch, raw events, or full diagnostics sections.
  - Work panel keeps decision-first layout with one primary CTA + collapsed evidence/audit.
- Validation/tests:
  - Included in combined extension command listed in Evidence section.

### B16
- Priority: `P1`
- Category: `inline-reason-sheet-migration`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` section `3.2` (progressive disclosure), AP decision rationale/audit requirements in section `4`
- Problem: action reason capture used brittle native browser dialogs and inconsistent prompt paths.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (`getReasonSheetDefaults`, `requestActionInput`, `openReasonSheet`, action handlers in `renderThreadContext`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.integration.test.cjs` (inline reason-sheet interaction coverage)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer-ui.test.cjs` (`reason capture path contains no native prompt/confirm calls`)
- Acceptance criteria:
  - Reject/override/budget/escalation paths use inline reason sheet instead of native prompt/confirm.
  - Required vs optional reason semantics enforced by action type.
- Validation/tests:
  - Included in combined extension command listed in Evidence section.

### B17
- Priority: `P1`
- Category: `work-panel-action-first-compression`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `3.1`, section `3.2`
- Problem: Work panel previously exposed excessive narrative and diagnostic density in the default decision path.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (`renderThreadContext`, `renderAgentActions`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.integration.test.cjs` (operator brief + action-first assertions)
- Acceptance criteria:
  - Work panel keeps decision brief + primary actions above fold.
  - Evidence/technical sections are collapsed details blocks.
  - Work panel shows compact recent activity with clear handoff to Ops.
- Validation/tests:
  - Included in combined extension command listed in Evidence section.

### B18
- Priority: `P1`
- Category: `ops-console-telemetry-relocation`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `3.3` (avoid dashboard bloat in Work surface), section `7` observability gates
- Problem: batch operations and full telemetry were mixed into operator decision surface.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js` (`opsPage`, `runOpsBatchAction`, retry-queue handlers, ops deep-link boot)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (`Open Ops Console` admin/operator link only)
- Acceptance criteria:
  - Admin Console Ops is canonical location for KPI snapshot, batch controls, retry queue actions, and operational diagnostics.
  - Gmail Work surface exposes only an admin/operator-gated `Open Ops Console` link.
- Validation/tests:
  - Included in combined extension command listed in Evidence section.

### B19
- Priority: `P1`
- Category: `gmail-sidebar-regression-realignment`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` section `3.2` + Beta quality gates in section `8`
- Problem: previous suite lacked explicit assertions for work-only Gmail layout, state-mapped CTAs, and inline reason-sheet paths.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-integration-harness.cjs`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.integration.test.cjs`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer-ui.test.cjs`
- Acceptance criteria:
  - Work-only Gmail assertions are enforced in integration tests.
  - `needs_approval` never renders `Approve & Post`.
  - Inline reason-sheet entry paths are covered.
  - Guard assertion verifies no native prompt/confirm path in source.
- Validation/tests:
  - Included in combined extension command listed in Evidence section.

### B20
- Priority: `P0`
- Category: `codebase-scope-audit`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` section `1.2`, section `2.4`, section `10`; `README.md` `Canonical Doctrine`
- Problem: Repo had no explicit audited keep/deprecate/delete matrix against canonical AP-v1 doctrine.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/PLAN_README_CODEBASE_SCOPE_AUDIT.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (`_runtime_surface_contract`, strict surface profile blocks)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/*.py` route-family inventory
- Acceptance criteria:
  - Route and file inventory classified into canonical keep vs off-plan deprecate/delete classes with concrete file evidence.
- Validation/tests:
  - Audit generated from live route table and tracked file inventory; no behavioral test change.

### B21
- Priority: `P0`
- Category: `repository-hygiene`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `10` (doc governance consistency), `README.md` AP-v1 doctrine
- Problem: Tracked runtime/build artifacts and vendored dependencies caused major repository drift and handoff risk.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/.gitignore`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/node_modules/` (deleted from working tree)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/build/` (deleted from working tree)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/state/learning.db` (deleted from working tree)
  - `/Users/mombalam/Desktop/Clearledgr.v1/email_tasks.sqlite3` (untracked from git index; ignored locally)
  - `/Users/mombalam/Desktop/Clearledgr.v1/task_scheduler.sqlite3` (untracked from git index; ignored locally)
  - `/Users/mombalam/Desktop/Clearledgr.v1/.uvicorn.pid` (removed from repo index)
  - `/Users/mombalam/Desktop/Clearledgr.v1/audit_trail.sqlite3` (removed from repo index)
  - `/Users/mombalam/Desktop/Clearledgr.v1/server*.log` (removed from repo index)
  - `/Users/mombalam/Desktop/Clearledgr.v1/project layout` (removed from repo index)
  - `/Users/mombalam/Desktop/Clearledgr.v1/yc_agent_session.md` (removed from repo index)
- Acceptance criteria:
  - Tracked local/runtime artifacts removed from repo.
  - Ignore rules prevent reintroduction.
- Validation/tests:
  - `git rm --cached email_tasks.sqlite3 task_scheduler.sqlite3`
  - `git rm .uvicorn.pid audit_trail.sqlite3 clearledgr/state/learning.db server-8010.log server.log server8010.log server_8010.log "project layout" yc_agent_session.md`
  - `git ls-files | rg '(\\.sqlite3$|\\.sqlite$|\\.db$|\\.pid$|server.*\\.log$|clearledgr/state/learning\\.db)'` returns no matches.
  - `git rm -r ui/gmail-extension/node_modules ui/gmail-extension/build`
  - `git ls-files | rg 'ui/gmail-extension/(node_modules|build)/'` returns no matches.
  - `git status --short | rg '(sqlite3|\\.pid|server.*\\.log|node_modules|ui/gmail-extension/build/)'` shows only cleanup deletions in progress.

### B22
- Priority: `P1`
- Category: `legacy-route-retirement`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` section `1.2`, section `2.4`, section `3.3`, section `3.6`
- Problem: AP-v1 strict surfaces still had env-driven full/legacy runtime toggles that could drift behavior across environments.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (`_runtime_surface_contract`, `_apply_runtime_surface_profile`, `LegacySurfaceGuardMiddleware`, `custom_openapi`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ops.py` (`_resolve_runtime_surface_contract` diagnostics alignment)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_multi_tenant_isolation.py`
- Acceptance criteria:
  - Runtime surface is strict-only in all environments (no full/legacy execution profile).
  - Legacy/full env flags are explicitly ignored and surfaced as warnings in runtime diagnostics.
  - OpenAPI and middleware enforce canonical AP-v1 surfaces only.
- Validation/tests:
  - `pytest tests/test_runtime_surface_scope.py tests/test_browser_agent_layer.py tests/test_multi_tenant_isolation.py`

### B23
- Priority: `P1`
- Category: `off-plan-module-deprecation`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `2.4`
- Problem: Off-plan modules remained in canonical tree and increased drift from AP-v1 scope.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/demo/` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/marketplace/` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/sheets/` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/slack/demo.html` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ai_enhanced.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_advanced.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_workflow.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/bank_feeds.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/engine.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/llm_proxy.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/outlook_webhooks.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/payment_requests.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/payments.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/analytics.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/learning.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/subscription.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/reconciliation_engine.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/reconciliation_runner.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/reconciliation_inputs.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/bank_feeds/` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/accruals.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/recurring_detection.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/journal_entries.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/payment_scheduler.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/cashflow_prediction.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/engine.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ai_enhanced.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/validation.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/matching.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/intelligent_matching.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/reconciliation.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/journal_entries.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/__init__.py` (removed lazy export for `JournalEntryService`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/__init__.py` (removed reconciliation model exports)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/requests.py` (removed legacy `ReconciliationRequest`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` (removed recurring-subscription branch)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_webhooks.py` (removed legacy reconciliation-engine + ai_enhanced coupling from AP intake path)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/gmail_autopilot.py` (removed legacy reconciliation-engine + ai_enhanced coupling; AP-only poll path)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_context_connectors.py` (payroll connector hard-disabled for AP-v1 scope)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_aging.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/bank_statement_parser.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/batch_intelligence.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/credit_notes.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/csv_parser.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/document_retention.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/email_matcher.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/eu_vat_validation.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/exception_priority.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/flutterwave_client.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/multi_factor_scoring.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/outlook_api.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/pattern_learning.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/paystack_client.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/realtime_sync.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sheets_integration.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/stripe_client.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/tax_calculations.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/transaction_quality.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/vita_audit.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/payment_execution.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/recurring_management.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/expense_workflow.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sap.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sheets_api.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/llm.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/notifications.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/natural_language_commands.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/optimal_matching.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/explainability.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/approval_chains.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_sync.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/exceptions.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/multi_currency.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/early_payment_discounts.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/audit.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/rate_limit.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/legacy_engine_store.py` (removed legacy `payments` schema + `save_payment` + AP payment CRUD methods)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py` (removed AP payment scheduling branch from ERP post path)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` (removed `early_payment_discount` payload exposure)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (removed discount banner/chip rendering)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/dist/inboxsdk-layer.js` (synced compiled sidebar bundle without discount banner/chip)
  - `/Users/mombalam/Desktop/Clearledgr.v1/main.py` (removed legacy include_router blocks for `analytics`, `learning`, `subscription`; removed strict-surface allowlist prefixes for those families)
  - `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js` (replaced legacy `/analytics/dashboard/*` fetches with canonical `/api/admin/bootstrap` source)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_controls.py` (removed recurring-service test coupling)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_policy_framework.py` (removed recurring-service test coupling)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py` (removed recurring-service test coupling)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_multi_system_context.py` (realigned payroll connector assertions to disabled contract)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` (realigned `process_single_email` test from legacy engine dependency to DB-backed AP intake; removed legacy `/analytics` and `/learning` endpoint suites)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` (removed fallback `/analytics/dashboard/*` coverage in favor of canonical admin-bootstrap dashboard assertions)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/conftest.py` (removed singleton reset references to retired recurring/payment services)
- Acceptance criteria:
  - Off-plan module files are physically removed from tracked tree.
  - Canonical runtime imports do not reference removed modules.
- Validation/tests:
  - `rg --files | rg '(^|/)demo(/|$)|(^|/)marketplace(/|$)|(^|/)ui/sheets(/|$)|(^|/)ui/slack/demo\\.html$'` returns no matches.
  - `rg --files /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api | rg '(ai_enhanced|ap_advanced|ap_workflow|bank_feeds|engine|llm_proxy|outlook_webhooks|payment_requests|payments|analytics|learning|subscription)\\.py$'` returns no matches.
  - `rg --files /Users/mombalam/Desktop/Clearledgr.v1/clearledgr | rg '(reconciliation_engine\\.py|reconciliation_runner\\.py|reconciliation_inputs\\.py|services/bank_feeds/)'` returns no matches.
  - `rg -n 'services\\.accruals|accruals\\.py|get_accruals_service|services\\.recurring_detection|get_recurring_detector|services\\.journal_entries|JournalEntryService|services\\.payment_scheduler|get_payment_scheduler|services\\.cashflow_prediction|get_cashflow_predictor' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py` returns no runtime/test matches.
  - `rg -n 'from clearledgr\\.core\\.engine import|get_engine\\(|from clearledgr\\.services\\.ai_enhanced import|EnhancedAIService|services\\.validation|SheetsRunRequest|PeriodDates' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py` returns no matches.
  - `rg -n 'services\\.matching|match_bank_to_gl|services\\.intelligent_matching|IntelligentMatchingService|models\\.reconciliation|ReconciliationRequest|ReconciliationConfig|ReconciliationResult|ReconciliationMatch|models\\.journal_entries|DraftJournalEntry' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py` returns no matches.
  - `rg -n 'from clearledgr\\.services\\.(payment_execution|recurring_management|expense_workflow|sap|sheets_api|llm|notifications|natural_language_commands|optimal_matching|explainability|ap_aging|bank_statement_parser|batch_intelligence|credit_notes|csv_parser|document_retention|email_matcher|eu_vat_validation|exception_priority|flutterwave_client|multi_factor_scoring|outlook_api|pattern_learning|paystack_client|realtime_sync|sheets_integration|stripe_client|tax_calculations|transaction_quality|vita_audit|approval_chains|erp_sync|exceptions|multi_currency) import|clearledgr\\.services\\.(payment_execution|recurring_management|expense_workflow|sap|sheets_api|llm|notifications|natural_language_commands|optimal_matching|explainability|ap_aging|bank_statement_parser|batch_intelligence|credit_notes|csv_parser|document_retention|email_matcher|eu_vat_validation|exception_priority|flutterwave_client|multi_factor_scoring|outlook_api|pattern_learning|paystack_client|realtime_sync|sheets_integration|stripe_client|tax_calculations|transaction_quality|vita_audit|approval_chains|erp_sync|exceptions|multi_currency)\\b' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py` returns no matches.
  - `rg -n 'from clearledgr\\.core\\.(audit|rate_limit) import|clearledgr\\.core\\.(audit|rate_limit)\\b' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py` returns no matches.
  - `rg -n 'early_payment_discount|EarlyPaymentDiscount|discount_opportunity' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src /Users/mombalam/Desktop/Clearledgr.v1/tests` returns no AP-v1 runtime/test matches.
  - `rg -n 'save_ap_payment|get_ap_payments\\(|get_ap_payment\\(|update_ap_payment\\(|get_ap_payments_summary\\(|save_payment\\(' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests` returns no matches.
  - `rg -n '/analytics|/learning|/subscription|analytics_router|learning_router|subscription_router|clearledgr\\.api\\.(analytics|learning|subscription)' /Users/mombalam/Desktop/Clearledgr.v1/main.py /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js` returns no legacy route-family matches (except canonical admin-console subscription controls).
  - `PYTHONPATH=/Users/mombalam/Desktop/Clearledgr.v1 pytest -q /Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` returns `82 passed`.

### B24
- Priority: `P1`
- Category: `legacy-test-suite-retirement`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` section `10`; `README.md` AP-v1 workflow scope
- Problem: Test suites still assert behavior for non-canonical legacy endpoints (analytics, legacy `/ap/*`, etc.), creating false compatibility pressure.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_engine.py` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow.py` (removed 2026-03-01)
- Acceptance criteria:
  - Legacy endpoint assertions for removed `/ap/payments` and `/ap/recurring` families are removed.
  - Reconciliation-focused legacy suites are removed.
  - Default CI suite validates AP-v1 canonical contract only.
- Validation/tests:
  - `PYTHONPATH=/Users/mombalam/Desktop/Clearledgr.v1 pytest -q /Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`

### B25
- Priority: `P1`
- Category: `audit-operator-contract`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` section `7.2` (observable rejected/logged transitions), section `7.6` (operator traceability), `README.md` operator trust clarity
- Problem: Gmail Work surface previously owned event/reason copy mapping, which created frontend drift risk and forced technical reason codes to leak when mappings diverged.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_operator_audit.py` (canonical backend operator audit normalization contract)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` (`GET /api/ap/items/{ap_item_id}/audit` now emits normalized `operator_*` fields)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js` (consumes backend `operator_title`/`operator_message` contract)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_operator_audit.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_browser_agent_layer.py` (`test_browser_evidence_is_queryable_via_audit_endpoint`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/tests/inboxsdk-layer.integration.test.cjs`
- Acceptance criteria:
  - Backend owns operator-readable audit wording and severity intent for common AP events.
  - Gmail Work audit rendering uses backend contract fields instead of frontend reason-code phrase maps.
  - Audit endpoint still returns canonical raw fields and now includes additive normalized operator fields.
- Validation/tests:
  - Included in current-cycle validation commands listed in Evidence section.

### B26
- Priority: `P0`
- Category: `runtime-canonical-contracts`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` one-runtime doctrine + canonical interface requirements
- Problem: Runtime intent API lacked a typed canonical request/response/action contract shared across skills.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/finance_contracts.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/base.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_contracts.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_agent_runtime.py`
- Acceptance criteria:
  - Canonical `SkillRequest`, `SkillResponse`, `ActionExecution`, `AuditEvent` contracts exist and are used by runtime dispatch.
  - Runtime preview/execute outputs include canonical fields (`recommended_next_action`, `legal_actions`, `blockers`, `confidence`, `evidence_refs`).

### B27
- Priority: `P1`
- Category: `agent-intents-canonical-api`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` canonical public API contracts
- Problem: Public intent APIs only supported legacy `intent + input` body shape.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_intents.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
- Acceptance criteria:
  - Added canonical request endpoints:
    - `POST /api/agent/intents/preview-request`
    - `POST /api/agent/intents/execute-request`
  - Added skill registry endpoint:
    - `GET /api/agent/intents/skills`
  - Legacy preview/execute endpoints remain backward-compatible.

### B28
- Priority: `P1`
- Category: `erp-adapter-contract-seam`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` ERP adapter interface and connector parity doctrine
- Problem: API-first ERP posting path used direct router call with no provider-agnostic adapter seam.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp/contracts.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_adapter_contracts.py`
- Acceptance criteria:
  - Canonical ERP adapter contract added with `validate`, `post`, `get_status`, `reconcile`.
  - API-first posting path routes through adapter contract seam.

### B29
- Priority: `P1`
- Category: `runtime-contract-regression-coverage`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` launch-gate testability and contract evidence
- Problem: No direct regression suite proving canonical runtime contracts and new intent endpoints.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_contracts.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_adapter_contracts.py`
- Acceptance criteria:
  - Regression tests verify canonical contract payloads and endpoint behavior.
  - Audit metadata includes canonical audit event schema fields.

### B30
- Priority: `P1`
- Category: `skill-package-manifest-readiness`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` canonical runtime interfaces (`C.2` skill package manifest gate); `README.md` one-runtime + multi-skill expansion doctrine
- Problem: Skill registry exposed intents only; runtime had no formal capability package contract and no readiness gate endpoint for skill promotion.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/finance_contracts.py` (`SkillCapabilityManifest`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/base.py` (`manifest` contract requirement)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/ap_skill.py` (AP skill package manifest)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/workflow_health_skill.py` (read-only manifest contract)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py` (`list_skills`, `skill_readiness_summary`, `skill_readiness`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_intents.py` (`GET /api/agent/intents/skills/{skill_id}/readiness`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_contracts.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
- Acceptance criteria:
  - Every runtime skill publishes required capability sections: state machine, action catalog, policy pack, evidence schema, adapter bindings, KPI contract.
  - `GET /api/agent/intents/skills` returns manifest + manifest validation/readiness summary.
  - `GET /api/agent/intents/skills/{skill_id}/readiness` returns explicit gate statuses (`pass`/`fail`/`not_verifiable`) instead of implied readiness.
  - Non-AP skills without runtime metrics are reported as `manifest_only`, not incorrectly marked ready.

### B31
- Priority: `P1`
- Category: `connector-readiness-hardening`
- Status: `DONE`
- Type: `partial`
- Plan refs: `PLAN.md` `6.5` adapter expansion rule, `9.4` ERP readiness validation, `8.4` GA acceptance criteria.
- Problem: connector strategy existed, but there was no unified readiness evaluator combining checklist evidence, configured connectors, and rollback controls.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_readiness.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/ap_skill.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/admin_console.py` (`GET /api/admin/ops/connector-readiness`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_erp_readiness.py`
- Acceptance criteria:
  - Runtime AP readiness includes connector readiness gate (`enabled_connector_readiness`).
  - Admin Ops exposes per-connector readiness details and blockers.
  - Rollback-disabled connectors are surfaced as blocked readiness.
- Validation/tests:
  - `PYTHONPATH=. pytest -q tests/test_erp_readiness.py tests/test_finance_agent_runtime.py tests/test_admin_launch_controls.py`

### B32
- Priority: `P1`
- Category: `learning-calibration-pipeline`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` trust/reliability metrics and AP learning-loop doctrine.
- Problem: feedback data existed, but no persisted tenant calibration snapshot pipeline for operations and review.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/learning_calibration.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/admin_console.py` (`GET /api/admin/ops/learning-calibration`, `POST /api/admin/ops/learning-calibration/recompute`)
  - `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_learning_calibration.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_admin_launch_controls.py`
- Acceptance criteria:
  - Recompute persists versioned calibration snapshots.
  - Ops can retrieve latest snapshot and recommendations.
  - Snapshot includes disagreement/override metrics and top vendor gaps.
- Validation/tests:
  - `PYTHONPATH=. pytest -q tests/test_learning_calibration.py tests/test_admin_launch_controls.py`

### B33
- Priority: `P1`
- Category: `additional-skill-launch`
- Status: `DONE`
- Type: `missing`
- Plan refs: `PLAN.md` one-runtime + multi-skill expansion doctrine.
- Problem: runtime needed an additional concrete skill package beyond AP/workflow-health to prove expansion path.
- Code touchpoints:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/vendor_compliance_skill.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/__init__.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_finance_agent_runtime.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py::TestAgentIntentEndpoints`
- Acceptance criteria:
  - New skill ships with full manifest contract sections.
  - Skill is discoverable via runtime/API skill registry.
  - Preview/execute returns canonical contract fields.
- Validation/tests:
  - `PYTHONPATH=. pytest -q tests/test_finance_agent_runtime.py tests/test_api_endpoints.py::TestAgentIntentEndpoints`

## Evidence (this cycle)
- Command:
  - `PYTHONPATH=. pytest -q tests/test_erp_readiness.py tests/test_learning_calibration.py tests/test_finance_agent_runtime.py tests/test_admin_launch_controls.py tests/test_api_endpoints.py::TestAgentIntentEndpoints`
  - Result: `36 passed`
- Command:
  - `PYTHONPATH=/Users/mombalam/Desktop/Clearledgr.v1 pytest -q tests/test_invoice_workflow_controls.py tests/test_ap_policy_framework.py tests/test_invoice_workflow_runtime_state_transitions.py tests/test_ap_multi_system_context.py tests/test_runtime_surface_scope.py tests/test_api_endpoints.py`
  - Result: `100 passed`
- Command:
  - `PYTHONPATH=. pytest -q tests/test_finance_contracts.py tests/test_finance_agent_runtime.py tests/test_erp_adapter_contracts.py tests/test_erp_api_first.py tests/test_api_endpoints.py::TestAgentIntentEndpoints`
  - Result: `37 passed`
- Command:
  - `PYTHONPATH=. pytest -q tests/test_finance_contracts.py tests/test_finance_agent_runtime.py tests/test_api_endpoints.py::TestAgentIntentEndpoints`
  - Result: `31 passed`
- Command:
  - `PYTHONPATH=/Users/mombalam/Desktop/Clearledgr.v1 pytest -q /Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
  - Result: `59 passed`
- Command:
  - `rg --files /Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api | rg '(ai_enhanced|ap_advanced|ap_workflow|bank_feeds|engine|llm_proxy|outlook_webhooks|payment_requests|payments)\\.py$'`
  - Result: `no matches`
- Command:
  - `rg --files /Users/mombalam/Desktop/Clearledgr.v1/clearledgr | rg '(reconciliation_engine\\.py|reconciliation_runner\\.py|reconciliation_inputs\\.py|services/bank_feeds/)'`
  - Result: `no matches`
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
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && node --test tests/inboxsdk-layer.integration.test.cjs tests/inboxsdk-layer-ui.test.cjs`
  - Result: `26 passed`
- Command:
  - `PYTHONPATH=. pytest tests/test_ap_operator_audit.py tests/test_browser_agent_layer.py::test_browser_evidence_is_queryable_via_audit_endpoint -q`
  - Result: `4 passed`
- Command:
  - `PYTHONPATH=/Users/mombalam/Desktop/Clearledgr.v1 pytest -q /Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_controls.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_policy_framework.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_invoice_workflow_runtime_state_transitions.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_multi_system_context.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_runtime_surface_scope.py /Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py`
  - Result: `100 passed`
- Command:
  - `rg -n 'services\\.accruals|accruals\\.py|get_accruals_service|services\\.recurring_detection|get_recurring_detector|services\\.journal_entries|JournalEntryService|services\\.payment_scheduler|get_payment_scheduler|services\\.cashflow_prediction|get_cashflow_predictor' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py`
  - Result: `no matches`
- Command:
  - `rg -n 'from clearledgr\\.core\\.engine import|get_engine\\(|from clearledgr\\.services\\.ai_enhanced import|EnhancedAIService|services\\.validation|SheetsRunRequest|PeriodDates' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py`
  - Result: `no matches`
- Command:
  - `rg -n 'services\\.matching|match_bank_to_gl|services\\.intelligent_matching|IntelligentMatchingService|models\\.reconciliation|ReconciliationRequest|ReconciliationConfig|ReconciliationResult|ReconciliationMatch|models\\.journal_entries|DraftJournalEntry' /Users/mombalam/Desktop/Clearledgr.v1/clearledgr /Users/mombalam/Desktop/Clearledgr.v1/tests /Users/mombalam/Desktop/Clearledgr.v1/main.py`
  - Result: `no matches`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && node --test tests/inboxsdk-layer.integration.test.cjs`
  - Result: `7 passed`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && npm test`
  - Result: `38 passed, 2 skipped`
- Command:
  - `cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension && npm run build`
  - Result: `webpack build succeeded` and updated extension bundle.
- Command:
  - `pytest tests/test_runtime_surface_scope.py tests/test_browser_agent_layer.py tests/test_multi_tenant_isolation.py`
  - Result: `39 passed`
- Command:
  - `git rm --cached email_tasks.sqlite3 task_scheduler.sqlite3 && git rm .uvicorn.pid audit_trail.sqlite3 clearledgr/state/learning.db server-8010.log server.log server8010.log server_8010.log "project layout" yc_agent_session.md && git rm -r ui/gmail-extension/node_modules ui/gmail-extension/build && git ls-files | rg '(\\.sqlite3$|\\.sqlite$|\\.db$|\\.pid$|server.*\\.log$|clearledgr/state/learning\\.db|ui/gmail-extension/(node_modules|build)/)'`
  - Result: `runtime db/pid/log/build/dependency artifacts removed from index; no tracked matches`
- Artifacts:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/GMAIL_SIDEBAR_RESET_EVIDENCE.md`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-reset-before.png`
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/ga-evidence/releases/ap-v1-2026-02-25-pilot-rc1/artifacts/sidebar-reset-after-work.png`

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
- 2026-03-01:
  - Closed B15 by enforcing a single Gmail `Clearledgr AP` work-only surface with strict state-to-action mapping.
  - Closed B16 by migrating reject/override/budget/escalation reason capture to inline reason sheet paths.
  - Closed B17 by compressing Work panel into decision-first layout with collapsed evidence/details blocks.
  - Closed B18 by relocating KPI/batch/retry diagnostics into Admin Console Ops with admin/operator gating.
  - Closed B19 by realigning integration/UI harness coverage for work-only Gmail and reason-sheet regression checks.
  - Closed B25 by introducing backend-owned operator audit normalization and making Gmail consume backend `operator_*` audit fields instead of UI-side reason-code copy maps.
  - Started B23 execution by removing out-of-scope `demo/` and `marketplace/` directories from tracked repository scope.
  - Continued B23 execution by removing out-of-scope `ui/sheets/` and `ui/slack/demo.html` from tracked repository scope.
  - Completed B23 clearledgr-service cleanup by removing legacy recurring/accrual/journal/payment/cashflow modules and deleting their workflow/test couplings in canonical AP-v1 paths.
  - Continued B23 cleanup by removing legacy `core/engine`, `services/ai_enhanced`, and `services/validation`, and decoupling Gmail webhook/autopilot AP intake from legacy reconciliation-engine paths.
  - Continued B23 cleanup by removing remaining reconciliation-specific matching and model contracts (`services/matching`, `services/intelligent_matching`, `models/reconciliation`, `models/journal_entries`, and related exports).
  - Continued B23 cleanup by removing additional unreferenced/out-of-scope `clearledgr/services` modules (legacy payment execution, recurring/expense, sheets/outlook connectors, utility stubs) and updating test singleton reset hooks to remove retired service references.
  - Continued B23 cleanup by removing remaining dead `clearledgr/services` modules with zero runtime import references (`approval_chains`, `erp_sync`, `exceptions`, `multi_currency`) and re-running AP regression slice.
  - Continued B23 cleanup by removing dead `clearledgr/core` modules with zero runtime import references (`audit`, `rate_limit`) and re-running AP regression slice.
  - Continued B23 cleanup by removing early-payment discount surface/module and legacy AP payment schema/methods (`services/early_payment_discounts.py`, `legacy_engine_store payments` CRUD, payload/UI discount exposure), plus AP regression revalidation (`100 passed`).
  - Continued B23 cleanup by retiring remaining off-plan API families (`/analytics`, `/learning`, `/subscription`), rewiring Admin Console dashboard refresh to `/api/admin/bootstrap`, and re-running strict-surface regression suites (`82 passed`).
  - Completed B22 strict-surface hardening by removing env-driven full/legacy runtime profile switching, enforcing strict-only route filtering/middleware behavior, and realigning runtime diagnostics + regression suites (`39 passed`).
  - Completed B21 repository hygiene by untracking local runtime SQLite artifacts (`email_tasks.sqlite3`, `task_scheduler.sqlite3`), removing tracked runtime pid/log/state files, and removing tracked extension dependency/build trees (`ui/gmail-extension/node_modules`, `ui/gmail-extension/build`) from the repository index.
  - Closed B26 by introducing canonical finance runtime contracts (`SkillRequest`, `SkillResponse`, `ActionExecution`, `AuditEvent`) and wiring runtime contract wrappers into skill dispatch.
  - Closed B27 by adding canonical agent-intents APIs (`/api/agent/intents/preview-request`, `/api/agent/intents/execute-request`) plus runtime skill registry endpoint (`/api/agent/intents/skills`).
  - Closed B28 by adding provider-agnostic ERP bill adapter contracts (`validate/post/get_status/reconcile`) and routing API-first posting through the adapter seam.
  - Closed B29 by adding regression coverage for canonical finance contracts, runtime contract fields, canonical audit schema propagation, agent-intents contract endpoints, and ERP adapter contract behavior.
  - Closed B30 by enforcing per-skill capability manifests and adding runtime/API readiness gate reporting (`/api/agent/intents/skills`, `/api/agent/intents/skills/{skill_id}/readiness`).
  - Closed B31 by adding connector readiness evaluator service + runtime gate integration (`enabled_connector_readiness`) + admin ops endpoint (`/api/admin/ops/connector-readiness`).
  - Closed B32 by implementing persisted learning calibration snapshots with ops endpoints (`/api/admin/ops/learning-calibration`, `/api/admin/ops/learning-calibration/recompute`).
  - Closed B33 by shipping `vendor_compliance_v1` as an additional finance skill package on the same runtime (`read_vendor_compliance_health`).

## Archive protocol
- Keep this file as the live tracker for current-cycle items.
- Move to `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/` only when all `OPEN` items are `DONE` or explicitly marked accepted risk with owner + expiry.
