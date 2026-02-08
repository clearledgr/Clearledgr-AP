"""Learning service for Clearledgr.

Learns from user actions to improve:
- Vendor → GL code mappings
- Invoice categorization
- Confidence scoring
- Auto-approval thresholds

This is the "feedback loop" that makes Clearledgr smarter over time.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
    last_used: datetime = field(default_factory=datetime.utcnow)
    confidence: float = 0.5
    
    def update(self, amount: float):
        """Update pattern with new occurrence."""
        self.occurrence_count += 1
        self.total_amount += amount
        self.avg_amount = self.total_amount / self.occurrence_count
        self.last_used = datetime.utcnow()
        # Confidence increases with more occurrences
        self.confidence = min(0.99, 0.5 + (self.occurrence_count * 0.05))


@dataclass
class AmountPattern:
    """Learned pattern for amount ranges."""
    vendor_name: str
    min_amount: float
    max_amount: float
    gl_code: str
    occurrence_count: int = 1


class LearningService:
    """
    Learns from user actions to improve predictions.
    
    Tracks:
    - Vendor → GL code mappings (most common mapping per vendor)
    - Amount-based patterns (different GL for different amounts)
    - User corrections (learn from overrides)
    
    Storage:
    - In-memory for fast access
    - Backed by database for persistence
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        
        # Vendor → GL patterns (most common mapping wins)
        # Key: normalized vendor name, Value: dict of gl_code → VendorPattern
        self.vendor_patterns: Dict[str, Dict[str, VendorPattern]] = defaultdict(dict)
        
        # Amount-based patterns (e.g., "Stripe > $1000 → Payment Processing Large")
        self.amount_patterns: List[AmountPattern] = []
        
        # Recent corrections (for detecting systematic errors)
        self.corrections: List[Dict[str, Any]] = []
        
        # Statistics
        self.stats = {
            "total_learned": 0,
            "corrections_received": 0,
            "auto_approved_count": 0,
            "accuracy_rate": 0.0,
        }
    
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
            self.corrections.append({
                "vendor": vendor,
                "original_gl": original_suggestion,
                "corrected_gl": gl_code,
                "amount": amount,
                "timestamp": datetime.utcnow().isoformat(),
            })
            self.stats["corrections_received"] += 1
            
            # Learn from correction: boost confidence for the corrected GL
            pattern.confidence = min(0.99, pattern.confidence + 0.1)
            
            # Decrease confidence for the wrongly suggested GL
            if original_suggestion in self.vendor_patterns[normalized]:
                orig_pattern = self.vendor_patterns[normalized][original_suggestion]
                orig_pattern.confidence = max(0.1, orig_pattern.confidence - 0.1)
        
        # Update statistics
        self.stats["total_learned"] += 1
        if was_auto_approved:
            self.stats["auto_approved_count"] += 1
        
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
        
        # Check amount-based patterns
        if amount and self.amount_patterns:
            for pattern in self.amount_patterns:
                if (self._normalize_vendor(pattern.vendor_name) == normalized and
                    pattern.min_amount <= amount <= pattern.max_amount):
                    return {
                        "gl_code": pattern.gl_code,
                        "confidence": 0.7,
                        "source": "amount_range",
                    }
        
        # No pattern found
        return None
    
    def get_vendor_history(self, vendor: str) -> Dict[str, Any]:
        """Get full history for a vendor."""
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
        data = {
            "organization_id": self.organization_id,
            "exported_at": datetime.utcnow().isoformat(),
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
        data = json.loads(json_data)
        count = 0
        
        for vendor_key, patterns in data.get("vendor_patterns", {}).items():
            for p in patterns:
                self.vendor_patterns[vendor_key][p["gl_code"]] = VendorPattern(
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


def get_learning_service(organization_id: str = "default") -> LearningService:
    """Get or create learning service for an organization."""
    if organization_id not in _learning_services:
        _learning_services[organization_id] = LearningService(organization_id)
    return _learning_services[organization_id]
