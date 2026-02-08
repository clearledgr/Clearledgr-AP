from datetime import datetime
from typing import List, Dict, Optional

from clearledgr.models.base import CLBaseModel


class DraftJournalEntry(CLBaseModel):
    """
    Draft journal entry generated from reconciliation matches.
    """

    entry_id: str
    date: datetime
    description: str
    debits: List[Dict]
    credits: List[Dict]
    confidence: float
    match_id: Optional[str] = None
    status: str = "DRAFT"  # DRAFT / APPROVED / POSTED
    created_at: datetime = datetime.utcnow()

