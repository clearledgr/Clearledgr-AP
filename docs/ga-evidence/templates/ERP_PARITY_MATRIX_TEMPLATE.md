# ERP Parity Matrix Template

Release ID: `TBD`
Environment: `TBD`
Date: `YYYY-MM-DD`
Owner: `TBD`

## Scope

- Enabled ERP connectors:
  - QuickBooks: `yes/no`
  - Xero: `yes/no`
  - NetSuite: `yes/no`
  - SAP: `yes/no`

## Matrix

| ERP | API-first success | API fail -> fallback request | Fallback completion success | Fallback completion failure | Canonical response fields verified | Idempotency verified | Evidence links | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| QuickBooks | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Xero | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| NetSuite | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| SAP | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## Canonical Response Contract Checks

- `erp_type`
- `status`
- `erp_reference`
- `idempotency_key`
- `error_code`
- `error_message`

## Signoff

- Reviewer: `TBD`
- Date: `TBD`
- Result: `pass/fail`
