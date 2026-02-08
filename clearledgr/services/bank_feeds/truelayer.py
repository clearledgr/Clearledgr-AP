"""TrueLayer bank feed integration for UK and Europe.

TrueLayer is a leading Open Banking platform for UK and EU.
Coverage: 68 banks across 63 countries, strongest in UK.

Docs: https://docs.truelayer.com/
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from .base import (
    BankAccount,
    BankFeedService,
    BankTransaction,
    TransactionCategory,
    TransactionType,
)

logger = logging.getLogger(__name__)


class TrueLayerService(BankFeedService):
    """
    TrueLayer API integration for UK/EU bank feeds.
    
    Environment variables:
    - TRUELAYER_CLIENT_ID: Your TrueLayer client ID
    - TRUELAYER_CLIENT_SECRET: Your TrueLayer client secret
    - TRUELAYER_ENV: "sandbox" or "production" (default: sandbox)
    """
    
    provider_name = "truelayer"
    supported_countries = ["GB", "IE", "ES", "FR", "DE", "IT", "NL", "BE", "AT", "FI", "LT", "PL"]
    
    def __init__(self):
        self.client_id = os.getenv("TRUELAYER_CLIENT_ID")
        self.client_secret = os.getenv("TRUELAYER_CLIENT_SECRET")
        self.env = os.getenv("TRUELAYER_ENV", "sandbox")
        
        # Base URLs
        if self.env == "production":
            self.auth_url = "https://auth.truelayer.com"
            self.api_url = "https://api.truelayer.com"
        else:
            self.auth_url = "https://auth.truelayer-sandbox.com"
            self.api_url = "https://api.truelayer-sandbox.com"
    
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)
    
    def get_authorization_url(
        self, 
        redirect_uri: str, 
        state: str,
        providers: List[str] = None,
        scopes: List[str] = None,
        **kwargs
    ) -> str:
        """
        Get TrueLayer authorization URL.
        
        Args:
            redirect_uri: Callback URL
            state: CSRF state
            providers: List of provider IDs to filter (optional)
            scopes: OAuth scopes (default: info, accounts, transactions, balance)
        """
        if not self.client_id:
            raise RuntimeError("TRUELAYER_CLIENT_ID not configured")
        
        scopes = scopes or ["info", "accounts", "transactions", "balance", "offline_access"]
        
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": " ".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        
        if providers:
            params["providers"] = " ".join(providers)
        
        return f"{self.auth_url}/?{urlencode(params)}"
    
    async def connect_account(self, authorization_code: str, redirect_uri: str = None, **kwargs) -> Dict[str, Any]:
        """
        Exchange authorization code for access tokens.
        """
        if not self.client_id or not self.client_secret:
            raise RuntimeError("TrueLayer credentials not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.auth_url}/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri or "",
                    "code": authorization_code,
                },
                timeout=30,
            )
            response.raise_for_status()
            tokens = response.json()
            
            return {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "expires_in": tokens.get("expires_in"),
                "token_type": tokens.get("token_type", "Bearer"),
                "connected_at": datetime.utcnow().isoformat(),
                "provider": self.provider_name,
            }
    
    async def get_accounts(self, access_token: str) -> List[BankAccount]:
        """Get all connected bank accounts."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/data/v1/accounts",
                headers=self._get_headers(access_token),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            accounts = []
            for acc in data.get("results", []):
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
        """Get transactions for an account."""
        from_date = from_date or (date.today() - timedelta(days=90))
        to_date = to_date or date.today()
        
        async with httpx.AsyncClient() as client:
            params = {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
            }
            
            response = await client.get(
                f"{self.api_url}/data/v1/accounts/{account_id}/transactions",
                headers=self._get_headers(access_token),
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            
            transactions = []
            for txn in data.get("results", [])[:limit]:
                transactions.append(self._normalize_transaction(txn, account_id))
            
            logger.info(f"Fetched {len(transactions)} transactions from TrueLayer")
            return transactions
    
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh expired access token."""
        if not self.client_id or not self.client_secret:
            raise RuntimeError("TrueLayer credentials not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.auth_url}/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=30,
            )
            response.raise_for_status()
            tokens = response.json()
            
            return {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "expires_in": tokens.get("expires_in"),
                "provider": self.provider_name,
            }
    
    async def get_balance(self, access_token: str, account_id: str) -> Dict[str, Any]:
        """Get real-time account balance."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/data/v1/accounts/{account_id}/balance",
                headers=self._get_headers(access_token),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            results = data.get("results", [{}])
            balance = results[0] if results else {}
            
            return {
                "current_balance": balance.get("current"),
                "available_balance": balance.get("available"),
                "currency": balance.get("currency"),
                "provider": self.provider_name,
            }
    
    def _get_headers(self, access_token: str) -> Dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
    
    def _normalize_account(self, raw: Dict[str, Any]) -> BankAccount:
        """Normalize TrueLayer account to standard format."""
        return BankAccount(
            id=raw.get("account_id"),
            name=raw.get("display_name") or raw.get("account_number", {}).get("number", "Account"),
            institution_name=raw.get("provider", {}).get("display_name", "Bank"),
            account_type=raw.get("account_type", "TRANSACTION"),
            currency=raw.get("currency", "GBP"),
            account_number_masked=raw.get("account_number", {}).get("number"),
            iban=raw.get("account_number", {}).get("iban"),
            sort_code=raw.get("account_number", {}).get("sort_code"),
            provider=self.provider_name,
            raw_data=raw,
        )
    
    def _normalize_transaction(self, raw: Dict[str, Any], account_id: str) -> BankTransaction:
        """Normalize TrueLayer transaction to standard format."""
        amount = Decimal(str(raw.get("amount", 0)))
        
        # TrueLayer uses negative for debits
        if amount < 0:
            transaction_type = TransactionType.DEBIT
            amount = abs(amount)
        else:
            transaction_type = TransactionType.CREDIT
        
        # Parse date
        timestamp = raw.get("timestamp")
        if timestamp:
            try:
                txn_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
            except ValueError:
                txn_date = date.today()
        else:
            txn_date = date.today()
        
        return BankTransaction(
            id=raw.get("transaction_id"),
            account_id=account_id,
            amount=amount,
            currency=raw.get("currency", "GBP"),
            transaction_date=txn_date,
            description=raw.get("description", ""),
            merchant_name=raw.get("merchant_name"),
            category=self._categorize_transaction(raw),
            transaction_type=transaction_type,
            reference=raw.get("reference"),
            running_balance=Decimal(str(raw.get("running_balance", {}).get("amount", 0))) if raw.get("running_balance") else None,
            provider=self.provider_name,
            raw_data=raw,
        )
    
    def _categorize_transaction(self, raw: Dict[str, Any]) -> TransactionCategory:
        """Categorize transaction."""
        category = (raw.get("transaction_category") or "").upper()
        classification = (raw.get("transaction_classification") or [""])[0].lower() if raw.get("transaction_classification") else ""
        
        if category == "TRANSFER" or "transfer" in classification:
            return TransactionCategory.TRANSFER
        elif category == "PURCHASE" or "purchase" in classification:
            return TransactionCategory.PAYMENT
        elif category == "CREDIT" or "income" in classification:
            return TransactionCategory.INCOME
        elif "fee" in classification or "charge" in classification:
            return TransactionCategory.FEE
        elif "interest" in classification:
            return TransactionCategory.INTEREST
        elif "refund" in classification:
            return TransactionCategory.REFUND
        else:
            return TransactionCategory.OTHER
