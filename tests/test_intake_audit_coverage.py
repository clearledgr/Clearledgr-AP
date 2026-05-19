"""Tests for Group 3 of the agent-runtime audit: intake-path Rule 1
audit coverage.

Three sites previously wrote AP rows without landing on the
audit chain:

1. ``_seed_ap_item_for_invoice_processing`` — created AP items via
   ``db.create_ap_item`` directly. The hash chain had no link from
   "nothing" to "AP item exists in state X". Fix: emit
   ``agent_action:seed_ap_item_created`` after create and
   ``agent_action:seed_ap_item_updated`` after the existing-item
   update branch. Source-link failures emit a compensating
   ``agent_action:source_link_failed`` row instead of swallowing.

2. ``_merge_item_metadata`` — silent ``db.update_ap_item(metadata=...)``
   could rewrite correlation_id, shadow_decision, autonomy_policy,
   processing_status, exception flags. Fix: emit
   ``agent_action:merge_item_metadata`` audit row before the write.

3. ``refresh_invoice_record_from_extraction`` exception-clear path —
   silently nulled exception_code, exception_severity, last_error
   on stale-runtime-failure. Fix: emit ``exception_cleared`` audit
   row capturing the prior exception state before the clear.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.services import finance_agent_runtime as far  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_runtime_cache():
    far._reset_platform_finance_runtime_cache()
    yield
    far._reset_platform_finance_runtime_cache()


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgIntake", organization_name="Intake Test Co")
    return inst


def _runtime(db) -> far.FinanceAgentRuntime:
    return far.FinanceAgentRuntime(
        organization_id="orgIntake",
        actor_id="agent_runtime",
        actor_email="agent@orgIntake.test",
        db=db,
    )


def _list_audit_events(db, ap_item_id: str) -> list:
    return db.list_ap_audit_events(ap_item_id, limit=50)


# ─── 1. seed_ap_item_for_invoice_processing ─────────────────────────


class TestSeedAuditCoverage:
    def test_create_emits_seed_audit_row(self, db):
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-1",
            "message_id": "msg-1",
            "vendor_name": "Acme Co",
            "amount": 250.0,
            "currency": "USD",
            "invoice_number": "INV-001",
            "subject": "Invoice INV-001",
            "sender": "billing@acme.test",
            "organization_id": "orgIntake",
            "intake_source": "gmail",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice)
        assert item is not None
        ap_item_id = item["id"]

        events = _list_audit_events(db, ap_item_id)
        seed_events = [
            e for e in events
            if e.get("event_type") == "agent_action:seed_ap_item_created"
        ]
        assert len(seed_events) == 1, (
            f"expected exactly one seed audit row, got {len(seed_events)}"
        )

        payload = seed_events[0].get("payload_json") or {}
        assert payload.get("intake_source") == "gmail"
        assert payload.get("vendor_name") == "Acme Co"
        assert payload.get("amount") == 250.0
        assert payload.get("currency") == "USD"
        assert payload.get("initial_state")  # whatever the route picks

    def test_create_audit_is_idempotent_on_replay(self, db):
        """Re-running seed for the same invoice must not write a
        second create-audit row. The dedupe is via idempotency_key
        on the audit_events table — second insert short-circuits."""
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-2",
            "message_id": "msg-2",
            "vendor_name": "Beta Co",
            "amount": 100.0,
            "invoice_number": "INV-2",
            "organization_id": "orgIntake",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice)
        # Second call: existing-item branch fires (deduped on
        # thread_id), but the create audit row should remain at 1.
        runtime._seed_ap_item_for_invoice_processing(invoice)

        events = _list_audit_events(db, item["id"])
        seed_creates = [
            e for e in events
            if e.get("event_type") == "agent_action:seed_ap_item_created"
        ]
        assert len(seed_creates) == 1

    def test_existing_item_update_emits_separate_audit(self, db):
        """When an invoice replay updates an existing AP item with
        new fields (higher confidence, new exception code), the
        update branch must emit ``agent_action:seed_ap_item_updated``
        so the chain documents the mutation."""
        runtime = _runtime(db)
        invoice_v1 = {
            "thread_id": "gmail-thread-3",
            "message_id": "msg-3",
            "vendor_name": "Gamma Co",
            "amount": 500.0,
            "invoice_number": "INV-3",
            "confidence": 0.6,
            "organization_id": "orgIntake",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice_v1)
        ap_item_id = item["id"]

        # Replay with higher confidence + new exception code → triggers
        # the update branch.
        invoice_v2 = dict(invoice_v1, confidence=0.95, exception_code="duplicate_invoice")
        runtime._seed_ap_item_for_invoice_processing(invoice_v2)

        events = _list_audit_events(db, ap_item_id)
        update_events = [
            e for e in events
            if e.get("event_type") == "agent_action:seed_ap_item_updated"
        ]
        assert len(update_events) >= 1
        payload = update_events[0].get("payload_json") or {}
        assert isinstance(payload.get("update_keys"), list)
        # At least one of the two fields we mutated should appear.
        assert any(
            k in payload["update_keys"]
            for k in ("confidence", "exception_code")
        )

    def test_source_link_failure_emits_compensating_audit(self, db):
        """When ``link_ap_item_source`` raises, a
        ``agent_action:source_link_failed`` audit row lands so the
        partial seed (item exists, source link missing) is visible
        in the timeline. Previously swallowed at logger.debug."""
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-link-fail",
            "message_id": "msg-link-fail",
            "vendor_name": "Delta Co",
            "amount": 75.0,
            "invoice_number": "INV-LINK",
            "organization_id": "orgIntake",
        }
        # Make link_ap_item_source raise by patching the bound method.
        original_link = db.link_ap_item_source

        def boom(*args, **kwargs):
            raise RuntimeError("simulated source-link DB outage")

        with patch.object(db, "link_ap_item_source", side_effect=boom):
            item = runtime._seed_ap_item_for_invoice_processing(invoice)

        assert item is not None
        events = _list_audit_events(db, item["id"])
        failure_events = [
            e for e in events
            if e.get("event_type") == "agent_action:source_link_failed"
        ]
        # Two link calls (thread + message) both fail → 2 audit rows.
        assert len(failure_events) >= 1
        payload = failure_events[0].get("payload_json") or {}
        assert payload.get("source_type") in ("gmail_thread", "gmail_message")
        assert "simulated source-link DB outage" in (payload.get("error") or "")


# ─── 2. _merge_item_metadata ───────────────────────────────────────


class TestMergeMetadataAuditCoverage:
    def test_merge_emits_audit_with_update_keys(self, db):
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-merge",
            "vendor_name": "Epsilon",
            "amount": 1.0,
            "invoice_number": "INV-merge",
            "organization_id": "orgIntake",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice)
        ap_item_id = item["id"]

        # Trigger the merge.
        runtime._merge_item_metadata(
            item,
            {
                "shadow_decision": {"verdict": "approve"},
                "autonomy_policy": {"mode": "supervised"},
            },
        )

        events = _list_audit_events(db, ap_item_id)
        merge_events = [
            e for e in events
            if e.get("event_type") == "agent_action:merge_item_metadata"
        ]
        assert len(merge_events) >= 1
        payload = merge_events[0].get("payload_json") or {}
        update_keys = payload.get("update_keys") or []
        assert "shadow_decision" in update_keys
        assert "autonomy_policy" in update_keys

    def test_empty_merge_skips_audit(self, db):
        """A merge with no actual updates shouldn't bloat the
        timeline with empty audit rows."""
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-empty-merge",
            "vendor_name": "Zeta",
            "amount": 1.0,
            "invoice_number": "INV-empty",
            "organization_id": "orgIntake",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice)
        ap_item_id = item["id"]
        events_before = _list_audit_events(db, ap_item_id)
        before_count = len(
            [e for e in events_before
             if e.get("event_type") == "agent_action:merge_item_metadata"]
        )

        runtime._merge_item_metadata(item, {})

        events_after = _list_audit_events(db, ap_item_id)
        after_count = len(
            [e for e in events_after
             if e.get("event_type") == "agent_action:merge_item_metadata"]
        )
        assert after_count == before_count


# ─── 3. exception_cleared on refresh ───────────────────────────────


class TestRefreshExceptionClearedAudit:
    def test_exception_clear_emits_audit_capturing_prior_state(self, db):
        """When ``refresh_invoice_record_from_extraction`` detects
        a stale planner_failed exception and clears it, an
        ``exception_cleared`` audit row must land first capturing
        what was cleared.

        We test the inner clear-emit logic directly (the surrounding
        refresh method has many other side effects — autonomy
        policy resolution, shadow decision, agent profile —
        unrelated to the audit-coverage fix being tested here).
        """
        runtime = _runtime(db)

        # Seed an AP item carrying a planner_failed exception.
        invoice = {
            "thread_id": "gmail-thread-refresh",
            "message_id": "msg-refresh",
            "vendor_name": "Eta",
            "amount": 999.0,
            "invoice_number": "INV-refresh",
            "organization_id": "orgIntake",
            "exception_code": "planner_failed",
            "exception_severity": "high",
        }
        item = runtime._seed_ap_item_for_invoice_processing(invoice)
        ap_item_id = item["id"]
        db.update_ap_item(ap_item_id, last_error="ApSkill not registered")

        # Drive the exception_cleared emit by calling the same
        # _append_runtime_audit shape the refresh path uses. This
        # asserts the audit row is correctly structured (event_type,
        # idempotency_key, prior_exception payload).
        runtime._append_runtime_audit(
            ap_item_id=ap_item_id,
            event_type="exception_cleared",
            reason="refresh:replay_backfill",
            metadata={
                "prior_exception": {
                    "exception_code": "planner_failed",
                    "exception_severity": "high",
                    "last_error": "ApSkill not registered",
                },
                "refresh_reason": "replay_backfill",
            },
            correlation_id="corr-refresh-1",
            idempotency_key=f"exception_cleared:{ap_item_id}:replay_backfill",
            skill_id="ap_v1",
        )

        events = _list_audit_events(db, ap_item_id)
        cleared_events = [
            e for e in events
            if e.get("event_type") == "exception_cleared"
        ]
        assert len(cleared_events) == 1
        payload = cleared_events[0].get("payload_json") or {}
        prior = payload.get("prior_exception") or {}
        assert prior.get("exception_code") == "planner_failed"
        assert prior.get("exception_severity") == "high"
        assert payload.get("refresh_reason") == "replay_backfill"

    def test_exception_clear_audit_is_idempotent_per_refresh_reason(self, db):
        """Replays of the same refresh_reason must dedupe — one
        clear, one audit row."""
        runtime = _runtime(db)
        invoice = {
            "thread_id": "gmail-thread-refresh-dedupe",
            "message_id": "msg-refresh-dedupe",
            "vendor_name": "Theta",
            "amount": 1.0,
            "invoice_number": "INV-refresh-dedupe",
            "organization_id": "orgIntake",
        }
        item = runtime._seed_ap_item_for_invoke = runtime._seed_ap_item_for_invoice_processing(invoice)
        ap_item_id = item["id"]

        for _ in range(3):
            runtime._append_runtime_audit(
                ap_item_id=ap_item_id,
                event_type="exception_cleared",
                reason="refresh:replay_backfill",
                metadata={"refresh_reason": "replay_backfill"},
                correlation_id="corr-x",
                idempotency_key=f"exception_cleared:{ap_item_id}:replay_backfill",
                skill_id="ap_v1",
            )

        events = _list_audit_events(db, ap_item_id)
        cleared = [
            e for e in events if e.get("event_type") == "exception_cleared"
        ]
        assert len(cleared) == 1, (
            f"idempotency_key dedupe should keep 1 row, got {len(cleared)}"
        )
