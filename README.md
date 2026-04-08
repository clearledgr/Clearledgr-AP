# Clearledgr — Finance Execution Layer

Clearledgr is the execution layer for finance operations, embedding AI agents into the tools finance teams already use.

AP v1 is the first production skill domain: Gmail-first intake, Slack/Teams approvals, ERP write-back, and full audit traceability.
It is the current wedge, not the full product boundary.

## Canonical Doctrine

Use these documents as source of truth:

1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
5. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`
6. `/Users/mombalam/Desktop/Clearledgr.v1/docs/WEDGE_QUALITY_SCORECARD.md`

If any document conflicts with `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, `PLAN.md` wins.

## Current Status (2026-03-22)

AP v1 is already implemented as a real product surface in this codebase. The shipped code includes:

- Gmail-first AP intake with a backend autopilot loop, Gmail-native thread/work surfaces, and routed setup/ops pages.
- Deterministic invoice validation, policy/confidence gates, approval routing, and audit/event recording.
- Slack and Teams approval handlers with authenticated callbacks, duplicate-safe handling, and workflow dispatch.
- ERP execution across NetSuite, QuickBooks, Xero, and SAP, including API-primary posting paths and standard follow-on credit/settlement operations behind readiness gates.
- Workspace and ops control surfaces for health, KPIs, rollback controls, connector readiness, and GA-readiness evidence metadata.

The main remaining gap to launch is not core product implementation. It is live-environment proof and operating discipline: staging/sandbox verification, deployment/config freeze, post-launch monitoring ownership, and continued product polish.

Use this README plus `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md` for current product and launch posture. Treat `/Users/mombalam/Desktop/Clearledgr.v1/TODOS.md` as deferred work only, not as an implementation-completeness ledger.
For Railway deployment, use `/Users/mombalam/Desktop/Clearledgr.v1/docs/RAILWAY_DEPLOYMENT.md`.

For pilot hardening and wedge-truth evaluation, use `/Users/mombalam/Desktop/Clearledgr.v1/docs/WEDGE_QUALITY_SCORECARD.md` as the operating scorecard for reliability, context-switch reduction, and elimination of approval chasing / ERP re-entry.

## Product Direction (Locked)

1. One Clearledgr finance execution agent runtime.
2. AP is the first production skill domain, not the terminal product scope.
3. `Pipeline` is the AP control plane and default landing route for queue work.
4. Gmail is the first inbox adapter and the primary current-record surface in the current wedge, not the entire product.
5. Gmail default pinned navigation stays intentionally small: `Pipeline`, `Home`.
6. Slack and Teams are approval/decision surfaces.
7. ERP is the system of record.
8. Human-in-the-loop is intentional for risky actions.
9. Policy, audit, idempotency, and durability are mandatory.
10. Current AP connector scope is NetSuite, QuickBooks, Xero, and SAP, each enabled by readiness gates.
11. Current durable orchestration backend is `local_db` (DB-backed); Temporal remains optional and must be truthfully reported.
12. Outlook intake is explicitly de-scoped for AP v1 GA (Gmail is the only inbox surface in production scope).
13. Initial rollout is Europe and Africa first (before any broader regional expansion).
14. Operator-facing timestamps are standardized to `Europe/London`; backend storage/audit timestamps remain UTC.

Clearledgr is not a generic automation builder and not a dashboard-first AP tool.

## AP v1 Workflow

1. AP email arrives in Gmail.
2. Clearledgr classifies and extracts invoice/AP fields.
3. Deterministic validation + policy/confidence checks run.
4. If required, approval is routed to Slack/Teams.
5. On approval and eligibility, Clearledgr posts to ERP.
6. End-to-end audit events are recorded and surfaced.

## Gmail Operator Surface

For AP v1, Gmail is the primary current-record surface and `Pipeline` is the queue control plane. The Gmail/AP wedge is built as Streak for finance ops:

1. `Clearledgr AP` thread panel is the daily execution workspace.
   - Focused invoice identity strip (vendor, amount, due date, invoice number, PO status).
   - One status badge + concise blocker chips.
   - One state-driven primary CTA with small secondary actions.
   - Evidence checklist + collapsed audit disclosure.
   - Audit copy is backend-owned via `/api/ap/items/{ap_item_id}/audit` `operator_*` fields (UI renders backend operator wording, not local reason-code phrase maps).
2. Gmail-native page routes handle setup, pipeline views, monitoring, policy management, team access, and plan/health pages.
   - The core Gmail work path is `Pipeline`, `Home`, `Review`, and `Upcoming`.
   - Default pinned nav stays intentionally sparse at `Pipeline` and `Home`; `Review` / `Upcoming` remain part of the core work path without bloating the default left nav.
   - `Pipeline` is the default AP queue/process surface and control plane, with AP-first slices, finance-native filters/sorts, and direct thread-to-pipeline / pipeline-to-thread reopening.
   - `Home` is a lightweight hub: quick access, recent work, upcoming work, and secondary tools.
   - Saved pipeline views are persisted per authenticated user and organization; `Home` surfaces saved views and finance-native starter views before secondary admin tools.
   - `Health` and comparable admin pages are role-gated secondary pages.
   - These pages are still inside Gmail and do not require a separate operating console for normal use.
   - Ops/telemetry/batch/debug content remains out of the thread panel itself and is role-gated in Gmail-native routed pages.
