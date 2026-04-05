"""Confidence calibration — verify Claude's self-reported scores.

When Claude says "I'm 89% confident about this due date," we need to know
if that actually means 89%. If Claude consistently over-reports confidence,
we adjust downward. If it under-reports, we adjust upward.

Calibration works by comparing Claude's reported confidence to operator
correction rates per field per vendor.

Example: If Claude reports 90% confidence on vendor name for Google invoices,
but operators correct the vendor name 30% of the time, the calibrated
confidence is ~70%, not 90%.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConfidenceCalibrator:
    """Calibrate extraction confidence based on actual correction rates."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def calibrate(
        self,
        vendor_name: str,
        field_confidences: Dict[str, float],
    ) -> Dict[str, float]:
        """Adjust field confidences based on historical correction rates.

        Returns calibrated confidence dict with same keys.
        """
        if not field_confidences or not vendor_name:
            return field_confidences or {}

        profile = self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        correction_rates = meta.get("field_correction_rates") or {}
        if not correction_rates:
            return field_confidences

        calibrated = {}
        for field, reported_conf in field_confidences.items():
            rate = correction_rates.get(field)
            if rate is not None and isinstance(rate, (int, float)):
                # If 20% of extractions for this field get corrected,
                # the real accuracy is at most 80%, regardless of what Claude reports
                max_calibrated = 1.0 - float(rate)
                calibrated[field] = min(float(reported_conf), max_calibrated)
            else:
                calibrated[field] = float(reported_conf)

        return calibrated

    def record_correction(self, vendor_name: str, field: str) -> None:
        """Record that a field was corrected, updating the correction rate."""
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        # Track per-field: total extractions and corrections
        stats = meta.get("field_extraction_stats") or {}
        field_stats = stats.get(field) or {"total": 0, "corrected": 0}
        field_stats["corrected"] = field_stats.get("corrected", 0) + 1
        stats[field] = field_stats
        meta["field_extraction_stats"] = stats

        # Recompute correction rate
        rates = meta.get("field_correction_rates") or {}
        total = field_stats.get("total", 0)
        corrected = field_stats.get("corrected", 0)
        if total > 0:
            rates[field] = round(corrected / total, 3)
        meta["field_correction_rates"] = rates

        self.db.upsert_vendor_profile(self.organization_id, vendor_name, metadata=meta)

    def record_extraction(self, vendor_name: str, fields: list) -> None:
        """Record that fields were extracted (whether corrected or not)."""
        profile = self.db.get_vendor_profile(self.organization_id, vendor_name) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        stats = meta.get("field_extraction_stats") or {}
        for field in fields:
            field_stats = stats.get(field) or {"total": 0, "corrected": 0}
            field_stats["total"] = field_stats.get("total", 0) + 1
            stats[field] = field_stats
        meta["field_extraction_stats"] = stats

        # Recompute correction rates
        rates = {}
        for field, fs in stats.items():
            total = fs.get("total", 0)
            corrected = fs.get("corrected", 0)
            if total > 0:
                rates[field] = round(corrected / total, 3)
        meta["field_correction_rates"] = rates

        self.db.upsert_vendor_profile(self.organization_id, vendor_name, metadata=meta)


def get_confidence_calibrator(organization_id: str = "default") -> ConfidenceCalibrator:
    return ConfidenceCalibrator(organization_id=organization_id)
