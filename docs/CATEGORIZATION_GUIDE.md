# Transaction Categorization Guide

## Overview

Transaction Categorization **automatically** classifies transactions to GL accounts based on intelligent pattern matching. Clearledgr runs autonomously when you open your spreadsheet. You only see exceptions that need your review.

**Local-first processing:** Categorization runs in your Google Sheets or Excel with optional backend augmentation when needed.

## How It Works

1. **Open your spreadsheet** - Clearledgr automatically detects new transactions
2. **Auto-categorization runs** - High-confidence matches are categorized instantly
3. **Exceptions surface** - Only low-confidence items appear for your review
4. **You correct once** - Your corrections train the system for next time

This is not a manual "click to run" process. Clearledgr is an autonomous agent that does the work for you.

## Getting Started

### Prerequisites

1. A sheet with transactions to categorize
2. A Chart of Accounts sheet with GL account definitions

### Required Data Format

#### Transactions Sheet

| Column | Required | Description |
|--------|----------|-------------|
| Description | Yes | Transaction description or memo |
| Vendor | Recommended | Vendor/payee name |
| Amount | Yes | Transaction amount |
| Date | Optional | Transaction date |

#### Chart of Accounts Sheet

| Column | Required | Description |
|--------|----------|-------------|
| Code | Yes | GL account code (e.g., "5000", "6100") |
| Name | Yes | Account name (e.g., "Office Supplies") |
| Category | Recommended | Category (e.g., "Expense", "Revenue") |
| Keywords | Recommended | Comma-separated keywords for matching |

### Example Chart of Accounts

| Code | Name | Category | Keywords |
|------|------|----------|----------|
| 5000 | Travel & Entertainment | Expense | airline, hotel, uber, lyft, flight |
| 5100 | Software & Subscriptions | Expense | aws, google, microsoft, slack, zoom |
| 5200 | Office Supplies | Expense | staples, office depot, supplies |
| 6000 | Utilities | Expense | electric, water, gas, internet |
| 4000 | Sales Revenue | Revenue | payment, invoice paid, customer |

## How It Runs (Autonomously)

### Google Sheets

1. **Just open your spreadsheet** - Clearledgr runs automatically
2. If there are new uncategorized transactions, they're processed
3. Check `CL_NEEDS_REVIEW` for any items needing your attention
4. To manually re-run: **Clearledgr → Categorization → Review Exceptions**

### Excel

1. **Just open your workbook** - Clearledgr runs automatically
2. A notification appears if items need review
3. Click the **Categorization** tab to see exceptions
4. To manually re-run: Click **Re-run Categorization**

**You don't need to "run" categorization.** It happens automatically.

## Understanding Results

### CL_CATEGORIZED Sheet

Transactions that were automatically categorized (high confidence).

| Field | Description |
|-------|-------------|
| Confidence | How confident the match is (e.g., 85%) |
| Match Reason | Why this account was selected |

### CL_NEEDS_REVIEW Sheet

Transactions requiring manual review (low confidence).

- Shows top 3 suggested accounts for each transaction
- Review and select the correct account
- Your selection is learned for future categorization

### CL_PATTERNS Sheet

Learned vendor patterns. As you review and correct categorizations, patterns are saved here.

## Improving Accuracy

### Add Keywords

Add relevant keywords to your Chart of Accounts:

```
Account: 5100 - Software & Subscriptions
Keywords: aws, google cloud, microsoft azure, slack, zoom, notion, figma
```

### Review Suggestions

When you select the correct account for a transaction in CL_NEEDS_REVIEW, that vendor pattern is learned and applied to future transactions.

### Consistent Vendor Names

Transactions with consistent vendor names categorize better. If your bank shows "AMZN*1234ABC", adding "amzn" as a keyword for Office Supplies helps.

## Confidence Threshold

Default: 70%

- **Higher threshold (80-90%)**: More items go to review, but higher accuracy
- **Lower threshold (50-70%)**: Faster processing, but may need more corrections

Adjust based on your data quality and tolerance for errors.

## Categorization Logic

The engine scores each GL account for each transaction:

1. **Historical Pattern** (+50%): Vendor previously assigned to this account
2. **Keyword Match** (+30%): Description/vendor contains account keywords
3. **Category Pattern** (+25%): Matches common industry patterns
4. **Account Name** (+20%): Description contains account name

Scores are summed and capped at 100%. The highest-scoring account is selected.

## FAQ

### Q: Why was a transaction categorized incorrectly?

Review the match reason. If keywords are too broad, make them more specific. If no historical pattern exists, correct the categorization to train the system.

### Q: Can I re-run categorization?

Yes. The CL_CATEGORIZED and CL_NEEDS_REVIEW sheets are overwritten each run. Historical patterns (CL_PATTERNS) are preserved.

### Q: How do I reset learned patterns?

Delete the CL_PATTERNS sheet. The engine will start fresh.

### Q: Is my data secure?

Yes. All categorization runs locally in your spreadsheet. No financial data is sent to any server.
