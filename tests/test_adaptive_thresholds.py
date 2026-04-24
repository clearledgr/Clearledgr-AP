"""Tests for adaptive decision thresholds — learns from operator feedback.

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
from clearledgr.services.adaptive_thresholds import (  # noqa: E402
    DEFAULT_THRESHOLD,
    MIN_HISTORY_FOR_ADJUSTMENT,
    MIN_THRESHOLD,
    MAX_THRESHOLD,
    ADJUSTMENT_STEP,
    AdaptiveThresholdService,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    _db = db_module.get_db()
    _db.initialize()
    _db.create_organization("default", "Default", settings={})
    return _db


@pytest.fixture()
def svc(db):
    return AdaptiveThresholdService(organization_id="default")


# ---------------------------------------------------------------------------
# get_threshold_for_vendor
# ---------------------------------------------------------------------------


class TestGetThresholdForVendor:
    def test_returns_default_when_no_history(self, svc):
        threshold = svc.get_threshold_for_vendor("Unknown Vendor")
        assert threshold == DEFAULT_THRESHOLD

    def test_returns_learned_threshold_from_vendor_profile(self, svc, db):
        db.upsert_vendor_profile(
            "default", "Acme Corp",
            metadata={"learned_auto_approve_threshold": 0.88},
        )
        threshold = svc.get_threshold_for_vendor("Acme Corp")
        assert threshold == 0.88

    def test_clamps_to_min(self, svc, db):
        db.upsert_vendor_profile(
            "default", "Acme Corp",
            metadata={"learned_auto_approve_threshold": 0.50},
        )
        threshold = svc.get_threshold_for_vendor("Acme Corp")
        assert threshold == MIN_THRESHOLD

    def test_clamps_to_max(self, svc, db):
        db.upsert_vendor_profile(
            "default", "Acme Corp",
            metadata={"learned_auto_approve_threshold": 1.5},
        )
        threshold = svc.get_threshold_for_vendor("Acme Corp")
        assert threshold == MAX_THRESHOLD


# ---------------------------------------------------------------------------
# record_decision_outcome
# ---------------------------------------------------------------------------


class TestRecordDecisionOutcome:
    def test_returns_none_when_not_enough_history(self, svc):
        """Before MIN_HISTORY_FOR_ADJUSTMENT entries, no adjustment is made."""
        for _ in range(MIN_HISTORY_FOR_ADJUSTMENT - 1):
            result = svc.record_decision_outcome(
                vendor_name="New Vendor",
                agent_recommendation="approve",
                operator_decision="approve",
                confidence=0.96,
            )
        assert result is None

    def test_threshold_decreases_when_agent_too_cautious(self, svc, db):
        """When agent escalates but operator approves consistently, threshold should drop."""
        vendor = "Cautious Vendor"
        # Build up enough history: mostly escalate → operator approved
        for i in range(MIN_HISTORY_FOR_ADJUSTMENT + 15):
            svc.record_decision_outcome(
                vendor_name=vendor,
                agent_recommendation="escalate",
                operator_decision="approved",
                confidence=0.90,
            )

        # Read the vendor profile to check the learned threshold
        profile = db.get_vendor_profile("default", vendor) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        learned = meta.get("learned_auto_approve_threshold")
        assert learned is not None
        assert learned < DEFAULT_THRESHOLD

    def test_threshold_increases_when_agent_too_lenient(self, svc, db):
        """When agent approves but operator rejects, threshold should rise."""
        vendor = "Lenient Vendor"
        # Mix: some approve+approve to build history, then approve+reject
        for i in range(MIN_HISTORY_FOR_ADJUSTMENT):
            svc.record_decision_outcome(
                vendor_name=vendor,
                agent_recommendation="approve",
                operator_decision="approved",
                confidence=0.96,
            )

        # Now add a bunch of approve → rejected
        for i in range(15):
            svc.record_decision_outcome(
                vendor_name=vendor,
                agent_recommendation="approve",
                operator_decision="rejected",
                confidence=0.96,
            )

        profile = db.get_vendor_profile("default", vendor) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        learned = meta.get("learned_auto_approve_threshold")
        assert learned is not None
        assert learned > DEFAULT_THRESHOLD

    def test_history_capped_at_50(self, svc, db):
        """Decision history should not grow beyond 50 entries."""
        vendor = "History Cap Vendor"
        for i in range(60):
            svc.record_decision_outcome(
                vendor_name=vendor,
                agent_recommendation="approve",
                operator_decision="approved",
                confidence=0.96,
            )

        profile = db.get_vendor_profile("default", vendor) or {}
        meta = profile.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        history = meta.get("decision_history") or []
        assert len(history) <= 50
