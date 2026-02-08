"""Base classes for bank feed integrations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class TransactionType(Enum):
    """Transaction type classification."""
    DEBIT = "debit"
    CREDIT = "credit"
    UNKNOWN = "unknown"


class TransactionCategory(Enum):
    """High-level transaction categories."""
    TRANSFER = "transfer"
    PAYMENT = "payment"
    INCOME = "income"
    FEE = "fee"
    INTEREST = "interest"
    REFUND = "refund"
    OTHER = "other"


@dataclass
class BankTransaction:
    """Normalized bank transaction from any provider."""
    
    # Core identifiers
    id: str
    account_id: str
    
    # Transaction details
    amount: Decimal
    currency: str
    transaction_date: date
    posted_date: Optional[date] = None
    
    # Description & categorization
    description: str = ""
    merchant_name: Optional[str] = None
    category: TransactionCategory = TransactionCategory.OTHER
    transaction_type: TransactionType = TransactionType.UNKNOWN
    
    # References
    reference: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_account: Optional[str] = None
    
    # Balance after transaction
    running_balance: Optional[Decimal] = None
    
    # Provider metadata
    provider: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "account_id": self.account_id,
            "amount": float(self.amount),
            "currency": self.currency,
            "transaction_date": self.transaction_date.isoformat(),
            "posted_date": self.posted_date.isoformat() if self.posted_date else None,
            "description": self.description,
            "merchant_name": self.merchant_name,
            "category": self.category.value,
            "transaction_type": self.transaction_type.value,
            "reference": self.reference,
            "counterparty_name": self.counterparty_name,
            "counterparty_account": self.counterparty_account,
            "running_balance": float(self.running_balance) if self.running_balance else None,
            "provider": self.provider,
        }


@dataclass
class BankAccount:
    """Normalized bank account from any provider."""
    
    id: str
    name: str
    institution_name: str
    
    # Account details
    account_type: str = "checking"  # checking, savings, credit, etc.
    currency: str = "USD"
    
    # Identifiers
    account_number_masked: Optional[str] = None
    iban: Optional[str] = None
    sort_code: Optional[str] = None
    
    # Balance
    current_balance: Optional[Decimal] = None
    available_balance: Optional[Decimal] = None
    
    # Provider metadata
    provider: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "institution_name": self.institution_name,
            "account_type": self.account_type,
            "currency": self.currency,
            "account_number_masked": self.account_number_masked,
            "iban": self.iban,
            "current_balance": float(self.current_balance) if self.current_balance else None,
            "available_balance": float(self.available_balance) if self.available_balance else None,
            "provider": self.provider,
        }


class BankFeedService(ABC):
    """
    Abstract base class for bank feed integrations.
    
    All providers must implement these methods to provide a consistent
    interface for fetching bank accounts and transactions.
    """
    
    provider_name: str = "unknown"
    supported_countries: List[str] = []
    
    @abstractmethod
    async def connect_account(self, authorization_code: str, **kwargs) -> Dict[str, Any]:
        """
        Connect a bank account using an authorization code from OAuth flow.
        
        Args:
            authorization_code: Code from bank's OAuth redirect
            **kwargs: Provider-specific parameters
            
        Returns:
            Connection details including access tokens and account info
        """
        pass
    
    @abstractmethod
    async def get_accounts(self, access_token: str) -> List[BankAccount]:
        """
        Get all connected bank accounts.
        
        Args:
            access_token: Valid access token for the connection
            
        Returns:
            List of BankAccount objects
        """
        pass
    
    @abstractmethod
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
            access_token: Valid access token
            account_id: Bank account ID
            from_date: Start date (defaults to 30 days ago)
            to_date: End date (defaults to today)
            limit: Maximum transactions to return
            
        Returns:
            List of BankTransaction objects
        """
        pass
    
    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh an expired access token.
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            New token details including access_token and expiry
        """
        pass
    
    @abstractmethod
    def get_authorization_url(self, redirect_uri: str, state: str, **kwargs) -> str:
        """
        Get URL to redirect user for bank authorization.
        
        Args:
            redirect_uri: Where to redirect after authorization
            state: State parameter for CSRF protection
            **kwargs: Provider-specific parameters (institution_id, etc.)
            
        Returns:
            Authorization URL
        """
        pass
    
    async def get_balance(self, access_token: str, account_id: str) -> Dict[str, Any]:
        """
        Get current balance for an account.
        
        Default implementation fetches account details. Override for efficiency.
        """
        accounts = await self.get_accounts(access_token)
        for account in accounts:
            if account.id == account_id:
                return {
                    "current_balance": float(account.current_balance) if account.current_balance else None,
                    "available_balance": float(account.available_balance) if account.available_balance else None,
                    "currency": account.currency,
                }
        return {}
    
    def is_configured(self) -> bool:
        """Check if provider credentials are configured."""
        return False
