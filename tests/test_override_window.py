"""Tests for Phase 1.4 — override-window mechanism + Slack undo UX.

Covers:
  - State machine: posted_to_erp → reversed → closed transitions
  - OverrideWindowStore CRUD (via the database mixin)
  - OverrideWindowService: open, expiry calculations, attempt_reversal
    happy path / expired / already-reversed / failed paths, expire_window
  - Slack card builders (pure block kit)
  - approval_action_contract: undo_post_* parsing
  - State observer: OverrideWindowObserver fires on posted_to_erp
  - Background reaper: reap_expired_override_windows
  - REST API: POST /api/ap/items/{id}/reverse for ops surface
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp-file ClearledgrDB wired as the singleton."""
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "override_window.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_org(db, org_id: str, settings: Optional[Dict[str, Any]] = None):
    db.create_organization(org_id, name=f"Test {org_id}", settings=settings or {})


def _seed_posted_ap_item(
    db,
    *,
    ap_item_id: str = "AP-OVW-1",
    organization_id: str = "org_ovw",
    erp_reference: str = "BILL-42",
    erp_type: str = "quickbooks",
    state: str = "posted_to_erp",
    metadata: Optional[Dict[str, Any]] = None,
):
    final_metadata = {"erp_type": erp_type}
    if metadata:
        final_metadata.update(metadata)
    db.create_ap_item(
        {
            "id": ap_item_id,
            "organization_id": organization_id,
            "vendor_name": "Override Vendor",
            "amount": 750.0,
            "currency": "USD",
            "state": state,
            "erp_reference": erp_reference,
            "thread_id": f"thread-{ap_item_id}",
            "invoice_number": f"INV-{ap_item_id}",
            "metadata": final_metadata,
        }
    )
    return db.get_ap_item(ap_item_id)


def _now_iso(offset_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).isoformat()


# ===========================================================================
# State machine
# ===========================================================================


class TestStateMachineReversed:

    def test_reversed_state_exists(self):
        from clearledgr.core.ap_states import APState
        assert APState.REVERSED.value == "reversed"

    def test_posted_to_erp_can_transition_to_reversed(self):
        from clearledgr.core.ap_states import validate_transition
        assert validate_transition("posted_to_erp", "reversed") is True

    def test_posted_to_erp_can_still_transition_to_closed(self):
        from clearledgr.core.ap_states import validate_transition
        assert validate_transition("posted_to_erp", "closed") is True

    def test_reversed_is_terminal(self):
        """Reversed is now terminal (no outbound edges). An item that was
        posted then reversed is a distinct outcome from one that was
        posted and successfully paid out; keeping them in separate
        terminal states stops reversed-then-closed items leaking into
        the Kanban Paid column."""
        from clearledgr.core.ap_states import validate_transition, TERMINAL_STATES, APState
        assert APState.REVERSED in TERMINAL_STATES
        assert validate_transition("reversed", "closed") is False
        assert validate_transition("reversed", "posted_to_erp") is False

    def test_closed_remains_terminal(self):
        from clearledgr.core.ap_states import validate_transition
        assert validate_transition("closed", "reversed") is False


# ===========================================================================
# OverrideWindowStore (DB mixin)
# ===========================================================================


