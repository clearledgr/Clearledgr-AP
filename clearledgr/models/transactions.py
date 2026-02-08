"""Transaction models for reconciliation."""
from datetime import date
from typing import Any, Dict, Optional
from pydantic import Field, field_validator
from clearledgr.models.base import CLBaseModel


class Money(CLBaseModel):
    amount: float = Field(..., ge=0)
    currency: str = Field(default="EUR", min_length=1, max_length=10)


class TransactionBase(CLBaseModel):
    transaction_id: str = Field(..., min_length=1)
    transaction_date: date
    description: Optional[str] = None
    counterparty: Optional[str] = None
    amount: Money
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("transaction_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return value.strip()


class BankTransaction(TransactionBase):
    source: str = Field(default="bank", min_length=1)


class GLTransaction(TransactionBase):
    gl_account_code: Optional[str] = None
    gl_account_name: Optional[str] = None
    source: str = Field(default="gl", min_length=1)
