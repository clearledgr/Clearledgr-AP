"""Simple pattern store for learned reconciliation patterns."""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from clearledgr.models.patterns import MatchPattern
from clearledgr.services.db import DB


DB_PATH = os.getenv("CLEARLEDGR_STATE_DB", os.path.join(os.getcwd(), "state.sqlite3"))


class PatternStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db = DB(sqlite_path=db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS cl_match_patterns (
                pattern_id TEXT PRIMARY KEY,
                gateway_pattern TEXT,
                bank_pattern TEXT,
                confidence REAL,
                match_count INTEGER,
                last_used TEXT,
                last_updated TEXT
            )
            """
        )

    def upsert(self, pattern: MatchPattern) -> None:
        self.db.execute(
            """
            INSERT INTO cl_match_patterns (pattern_id, gateway_pattern, bank_pattern, confidence, match_count, last_used, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pattern_id) DO UPDATE SET
                gateway_pattern=excluded.gateway_pattern,
                bank_pattern=excluded.bank_pattern,
                confidence=excluded.confidence,
                match_count=excluded.match_count,
                last_used=excluded.last_used,
                last_updated=excluded.last_updated
            """,
            (
                pattern.pattern_id,
                pattern.gateway_pattern,
                pattern.bank_pattern,
                pattern.confidence,
                pattern.match_count,
                pattern.last_used.isoformat() if pattern.last_used else None,
                datetime.utcnow().isoformat(),
            ),
        )

    def list(self) -> List[MatchPattern]:
        rows = self.db.fetchall(
            "SELECT pattern_id, gateway_pattern, bank_pattern, confidence, match_count, last_used, last_updated FROM cl_match_patterns"
        )

        patterns: List[MatchPattern] = []
        for row in rows:
            last_used = datetime.fromisoformat(row[5]) if row[5] else None
            last_updated = datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None
            patterns.append(
                MatchPattern(
                    pattern_id=row[0],
                    gateway_pattern=row[1],
                    bank_pattern=row[2],
                    confidence=row[3],
                    match_count=row[4],
                    last_used=last_used,
                    last_updated=last_updated,
                )
            )
        return patterns

    def increment_usage(self, pattern_id: str) -> None:
        self.db.execute(
            """
            UPDATE cl_match_patterns
            SET match_count = match_count + 1,
                last_used = ?
            WHERE pattern_id = ?
            """,
            (datetime.utcnow().isoformat(), pattern_id),
        )
