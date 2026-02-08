"""
Paystack Integration

Pulls settlement and transaction data from Paystack.
Paystack is widely used in Nigeria, Ghana, South Africa, and Kenya.

Use case: Investment/savings platforms receive payments via Paystack,
then need to reconcile Paystack settlements against bank deposits
and internal records of what customers paid.

Flow:
1. Customer pays via Paystack (NGN 50,000)
2. Paystack settles to bank (NGN 49,250 after fees)
3. Bank shows deposit (NGN 49,250)
4. Internal records show customer payment (NGN 50,000)

Clearledgr reconciles all three.
"""

import os
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PaystackTransaction:
    """A transaction from Paystack."""
    id: str
    amount: float  # In major currency units (e.g., NGN, not kobo)
    currency: str
    date: str  # ISO format
    description: str
    type: str  # charge, transfer, settlement
    status: str
    reference: Optional[str] = None
    customer_email: Optional[str] = None
    fee: float = 0.0
    net: float = 0.0
    settlement_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "amount": self.amount,
            "currency": self.currency,
            "date": self.date,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "reference": self.reference,
            "customer_email": self.customer_email,
            "fee": self.fee,
            "net": self.net,
            "settlement_id": self.settlement_id,
            "source": "paystack",
        }


class PaystackClient:
    """
    Client for fetching Paystack data.
    
    Paystack API: https://paystack.com/docs/api/
    
    Key endpoints for reconciliation:
    - /settlement: Batched payouts to bank account
    - /transaction: Individual customer payments
    - /transfer: Payouts you initiate
    """
    
    BASE_URL = "https://api.paystack.co"
    
    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize Paystack client.
        
        Args:
            secret_key: Paystack secret key. If not provided, uses PAYSTACK_SECRET_KEY env var.
        """
        self.secret_key = secret_key or os.getenv("PAYSTACK_SECRET_KEY")
        if not self.secret_key:
            raise ValueError("Paystack secret key not provided. Set PAYSTACK_SECRET_KEY env var.")
        
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
    
    async def get_settlements(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> List[PaystackTransaction]:
        """
        Get settlements (payouts to bank account).
        
        Settlements are what appear on your bank statement.
        Paystack batches transactions and settles daily (T+1 or T+2).
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            page: Page number
            per_page: Results per page (max 200)
        
        Returns:
            List of settlement transactions
        """
        params = {
            "page": page,
            "perPage": min(per_page, 200),
        }
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/settlement",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if not data.get("status"):
            logger.error(f"Paystack API error: {data.get('message')}")
            return []
        
        transactions = []
        for settlement in data.get("data", []):
            # Paystack amounts are in kobo (NGN) or pesewas (GHS)
            # Divide by 100 to get major currency units
            total_amount = settlement.get("total_amount", 0) / 100
            
            tx = PaystackTransaction(
                id=str(settlement.get("id")),
                amount=total_amount,
                currency=settlement.get("currency", "NGN"),
                date=self._parse_date(settlement.get("settled_date") or settlement.get("created_at")),
                description=f"Paystack Settlement #{settlement.get('id')}",
                type="settlement",
                status=settlement.get("status", "success"),
                reference=str(settlement.get("id")),
                settlement_id=str(settlement.get("id")),
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} settlements from Paystack")
        return transactions
    
    async def get_settlement_transactions(
        self,
        settlement_id: str,
    ) -> List[PaystackTransaction]:
        """
        Get all transactions in a specific settlement.
        
        Useful for detailed reconciliation - see exactly which
        customer payments are included in a bank deposit.
        
        Args:
            settlement_id: Paystack settlement ID
        
        Returns:
            List of transactions in the settlement
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/settlement/{settlement_id}/transactions",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()
        
        if not data.get("status"):
            logger.error(f"Paystack API error: {data.get('message')}")
            return []
        
        transactions = []
        for tx_data in data.get("data", []):
            amount = tx_data.get("amount", 0) / 100
            fees = tx_data.get("fees", 0) / 100
            
            tx = PaystackTransaction(
                id=str(tx_data.get("id")),
                amount=amount,
                currency=tx_data.get("currency", "NGN"),
                date=self._parse_date(tx_data.get("paid_at") or tx_data.get("created_at")),
                description=tx_data.get("metadata", {}).get("custom_fields", [{}])[0].get("value", "") 
                           or f"Payment {tx_data.get('reference', '')}",
                type="charge",
                status=tx_data.get("status", "success"),
                reference=tx_data.get("reference"),
                customer_email=tx_data.get("customer", {}).get("email"),
                fee=fees,
                net=amount - fees,
                settlement_id=settlement_id,
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transactions for settlement {settlement_id}")
        return transactions
    
    async def get_transactions(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: str = "success",
        page: int = 1,
        per_page: int = 50,
    ) -> List[PaystackTransaction]:
        """
        Get individual transactions (customer payments).
        
        These are the payments customers made - use for reconciling
        against internal records of what you expected to receive.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            status: Filter by status (success, failed, abandoned)
            page: Page number
            per_page: Results per page
        
        Returns:
            List of payment transactions
        """
        params = {
            "status": status,
            "page": page,
            "perPage": min(per_page, 200),
        }
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transaction",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if not data.get("status"):
            logger.error(f"Paystack API error: {data.get('message')}")
            return []
        
        transactions = []
        for tx_data in data.get("data", []):
            amount = tx_data.get("amount", 0) / 100
            fees = tx_data.get("fees", 0) / 100
            
            tx = PaystackTransaction(
                id=str(tx_data.get("id")),
                amount=amount,
                currency=tx_data.get("currency", "NGN"),
                date=self._parse_date(tx_data.get("paid_at") or tx_data.get("created_at")),
                description=tx_data.get("metadata", {}).get("description", "") 
                           or f"Payment from {tx_data.get('customer', {}).get('email', 'customer')}",
                type="charge",
                status=tx_data.get("status", "success"),
                reference=tx_data.get("reference"),
                customer_email=tx_data.get("customer", {}).get("email"),
                fee=fees,
                net=amount - fees,
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transactions from Paystack")
        return transactions
    
    async def get_transfers(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> List[PaystackTransaction]:
        """
        Get transfers (payouts you initiate).
        
        These are manual payouts you send to vendors, partners, or
        customer refunds via Paystack.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            page: Page number
            per_page: Results per page
        
        Returns:
            List of transfer transactions
        """
        params = {
            "page": page,
            "perPage": min(per_page, 200),
        }
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transfer",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if not data.get("status"):
            logger.error(f"Paystack API error: {data.get('message')}")
            return []
        
        transactions = []
        for transfer in data.get("data", []):
            amount = transfer.get("amount", 0) / 100
            
            tx = PaystackTransaction(
                id=str(transfer.get("id")),
                amount=-amount,  # Negative because it's money out
                currency=transfer.get("currency", "NGN"),
                date=self._parse_date(transfer.get("transferred_at") or transfer.get("created_at")),
                description=transfer.get("reason") or f"Transfer to {transfer.get('recipient', {}).get('name', 'recipient')}",
                type="transfer",
                status=transfer.get("status", "success"),
                reference=transfer.get("reference"),
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transfers from Paystack")
        return transactions
    
    def _parse_date(self, date_str: Optional[str]) -> str:
        """Parse Paystack date format to ISO."""
        if not date_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Paystack uses ISO format: "2026-01-15T10:30:00.000Z"
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return date_str[:10] if len(date_str) >= 10 else date_str


async def get_paystack_settlements(
    secret_key: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Get recent Paystack settlements for reconciliation.
    
    Settlements are what appear on your bank statement.
    
    Args:
        secret_key: Paystack secret key
        days: Number of days to look back
    
    Returns:
        List of settlement dictionaries
    """
    client = PaystackClient(secret_key=secret_key)
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    settlements = await client.get_settlements(start_date=start_date, end_date=end_date)
    
    return [s.to_dict() for s in settlements]


async def get_paystack_transactions(
    secret_key: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Get recent Paystack transactions (customer payments).
    
    Use this to reconcile against internal records.
    
    Args:
        secret_key: Paystack secret key
        days: Number of days to look back
    
    Returns:
        List of transaction dictionaries
    """
    client = PaystackClient(secret_key=secret_key)
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    transactions = await client.get_transactions(start_date=start_date, end_date=end_date)
    
    return [t.to_dict() for t in transactions]
