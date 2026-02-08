"""Nordigen (GoCardless) bank feed integration for EU Open Banking.

Nordigen provides free Open Banking API access across Europe.
Now part of GoCardless. Has a generous free tier.

Coverage: 2300+ banks across 31 European countries.

Docs: https://nordigen.com/en/docs/
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


class NordigenService(BankFeedService):
    """
    Nordigen/GoCardless Bank Account Data API.
    
    Free tier includes:
    - 50 bank connections
    - 90 days transaction history
    - Account balances
    
    Environment variables:
    - NORDIGEN_SECRET_ID: Your Nordigen secret ID
    - NORDIGEN_SECRET_KEY: Your Nordigen secret key
    """
    
    provider_name = "nordigen"
    supported_countries = [
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
        "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
        "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "GB"
    ]
    
    BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"
    
    def __init__(self):
        self.secret_id = os.getenv("NORDIGEN_SECRET_ID")
        self.secret_key = os.getenv("NORDIGEN_SECRET_KEY")
        self._access_token = None
        self._token_expires = None
    
    def is_configured(self) -> bool:
        return bool(self.secret_id and self.secret_key)
    
    async def _ensure_token(self) -> str:
        """Get or refresh the access token."""
        if self._access_token and self._token_expires and datetime.utcnow() < self._token_expires:
            return self._access_token
        
        if not self.secret_id or not self.secret_key:
            raise RuntimeError("Nordigen credentials not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/token/new/",
                json={
                    "secret_id": self.secret_id,
                    "secret_key": self.secret_key,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            self._access_token = data.get("access")
            expires_in = data.get("access_expires", 86400)
            self._token_expires = datetime.utcnow() + timedelta(seconds=expires_in - 60)
            
            return self._access_token
    
    def get_authorization_url(
        self, 
        redirect_uri: str, 
        state: str,
        institution_id: str = None,
        **kwargs
    ) -> str:
        """
        Nordigen requires a multi-step flow:
        1. Create requisition (agreement)
        2. Redirect user to bank
        
        This returns a placeholder - actual URL comes from create_requisition().
        """
        # Return a placeholder - the actual flow is:
        # 1. Call get_institutions() to list banks
        # 2. User selects a bank
        # 3. Call create_requisition() with that institution
        # 4. Redirect to the link returned
        return f"nordigen://select-bank?redirect={redirect_uri}&state={state}"
    
    async def get_institutions(self, country: str) -> List[Dict[str, Any]]:
        """
        Get list of supported banks for a country.
        
        Args:
            country: 2-letter country code (e.g., "DE", "FR", "GB")
            
        Returns:
            List of institutions with id, name, logo, etc.
        """
        token = await self._ensure_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/institutions/",
                headers=self._get_headers(token),
                params={"country": country.upper()},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
    
    async def create_requisition(
        self,
        institution_id: str,
        redirect_uri: str,
        reference: str,
        user_language: str = "EN",
    ) -> Dict[str, Any]:
        """
        Create a requisition (bank connection request).
        
        Returns:
            Requisition with 'link' to redirect user to
        """
        token = await self._ensure_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/requisitions/",
                headers=self._get_headers(token),
                json={
                    "institution_id": institution_id,
                    "redirect": redirect_uri,
                    "reference": reference,
                    "user_language": user_language,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "requisition_id": data.get("id"),
                "link": data.get("link"),  # Redirect user here
                "status": data.get("status"),
                "institution_id": institution_id,
                "provider": self.provider_name,
            }
    
    async def connect_account(self, authorization_code: str, **kwargs) -> Dict[str, Any]:
        """
        Get account access after user completes bank authorization.
        
        In Nordigen, authorization_code is the requisition_id.
        """
        token = await self._ensure_token()
        requisition_id = authorization_code
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/requisitions/{requisition_id}/",
                headers=self._get_headers(token),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "requisition_id": requisition_id,
                "account_ids": data.get("accounts", []),  # List of account IDs
                "status": data.get("status"),
                "institution_id": data.get("institution_id"),
                "connected_at": datetime.utcnow().isoformat(),
                "provider": self.provider_name,
            }
    
    async def get_accounts(self, access_token: str) -> List[BankAccount]:
        """
        Get accounts from a requisition.
        
        In Nordigen, access_token is the requisition_id.
        """
        token = await self._ensure_token()
        requisition_id = access_token
        
        # Get requisition to find account IDs
        async with httpx.AsyncClient() as client:
            req_response = await client.get(
                f"{self.BASE_URL}/requisitions/{requisition_id}/",
                headers=self._get_headers(token),
                timeout=30,
            )
            req_response.raise_for_status()
            requisition = req_response.json()
            
            accounts = []
            for account_id in requisition.get("accounts", []):
                # Get account details
                acc_response = await client.get(
                    f"{self.BASE_URL}/accounts/{account_id}/details/",
                    headers=self._get_headers(token),
                    timeout=30,
                )
                if acc_response.status_code == 200:
                    acc_data = acc_response.json()
                    
                    # Get balance
                    bal_response = await client.get(
                        f"{self.BASE_URL}/accounts/{account_id}/balances/",
                        headers=self._get_headers(token),
                        timeout=30,
                    )
                    balance_data = bal_response.json() if bal_response.status_code == 200 else {}
                    
                    accounts.append(self._normalize_account(account_id, acc_data, balance_data))
            
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
        token = await self._ensure_token()
        from_date = from_date or (date.today() - timedelta(days=90))
        to_date = to_date or date.today()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/accounts/{account_id}/transactions/",
                headers=self._get_headers(token),
                params={
                    "date_from": from_date.isoformat(),
                    "date_to": to_date.isoformat(),
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            
            transactions = []
            
            # Nordigen returns booked and pending separately
            booked = data.get("transactions", {}).get("booked", [])
            for txn in booked[:limit]:
                transactions.append(self._normalize_transaction(txn, account_id, is_pending=False))
            
            pending = data.get("transactions", {}).get("pending", [])
            for txn in pending[:max(0, limit - len(transactions))]:
                transactions.append(self._normalize_transaction(txn, account_id, is_pending=True))
            
            logger.info(f"Fetched {len(transactions)} transactions from Nordigen")
            return transactions
    
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Nordigen requisitions remain valid for 90 days.
        No token refresh needed - just return the same access.
        """
        return {
            "access_token": refresh_token,
            "provider": self.provider_name,
        }
    
    async def get_balance(self, access_token: str, account_id: str) -> Dict[str, Any]:
        """Get account balance."""
        token = await self._ensure_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/accounts/{account_id}/balances/",
                headers=self._get_headers(token),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            
            balances = data.get("balances", [])
            
            current = None
            available = None
            currency = "EUR"
            
            for bal in balances:
                bal_type = bal.get("balanceType", "")
                amount = bal.get("balanceAmount", {})
                
                if "interimAvailable" in bal_type or "available" in bal_type.lower():
                    available = float(amount.get("amount", 0))
                    currency = amount.get("currency", "EUR")
                elif "closingBooked" in bal_type or "current" in bal_type.lower():
                    current = float(amount.get("amount", 0))
                    currency = amount.get("currency", "EUR")
            
            return {
                "current_balance": current,
                "available_balance": available or current,
                "currency": currency,
                "provider": self.provider_name,
            }
    
    def _get_headers(self, token: str) -> Dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def _normalize_account(
        self, 
        account_id: str, 
        acc_data: Dict[str, Any],
        balance_data: Dict[str, Any]
    ) -> BankAccount:
        """Normalize Nordigen account to standard format."""
        account = acc_data.get("account", {})
        
        # Find balance
        balances = balance_data.get("balances", [])
        current = None
        available = None
        currency = account.get("currency", "EUR")
        
        for bal in balances:
            amount = bal.get("balanceAmount", {})
            bal_type = bal.get("balanceType", "")
            
            if "available" in bal_type.lower():
                available = Decimal(str(amount.get("amount", 0)))
            elif "booked" in bal_type.lower() or "current" in bal_type.lower():
                current = Decimal(str(amount.get("amount", 0)))
            currency = amount.get("currency", currency)
        
        return BankAccount(
            id=account_id,
            name=account.get("name") or account.get("product") or "Account",
            institution_name=account.get("institution_id", "Bank"),
            account_type=account.get("cashAccountType", "CACC"),
            currency=currency,
            iban=account.get("iban"),
            current_balance=current,
            available_balance=available or current,
            provider=self.provider_name,
            raw_data=acc_data,
        )
    
    def _normalize_transaction(
        self, 
        raw: Dict[str, Any], 
        account_id: str,
        is_pending: bool = False
    ) -> BankTransaction:
        """Normalize Nordigen transaction to standard format."""
        amount_data = raw.get("transactionAmount", {})
        amount = Decimal(str(amount_data.get("amount", 0)))
        currency = amount_data.get("currency", "EUR")
        
        # Determine type from amount sign
        if amount < 0:
            transaction_type = TransactionType.DEBIT
            amount = abs(amount)
        else:
            transaction_type = TransactionType.CREDIT
        
        # Parse dates
        booking_date = raw.get("bookingDate") or raw.get("valueDate")
        if booking_date:
            try:
                txn_date = datetime.fromisoformat(booking_date).date()
            except ValueError:
                txn_date = date.today()
        else:
            txn_date = date.today()
        
        # Build description from available fields
        description_parts = [
            raw.get("remittanceInformationUnstructured"),
            raw.get("additionalInformation"),
        ]
        description = " | ".join([p for p in description_parts if p])
        
        # Get counterparty info
        counterparty = raw.get("creditorName") or raw.get("debtorName")
        counterparty_account = raw.get("creditorAccount", {}).get("iban") or raw.get("debtorAccount", {}).get("iban")
        
        return BankTransaction(
            id=raw.get("transactionId") or raw.get("internalTransactionId", ""),
            account_id=account_id,
            amount=amount,
            currency=currency,
            transaction_date=txn_date,
            posted_date=None if is_pending else txn_date,
            description=description,
            merchant_name=raw.get("merchantCategoryCode"),  # May have merchant info
            category=self._categorize_transaction(raw),
            transaction_type=transaction_type,
            reference=raw.get("endToEndId") or raw.get("transactionId"),
            counterparty_name=counterparty,
            counterparty_account=counterparty_account,
            provider=self.provider_name,
            raw_data=raw,
        )
    
    def _categorize_transaction(self, raw: Dict[str, Any]) -> TransactionCategory:
        """Categorize transaction."""
        info = (
            (raw.get("remittanceInformationUnstructured") or "") +
            (raw.get("additionalInformation") or "")
        ).lower()
        
        if "transfer" in info:
            return TransactionCategory.TRANSFER
        elif "payment" in info or "purchase" in info:
            return TransactionCategory.PAYMENT
        elif "fee" in info or "charge" in info:
            return TransactionCategory.FEE
        elif "interest" in info:
            return TransactionCategory.INTEREST
        elif "refund" in info:
            return TransactionCategory.REFUND
        else:
            return TransactionCategory.OTHER
