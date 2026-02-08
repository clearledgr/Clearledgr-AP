"""
Multi-Factor Scoring System for Clearledgr v1 (Autonomous Edition)

Implements the 100-point scoring system from product_spec_updated.md:
- Amount Match: 0-40 points
- Date Proximity: 0-30 points
- Description Similarity: 0-20 points
- Reference Match: 0-10 points

Auto-match threshold: 80+ points
Auto-draft JE threshold: 90+ points
"""
from __future__ import annotations

import re
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum


class MatchConfidence(Enum):
    """Match confidence levels based on score thresholds."""
    HIGH = "high"           # 90+ points - auto-draft JE
    MEDIUM = "medium"       # 80-89 points - auto-match
    LOW = "low"             # 60-79 points - needs review
    NO_MATCH = "no_match"   # <60 points - exception


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of match score components."""
    amount_score: float = 0.0
    date_score: float = 0.0
    description_score: float = 0.0
    reference_score: float = 0.0
    pattern_boost: float = 0.0  # From learned patterns
    
    amount_detail: str = ""
    date_detail: str = ""
    description_detail: str = ""
    reference_detail: str = ""
    
    @property
    def total_score(self) -> float:
        """Total score capped at 100."""
        return min(100.0, 
            self.amount_score + 
            self.date_score + 
            self.description_score + 
            self.reference_score +
            self.pattern_boost
        )
    
    @property
    def confidence_level(self) -> MatchConfidence:
        """Determine confidence level from total score."""
        score = self.total_score
        if score >= 90:
            return MatchConfidence.HIGH
        elif score >= 80:
            return MatchConfidence.MEDIUM
        elif score >= 60:
            return MatchConfidence.LOW
        else:
            return MatchConfidence.NO_MATCH
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_score": self.total_score,
            "confidence_level": self.confidence_level.value,
            "amount": {"score": self.amount_score, "detail": self.amount_detail},
            "date": {"score": self.date_score, "detail": self.date_detail},
            "description": {"score": self.description_score, "detail": self.description_detail},
            "reference": {"score": self.reference_score, "detail": self.reference_detail},
            "pattern_boost": self.pattern_boost,
        }


class MultiFactorScorer:
    """
    Scores transaction matches using the 100-point multi-factor system.
    
    Thresholds:
    - AUTO_MATCH_THRESHOLD (80): Auto-match transactions
    - AUTO_JE_THRESHOLD (90): Auto-generate journal entries
    """
    
    AUTO_MATCH_THRESHOLD = 80.0
    AUTO_JE_THRESHOLD = 90.0
    
    def __init__(self, pattern_service: Optional[Any] = None):
        """
        Initialize scorer with optional pattern learning service.
        
        Args:
            pattern_service: Optional PatternLearningService for boost scores
        """
        self.pattern_service = pattern_service
    
    def score_match(
        self,
        source_txn: Dict[str, Any],
        target_txn: Dict[str, Any],
        source_amount_key: str = "amount",
        target_amount_key: str = "amount",
        source_date_key: str = "date",
        target_date_key: str = "date",
        source_desc_key: str = "description",
        target_desc_key: str = "description",
        source_ref_key: str = "reference",
        target_ref_key: str = "reference",
    ) -> ScoreBreakdown:
        """
        Score a potential match between two transactions.
        
        Args:
            source_txn: Source transaction (e.g., gateway)
            target_txn: Target transaction (e.g., bank)
            *_key: Field names for each attribute
            
        Returns:
            ScoreBreakdown with detailed scoring
        """
        breakdown = ScoreBreakdown()
        
        # Amount scoring (0-40 points)
        source_amount = self._get_amount(source_txn, source_amount_key)
        target_amount = self._get_amount(target_txn, target_amount_key)
        breakdown.amount_score, breakdown.amount_detail = self._score_amount(
            source_amount, target_amount
        )
        
        # Date scoring (0-30 points)
        source_date = self._parse_date(source_txn.get(source_date_key))
        target_date = self._parse_date(target_txn.get(target_date_key))
        breakdown.date_score, breakdown.date_detail = self._score_date(
            source_date, target_date
        )
        
        # Description scoring (0-20 points)
        source_desc = str(source_txn.get(source_desc_key) or "").strip()
        target_desc = str(target_txn.get(target_desc_key) or "").strip()
        breakdown.description_score, breakdown.description_detail = self._score_description(
            source_desc, target_desc
        )
        
        # Reference scoring (0-10 points)
        source_ref = str(source_txn.get(source_ref_key) or "").strip()
        target_ref = str(target_txn.get(target_ref_key) or "").strip()
        breakdown.reference_score, breakdown.reference_detail = self._score_reference(
            source_ref, target_ref
        )
        
        # Pattern boost from learned patterns
        if self.pattern_service:
            breakdown.pattern_boost = self.pattern_service.get_boost(
                source_desc, target_desc
            )
        
        return breakdown
    
    def _get_amount(self, txn: Dict[str, Any], key: str) -> float:
        """Extract amount from transaction, handling various field names."""
        # Try primary key
        amount = txn.get(key)
        if amount is not None:
            return abs(float(amount))
        
        # Try common alternatives
        for alt_key in ["net_amount", "amount", "total", "value"]:
            if alt_key in txn and txn[alt_key] is not None:
                return abs(float(txn[alt_key]))
        
        return 0.0
    
    def _score_amount(self, amount1: float, amount2: float) -> Tuple[float, str]:
        """
        Score amount match (0-40 points).
        
        Scoring:
        - Exact match: 40 points
        - Within 0.5%: 35 points
        - Within 1%: 30 points
        - Within 2%: 20 points
        - Within 5%: 10 points
        - Otherwise: 0 points
        """
        if amount1 == 0 and amount2 == 0:
            return 40.0, "Both amounts are zero"
        
        if amount1 == 0 or amount2 == 0:
            return 0.0, "One amount is zero"
        
        diff = abs(amount1 - amount2)
        max_amount = max(amount1, amount2)
        diff_pct = (diff / max_amount) * 100
        
        if diff == 0:
            return 40.0, "Exact match"
        elif diff_pct <= 0.5:
            return 35.0, f"Within 0.5% (diff: {diff_pct:.2f}%)"
        elif diff_pct <= 1.0:
            return 30.0, f"Within 1% (diff: {diff_pct:.2f}%)"
        elif diff_pct <= 2.0:
            return 20.0, f"Within 2% (diff: {diff_pct:.2f}%)"
        elif diff_pct <= 5.0:
            return 10.0, f"Within 5% (diff: {diff_pct:.2f}%)"
        else:
            return 0.0, f"Amount difference too large ({diff_pct:.2f}%)"
    
    def _parse_date(self, date_val: Any) -> Optional[date]:
        """Parse various date formats to date object."""
        if date_val is None:
            return None
        
        if isinstance(date_val, datetime):
            return date_val.date()
        
        if isinstance(date_val, date):
            return date_val
        
        date_str = str(date_val).strip()
        if not date_str:
            return None
        
        # Try common formats
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%m-%d-%Y",
            "%m/%d/%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str[:10], fmt[:min(len(fmt), 10)]).date()
            except ValueError:
                continue
        
        return None
    
    def _score_date(self, date1: Optional[date], date2: Optional[date]) -> Tuple[float, str]:
        """
        Score date proximity (0-30 points).
        
        Scoring:
        - Same day: 30 points
        - 1 day difference: 25 points
        - 2 days: 20 points
        - 3 days: 15 points
        - 4-5 days: 10 points
        - 6-7 days: 5 points
        - Otherwise: 0 points
        """
        if date1 is None or date2 is None:
            return 0.0, "Missing date"
        
        diff_days = abs((date1 - date2).days)
        
        if diff_days == 0:
            return 30.0, "Same day"
        elif diff_days == 1:
            return 25.0, "1 day difference"
        elif diff_days == 2:
            return 20.0, "2 days difference"
        elif diff_days == 3:
            return 15.0, "3 days difference"
        elif diff_days <= 5:
            return 10.0, f"{diff_days} days difference"
        elif diff_days <= 7:
            return 5.0, f"{diff_days} days difference"
        else:
            return 0.0, f"Date difference too large ({diff_days} days)"
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    def _normalize_description(self, desc: str) -> str:
        """Normalize description for comparison."""
        # Lowercase
        desc = desc.lower()
        # Remove common noise words
        noise_words = ["payment", "transfer", "transaction", "from", "to", "ref", "reference"]
        for word in noise_words:
            desc = desc.replace(word, "")
        # Remove special characters except alphanumeric and spaces
        desc = re.sub(r"[^a-z0-9\s]", "", desc)
        # Collapse whitespace
        desc = " ".join(desc.split())
        return desc
    
    def _extract_keywords(self, desc: str) -> set:
        """Extract meaningful keywords from description."""
        normalized = self._normalize_description(desc)
        # Split and filter short words
        keywords = {w for w in normalized.split() if len(w) >= 3}
        return keywords
    
    def _score_description(self, desc1: str, desc2: str) -> Tuple[float, str]:
        """
        Score description similarity (0-20 points).
        
        Uses Levenshtein distance percentage:
        - Distance < 10%: 20 points
        - Distance < 20%: 15 points
        - Distance < 30%: 10 points
        - Contains same keywords: 5 points
        - Otherwise: 0 points
        """
        if not desc1 or not desc2:
            return 0.0, "Missing description"
        
        norm1 = self._normalize_description(desc1)
        norm2 = self._normalize_description(desc2)
        
        if not norm1 or not norm2:
            return 0.0, "Empty after normalization"
        
        # Calculate Levenshtein distance as percentage
        max_len = max(len(norm1), len(norm2))
        distance = self._levenshtein_distance(norm1, norm2)
        distance_pct = (distance / max_len) * 100
        
        if distance_pct < 10:
            return 20.0, f"Very similar (distance: {distance_pct:.1f}%)"
        elif distance_pct < 20:
            return 15.0, f"Similar (distance: {distance_pct:.1f}%)"
        elif distance_pct < 30:
            return 10.0, f"Somewhat similar (distance: {distance_pct:.1f}%)"
        
        # Check for keyword overlap
        keywords1 = self._extract_keywords(desc1)
        keywords2 = self._extract_keywords(desc2)
        common_keywords = keywords1 & keywords2
        
        if common_keywords:
            return 5.0, f"Common keywords: {', '.join(list(common_keywords)[:3])}"
        
        return 0.0, "Descriptions too different"
    
    def _score_reference(self, ref1: str, ref2: str) -> Tuple[float, str]:
        """
        Score reference/ID match (0-10 points).
        
        Scoring:
        - Exact match: 10 points
        - One contains the other: 7 points
        - Partial overlap (>50%): 5 points
        - Otherwise: 0 points
        """
        if not ref1 or not ref2:
            return 0.0, "Missing reference"
        
        # Normalize references
        norm1 = re.sub(r"[^a-z0-9]", "", ref1.lower())
        norm2 = re.sub(r"[^a-z0-9]", "", ref2.lower())
        
        if not norm1 or not norm2:
            return 0.0, "Empty after normalization"
        
        # Exact match
        if norm1 == norm2:
            return 10.0, "Exact reference match"
        
        # One contains the other
        if norm1 in norm2 or norm2 in norm1:
            return 7.0, "Reference contained in other"
        
        # Check for significant overlap
        shorter = norm1 if len(norm1) < len(norm2) else norm2
        longer = norm2 if len(norm1) < len(norm2) else norm1
        
        # Find longest common substring
        for length in range(len(shorter), len(shorter) // 2, -1):
            for start in range(len(shorter) - length + 1):
                substring = shorter[start:start + length]
                if substring in longer:
                    overlap_pct = (length / len(shorter)) * 100
                    if overlap_pct > 50:
                        return 5.0, f"Partial match ({overlap_pct:.0f}% overlap)"
        
        return 0.0, "No reference match"
    
    def is_auto_match(self, score: ScoreBreakdown) -> bool:
        """Check if score meets auto-match threshold (80+)."""
        return score.total_score >= self.AUTO_MATCH_THRESHOLD
    
    def is_auto_je(self, score: ScoreBreakdown) -> bool:
        """Check if score meets auto-JE threshold (90+)."""
        return score.total_score >= self.AUTO_JE_THRESHOLD
    
    def find_best_matches(
        self,
        source_txns: List[Dict[str, Any]],
        target_txns: List[Dict[str, Any]],
        source_amount_key: str = "net_amount",
        target_amount_key: str = "amount",
        min_score: float = 60.0,
    ) -> List[Tuple[int, int, ScoreBreakdown]]:
        """
        Find best matches between source and target transactions.
        
        Returns list of (source_idx, target_idx, score) tuples sorted by score.
        Only includes matches above min_score.
        """
        matches = []
        
        for i, source in enumerate(source_txns):
            for j, target in enumerate(target_txns):
                score = self.score_match(
                    source, target,
                    source_amount_key=source_amount_key,
                    target_amount_key=target_amount_key,
                )
                if score.total_score >= min_score:
                    matches.append((i, j, score))
        
        # Sort by score descending
        matches.sort(key=lambda x: x[2].total_score, reverse=True)
        
        return matches
    
    def optimal_matching(
        self,
        source_txns: List[Dict[str, Any]],
        target_txns: List[Dict[str, Any]],
        source_amount_key: str = "net_amount",
        target_amount_key: str = "amount",
    ) -> Dict[str, Any]:
        """
        Perform optimal 1:1 matching using greedy assignment.
        
        Returns:
            {
                "matches": [(source_idx, target_idx, score), ...],
                "unmatched_sources": [idx, ...],
                "unmatched_targets": [idx, ...],
                "auto_match_count": int,
                "auto_je_count": int,
            }
        """
        all_scores = self.find_best_matches(
            source_txns, target_txns,
            source_amount_key=source_amount_key,
            target_amount_key=target_amount_key,
            min_score=60.0,
        )
        
        matched_sources = set()
        matched_targets = set()
        final_matches = []
        auto_match_count = 0
        auto_je_count = 0
        
        # Greedy assignment by highest score first
        for source_idx, target_idx, score in all_scores:
            if source_idx in matched_sources or target_idx in matched_targets:
                continue
            
            if score.total_score >= self.AUTO_MATCH_THRESHOLD:
                final_matches.append((source_idx, target_idx, score))
                matched_sources.add(source_idx)
                matched_targets.add(target_idx)
                auto_match_count += 1
                if score.total_score >= self.AUTO_JE_THRESHOLD:
                    auto_je_count += 1
        
        unmatched_sources = [i for i in range(len(source_txns)) if i not in matched_sources]
        unmatched_targets = [i for i in range(len(target_txns)) if i not in matched_targets]
        
        return {
            "matches": final_matches,
            "unmatched_sources": unmatched_sources,
            "unmatched_targets": unmatched_targets,
            "auto_match_count": auto_match_count,
            "auto_je_count": auto_je_count,
            "match_rate": len(final_matches) / max(len(source_txns), 1) * 100,
        }
