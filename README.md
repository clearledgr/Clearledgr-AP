# Clearledgr — Finance AI Agent (AP Skill v1)

Clearledgr is the finance execution agent platform.

AP v1 is the first production skill domain: Gmail-first intake, Slack/Teams approvals, ERP write-back, and full audit traceability.

## Canonical Doctrine

Use these documents as source of truth:

1. `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
2. `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
3. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_EMBEDDED_WORKER_EXPERIENCE.md`
4. `/Users/mombalam/Desktop/Clearledgr.v1/docs/V1_BACKEND_CONTRACTS.md`
5. `/Users/mombalam/Desktop/Clearledgr.v1/docs/API_REFERENCE.md`

If any document conflicts with `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, `PLAN.md` wins.

## Product Direction (Locked)

1. One Clearledgr finance execution agent runtime.
2. AP is the first production skill domain.
3. Gmail is the primary AP operator surface.
4. Slack and Teams are approval/decision surfaces.
5. ERP is the system of record.
6. Human-in-the-loop is intentional for risky actions.
7. Policy, audit, idempotency, and durability are mandatory.
8. Current AP connector scope is NetSuite, QuickBooks, Xero, and SAP, each enabled by readiness gates.
9. Current durable orchestration backend is `local_db` (DB-backed); Temporal remains optional and must be truthfully reported.

Clearledgr is not a generic automation builder and not a dashboard-first AP tool.

## AP v1 Workflow

1. AP email arrives in Gmail.
2. Clearledgr classifies and extracts invoice/AP fields.
3. Deterministic validation + policy/confidence checks run.
4. If required, approval is routed to Slack/Teams.
5. On approval and eligibility, Clearledgr posts to ERP.
6. End-to-end audit events are recorded and surfaced.

## Gmail Operator Surface (Work-Only)

Gmail now runs a single decision-first operator panel:

1. `Clearledgr AP` (Work panel)
   - Focused invoice identity strip (vendor, amount, due date, invoice number, PO status).
   - One status badge + concise blocker chips.
   - One state-driven primary CTA with small secondary actions.
   - Evidence checklist + collapsed context/audit details.
   - Audit copy is backend-owned via `/api/ap/items/{ap_item_id}/audit` `operator_*` fields (UI renders backend operator wording, not local reason-code phrase maps).
2. Ops controls are intentionally removed from Gmail.
   - KPI telemetry, batch operations, raw agent events, and debug tools live in Admin Console `/console?page=ops`.
   - Gmail shows `Open Ops Console` only for admin/operator roles.

Reason capture is inline and non-blocking (reason sheet); native browser `prompt/confirm` dialogs are not used in AP action flows.

UI hardening guardrails:

1. Extension ships from `dist/inboxsdk-layer.js` only, with CI parity checks that fail on stale or off-doctrine bundle content.
2. Legacy extension popup/options/demo surfaces are removed from shipped root and archived under `/Users/mombalam/Desktop/Clearledgr.v1/docs/legacy/gmail-extension-ui/`.
3. Work audit copy is backend-owned from `/api/ap/items/{ap_item_id}/audit` (`operator_*` fields); Gmail fallback copy stays generic-safe and does not display raw reason codes.

## Onboarding and Account Backbone (Admin-First)

Clearledgr onboarding and account management follows an admin-first model:

1. Admin Console owns onboarding (`/console?page=integrations`) for Gmail, Slack/Teams, and ERP setup.
2. Team roles/invites are managed in Admin Console APIs (`/api/admin/team/*` + `/auth/invites/*`).
3. Gmail sidebar stays execution-focused; when auth/setup is required it links operators to Admin Console integration setup.
4. OAuth entry points for setup are launched from authenticated Admin Console endpoints (for example, `/api/admin/integrations/gmail/connect/start`).

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
- Admin console surface: served from `/console` (when enabled)

### Launch and Readiness Docs

- Getting started: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GETTING_STARTED.md`
- Runbooks: `/Users/mombalam/Desktop/Clearledgr.v1/docs/RUNBOOKS.md`
- Staging drill runbook: `/Users/mombalam/Desktop/Clearledgr.v1/docs/STAGING_DRILL_RUNBOOK.md`
- GA evidence process: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`
- Admin Ops APIs:
  - `GET /api/admin/ops/connector-readiness` (per-connector readiness + blockers for NetSuite/QuickBooks/Xero/SAP)
  - `GET /api/admin/ops/learning-calibration` (latest tenant calibration snapshot)
  - `POST /api/admin/ops/learning-calibration/recompute` (recompute + persist calibration snapshot)

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
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Open local surfaces

- API docs: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Admin console (if enabled): `http://localhost:8000/console`

### 5. Run core regression slices

```bash
PYTHONPATH=. pytest -q tests/test_finance_contracts.py tests/test_finance_agent_runtime.py tests/test_erp_adapter_contracts.py tests/test_api_endpoints.py::TestAgentIntentEndpoints tests/test_api_endpoints.py::TestExtensionEndpoints
node --test ui/gmail-extension/tests/*.test.cjs
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
