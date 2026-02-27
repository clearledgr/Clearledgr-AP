# Clearledgr Privacy Policy

**Last updated: January 2026**

## Overview

Clearledgr ("we", "our", or "us") is a Chrome extension that helps finance teams run AP workflows directly within Gmail. This privacy policy explains how we collect, use, and protect your data.

## Data We Collect

### Email Data (Processed Locally)
- **What**: Subject lines, sender addresses, and email content of emails you explicitly interact with
- **Why**: To detect and process AP-relevant documents (invoices, payment requests, and remittance-related context)
- **Where**: Processed in extension + backend runtime surfaces. Extracted finance fields and workflow metadata are sent to backend services for AP execution, policy checks, and auditability.

### Financial Data
- **What**: Transaction amounts, dates, vendor names, invoice numbers
- **Why**: To run AP workflow decisions, approvals, ERP posting, and exception handling
- **Where**: Stored securely on our servers, encrypted at rest and in transit

### Usage Data
- **What**: Feature usage, error logs, performance metrics
- **Why**: To improve the product and diagnose issues
- **Where**: Our analytics systems (no personally identifiable information)

## Data We Do NOT Collect

- Full email content (only financial metadata)
- Passwords or authentication credentials
- Personal emails unrelated to finance
- Browsing history outside Gmail
- Data from other Google services

## How We Use Your Data

1. **Invoice/AP Processing**: Detect and extract invoice/AP request details from finance emails
2. **Approval Routing**: Send AP approval decisions to Slack/Teams surfaces
3. **ERP Posting**: Post approved invoices to ERP with idempotency controls
4. **Audit Trail**: Maintain compliance-ready logs of AP state transitions and external writes

## Data Security

- All data transmitted using TLS 1.3 encryption
- Data at rest encrypted using AES-256
- SOC 2 Type II compliant infrastructure
- Regular security audits and penetration testing
- No data sold to third parties

## Data Retention

- Active account data: Retained while account is active
- Deleted account data: Purged within 30 days of account deletion
- Audit logs: Retained for 7 years (regulatory requirement)

## Your Rights

You have the right to:
- **Access**: Request a copy of your data
- **Correct**: Update inaccurate data
- **Delete**: Request deletion of your data
- **Export**: Download your data in a standard format
- **Opt-out**: Disable specific data collection features

## Third-Party Services

We integrate with:
- **Slack / Teams**: For approval decisions and notifications (with your explicit authorization)
- **ERP systems**: For AP write-back (for example SAP, NetSuite, QuickBooks, Xero)
- **Google APIs**: For Gmail operator and runtime integration

Each integration requires separate authorization and can be revoked at any time.

## GDPR Compliance

For users in the European Economic Area:
- Legal basis for processing: Legitimate interest and explicit consent
- Data controller: Clearledgr Inc.
- Data transfers: EU-US Data Privacy Framework certified

## Contact

For privacy inquiries:
- Email: privacy@clearledgr.com
- Address: [Company Address]

## Changes to This Policy

We will notify users of material changes via email and in-app notification at least 30 days before changes take effect.
