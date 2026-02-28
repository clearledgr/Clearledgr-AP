# Clearledgr AP v1

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

Clearledgr is not a generic automation builder and not a dashboard-first AP tool.

## AP v1 Workflow

1. AP email arrives in Gmail.
2. Clearledgr classifies and extracts invoice/AP fields.
3. Deterministic validation + policy/confidence checks run.
4. If required, approval is routed to Slack/Teams.
5. On approval and eligibility, Clearledgr posts to ERP.
6. End-to-end audit events are recorded and surfaced.

## Runtime Shape (Agent + Skills)

Clearledgr runs one core agent runtime and domain skills:

- Runtime intent APIs:
  - `POST /api/agent/intents/preview`
  - `POST /api/agent/intents/execute`
- AP skill execution (current production domain)
- Planner/runtime errors fail closed by default (`AGENT_LEGACY_FALLBACK_ON_ERROR=false`)
- Shared controls:
  - policy prechecks
  - HITL gates
  - auditable state transitions
  - idempotent mutating actions
  - retry/durability semantics

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
PYTHONPATH=. pytest -q tests/test_finance_agent_runtime.py tests/test_api_endpoints.py::TestAgentIntentEndpoints tests/test_api_endpoints.py::TestExtensionEndpoints
node --test ui/gmail-extension/tests/*.test.cjs
```

Optional real-browser Gmail harness:

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:browser-harness
```

If Playwright/Chromium is unavailable locally, the harness test reports a skip with setup guidance.

Authenticated Gmail runtime evidence capture (for staging/pilot readiness):

```bash
cd /Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension
npm run test:e2e-auth:evidence -- --release-id ap-v1-2026-03-01-pilot-rc1
```

This writes:
- evidence JSON + screenshot under `docs/ga-evidence/releases/<release_id>/artifacts/`
- normalized report at `docs/ga-evidence/releases/<release_id>/GMAIL_RUNTIME_E2E.md`

## Security and Operational Expectations

AP v1 must enforce:

1. Auth boundaries on sensitive/mutating surfaces.
2. Verified Slack/Teams callback handling.
3. Server-side AP state-machine enforcement.
4. Policy checks before mutating operations.
5. Idempotent approval and posting behavior.
6. Complete, queryable audit trail.
7. Truthful runtime/durability reporting.
8. Production/staging strict AP-v1 surface mode (`AP_V1_STRICT_SURFACES=true`) unless legacy compatibility is explicitly required.

## Legacy Notes

This repo still contains legacy and experimental modules/docs from earlier directions.

They are non-canonical for AP v1 unless explicitly referenced by `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`.

## License

Proprietary.
