# Clearledgr Demo Data

Sample data for testing and demonstrating Clearledgr v1 capabilities.

## Reconciliation Demo

Located in `/demo/reconciliation/`

### Files
- **gateway.csv** - Payment gateway transactions (12 transactions, EUR)
- **bank.csv** - Bank statement transactions (10 transactions)
- **internal.csv** - Internal ledger entries (12 invoices)

### Scenario
This demo data represents a January 2025 period for a B2B SaaS company with customers across Europe and Africa:

- 10 transactions should match 3-way (gateway + bank + internal)
- 1 failed payment (TXN-008) has no bank record
- 1 pending payment (TXN-012) has no bank record yet

### Expected Results
- **Matched**: 10 groups (3-way matches)
- **Exceptions**: 2 items (failed payment, pending payment)

### How to Test

1. Go to http://localhost:8000/admin
2. Upload the three CSV files
3. Set period: 2025-01-01 to 2025-01-31
4. Use this config:

```json
{
  "mappings": {
    "payment_gateway": {
      "Transaction ID": "txn_id",
      "Date": "date",
      "Net Amount": "net_amount"
    },
    "bank": {
      "Bank Transaction ID": "bank_txn_id",
      "Booking Date": "date",
      "Amount": "amount"
    },
    "internal": {
      "Internal ID": "internal_id",
      "Date": "date",
      "Amount": "amount"
    }
  },
  "amount_tolerance_pct": 0.5,
  "date_window_days": 3
}
```

---

## Importing to Google Sheets

1. Create a new Google Sheet
2. Import each CSV as a separate tab
3. Rename tabs to match (GATEWAY, BANK, INTERNAL for reconciliation)
4. Install Clearledgr add-on
5. Run from Clearledgr menu

---

## Importing to Excel

1. Open Excel
2. Import each CSV as a separate sheet
3. Rename sheets to match
4. Install Clearledgr add-in
5. Run from Clearledgr tab

