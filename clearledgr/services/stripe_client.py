"""
Stripe Integration

Pulls payout transactions from Stripe for reconciliation against bank statements.

This is the gateway side of gateway-to-bank reconciliation.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Stripe SDK - optional import
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    stripe = None
    STRIPE_AVAILABLE = False
    logger.warning("Stripe SDK not installed. Run: pip install stripe")


@dataclass
class StripeTransaction:
    """A transaction from Stripe."""
    id: str
    amount: float  # In major currency units (e.g., EUR, not cents)
    currency: str
    date: str  # ISO format
    description: str
    type: str  # payout, charge, refund, etc.
    status: str
    payout_id: Optional[str] = None
    fee: float = 0.0
    net: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "amount": self.amount,
            "currency": self.currency.upper(),
            "date": self.date,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "payout_id": self.payout_id,
            "fee": self.fee,
            "net": self.net,
            "source": "stripe",
        }


class StripeClient:
    """
    Client for fetching Stripe transactions.
    
    For reconciliation, we primarily care about:
    1. Payouts - money transferred to bank account
    2. Balance transactions - all money movement
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Stripe client.
        
        Args:
            api_key: Stripe secret key. If not provided, uses STRIPE_SECRET_KEY env var.
        """
        if not STRIPE_AVAILABLE:
            raise ImportError("Stripe SDK not installed. Run: pip install stripe")
        
        self.api_key = api_key or os.getenv("STRIPE_SECRET_KEY")
        if not self.api_key:
            raise ValueError("Stripe API key not provided. Set STRIPE_SECRET_KEY env var.")
        
        stripe.api_key = self.api_key
    
    def get_payouts(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: str = "paid",
        limit: int = 100,
    ) -> List[StripeTransaction]:
        """
        Get payouts (transfers to bank account).
        
        These are what appear on bank statements.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            status: Filter by status (paid, pending, in_transit, canceled, failed)
            limit: Max number of payouts to fetch
        
        Returns:
            List of payout transactions
        """
        params = {
            "limit": limit,
            "status": status,
        }
        
        if start_date:
            params["created"] = {"gte": int(start_date.timestamp())}
        if end_date:
            if "created" in params:
                params["created"]["lte"] = int(end_date.timestamp())
            else:
                params["created"] = {"lte": int(end_date.timestamp())}
        
        try:
            payouts = stripe.Payout.list(**params)
        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {e}")
            raise
        
        transactions = []
        for payout in payouts.data:
            # Convert from cents to major units
            amount = payout.amount / 100
            
            tx = StripeTransaction(
                id=payout.id,
                amount=amount,
                currency=payout.currency,
                date=datetime.fromtimestamp(payout.arrival_date, tz=timezone.utc).strftime("%Y-%m-%d"),
                description=f"Stripe Payout {payout.id}",
                type="payout",
                status=payout.status,
                payout_id=payout.id,
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} payouts from Stripe")
        return transactions
    
    def get_balance_transactions(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        type_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[StripeTransaction]:
        """
        Get balance transactions (all money movement).
        
        More detailed than payouts - includes charges, refunds, fees.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            type_filter: Filter by type (charge, refund, payout, etc.)
            limit: Max transactions to fetch
        
        Returns:
            List of balance transactions
        """
        params = {"limit": limit}
        
        if start_date:
            params["created"] = {"gte": int(start_date.timestamp())}
        if end_date:
            if "created" in params:
                params["created"]["lte"] = int(end_date.timestamp())
            else:
                params["created"] = {"lte": int(end_date.timestamp())}
        
        if type_filter:
            params["type"] = type_filter
        
        try:
            balance_txs = stripe.BalanceTransaction.list(**params)
        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {e}")
            raise
        
        transactions = []
        for bt in balance_txs.data:
            # Convert from cents to major units
            amount = bt.amount / 100
            fee = bt.fee / 100
            net = bt.net / 100
            
            tx = StripeTransaction(
                id=bt.id,
                amount=amount,
                currency=bt.currency,
                date=datetime.fromtimestamp(bt.created, tz=timezone.utc).strftime("%Y-%m-%d"),
                description=bt.description or f"Stripe {bt.type}",
                type=bt.type,
                status="available" if bt.status == "available" else bt.status,
                fee=fee,
                net=net,
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} balance transactions from Stripe")
        return transactions
    
    def get_payout_transactions(self, payout_id: str) -> List[StripeTransaction]:
        """
        Get all transactions included in a specific payout.
        
        Useful for detailed reconciliation of what's in a bank deposit.
        
        Args:
            payout_id: Stripe payout ID
        
        Returns:
            List of transactions in the payout
        """
        try:
            balance_txs = stripe.BalanceTransaction.list(payout=payout_id, limit=100)
        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {e}")
            raise
        
        transactions = []
        for bt in balance_txs.data:
            amount = bt.amount / 100
            fee = bt.fee / 100
            net = bt.net / 100
            
            tx = StripeTransaction(
                id=bt.id,
                amount=amount,
                currency=bt.currency,
                date=datetime.fromtimestamp(bt.created, tz=timezone.utc).strftime("%Y-%m-%d"),
                description=bt.description or f"Stripe {bt.type}",
                type=bt.type,
                status=bt.status,
                payout_id=payout_id,
                fee=fee,
                net=net,
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transactions for payout {payout_id}")
        return transactions


def get_stripe_payouts(
    api_key: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Convenience function to get recent Stripe payouts.
    
    Args:
        api_key: Stripe secret key
        days: Number of days to look back
    
    Returns:
        List of payout dictionaries ready for reconciliation
    """
    client = StripeClient(api_key=api_key)
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    payouts = client.get_payouts(start_date=start_date, end_date=end_date)
    
    return [p.to_dict() for p in payouts]
