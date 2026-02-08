# Reconciliation Guide

This guide covers the reconciliation process in detail.

## How Reconciliation Works

Clearledgr performs **3-way matching** across your payment gateway, bank, and internal records:

```
Gateway Transaction  ←→  Bank Statement  ←→  Internal Record
       TXN-001              BANK-001           INV-001
       €1,250               €1,250             €1,250
       2025-01-05           2025-01-06         2025-01-05
```

When all three match (within tolerance), they form a **matched group**.

## Matching Rules

### Amount Matching

By default, amounts must match within **0.5%** tolerance.

Example: €1,000 matches €1,005 (0.5% difference)

You can adjust this in settings:
- Lower tolerance (0.1%) for stricter matching
- Higher tolerance (1%) for more flexible matching

### Date Matching

By default, dates must be within **3 days** of each other.

This accounts for:
- Bank processing delays
- Weekend/holiday delays
- Time zone differences

### Matching Priority

1. **3-way match** (gateway + bank + internal) - Ideal
2. **2-way match** (any two sources) - Partial
3. **Unmatched** (only one source) - Exception

## Understanding Results

### CL_SUMMARY Sheet

| Field | Meaning |
|-------|---------|
| period_start | Start of reconciliation period |
| period_end | End of reconciliation period |
| total_gateway_volume | Sum of all gateway transactions |
| total_bank_volume | Sum of all bank transactions |
| matched_volume | Amount that matched across sources |
| matched_pct | Percentage of volume matched |
| exception_count | Number of unmatched items |

### CL_RECONCILED Sheet

Each row represents a matched group:

| Field | Meaning |
|-------|---------|
| group_id | Unique identifier for this match |
| gateway_tx_ids | Gateway transaction ID(s) |
| bank_tx_ids | Bank transaction ID(s) |
| internal_tx_ids | Internal record ID(s) |
| amount_gateway | Amount from gateway |
| amount_bank | Amount from bank |
| amount_internal | Amount from internal |
| status | Match type (3-way-match, 2-way-match, etc.) |

### CL_EXCEPTIONS Sheet

Each row is an unmatched item:

| Field | Meaning |
|-------|---------|
| source | Which system(s) the item is from |
| tx_ids | Transaction ID(s) |
| amounts | Transaction amount(s) |
| reason | Why it didn't match (machine-readable) |
| llm_explanation | AI explanation in plain language |
| suggested_action | What to do next |

## Common Exception Reasons

### no_counterparty
The transaction exists in one source but has no matching entry in other sources.

**Common causes:**
- Transaction not yet processed
- Recording error
- Different time periods

**Suggested actions:**
- Check if transaction is pending
- Verify recording in other systems
- Extend date range

### amount_mismatch
Amounts differ by more than the tolerance.

**Common causes:**
- Fees deducted separately
- Currency conversion differences
- Partial payments

**Suggested actions:**
- Check for associated fee transactions
- Verify currency conversion rates
- Look for split transactions

### timing_difference
Dates are too far apart.

**Common causes:**
- Month-end cutoff differences
- Bank processing delays
- System clock differences

**Suggested actions:**
- Adjust date tolerance
- Check transaction posting dates
- Verify system time settings

## Best Practices

### Data Preparation

1. **Export complete date ranges** - Include a buffer of a few days before and after your target period

2. **Standardize formats** - Use consistent date and number formats

3. **Include all columns** - More data helps with matching

### Review Process

1. **Start with exceptions** - Review CL_EXCEPTIONS first

2. **Group by reason** - Sort exceptions by reason to identify patterns

3. **Take action** - Use suggested_action as a starting point

4. **Track resolution** - Update your records as you resolve exceptions

### Improving Match Rates

1. **Consistent timing** - Run reconciliation at the same point in your process each period

2. **Clean data** - Fix data quality issues in source systems

3. **Adjust tolerances** - If you have known fee patterns, adjust amount tolerance

4. **Feedback** - Use the feedback feature to help Clearledgr learn your patterns

