# Sub-Processors

Authoritative list of every third party that processes Clearledgr
customer data. Every sub-processor is bound by a Data Processing
Agreement and an SCC for any cross-border transfer outside the EU.

Last reviewed: 2026-04-26.

| Sub-processor | Purpose | Data categories | Region | DPA |
|---|---|---|---|---|
| **Railway** | Application hosting (api, worker, web-app, beat) and managed Postgres + Redis. | All customer data at rest + in transit. | US-East. | https://railway.app/legal/dpa |
| **Anthropic** | LLM inference for invoice extraction, AP decision reasoning, vendor profile updates. PII redacted before inference; no training on customer data per Anthropic's data policy. | Invoice subject lines, vendor names, amount fields, OCR'd attachment text. NEVER bank details (encrypted before any LLM path). | US. | https://www.anthropic.com/legal/dpa |
| **Google Cloud (Gmail API + OAuth)** | Gmail scope-bound API access to inboxes the customer has explicitly connected. Read-only on default; modify scope only when customer enables draft-summarisation. | Email headers, body, attachments — only for connected mailboxes. | US (with global Google CDN). | https://cloud.google.com/terms/data-processing-addendum |
| **Microsoft (Azure AD)** | Workspace login OAuth for Microsoft 365 customers + Bot Framework for Teams approvals (when `FEATURE_TEAMS_ENABLED=true`). | Profile (email, name, tenant id) on auth; approval-card payloads on Teams. | US / EU (per tenant region). | https://www.microsoft.com/licensing/docs/dpa |
| **Slack** | Slack approvals — surfaces approval cards in customer-installed channels. | AP item summaries (vendor, amount, GL code) in card payloads. | US. | https://slack.com/trust/data-management/data-processing-addendum |
| **Sentry** | Error aggregation. PII-scrubbed via custom `before_send` hook; no invoice/vendor/bank details transmitted. | Stack traces + request metadata only — PII scrubbed at SDK boundary. | US. | https://sentry.io/legal/dpa/ |
| **Stripe** *(not yet active)* | Subscription billing. Will activate when paid plans launch. | Customer billing email, subscription state. NEVER any AP data. | US/EU. | https://stripe.com/dpa |

## Notification of changes

We notify customers at least **30 days before** any new sub-processor
processes their data, via:

1. Email to the workspace `Owner` user.
2. Update to this file in the public source tree.
3. Notification banner in the workspace.

Customers may object to a new sub-processor within 14 days, in which
case we work with them to find a workable path or, failing that, accept
their termination notice without penalty.

## Removed sub-processors

None to date. This section will document any removal with date + reason
when applicable.
