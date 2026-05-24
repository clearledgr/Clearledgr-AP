"""
Solden Integrations

Real integrations with:
- ERP systems (QuickBooks, Xero, SAP)
- Bank connections (Plaid, manual CSV)

Solden never executes payments — the ERP/bank pays — so there are no
payment-gateway integrations here.
"""

from solden.integrations.erp_router import (
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
