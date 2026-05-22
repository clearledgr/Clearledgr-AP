"""Learning service for Solden.

Learns from user actions to improve:
- Vendor → GL code mappings
- Invoice categorization
- Confidence scoring
- Auto-approval thresholds

This is the "feedback loop" that makes Solden smarter over time.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from solden.core.org_utils import assert_org_id

logger = logging.getLogger(__name__)


@dataclass
class VendorPattern:
    """Learned pattern for a vendor."""
    vendor_name: str
    gl_code: str
    gl_description: str
    occurrence_count: int = 1
    total_amount: float = 0.0
    avg_amount: float = 0.0
    currency: str = "USD"
    last_used: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 0.5
    
    def update(self, amount: float):
        """Update pattern with new occurrence."""
        self.occurrence_count += 1
        self.total_amount += amount
        self.avg_amount = self.total_amount / self.occurrence_count
        self.last_used = datetime.now(timezone.utc)
        # Confidence increases with more occurrences
        self.confidence = min(0.99, 0.5 + (self.occurrence_count * 0.05))


class LearningService:
    """
    Learns from user actions to improve predictions.
    
    Tracks:
    - Vendor → GL code mappings (most common mapping per vendor)
    - Amount-based patterns (different GL for different amounts)
    - User corrections (learn from overrides)
    
    Storage:
    - Postgres-backed (LearningStore mixin), org-scoped: tables
      ``learning_vendor_patterns`` + ``learning_org_stats``.
    - In-memory write-through cache for fast reads, reloaded from Postgres on a
      TTL so the learning survives deploys and stays consistent across workers.
    """

    _CACHE_REFRESH_INTERVAL: int = 300  # 5-minute refresh window

    def __init__(self, organization_id: str):
        self.organization_id = assert_org_id(
            organization_id, context="LearningService"
        )

        # Vendor → GL patterns (most common mapping wins)
        # Key: normalized vendor name, Value: dict of gl_code → VendorPattern
        self.vendor_patterns: Dict[str, Dict[str, VendorPattern]] = defaultdict(dict)

        # Statistics (cumulative counters, persisted per-org)
        self.stats = {
            "total_learned": 0,
            "corrections_received": 0,
            "auto_approved_count": 0,
            "accuracy_rate": 0.0,
        }

        self._loaded = False
        self._last_refresh = 0.0

    # ------------------------------------------------------------------ #
    # Persistence + cache helpers
    # ------------------------------------------------------------------ #
    def _db(self):
        from solden.core.database import get_db
        return get_db()

    def _ensure_loaded(self) -> None:
        """Load (or TTL-refresh) this org's patterns + stats from Postgres."""
        now = time.time()
        if self._loaded and (now - self._last_refresh) <= self._CACHE_REFRESH_INTERVAL:
            return
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Destructive reload of this org's cache from Postgres.

        Invariant: every in-memory mutator (currently only ``record_approval`` /
        ``import_patterns``) MUST write-through to Postgres before returning,
        because this method fully replaces ``self.vendor_patterns``. An
        un-persisted in-memory write would be silently dropped on the next
        TTL refresh.
        """
        load_ok = True
        vendor_patterns: Dict[str, Dict[str, VendorPattern]] = defaultdict(dict)
        try:
            rows = self._db().list_vendor_gl_patterns(self.organization_id)
        except Exception as exc:
            logger.warning(
                "Could not load vendor patterns for org %s: %s", self.organization_id, exc
            )
            rows = []
            load_ok = False
        for row in rows:
            last_used = row.get("last_used")
            vendor_patterns[row["vendor_normalized"]][row["gl_code"]] = VendorPattern(
                vendor_name=row.get("vendor_name") or "",
                gl_code=row["gl_code"],
                gl_description=row.get("gl_description") or "",
                occurrence_count=int(row.get("occurrence_count") or 0),
                total_amount=float(row.get("total_amount") or 0.0),
                avg_amount=float(row.get("avg_amount") or 0.0),
                currency=row.get("currency") or "USD",
                last_used=datetime.fromisoformat(last_used) if last_used else datetime.now(timezone.utc),
                confidence=float(row.get("confidence") or 0.5),
            )
        self.vendor_patterns = vendor_patterns
        try:
            self.stats.update(self._db().get_learning_org_stats(self.organization_id))
        except Exception as exc:
            logger.warning(
                "Could not load learning stats for org %s: %s", self.organization_id, exc
            )
            load_ok = False
        self._loaded = True
        # On a failed/partial load, don't hold the empty state for the full TTL —
        # leave _last_refresh at 0 so the next call retries instead of serving
        # stale-empty (and degraded suggestions) for 5 minutes after a DB blip.
        self._last_refresh = time.time() if load_ok else 0.0

    def _persist_pattern(self, normalized: str, pattern: VendorPattern) -> None:
        self._db().save_vendor_gl_pattern(self.organization_id, {
            "vendor_normalized": normalized,
            "gl_code": pattern.gl_code,
            "vendor_name": pattern.vendor_name,
            "gl_description": pattern.gl_description,
            "occurrence_count": pattern.occurrence_count,
            "total_amount": pattern.total_amount,
            "avg_amount": pattern.avg_amount,
            "currency": pattern.currency,
            "last_used": pattern.last_used.isoformat() if pattern.last_used else None,
            "confidence": pattern.confidence,
        })

    def _persist_stats(self) -> None:
        self._db().save_learning_org_stats(self.organization_id, {
            "total_learned": self.stats.get("total_learned", 0),
            "corrections_received": self.stats.get("corrections_received", 0),
            "auto_approved_count": self.stats.get("auto_approved_count", 0),
        })

    def record_approval(
        self,
        vendor: str,
        gl_code: str,
        gl_description: str,
        amount: float,
        currency: str = "USD",
        was_auto_approved: bool = False,
        was_corrected: bool = False,
        original_suggestion: Optional[str] = None,
    ) -> None:
        """
        Record an approved invoice to learn from.
        
        Called when user approves an invoice (or system auto-approves).
        """
        self._ensure_loaded()
        normalized = self._normalize_vendor(vendor)

        # Get or create pattern for this vendor/GL combo
        if gl_code not in self.vendor_patterns[normalized]:
            self.vendor_patterns[normalized][gl_code] = VendorPattern(
                vendor_name=vendor,
                gl_code=gl_code,
                gl_description=gl_description,
                currency=currency,
            )

        pattern = self.vendor_patterns[normalized][gl_code]
        pattern.update(amount)

        # Track corrections
        if was_corrected and original_suggestion:
            self.stats["corrections_received"] += 1

            # Learn from correction: boost confidence for the corrected GL
            pattern.confidence = min(0.99, pattern.confidence + 0.1)

            # Decrease confidence for the wrongly suggested GL
            if original_suggestion in self.vendor_patterns[normalized]:
                orig_pattern = self.vendor_patterns[normalized][original_suggestion]
                orig_pattern.confidence = max(0.1, orig_pattern.confidence - 0.1)
                self._persist_pattern(normalized, orig_pattern)

        # Update statistics
        self.stats["total_learned"] += 1
        if was_auto_approved:
            self.stats["auto_approved_count"] += 1

        # Write through to Postgres so the learning survives deploys.
        self._persist_pattern(normalized, pattern)
        self._persist_stats()

        logger.info(f"Learned: {vendor} → GL {gl_code} (confidence: {pattern.confidence:.2f})")
    
    def suggest_gl_code(
        self, 
        vendor: str, 
        amount: Optional[float] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Suggest GL code based on learned patterns.
        
        Returns:
            Dict with gl_code, gl_description, confidence, source
            or None if no pattern found
        """
        self._ensure_loaded()
        normalized = self._normalize_vendor(vendor)

        # Check vendor patterns
        if normalized in self.vendor_patterns:
            patterns = self.vendor_patterns[normalized]
            
            if patterns:
                # Find best pattern (highest confidence * occurrence_count)
                best = max(
                    patterns.values(),
                    key=lambda p: p.confidence * min(p.occurrence_count, 10)  # Cap at 10 for recency
                )
                
                return {
                    "gl_code": best.gl_code,
                    "gl_description": best.gl_description,
                    "confidence": best.confidence,
                    "source": "vendor_history",
                    "occurrence_count": best.occurrence_count,
                    "avg_amount": best.avg_amount,
                    "alternatives": [
                        {
                            "gl_code": p.gl_code,
                            "gl_description": p.gl_description,
                            "confidence": p.confidence,
                            "count": p.occurrence_count,
                        }
                        for code, p in patterns.items()
                        if code != best.gl_code
                    ][:3],
                }

        # No pattern found
        return None

    def get_vendor_pattern(self, vendor: str) -> Optional[Dict[str, Any]]:
        """Best learned pattern for a vendor, shaped for the reasoning layer.

        Returns the highest-confidence vendor->GL pattern as a dict, or None if
        the vendor is unknown. ``typical_amount`` maps to the pattern's avg_amount
        so the reasoning layer's vendor_context (amount-variance factor + risk)
        can use it. Previously this method did not exist, so callers
        (finance_learning, agent_reasoning) silently got None via a swallowed
        AttributeError and that path never fired.
        """
        self._ensure_loaded()
        normalized = self._normalize_vendor(vendor)
        patterns = self.vendor_patterns.get(normalized)
        if not patterns:
            return None

        best = max(
            patterns.values(),
            key=lambda p: p.confidence * min(p.occurrence_count, 10),
        )
        return {
            "vendor": best.vendor_name,
            "gl_code": best.gl_code,
            "gl_description": best.gl_description,
            "confidence": best.confidence,
            "occurrence_count": best.occurrence_count,
            "typical_amount": best.avg_amount,
            "currency": best.currency,
            "last_used": best.last_used.isoformat() if best.last_used else None,
        }

    def get_vendor_history(self, vendor: str) -> Dict[str, Any]:
        """Get full history for a vendor."""
        self._ensure_loaded()
        normalized = self._normalize_vendor(vendor)
        
        if normalized not in self.vendor_patterns:
            return {"vendor": vendor, "patterns": [], "total_invoices": 0}
        
        patterns = self.vendor_patterns[normalized]
        total = sum(p.occurrence_count for p in patterns.values())
        
        return {
            "vendor": vendor,
            "normalized": normalized,
            "total_invoices": total,
            "patterns": [
                {
                    "gl_code": p.gl_code,
                    "gl_description": p.gl_description,
                    "occurrence_count": p.occurrence_count,
                    "total_amount": p.total_amount,
                    "avg_amount": p.avg_amount,
                    "currency": p.currency,
                    "confidence": p.confidence,
                    "last_used": p.last_used.isoformat(),
                }
                for p in sorted(patterns.values(), key=lambda x: -x.occurrence_count)
            ],
        }
    
    def get_all_patterns(self) -> List[Dict[str, Any]]:
        """Get all learned vendor patterns."""
        self._ensure_loaded()
        all_patterns = []
        
        for vendor_key, patterns in self.vendor_patterns.items():
            for gl_code, pattern in patterns.items():
                all_patterns.append({
                    "vendor": pattern.vendor_name,
                    "vendor_normalized": vendor_key,
                    "gl_code": pattern.gl_code,
                    "gl_description": pattern.gl_description,
                    "occurrence_count": pattern.occurrence_count,
                    "avg_amount": pattern.avg_amount,
                    "confidence": pattern.confidence,
                    "last_used": pattern.last_used.isoformat(),
                })
        
        # Sort by occurrence count
        all_patterns.sort(key=lambda x: -x["occurrence_count"])
        return all_patterns
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get learning statistics."""
        self._ensure_loaded()
        total_vendors = len(self.vendor_patterns)
        total_patterns = sum(len(p) for p in self.vendor_patterns.values())
        
        # Calculate accuracy from corrections
        if self.stats["total_learned"] > 0:
            accuracy = 1 - (self.stats["corrections_received"] / self.stats["total_learned"])
        else:
            accuracy = 0.0
        
        return {
            **self.stats,
            "total_vendors": total_vendors,
            "total_patterns": total_patterns,
            "accuracy_rate": round(accuracy, 3),
            "organization_id": self.organization_id,
        }
    
    def export_patterns(self) -> str:
        """Export patterns as JSON for backup/transfer."""
        self._ensure_loaded()
        data = {
            "organization_id": self.organization_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "vendor_patterns": {},
            "amount_patterns": [],
            "statistics": self.get_statistics(),
        }
        
        for vendor_key, patterns in self.vendor_patterns.items():
            data["vendor_patterns"][vendor_key] = [
                {
                    "vendor_name": p.vendor_name,
                    "gl_code": p.gl_code,
                    "gl_description": p.gl_description,
                    "occurrence_count": p.occurrence_count,
                    "total_amount": p.total_amount,
                    "avg_amount": p.avg_amount,
                    "currency": p.currency,
                    "confidence": p.confidence,
                    "last_used": p.last_used.isoformat(),
                }
                for p in patterns.values()
            ]
        
        return json.dumps(data, indent=2)
    
    def import_patterns(self, json_data: str) -> int:
        """
        Import patterns from JSON.
        
        Returns count of patterns imported.
        """
        self._ensure_loaded()
        data = json.loads(json_data)
        count = 0

        for vendor_key, patterns in data.get("vendor_patterns", {}).items():
            for p in patterns:
                pattern = VendorPattern(
                    vendor_name=p["vendor_name"],
                    gl_code=p["gl_code"],
                    gl_description=p["gl_description"],
                    occurrence_count=p["occurrence_count"],
                    total_amount=p["total_amount"],
                    avg_amount=p["avg_amount"],
                    currency=p.get("currency", "USD"),
                    last_used=datetime.fromisoformat(p["last_used"]),
                    confidence=p["confidence"],
                )
                self.vendor_patterns[vendor_key][p["gl_code"]] = pattern
                self._persist_pattern(vendor_key, pattern)
                count += 1

        logger.info(f"Imported {count} patterns")
        return count
    
    def _normalize_vendor(self, vendor: str) -> str:
        """Normalize vendor name for consistent matching."""
        if not vendor:
            return ""
        
        # Lowercase
        normalized = vendor.lower().strip()
        
        # Remove common suffixes
        for suffix in [" inc", " inc.", " llc", " ltd", " ltd.", " co", " co.", " corp", " corp."]:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        
        # Remove special characters but keep spaces
        normalized = "".join(c if c.isalnum() or c.isspace() else "" for c in normalized)
        
        # Collapse multiple spaces
        normalized = " ".join(normalized.split())
        
        return normalized


# Singleton per organization
_learning_services: Dict[str, LearningService] = {}


def get_learning_service(organization_id: str) -> LearningService:
    """Get or create learning service for an organization."""
    organization_id = assert_org_id(
        organization_id, context="get_learning_service"
    )
    if organization_id not in _learning_services:
        _learning_services[organization_id] = LearningService(organization_id)
    return _learning_services[organization_id]