3. Gmail authorization is explicit and user-initiated from inline CTAs (`Connect Gmail` / `Connections`); the extension does not auto-launch Gmail OAuth on startup.

Reason capture is inline and non-blocking (reason sheet); native browser `prompt/confirm` dialogs are not used in AP action flows.

UI hardening guardrails:

1. Extension ships from `dist/inboxsdk-layer.js` only, with CI parity checks that fail on stale or off-doctrine bundle content.
2. Legacy extension popup/options/demo surfaces are removed from shipped root and archived under `/Users/mombalam/Desktop/Clearledgr.v1/docs/legacy/gmail-extension-ui/`.
3. Work audit copy is backend-owned from `/api/ap/items/{ap_item_id}/audit` (`operator_*` fields); Gmail fallback copy stays generic-safe and does not display raw reason codes.
4. Gmail extension build/watch now uses Bun locally; `npm run build` and `npm run start` delegate to Bun-backed bundling while preserving audited `dist` parity checks.

## Onboarding and Account Backbone

Clearledgr onboarding and account management still follows an admin-first model, but it remains Gmail-native:

1. Gmail routed pages own onboarding for Gmail, Slack/Teams, and ERP setup.
2. Team roles/invites are managed through the same authenticated backend APIs (`/api/workspace/team/*` + `/auth/invites/*`).
3. `Home` is a lightweight hub for quick access, recent activity, upcoming work, and secondary tools, not the center of daily AP operations.
4. The thread panel stays execution-focused; setup/config flows live in Gmail-native pages rather than a separate dashboard.
5. OAuth entry points for setup are launched from authenticated backend endpoints and surfaced inside the Gmail product shell.

## Runtime Shape (Agent + Skills)

Clearledgr runs one core agent runtime and domain skills:

- Runtime intent APIs:
  - `POST /api/agent/intents/preview`
  - `POST /api/agent/intents/execute`
  - `POST /api/agent/intents/preview-request` (canonical `SkillRequest`)
  - `POST /api/agent/intents/execute-request` (canonical `SkillRequest` + `ActionExecution`)
  - `GET /api/agent/intents/skills` (runtime skill registry + capability manifests)
  - `GET /api/agent/intents/skills/{skill_id}/readiness` (promotion-gate readiness report)
- Current runtime skill packages:
  - `ap_v1` (production AP execution intents)
  - `workflow_health_v1` (read-only AP workflow diagnostics skill)
  - `vendor_compliance_v1` (read-only vendor compliance posture skill)
- AP skill execution (current production domain)
- Planner/runtime errors fail closed; AP execution does not branch into legacy runtime fallback.
- Shared controls:
  - policy prechecks
  - HITL gates
  - auditable state transitions
  - idempotent mutating actions
  - retry/durability semantics
  - runtime truth-in-claims (`runtime_backend`, `temporal_available`, gating state)

Canonical runtime contracts are implemented in:

- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/core/finance_contracts.py`
  - `SkillRequest`
  - `SkillResponse`
  - `ActionExecution`
  - `AuditEvent`
  - `SkillCapabilityManifest` (state machine, action catalog, policy pack, evidence schema, adapter bindings, KPI contract)

ERP API-first adapter contract is provider-agnostic and shared across NetSuite/QuickBooks/Xero/SAP via:

- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/erp/contracts.py`
  - `ERPBillAdapter.validate(payload)`
  - `ERPBillAdapter.post(organization_id, bill, ...)`
  - `ERPBillAdapter.get_status(organization_id, external_ref)`
  - `ERPBillAdapter.reconcile(organization_id, entity_id)`

## Repository Map

### Backend

- `/Users/mombalam/Desktop/Clearledgr.v1/main.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/invoice_workflow.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_agent_runtime.py`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/services/finance_skills/`
- `/Users/mombalam/Desktop/Clearledgr.v1/clearledgr/api/`

### Embedded Surfaces

- Gmail extension: `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/`
- Slack app: `/Users/mombalam/Desktop/Clearledgr.v1/ui/slack/`
- Optional workspace shell surface: served from `/workspace` (when enabled), but it is not the canonical daily operator shell

### Launch and Readiness Docs

- Getting started: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GETTING_STARTED.md`
- Runbooks: `/Users/mombalam/Desktop/Clearledgr.v1/docs/RUNBOOKS.md`
- Staging drill runbook: `/Users/mombalam/Desktop/Clearledgr.v1/docs/STAGING_DRILL_RUNBOOK.md`
- GA evidence process: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`
- Admin Ops APIs:
  - `GET /api/workspace/ops/connector-readiness` (per-connector readiness + blockers for NetSuite/QuickBooks/Xero/SAP)
  - `GET /api/workspace/ops/learning-calibration` (latest tenant calibration snapshot)
  - `POST /api/workspace/ops/learning-calibration/recompute` (recompute + persist calibration snapshot)

## Local Development

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp env.example .env
```

