"""
Request validation models for Clearledgr Reconciliation API.
"""
from pydantic import BaseModel, Field, validator
from datetime import datetime


class PeriodDates(BaseModel):
    """Period date range."""
    period_start: str = Field(..., description="Period start date (YYYY-MM-DD)")
    period_end: str = Field(..., description="Period end date (YYYY-MM-DD)")
    
    @validator("period_start", "period_end")
    def validate_date_format(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be in YYYY-MM-DD format")
        return v
    
    @validator("period_end")
    def validate_date_range(cls, v, values):
        if "period_start" in values:
            start = datetime.strptime(values["period_start"], "%Y-%m-%d")
            end = datetime.strptime(v, "%Y-%m-%d")
            if end < start:
                raise ValueError("period_end must be after period_start")
        return v


class SheetsRunRequest(PeriodDates):
    """Request model for Google Sheets-based Reconciliation reconciliation."""
    sheet_id: str = Field(..., description="Google Sheets ID")
    gateway_tab: str = Field("GATEWAY", description="Gateway tab name")
    bank_tab: str = Field("BANK", description="Bank tab name")
    internal_tab: str = Field("INTERNAL", description="Internal tab name")
