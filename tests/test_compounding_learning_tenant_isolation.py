"""Tenant-isolation + persistence tests for the compounding-learning service.

Regression cover for the 2026-05-22 fix: the service used to keep one global
SQLite file with an org-blind read path and org-blind pattern ids, so one org's
learned categorization/match patterns leaked into another org's reasoning. It is
now Postgres-backed (LearningStore mixin) with a per-org cache and org in the
primary key.
"""
from __future__ import annotations

import pytest

from solden.core.database import get_db
from solden.core.org_utils import OrgIdMissing
from solden.services.compounding_learning import CompoundingLearningService

ORG_A = "org-aaa"
ORG_B = "org-bbb"


def _record_categorization(svc, org, vendor, gl_code):
    return svc.record_correction(
        organization_id=org,
        correction_type="categorization",
        original_value={"gl_code": "6000"},
        corrected_value={"gl_code": gl_code, "gl_name": f"GL {gl_code}"},
        user_email="user@example.com",
        context={"vendor": vendor, "description": "cloud hosting services"},
    )


def test_categorization_hint_is_org_scoped(postgres_test_db):
    svc = CompoundingLearningService()
    _record_categorization(svc, ORG_A, "acme corp", "6010")

    hint_a = svc.get_categorization_hint(ORG_A, "acme corp", "cloud hosting services")
    assert hint_a is not None
    assert hint_a["gl_code"] == "6010"

    # Org B must NOT see Org A's learned pattern — this is the bleed the fix closes.
    hint_b = svc.get_categorization_hint(ORG_B, "acme corp", "cloud hosting services")
    assert hint_b is None


def test_patterns_persist_in_postgres_across_instances(postgres_test_db):
    svc1 = CompoundingLearningService()
    _record_categorization(svc1, ORG_A, "acme corp", "6010")

    # A brand-new instance has an empty cache; the hint must come from Postgres,
    # proving persistence (the old SQLite file was wiped on every deploy).
    svc2 = CompoundingLearningService()
    hint = svc2.get_categorization_hint(ORG_A, "acme corp", "cloud hosting services")
    assert hint is not None and hint["gl_code"] == "6010"


def test_same_pattern_id_does_not_collide_across_orgs(postgres_test_db):
    svc = CompoundingLearningService()
    # Identical vendor+GL in both orgs -> identical pattern_id, but distinct rows
    # because organization_id is part of the primary key.
    _record_categorization(svc, ORG_A, "acme corp", "6010")
    _record_categorization(svc, ORG_B, "acme corp", "6010")

    db = get_db()
    a = db.list_learning_patterns(ORG_A)
    b = db.list_learning_patterns(ORG_B)
    assert len(a) == 1 and len(b) == 1
    assert a[0]["pattern_id"] == b[0]["pattern_id"]  # same id, no overwrite
    assert svc.get_categorization_hint(ORG_A, "acme corp", "cloud hosting services")["gl_code"] == "6010"
    assert svc.get_categorization_hint(ORG_B, "acme corp", "cloud hosting services")["gl_code"] == "6010"


def test_metrics_are_org_scoped(postgres_test_db):
    svc = CompoundingLearningService()
    _record_categorization(svc, ORG_A, "acme corp", "6010")

    m_a = svc.get_learning_metrics(ORG_A)
    m_b = svc.get_learning_metrics(ORG_B)
    assert m_a.total_corrections >= 1
    assert m_a.patterns_learned >= 1
    assert m_b.total_corrections == 0
    assert m_b.patterns_learned == 0


def test_missing_org_fails_loud(postgres_test_db):
    svc = CompoundingLearningService()
    for org in (None, "", "default"):
        with pytest.raises(OrgIdMissing):
            svc.get_categorization_hint(org, "acme", "x")
        with pytest.raises(OrgIdMissing):
            svc.record_correction(
                organization_id=org,
                correction_type="categorization",
                original_value={},
                corrected_value={"gl_code": "6010"},
                user_email="user@example.com",
                context={"vendor": "acme"},
            )
