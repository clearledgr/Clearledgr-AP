# Clearledgr AP v1

Clearledgr is an **embedded finance execution layer** for finance teams.

AP v1 starts in Gmail, routes approvals in Slack and Teams, and writes back to ERP systems with policy checks, deterministic workflow orchestration, and an auditable execution trail.

Clearledgr is not a generic automation builder and not a standalone AP dashboard for daily work.

## Current Product Focus (AP v1)

Clearledgr AP v1 is focused on an inbox-native AP workflow:

1. Detect invoice and AP requests from Gmail
2. Extract invoice fields from email bodies and attachments
3. Run deterministic validation (including policy and exception checks)
4. Surface status, exceptions, and next action inside Gmail
5. Route approvals to Slack and Teams
6. Post approved invoices to ERP systems (with idempotency and audit)
7. Record immutable audit breadcrumbs and operator-visible outcomes

## Product Doctrine (Canonical)

The canonical AP v1 doctrine, contracts, and launch gates live in:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

Key doctrine points:

- Clearledgr is an **embedded finance execution layer**
- Gmail is the AP **entry point**, not the product boundary
- Slack and Teams are approval/decision surfaces
- ERP is the system of record
- Admin Console is setup/ops infrastructure, not the daily AP workflow UI
- "Streak-like" is an **internal UX doctrine** only (not external positioning)
- WhatsApp/Telegram are **not** product surfaces

## Document Map (Use These Deliberately)

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
  - Canonical doctrine, API/interface contracts, launch gates, release taxonomy (pilot vs GA)
- `/Users/mombalam/Desktop/Clearledgr.v1/TODO_BACKLOG.md`
  - Execution backlog and sequencing
- `/Users/mombalam/Desktop/Clearledgr.v1/gaps_opportunities`
  - Strategic gaps and expansion opportunities
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/GO_LIVE_ASSESSMENT.md`
  - Point-in-time readiness audit (dated, not canonical doctrine)
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/HOW_IT_WORKS.md`
  - AP v1 user-facing flow overview aligned to the current doctrine
- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/MVP_SCOPE.md`
  - Historical MVP framing (archived; not canonical)

## What This Repository Contains

This repository contains the AP v1 implementation and supporting infrastructure, plus some legacy/experimental modules from earlier product directions (for example reconciliation and spreadsheet-heavy flows).

Those modules may remain in the codebase, but they are not the canonical product scope for AP v1.

## AP v1 Runtime Surfaces

1. **Gmail extension** (primary operator surface)
   - Thread-level AP workspace
   - Status, exceptions, next action
   - Progressive disclosure for context and audit details
2. **Slack / Teams**
   - Approval and exception decisions
   - Action callbacks with audit propagation
3. **ERP connectors**
   - System-of-record write-back for approved invoices
4. **Backend API**
   - State machine, policy checks, execution orchestration, audit
5. **Admin Console (`/console`)**
   - Setup, integration configuration, health checks, policies, team/admin operations

## Local Development (AP v1)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp env.example .env
# Edit .env
```

At minimum for useful AP v1 local testing, configure:

- Gmail OAuth credentials
- Slack app credentials (including `SLACK_SIGNING_SECRET`)
- Teams app creds if testing Teams flows (including `TEAMS_APP_ID`)
- Anthropic key (for attachment/image extraction where applicable)
- ERP connector credentials for the ERP(s) you are testing
- AP runtime flags appropriate to your environment (`AP_TEMPORAL_*`, retry gating, runner trust mode)

### 3. Run the backend

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Useful local URLs

- API docs: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Admin Console (if enabled): `http://localhost:8000/console`

If the console is feature-gated in your environment, enable the admin console flag before startup.

## Gmail Extension (AP v1)

The Gmail extension is the primary AP operator surface.

Relevant code:

- `/Users/mombalam/Desktop/Clearledgr.v1/ui/gmail-extension/`

Notes:

- The extension should point to your local backend URL (typically `http://127.0.0.1:8000`) for development.
- Reload the unpacked extension in Chrome after frontend changes.
- The AP experience should stay decision-first and low-clutter (see `PLAN.md` UX doctrine).

## Integrations (AP v1 Focus)

### Approvals

- Slack
- Teams

### ERP (AP posting target set for v1 GA doctrine)

- QuickBooks
- Xero
- NetSuite
- SAP

Actual connector readiness and enablement are governed by the parity and launch-gate rules in `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`.

## Security and Reliability Expectations (AP v1)

AP v1 is expected to enforce:

1. Server-side state transition validation
2. Policy checks before mutating actions
3. Idempotent approval actions and ERP posting
4. Immutable audit trail coverage for transitions and external writes
5. Clear exception handling and operator-visible outcomes

These are launch-gate requirements, not optional hardening items.

## Deployment Config Notes (AP v1)

The AP v1 implementation now expects the following to be explicitly configured in non-local environments:

- Slack callback verification: `SLACK_SIGNING_SECRET`
- Teams callback verification: `TEAMS_APP_ID`
- Runtime/orchestration truth flags: `AP_TEMPORAL_ENABLED`, `AP_TEMPORAL_REQUIRED`
- Autonomous retry gating: `AP_AGENT_AUTONOMOUS_RETRY_ENABLED`, `AP_AGENT_NON_DURABLE_RETRY_ALLOWED`
- Browser runner callback trust mode: `AP_BROWSER_RUNNER_TRUST_MODE`

For pilot deployments using the browser fallback runner:

- Protect `/api/agent/*` with JWT/API key auth (already enforced in-app)
- Use service/API-key credentials for external runner callbacks (`/results`, `/complete`)
- Prefer `AP_BROWSER_RUNNER_TRUST_MODE=api_or_admin` or tighter (`api_only`)

## GA Readiness Evidence Process

Repository + external artifact workflow for ERP parity, runbooks, and signoff evidence:

- `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_READINESS_EVIDENCE_PROCESS.md`

## Legacy / Historical Notes

- This repo includes historical docs and modules from earlier product iterations.
- If a document conflicts with `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`, treat `PLAN.md` as canonical.
- Historical MVP framing has been archived at:
  - `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/MVP_SCOPE.md`

## License

Proprietary
