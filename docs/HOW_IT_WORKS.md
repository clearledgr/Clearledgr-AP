# How Clearledgr AP v1 Works

## Overview

Clearledgr AP v1 is an **inbox-native, agentic AP workflow** for finance teams.

It starts in Gmail, routes approvals in Slack and Teams, and writes approved invoices into ERP systems with policy checks, deterministic workflow orchestration, and an auditable execution trail.

Clearledgr is not a standalone AP dashboard for daily work. The day-to-day operator workflow lives in Gmail and chat.

## Product Shape (AP v1)

- **Gmail** = intake, context, status, exceptions, next action
- **Slack / Teams** = approval and escalation decisions
- **ERP** = system of record
- **Clearledgr** = policy + orchestration + execution + audit

## Step-by-Step (AP v1)

### 1. Set up Clearledgr (Workspace Shell)

An admin connects:

1. Gmail
2. Slack and/or Teams (AP v1 GA requires both channel contracts)
3. ERP connector(s)
4. Approval routing configuration
5. AP policies

The Workspace Shell is for setup, configuration, and health checks, not daily AP processing.

### 2. Invoice arrives in Gmail

When an invoice or AP-related request lands in the inbox, Clearledgr detects it and creates (or links to) an invoice-centric AP item.

Clearledgr can:

- classify AP-relevant messages
- parse email content and attachments
- extract key invoice fields (including PDFs/images where supported)
- associate multiple related emails/threads to one invoice item when appropriate

### 3. Clearledgr validates before routing

Before approval routing or ERP posting, Clearledgr runs deterministic checks such as:

- duplicate detection / merge-link checks
- policy checks
- PO / receipt / budget checks (where configured data is available)
- extraction confidence gate checks for critical fields

If there is an issue, Clearledgr creates an explicit exception state (for example low confidence, mismatch, missing info) with a clear next action.

### 4. Gmail thread becomes the AP control surface

Inside Gmail, the operator sees a focused AP workspace for the invoice:

- status
- extracted fields
- exceptions
- next action
- audit breadcrumbs

Technical details and deep context are hidden behind progressive disclosure so the thread card stays decision-first and uncluttered.

### 5. Approvals happen in Slack and Teams

When an invoice needs approval, Clearledgr sends an approval request to the configured approver(s) in Slack or Teams.

The approval card includes the information needed to decide:

- invoice summary
- validation summary / exceptions
- requested action
- approve / reject / request-info actions

Approval actions are handled through a common contract and must be idempotent (duplicate clicks/callbacks cannot create duplicate approvals or posts).

### 6. Approved invoices are posted to ERP (system of record)

Once an invoice is approved and all posting preconditions are satisfied, Clearledgr posts the invoice to the ERP.

AP v1 doctrine defines ERP write-back as:

- API-first
- idempotent
- audit-traced
- policy-guarded

If a fallback path is used (for example a gated browser-based path where allowed), it must be previewed, confirmed, and audited.

### 7. Clearledgr records audit breadcrumbs and outcomes

Clearledgr records:

- validation outcomes
- approval requests and decisions
- state transitions
- ERP posting attempts/results
- overrides and exception resolutions

These breadcrumbs are surfaced in-context (Gmail and admin/ops tools) so finance teams can trust what happened and why.

## Confidence and Human Review (AP v1 defaults)

Clearledgr uses confidence-based extraction and review gating.

Default AP v1 behavior:

1. Critical extracted fields are confidence-checked.
2. Low-confidence critical fields block posting.
3. A human can review/correct fields.
4. Overrides require justification and audit logging.

This is how Clearledgr remains agentic without becoming unsafe or opaque.

## What AP v1 Does Not Do

AP v1 does **not** include:

- payment execution / payment scheduling
- bank-feed reconciliation workflows
- consumer messaging surfaces (WhatsApp/Telegram)
- a required standalone AP dashboard for daily use

Those are separate product decisions and are not part of the AP v1 doctrine.

## Why This Matters

Clearledgr’s AP v1 value is not just "UI inside Gmail."

The differentiator is the combination of:

- inbox-native workflow
- chat-native approvals
- ERP write-back
- deterministic policy enforcement
- auditable execution

That is what makes it an embedded finance execution layer rather than a lightweight automation plugin.

## Canonical Reference

For the authoritative AP v1 doctrine, contracts, and GA launch gates, see:

- `/Users/mombalam/Desktop/Clearledgr.v1/PLAN.md`
