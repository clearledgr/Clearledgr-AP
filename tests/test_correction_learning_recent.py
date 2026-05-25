"""correction_learning.get_recent_corrections reads the learned rules.

It iterated self._rules (never assigned; class uses _learned_rules), and the
broad except swallowed the AttributeError -> it always returned []. Now it
actually surfaces a vendor's recent corrections to feed the extraction prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.services.correction_learning import CorrectionLearningService, LearningRule  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    return inst


def test_get_recent_corrections_reads_learned_rules(db):
    svc = CorrectionLearningService("orgA")
    svc._learned_rules = {
        "r1": LearningRule(
            rule_id="r1", rule_type="vendor_alias",
            condition={"raw_vendor": "acme inc"},
            action={"normalized_vendor": "Acme Inc"},
            confidence=0.9, learned_from=3, created_at="2026-01-01",
        ),
        "r2": LearningRule(
            rule_id="r2", rule_type="field_correction",
            condition={"vendor_name": "acme inc", "field": "gl_code", "original_value": "5000"},
            action={"corrected_value": "5200"},
            confidence=0.8, learned_from=2, created_at="2026-01-01",
        ),
    }
    out = svc.get_recent_corrections("Acme Inc")
    assert any(c["field"] == "vendor" and c["corrected"] == "Acme Inc" for c in out)
    assert any(c["field"] == "gl_code" and c["corrected"] == "5200" for c in out)