### 3. Run backend

```bash
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

### 4. Open local surfaces

- API docs: `http://localhost:8010/docs`
- ReDoc: `http://localhost:8010/redoc`
- Optional workspace shell (if enabled): `http://localhost:8010/workspace`

## Railway Deployment

Clearledgr is now deployment-ready for a split Railway topology:

- `api` service: `sh scripts/start-api.sh`
- `worker` service: `sh scripts/start-worker.sh`
- managed Postgres
- Redis strongly recommended

Use `/Users/mombalam/Desktop/Clearledgr.v1/docs/RAILWAY_DEPLOYMENT.md` for the exact env, Slack/Gmail callback URLs, and Gmail extension build command.

### 5. Run core regression slices

```bash
PYTHONPATH=. pytest -q tests/test_finance_contracts.py tests/test_finance_agent_runtime.py tests/test_erp_adapter_contracts.py tests/test_api_endpoints.py::TestAgentIntentEndpoints tests/test_api_endpoints.py::TestExtensionEndpoints tests/test_runtime_surface_scope.py
node --test ui/gmail-extension/tests/inboxsdk-layer-ui.test.cjs ui/gmail-extension/tests/pipeline-views.test.cjs
```

Release-gate doctrine slices:

```bash
PYTHONPATH=. pytest -q tests/test_runtime_surface_scope.py
node --test ui/gmail-extension/tests/inboxsdk-layer-ui.test.cjs ui/gmail-extension/tests/pipeline-views.test.cjs
```

Optional real-browser Gmail harness:

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:browser-harness
```

If Playwright/Chromium is unavailable locally, the harness test reports a skip with setup guidance.

CI-enforced deterministic harness (fails if browser prerequisites are missing):

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:browser-harness:ci
```

Authenticated Gmail runtime evidence capture (for staging/pilot readiness):

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:e2e-auth:evidence -- --release-id ap-v1-2026-03-01-pilot-rc1
```

This writes:
- evidence JSON + screenshot under `docs/ga-evidence/releases/<release_id>/artifacts/`
- normalized report at `docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`

Launch evidence gate check (pilot mode):

```bash
python3 /Users/mombalam/Desktop/Clearledgr.v1/scripts/validate_launch_evidence.py --mode pilot --json
```

GitHub workflows:
- `/.github/workflows/gmail-extension-browser-harness.yml` runs deterministic browser harness on PR/push for extension changes.
- `/.github/workflows/gmail-runtime-smoke-nightly.yml` runs nightly authenticated Gmail runtime smoke in a controlled self-hosted environment and uploads evidence artifacts.
- Runner setup/playbook: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GMAIL_RUNTIME_RUNNER_SETUP.md`

## Phase 7 Release Gate

Doctrine tests must explicitly enforce:

1. Sparse default Gmail nav (`Home` and `Pipeline` by default; `Review` / `Upcoming` in the core work path; admin/setup routes secondary and role-gated).
2. Thread card content limits: one focused work panel, evidence checklist, collapsed audit memory, no KPI/debug/dashboard clutter.
3. No startup Gmail OAuth auto-popup; auth opens only from explicit operator/admin CTAs.
4. Role-gated admin routes and secondary pages.
5. Runtime-backed AP mutations at the Gmail contract boundary.
6. Pipeline slice/view persistence for authenticated user and organization scope.

Manual product review checklist for each release candidate:

1. Thread card: identity strip, status, blockers, one primary CTA, evidence checklist, collapsed key history/background activity.
2. Pipeline slices: waiting on approval, ready to post, needs info, failed post, blocked/exception, due soon, overdue.
3. Home lightness: Streak-style hub only, not a dashboard competing with Pipeline or the thread card.
4. Route gating: admin/setup pages remain secondary and unavailable to non-admin users.
5. Gmail auth flow: no startup popup; `Connect Gmail` stays user-initiated.
6. Slack/Teams to Gmail roundtrip: approval/reject/request-info decisions update the same AP item and Gmail record state.
7. ERP post roundtrip: ready-to-post, posted, and failed-post states return to the same AP record and audit history.

## Security and Operational Expectations

AP v1 must enforce:

1. Auth boundaries on sensitive/mutating surfaces.
2. Verified Slack/Teams callback handling.
3. Server-side AP state-machine enforcement.
4. Policy checks before mutating operations.
5. Idempotent approval and posting behavior.
6. Complete, queryable audit trail.
7. Truthful runtime/durability reporting.
8. Strict AP-v1 runtime surface mode in all environments (legacy/full surface toggles are not supported).
9. No docs/operator messaging should claim Temporal-backed orchestration when runtime reports `temporal_available=false`.

## Legacy Notes

This repo still contains legacy and experimental modules/docs from earlier directions.

They are non-canonical for AP v1 unless explicitly referenced by `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`.

## License

Proprietary.
