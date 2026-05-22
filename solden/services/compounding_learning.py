"""
Compounding Learning System

Every user action improves the system. This service:
1. Records user corrections and feedback
2. Generalizes patterns from corrections
3. Updates confidence models
4. Tracks improvement over time

The goal: Solden gets smarter with every interaction.

Tenancy: all state is org-scoped. Patterns and corrections persist in Postgres
(``learning_patterns`` / ``learning_corrections``, via the LearningStore mixin),
and the in-memory cache is partitioned per organization. One org's learned
patterns never surface in another org's reasoning. Every public method requires
``organization_id`` and fails loud if it is missing.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from solden.core.org_utils import assert_org_id

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
    last_used: Optional[datetime]
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

    All persistence is Postgres-backed and org-scoped; the in-memory pattern
    cache is keyed by organization_id so lookups can never cross tenants.
    """

    _CACHE_REFRESH_INTERVAL: int = 300  # 5-minute refresh interval

    def __init__(self) -> None:
        # Per-org caches for fast lookup: {org_id: {pattern_id: LearnedPattern}}
        self._pattern_cache: Dict[str, Dict[str, LearnedPattern]] = {}
        self._last_refresh: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Persistence + cache helpers
    # ------------------------------------------------------------------ #
    def _db(self):
        from solden.core.database import get_db
        return get_db()

    def _cache_for(self, organization_id: str) -> Dict[str, LearnedPattern]:
        """Return the org's pattern cache, loading from Postgres if stale/absent."""
        now = time.time()
        last = self._last_refresh.get(organization_id, 0.0)
        if (
            organization_id not in self._pattern_cache
            or (now - last) > self._CACHE_REFRESH_INTERVAL
        ):
            self._load_patterns_to_cache(organization_id)
        return self._pattern_cache.get(organization_id, {})

    def _load_patterns_to_cache(self, organization_id: str) -> None:
        """Load one org's patterns from Postgres into its cache slice."""
        self._last_refresh[organization_id] = time.time()
        cache: Dict[str, LearnedPattern] = {}
        try:
            rows = self._db().list_learning_patterns(organization_id, min_confidence=0.3)
        except Exception as exc:
            logger.warning(
                "Could not load learned patterns for org %s: %s", organization_id, exc
            )
            rows = []
        for row in rows:
            last_used = row.get("last_used")
            cache[row["pattern_id"]] = LearnedPattern(
                pattern_id=row["pattern_id"],
                pattern_type=row["pattern_type"],
                pattern_data=row.get("pattern_data") or {},
                confidence=float(row.get("confidence") or 0.0),
                usage_count=int(row.get("usage_count") or 0),
                success_count=int(row.get("success_count") or 0),
                last_used=datetime.fromisoformat(last_used) if last_used else None,
                created_from=row.get("created_from") or [],
            )
        self._pattern_cache[organization_id] = cache

    def _persist_pattern(self, organization_id: str, pattern: LearnedPattern) -> None:
        """Upsert a pattern to Postgres (scoped to the org)."""
        self._db().save_learning_pattern(organization_id, {
            "pattern_id": pattern.pattern_id,
            "pattern_type": pattern.pattern_type,
            "pattern_data": pattern.pattern_data,
            "confidence": pattern.confidence,
            "usage_count": pattern.usage_count,
            "success_count": pattern.success_count,
            "last_used": pattern.last_used.isoformat() if pattern.last_used else None,
            "created_from": pattern.created_from,
        })

    # ------------------------------------------------------------------ #
    # Recording corrections + learning
    # ------------------------------------------------------------------ #
    def record_correction(
        self,
        organization_id: str,
        correction_type: str,
        original_value: Dict[str, Any],
        corrected_value: Dict[str, Any],
        user_email: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Correction:
        """
        Record a user correction and trigger learning.

        Args:
            organization_id: Owning org (required — corrections are tenant-scoped)
            correction_type: Type of correction (match, categorization, routing)
            original_value: What the system decided
            corrected_value: What the user corrected to
            user_email: User who made correction
            context: Additional context (transaction IDs, etc.)

        Returns:
            The recorded Correction
        """
        org = assert_org_id(organization_id, context="record_correction")
        correction_id = (
            f"corr_{datetime.now(timezone.utc).timestamp():.0f}_{uuid.uuid4().hex[:8]}"
        )

        correction = Correction(
            correction_id=correction_id,
            correction_type=correction_type,
            original_value=original_value,
            corrected_value=corrected_value,
            user_email=user_email,
            timestamp=datetime.now(timezone.utc),
            context=context or {},
        )

        self._db().save_learning_correction(org, {
            "correction_id": correction.correction_id,
            "correction_type": correction.correction_type,
            "original_value": correction.original_value,
            "corrected_value": correction.corrected_value,
            "user_email": correction.user_email,
            "context": correction.context,
            "created_at": correction.timestamp.isoformat(),
        })

        # Trigger pattern learning
        self._learn_from_correction(org, correction)

        logger.info("Recorded correction %s: %s", correction_id, correction_type)

        return correction

    def _learn_from_correction(
        self,
        organization_id: str,
        correction: Correction,
    ) -> Optional[LearnedPattern]:
        """Learn a pattern from a correction (strategy depends on type)."""
        if correction.correction_type == "match":
            return self._learn_match_pattern(organization_id, correction)
        elif correction.correction_type == "categorization":
            return self._learn_categorization_pattern(organization_id, correction)
        elif correction.correction_type == "routing":
            return self._learn_routing_pattern(organization_id, correction)
        return None

    def _learn_match_pattern(
        self,
        organization_id: str,
        correction: Correction,
    ) -> Optional[LearnedPattern]:
        """Learn a matching pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value

        source_desc = (original.get("source_description") or "").lower()
        target_desc = (corrected.get("matched_description") or "").lower()

        if not source_desc or not target_desc:
            return None

        source_tokens = set(source_desc.split())
        target_tokens = set(target_desc.split())

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

        existing = self._find_similar_pattern(organization_id, pattern_id, "match", pattern_data)
        if existing:
            self._reinforce_pattern(organization_id, existing.pattern_id, correction.correction_id)
            return existing

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

        self._persist_pattern(organization_id, pattern)
        self._cache_for(organization_id)[pattern.pattern_id] = pattern

        logger.info("Learned new match pattern: %s", pattern_id)
        return pattern

    def _learn_categorization_pattern(
        self,
        organization_id: str,
        correction: Correction,
    ) -> Optional[LearnedPattern]:
        """Learn a categorization pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value
        context = correction.context

        vendor = (context.get("vendor") or "").lower()
        description = (context.get("description") or "").lower()
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

        existing = self._find_similar_pattern(organization_id, pattern_id, "categorization", pattern_data)
        if existing:
            self._reinforce_pattern(organization_id, existing.pattern_id, correction.correction_id)
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

        self._persist_pattern(organization_id, pattern)
        self._cache_for(organization_id)[pattern.pattern_id] = pattern

        logger.info("Learned categorization pattern: %s", pattern_id)
        return pattern

    def _learn_routing_pattern(
        self,
        organization_id: str,
        correction: Correction,
    ) -> Optional[LearnedPattern]:
        """Learn an exception routing pattern from correction."""
        original = correction.original_value
        corrected = correction.corrected_value
        context = correction.context

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

        existing = self._find_similar_pattern(organization_id, pattern_id, "routing", pattern_data)
        if existing:
            self._reinforce_pattern(organization_id, existing.pattern_id, correction.correction_id)
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

        self._persist_pattern(organization_id, pattern)
        self._cache_for(organization_id)[pattern.pattern_id] = pattern

        return pattern

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from text."""
        words = text.lower().split()
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "for", "to", "from", "in", "on"}
        keywords = [w for w in words if len(w) > 2 and w not in stop_words]
        return keywords[:10]

    def _calculate_amount_tolerance(self, original: Dict, corrected: Dict) -> float:
        """Calculate amount tolerance from correction."""
        orig_amt = float(original.get("amount", 0) or 0)
        corr_amt = float(corrected.get("amount", 0) or 0)
        if orig_amt == 0:
            return 0.05  # Default 5%
        diff_pct = abs(orig_amt - corr_amt) / orig_amt
        return min(diff_pct + 0.01, 0.10)  # Max 10%

    def _calculate_date_tolerance(self, original: Dict, corrected: Dict) -> int:
        """Calculate date tolerance from correction."""
        return 3  # Default to 3 days if not calculable

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
        organization_id: str,
        pattern_id: str,
        pattern_type: str,
        pattern_data: Dict,
    ) -> Optional[LearnedPattern]:
        """Find an existing similar pattern within the org."""
        cache = self._cache_for(organization_id)
        if pattern_id in cache:
            return cache[pattern_id]

        for existing in cache.values():
            if existing.pattern_type != pattern_type:
                continue
            existing_keywords = set(existing.pattern_data.get("keywords", []))
            new_keywords = set(pattern_data.get("keywords", []))
            if existing_keywords and new_keywords:
                overlap = len(existing_keywords & new_keywords) / len(existing_keywords | new_keywords)
                if overlap > 0.5:
                    return existing
        return None

    def _reinforce_pattern(
        self,
        organization_id: str,
        pattern_id: str,
        correction_id: str,
    ) -> None:
        """Reinforce an existing pattern with a new correction."""
        cache = self._cache_for(organization_id)
        if pattern_id not in cache:
            return

        pattern = cache[pattern_id]
        pattern.created_from.append(correction_id)

        confidence_boost = 0.05 * (1 - pattern.confidence)
        pattern.confidence = min(1.0, max(0.0, min(0.95, pattern.confidence + confidence_boost)))

        try:
            self._persist_pattern(organization_id, pattern)
        except Exception as exc:
            logger.error("Failed to reinforce pattern %s in DB: %s", pattern_id, exc)
            cache.pop(pattern_id, None)  # invalidate the stale in-memory entry
            return

        logger.info("Reinforced pattern %s, confidence: %.0f%%", pattern_id, pattern.confidence * 100)

    # ------------------------------------------------------------------ #
    # Reading learned patterns
    # ------------------------------------------------------------------ #
    def get_patterns_for_matching(
        self,
        organization_id: str,
        source_description: str,
        min_confidence: float = 0.5,
    ) -> List[LearnedPattern]:
        """Get relevant patterns for transaction matching (org-scoped)."""
        org = assert_org_id(organization_id, context="get_patterns_for_matching")
        relevant = []
        source_keywords = set(source_description.lower().split())

        for pattern in self._cache_for(org).values():
            if pattern.pattern_type != "match":
                continue
            if pattern.confidence < min_confidence:
                continue
            pattern_keywords = set(pattern.pattern_data.get("source_keywords", []))
            if pattern_keywords & source_keywords:
                relevant.append(pattern)

        return sorted(relevant, key=lambda p: p.confidence, reverse=True)

    def get_categorization_hint(
        self,
        organization_id: str,
        vendor: str,
        description: str,
    ) -> Optional[Dict[str, Any]]:
        """Get learned categorization hint for vendor/description (org-scoped)."""
        org = assert_org_id(organization_id, context="get_categorization_hint")
        vendor_lower = vendor.lower()
        desc_lower = description.lower()

        best_match = None
        best_score = 0.0

        for pattern in self._cache_for(org).values():
            if pattern.pattern_type != "categorization":
                continue

            data = pattern.pattern_data
            score = 0.0

            for kw in data.get("vendor_keywords", []):
                if kw in vendor_lower:
                    score += 0.3

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
        organization_id: str,
        pattern_id: str,
        was_successful: bool,
    ) -> None:
        """Record that a pattern was used (for tracking accuracy), org-scoped."""
        org = assert_org_id(organization_id, context="record_pattern_usage")
        cache = self._cache_for(org)
        if pattern_id not in cache:
            return

        pattern = cache[pattern_id]
        pattern.usage_count += 1
        if was_successful:
            pattern.success_count += 1
        pattern.last_used = datetime.now(timezone.utc)

        if pattern.usage_count >= 5:
            new_confidence = 0.5 + (pattern.success_rate * 0.45)
            pattern.confidence = min(1.0, max(0.0, new_confidence))

        self._persist_pattern(org, pattern)

    def get_learning_metrics(self, organization_id: str) -> LearningMetrics:
        """Get overall learning metrics for one org."""
        org = assert_org_id(organization_id, context="get_learning_metrics")
        metrics = self._db().learning_metrics(org)
        return LearningMetrics(
            total_corrections=int(metrics.get("total_corrections") or 0),
            patterns_learned=int(metrics.get("patterns_learned") or 0),
            accuracy_after=float(metrics.get("accuracy") or 0.0),
        )


# Singleton instance
_learning_service: Optional[CompoundingLearningService] = None


def get_learning_service() -> CompoundingLearningService:
    """Get the learning service singleton."""
    global _learning_service
    if _learning_service is None:
        _learning_service = CompoundingLearningService()
    return _learning_service
