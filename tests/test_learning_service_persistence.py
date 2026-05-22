"""Persistence + tenant-isolation tests for LearningService (vendor->GL learning).

Regression cover for the 2026-05-22 fix: LearningService was org-isolated but kept
its learned vendor->GL patterns purely in memory, so everything reset on every
deploy. It is now Postgres-backed (LearningStore mixin, migration v96) with a
write-through per-org cache.
"""
from __future__ import annotations

import pytest

from solden.core.org_utils import OrgIdMissing
from solden.services.learning import LearningService, get_learning_service

ORG_A = "org-learn-aaa"
ORG_B = "org-learn-bbb"


def test_suggest_persists_across_instances(postgres_test_db):
    svc = LearningService(ORG_A)
    svc.record_approval(vendor="Acme Inc", gl_code="6010", gl_description="Cloud", amount=100.0)

    # A brand-new instance has an empty cache; the suggestion must come from
    # Postgres — the whole point of the fix (in-memory state used to reset).
    fresh = LearningService(ORG_A)
    sug = fresh.suggest_gl_code(vendor="Acme Inc")
    assert sug is not None and sug["gl_code"] == "6010"


def test_patterns_are_org_scoped(postgres_test_db):
    a = LearningService(ORG_A)
    a.record_approval(vendor="Acme Inc", gl_code="6010", gl_description="Cloud", amount=100.0)

    b = LearningService(ORG_B)
    assert b.suggest_gl_code(vendor="Acme Inc") is None


def test_stats_are_org_scoped(postgres_test_db):
    a = LearningService(ORG_A)
    a.record_approval(
        vendor="Acme Inc", gl_code="6010", gl_description="Cloud",
        amount=100.0, was_auto_approved=True,
    )
    assert a.get_statistics()["total_learned"] >= 1

    b = LearningService(ORG_B)
    assert b.get_statistics()["total_learned"] == 0


def test_correction_confidence_shift_persists(postgres_test_db):
    svc = LearningService(ORG_A)
    svc.record_approval(vendor="Acme Inc", gl_code="6000", gl_description="Wrong", amount=100.0)
    svc.record_approval(
        vendor="Acme Inc", gl_code="6010", gl_description="Right", amount=100.0,
        was_corrected=True, original_suggestion="6000",
    )

    fresh = LearningService(ORG_A)
    hist = {p["gl_code"]: p for p in fresh.get_vendor_history("Acme Inc")["patterns"]}
    # The corrected GL is boosted above the wrongly-suggested one, and it persisted.
    assert hist["6010"]["confidence"] > hist["6000"]["confidence"]
    assert fresh.get_statistics()["corrections_received"] >= 1


def test_export_import_roundtrip_persists(postgres_test_db):
    src = LearningService(ORG_A)
    src.record_approval(vendor="Acme Inc", gl_code="6010", gl_description="Cloud", amount=100.0)
    blob = src.export_patterns()

    dst = LearningService(ORG_B)
    imported = dst.import_patterns(blob)
    assert imported >= 1

    # import must persist, not just populate memory
    fresh = LearningService(ORG_B)
    sug = fresh.suggest_gl_code(vendor="Acme Inc")
    assert sug is not None and sug["gl_code"] == "6010"


def test_get_vendor_pattern_returns_best_with_typical_amount(postgres_test_db):
    svc = LearningService(ORG_A)
    svc.record_approval(vendor="Acme Inc", gl_code="6010", gl_description="Cloud", amount=200.0)

    pat = svc.get_vendor_pattern("Acme Inc")
    assert pat is not None
    assert pat["gl_code"] == "6010"
    # typical_amount maps to avg_amount; first approval halves it via the
    # preserved create-then-update quirk (200 over occurrence_count 2).
    assert pat["typical_amount"] == 100.0

    # unknown vendor -> None (not an AttributeError, the old silent-bug shape)
    assert svc.get_vendor_pattern("Nobody Co") is None


def test_get_vendor_pattern_is_org_scoped(postgres_test_db):
    a = LearningService(ORG_A)
    a.record_approval(vendor="Acme Inc", gl_code="6010", gl_description="Cloud", amount=100.0)

    b = LearningService(ORG_B)
    assert b.get_vendor_pattern("Acme Inc") is None


def test_missing_org_fails_loud(postgres_test_db):
    with pytest.raises(OrgIdMissing):
        LearningService("")
    with pytest.raises(OrgIdMissing):
        get_learning_service("default")
