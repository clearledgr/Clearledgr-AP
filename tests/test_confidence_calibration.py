"""Tests for confidence calibration — adjusts Claude's self-reported scores
based on historical operator correction rates.

Uses a tmp_path DB fixture. No real API calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services.confidence_calibration import (  # noqa: E402
    ConfidenceCalibrator,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "confidence-cal.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    _db = db_module.get_db()
    _db.initialize()
    _db.create_organization("default", "Default", settings={})
    return _db


@pytest.fixture()
def calibrator(db):
    return ConfidenceCalibrator(organization_id="default")


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


class TestCalibrate:
    def test_returns_original_confidence_when_no_history(self, calibrator):
        """Without correction history, calibration returns the input unchanged."""
        confidences = {"vendor": 0.95, "amount": 0.98, "invoice_number": 0.90}
        result = calibrator.calibrate("New Vendor", confidences)
        assert result == confidences

    def test_reduces_confidence_based_on_correction_rate(self, calibrator, db):
        """If a field is corrected 30% of the time, max calibrated confidence is 0.7."""
        db.upsert_vendor_profile(
            "default", "Acme Corp",
            metadata={
                "field_correction_rates": {"vendor": 0.3, "amount": 0.1},
            },
        )
        result = calibrator.calibrate(
            "Acme Corp",
            {"vendor": 0.95, "amount": 0.98, "invoice_number": 0.90},
        )
        # vendor: min(0.95, 1.0 - 0.3) = min(0.95, 0.7) = 0.7
        assert result["vendor"] == 0.7
        # amount: min(0.98, 1.0 - 0.1) = min(0.98, 0.9) = 0.9
        assert result["amount"] == 0.9
        # invoice_number: no correction rate → unchanged
        assert result["invoice_number"] == 0.90

    def test_returns_empty_dict_for_empty_input(self, calibrator):
        result = calibrator.calibrate("Vendor", {})
        assert result == {}

    def test_returns_input_when_vendor_is_empty(self, calibrator):
        confidences = {"vendor": 0.9}
        result = calibrator.calibrate("", confidences)
        assert result == confidences


# ---------------------------------------------------------------------------
# record_correction
# ---------------------------------------------------------------------------


class TestRecordCorrection:
    def test_increments_corrected_count(self, calibrator, db):
        calibrator.record_correction("Acme Corp", "vendor")
        calibrator.record_correction("Acme Corp", "vendor")

        profile = db.get_vendor_profile("default", "Acme Corp") or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        stats = meta.get("field_extraction_stats") or {}
        assert stats["vendor"]["corrected"] == 2

    def test_correction_without_extraction_sets_zero_total(self, calibrator, db):
        """Corrections increment corrected, total stays at whatever it was (0 initially)."""
        calibrator.record_correction("Acme Corp", "amount")

        profile = db.get_vendor_profile("default", "Acme Corp") or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        stats = meta.get("field_extraction_stats") or {}
        assert stats["amount"]["corrected"] == 1
        assert stats["amount"]["total"] == 0


# ---------------------------------------------------------------------------
# record_extraction
# ---------------------------------------------------------------------------


class TestRecordExtraction:
    def test_increments_total_count(self, calibrator, db):
        calibrator.record_extraction("Acme Corp", ["vendor", "amount"])
        calibrator.record_extraction("Acme Corp", ["vendor", "amount"])

        profile = db.get_vendor_profile("default", "Acme Corp") or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        stats = meta.get("field_extraction_stats") or {}
        assert stats["vendor"]["total"] == 2
        assert stats["amount"]["total"] == 2
        assert stats["vendor"]["corrected"] == 0

    def test_extraction_and_correction_produce_correct_rate(self, calibrator, db):
        """10 extractions + 2 corrections = 0.2 correction rate."""
        vendor = "Rate Vendor"
        for _ in range(10):
            calibrator.record_extraction(vendor, ["vendor"])
        calibrator.record_correction(vendor, "vendor")
        calibrator.record_correction(vendor, "vendor")

        profile = db.get_vendor_profile("default", vendor) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        rates = meta.get("field_correction_rates") or {}
        assert rates["vendor"] == 0.2

        # Now calibrate with that vendor
        result = calibrator.calibrate(vendor, {"vendor": 0.95})
        # min(0.95, 1.0 - 0.2) = 0.8
        assert result["vendor"] == 0.8
