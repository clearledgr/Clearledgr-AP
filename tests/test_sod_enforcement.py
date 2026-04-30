"""Tests for Wave 1 / D1 — segregation-of-duties enforcement.

Coverage:
  * Mode resolver: True / "true" / "on" → enforced; False / "off" /
    "disabled" → disabled; "warn" → warn; missing → enforced.
  * No processor activity yet (fresh AP item) → SOD passes.
  * Processor exists, different user approves → passes.
  * Processor and approver are the same user_id → blocked with
    violation_reason='approver_is_processor'.
  * Processor and approver share email but no user_id → still
    blocked (email-only match path).
  * Requester (ap_items.user_id) and approver are the same user_id
    → blocked with violation_reason='approver_is_requester'.
  * Warn mode: same violation, but ``allowed=True`` returned (the
    approve handler still proceeds; audit captures the warning).
  * Disabled mode: returns allowed=True with mode='disabled', no
    violation_reason.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services.sod_check import (  # noqa: E402
    _resolve_mode,
    check_sod,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _seed_ap_item(db, *, item_id: str, requester_user_id: str = "alice-uid"):
    return db.create_ap_item({
        "id": item_id,
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 250.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "needs_approval",
        "user_id": requester_user_id,
    })


def _seed_processor_event(
    db, *, ap_item_id: str, processor_user_id: str,
    processor_email: str = "", event_type: str = "field_review_corrected",
    counter: int = 0,
):
    """Insert a processor-style audit event for an AP item."""
    payload = {"actor_email": processor_email} if processor_email else {}
    return db.append_audit_event({
        "event_type": event_type,
        "actor_type": "user",
        "actor_id": processor_user_id,
        "organization_id": "default",
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "source": "test",
        "payload_json": payload,
        "idempotency_key": f"sod_test:{ap_item_id}:{processor_user_id}:{event_type}:{counter}:{time.time_ns()}",
    })


# ─── Mode resolver ──────────────────────────────────────────────────


def test_mode_default_when_setting_missing_is_enforced(db):
    assert _resolve_mode(db, "default") == "enforced"


def test_mode_disabled_via_bool_false(db):
    db.update_organization("default", settings_json={"sod_enforcement": False})
    assert _resolve_mode(db, "default") == "disabled"


def test_mode_disabled_via_string(db):
    db.update_organization("default", settings_json={"sod_enforcement": "off"})
    assert _resolve_mode(db, "default") == "disabled"


def test_mode_warn(db):
    db.update_organization("default", settings_json={"sod_enforcement": "warn"})
    assert _resolve_mode(db, "default") == "warn"


def test_mode_enforced_via_string_true(db):
    db.update_organization("default", settings_json={"sod_enforcement": "true"})
    assert _resolve_mode(db, "default") == "enforced"


# ─── Service: check_sod ─────────────────────────────────────────────


def test_no_processor_yet_passes(db):
    item = _seed_ap_item(db, item_id="ap-sod-fresh-1", requester_user_id="alice-uid")
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="bob-uid",
        approver_email="bob@example.test",
        organization_id="default",
    )
    assert out.allowed is True
    assert out.violation_reason is None
    assert out.processor_user_id is None


def test_different_user_approves_passes(db):
    item = _seed_ap_item(db, item_id="ap-sod-diff-1", requester_user_id="alice-uid")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="alice-uid",
        processor_email="alice@example.test", counter=1,
    )
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="bob-uid",
        approver_email="bob@example.test",
        organization_id="default",
    )
    assert out.allowed is True
    assert out.violation_reason is None
    assert out.processor_user_id == "alice-uid"


def test_approver_is_processor_blocks(db):
    item = _seed_ap_item(db, item_id="ap-sod-block-1")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="alice-uid",
        processor_email="alice@example.test", counter=2,
    )
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    assert out.allowed is False
    assert out.violation_reason == "approver_is_processor"
    assert "Segregation of duties" in (out.message or "")


def test_approver_is_processor_via_email_only(db):
    """When user_id is missing on the approver side, an email match
    against the processor still triggers the violation."""
    item = _seed_ap_item(db, item_id="ap-sod-email-1")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="some-uid",
        processor_email="alice@example.test", counter=3,
    )
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id=None,  # no user_id resolved
        approver_email="alice@example.test",
        organization_id="default",
    )
    assert out.allowed is False
    assert out.violation_reason == "approver_is_processor"


def test_approver_is_requester_blocks(db):
    item = _seed_ap_item(db, item_id="ap-sod-req-1", requester_user_id="alice-uid")
    # No processor event — only requester proxy from ap_items.user_id
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    assert out.allowed is False
    assert out.violation_reason == "approver_is_requester"


def test_warn_mode_still_returns_allowed_true(db):
    db.update_organization("default", settings_json={"sod_enforcement": "warn"})
    item = _seed_ap_item(db, item_id="ap-sod-warn-1")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="alice-uid",
        processor_email="alice@example.test", counter=4,
    )
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    # Warn mode = audit-emit but still proceed
    assert out.allowed is True
    assert out.mode == "warn"
    assert out.violation_reason == "approver_is_processor"


def test_disabled_mode_skips_check_entirely(db):
    db.update_organization("default", settings_json={"sod_enforcement": False})
    item = _seed_ap_item(db, item_id="ap-sod-disabled-1")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="alice-uid",
        processor_email="alice@example.test", counter=5,
    )
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    assert out.allowed is True
    assert out.mode == "disabled"
    assert out.violation_reason is None


def test_decision_events_are_not_treated_as_processor_activity(db):
    """An ``invoice_routed_for_approval`` event from the same user
    must NOT make them the processor. Only data-entry / correction
    events count."""
    item = _seed_ap_item(db, item_id="ap-sod-decision-1")
    db.append_audit_event({
        "event_type": "invoice_routed_for_approval",  # decision, not processor
        "actor_type": "user",
        "actor_id": "alice-uid",
        "organization_id": "default",
        "box_id": item["id"],
        "box_type": "ap_item",
        "source": "test",
        "payload_json": {"actor_email": "alice@example.test"},
        "idempotency_key": f"sod_decision_test:{item['id']}:{time.time_ns()}",
    })
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    # Alice never PROCESSED — she only routed. Processor-gate passes.
    # (She IS the requester, but for this test the AP item user_id is
    # alice-uid so we'd hit approver_is_requester. Verify that path.)
    assert out.violation_reason in ("approver_is_requester", None)


def test_latest_processor_event_wins(db):
    """When multiple processor events exist, the most-recent user
    is taken as the processor (not the first or any other)."""
    item = _seed_ap_item(db, item_id="ap-sod-latest-1", requester_user_id="zelda-uid")
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="alice-uid",
        processor_email="alice@example.test", counter=6,
    )
    time.sleep(0.01)  # ensure distinct timestamps
    _seed_processor_event(
        db, ap_item_id=item["id"], processor_user_id="bob-uid",
        processor_email="bob@example.test",
        event_type="gl_correction_recorded", counter=7,
    )
    # Bob is the latest processor; Alice approves → no violation
    # (because the gate compares against the LATEST processor)
    out = check_sod(
        db,
        ap_item_id=item["id"],
        approver_user_id="alice-uid",
        approver_email="alice@example.test",
        organization_id="default",
    )
    assert out.allowed is True
    assert out.processor_user_id == "bob-uid"
