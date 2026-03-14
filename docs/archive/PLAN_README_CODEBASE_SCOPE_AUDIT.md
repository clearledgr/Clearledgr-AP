# PLAN/README Codebase Scope Audit (AP v1 Canonical)

Date: 2026-03-01  
Canonical sources: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md`

Update (2026-03-01, later pass): the off-plan API families `ai_enhanced`, `ap_advanced`, `ap_workflow`, `bank_feeds`, `engine`, `llm_proxy`, `outlook_webhooks`, `payment_requests`, and `payments` were removed from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/`; reconciliation files and bank-feeds service package were removed from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/`; legacy endpoint suites `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_engine.py` and `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_ap_workflow.py` were removed.
Update (2026-03-01, AP intake cleanup pass): removed legacy runtime modules `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/engine.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ai_enhanced.py`, and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/validation.py`, and removed Gmail webhook/autopilot dependencies on the legacy reconciliation engine to keep AP intake paths aligned to AP-v1 doctrine.
Update (2026-03-01, reconciliation-contract cleanup pass): removed reconciliation matching/model contracts (`/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/matching.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/intelligent_matching.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/reconciliation.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/journal_entries.py`) and pruned related exports from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/__init__.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/models/requests.py`, and `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/__init__.py`.
Update (2026-03-01, service-surface reduction pass): removed additional unreferenced/out-of-scope service modules from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/` (`ap_aging`, `bank_statement_parser`, `batch_intelligence`, `credit_notes`, `csv_parser`, `document_retention`, `email_matcher`, `eu_vat_validation`, `exception_priority`, `flutterwave_client`, `multi_factor_scoring`, `outlook_api`, `pattern_learning`, `paystack_client`, `realtime_sync`, `sheets_integration`, `stripe_client`, `tax_calculations`, `transaction_quality`, `vita_audit`, `payment_execution`, `recurring_management`, `expense_workflow`, `sap`, `sheets_api`, `llm`, `notifications`, `natural_language_commands`, `optimal_matching`, `explainability`) and cleaned test singleton hooks in `/Users/mombalam/Desktop/Clearledgr.v1/tests/conftest.py`.
Update (2026-03-01, dead-service finalization pass): removed additional dead service modules with zero runtime import references (`approval_chains`, `erp_sync`, `exceptions`, `multi_currency`) from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/`.
Update (2026-03-01, dead-core finalization pass): removed additional dead core modules with zero runtime import references (`audit`, `rate_limit`) from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/`.
Update (2026-03-01, payments-surface finalization pass): removed legacy payment-scheduling/discount artifacts from canonical AP-v1 paths (`/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/early_payment_discounts.py`, legacy `payments` schema + AP payment CRUD in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/legacy_engine_store.py`, discount payload/UI exposure in `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py` and `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`).
Update (2026-03-01, legacy-api retirement pass): retired remaining off-plan API route families `/analytics`, `/learning`, `/subscription` by removing `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/analytics.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/learning.py`, `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/subscription.py`, removing their mounts and strict-prefix allowlist entries in `/Users/mombalam/Desktop/Clearledgr.v1/main.py`, and rewiring `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js` dashboard refresh paths to canonical `/api/admin/bootstrap`.

## 1) Canonical scope baseline

From canonical docs, AP v1 scope is:

- Finance AI Agent runtime with AP as Skill #1 (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:13`, `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:16`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md:21`).
- Embedded surfaces:
  - Gmail work surface (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:66`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md:42`)
  - Slack + Teams approvals (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:67`, `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:332`, `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:342`)
  - ERP write-back (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:68`, `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:374`)
  - Admin Console for setup/ops, not daily AP processing (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:118`, `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:167`, `/Users/mombalam/Desktop/Clearledgr.v1/README.md:52`)
- Explicit non-goals for AP v1: reconciliation, month-end close, FP&A-first workflow, standalone daily AP dashboard, consumer messaging surfaces (`/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md:110`).

## 2) Audit method

- Route-level runtime audit from `main.app` full route table and strict profile route table.
- Router-module inventory from `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/*.py`.
- Tracked-file inventory from `git ls-files`.
- Main router-mount path review in `/Users/mombalam/Desktop/Clearledgr.v1/main.py:478` through `/Users/mombalam/Desktop/Clearledgr.v1/main.py:618`.

## 3) Runtime scope findings

### 3.1 Full runtime legacy-route gap is being retired

Historical off-plan route families identified in early passes (`/ap`, `/ap-advanced`, `/payment-requests`, `/payments`, `/bank-feeds`, `/ai`, `/llm`, `/outlook`, `/analytics`, `/learning`, `/subscription`) have been removed from runtime.

Current state:

- Legacy mount gate remains in `/Users/mombalam/Desktop/Clearledgr.v1/main.py` and should still be simplified in a final hardening pass.
- Off-plan `/analytics`, `/learning`, `/subscription` families are no longer mounted and no longer exist as standalone API modules.

### 3.2 Strict profile aligns much better

Strict mode profile allows the AP-v1 core families from `/Users/mombalam/Desktop/Clearledgr.v1/main.py:162` through `/Users/mombalam/Desktop/Clearledgr.v1/main.py:180`, and blocks legacy families with `endpoint_disabled_in_ap_v1_profile` (`/Users/mombalam/Desktop/Clearledgr.v1/main.py:319`).

Gap:

- `/v1/health` exists (`/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/v1.py:4`) but is not in strict allow prefixes (`/Users/mombalam/Desktop/Clearledgr.v1/main.py:162`).

## 4) File-level scope findings (keep/deprecate/delete)

### 4.1 Keep (canonical AP v1)

