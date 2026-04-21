# ADR-003: Why Gmail is work surface, Slack is decision surface, ERP is record

Status: Accepted
Date: 2026-04-20 (identity lock-in this session; earlier doctrine had drifted)
Author: Mo

## Context

The product shape has three distinct surface roles. Prior doctrine (see 2026-03-25 product-direction plan, now SUPERSEDED) confused them — it treated "Pipeline" as the primary control plane, positioned Slack and Teams as co-equal decision surfaces, and left the surface ownership model open-ended.

Practical reality is sharper:

- **Invoices arrive in Gmail.** AP teams already live there. Email is where the thread history, attachments, and vendor conversation already exist. A dashboard that replaces the inbox is a dashboard nobody opens.
- **Approvals happen in Slack.** Finance leads are in Slack most of the working day. Teams exists in some enterprises — it's a V1.1 parity concern, not a V1 co-equal.
- **ERP owns the ledger.** QuickBooks / Xero / NetSuite / SAP is where the accountant's view of the world lives. If our internal record disagrees with the ERP's, the ERP wins by default. Our job is to write there well, not to replace it.

## Decision

**Surface roles are distinct and non-transferable.**

| Surface | Role | Authority |
|---|---|---|
| Gmail | Work surface | Intake + contextual per-item work. The sidebar shows Box state in thread context. |
| Slack | Decision surface | Approve / reject / needs-info / request-info. Cards are the only surface with action buttons. |
| ERP | Record surface | Post bill → get bill ID → we store the ID. ERP is the source of truth for posted transaction state. |

One Box, three renderings. The Box state is the source of truth internally; each surface shows the part of the Box it's responsible for.

## Consequences

**Wins:**
- The sales pitch is one sentence: "AP coordination agent that lives in your Gmail, routes approvals through Slack, and posts bills into your ERP." Every finance person understands it in 15 seconds.
- The extension doesn't need to reproduce Slack features or ERP features. Gmail stays Gmail-native.
- No "which surface is the product?" debate. Each surface has a defined role.
- Sidebar doesn't have approve/reject buttons because approvals don't happen in Gmail. That's a design choice the prior doctrine got wrong (see 2026-03-25 plan).

**Costs:**
- Customers on Outlook-only or Teams-only are V1.1, not V1. Shuts out ~20-30% of the mid-market.
- Requires Slack workspace + bot install + channel configuration per-tenant — adds 20 minutes to onboarding vs. "everything in-product."
- Gmail + Slack both down = the product degrades. Both are multi-region Google/Slack services with 99.9%+ SLAs, but the dependency surface is real.

## Alternatives considered

- **Pipeline (our own UI) as primary work surface.** What the 2026-03-25 plan argued for. Rejected: finance teams don't want to context-switch to another tab for AP; they want AP to come to where they already are. Pipeline exists as a *view* (batch workload, cross-entity), not the product's center of gravity.
- **In-product approval flows** (don't route to Slack/Teams — do it in the extension/web UI). Rejected: finance leads are already getting pinged in Slack for other things; adding another pinging surface is friction. Meet them where they are.
- **Own the ERP as a write-through cache.** Tempting for a future product; wrong for V1. Customers' accountants trust their ERP, not our system. Being a writer-not-owner is a feature, not a compromise.
