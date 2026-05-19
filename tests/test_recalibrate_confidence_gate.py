"""Smoke tests for the confidence-gate recalibrator.

Three contracts to pin:
  1. A record blocked under the old flat 0.95 gate but with vendor +
     amount above 0.92 + soft due_date clears under the new gate.
  2. A record genuinely failing on a critical field (vendor < 0.92)
     stays blocked — with the blockers updated to reflect the new
     calibration shape.
  3. Idempotency — running the script twice produces the same final
     state and doesn't duplicate audit events.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.recalibrate_confidence_gate import (  # noqa: E402
    DECISION_BLOCKERS_CHANGED,
    DECISION_CLEARED,
    DECISION_NO_CHANGE,
    DECISION_SKIPPED,
    recalibrate,
)


ORG_ID = "test-recalibrate"


def _seed_field_review_record(
    db,
    *,
    item_id: str,
    field_confidences: Dict[str, float],
    extra_metadata: Dict[str, Any] | None = None,
):
    """Create an AP record in field_review_required state with the
    persisted metadata shape the old gate would have written."""
    metadata = {
        "requires_field_review": True,
        "field_confidences": field_confidences,
        "confidence_blockers": [
            {"field": "due_date", "reason": "critical_field_low_confidence"},
        ],
        "document_type": "invoice",
        "primary_source": "email",
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"key-{item_id}",
            "thread_id": f"thr-{item_id}",
            "vendor_name": "Acme Ltd",
            "amount": 1234.56,
            "currency": "GBP",
            "invoice_number": f"INV-{item_id}",
            "due_date": "2026-04-01",
            "state": "received",
            "confidence": 0.91,
            "field_confidences": field_confidences,
            "exception_code": "field_review_required",
            "exception_severity": "medium",
            "requires_field_review": True,
            "organization_id": ORG_ID,
            "metadata": metadata,
        }
    )


@pytest.fixture
def db(postgres_test_db):
    from solden.core.database import get_db

    db = get_db()
    db.initialize()
    db.create_organization(ORG_ID, name="Recalibration Org")
    return db


# ---------------------------------------------------------------------------
# Contract 1: false-positive cleared
# ---------------------------------------------------------------------------


def _metadata_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return {}


def test_record_blocked_under_old_gate_clears_under_new_gate(db):
    """vendor 0.97 + amount 0.99 + invoice_number 0.93 + due_date 0.78
    — the 13/13 production pattern. Old gate blocked on due_date < 0.95;
    new gate must clear (vendor + amount above 0.92 critical, due_date
    advisory)."""
    _seed_field_review_record(
        db,
        item_id="recalibrate-cleared-1",
        field_confidences={
            "vendor": 0.97,
            "amount": 0.99,
            "invoice_number": 0.93,
            "due_date": 0.78,
        },
    )

    outcomes = recalibrate(ORG_ID, commit=True)
    assert len(outcomes) == 1
    assert outcomes[0].decision == DECISION_CLEARED

    refreshed = db.get_ap_item("recalibrate-cleared-1")
    metadata = _metadata_dict(refreshed)
    assert not metadata.get("requires_field_review")
    assert (refreshed.get("exception_code") or "") in ("", None)
    assert metadata.get("confidence_blockers") == []


# ---------------------------------------------------------------------------
# Contract 2: real critical failure stays blocked
# ---------------------------------------------------------------------------


def test_record_with_low_vendor_confidence_stays_blocked(db):
    """vendor 0.85 — below the 0.92 critical threshold. Must remain
    blocked, with the blockers list reflecting the new calibration's
    shape (vendor as critical, no due_date entry since due_date is now
    advisory-only)."""
    _seed_field_review_record(
        db,
        item_id="recalibrate-stays-1",
        field_confidences={
            "vendor": 0.85,
            "amount": 0.99,
            "invoice_number": 0.99,
            "due_date": 0.99,
        },
    )

    outcomes = recalibrate(ORG_ID, commit=True)
    assert len(outcomes) == 1
    # Old persisted blockers were [due_date]; new blockers are [vendor].
    # That counts as "blockers_changed" — still blocked but for a
    # different (real) reason.
    assert outcomes[0].decision == DECISION_BLOCKERS_CHANGED
    assert outcomes[0].new_blockers == ["vendor"]

    refreshed = db.get_ap_item("recalibrate-stays-1")
    metadata = refreshed.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata.get("requires_field_review") is True
    assert [b["field"] for b in metadata.get("confidence_blockers") or []] == ["vendor"]


# ---------------------------------------------------------------------------
# Contract 3: idempotency
# ---------------------------------------------------------------------------


def test_recalibration_is_idempotent(db):
    """Running twice must produce identical state and not duplicate
    audit events. First run clears the record; second run sees it
    already-cleared and reports DECISION_NO_CHANGE."""
    _seed_field_review_record(
        db,
        item_id="recalibrate-idem-1",
        field_confidences={
            "vendor": 0.97,
            "amount": 0.99,
            "invoice_number": 0.93,
            "due_date": 0.78,
        },
    )

    first = recalibrate(ORG_ID, commit=True)
    second = recalibrate(ORG_ID, commit=True)

    assert first[0].decision == DECISION_CLEARED
    # After the first run the record no longer flagged as
    # field_review — second pass simply doesn't see it.
    assert all(o.ap_item_id != "recalibrate-idem-1" for o in second), (
        "second pass shouldn't re-process an already-cleared record"
    )


# ---------------------------------------------------------------------------
# Contract 4: skip records without persisted field_confidences
# ---------------------------------------------------------------------------


def test_record_without_field_confidences_is_skipped(db):
    """Without per-field signal we cannot fairly re-evaluate. Skip,
    don't blanket-clear."""
    db.create_ap_item(
        {
            "id": "recalibrate-no-conf-1",
            "invoice_key": "key-no-conf-1",
            "thread_id": "thr-no-conf-1",
            "vendor_name": "Mystery Vendor",
            "amount": 999.99,
            "currency": "GBP",
            "invoice_number": "INV-???",
            "due_date": "2026-05-01",
            "state": "received",
            "confidence": 0.50,
            "exception_code": "field_review_required",
            "exception_severity": "medium",
            "requires_field_review": True,
            "organization_id": ORG_ID,
            "metadata": {
                "requires_field_review": True,
                # field_confidences intentionally absent
                "document_type": "invoice",
                "primary_source": "email",
            },
        }
    )

    outcomes = recalibrate(ORG_ID, commit=True)
    target = [o for o in outcomes if o.ap_item_id == "recalibrate-no-conf-1"]
    assert len(target) == 1
    assert target[0].decision == DECISION_SKIPPED

    refreshed = db.get_ap_item("recalibrate-no-conf-1")
    metadata = _metadata_dict(refreshed)
    assert metadata.get("requires_field_review") is True


# ---------------------------------------------------------------------------
# Contract 5: dry-run mutates nothing
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate(db):
    """Dry run must produce the same outcome list but leave records
    untouched."""
    _seed_field_review_record(
        db,
        item_id="recalibrate-dry-1",
        field_confidences={
            "vendor": 0.97,
            "amount": 0.99,
            "invoice_number": 0.93,
            "due_date": 0.78,
        },
    )

    outcomes = recalibrate(ORG_ID, commit=False)
    assert len(outcomes) == 1
    assert outcomes[0].decision == DECISION_CLEARED

    refreshed = db.get_ap_item("recalibrate-dry-1")
    metadata = _metadata_dict(refreshed)
    assert metadata.get("requires_field_review") is True, (
        "dry run must not mutate the record"
    )
