from datetime import datetime
from typing import Optional

from clearledgr.models.base import CLBaseModel


class MatchPattern(CLBaseModel):
    """
    Learned matching patterns captured from user corrections.
    """

    pattern_id: str
    gateway_pattern: str
    bank_pattern: str
    confidence: float = 0.75
    match_count: int = 0
    last_used: Optional[datetime] = None
    last_updated: Optional[datetime] = None

