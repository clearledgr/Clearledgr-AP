"""
Pattern Learning Service for Clearledgr v1 (Autonomous Edition)

Implements the learning loop from product_spec_updated.md:
- Store user corrections in CLMATCHPATTERNS
- Apply learned patterns to future reconciliations
- Boost confidence scores based on pattern history

CLMATCHPATTERNS columns:
- pattern_id: Unique identifier
- gateway_pattern: Regex for gateway description
- bank_pattern: Regex for bank description
- match_rule: Custom matching logic
- times_applied: Usage count
- confidence_boost: Score increase (0-20 points)
- last_used: Last application timestamp
- created_by: User who created
- created_at: Creation timestamp
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

from clearledgr.services.db import DB


DB_PATH = os.getenv("CLEARLEDGR_STATE_DB", os.path.join(os.getcwd(), "state.sqlite3"))


@dataclass
class MatchPattern:
    """Learned matching pattern from user corrections."""
    pattern_id: str
    gateway_pattern: str
    bank_pattern: str
    match_rule: Optional[str] = None
    times_applied: int = 0
    confidence_boost: float = 15.0  # Default boost
    last_used: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    
    def matches_gateway(self, description: str) -> bool:
        """Check if gateway description matches pattern."""
        if not self.gateway_pattern or not description:
            return False
        try:
            return bool(re.search(self.gateway_pattern, description, re.IGNORECASE))
        except re.error:
            # Fallback to substring match if regex is invalid
            return self.gateway_pattern.lower() in description.lower()
    
    def matches_bank(self, description: str) -> bool:
        """Check if bank description matches pattern."""
        if not self.bank_pattern or not description:
            return False
        try:
            return bool(re.search(self.bank_pattern, description, re.IGNORECASE))
        except re.error:
            return self.bank_pattern.lower() in description.lower()
    
    def matches(self, gateway_desc: str, bank_desc: str) -> bool:
        """Check if both descriptions match their patterns."""
        return self.matches_gateway(gateway_desc) and self.matches_bank(bank_desc)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "gateway_pattern": self.gateway_pattern,
            "bank_pattern": self.bank_pattern,
            "match_rule": self.match_rule,
            "times_applied": self.times_applied,
            "confidence_boost": self.confidence_boost,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
    
    def to_sheets_row(self) -> List[Any]:
        """Convert to row format for CLMATCHPATTERNS sheet."""
        return [
            self.pattern_id,
            self.gateway_pattern,
            self.bank_pattern,
            self.match_rule or "",
            self.times_applied,
            self.confidence_boost,
            self.last_used.isoformat() if self.last_used else "",
            self.created_by or "",
            self.created_at.isoformat() if self.created_at else "",
        ]


class PatternLearningService:
    """
    Service for learning and applying match patterns.
    
    Patterns are stored in SQLite and can be synced to/from Google Sheets.
    """
    
    # Common patterns from product_spec_updated.md examples
    DEFAULT_PATTERNS = [
        MatchPattern(
            pattern_id="default_stripe",
            gateway_pattern=r"STRIPE.*PMT.*",
            bank_pattern=r"STRIPE PAYMENT.*",
            confidence_boost=15.0,
            times_applied=0,
        ),
        MatchPattern(
            pattern_id="default_amazon",
            gateway_pattern=r"AMZN Mktp.*",
            bank_pattern=r"AMAZON MARKETPLACE.*",
            confidence_boost=18.0,
            times_applied=0,
        ),
        MatchPattern(
            pattern_id="default_flutterwave",
            gateway_pattern=r"FLW-.*",
            bank_pattern=r"FLUTTERWAVE.*",
            confidence_boost=20.0,
            times_applied=0,
        ),
        MatchPattern(
            pattern_id="default_paypal",
            gateway_pattern=r"PAYPAL.*",
            bank_pattern=r"PAYPAL.*|PP\*.*",
            confidence_boost=15.0,
            times_applied=0,
        ),
    ]
    
    def __init__(self, db_path: str = DB_PATH):
        self.db = DB(sqlite_path=db_path)
        self._ensure_table()
        self._ensure_default_patterns()
    
    def _ensure_table(self) -> None:
        """Create patterns table if not exists."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS cl_match_patterns (
                pattern_id TEXT PRIMARY KEY,
                gateway_pattern TEXT NOT NULL,
                bank_pattern TEXT NOT NULL,
                match_rule TEXT,
                times_applied INTEGER DEFAULT 0,
                confidence_boost REAL DEFAULT 15.0,
                last_used TEXT,
                created_by TEXT,
                created_at TEXT
            )
        """)
    
    def _ensure_default_patterns(self) -> None:
        """Insert default patterns if table is empty."""
        count = self.db.fetchone("SELECT COUNT(*) FROM cl_match_patterns")
        if count and count[0] == 0:
            for pattern in self.DEFAULT_PATTERNS:
                self._save_pattern(pattern)
    
    def _save_pattern(self, pattern: MatchPattern) -> None:
        """Save or update a pattern in the database."""
        self.db.execute("""
            INSERT INTO cl_match_patterns 
            (pattern_id, gateway_pattern, bank_pattern, match_rule, times_applied, 
             confidence_boost, last_used, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pattern_id) DO UPDATE SET
                gateway_pattern = excluded.gateway_pattern,
                bank_pattern = excluded.bank_pattern,
                match_rule = excluded.match_rule,
                times_applied = excluded.times_applied,
                confidence_boost = excluded.confidence_boost,
                last_used = excluded.last_used,
                created_by = excluded.created_by,
                created_at = excluded.created_at
        """, (
            pattern.pattern_id,
            pattern.gateway_pattern,
            pattern.bank_pattern,
            pattern.match_rule,
            pattern.times_applied,
            pattern.confidence_boost,
            pattern.last_used.isoformat() if pattern.last_used else None,
            pattern.created_by,
            pattern.created_at.isoformat() if pattern.created_at else None,
        ))
    
    def store_pattern(
        self,
        gateway_description: str,
        bank_description: str,
        created_by: Optional[str] = None,
        confidence_boost: float = 15.0,
    ) -> MatchPattern:
        """
        Store a new pattern from a user-confirmed match.
        
        Converts descriptions to regex patterns for future matching.
        
        Args:
            gateway_description: Gateway transaction description
            bank_description: Bank transaction description
            created_by: User who created the pattern
            confidence_boost: Score boost for this pattern (0-20)
            
        Returns:
            Created MatchPattern
        """
        # Generate regex patterns from descriptions
        gateway_pattern = self._description_to_pattern(gateway_description)
        bank_pattern = self._description_to_pattern(bank_description)
        
        # Check if similar pattern already exists
        existing = self._find_similar_pattern(gateway_pattern, bank_pattern)
        if existing:
            # Increment usage and update boost
            existing.times_applied += 1
            existing.confidence_boost = min(20.0, existing.confidence_boost + 0.5)
            existing.last_used = datetime.utcnow()
            self._save_pattern(existing)
            return existing
        
        # Create new pattern
        pattern = MatchPattern(
            pattern_id=f"learned_{uuid.uuid4().hex[:8]}",
            gateway_pattern=gateway_pattern,
            bank_pattern=bank_pattern,
            times_applied=1,
            confidence_boost=min(20.0, confidence_boost),
            last_used=datetime.utcnow(),
            created_by=created_by,
            created_at=datetime.utcnow(),
        )
        
        self._save_pattern(pattern)
        return pattern
    
    def _description_to_pattern(self, description: str) -> str:
        """
        Convert a transaction description to a regex pattern.
        
        Extracts stable parts (vendor names, codes) while allowing
        variable parts (dates, amounts, transaction IDs).
        """
        if not description:
            return ""
        
        # Normalize
        desc = description.strip()
        
        # Replace common variable parts with wildcards
        # Transaction IDs (sequences of numbers)
        pattern = re.sub(r'\d{6,}', r'\\d+', desc)
        # Dates in various formats
        pattern = re.sub(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', r'\\d+[/-]\\d+[/-]\\d+', pattern)
        # Amounts with decimals
        pattern = re.sub(r'\d+\.\d{2}', r'\\d+\\.\\d+', pattern)
        # Escape regex special chars (except those we've already added)
        pattern = re.sub(r'([.+?^${}()|[\]\\])', r'\\\1', pattern)
        # Replace escaped backslash-d with actual \d
        pattern = pattern.replace('\\\\d', '\\d')
        
        # Add wildcards for flexibility
        pattern = pattern.replace(' ', '.*')
        
        # Ensure pattern starts matching at word boundary
        if not pattern.startswith('.*'):
            pattern = '.*' + pattern
        if not pattern.endswith('.*'):
            pattern = pattern + '.*'
        
        return pattern
    
    def _find_similar_pattern(
        self, gateway_pattern: str, bank_pattern: str
    ) -> Optional[MatchPattern]:
        """Find existing pattern with similar patterns."""
        patterns = self.list_patterns()
        for p in patterns:
            # Simple similarity check: same core keywords
            gw_core = re.sub(r'[.*\\]+', '', gateway_pattern).lower()
            bank_core = re.sub(r'[.*\\]+', '', bank_pattern).lower()
            p_gw_core = re.sub(r'[.*\\]+', '', p.gateway_pattern).lower()
            p_bank_core = re.sub(r'[.*\\]+', '', p.bank_pattern).lower()
            
            if gw_core and p_gw_core and gw_core in p_gw_core or p_gw_core in gw_core:
                if bank_core and p_bank_core and bank_core in p_bank_core or p_bank_core in bank_core:
                    return p
        
        return None
    
    def list_patterns(self, limit: int = 100) -> List[MatchPattern]:
        """List all patterns ordered by usage."""
        rows = self.db.fetchall("""
            SELECT pattern_id, gateway_pattern, bank_pattern, match_rule,
                   times_applied, confidence_boost, last_used, created_by, created_at
            FROM cl_match_patterns
            ORDER BY times_applied DESC
            LIMIT ?
        """, (limit,))
        
        patterns = []
        for row in rows:
            patterns.append(MatchPattern(
                pattern_id=row[0],
                gateway_pattern=row[1],
                bank_pattern=row[2],
                match_rule=row[3],
                times_applied=row[4] or 0,
                confidence_boost=row[5] or 15.0,
                last_used=datetime.fromisoformat(row[6]) if row[6] else None,
                created_by=row[7],
                created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            ))
        
        return patterns
    
    def get_boost(
        self, gateway_desc: str, bank_desc: str
    ) -> float:
        """
        Get confidence boost for a potential match based on learned patterns.
        
        Args:
            gateway_desc: Gateway transaction description
            bank_desc: Bank transaction description
            
        Returns:
            Confidence boost (0-20 points) if pattern matches, 0 otherwise
        """
        patterns = self.list_patterns()
        
        for pattern in patterns:
            if pattern.matches(gateway_desc, bank_desc):
                # Update usage stats
                pattern.times_applied += 1
                pattern.last_used = datetime.utcnow()
                self._save_pattern(pattern)
                return pattern.confidence_boost
        
        return 0.0
    
    def apply_patterns(
        self, transactions: List[Dict[str, Any]]
    ) -> List[Tuple[int, int, float]]:
        """
        Apply patterns to find potential matches between transactions.
        
        Useful for suggesting matches before multi-factor scoring.
        
        Args:
            transactions: List of transactions with 'description' and 'source' fields
            
        Returns:
            List of (idx1, idx2, boost) tuples for matching pairs
        """
        patterns = self.list_patterns()
        suggestions = []
        
        gateway_txns = [(i, t) for i, t in enumerate(transactions) 
                        if t.get("source") == "gateway"]
        bank_txns = [(i, t) for i, t in enumerate(transactions) 
                     if t.get("source") == "bank"]
        
        for gw_idx, gw_txn in gateway_txns:
            gw_desc = gw_txn.get("description", "")
            for bank_idx, bank_txn in bank_txns:
                bank_desc = bank_txn.get("description", "")
                for pattern in patterns:
                    if pattern.matches(gw_desc, bank_desc):
                        suggestions.append((gw_idx, bank_idx, pattern.confidence_boost))
                        break
        
        return suggestions
    
    def delete_pattern(self, pattern_id: str) -> bool:
        """Delete a pattern by ID."""
        self.db.execute(
            "DELETE FROM cl_match_patterns WHERE pattern_id = ?",
            (pattern_id,)
        )
        return True
    
    def export_for_sheets(self) -> List[List[Any]]:
        """
        Export patterns for CLMATCHPATTERNS sheet.
        
        Returns header row + data rows.
        """
        headers = [
            "pattern_id", "gateway_pattern", "bank_pattern", "match_rule",
            "times_applied", "confidence_boost", "last_used", "created_by", "created_at"
        ]
        
        patterns = self.list_patterns()
        rows = [headers]
        rows.extend([p.to_sheets_row() for p in patterns])
        
        return rows
    
    def import_from_sheets(self, rows: List[List[Any]]) -> int:
        """
        Import patterns from CLMATCHPATTERNS sheet data.
        
        Args:
            rows: Sheet data (first row is header, rest is data)
            
        Returns:
            Number of patterns imported
        """
        if len(rows) < 2:
            return 0
        
        # Skip header row
        data_rows = rows[1:]
        count = 0
        
        for row in data_rows:
            if len(row) < 3:
                continue
            
            pattern = MatchPattern(
                pattern_id=str(row[0]) if row[0] else f"imported_{uuid.uuid4().hex[:8]}",
                gateway_pattern=str(row[1]) if len(row) > 1 else "",
                bank_pattern=str(row[2]) if len(row) > 2 else "",
                match_rule=str(row[3]) if len(row) > 3 and row[3] else None,
                times_applied=int(row[4]) if len(row) > 4 and row[4] else 0,
                confidence_boost=float(row[5]) if len(row) > 5 and row[5] else 15.0,
                last_used=datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None,
                created_by=str(row[7]) if len(row) > 7 and row[7] else None,
                created_at=datetime.fromisoformat(row[8]) if len(row) > 8 and row[8] else None,
            )
            
            self._save_pattern(pattern)
            count += 1
        
        return count
