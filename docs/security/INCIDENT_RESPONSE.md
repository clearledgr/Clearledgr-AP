# Incident Response Plan

How Clearledgr detects, contains, communicates, and learns from
security incidents. This document is the operational runbook; for the
high-level commitment shown to prospects, see CC7.3 in
[CONTROLS.md](CONTROLS.md).

Last reviewed: 2026-04-26.

## Definitions

- **Incident** — any unauthorized access, disclosure, alteration,
  destruction, or unavailability of customer data; or any compromise
  of authentication, encryption, or access controls.
- **Severity 1 (S1)** — confirmed customer-data exposure or production
  outage > 30 min for a paying tenant.
- **Severity 2 (S2)** — suspected compromise; one customer affected;
  workaround available.
- **Severity 3 (S3)** — security finding without active exploitation
  (e.g., a vulnerability disclosed before exploitation is observed).

## On-call

A single on-call engineer covers nights/weekends. During business
hours, the founding engineer is primary; backup is the second-most-
recently-active committer in the repo.

Pager flow: Sentry alert → on-call PagerDuty / phone (configured in
deployment env) → engineer ack within 15 min for S1, 1h for S2, 1
business day for S3.

## Detection signals

| Signal | Source | Threshold |
|---|---|---|
| Error rate spike | Sentry | > 10x 1h baseline |
| Auth failure burst | Application logs (`RequestLoggingMiddleware`) | > 50 401/403 from one IP / 5 min |
| Health endpoint failing | External uptime monitor | 3 consecutive `/health` failures |
| Anomalous DB activity | psycopg connection pool metrics | Pool exhaustion > 30s |
| Vulnerability disclosure | `security@clearledgr.com` mailbox + GitHub Security Advisories | Any inbound report |
| Sub-processor incident | Vendor notification email | Any inbound notice |

## Response — first 60 minutes

1. **Acknowledge** — on-call ack the page; opens an incident channel
   (Slack `#incident-YYYYMMDD`).
2. **Triage** — assign severity (S1/S2/S3); identify scope (which org,
   which data, which surface).
3. **Contain** — for S1 with confirmed access: rotate the affected
   credential, revoke active sessions for the affected tenant, freeze
   the affected feature behind a flag.
4. **Preserve evidence** — snapshot Postgres + dump audit_events for
   the affected `organization_id` to immutable storage.
5. **Notify internal** — founder + counsel for any S1.

## Response — first 24 hours

6. **Customer notification** — for confirmed customer-data exposure:
   email the affected tenant's `Owner` and `Financial Controller`
   users with: what happened, what data was touched, what we've done,
   what the customer should do. **GDPR 72-hour breach notification
   clock starts when we have substantiated awareness.**
7. **Sub-processor coordination** — if the incident touches a
   sub-processor (e.g., Anthropic, Railway), open a vendor incident
   ticket and link it in our channel.
8. **Public statement** — if the incident affects multiple tenants,
   post a status-page entry and update the workspace `/status` page.

## Response — first week

9. **Root-cause analysis** — written RCA. Required sections: timeline,
   root cause, contributing factors, fix, follow-ups.
10. **Customer follow-up** — share RCA with affected customers.
11. **Public RCA** — for incidents that affect ≥ 25% of tenants or any
    breach notifiable under GDPR, publish a redacted RCA on the status
    page or company blog.

## Long-tail

12. **Lessons-learned retro** — within 2 weeks. Action items go on the
    sprint board with owners + dates.
13. **Control update** — if the incident reveals a missing control,
    update [CONTROLS.md](CONTROLS.md) in the same PR as the fix.

## Customer-facing SLAs

| Severity | Acknowledgement | Initial response | Resolution target |
|---|---|---|---|
| S1 | 15 min | 1h | 24h |
| S2 | 1h | 4h | 5 business days |
| S3 | 1 business day | 5 business days | Per joint plan |

## Contacts

- **Security reports**: `security@clearledgr.com`
- **Customer comms during S1**: the affected tenant's `Owner` email
  (on file from signup).
- **Regulatory authority**: per applicable GDPR supervisory authority
  jurisdiction; legal counsel coordinates filing.

## What this plan does NOT cover

- Routine bug triage (lives in the engineering issue tracker).
- Performance regressions without security implication (lives in SLO
  review).
- Sub-processor non-security incidents (vendor-driven RCA only).
