# Data Processing Addendum (DPA) — Summary

This is the customer-facing summary of Clearledgr's GDPR-aligned Data
Processing Addendum. The full executable DPA is provided to customers
on request as a PDF and is signed alongside the service agreement.

Last reviewed: 2026-04-26.

## Roles

- **Customer** is the **Controller** of personal data processed in its
  workspace.
- **Clearledgr** is the **Processor**, acting on Customer's documented
  instructions.

## Categories of personal data

Clearledgr processes the following on behalf of the Customer:

- **Workspace user identifiers** — email, name, role, organisation
  membership.
- **Vendor metadata** — vendor name, sender email domain, payment
  terms (where personal — e.g., sole proprietors).
- **Invoice content** — vendor name, amount, GL code, invoice number,
  attachment text.
- **Bank details** — IBAN, routing number, account number — encrypted
  at rest and never transmitted to third-party LLMs.
- **Email metadata** — from connected mailboxes only; restricted to
  threads classified as finance-relevant.

## Sub-processors

See [SUB_PROCESSORS.md](SUB_PROCESSORS.md). Customer authorizes
Clearledgr to engage the listed sub-processors and is notified at
least 30 days before any new sub-processor begins processing.

## Cross-border transfers

For transfers from EU/UK/Switzerland to the US, Clearledgr relies on:

1. **EU-US Data Privacy Framework** where the sub-processor is
   self-certified.
2. **Standard Contractual Clauses (SCC 2021/914)** where the
   sub-processor is not certified.
3. **UK IDTA** for transfers from the UK.

## Security measures

Clearledgr maintains technical and organizational measures sufficient
to protect personal data, including:

- Encryption in transit (TLS 1.2+) and at rest (Fernet for sensitive
  fields, AES-256 for the database volume).
- Role-based access control with hierarchical enforcement.
- Append-only audit logging.
- Authenticated, rate-limited, and CSRF-protected mutating endpoints.
- Vulnerability disclosure program (see
  [VULNERABILITY_DISCLOSURE.md](VULNERABILITY_DISCLOSURE.md)).
- Incident response with a 72-hour breach-notification SLA (see
  [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md)).

Detailed control mapping is in [CONTROLS.md](CONTROLS.md).

## Data subject rights

Clearledgr will assist Customer in responding to data subject requests
(access, erasure, rectification, portability, objection, restriction)
through the workspace admin console and an export API. Where assistance
beyond standard tooling is needed, Clearledgr will respond within 30
days.

## Breach notification

Clearledgr will notify Customer of any Personal Data Breach without
undue delay and in any case within **72 hours** of becoming aware,
providing:

- Nature of the breach (categories, approximate numbers).
- Likely consequences.
- Measures taken or proposed.
- Point of contact for further information.

## Audit rights

Customer may, no more than once per 12 months and on 30 days' written
notice, audit Clearledgr's compliance with this DPA, either by
reviewing Clearledgr's then-current SOC2 / ISO 27001 reports or by
Customer's security team conducting a remote assessment.

## Term + return of data

This DPA remains in effect for the term of the service agreement.
Within 30 days of termination, Clearledgr will, at Customer's option,
delete or return all Personal Data, except as required by law to
retain (e.g., audit_events for tax or compliance retention windows).

## Liability

Subject to the limits in the service agreement.

## Governing law

English law unless the service agreement specifies otherwise.

## How to execute

Email `legal@clearledgr.com` to request the executable DPA. We aim to
return countersigned within 5 business days.
