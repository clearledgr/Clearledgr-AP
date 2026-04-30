"""Tests for annotation targets + dispatch (Gap 5).

Covers:

* Target registry: all 5 targets register at import.
* Annotation-prefix handler is registered with the outbox.
* `AnnotationDispatchObserver` reads policy, enqueues per active target.
* Disabled targets are skipped.
* Each target's `apply` skips correctly for irrelevant transitions
  (Gmail target on ERP-native, NetSuite target on Gmail-source bills,
  etc.) and returns the right `AnnotationResult`.
* Outbox handler resolves target='annotation:<type>' to the registered
  instance and persists the audit row.
* `annotation_targets` is the 7th policy kind, default + slice + merge
  round-trip cleanly.

No Postgres / Docker — pure logic + mocks.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ─── Registry ──────────────────────────────────────────────────────


def test_all_five_targets_registered_at_import():
    import clearledgr.services.annotation_targets  # noqa: F401
    from clearledgr.services.annotation_targets.base import list_registered_targets
    targets = list_registered_targets()
    for expected in [
        "gmail_label", "netsuite_custom_field", "sap_z_field",
        "customer_webhook", "slack_card_update",
    ]:
        assert expected in targets, f"{expected} not registered"


def test_annotation_handler_registered_at_import():
    import clearledgr.services.annotation_targets  # noqa: F401
    from clearledgr.services.outbox import list_handlers
    assert "annotation" in list_handlers()


def test_unknown_target_returns_none():
    from clearledgr.services.annotation_targets.base import get_target
    assert get_target("nonexistent_target") is None


# ─── Policy: annotation_targets is the 7th kind ────────────────────


def test_annotation_targets_is_a_policy_kind():
    from clearledgr.services.policy_service import POLICY_KINDS
    assert "annotation_targets" in POLICY_KINDS


def test_default_annotation_targets_all_disabled():
    from clearledgr.services.policy_service import _default_content
    default = _default_content("annotation_targets")
    assert default["gmail_label"]["enabled"] is False
    assert default["netsuite_custom_field"]["enabled"] is False
    assert default["sap_z_field"]["enabled"] is False
    assert default["customer_webhook"]["enabled"] is False
    assert default["slack_card_update"]["enabled"] is False


def test_annotation_targets_slice_and_merge_round_trip():
    from clearledgr.services.policy_service import (
        _slice_settings_for_kind, _merge_kind_into_settings,
    )
    fixture = {
        "gmail_label": {"enabled": True},
        "netsuite_custom_field": {"enabled": True, "field_id": "custom_field"},
    }
    settings: Dict[str, Any] = {}
    _merge_kind_into_settings("annotation_targets", fixture, settings)
    assert settings["annotation_targets"] == fixture
    sliced = _slice_settings_for_kind("annotation_targets", settings)
    assert sliced == fixture


# ─── GmailLabelTarget ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gmail_label_skips_for_erp_native():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.gmail_label import GmailLabelTarget
    target = GmailLabelTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="netsuite", erp_native=True,
        metadata={}, target_config={"enabled": True},
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "not_gmail_source"


@pytest.mark.asyncio
async def test_gmail_label_skips_for_non_gmail_source():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.gmail_label import GmailLabelTarget
    target = GmailLabelTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="x", new_state="y", actor_id=None, correlation_id=None,
        source_type="outlook", erp_native=False,
        metadata={}, target_config={"enabled": True},
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "not_gmail_source"


# ─── NetSuiteCustomFieldTarget ────────────────────────────────────


@pytest.mark.asyncio
async def test_netsuite_target_skips_for_gmail_source():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.netsuite_custom_field import (
        NetSuiteCustomFieldTarget,
    )
    target = NetSuiteCustomFieldTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={"enabled": True},
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "not_netsuite_source"


@pytest.mark.asyncio
async def test_netsuite_target_skips_when_no_ns_id():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.netsuite_custom_field import (
        NetSuiteCustomFieldTarget,
    )
    target = NetSuiteCustomFieldTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="x", new_state="y", actor_id=None, correlation_id=None,
        source_type="netsuite", erp_native=True,
        metadata={},  # no ns_internal_id
        target_config={"enabled": True},
    )
    db = MagicMock()
    db.get_ap_item.return_value = {}  # no erp_reference either
    with patch("clearledgr.services.annotation_targets.netsuite_custom_field.get_db", return_value=db) if False else patch.object(
        __import__("clearledgr.core.database", fromlist=["get_db"]), "get_db", return_value=db,
    ):
        # The netsuite target imports get_db inside _extract_ns_id; the
        # patch above is broad enough.
        result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "missing_ns_internal_id"


# ─── SapZFieldTarget ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sap_target_skips_for_non_sap_source():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.sap_z_field import SapZFieldTarget
    target = SapZFieldTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="x", new_state="y", actor_id=None, correlation_id=None,
        source_type="netsuite", erp_native=True,
        metadata={}, target_config={"enabled": True},
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "not_sap_source"


@pytest.mark.asyncio
async def test_sap_target_skips_without_composite_key():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.sap_z_field import SapZFieldTarget
    target = SapZFieldTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="x", new_state="y", actor_id=None, correlation_id=None,
        source_type="sap_s4hana", erp_native=True,
        metadata={},  # missing CC/Doc/FY
        target_config={"enabled": True},
    )
    db = MagicMock()
    db.get_ap_item.return_value = {"erp_reference": ""}
    with patch.object(
        __import__("clearledgr.core.database", fromlist=["get_db"]), "get_db", return_value=db,
    ):
        result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "missing_composite_key"


# ─── CustomerWebhookTarget ────────────────────────────────────────


@pytest.mark.asyncio
async def test_customer_webhook_filters_by_event_type():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.customer_webhook import CustomerWebhookTarget
    target = CustomerWebhookTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={
            "enabled": True,
            "filter_event_types": ["state.posted_to_erp"],  # not 'state.validated'
        },
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "event_filtered_out"


@pytest.mark.asyncio
async def test_customer_webhook_skipped_when_no_subscriptions():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.customer_webhook import CustomerWebhookTarget
    target = CustomerWebhookTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={"enabled": True},
    )
    with patch.object(CustomerWebhookTarget, "_fetch_active_subscriptions", return_value=[]):
        result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "no_active_subscriptions"


def test_customer_webhook_signature_format():
    """v1 signatures are HMAC-SHA256 hex of (timestamp + '.' + body)."""
    from clearledgr.services.annotation_targets.customer_webhook import CustomerWebhookTarget
    sig = CustomerWebhookTarget._sign("12345", b'{"a":1}', "secret")
    # 64 chars hex
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)
    # Stable: same inputs produce same output
    assert sig == CustomerWebhookTarget._sign("12345", b'{"a":1}', "secret")


# ─── SlackCardUpdateTarget ────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_card_update_skips_for_no_state_change():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.slack_card_update import (
        SlackCardUpdateTarget,
    )
    target = SlackCardUpdateTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="validated", new_state="validated",  # no-op
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={"enabled": True},
    )
    result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "no_state_change"


@pytest.mark.asyncio
async def test_slack_card_update_skips_when_no_thread_recorded():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.slack_card_update import (
        SlackCardUpdateTarget,
    )
    target = SlackCardUpdateTarget()
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={"enabled": True},
    )
    db = MagicMock()
    db.get_slack_thread.return_value = None
    with patch.object(
        __import__("clearledgr.core.database", fromlist=["get_db"]),
        "get_db", return_value=db,
    ):
        result = await target.apply(ctx)
    assert result.status == "skipped"
    assert result.skip_reason == "no_slack_thread_for_box"


def test_slack_card_status_text_includes_emoji_and_actor():
    from clearledgr.services.annotation_targets.base import AnnotationContext
    from clearledgr.services.annotation_targets.slack_card_update import (
        SlackCardUpdateTarget,
    )
    ctx = AnnotationContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="needs_approval",
        actor_id="alice@example.com", correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, target_config={},
    )
    text = SlackCardUpdateTarget._build_status_text(ctx, show_actor=True)
    assert ":hourglass_flowing_sand:" in text
    assert "alice@example.com" in text
    text_no_actor = SlackCardUpdateTarget._build_status_text(ctx, show_actor=False)
    assert "alice@example.com" not in text_no_actor


# ─── AnnotationDispatchObserver ────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_observer_enqueues_per_active_target():
    """The dispatcher reads the org's annotation_targets policy and
    enqueues one outbox row per target whose ``enabled=True``."""
    from clearledgr.services.annotation_targets.base import (
        AnnotationDispatchObserver,
    )
    from clearledgr.services.state_observers import StateTransitionEvent

    db = MagicMock()
    observer = AnnotationDispatchObserver(db)
    event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org-1",
        old_state="received", new_state="validated",
        actor_id="alice", correlation_id="cor-1",
        source_type="gmail", erp_native=False,
        metadata={"vendor": "Acme"},
    )

    enqueued: List[Dict[str, Any]] = []

    def fake_enqueue(self, **kwargs):
        enqueued.append(kwargs)
        return f"OE-{len(enqueued)}"

    fake_resolved = {
        "gmail_label": {"enabled": True},
        "netsuite_custom_field": {"enabled": True, "field_id": "custom"},
        "customer_webhook": {"enabled": False},  # disabled — should NOT enqueue
    }
    with patch(
        "clearledgr.services.annotation_targets.base._resolve_active_targets",
    ) as mocker, patch("clearledgr.services.outbox.OutboxWriter.enqueue", fake_enqueue):
        # _resolve_active_targets returns only the enabled ones
        mocker.return_value = {k: v for k, v in fake_resolved.items() if v.get("enabled")}
        await observer.on_transition(event)

    assert len(enqueued) == 2
    targets = sorted(e["target"] for e in enqueued)
    assert targets == ["annotation:gmail_label", "annotation:netsuite_custom_field"]
    for e in enqueued:
        assert e["payload"]["new_state"] == "validated"
        assert e["payload"]["box_id"] == "AP-1"
        assert e["payload"]["target_type"] in {"gmail_label", "netsuite_custom_field"}


@pytest.mark.asyncio
async def test_dispatch_observer_noop_when_no_active_targets():
    from clearledgr.services.annotation_targets.base import (
        AnnotationDispatchObserver,
    )
    from clearledgr.services.state_observers import StateTransitionEvent

    db = MagicMock()
    observer = AnnotationDispatchObserver(db)
    event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org-1",
        old_state="x", new_state="y",
    )
    enqueued: List[Any] = []
    with patch(
        "clearledgr.services.annotation_targets.base._resolve_active_targets",
        return_value={},
    ), patch(
        "clearledgr.services.outbox.OutboxWriter.enqueue",
        side_effect=lambda *a, **kw: enqueued.append(kw),
    ):
        await observer.on_transition(event)
    assert enqueued == []


# ─── Outbox handler ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outbox_handler_dispatches_to_registered_target():
    from clearledgr.services.annotation_targets.base import (
        AnnotationContext, AnnotationResult, _outbox_handler_annotation,
        _TARGET_REGISTRY,
    )
    from clearledgr.services.outbox import OutboxEvent

    received: List[AnnotationContext] = []

    class FakeTarget:
        target_type = "_test_target"

        async def apply(self, ctx):
            received.append(ctx)
            return AnnotationResult(
                status="succeeded", applied_value=ctx.new_state,
            )

    saved = dict(_TARGET_REGISTRY)
    try:
        # Manually inject without going through register_target so we
        # can clean up cleanly.
        _TARGET_REGISTRY["_test_target"] = FakeTarget()

        ev = OutboxEvent(
            id="OE-1", organization_id="org-1",
            event_type="annotation.validated",
            target="annotation:_test_target",
            payload={
                "box_type": "ap_item", "box_id": "AP-1",
                "old_state": "received", "new_state": "validated",
                "actor_id": "alice", "correlation_id": "cor-1",
                "source_type": "gmail", "erp_native": False,
                "metadata": {}, "target_config": {"enabled": True},
                "target_type": "_test_target",
            },
            dedupe_key=None, parent_event_id=None,
            status="processing", attempts=0, max_attempts=5,
            next_attempt_at=None, last_attempted_at=None, succeeded_at=None,
            error_log=[], created_at="", updated_at="", created_by="system",
        )
        # Patch the audit-row write since we don't have a real DB.
        with patch(
            "clearledgr.services.annotation_targets.base._persist_annotation_attempt",
        ):
            await _outbox_handler_annotation(ev)
    finally:
        _TARGET_REGISTRY.clear()
        _TARGET_REGISTRY.update(saved)

    assert len(received) == 1
    ctx = received[0]
    assert ctx.box_id == "AP-1"
    assert ctx.new_state == "validated"
    assert ctx.target_config == {"enabled": True}


@pytest.mark.asyncio
async def test_outbox_handler_raises_on_unknown_target():
    from clearledgr.services.annotation_targets.base import (
        _TARGET_REGISTRY, _outbox_handler_annotation,
    )
    from clearledgr.services.outbox import OutboxEvent

    saved = dict(_TARGET_REGISTRY)
    try:
        _TARGET_REGISTRY.clear()
        ev = OutboxEvent(
            id="OE-1", organization_id="org-1",
            event_type="annotation.validated",
            target="annotation:not_registered",
            payload={"box_id": "AP-1", "new_state": "validated"},
            dedupe_key=None, parent_event_id=None,
            status="processing", attempts=0, max_attempts=5,
            next_attempt_at=None, last_attempted_at=None, succeeded_at=None,
            error_log=[], created_at="", updated_at="", created_by="system",
        )
        with pytest.raises(LookupError):
            await _outbox_handler_annotation(ev)
    finally:
        _TARGET_REGISTRY.clear()
        _TARGET_REGISTRY.update(saved)


@pytest.mark.asyncio
async def test_outbox_handler_persists_audit_row_for_skipped():
    """Even skipped attempts get audited so the ops view can show
    'this target was inactive for this transition' as a reason."""
    from clearledgr.services.annotation_targets.base import (
        AnnotationResult, _TARGET_REGISTRY, _outbox_handler_annotation,
    )
    from clearledgr.services.outbox import OutboxEvent

    persisted: List[Dict[str, Any]] = []

    class SkipTarget:
        target_type = "_test_skip"

        async def apply(self, ctx):
            return AnnotationResult(
                status="skipped", skip_reason="testing",
                metadata={"why": "test"},
            )

    saved = dict(_TARGET_REGISTRY)
    try:
        _TARGET_REGISTRY["_test_skip"] = SkipTarget()
        ev = OutboxEvent(
            id="OE-1", organization_id="org-1",
            event_type="annotation.x",
            target="annotation:_test_skip",
            payload={
                "box_type": "ap_item", "box_id": "AP-1",
                "old_state": "x", "new_state": "y",
                "source_type": "gmail", "erp_native": False,
                "target_config": {}, "metadata": {},
            },
            dedupe_key=None, parent_event_id=None,
            status="processing", attempts=0, max_attempts=5,
            next_attempt_at=None, last_attempted_at=None, succeeded_at=None,
            error_log=[], created_at="", updated_at="", created_by="system",
        )
        with patch(
            "clearledgr.services.annotation_targets.base._persist_annotation_attempt",
            side_effect=lambda **kw: persisted.append(kw),
        ):
            await _outbox_handler_annotation(ev)
    finally:
        _TARGET_REGISTRY.clear()
        _TARGET_REGISTRY.update(saved)

    assert len(persisted) == 1
    args = persisted[0]
    assert args["target_type"] == "_test_skip"
    assert args["result"].status == "skipped"
    assert args["error"] is None
