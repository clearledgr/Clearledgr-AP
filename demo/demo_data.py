"""
Clearledgr Demo Data Generator

Run this script to populate the backend with realistic demo data
for demo presentations.

Usage:
    python demo/demo_data.py
"""

import asyncio
import httpx
from datetime import datetime, timedelta
import random

API_BASE = "http://localhost:8000"
ORG_ID = "demo-org"

# Demo Finance Emails
DEMO_EMAILS = [
    {
        "gmail_id": "demo-email-001",
        "subject": "Invoice #INV-2024-0892 from AWS",
        "sender": "billing@amazon.com",
        "snippet": "Your January 2026 AWS invoice is ready. Total amount: $12,847.32",
        "email_type": "invoice",
        "confidence": 0.97,
        "status": "pending",
        "received_at": (datetime.now() - timedelta(days=2)).isoformat(),
        "extracted_data": {
            "vendor": "Amazon Web Services",
            "invoice_number": "INV-2024-0892",
            "amount": 12847.32,
            "currency": "USD",
            "due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
            "line_items": [
                {"description": "EC2 Instances", "amount": 8234.50},
                {"description": "S3 Storage", "amount": 2156.82},
                {"description": "RDS Database", "amount": 1892.00},
                {"description": "Data Transfer", "amount": 564.00}
            ],
            "suggested_category": "Cloud Infrastructure",
            "suggested_gl_code": "6200"
        }
    },
    {
        "gmail_id": "demo-email-002",
        "subject": "Stripe Invoice - January 2026",
        "sender": "receipts@stripe.com",
        "snippet": "Invoice for payment processing fees. Amount due: $3,421.89",
        "email_type": "invoice",
        "confidence": 0.98,
        "status": "pending",
        "received_at": (datetime.now() - timedelta(days=3)).isoformat(),
        "extracted_data": {
            "vendor": "Stripe Inc.",
            "invoice_number": "STRIPE-89234",
            "amount": 3421.89,
            "currency": "USD",
            "due_date": (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d"),
            "line_items": [
                {"description": "Payment Processing (2.9% + $0.30)", "amount": 3156.42},
                {"description": "Radar Fraud Protection", "amount": 189.50},
                {"description": "Connect Platform Fees", "amount": 75.97}
            ],
            "suggested_category": "Payment Processing",
            "suggested_gl_code": "6150"
        }
    },
    {
        "gmail_id": "demo-email-003",
        "subject": "Your January Statement is Ready - Chase Business",
        "sender": "alerts@chase.com",
        "snippet": "Your business checking account statement for January 2026 is now available.",
        "email_type": "bank_statement",
        "confidence": 0.99,
        "status": "pending",
        "received_at": (datetime.now() - timedelta(days=1)).isoformat(),
        "extracted_data": {
            "bank": "Chase Business",
            "account_ending": "4892",
            "period": "January 1-31, 2026",
            "opening_balance": 284532.67,
            "closing_balance": 312847.23,
            "total_deposits": 156789.45,
            "total_withdrawals": 128474.89,
            "transaction_count": 47
        }
    },
    {
        "gmail_id": "demo-email-004",
        "subject": "Payment Received - Invoice #4521",
        "sender": "ar@acmecorp.com",
        "snippet": "We have received your payment of $45,000.00. Thank you for your business.",
        "email_type": "payment",
        "confidence": 0.92,
        "status": "processed",
        "received_at": (datetime.now() - timedelta(days=5)).isoformat(),
        "extracted_data": {
            "type": "payment_receipt",
            "amount": 45000.00,
            "currency": "USD",
            "reference": "CHK-89234",
            "payer": "Acme Corporation",
            "matched_invoice": "INV-2025-0234"
        }
    },
    {
        "gmail_id": "demo-email-005",
        "subject": "Gusto Payroll Invoice - Pay Period 01/01-01/15",
        "sender": "invoices@gusto.com",
        "snippet": "Payroll processing invoice for 23 employees",
        "email_type": "invoice",
        "confidence": 0.96,
        "status": "pending",
        "received_at": (datetime.now() - timedelta(days=4)).isoformat(),
        "extracted_data": {
            "vendor": "Gusto",
            "invoice_number": "GUSTO-2026-0115",
            "amount": 892.50,
            "currency": "USD",
            "due_date": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
            "line_items": [
                {"description": "Payroll Processing (23 employees)", "amount": 690.00},
                {"description": "Benefits Administration", "amount": 115.00},
                {"description": "Tax Filing Service", "amount": 87.50}
            ],
            "suggested_category": "Payroll Services",
            "suggested_gl_code": "6100"
        }
    },
    {
        "gmail_id": "demo-email-006",
        "subject": "HubSpot Invoice - Marketing Hub Professional",
        "sender": "billing@hubspot.com",
        "snippet": "Your monthly subscription invoice for HubSpot Marketing Hub",
        "email_type": "invoice",
        "confidence": 0.95,
        "status": "processed",
        "received_at": (datetime.now() - timedelta(days=6)).isoformat(),
        "extracted_data": {
            "vendor": "HubSpot Inc.",
            "invoice_number": "HS-2026-01-4521",
            "amount": 890.00,
            "currency": "USD",
            "due_date": (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
            "line_items": [
                {"description": "Marketing Hub Professional", "amount": 800.00},
                {"description": "Additional Contacts (5,000)", "amount": 90.00}
            ],
            "suggested_category": "Marketing Software",
            "suggested_gl_code": "6300"
        }
    }
]

# Demo Gateway Transactions (from payment processor)
DEMO_GATEWAY_TRANSACTIONS = [
    {
        "id": "gw-001",
        "amount": 45000.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
        "description": "Acme Corp Payment",
        "reference": "INV-4521",
        "source": "stripe"
    },
    {
        "id": "gw-002",
        "amount": 28456.78,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"),
        "description": "Stripe Payout",
        "reference": "STR-89234",
        "source": "stripe"
    },
    {
        "id": "gw-003",
        "amount": 15234.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
        "description": "TechStart Inc",
        "reference": "INV-4522",
        "source": "stripe"
    },
    {
        "id": "gw-004",
        "amount": 8750.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d"),
        "description": "GlobalCo Ltd",
        "reference": "INV-4523",
        "source": "paystack"
    },
    {
        "id": "gw-005",
        "amount": 12500.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        "description": "Enterprise Solutions Inc",
        "reference": "INV-4524",
        "source": "stripe"
    }
]

# Demo Bank Transactions
DEMO_BANK_TRANSACTIONS = [
    {
        "id": "bank-001",
        "amount": 45000.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"),
        "description": "Wire from ACME CORP",
        "reference": "CHK-89234",
        "source": "chase"
    },
    {
        "id": "bank-002",
        "amount": 28456.78,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
        "description": "STRIPE PAYOUT",
        "reference": "STR-89234",
        "source": "chase"
    },
    {
        "id": "bank-003",
        "amount": 15234.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d"),
        "description": "TECHSTART INC PAYMENT",
        "reference": "",
        "source": "chase"
    },
    {
        "id": "bank-004",
        "amount": 8750.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        "description": "GLOBALCO LTD",
        "reference": "",
        "source": "chase"
    },
    {
        "id": "bank-005",
        "amount": -12847.32,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
        "description": "AWS SERVICES",
        "reference": "",
        "source": "chase"
    },
    {
        "id": "bank-006",
        "amount": 5234.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        "description": "UNKNOWN TRANSFER",
        "reference": "",
        "source": "chase"
    },
    {
        "id": "bank-007",
        "amount": 12500.00,
        "currency": "USD",
        "date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
        "description": "ENTERPRISE SOLUTIONS",
        "reference": "",
        "source": "chase"
    }
]

# Demo Exceptions
DEMO_EXCEPTIONS = [
    {
        "id": "exc-001",
        "type": "no_match",
        "amount": 5234.00,
        "currency": "USD",
        "vendor": "Unknown Transfer",
        "priority": "high",
        "status": "open",
        "reason": "No matching invoice found for bank transaction",
        "suggestion": "Possible customer prepayment - review AR"
    },
    {
        "id": "exc-002",
        "type": "amount_mismatch",
        "amount": 150.00,
        "currency": "USD",
        "vendor": "Office Supplies Co",
        "priority": "low",
        "status": "open",
        "reason": "Amount differs by $150 from invoice",
        "suggestion": "Likely shipping charges - verify with vendor"
    },
    {
        "id": "exc-003",
        "type": "duplicate",
        "amount": 3421.89,
        "currency": "USD",
        "vendor": "Stripe",
        "priority": "medium",
        "status": "open",
        "reason": "Possible duplicate charge detected",
        "suggestion": "Compare with previous month's statement"
    }
]

# Demo Draft Journal Entries
DEMO_DRAFTS = [
    {
        "id": "draft-001",
        "description": "AWS Cloud Services - January 2026",
        "amount": 12847.32,
        "currency": "USD",
        "confidence": 0.97,
        "status": "pending",
        "debit_account": "6200 - Technology Expenses",
        "credit_account": "2000 - Accounts Payable",
        "source_email": "demo-email-001"
    },
    {
        "id": "draft-002",
        "description": "Stripe Processing Fees - January 2026",
        "amount": 3421.89,
        "currency": "USD",
        "confidence": 0.98,
        "status": "pending",
        "debit_account": "6150 - Merchant Fees",
        "credit_account": "2000 - Accounts Payable",
        "source_email": "demo-email-002"
    },
    {
        "id": "draft-003",
        "description": "Gusto Payroll Services - January 2026",
        "amount": 892.50,
        "currency": "USD",
        "confidence": 0.96,
        "status": "pending",
        "debit_account": "6100 - Payroll Expenses",
        "credit_account": "2000 - Accounts Payable",
        "source_email": "demo-email-005"
    }
]


async def seed_demo_data():
    """Seed the backend with demo data."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {
            "Content-Type": "application/json",
            "X-Organization-ID": ORG_ID
        }
        
        print("🚀 Seeding Clearledgr demo data...")
        
        # 1. Seed finance emails
        print("\n📧 Seeding finance emails...")
        for email in DEMO_EMAILS:
            try:
                resp = await client.post(
                    f"{API_BASE}/engine/emails",
                    json={"organization_id": ORG_ID, **email},
                    headers=headers
                )
                if resp.status_code < 400:
                    print(f"  ✓ {email['subject'][:50]}...")
                else:
                    print(f"  ✗ Failed: {email['subject'][:30]}... ({resp.status_code})")
            except Exception as e:
                print(f"  ✗ Error: {e}")
        
        # 2. Run reconciliation with demo transactions
        print("\n🔄 Running demo reconciliation...")
        try:
            resp = await client.post(
                f"{API_BASE}/engine/reconcile",
                json={
                    "organization_id": ORG_ID,
                    "gateway_transactions": DEMO_GATEWAY_TRANSACTIONS,
                    "bank_transactions": DEMO_BANK_TRANSACTIONS
                },
                headers=headers
            )
            if resp.status_code < 400:
                result = resp.json()
                print(f"  ✓ Reconciliation complete")
                print(f"    Matches: {result.get('result', {}).get('matches', 0)}")
                print(f"    Exceptions: {result.get('result', {}).get('exceptions', 0)}")
                print(f"    Match Rate: {result.get('result', {}).get('match_rate', 0):.1f}%")
            else:
                print(f"  ✗ Reconciliation failed ({resp.status_code})")
        except Exception as e:
            print(f"  ✗ Error: {e}")
        
        # 3. Seed exceptions (if not created by reconciliation)
        print("\n  Seeding exceptions...")
        for exc in DEMO_EXCEPTIONS:
            try:
                resp = await client.post(
                    f"{API_BASE}/engine/exceptions",
                    json={"organization_id": ORG_ID, **exc},
                    headers=headers
                )
                if resp.status_code < 400:
                    print(f"  ✓ {exc['vendor']}: ${exc['amount']}")
                else:
                    print(f"  ✗ Failed: {exc['vendor']} ({resp.status_code})")
            except Exception as e:
                print(f"  ✗ Error: {e}")
        
        # 4. Seed draft entries
        print("\n📝 Seeding draft journal entries...")
        for draft in DEMO_DRAFTS:
            try:
                resp = await client.post(
                    f"{API_BASE}/engine/drafts",
                    json={"organization_id": ORG_ID, **draft},
                    headers=headers
                )
                if resp.status_code < 400:
                    print(f"  ✓ {draft['description'][:40]}...")
                else:
                    print(f"  ✗ Failed: {draft['description'][:30]}... ({resp.status_code})")
            except Exception as e:
                print(f"  ✗ Error: {e}")
        
        print("\n Demo data seeding complete!")
        print("\nDemo Summary:")
        print(f"  • {len(DEMO_EMAILS)} finance emails")
        print(f"  • {len(DEMO_GATEWAY_TRANSACTIONS)} gateway transactions")
        print(f"  • {len(DEMO_BANK_TRANSACTIONS)} bank transactions")
        print(f"  • {len(DEMO_EXCEPTIONS)} exceptions")
        print(f"  • {len(DEMO_DRAFTS)} draft entries")


def generate_sheets_csv():
    """Generate CSV files for Google Sheets demo."""
    import csv
    import os
    
    demo_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Gateway transactions CSV
    gateway_file = os.path.join(demo_dir, "gateway_transactions.csv")
    with open(gateway_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Amount", "Date", "Description", "Reference"])
        for tx in DEMO_GATEWAY_TRANSACTIONS:
            writer.writerow([tx["amount"], tx["date"], tx["description"], tx["reference"]])
    print(f"✓ Created {gateway_file}")
    
    # Bank transactions CSV
    bank_file = os.path.join(demo_dir, "bank_transactions.csv")
    with open(bank_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Amount", "Date", "Description", "Reference"])
        for tx in DEMO_BANK_TRANSACTIONS:
            writer.writerow([tx["amount"], tx["date"], tx["description"], tx["reference"]])
    print(f"✓ Created {bank_file}")


if __name__ == "__main__":
    print("=" * 60)
    print("CLEARLEDGR DEMO DATA GENERATOR")
    print("=" * 60)
    
    # Generate CSV files for Sheets
    print("\n Generating CSV files for Google Sheets...")
    generate_sheets_csv()
    
    # Seed backend
    print("\n🔌 Connecting to backend...")
    try:
        asyncio.run(seed_demo_data())
    except Exception as e:
        print(f"\n Failed to connect to backend: {e}")
        print("Make sure the backend is running: python3 -m uvicorn main:app --port 8000")
