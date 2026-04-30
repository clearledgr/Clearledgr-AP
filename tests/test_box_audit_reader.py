"""Generic Box audit reader.

``list_box_audit_events(box_type, box_id)`` is the canonical reader
for any Box type's audit trail. AP convenience ``list_ap_audit_events``
is a thin wrapper over it.
"""
from __future__ import annotations

import pytest


def _fresh_db(tmp_path, monkeypatch):
    import clearledgr.core.database as db_module
    db = db_module.get_db()
    db.initialize()
    return db


class TestListBoxAuditEvents:

    def test_ap_reader_returns_new_write(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        db.append_audit_event({
            "ap_item_id": "ap-r1",
            "event_type": "state_transition",
            "to_state": "validated",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })
        events = db.list_box_audit_events("ap_item", "ap-r1")
        assert len(events) == 1
        assert events[0].get("box_id") == "ap-r1"
        assert events[0].get("box_type") == "ap_item"

    def test_list_ap_audit_events_delegates_to_generic(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        db.append_audit_event({
            "ap_item_id": "ap-delegate",
            "event_type": "state_transition",
            "to_state": "validated",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })
        events = db.list_ap_audit_events("ap-delegate")
        assert len(events) == 1
        assert events[0].get("box_id") == "ap-delegate"

    @pytest.mark.skip(
        reason=(
            "vendor_onboarding_deferred_2026_04_30 "
            "— see memory/project_vendor_onboarding_subordinate.md"
        ),
    )
    def test_vendor_reader_uses_box_id(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        session = db.create_vendor_onboarding_session(
            organization_id="test-org",
            vendor_name="Acme",
            invited_by="ap@test-org",
        )
        sid = session["id"]
        db.transition_onboarding_session_state(sid, target_state="kyc", actor_id="agent")

        # AP reader cannot find it — wrong box_type.
        ap_view = db.list_box_audit_events("ap_item", sid)
        assert ap_view == []

        # Vendor reader finds it by box_id + box_type.
        events = db.list_box_audit_events("vendor_onboarding_session", sid)
        assert len(events) == 1
        assert events[0].get("event_type") == "vendor_onboarding_state_transition"

    @pytest.mark.skip(
        reason=(
            "vendor_onboarding_deferred_2026_04_30 "
            "— see memory/project_vendor_onboarding_subordinate.md"
        ),
    )
    def test_ap_reader_does_not_leak_vendor_rows(self, tmp_path, monkeypatch):
        """If a vendor onboarding session shares an id with an AP
        item (unlikely but possible), the reader must not mix them.
        The box_type filter enforces the separation.
        """
        db = _fresh_db(tmp_path, monkeypatch)
        db.append_audit_event({
            "ap_item_id": "id-collision",
            "event_type": "state_transition",
            "to_state": "validated",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })
        db.append_audit_event({
            "box_id": "id-collision",
            "box_type": "vendor_onboarding_session",
            "event_type": "vendor_onboarding_state_transition",
            "from_state": "invited",
            "to_state": "kyc",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })

        ap_events = db.list_box_audit_events("ap_item", "id-collision")
        vo_events = db.list_box_audit_events("vendor_onboarding_session", "id-collision")

        assert len(ap_events) == 1
        assert ap_events[0].get("event_type") == "state_transition"
        assert len(vo_events) == 1
        assert vo_events[0].get("event_type") == "vendor_onboarding_state_transition"

    def test_order_and_limit(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        for i in range(3):
            db.append_audit_event({
                "ap_item_id": "ap-multi",
                "event_type": f"event_{i}",
                "actor_type": "agent",
                "actor_id": "test",
                "organization_id": "test-org",
                "ts": f"2026-04-17T12:00:0{i}Z",
            })

        asc = db.list_box_audit_events("ap_item", "ap-multi", order="asc")
        desc = db.list_box_audit_events("ap_item", "ap-multi", order="desc")
        limited = db.list_box_audit_events("ap_item", "ap-multi", limit=2)

        assert [e["event_type"] for e in asc] == ["event_0", "event_1", "event_2"]
        assert [e["event_type"] for e in desc] == ["event_2", "event_1", "event_0"]
        assert len(limited) == 2
