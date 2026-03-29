# Getting Started with Clearledgr AP v1

This guide covers the current AP v1 product shape:

- **Gmail** for inbox-native AP intake and triage
- **Slack / Teams** for approvals and escalation decisions
- **ERP** as system of record
- **Clearledgr** for policy, orchestration, execution, and audit

For canonical doctrine, contracts, and launch gates, use:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`

## Before You Start

Clearledgr AP v1 is not a standalone AP dashboard for daily work.

Daily AP operations happen in:
1. Gmail (primary operator surface)
2. Slack/Teams (approval surfaces)

The Workspace Shell is used for setup, configuration, and health checks.

## Quick Start (Admin Setup)

### Step 1: Start the backend

```bash
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

If using the Workspace Shell, ensure `WORKSPACE_SHELL_ENABLED` is enabled in your environment.

### Step 2: Open the Workspace Shell

Open:

- `http://127.0.0.1:8010/workspace`

Use the Workspace Shell for:
1. organization setup
2. integration connections
3. policy configuration
4. team invites
5. health diagnostics

### Step 3: Connect Gmail

Connect the Gmail account/workspace that will be monitored for AP intake.

Clearledgr AP v1 expects Gmail to be the primary intake surface for:
- invoices
- payment requests
- remittance and supporting AP context
- AP exceptions and follow-ups

### Step 4: Connect Slack and Teams

Configure approval channels and callbacks for both:
1. Slack
2. Teams

AP v1 doctrine requires both channel contracts to be supported at GA, even if you stage rollout during pilot validation.

### Step 5: Connect ERP

Connect the ERP(s) you plan to validate or use:

- QuickBooks
- Xero
- NetSuite
- SAP

Note:
- Connector presence in the repo is not the same as operational readiness.
- Readiness and parity are governed by `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`.

### Step 6: Configure AP policies

Set the baseline AP policy rules for:
1. approval thresholds
2. routing requirements
3. budget/PO behavior (where configured)
4. confidence and override requirements
5. browser fallback policy (if enabled)

### Step 7: Load the Gmail extension (operator experience)

Load the Gmail extension and verify it points to your backend URL (typically `http://127.0.0.1:8010` for local dev).

The Gmail surface should show:
1. focused AP item workspace
2. status + exceptions + next action
3. progressive disclosure for sources/context/audit details

## First End-to-End AP v1 Validation Flow

Use this as the first sanity test:

1. Send a test invoice email to the connected Gmail inbox
2. Confirm Clearledgr detects and creates/links an AP item
3. Open Gmail and verify the AP workspace appears on the thread
4. Confirm extraction fields and confidence state are visible
5. Trigger approval routing
6. Approve in Slack or Teams
7. Confirm ERP posting behavior (or safe blocked state if prerequisites are missing)
8. Confirm audit breadcrumbs are visible and consistent

## What to Expect in Gmail (AP v1)

The Gmail AP experience should prioritize:
1. current state
2. exceptions
3. next action
4. key extracted fields
5. audit breadcrumbs

It should not behave like a full dashboard embedded in Gmail.

## Troubleshooting (AP v1)

### Workspace Shell returns 404
Check:
1. backend is running on the expected port
2. `WORKSPACE_SHELL_ENABLED` is enabled in the environment

### Gmail sidebar cannot reach backend
Check:
1. extension backend URL is correct (`http://127.0.0.1:8010` for local)
2. backend server is running
3. CORS is configured for Gmail origin in your local setup

### Approvals do not appear in Slack/Teams
Check:
1. integration credentials installed correctly
2. callback routes reachable
3. channel/team configuration set in the Workspace Shell
4. per-org integration health in `/workspace` and `/api/workspace/health`

### Invoice detected but cannot post
Common causes:
1. low-confidence critical fields (review required)
2. policy or budget/PO block
3. missing ERP connector readiness
4. duplicate/exception state requiring explicit resolution

## Developer Notes

### Canonical references for AP v1 work

- Doctrine / launch gates: `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
- Backlog / sequencing: `/Users/mombalam/Desktop/Clearledgr.v1/TODO_BACKLOG.md`
- Strategy / expansion direction: `/Users/mombalam/Desktop/Clearledgr.v1/VISION.md`
- Point-in-time readiness audit: `/Users/mombalam/Desktop/Clearledgr.v1/docs/GA_LAUNCH_READINESS_TRACKER.md`

### Historical MVP framing

The earlier MVP scope doc has been archived:

- `/Users/mombalam/Desktop/Clearledgr.v1/docs/archive/MVP_SCOPE.md`

It is not the canonical source for AP v1.
