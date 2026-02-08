"""
Clearledgr Integrations

Real integrations with:
- Payment gateways (Stripe, Paystack, Flutterwave)
- ERP systems (QuickBooks, Xero, SAP)
- Bank connections (Plaid, manual CSV)
"""

from clearledgr.integrations.erp_router import (
    post_journal_entry,
    ERPConnection,
    get_erp_connection,
    set_erp_connection,
)

__all__ = [
    "post_journal_entry",
    "ERPConnection",
    "get_erp_connection",
    "set_erp_connection",
]
