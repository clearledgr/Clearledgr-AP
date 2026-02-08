"""
Flutterwave Integration

Pulls transaction data from Flutterwave for African markets.
Flutterwave is the dominant payment processor in Nigeria, Kenya, Ghana, and other African countries.

This is the gateway side of gateway-to-bank reconciliation for Africa.
"""

import os
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FlutterwaveTransaction:
    """A transaction from Flutterwave."""
    id: str
    amount: float
    currency: str
    date: str  # ISO format
    description: str
    type: str  # transfer, charge, etc.
    status: str
    reference: Optional[str] = None
    fee: float = 0.0
    
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
            "fee": self.fee,
            "source": "flutterwave",
        }


class FlutterwaveClient:
    """
    Client for fetching Flutterwave transactions.
    
    Flutterwave API: https://developer.flutterwave.com/
    
    Supports:
    - Transfers (payouts to bank accounts)
    - Transactions (payments received)
    - Settlements (batched payouts)
    """
    
    BASE_URL = "https://api.flutterwave.com/v3"
    
    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize Flutterwave client.
        
        Args:
            secret_key: Flutterwave secret key. If not provided, uses FLUTTERWAVE_SECRET_KEY env var.
        """
        self.secret_key = secret_key or os.getenv("FLUTTERWAVE_SECRET_KEY")
        if not self.secret_key:
            raise ValueError("Flutterwave secret key not provided. Set FLUTTERWAVE_SECRET_KEY env var.")
        
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
    
    async def get_transfers(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: str = "successful",
        page: int = 1,
        limit: int = 100,
    ) -> List[FlutterwaveTransaction]:
        """
        Get transfers (payouts to bank accounts).
        
        These are what appear on bank statements as deposits.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            status: Filter by status (successful, failed, pending)
            page: Page number for pagination
            limit: Results per page
        
        Returns:
            List of transfer transactions
        """
        params = {
            "page": page,
            "status": status,
        }
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transfers",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if data.get("status") != "success":
            logger.error(f"Flutterwave API error: {data.get('message')}")
            return []
        
        transactions = []
        for transfer in data.get("data", []):
            tx = FlutterwaveTransaction(
                id=str(transfer.get("id")),
                amount=float(transfer.get("amount", 0)),
                currency=transfer.get("currency", "NGN"),
                date=self._parse_date(transfer.get("created_at")),
                description=transfer.get("narration", f"Flutterwave Transfer {transfer.get('id')}"),
                type="transfer",
                status=transfer.get("status", "unknown"),
                reference=transfer.get("reference"),
                fee=float(transfer.get("fee", 0)),
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transfers from Flutterwave")
        return transactions
    
    async def get_transactions(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: str = "successful",
        page: int = 1,
        limit: int = 100,
    ) -> List[FlutterwaveTransaction]:
        """
        Get transactions (payments received).
        
        These are the incoming payments from customers.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            status: Filter by status
            page: Page number
            limit: Results per page
        
        Returns:
            List of payment transactions
        """
        params = {
            "page": page,
            "status": status,
        }
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transactions",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if data.get("status") != "success":
            logger.error(f"Flutterwave API error: {data.get('message')}")
            return []
        
        transactions = []
        for tx_data in data.get("data", []):
            tx = FlutterwaveTransaction(
                id=str(tx_data.get("id")),
                amount=float(tx_data.get("amount", 0)),
                currency=tx_data.get("currency", "NGN"),
                date=self._parse_date(tx_data.get("created_at")),
                description=tx_data.get("narration", f"Payment {tx_data.get('tx_ref', '')}"),
                type="charge",
                status=tx_data.get("status", "unknown"),
                reference=tx_data.get("tx_ref"),
                fee=float(tx_data.get("app_fee", 0)),
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} transactions from Flutterwave")
        return transactions
    
    async def get_settlements(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
    ) -> List[FlutterwaveTransaction]:
        """
        Get settlements (batched payouts).
        
        Flutterwave batches transactions and settles to bank account.
        These settlements are what appear on bank statements.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            page: Page number
        
        Returns:
            List of settlement transactions
        """
        params = {"page": page}
        
        if start_date:
            params["from"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["to"] = end_date.strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/settlements",
                headers=self.headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        
        if data.get("status") != "success":
            logger.error(f"Flutterwave API error: {data.get('message')}")
            return []
        
        transactions = []
        for settlement in data.get("data", []):
            tx = FlutterwaveTransaction(
                id=str(settlement.get("id")),
                amount=float(settlement.get("gross_amount", 0)),
                currency=settlement.get("currency", "NGN"),
                date=self._parse_date(settlement.get("created_at")),
                description=f"Flutterwave Settlement {settlement.get('id')}",
                type="settlement",
                status=settlement.get("status", "unknown"),
                reference=str(settlement.get("id")),
                fee=float(settlement.get("fee", 0)),
            )
            transactions.append(tx)
        
        logger.info(f"Fetched {len(transactions)} settlements from Flutterwave")
        return transactions
    
    def _parse_date(self, date_str: Optional[str]) -> str:
        """Parse Flutterwave date format to ISO."""
        if not date_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Flutterwave uses ISO format: "2026-01-15T10:30:00.000Z"
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return date_str[:10] if len(date_str) >= 10 else date_str


# Synchronous wrapper for convenience
def get_flutterwave_transfers_sync(
    secret_key: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Synchronous convenience function to get recent Flutterwave transfers.
    
    Args:
        secret_key: Flutterwave secret key
        days: Number of days to look back
    
    Returns:
        List of transfer dictionaries ready for reconciliation
    """
    import asyncio
    
    async def _fetch():
        client = FlutterwaveClient(secret_key=secret_key)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        transfers = await client.get_transfers(start_date=start_date, end_date=end_date)
        return [t.to_dict() for t in transfers]
    
    return asyncio.run(_fetch())


async def get_flutterwave_transfers(
    secret_key: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Async convenience function to get recent Flutterwave transfers.
    
    Args:
        secret_key: Flutterwave secret key
        days: Number of days to look back
    
    Returns:
        List of transfer dictionaries ready for reconciliation
    """
    client = FlutterwaveClient(secret_key=secret_key)
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    transfers = await client.get_transfers(start_date=start_date, end_date=end_date)
    
    return [t.to_dict() for t in transfers]