- Core AP surfaces and contracts:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/gmail_extension.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/slack_invoices.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/teams_invoices.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_items.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_policies.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_intents.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/agent_sessions.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_api_first.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/stores/ap_store.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/database.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/src/inboxsdk-layer.js`
  - `/Users/mombalam/Desktop/Clearledgr.v1/static/console/app.js`

### 4.2 Deprecate (off-plan for AP v1; disable + remove in cleanup wave)

- API families:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ai_enhanced.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/llm_proxy.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/bank_feeds.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/payments.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/payment_requests.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/outlook_webhooks.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_workflow.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/ap_advanced.py`

- Service families tied to AP-v1 non-goals:
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/reconciliation_engine.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/reconciliation_runner.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/reconciliation_inputs.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/bank_feeds/`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/subscription.py`
  - `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/payment_request.py`

- Non-canonical UI surfaces:
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/sheets/` (removed 2026-03-01)
  - `/Users/mombalam/Desktop/Clearledgr.v1/ui/slack/demo.html` (removed 2026-03-01)

### 4.3 Delete now (repo hygiene + non-canonical artifacts)

Deleted in this pass:

- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/node_modules/` (tracked vendored dependencies, 7,873 files in git index)
- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/build/` (generated build output)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/state/learning.db` (runtime DB artifact)
- `/Users/mombalam/Desktop/Clearledgr.v1/demo/` (out-of-scope demo dataset/scripts)
- `/Users/mombalam/Desktop/Clearledgr.v1/marketplace/` (out-of-scope marketplace packaging collateral)
- `/Users/mombalam/Desktop/Clearledgr.v1/ui/sheets/` (out-of-scope Google Sheets add-on surface)
- `/Users/mombalam/Desktop/Clearledgr.v1/ui/slack/demo.html` (out-of-scope Slack demo surface)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/ap_aging.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/bank_statement_parser.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/batch_intelligence.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/credit_notes.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/csv_parser.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/document_retention.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/email_matcher.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/eu_vat_validation.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/exception_priority.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/flutterwave_client.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/multi_factor_scoring.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/outlook_api.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/pattern_learning.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/paystack_client.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/realtime_sync.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sheets_integration.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/stripe_client.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/tax_calculations.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/transaction_quality.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/vita_audit.py` (unreferenced AP-v1-out-of-scope service)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/payment_execution.py` (legacy post-approval payment execution path outside AP-v1 execution contract)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/recurring_management.py` (legacy recurring workflow outside AP-v1 non-goals)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/expense_workflow.py` (legacy expense workflow outside AP-v1)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sap.py` (duplicate legacy SAP service outside canonical ERP adapter path)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/sheets_api.py` (legacy sheets service outside AP-v1)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/llm.py` (unused standalone LLM helper outside canonical AP runtime path)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/notifications.py` (unused generic notification utility outside AP-v1 contract)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/natural_language_commands.py` (unused command surface outside canonical agent intent API)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/optimal_matching.py` (unused matching module outside AP-v1)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/explainability.py` (unused module outside AP-v1 contract)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/approval_chains.py` (dead module with no remaining runtime/test imports)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp_sync.py` (dead module with no remaining runtime/test imports)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/exceptions.py` (legacy reconciliation exception store outside AP-v1)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/multi_currency.py` (dead module with no remaining runtime/test imports)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/early_payment_discounts.py` (legacy payment-optimization surface outside AP-v1 execution contract)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/audit.py` (dead core audit module with no remaining runtime/test imports)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/rate_limit.py` (dead core rate-limit module with no remaining runtime/test imports)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/analytics.py` (retired off-plan admin dashboard API in favor of `/api/admin/bootstrap`)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/learning.py` (retired off-plan external learning API surface; learning remains internal service capability)
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/subscription.py` (retired off-plan standalone subscription API; admin subscription controls remain in `/api/admin/*`)
- previously removed local runtime artifacts:
  - `/Users/mombalam/Desktop/Clearledgr.v1/.uvicorn.pid`
  - `/Users/mombalam/Desktop/Clearledgr.v1/audit_trail.sqlite3`
  - `/Users/mombalam/Desktop/Clearledgr.v1/email_tasks.sqlite3`
  - `/Users/mombalam/Desktop/Clearledgr.v1/task_scheduler.sqlite3`
  - `/Users/mombalam/Desktop/Clearledgr.v1/server.log`
  - `/Users/mombalam/Desktop/Clearledgr.v1/server-8010.log`
  - `/Users/mombalam/Desktop/Clearledgr.v1/server8010.log`
  - `/Users/mombalam/Desktop/Clearledgr.v1/server_8010.log`
  - `/Users/mombalam/Desktop/Clearledgr.v1/project layout`
  - `/Users/mombalam/Desktop/Clearledgr.v1/yc_agent_session.md`

## 5) Required follow-on cleanup program (no shortcuts)

1. Hard-disable legacy route families by default in all environments, not only production-like profiles, and require explicit startup-time opt-in for legacy QA.  
2. Remove deprecated API modules from runtime mounting and from test matrix.  
3. Delete deprecated modules once no imports remain (with focused replacement tests for AP-v1-only scope).  
4. Shrink `/Users/mombalam/Desktop/Clearledgr.v1/tests/test_api_endpoints.py` by removing non-canonical endpoint suites (`/analytics`, `/ap/*` legacy, `/payments`, etc.) and replacing with AP-v1 canonical endpoint assertions.  
5. Keep strict AP-v1 route profile as the default launch contract and preserve admin-only ops console access.

## 6) Verdict

Current repo is **not yet scope-clean** against canonical AP-v1 docs.  
The runtime has a valid strict profile, but legacy/off-plan families still exist in-code and can be reintroduced via legacy gates.  
Immediate artifact cleanup is complete; structured code-level deprecation/removal is still required.
