# Clearledgr AP v1 Scope

## Included (must map to the AP execution loop)

Email intake
- Gmail
- PDF invoices and email-body requests
- Multiple invoices across multiple emails

Validation
- Vendor and amount extraction
- Duplicate detection
- Required document checks

Approval
- Slack approval requests
- Explicit approve / reject actions
- Recorded decision and reason

Posting
- ERP write for approved invoices only
- No posting without approval

Audit
- Immutable record per invoice:
  - Source
  - Validation results
  - Approval decision
  - ERP action
  - Explanation

## Explicitly excluded

- Payment execution
- Reconciliation
- FP&A
- Close management
- Dashboards or analytics
- Vendor onboarding
- Procurement workflows
- Custom per-customer logic

If a feature does not move an invoice from email to ERP,
it is out of scope.
