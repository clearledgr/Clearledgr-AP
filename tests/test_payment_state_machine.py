"""Tests for Wave 2 / C1 — payment-tracking state machine extension.

Coverage:
  * Four new states added to APState enum:
    awaiting_payment, payment_in_flight, payment_executed, payment_failed.
  * Transitions valid (per VALID_TRANSITIONS):
      posted_to_erp → awaiting_payment / closed / reversed
      awaiting_payment → payment_in_flight / payment_executed /
                         payment_failed / closed / reversed
      payment_in_flight → payment_executed / payment_failed / reversed
      payment_executed → closed / reversed
      payment_failed → awaiting_payment / closed / reversed
  * Forbidden transitions stay forbidden (e.g. closed → anything,
    payment_executed → payment_in_flight, awaiting_payment →
    posted_to_erp etc).
  * Terminal states unchanged (closed, reversed, rejected).
  * DB-level state guard accepts the new state values without
    rejection. Refreshing the trigger function across migrations
    is verified by inserting an AP item directly with state=
    awaiting_payment.
  * Legacy state map still maps "posted" → POSTED_TO_ERP (no
    accidental shadow by new states).
  * normalize_state handles all 4 new state strings.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.ap_states import (  # noqa: E402
    APState,
    LEGACY_STATE_MAP,
    TERMINAL_STATES,
    VALID_STATE_VALUES,
    VALID_TRANSITIONS,
    IllegalTransitionError,
    normalize_state,
    validate_transition,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


# ─── Enum + constants ───────────────────────────────────────────────


def test_four_new_payment_states_exist():
    """APState carries the new lifecycle members."""
    assert APState.AWAITING_PAYMENT.value == "awaiting_payment"
    assert APState.PAYMENT_IN_FLIGHT.value == "payment_in_flight"
    assert APState.PAYMENT_EXECUTED.value == "payment_executed"
    assert APState.PAYMENT_FAILED.value == "payment_failed"


def test_valid_state_values_includes_payment_states():
    for s in (
        "awaiting_payment", "payment_in_flight",
        "payment_executed", "payment_failed",
    ):
        assert s in VALID_STATE_VALUES


def test_terminal_states_unchanged_by_payment_extension():
    """Adding payment lifecycle states must not change the terminal
    set — closed/reversed/rejected stay terminal; payment states
    are not terminal (closed/reversed are still the ends of the
    pipeline)."""
    assert TERMINAL_STATES == frozenset({
        APState.REJECTED, APState.REVERSED, APState.CLOSED,
    })
    # Each new state has at least one outbound transition (i.e. is
    # not silently terminal):
    for s in (
        APState.AWAITING_PAYMENT, APState.PAYMENT_IN_FLIGHT,
        APState.PAYMENT_EXECUTED, APState.PAYMENT_FAILED,
    ):
        assert len(VALID_TRANSITIONS[s]) > 0, f"{s} has no outbound transitions"


def test_normalize_state_handles_all_payment_states():
    assert normalize_state("awaiting_payment") == "awaiting_payment"
    assert normalize_state("PAYMENT_IN_FLIGHT") == "payment_in_flight"
    assert normalize_state(" payment_executed ") == "payment_executed"
    assert normalize_state("payment_failed") == "payment_failed"


def test_legacy_state_map_unaffected():
    """Adding new canonical states must not shadow the legacy map
    used during migration. ``posted`` still maps to POSTED_TO_ERP."""
    assert LEGACY_STATE_MAP["posted"] == APState.POSTED_TO_ERP
    assert LEGACY_STATE_MAP["closed"] == APState.CLOSED


# ─── Allowed transitions ────────────────────────────────────────────


def test_posted_to_erp_can_advance_to_awaiting_payment():
    assert validate_transition("posted_to_erp", "awaiting_payment") is True


def test_posted_to_erp_can_close_directly():
    """Legacy / payment-tracking-disabled tenants close directly."""
    assert validate_transition("posted_to_erp", "closed") is True


def test_posted_to_erp_can_reverse():
    assert validate_transition("posted_to_erp", "reversed") is True


def test_awaiting_payment_to_in_flight():
    assert validate_transition("awaiting_payment", "payment_in_flight") is True


def test_awaiting_payment_can_skip_to_executed():
    """Some webhooks (SAP B1 polling) only fire on cleared payment —
    the pipeline can skip the in-flight intermediate state."""
    assert validate_transition("awaiting_payment", "payment_executed") is True


def test_awaiting_payment_to_failed():
    assert validate_transition("awaiting_payment", "payment_failed") is True


def test_awaiting_payment_operator_close():
    """Operator can override-close (cash sale, write-off)."""
    assert validate_transition("awaiting_payment", "closed") is True


def test_in_flight_to_executed():
    assert validate_transition("payment_in_flight", "payment_executed") is True


def test_in_flight_to_failed():
    assert validate_transition("payment_in_flight", "payment_failed") is True


def test_executed_to_closed():
    assert validate_transition("payment_executed", "closed") is True


def test_executed_can_reverse_for_post_payment_dispute():
    """Doc Stage 9 'post-payment dispute' path."""
    assert validate_transition("payment_executed", "reversed") is True


def test_failed_to_awaiting_for_retry():
    assert validate_transition("payment_failed", "awaiting_payment") is True


def test_failed_to_reversed_give_up():
    assert validate_transition("payment_failed", "reversed") is True


# ─── Forbidden transitions ──────────────────────────────────────────


def test_closed_is_terminal():
    """No outbound transitions from closed."""
    assert validate_transition("closed", "awaiting_payment") is False
    assert validate_transition("closed", "payment_in_flight") is False
    assert validate_transition("closed", "reversed") is False


def test_reversed_is_terminal():
    assert validate_transition("reversed", "closed") is False
    assert validate_transition("reversed", "awaiting_payment") is False


def test_no_backwards_from_executed_to_in_flight():
    """Once a payment is recorded as executed, going back to
    in-flight would be a stale-event bug — reject."""
    assert validate_transition("payment_executed", "payment_in_flight") is False


def test_no_skipping_back_from_awaiting_to_posted():
    """Audit-trail integrity: payment lifecycle is forward-only."""
    assert validate_transition("awaiting_payment", "posted_to_erp") is False


def test_no_payment_states_from_pre_post_states():
    """A bill cannot enter payment states without going through
    posted_to_erp first."""
    pre_post = ["received", "validated", "needs_approval", "approved", "ready_to_post"]
    for src in pre_post:
        for tgt in (
            "awaiting_payment", "payment_in_flight",
            "payment_executed", "payment_failed",
        ):
            assert validate_transition(src, tgt) is False, (
                f"{src} -> {tgt} should be illegal"
            )


# ─── DB-level guard ─────────────────────────────────────────────────


def test_db_state_guard_accepts_new_payment_states(db):
    """The Postgres trigger ``enforce_valid_ap_state`` validates against
    the embedded state list. The function-replace pattern in
    ``_install_ap_state_guard`` ensures existing tenants get the
    refreshed state list on init.

    Direct-INSERT a row with state=awaiting_payment to verify the
    trigger doesn't reject."""
    item = db.create_ap_item({
        "id": "AP-payment-state-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "received",
    })
    # Walk through the lifecycle so the state-machine validator and
    # DB trigger both accept each step.
    db.update_ap_item(item["id"], state="validated")
    db.update_ap_item(item["id"], state="needs_approval")
    db.update_ap_item(item["id"], state="approved")
    db.update_ap_item(item["id"], state="ready_to_post")
    db.update_ap_item(item["id"], state="posted_to_erp")
    db.update_ap_item(item["id"], state="awaiting_payment")
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "awaiting_payment"

    db.update_ap_item(item["id"], state="payment_in_flight")
    db.update_ap_item(item["id"], state="payment_executed")
    db.update_ap_item(item["id"], state="closed")
    final = db.get_ap_item(item["id"])
    assert final["state"] == "closed"


def test_db_state_guard_rejects_invalid_state(db):
    """Bogus state values are still rejected by the trigger."""
    item = db.create_ap_item({
        "id": "AP-bogus-state-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "received",
    })
    # Direct-SQL bypass of the application-layer validator to test
    # the DB trigger in isolation. transition_or_raise would catch
    # this earlier; the trigger is defence-in-depth.
    with pytest.raises(Exception) as excinfo:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ap_items SET state = %s WHERE id = %s",
                ("not_a_real_state", item["id"]),
            )
            conn.commit()
    assert "invalid ap item state" in str(excinfo.value).lower()


def test_db_state_guard_rejects_legal_app_state_via_illegal_transition(db):
    """Application-layer state machine catches illegal transitions
    before the DB sees them. Verify by trying received → posted_to_erp
    in one shot (illegal — must go through validated etc)."""
    item = db.create_ap_item({
        "id": "AP-illegal-trans-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "received",
    })
    with pytest.raises(IllegalTransitionError):
        db.update_ap_item(item["id"], state="payment_executed")
