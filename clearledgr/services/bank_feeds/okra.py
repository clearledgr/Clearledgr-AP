"""Okra bank feed integration for Africa.

Okra provides open finance APIs for African financial institutions.
Coverage: Nigeria (full), Kenya & South Africa (beta)

Docs: https://docs.okra.ng/
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from .base import (
    BankAccount,
    BankFeedService,
    BankTransaction,
    TransactionCategory,
    TransactionType,
)

logger = logging.getLogger(__name__)


class OkraService(BankFeedService):
    """
    Okra API integration for African bank feeds.
    
    Supports:
    - Nigeria: GTBank, Zenith, Access, First Bank, UBA, etc.
    - Kenya: Equity, KCB, Co-op Bank (beta)
    - South Africa: ABSA, Standard Bank, FNB (beta)
    
    Environment variables:
    - OKRA_SECRET_KEY: Your Okra secret key
    - OKRA_PUBLIC_KEY: Your Okra public key (for widget)
    - OKRA_ENV: "sandbox" or "production" (default: sandbox)
    """
    
    provider_name = "okra"
    supported_countries = ["NG", "KE", "ZA"]  # Nigeria, Kenya, South Africa
    
    def __init__(self):
        self.secret_key = os.getenv("OKRA_SECRET_KEY")
        self.public_key = os.getenv("OKRA_PUBLIC_KEY")
        self.env = os.getenv("OKRA_ENV", "sandbox")
        
        # Base URLs
        self.base_url = (
            "https://api.okra.ng/v2" if self.env == "production"
            else "https://api.okra.ng/v2/sandbox"
        )
        self.widget_url = "https://okra.ng/widget"
    
    def is_configured(self) -> bool:
        return bool(self.secret_key)
    
    def get_authorization_url(
        self, 
        redirect_uri: str, 
        state: str,
        products: List[str] = None,
        **kwargs
    ) -> str:
        """
        Get Okra widget URL for bank connection.
        
        Args:
            redirect_uri: Callback URL after connection
            state: CSRF state parameter
            products: List of products ["auth", "transactions", "balance", "identity"]
        """
        if not self.public_key:
            raise RuntimeError("OKRA_PUBLIC_KEY not configured")
        
        products = products or ["auth", "transactions", "balance"]
        products_str = ",".join(products)
        
        # Okra uses a widget-based flow
        return (
            f"{self.widget_url}?key={self.public_key}"
            f"&products={products_str}"
            f"&redirect_url={redirect_uri}"
            f"&state={state}"
            f"&env={self.env}"
        )
    
    async def connect_account(self, authorization_code: str, **kwargs) -> Dict[str, Any]:
        """
        Exchange Okra record ID for account access.
        
        After the widget flow, Okra sends a record_id that identifies the connection.
        """
        if not self.secret_key:
            raise RuntimeError("OKRA_SECRET_KEY not configured")
        
        # The authorization_code is actually the record_id from Okra widget
        record_id = authorization_code
        
        async with httpx.AsyncClient() as client:
            # Get the record details
            response = await client.get(
                f"{self.base_url}/records/{record_id}",
                headers=self._get_headers(),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            record = data.get("data", {}).get("record", {})
            
            return {
                "record_id": record_id,
                "customer_id": record.get("customer"),
                "account_id": record.get("account"),
                "bank_name": record.get("bank", {}).get("name"),
                "connected_at": datetime.utcnow().isoformat(),
                "provider": self.provider_name,
            }
    
    async def get_accounts(self, access_token: str) -> List[BankAccount]:
        """
        Get all accounts for a customer.
        
        In Okra, access_token is the customer_id or record_id.
        """
        if not self.secret_key:
            raise RuntimeError("OKRA_SECRET_KEY not configured")
        
        async with httpx.AsyncClient() as client:
            # Fetch accounts by customer
            response = await client.post(
                f"{self.base_url}/accounts/byCustomer",
                headers=self._get_headers(),
                json={"customer": access_token},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            accounts = []
            for acc in data.get("data", {}).get("accounts", []):
                accounts.append(self._normalize_account(acc))
            
            return accounts
    
    async def get_transactions(
        self,
        access_token: str,
        account_id: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 500,
    ) -> List[BankTransaction]:
        """
        Get transactions for an account.
        
        Args:
            access_token: Customer ID
            account_id: Okra account ID
            from_date: Start date
            to_date: End date
            limit: Max transactions
        """
        if not self.secret_key:
            raise RuntimeError("OKRA_SECRET_KEY not configured")
        
        from_date = from_date or (date.today() - timedelta(days=30))
        to_date = to_date or date.today()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/transactions/byAccount",
                headers=self._get_headers(),
                json={
                    "account": account_id,
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "limit": limit,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            
            transactions = []
            for txn in data.get("data", {}).get("transactions", []):
                transactions.append(self._normalize_transaction(txn, account_id))
            
            logger.info(f"Fetched {len(transactions)} transactions from Okra")
            return transactions
    
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Okra uses record-based access, no token refresh needed.
        Records remain valid until the user revokes access.
        """
        return {
            "access_token": refresh_token,  # Same as before
            "provider": self.provider_name,
        }
    
    async def get_balance(self, access_token: str, account_id: str) -> Dict[str, Any]:
        """Get real-time balance from Okra."""
        if not self.secret_key:
            raise RuntimeError("OKRA_SECRET_KEY not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/balance/byAccount",
                headers=self._get_headers(),
                json={"account": account_id},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            balance = data.get("data", {}).get("balance", {})
            
            return {
                "current_balance": balance.get("available_balance") or balance.get("ledger_balance"),
                "available_balance": balance.get("available_balance"),
                "ledger_balance": balance.get("ledger_balance"),
                "currency": balance.get("currency", "NGN"),
                "provider": self.provider_name,
            }
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers for Okra API."""
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
    
    def _normalize_account(self, raw: Dict[str, Any]) -> BankAccount:
        """Normalize Okra account to standard format."""
        return BankAccount(
            id=raw.get("_id") or raw.get("id"),
            name=raw.get("name") or raw.get("account_name", "Account"),
            institution_name=raw.get("bank", {}).get("name") or raw.get("bank_name", "Bank"),
            account_type=raw.get("type", "checking"),
            currency=raw.get("currency", "NGN"),
            account_number_masked=self._mask_account_number(raw.get("nuban")),
            current_balance=Decimal(str(raw.get("balance", 0))),
            available_balance=Decimal(str(raw.get("available_balance", raw.get("balance", 0)))),
            provider=self.provider_name,
            raw_data=raw,
        )
    
    def _normalize_transaction(self, raw: Dict[str, Any], account_id: str) -> BankTransaction:
        """Normalize Okra transaction to standard format."""
        # Determine transaction type
        amount = Decimal(str(raw.get("amount", 0)))
        txn_type = raw.get("type", "").lower()
        
        if txn_type == "credit" or amount > 0:
            transaction_type = TransactionType.CREDIT
        elif txn_type == "debit" or amount < 0:
            transaction_type = TransactionType.DEBIT
            amount = abs(amount)  # Normalize to positive
        else:
            transaction_type = TransactionType.UNKNOWN
        
        # Parse date
        date_str = raw.get("date") or raw.get("trans_date") or raw.get("cleared_date")
        if date_str:
            try:
                txn_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                txn_date = date.today()
        else:
            txn_date = date.today()
        
        # Categorize
        category = self._categorize_transaction(raw)
        
        return BankTransaction(
            id=raw.get("_id") or raw.get("id") or raw.get("trans_id", ""),
            account_id=account_id,
            amount=amount,
            currency=raw.get("currency", "NGN"),
            transaction_date=txn_date,
            posted_date=txn_date,
            description=raw.get("notes") or raw.get("narration") or "",
            merchant_name=raw.get("merchant", {}).get("name"),
            category=category,
            transaction_type=transaction_type,
            reference=raw.get("ref") or raw.get("reference"),
            counterparty_name=raw.get("beneficiary", {}).get("account_name"),
            counterparty_account=raw.get("beneficiary", {}).get("account_number"),
            running_balance=Decimal(str(raw.get("balance", 0))) if raw.get("balance") else None,
            provider=self.provider_name,
            raw_data=raw,
        )
    
    def _categorize_transaction(self, raw: Dict[str, Any]) -> TransactionCategory:
        """Categorize transaction based on Okra data."""
        category = (raw.get("category") or "").lower()
        narration = (raw.get("narration") or raw.get("notes") or "").lower()
        
        if "transfer" in category or "transfer" in narration:
            return TransactionCategory.TRANSFER
        elif "payment" in category or "payment" in narration:
            return TransactionCategory.PAYMENT
        elif "fee" in category or "charge" in narration:
            return TransactionCategory.FEE
        elif "interest" in category:
            return TransactionCategory.INTEREST
        elif "refund" in category or "reversal" in narration:
            return TransactionCategory.REFUND
        elif raw.get("type", "").lower() == "credit":
            return TransactionCategory.INCOME
        else:
            return TransactionCategory.OTHER
    
    def _mask_account_number(self, account_number: Optional[str]) -> Optional[str]:
        """Mask account number for display."""
        if not account_number or len(account_number) < 4:
            return account_number
        return f"****{account_number[-4:]}"