class TestOverrideWindowStore:

    def test_create_returns_pending_window(self, tmp_db):
        row = tmp_db.create_override_window(
            ap_item_id="AP-1",
            organization_id="org_t",
            erp_reference="bill-1",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        assert row["state"] == "pending"
        assert row["ap_item_id"] == "AP-1"
        assert row["erp_type"] == "xero"
        assert row["id"].startswith("ovw_")

    def test_get_window_round_trip(self, tmp_db):
        created = tmp_db.create_override_window(
            ap_item_id="AP-2",
            organization_id="org_t",
            erp_reference="bill-2",
            erp_type="quickbooks",
            expires_at=_now_iso(15),
        )
        fetched = tmp_db.get_override_window(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["state"] == "pending"

    def test_get_by_ap_item_id_returns_most_recent(self, tmp_db):
        first = tmp_db.create_override_window(
            ap_item_id="AP-3",
            organization_id="org_t",
            erp_reference="bill-3a",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        # Mark first expired so we have a clear "stale" row
        tmp_db.mark_override_window_expired(first["id"])
        second = tmp_db.create_override_window(
            ap_item_id="AP-3",
            organization_id="org_t",
            erp_reference="bill-3b",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        latest = tmp_db.get_override_window_by_ap_item_id("AP-3")
        assert latest["id"] == second["id"]

    def test_list_expired_returns_only_pending_past_deadline(self, tmp_db):
        # Pending + future expiry → not expired
        future = tmp_db.create_override_window(
            ap_item_id="AP-4",
            organization_id="org_t",
            erp_reference="bill-4",
            erp_type="xero",
            expires_at=_now_iso(60),
        )
        # Pending + past expiry → expired
        past = tmp_db.create_override_window(
            ap_item_id="AP-5",
            organization_id="org_t",
            erp_reference="bill-5",
            erp_type="xero",
            expires_at=_now_iso(-5),
        )
        # Already expired → not in list
        already = tmp_db.create_override_window(
            ap_item_id="AP-6",
            organization_id="org_t",
            erp_reference="bill-6",
            erp_type="xero",
            expires_at=_now_iso(-30),
        )
        tmp_db.mark_override_window_expired(already["id"])

        expired = tmp_db.list_expired_override_windows()
        ids = {row["id"] for row in expired}
        assert past["id"] in ids
        assert future["id"] not in ids
        assert already["id"] not in ids

    def test_mark_reversed_only_succeeds_for_pending(self, tmp_db):
        created = tmp_db.create_override_window(
            ap_item_id="AP-7",
            organization_id="org_t",
            erp_reference="bill-7",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        ok = tmp_db.mark_override_window_reversed(
            created["id"],
            reversed_by="user@test",
            reversal_reason="human_override",
            reversal_ref="rev-1",
        )
        assert ok is True
        # Second attempt is a no-op (state is now 'reversed', not 'pending')
        ok2 = tmp_db.mark_override_window_reversed(
            created["id"],
            reversed_by="user@test",
            reversal_reason="duplicate",
            reversal_ref="rev-2",
        )
        assert ok2 is False
        row = tmp_db.get_override_window(created["id"])
        assert row["state"] == "reversed"
        assert row["reversed_by"] == "user@test"
        assert row["reversal_ref"] == "rev-1"

    def test_mark_expired_only_succeeds_for_pending(self, tmp_db):
        created = tmp_db.create_override_window(
            ap_item_id="AP-8",
            organization_id="org_t",
            erp_reference="bill-8",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        # Manually mark reversed first
        tmp_db.mark_override_window_reversed(
            created["id"], reversed_by="u", reversal_reason="r", reversal_ref="x"
        )
        # expire should be a no-op now
        ok = tmp_db.mark_override_window_expired(created["id"])
        assert ok is False
        row = tmp_db.get_override_window(created["id"])
        assert row["state"] == "reversed"

    def test_mark_failed_records_failure_reason(self, tmp_db):
        created = tmp_db.create_override_window(
            ap_item_id="AP-9",
            organization_id="org_t",
            erp_reference="bill-9",
            erp_type="netsuite",
            expires_at=_now_iso(15),
        )
        ok = tmp_db.mark_override_window_failed(created["id"], "payment_already_applied")
        assert ok is True
        row = tmp_db.get_override_window(created["id"])
        assert row["state"] == "failed"
        assert row["failure_reason"] == "payment_already_applied"

    def test_update_slack_refs_persists(self, tmp_db):
        created = tmp_db.create_override_window(
            ap_item_id="AP-10",
            organization_id="org_t",
            erp_reference="bill-10",
            erp_type="xero",
            expires_at=_now_iso(15),
        )
        ok = tmp_db.update_override_window_slack_refs(
            created["id"], slack_channel="C123", slack_message_ts="1234567.890"
        )
        assert ok is True
        row = tmp_db.get_override_window(created["id"])
        assert row["slack_channel"] == "C123"
        assert row["slack_message_ts"] == "1234567.890"


# ===========================================================================
# OverrideWindowService — duration config + lifecycle
# ===========================================================================


class TestOverrideWindowServiceDuration:

    def test_default_duration_when_no_config(self, tmp_db):
        from clearledgr.services.override_window import (
            DEFAULT_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(tmp_db, "org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes() == DEFAULT_OVERRIDE_WINDOW_MINUTES == 15

    def test_configured_duration_for_erp_post(self, tmp_db):
        """Per-action dict with an explicit erp_post entry."""
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"erp_post": 30}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == 30

    def test_default_action_key_used_as_fallback(self, tmp_db):
        """When the requested action isn't in the dict, fall back to 'default'."""
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"default": 45}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        # Unknown action type → falls back to "default" key
        assert service.get_window_duration_minutes("payment_execution") == 45
        # Same for the standard erp_post action
        assert service.get_window_duration_minutes("erp_post") == 45

    def test_per_action_overrides_default_key(self, tmp_db):
        """Exact action match wins over the 'default' key."""
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {
                        "default": 15,
                        "erp_post": 5,
                        "payment_execution": 60,
                    }
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == 5
        assert service.get_window_duration_minutes("payment_execution") == 60
        # An action not in the map falls through to 'default'
        assert service.get_window_duration_minutes("vendor_onboarding") == 15

    def test_dict_with_no_default_or_action_returns_constant_default(self, tmp_db):
        """Empty config dict → DEFAULT_OVERRIDE_WINDOW_MINUTES constant."""
        from clearledgr.services.override_window import (
            DEFAULT_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {"override_window_minutes": {}}
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == DEFAULT_OVERRIDE_WINDOW_MINUTES

    def test_negative_duration_clamped_to_minimum(self, tmp_db):
        from clearledgr.services.override_window import (
            MIN_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"erp_post": -5}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == MIN_OVERRIDE_WINDOW_MINUTES

    def test_huge_duration_clamped_to_maximum(self, tmp_db):
        from clearledgr.services.override_window import (
            MAX_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"erp_post": 99_999}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == MAX_OVERRIDE_WINDOW_MINUTES

    def test_invalid_duration_falls_back_to_default(self, tmp_db):
        from clearledgr.services.override_window import (
            DEFAULT_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"erp_post": "not-a-number"}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        assert service.get_window_duration_minutes("erp_post") == DEFAULT_OVERRIDE_WINDOW_MINUTES

    def test_legacy_int_shape_rejected_with_default(self, tmp_db):
        """The old flat-int shape is no longer accepted (no-backcompat policy)."""
        from clearledgr.services.override_window import (
            DEFAULT_OVERRIDE_WINDOW_MINUTES,
            OverrideWindowService,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={"workflow_controls": {"override_window_minutes": 30}},
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        # Falls back to the constant default — orgs must migrate to the dict shape
        assert service.get_window_duration_minutes("erp_post") == DEFAULT_OVERRIDE_WINDOW_MINUTES


class TestOverrideWindowServiceLifecycle:

    def test_open_window_persists_with_correct_expiry(self, tmp_db):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {"erp_post": 20}
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        before = datetime.now(timezone.utc)
        window = service.open_window(
            ap_item_id="AP-OPEN",
            erp_reference="bill-open",
            erp_type="xero",
            action_type="erp_post",
        )
        after = datetime.now(timezone.utc)
        assert window["state"] == "pending"
        assert window["action_type"] == "erp_post"
        expires = datetime.fromisoformat(
            window["expires_at"].replace("Z", "+00:00")
        )
        delta_min = (expires - before).total_seconds() / 60
        assert 19.5 <= delta_min <= 20.5
        # also ensure we wrote the row
        fetched = tmp_db.get_override_window(window["id"])
        assert fetched["ap_item_id"] == "AP-OPEN"
        assert fetched["action_type"] == "erp_post"

    def test_open_window_uses_per_action_duration(self, tmp_db):
        """Two different action types in the same org get different durations."""
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {
                        "erp_post": 5,
                        "payment_execution": 60,
                    }
                }
            },
        )
        service = OverrideWindowService("org_t", db=tmp_db)
        before = datetime.now(timezone.utc)

        erp_window = service.open_window(
            ap_item_id="AP-ERP-1",
            erp_reference="bill-erp-1",
            erp_type="xero",
            action_type="erp_post",
        )
        pay_window = service.open_window(
            ap_item_id="AP-PAY-1",
            erp_reference="bill-pay-1",
            erp_type="xero",
            action_type="payment_execution",
        )

        erp_expires = datetime.fromisoformat(erp_window["expires_at"].replace("Z", "+00:00"))
        pay_expires = datetime.fromisoformat(pay_window["expires_at"].replace("Z", "+00:00"))

        erp_delta_min = (erp_expires - before).total_seconds() / 60
        pay_delta_min = (pay_expires - before).total_seconds() / 60

        assert 4.5 <= erp_delta_min <= 5.5
        assert 59.5 <= pay_delta_min <= 60.5

        # action_type persisted independently
        assert erp_window["action_type"] == "erp_post"
        assert pay_window["action_type"] == "payment_execution"
        assert tmp_db.get_override_window(erp_window["id"])["action_type"] == "erp_post"
        assert tmp_db.get_override_window(pay_window["id"])["action_type"] == "payment_execution"

    def test_is_window_expired_logic(self):
        from clearledgr.services.override_window import OverrideWindowService
        future = {"expires_at": _now_iso(60)}
        past = {"expires_at": _now_iso(-1)}
        assert OverrideWindowService.is_window_expired(future) is False
        assert OverrideWindowService.is_window_expired(past) is True
        assert OverrideWindowService.is_window_expired({}) is True
        assert OverrideWindowService.is_window_expired({"expires_at": "not-iso"}) is True

    def test_time_remaining_seconds(self):
        from clearledgr.services.override_window import OverrideWindowService
        future = {"expires_at": _now_iso(5)}
        past = {"expires_at": _now_iso(-1)}
        future_secs = OverrideWindowService.time_remaining_seconds(future)
        assert 290 <= future_secs <= 300
        assert OverrideWindowService.time_remaining_seconds(past) == 0
        assert OverrideWindowService.time_remaining_seconds({}) == 0


class TestOverrideWindowServiceAttemptReversal:

    def _setup_workflow_stub(self, monkeypatch):
        """Stub get_invoice_workflow so we don't hit the full workflow at all."""
        fake_workflow = MagicMock()
        fake_workflow._transition_invoice_state = MagicMock(return_value=True)
        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda org_id: fake_workflow,
        )
        return fake_workflow

    def test_window_not_found(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        outcome = asyncio.run(
            service.attempt_reversal(
                window_id="ovw_does_not_exist",
                actor_id="user@test",
                reason="override",
            )
        )
        assert outcome.status == "not_found"

    def test_already_reversed_short_circuit(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-AR", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        window = service.open_window(
            ap_item_id="AP-AR",
            erp_reference="BILL-AR",
            erp_type="xero",
        )
        tmp_db.mark_override_window_reversed(
            window["id"], reversed_by="prev", reversal_reason="prev", reversal_ref="ref-prev"
        )

        outcome = asyncio.run(
            service.attempt_reversal(
                window_id=window["id"],
                actor_id="user@test",
                reason="override",
            )
        )
        assert outcome.status == "already_reversed"

    def test_window_expired_short_circuit(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-EX", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)

        # Create an already-expired window directly
        window = tmp_db.create_override_window(
            ap_item_id="AP-EX",
            organization_id="org_t",
            erp_reference="BILL-EX",
            erp_type="xero",
            expires_at=_now_iso(-5),
        )

        # Stub the workflow so the safety close transition doesn't fail
        self._setup_workflow_stub(monkeypatch)

        outcome = asyncio.run(
            service.attempt_reversal(
                window_id=window["id"],
                actor_id="user@test",
                reason="override",
            )
        )
        assert outcome.status == "expired"
        # The window should now be marked expired in the DB
        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "expired"

    def test_happy_path_reversal(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-OK", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        window = service.open_window(
            ap_item_id="AP-OK",
            erp_reference="BILL-OK",
            erp_type="quickbooks",
        )

        async def _fake_reverse_bill(*args, **kwargs):
            return {
                "status": "success",
                "erp": "quickbooks",
                "reference_id": kwargs.get("erp_reference"),
                "reversal_method": "delete",
                "reversal_ref": kwargs.get("erp_reference"),
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill",
            _fake_reverse_bill,
        )
        self._setup_workflow_stub(monkeypatch)

        outcome = asyncio.run(
            service.attempt_reversal(
                window_id=window["id"],
                actor_id="user@test",
                reason="human_override",
            )
        )
        assert outcome.status == "reversed"
        assert outcome.reversal_method == "delete"
        assert outcome.erp == "quickbooks"

        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "reversed"
        assert row["reversed_by"] == "user@test"
        assert row["reversal_reason"] == "human_override"

    def test_erp_failure_marks_window_failed(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-FAIL", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        window = service.open_window(
            ap_item_id="AP-FAIL",
            erp_reference="BILL-FAIL",
            erp_type="xero",
        )

        async def _fake_reverse_bill(*args, **kwargs):
            return {
                "status": "error",
                "erp": "xero",
                "reason": "payment_already_applied",
                "erp_error_detail": "Payment allocated",
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill",
            _fake_reverse_bill,
        )
        self._setup_workflow_stub(monkeypatch)

        outcome = asyncio.run(
            service.attempt_reversal(
                window_id=window["id"],
                actor_id="user@test",
                reason="override",
            )
        )
        assert outcome.status == "failed"
        assert outcome.reason == "payment_already_applied"

        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "failed"
        assert row["failure_reason"] == "payment_already_applied"

    def test_skipped_when_no_erp_connected(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-SK", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        window = service.open_window(
            ap_item_id="AP-SK",
            erp_reference="BILL-SK",
            erp_type="xero",
        )

        async def _fake_reverse_bill(*args, **kwargs):
            return {"status": "skipped", "reason": "no_erp_connected"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill",
            _fake_reverse_bill,
        )

        outcome = asyncio.run(
            service.attempt_reversal(
                window_id=window["id"],
                actor_id="user@test",
                reason="override",
            )
        )
        assert outcome.status == "skipped"
        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "failed"

    def test_expire_window_marks_pending_only(self, tmp_db, monkeypatch):
        from clearledgr.services.override_window import OverrideWindowService
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-EX2", organization_id="org_t")
        service = OverrideWindowService("org_t", db=tmp_db)
        window = service.open_window(
            ap_item_id="AP-EX2",
            erp_reference="BILL-EX2",
            erp_type="xero",
        )
        self._setup_workflow_stub(monkeypatch)

        ok = service.expire_window(window["id"])
        assert ok is True
        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "expired"

        # Idempotent — second call is a no-op
        ok2 = service.expire_window(window["id"])
        assert ok2 is False


# ===========================================================================
# Slack card builders (pure functions)
# ===========================================================================


class TestSlackCardBuilders:

    def _ap_item(self) -> Dict[str, Any]:
        return {
            "id": "AP-CARD-1",
            "vendor_name": "Acme Inc",
            "amount": 1234.56,
            "currency": "USD",
            "invoice_number": "INV-1001",
        }

    def _window(self) -> Dict[str, Any]:
        return {
            "id": "ovw_card1",
            "ap_item_id": "AP-CARD-1",
            "erp_type": "quickbooks",
            "erp_reference": "QBO-42",
            "expires_at": _now_iso(14),
        }

    def test_build_undo_post_card_includes_undo_button(self):
        from clearledgr.services.slack_cards import build_undo_post_card
        blocks = build_undo_post_card(ap_item=self._ap_item(), window=self._window())
        # Find the actions block
        actions_block = next((b for b in blocks if b.get("type") == "actions"), None)
        assert actions_block is not None
        button = actions_block["elements"][0]
        assert button["type"] == "button"
        assert button["style"] == "danger"
        assert button["action_id"] == "undo_post_ovw_card1"
        assert button["value"] == "ovw_card1"
        # Confirm dialog must be present so misclicks don't fire reversal
        assert "confirm" in button

    def test_undo_card_displays_amount_and_vendor(self):
        from clearledgr.services.slack_cards import build_undo_post_card
        blocks = build_undo_post_card(ap_item=self._ap_item(), window=self._window())
        all_text = json.dumps(blocks)
        assert "Acme Inc" in all_text
        assert "1,234.56" in all_text
        assert "QBO-42" in all_text
        assert "QuickBooks" in all_text

    def test_reversed_card_omits_undo_button(self):
        from clearledgr.services.slack_cards import build_card_reversed
        blocks = build_card_reversed(
            ap_item=self._ap_item(),
            window=self._window(),
            actor_id="user@test",
            reversal_ref="rev-1",
            reversal_method="delete",
        )
        actions_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert actions_blocks == []
        text = json.dumps(blocks)
        assert "user@test" in text
        assert "Reversed" in text

    def test_finalized_card_shows_locked(self):
        from clearledgr.services.slack_cards import build_card_finalized
        blocks = build_card_finalized(ap_item=self._ap_item(), window=self._window())
        text = json.dumps(blocks)
        assert "Override window has closed" in text
        assert any(b.get("type") == "actions" for b in blocks) is False

    def test_failed_card_surfaces_reason_and_actor(self):
        from clearledgr.services.slack_cards import build_card_reversal_failed
        blocks = build_card_reversal_failed(
            ap_item=self._ap_item(),
            window=self._window(),
            actor_id="user@test",
            failure_reason="payment_already_applied",
            failure_message="Payment was allocated",
        )
        text = json.dumps(blocks)
        assert "payment_already_applied" in text
        assert "Payment was allocated" in text
        assert "user@test" in text
        assert "Manual intervention" in text


# ===========================================================================
# approval_action_contract — undo_post parsing
# ===========================================================================


class TestUndoPostActionContract:

    def test_canonical_action_recognized(self):
        from clearledgr.core.approval_action_contract import _canonical_slack_action
        action, variant = _canonical_slack_action("undo_post_ovw_xyz123")
        assert action == "undo_post"
        assert variant is None

    def test_normalize_slack_action_extracts_window_id_into_gmail_id_field(self):
        """The contract overloads gmail_id to carry the lookup key. The
        handler is responsible for treating it as the override window id
        when action == 'undo_post'."""
        from clearledgr.core.approval_action_contract import normalize_slack_action

        payload = {
            "actions": [
                {
                    "action_id": "undo_post_ovw_xyz123",
                    "value": "ovw_xyz123",
                }
            ],
            "user": {"id": "U999", "name": "tester"},
            "channel": {"id": "C111"},
            "message": {"ts": "1234.5678"},
        }
        normalized = normalize_slack_action(
            payload, request_ts="1234.5678", organization_id="org_t"
        )
        assert normalized.action == "undo_post"
        assert normalized.gmail_id == "ovw_xyz123"
        assert normalized.actor_id == "U999"


# ===========================================================================
# OverrideWindowObserver
# ===========================================================================


class TestOverrideWindowObserver:

    def test_observer_skips_non_posted_states(self, tmp_db):
        from clearledgr.services.state_observers import (
            OverrideWindowObserver,
            StateTransitionEvent,
        )
        observer = OverrideWindowObserver(tmp_db)
        event = StateTransitionEvent(
            ap_item_id="AP-1",
            organization_id="org_t",
            old_state="needs_approval",
            new_state="approved",
        )
        # Should not raise; should not write any window row
        asyncio.run(observer.on_transition(event))
        assert tmp_db.get_override_window_by_ap_item_id("AP-1") is None

    def test_observer_creates_window_on_posted_to_erp(self, tmp_db, monkeypatch):
        from clearledgr.services.state_observers import (
            OverrideWindowObserver,
            StateTransitionEvent,
        )
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-OB-1", organization_id="org_t")

        # Stub slack post so we don't hit the network
        async def _fake_post(*args, **kwargs):
            return {"channel": "C123", "message_ts": "ts-1"}

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.post_undo_card_for_window",
            _fake_post,
        )

        observer = OverrideWindowObserver(tmp_db)
        event = StateTransitionEvent(
            ap_item_id="AP-OB-1",
            organization_id="org_t",
            old_state="ready_to_post",
            new_state="posted_to_erp",
        )
        asyncio.run(observer.on_transition(event))

        window = tmp_db.get_override_window_by_ap_item_id("AP-OB-1")
        assert window is not None
        assert window["state"] == "pending"
        assert window["slack_channel"] == "C123"
        assert window["slack_message_ts"] == "ts-1"

    def test_observer_records_action_type_erp_post(self, tmp_db, monkeypatch):
        """The observer reacts to posted_to_erp transitions, so it must
        record action_type='erp_post' on the window row regardless of
        the org's per-action duration config."""
        from clearledgr.services.state_observers import (
            OverrideWindowObserver,
            StateTransitionEvent,
        )
        _seed_org(
            tmp_db,
            "org_t",
            settings={
                "workflow_controls": {
                    "override_window_minutes": {
                        "erp_post": 7,
                        "payment_execution": 120,
                    }
                }
            },
        )
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-ACT", organization_id="org_t")

        async def _fake_post(*args, **kwargs):
            return {"channel": "C1", "message_ts": "ts1"}

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.post_undo_card_for_window",
            _fake_post,
        )

        observer = OverrideWindowObserver(tmp_db)
        event = StateTransitionEvent(
            ap_item_id="AP-ACT",
            organization_id="org_t",
            old_state="ready_to_post",
            new_state="posted_to_erp",
        )
        asyncio.run(observer.on_transition(event))

        window = tmp_db.get_override_window_by_ap_item_id("AP-ACT")
        assert window is not None
        assert window["action_type"] == "erp_post"
        # Duration must be 7 min (the erp_post entry), not 120 (payment_execution)
        from datetime import datetime as _dt
        posted_dt = _dt.fromisoformat(window["posted_at"].replace("Z", "+00:00"))
        expires_dt = _dt.fromisoformat(window["expires_at"].replace("Z", "+00:00"))
        delta_min = (expires_dt - posted_dt).total_seconds() / 60
        assert 6.5 <= delta_min <= 7.5

    def test_observer_skips_when_no_erp_reference(self, tmp_db, monkeypatch):
        from clearledgr.services.state_observers import (
            OverrideWindowObserver,
            StateTransitionEvent,
        )
        _seed_org(tmp_db, "org_t")
        # Create AP item WITHOUT erp_reference
        tmp_db.create_ap_item(
            {
                "id": "AP-NOREF",
                "organization_id": "org_t",
                "vendor_name": "X",
                "amount": 100.0,
                "currency": "USD",
                "state": "posted_to_erp",
                "thread_id": "thread-noref",
                "invoice_number": "INV-NR",
            }
        )

        observer = OverrideWindowObserver(tmp_db)
        event = StateTransitionEvent(
            ap_item_id="AP-NOREF",
            organization_id="org_t",
            old_state="ready_to_post",
            new_state="posted_to_erp",
        )
        asyncio.run(observer.on_transition(event))
        assert tmp_db.get_override_window_by_ap_item_id("AP-NOREF") is None


# ===========================================================================
# Background reaper
# ===========================================================================


class TestReaper:

    def test_reaper_finalizes_expired_windows(self, tmp_db, monkeypatch):
        from clearledgr.services.agent_background import (
            reap_expired_override_windows,
        )
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-RE-1", organization_id="org_t")

        # Create one expired + one still-pending window
        expired = tmp_db.create_override_window(
            ap_item_id="AP-RE-1",
            organization_id="org_t",
            erp_reference="BILL-RE-1",
            erp_type="xero",
            expires_at=_now_iso(-5),
        )
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-RE-2", organization_id="org_t")
        pending = tmp_db.create_override_window(
            ap_item_id="AP-RE-2",
            organization_id="org_t",
            erp_reference="BILL-RE-2",
            erp_type="xero",
            expires_at=_now_iso(60),
        )

        # Stub Slack card finalize so we don't hit the network
        async def _fake_finalize(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.update_card_to_finalized",
            _fake_finalize,
        )
        # Stub the workflow get_invoice_workflow used by service.expire_window
        fake_workflow = MagicMock()
        fake_workflow._transition_invoice_state = MagicMock(return_value=True)
        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda org_id: fake_workflow,
        )

        reaped = asyncio.run(reap_expired_override_windows())
        assert reaped == 1

        expired_row = tmp_db.get_override_window(expired["id"])
        pending_row = tmp_db.get_override_window(pending["id"])
        assert expired_row["state"] == "expired"
        assert pending_row["state"] == "pending"

    def test_reaper_handles_slack_failure_gracefully(self, tmp_db, monkeypatch):
        from clearledgr.services.agent_background import (
            reap_expired_override_windows,
        )
        _seed_org(tmp_db, "org_t")
        _seed_posted_ap_item(tmp_db, ap_item_id="AP-RE-3", organization_id="org_t")
        window = tmp_db.create_override_window(
            ap_item_id="AP-RE-3",
            organization_id="org_t",
            erp_reference="BILL-RE-3",
            erp_type="xero",
            expires_at=_now_iso(-5),
        )

        async def _broken_finalize(*args, **kwargs):
            raise RuntimeError("simulated slack outage")

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.update_card_to_finalized",
            _broken_finalize,
        )
        fake_workflow = MagicMock()
        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda org_id: fake_workflow,
        )

        reaped = asyncio.run(reap_expired_override_windows())
        # Window should still be reaped even if Slack failed
        assert reaped == 1
        row = tmp_db.get_override_window(window["id"])
        assert row["state"] == "expired"


# ===========================================================================
# REST API: POST /api/ap/items/{id}/reverse
# ===========================================================================


class TestReverseAPItemEndpoint:

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        import importlib
        import main

        db = ClearledgrDB(db_path=str(tmp_path / "reverse_api.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _override_user(self, main, role: str = "admin", org_id: str = "org_t"):
        from clearledgr.core.auth import TokenData, get_current_user, require_ops_user
        from datetime import datetime, timezone

        def _user():
            return TokenData(
                user_id="user_1",
                email="user@test",
                organization_id=org_id,
                role=role,
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        main.app.dependency_overrides[require_ops_user] = _user

    def test_404_when_no_window_exists(self, app_client, monkeypatch):
        client, main, db = app_client
        _seed_org(db, "org_t")
        _seed_posted_ap_item(db, ap_item_id="AP-API-1", organization_id="org_t")
        self._override_user(main)
        try:
            resp = client.post(
                "/api/ap/items/AP-API-1/reverse?organization_id=org_t",
                json={"reason": "human_override"},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == "no_override_window"
        finally:
            main.app.dependency_overrides.clear()

    def test_happy_path_reverses(self, app_client, monkeypatch):
        client, main, db = app_client
        _seed_org(db, "org_t")
        _seed_posted_ap_item(db, ap_item_id="AP-API-2", organization_id="org_t")
        db.create_override_window(
            ap_item_id="AP-API-2",
            organization_id="org_t",
            erp_reference="BILL-API-2",
            erp_type="xero",
            expires_at=_now_iso(15),
        )

        async def _fake_reverse_bill(*args, **kwargs):
            return {
                "status": "success",
                "erp": "xero",
                "reference_id": kwargs.get("erp_reference"),
                "reversal_method": "void",
                "reversal_ref": kwargs.get("erp_reference"),
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill",
            _fake_reverse_bill,
        )

        async def _fake_update(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.update_card_to_reversed",
            _fake_update,
        )
        fake_workflow = MagicMock()
        fake_workflow._transition_invoice_state = MagicMock(return_value=True)
        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda org_id: fake_workflow,
        )

        self._override_user(main)
        try:
            resp = client.post(
                "/api/ap/items/AP-API-2/reverse?organization_id=org_t",
                json={"reason": "duplicate_post"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "reversed"
            assert body["reversal_method"] == "void"
            assert body["erp"] == "xero"
        finally:
            main.app.dependency_overrides.clear()

    def test_410_when_window_expired(self, app_client, monkeypatch):
        client, main, db = app_client
        _seed_org(db, "org_t")
        _seed_posted_ap_item(db, ap_item_id="AP-API-3", organization_id="org_t")
        db.create_override_window(
            ap_item_id="AP-API-3",
            organization_id="org_t",
            erp_reference="BILL-API-3",
            erp_type="xero",
            expires_at=_now_iso(-1),
        )

        async def _fake_update(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.update_card_to_finalized",
            _fake_update,
        )
        fake_workflow = MagicMock()
        monkeypatch.setattr(
            "clearledgr.services.invoice_workflow.get_invoice_workflow",
            lambda org_id: fake_workflow,
        )

        self._override_user(main)
        try:
            resp = client.post(
                "/api/ap/items/AP-API-3/reverse?organization_id=org_t",
                json={"reason": "human_override"},
            )
            assert resp.status_code == 410
            assert resp.json()["detail"] == "override_window_expired"
        finally:
            main.app.dependency_overrides.clear()

    def test_502_when_erp_rejects(self, app_client, monkeypatch):
        client, main, db = app_client
        _seed_org(db, "org_t")
        _seed_posted_ap_item(db, ap_item_id="AP-API-4", organization_id="org_t")
        db.create_override_window(
            ap_item_id="AP-API-4",
            organization_id="org_t",
            erp_reference="BILL-API-4",
            erp_type="xero",
            expires_at=_now_iso(15),
        )

        async def _fake_reverse_bill(*args, **kwargs):
            return {
                "status": "error",
                "erp": "xero",
                "reason": "payment_already_applied",
                "erp_error_detail": "Payment was allocated",
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill",
            _fake_reverse_bill,
        )

        async def _fake_update(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clearledgr.services.slack_cards.update_card_to_reversal_failed",
            _fake_update,
        )

        self._override_user(main)
        try:
            resp = client.post(
                "/api/ap/items/AP-API-4/reverse?organization_id=org_t",
                json={"reason": "human_override"},
            )
            assert resp.status_code == 502
            detail = resp.json()["detail"]
            assert detail["error"] == "reversal_failed"
            assert detail["reason"] == "payment_already_applied"
        finally:
            main.app.dependency_overrides.clear()

    def test_400_when_reason_missing(self, app_client, monkeypatch):
        client, main, db = app_client
        _seed_org(db, "org_t")
        _seed_posted_ap_item(db, ap_item_id="AP-API-5", organization_id="org_t")
        self._override_user(main)
        try:
            resp = client.post(
                "/api/ap/items/AP-API-5/reverse?organization_id=org_t",
                json={"reason": ""},
            )
            assert resp.status_code == 422  # pydantic validation error
        finally:
            main.app.dependency_overrides.clear()
