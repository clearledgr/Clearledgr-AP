"""
Compounding Learning System

Every user action improves the system. This service:
1. Records user corrections and feedback
2. Generalizes patterns from corrections
3. Updates confidence models
4. Tracks improvement over time

The goal: Clearledgr gets smarter with every interaction.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import os

logger = logging.getLogger(__name__)


@dataclass
class Correction:
    """A user correction to an agent decision."""
    correction_id: str
    correction_type: str  # match, categorization, routing, approval
    original_value: Dict[str, Any]
    corrected_value: Dict[str, Any]
    user_email: str
    timestamp: datetime
    context: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "correction_type": self.correction_type,
            "original_value": self.original_value,
            "corrected_value": self.corrected_value,
            "user_email": self.user_email,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
        }


@dataclass
class LearnedPattern:
    """A pattern learned from corrections."""
    pattern_id: str
    pattern_type: str
    pattern_data: Dict[str, Any]
    confidence: float
    usage_count: int
    success_count: int
    last_used: datetime
    created_from: List[str]  # correction_ids that formed this pattern
    
    @property
    def success_rate(self) -> float:
        if self.usage_count == 0:
            return 0.0
        return self.success_count / self.usage_count


@dataclass
class LearningMetrics:
    """Metrics tracking learning progress."""
    total_corrections: int = 0
    patterns_learned: int = 0
    accuracy_before: float = 0.0
    accuracy_after: float = 0.0
    improvement_rate: float = 0.0
    by_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class CompoundingLearningService:
    """
    Service that learns from user corrections and improves over time.
    
    Learning loop:
    1. Agent makes decision
    2. User corrects if wrong
    3. System records correction
    4. System generalizes pattern
    5. Next time, agent uses learned pattern
    6. Confidence increases with successful use
    """
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(
            os.path.dirname(__file__), "..", "state", "learning.db"
        )
        self._init_db()
        
        # In-memory caches for fast lookup
        self._pattern_cache: Dict[str, LearnedPattern] = {}
        self._load_patterns_to_cache()
    
    def _init_db(self) -> None:
        """Initialize the learning database."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS corrections (
                    correction_id TEXT PRIMARY KEY,
                    correction_type TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    corrected_value TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    context TEXT,
                    organization_id TEXT
                );
                
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    pattern_data TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    last_used TEXT,
                    created_from TEXT,
                    organization_id TEXT
                );
                
                CREATE TABLE IF NOT EXISTS learning_metrics (
                    metric_date TEXT PRIMARY KEY,
                    total_corrections INTEGER,
                    patterns_learned INTEGER,
                    accuracy_rate REAL,
                    by_type TEXT
                );
                
                CREATE INDEX IF NOT EXISTS idx_corrections_type 
                ON corrections(correction_type);
                
                CREATE INDEX IF NOT EXISTS idx_patterns_type 
                ON learned_patterns(pattern_type);
            """)
    
    def _load_patterns_to_cache(self) -> None:
        """Load patterns into memory cache."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT pattern_id, pattern_type, pattern_data, confidence,
                       usage_count, success_count, last_used, created_from
                FROM learned_patterns
                WHERE confidence > 0.3
            """)
            
            for row in cursor.fetchall():
                pattern = LearnedPattern(
                    pattern_id=row[0],
                    pattern_type=row[1],
                    pattern_data=json.loads(row[2]),
                    confidence=row[3],
                    usage_count=row[4],
                    success_count=row[5],
                    last_used=datetime.fromisoformat(row[6]) if row[6] else None,
                    created_from=json.loads(row[7]) if row[7] else [],
                )
                self._pattern_cache[pattern.pattern_id] = pattern
    
    def record_correction(
        self,
        correction_type: str,
        original_value: Dict[str, Any],
        corrected_value: Dict[str, Any],
        user_email: str,
        context: Optional[Dict[str, Any]] = None,
        organization_id: Optional[str] = None,
    ) -> Correction:
        """
        Record a user correction and trigger learning.
        
        Args:
            correction_type: Type of correction (match, categorization, routing)
            original_value: What the system decided
            corrected_value: What the user corrected to
            user_email: User who made correction
            context: Additional context (transaction IDs, etc.)
            organization_id: Organization for multi-tenancy
        
        Returns:
            The recorded Correction
        """
        correction_id = f"corr_{datetime.now(timezone.utc).timestamp():.0f}"
        
        correction = Correction(
            correction_id=correction_id,
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            user_email=user_email,
            timestamp=datetime.now(timezone.utc),
            context=context or {},
        )
        
        # Store in database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO corrections 
                (correction_id, correction_type, original_value, corrected_value,
                 user_email, timestamp, context, organization_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                correction.correction_id,
                correction.correction_type,
                json.dumps(correction.original_value),
                json.dumps(correction.corrected_value),
                correction.user_email,
                correction.timestamp.isoformat(),
                json.dumps(correction.context),
                organization_id,
            ))
        
        # Trigger pattern learning
        self._learn_from_correction(correction, organization_id)
        
        logger.info(f"Recorded correction {correction_id}: {correction_type}")
        
        return correction
    
    def _learn_from_correction(
        self, 
        correction: Correction,
        organization_id: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """
        Learn a pattern from a correction.
        
        Different learning strategies for different correction types.
        """
        if correction.correction_type == "match":
            return self._learn_match_pattern(correction, organization_id)
        elif correction.correction_type == "categorization":
            return self._learn_categorization_pattern(correction, organization_id)
        elif correction.correction_type == "routing":
            return self._learn_routing_pattern(correction, organization_id)
        
        return None
    
    def _learn_match_pattern(
        self,
        correction: Correction,
        organization_id: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """Learn a matching pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value
        
        # Extract pattern components
        source_desc = (original.get("source_description") or "").lower()
        target_desc = (corrected.get("matched_description") or "").lower()
        
        if not source_desc or not target_desc:
            return None
        
        # Create pattern: extract key tokens
        source_tokens = set(source_desc.split())
        target_tokens = set(target_desc.split())
        
        # Find distinctive tokens (present in one but not both)
        source_distinctive = source_tokens - target_tokens
        target_distinctive = target_tokens - source_tokens
        common_tokens = source_tokens & target_tokens
        
        pattern_data = {
            "source_keywords": list(source_distinctive)[:5],
            "target_keywords": list(target_distinctive)[:5],
            "common_keywords": list(common_tokens)[:5],
            "amount_tolerance": self._calculate_amount_tolerance(original, corrected),
            "date_tolerance_days": self._calculate_date_tolerance(original, corrected),
        }
        
        pattern_id = f"match_{hash(json.dumps(pattern_data, sort_keys=True)) % 1000000}"
        
        # Check if similar pattern exists
        existing = self._find_similar_pattern(pattern_id, "match", pattern_data)
        
        if existing:
            # Reinforce existing pattern
            self._reinforce_pattern(existing.pattern_id, correction.correction_id)
            return existing
        
        # Create new pattern
        pattern = LearnedPattern(
            pattern_id=pattern_id,
            pattern_type="match",
            pattern_data=pattern_data,
            confidence=0.6,  # Start with moderate confidence
            usage_count=0,
            success_count=0,
            last_used=None,
            created_from=[correction.correction_id],
        )
        
        self._save_pattern(pattern, organization_id)
        self._pattern_cache[pattern.pattern_id] = pattern
        
        logger.info(f"Learned new match pattern: {pattern_id}")
        
        return pattern
    
    def _learn_categorization_pattern(
        self,
        correction: Correction,
        organization_id: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """Learn a categorization pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value
        context = correction.context
        
        # Extract what led to wrong categorization
        vendor = context.get("vendor", "").lower()
        description = context.get("description", "").lower()
        correct_gl = corrected.get("gl_code")
        
        if not correct_gl:
            return None
        
        pattern_data = {
            "vendor_keywords": self._extract_keywords(vendor),
            "description_keywords": self._extract_keywords(description),
            "correct_gl_code": correct_gl,
            "correct_gl_name": corrected.get("gl_name"),
            "wrong_gl_code": original.get("gl_code"),
        }
        
        pattern_id = f"cat_{vendor[:10]}_{correct_gl}"
        
        existing = self._find_similar_pattern(pattern_id, "categorization", pattern_data)
        
        if existing:
            self._reinforce_pattern(existing.pattern_id, correction.correction_id)
            return existing
        
        pattern = LearnedPattern(
            pattern_id=pattern_id,
            pattern_type="categorization",
            pattern_data=pattern_data,
            confidence=0.7,
            usage_count=0,
            success_count=0,
            last_used=None,
            created_from=[correction.correction_id],
        )
        
        self._save_pattern(pattern, organization_id)
        self._pattern_cache[pattern.pattern_id] = pattern
        
        logger.info(f"Learned categorization pattern: {pattern_id}")
        
        return pattern
    
    def _learn_routing_pattern(
        self,
        correction: Correction,
        organization_id: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """Learn an exception routing pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value
        context = correction.context
        
        # What exception type went to wrong person?
        exception_type = context.get("exception_type", "")
        exception_amount = context.get("amount", 0)
        correct_assignee = corrected.get("assignee")
        
        if not correct_assignee:
            return None
        
        pattern_data = {
            "exception_type": exception_type,
            "amount_range": self._get_amount_range(exception_amount),
            "keywords": self._extract_keywords(context.get("description", "")),
            "correct_assignee": correct_assignee,
            "wrong_assignee": original.get("assignee"),
        }
        
        pattern_id = f"route_{exception_type}_{correct_assignee[:10]}"
        
        existing = self._find_similar_pattern(pattern_id, "routing", pattern_data)
        
        if existing:
            self._reinforce_pattern(existing.pattern_id, correction.correction_id)
            return existing
        
        pattern = LearnedPattern(
            pattern_id=pattern_id,
            pattern_type="routing",
            pattern_data=pattern_data,
            confidence=0.65,
            usage_count=0,
            success_count=0,
            last_used=None,
            created_from=[correction.correction_id],
        )
        
        self._save_pattern(pattern, organization_id)
        self._pattern_cache[pattern.pattern_id] = pattern
        
        return pattern
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from text."""
        words = text.lower().split()
        # Filter out common words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "for", "to", "from", "in", "on"}
        keywords = [w for w in words if len(w) > 2 and w not in stop_words]
        return keywords[:10]
    
    def _calculate_amount_tolerance(
        self, original: Dict, corrected: Dict
    ) -> float:
        """Calculate amount tolerance from correction."""
        orig_amt = float(original.get("amount", 0) or 0)
        corr_amt = float(corrected.get("amount", 0) or 0)
        
        if orig_amt == 0:
            return 0.05  # Default 5%
        
        diff_pct = abs(orig_amt - corr_amt) / orig_amt
        return min(diff_pct + 0.01, 0.10)  # Max 10%
    
    def _calculate_date_tolerance(
        self, original: Dict, corrected: Dict
    ) -> int:
        """Calculate date tolerance from correction."""
        # Default to 3 days if not calculable
        return 3
    
    def _get_amount_range(self, amount: float) -> str:
        """Categorize amount into range."""
        if amount < 1000:
            return "small"
        elif amount < 10000:
            return "medium"
        elif amount < 50000:
            return "large"
        return "very_large"
    
    def _find_similar_pattern(
        self,
        pattern_id: str,
        pattern_type: str,
        pattern_data: Dict,
    ) -> Optional[LearnedPattern]:
        """Find an existing similar pattern."""
        # Check exact match first
        if pattern_id in self._pattern_cache:
            return self._pattern_cache[pattern_id]
        
        # Check for similar patterns
        for existing in self._pattern_cache.values():
            if existing.pattern_type != pattern_type:
                continue
            
            # Simple similarity check based on shared keywords
            existing_keywords = set(existing.pattern_data.get("keywords", []))
            new_keywords = set(pattern_data.get("keywords", []))
            
            if existing_keywords and new_keywords:
                overlap = len(existing_keywords & new_keywords) / len(existing_keywords | new_keywords)
                if overlap > 0.5:
                    return existing
        
        return None
    
    def _reinforce_pattern(self, pattern_id: str, correction_id: str) -> None:
        """Reinforce an existing pattern with new correction."""
        if pattern_id not in self._pattern_cache:
            return
        
        pattern = self._pattern_cache[pattern_id]
        pattern.created_from.append(correction_id)
        
        # Increase confidence (diminishing returns)
        confidence_boost = 0.05 * (1 - pattern.confidence)
        pattern.confidence = min(0.95, pattern.confidence + confidence_boost)
        
        # Update in database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE learned_patterns 
                SET confidence = ?, created_from = ?
                WHERE pattern_id = ?
            """, (
                pattern.confidence,
                json.dumps(pattern.created_from),
                pattern_id,
            ))
        
        logger.info(f"Reinforced pattern {pattern_id}, confidence: {pattern.confidence:.0%}")
    
    def _save_pattern(
        self, 
        pattern: LearnedPattern,
        organization_id: Optional[str] = None,
    ) -> None:
        """Save a pattern to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO learned_patterns
                (pattern_id, pattern_type, pattern_data, confidence,
                 usage_count, success_count, last_used, created_from, organization_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pattern.pattern_id,
                pattern.pattern_type,
                json.dumps(pattern.pattern_data),
                pattern.confidence,
                pattern.usage_count,
                pattern.success_count,
                pattern.last_used.isoformat() if pattern.last_used else None,
                json.dumps(pattern.created_from),
                organization_id,
            ))
    
    def get_patterns_for_matching(
        self,
        source_description: str,
        min_confidence: float = 0.5,
    ) -> List[LearnedPattern]:
        """Get relevant patterns for transaction matching."""
        relevant = []
        source_keywords = set(source_description.lower().split())
        
        for pattern in self._pattern_cache.values():
            if pattern.pattern_type != "match":
                continue
            if pattern.confidence < min_confidence:
                continue
            
            # Check keyword overlap
            pattern_keywords = set(pattern.pattern_data.get("source_keywords", []))
            if pattern_keywords & source_keywords:
                relevant.append(pattern)
        
        return sorted(relevant, key=lambda p: p.confidence, reverse=True)
    
    def get_categorization_hint(
        self,
        vendor: str,
        description: str,
    ) -> Optional[Dict[str, Any]]:
        """Get learned categorization hint for vendor/description."""
        vendor_lower = vendor.lower()
        desc_lower = description.lower()
        
        best_match = None
        best_score = 0.0
        
        for pattern in self._pattern_cache.values():
            if pattern.pattern_type != "categorization":
                continue
            
            data = pattern.pattern_data
            score = 0.0
            
            # Check vendor keywords
            for kw in data.get("vendor_keywords", []):
                if kw in vendor_lower:
                    score += 0.3
            
            # Check description keywords
            for kw in data.get("description_keywords", []):
                if kw in desc_lower:
                    score += 0.2
            
            score *= pattern.confidence
            
            if score > best_score:
                best_score = score
                best_match = {
                    "gl_code": data.get("correct_gl_code"),
                    "gl_name": data.get("correct_gl_name"),
                    "confidence": score,
                    "pattern_id": pattern.pattern_id,
                }
        
        return best_match if best_score > 0.3 else None
    
    def record_pattern_usage(
        self,
        pattern_id: str,
        was_successful: bool,
    ) -> None:
        """Record that a pattern was used (for tracking accuracy)."""
        if pattern_id not in self._pattern_cache:
            return
        
        pattern = self._pattern_cache[pattern_id]
        pattern.usage_count += 1
        if was_successful:
            pattern.success_count += 1
        pattern.last_used = datetime.now(timezone.utc)
        
        # Adjust confidence based on success rate
        if pattern.usage_count >= 5:
            new_confidence = 0.5 + (pattern.success_rate * 0.45)
            pattern.confidence = new_confidence
        
        # Update database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE learned_patterns
                SET usage_count = ?, success_count = ?, last_used = ?, confidence = ?
                WHERE pattern_id = ?
            """, (
                pattern.usage_count,
                pattern.success_count,
                pattern.last_used.isoformat(),
                pattern.confidence,
                pattern_id,
            ))
    
    def get_learning_metrics(self) -> LearningMetrics:
        """Get overall learning metrics."""
        with sqlite3.connect(self.db_path) as conn:
            # Count corrections
            total_corrections = conn.execute(
                "SELECT COUNT(*) FROM corrections"
            ).fetchone()[0]
            
            # Count patterns
            patterns_learned = conn.execute(
                "SELECT COUNT(*) FROM learned_patterns WHERE confidence > 0.5"
            ).fetchone()[0]
            
            # Calculate accuracy (success rate of patterns)
            accuracy_result = conn.execute("""
                SELECT 
                    SUM(success_count) * 1.0 / NULLIF(SUM(usage_count), 0)
                FROM learned_patterns
                WHERE usage_count > 0
            """).fetchone()
            accuracy = accuracy_result[0] or 0.0
        
        return LearningMetrics(
            total_corrections=total_corrections,
            patterns_learned=patterns_learned,
            accuracy_after=accuracy,
        )


# Singleton instance
_learning_service: Optional[CompoundingLearningService] = None


def get_learning_service() -> CompoundingLearningService:
    """Get the learning service singleton."""
    global _learning_service
    if _learning_service is None:
        _learning_service = CompoundingLearningService()
    return _learning_service
