"""Tests for the versioned PolicyService (Gap 2).

Covers:

* Content hashing is stable across key ordering (so idempotent
  re-saves are detected).
* Slice extraction + merge per kind.
* Threshold band matcher.
* Replay strategies for approval_thresholds + gl_account_map.
* Validation of unknown kinds.
* PolicyService end-to-end with a mocked DB: append-only writes,
  idempotent no-op on duplicate content, rollback creates a new
  version linking to the parent.

Avoids Postgres dependencies — pure-logic tests + mocked DB for the
write paths.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ─── Hash stability ────────────────────────────────────────────────


def test_hash_stable_across_key_order():
    from clearledgr.services.policy_service import _hash_content
    assert _hash_content({"a": 1, "b": 2}) == _hash_content({"b": 2, "a": 1})


def test_hash_distinct_for_distinct_content():
    from clearledgr.services.policy_service import _hash_content
    assert _hash_content({"a": 1}) != _hash_content({"a": 2})


# ─── Slice + merge per kind ────────────────────────────────────────


def test_slice_approval_thresholds():
    from clearledgr.services.policy_service import _slice_settings_for_kind
    settings = {"approval_thresholds": [{"min_amount": 0, "max_amount": 1000}]}
    out = _slice_settings_for_kind("approval_thresholds", settings)
    assert out == {"thresholds": [{"min_amount": 0, "max_amount": 1000}]}


def test_slice_gl_account_map():
    from clearledgr.services.policy_service import _slice_settings_for_kind
    settings = {"gl_account_map": {"expenses": "6100", "ap": "2000"}}
    out = _slice_settings_for_kind("gl_account_map", settings)
    assert out == {"map": {"expenses": "6100", "ap": "2000"}}


def test_slice_confidence_gate_with_default():
    """When no confidence keys are set, returns the default 0.95 floor."""
    from clearledgr.services.policy_service import _slice_settings_for_kind
    out = _slice_settings_for_kind("confidence_gate", {})
    assert out == {"critical_field_confidence_threshold": 0.95}


def test_slice_confidence_gate_preserves_set_values():
    from clearledgr.services.policy_service import _slice_settings_for_kind
    settings = {"critical_field_confidence_threshold": 0.85, "confidence_gate_threshold": 0.80}
    out = _slice_settings_for_kind("confidence_gate", settings)
    assert out["critical_field_confidence_threshold"] == 0.85
    assert out["confidence_gate_threshold"] == 0.80


def test_merge_round_trips_for_every_kind():
    from clearledgr.services.policy_service import (
        _merge_kind_into_settings, _slice_settings_for_kind, POLICY_KINDS,
    )
    fixtures: Dict[str, Dict[str, Any]] = {
        "approval_thresholds": {"thresholds": [{"min_amount": 0, "max_amount": 100}]},
        "gl_account_map": {"map": {"expenses": "6100"}},
        "confidence_gate": {"critical_field_confidence_threshold": 0.95},
        "autonomy_policy": {"autonomy_actions": {"auto_post": {"max_amount": 500}}},
        "vendor_master_gate": {"vendor_master_gate": True},
    }
    for kind in POLICY_KINDS:
        settings: Dict[str, Any] = {}
        _merge_kind_into_settings(kind, fixtures[kind], settings)
        sliced = _slice_settings_for_kind(kind, settings)
        assert sliced == fixtures[kind], f"round-trip failed for kind={kind}"


# ─── Validation ────────────────────────────────────────────────────


def test_unknown_kind_raises():
    from clearledgr.services.policy_service import PolicyKindError, _validate_kind
    with pytest.raises(PolicyKindError):
        _validate_kind("not_a_real_kind")


# ─── Threshold band matching ───────────────────────────────────────


def test_match_threshold_band_picks_first_matching_rule():
    from clearledgr.services.policy_service import _match_threshold_band
    thresholds = [
        {"min_amount": 0, "max_amount": 1000, "label": "small"},
        {"min_amount": 1000, "max_amount": 10000, "label": "mid"},
        {"min_amount": 10000, "label": "large"},  # open-ended top
    ]
    assert _match_threshold_band(thresholds, 500, {"vendor_name": "x"}) == "small"
    assert _match_threshold_band(thresholds, 5000, {"vendor_name": "x"}) == "mid"
    assert _match_threshold_band(thresholds, 50000, {"vendor_name": "x"}) == "large"


def test_match_threshold_band_respects_vendor_filter():
    from clearledgr.services.policy_service import _match_threshold_band
    thresholds = [
        {"min_amount": 0, "max_amount": 10000, "label": "vip", "vendors": ["acme"]},
        {"min_amount": 0, "max_amount": 10000, "label": "default"},
    ]
    assert _match_threshold_band(thresholds, 500, {"vendor_name": "Acme"}) == "vip"
    assert _match_threshold_band(thresholds, 500, {"vendor_name": "Other Co"}) == "default"


# ─── Replay strategies ─────────────────────────────────────────────


def test_replay_approval_thresholds_flags_band_changes():
    """A bill currently routed under the 'small' band would route
    under 'mid' if the threshold ceiling moved from 1000 → 100."""
    from clearledgr.services.policy_service import _replay_approval_thresholds

    # Target version's thresholds: ceiling moved from 1000 to 100
    target_content = {"thresholds": [
        {"min_amount": 0, "max_amount": 100, "label": "small"},
        {"min_amount": 100, "max_amount": 10000, "label": "mid"},
        {"min_amount": 10000, "label": "large"},
    ]}
    ap_items = [
        # Currently routed under 'small' band; under target, would route 'mid'
        {"id": "AP-1", "amount": 500, "vendor_name": "x",
         "metadata": {"approval_target": {"threshold_label": "small"}}},
        # Currently routed under 'large'; under target, still 'large'
        {"id": "AP-2", "amount": 50000, "vendor_name": "x",
         "metadata": {"approval_target": {"threshold_label": "large"}}},
        # Skipped — no amount
        {"id": "AP-3", "amount": None, "vendor_name": "x", "metadata": {}},
    ]
    deltas, summary = _replay_approval_thresholds(target_content, ap_items)
    assert summary["would_change"] == 1
    assert summary["no_change"] == 1
    assert summary["skipped"] == 1
    assert len(deltas) == 1
    assert deltas[0].ap_item_id == "AP-1"
    assert deltas[0].current_value == "small"
    assert deltas[0].replayed_value == "mid"


def test_replay_approval_thresholds_metadata_as_string():
    """Metadata stored as a JSON string (Postgres TEXT) is also handled."""
    import json
    from clearledgr.services.policy_service import _replay_approval_thresholds
    target_content = {"thresholds": [
        {"min_amount": 0, "max_amount": 100, "label": "tiny"},
        {"min_amount": 100, "label": "rest"},
    ]}
    ap_items = [
        {"id": "AP-1", "amount": 50, "vendor_name": "x",
         "metadata": json.dumps({"approval_target": {"threshold_label": "old_band"}})},
    ]
    deltas, summary = _replay_approval_thresholds(target_content, ap_items)
    # Metadata-string path is exercised by _extract_current_band — the
    # key thing is that it doesn't crash and produces a delta because
    # 'tiny' != 'old_band'.
    assert summary["would_change"] == 1
    assert deltas[0].current_value == "old_band"
    assert deltas[0].replayed_value == "tiny"


def test_replay_gl_account_map_flags_account_changes():
    """An item posted under '6100' that would have gone to '7000' under
    the target map shows up as a delta."""
    from clearledgr.services.policy_service import _replay_gl_account_map
    target_content = {"map": {"expenses": "7000"}}
    ap_items = [
        # State == posted_to_erp + GL was 6100 → would change to 7000
        {"id": "AP-1", "state": "posted_to_erp",
         "metadata": {"posting_metadata": {"gl_account": "6100"}}},
        # State == posted_to_erp + GL was 7000 → no change
        {"id": "AP-2", "state": "closed",
         "metadata": {"posting_metadata": {"gl_account": "7000"}}},
        # State != posted → skipped
        {"id": "AP-3", "state": "needs_approval", "metadata": {}},
        # No GL on metadata → skipped
        {"id": "AP-4", "state": "posted_to_erp", "metadata": {}},
    ]
    deltas, summary = _replay_gl_account_map(target_content, ap_items)
    assert summary["would_change"] == 1
    assert summary["no_change"] == 1
    assert summary["skipped"] == 2
    assert deltas[0].ap_item_id == "AP-1"
    assert deltas[0].current_value == "6100"
    assert deltas[0].replayed_value == "7000"


# ─── PolicyService write paths with mocked DB ─────────────────────


def _make_mock_db():
    """Build a MagicMock DB with the methods PolicyService reads/writes."""
    db = MagicMock()
    db.initialize = MagicMock()
    # In-memory store for policy_versions
    rows: List[Dict[str, Any]] = []

    class _FakeCursor:
        def __init__(self):
            self._last = None

        def execute(self, sql: str, params=None):
            sql_lower = sql.strip().lower()
            params = params or ()
            if sql_lower.startswith("select coalesce(max(version_number)"):
                org, kind = params
                matching = [r for r in rows if r["organization_id"] == org and r["policy_kind"] == kind]
                self._last = ({"coalesce": max((r["version_number"] for r in matching), default=0)},)
            elif sql_lower.startswith("select * from policy_versions where organization_id") and "limit 1" in sql_lower:
                org, kind = params
                matching = sorted(
                    [r for r in rows if r["organization_id"] == org and r["policy_kind"] == kind],
                    key=lambda r: r["version_number"], reverse=True,
                )
                self._last = (matching[0],) if matching else ()
            elif sql_lower.startswith("select * from policy_versions where organization_id") and "order by version_number desc limit %s" in sql_lower:
                org, kind, limit = params
                matching = sorted(
                    [r for r in rows if r["organization_id"] == org and r["policy_kind"] == kind],
                    key=lambda r: r["version_number"], reverse=True,
                )[:limit]
                self._last = tuple(matching)
            elif sql_lower.startswith("select * from policy_versions where id"):
                version_id, org = params
                matching = [r for r in rows if r["id"] == version_id and r["organization_id"] == org]
                self._last = (matching[0],) if matching else ()
            elif sql_lower.startswith("insert into policy_versions"):
                (vid, org, kind, vnum, content_json, content_hash, created_at,
                 created_by, description, parent_version_id, is_rollback) = params
                rows.append({
                    "id": vid, "organization_id": org, "policy_kind": kind,
                    "version_number": vnum, "content_json": content_json,
                    "content_hash": content_hash, "created_at": created_at,
                    "created_by": created_by, "description": description,
                    "parent_version_id": parent_version_id, "is_rollback": is_rollback,
                })
                self._last = ()
            elif sql_lower.startswith("select * from ap_items"):
                self._last = ()
            else:
                self._last = ()

        def fetchone(self):
            return self._last[0] if self._last else None

        def fetchall(self):
            return list(self._last)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return _FakeCursor()

        def commit(self):
            pass

    db.connect = MagicMock(return_value=_FakeConn())
    db._policy_rows = rows  # for test inspection
    db.get_organization = MagicMock(return_value={"id": "org-1", "settings_json": {}})
    db.update_organization = MagicMock()
    return db


def test_policy_service_lazy_migration_on_first_read():
    """First read for an org+kind that has no row creates v1 from
    settings_json."""
    from clearledgr.services.policy_service import PolicyService
    db = _make_mock_db()
    db.get_organization.return_value = {
        "id": "org-1",
        "settings_json": {
            "approval_thresholds": [{"min_amount": 0, "max_amount": 1000, "label": "small"}],
        },
    }
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        version = service.get_active("approval_thresholds")
    assert version.version_number == 1
    assert version.content == {"thresholds": [{"min_amount": 0, "max_amount": 1000, "label": "small"}]}
    assert version.created_by == "system:lazy_migration_v45"
    assert len(db._policy_rows) == 1


def test_policy_service_set_creates_new_version():
    from clearledgr.services.policy_service import PolicyService
    db = _make_mock_db()
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        v1 = service.set_policy(
            kind="approval_thresholds",
            content={"thresholds": [{"min_amount": 0, "label": "any"}]},
            actor="alice@example.com",
            description="initial",
        )
        v2 = service.set_policy(
            kind="approval_thresholds",
            content={"thresholds": [{"min_amount": 0, "max_amount": 1000, "label": "small"}]},
            actor="alice@example.com",
            description="tightened ceiling",
        )
    assert v1.version_number == 1
    assert v2.version_number == 2
    assert v2.parent_version_id == v1.id
    assert v2.is_rollback is False
    assert len(db._policy_rows) == 2


def test_policy_service_set_idempotent_on_duplicate_content():
    """Saving the same content twice doesn't inflate version_number."""
    from clearledgr.services.policy_service import PolicyService
    db = _make_mock_db()
    content = {"thresholds": [{"min_amount": 0, "label": "any"}]}
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        v1 = service.set_policy(kind="approval_thresholds", content=content, actor="alice")
        v2 = service.set_policy(kind="approval_thresholds", content=content, actor="alice")
    assert v1.id == v2.id
    assert v1.version_number == 1
    assert v2.version_number == 1
    assert len(db._policy_rows) == 1


def test_policy_service_rollback_creates_new_version_linking_to_target():
    """Rollback never mutates old rows; creates a new version with
    is_rollback=True and parent_version_id pointing at the target."""
    from clearledgr.services.policy_service import PolicyService
    db = _make_mock_db()
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        v1 = service.set_policy(
            kind="approval_thresholds",
            content={"thresholds": [{"min_amount": 0, "label": "v1"}]},
            actor="alice",
        )
        v2 = service.set_policy(
            kind="approval_thresholds",
            content={"thresholds": [{"min_amount": 0, "label": "v2"}]},
            actor="alice",
        )
        v3 = service.rollback_to(v1.id, actor="alice", description="restore v1")
    assert v3.version_number == 3
    assert v3.is_rollback is True
    assert v3.parent_version_id == v1.id
    assert v3.content == v1.content
    # Old versions untouched
    assert v1.content != v2.content
    assert len(db._policy_rows) == 3


def test_policy_service_unknown_kind_raises():
    from clearledgr.services.policy_service import PolicyKindError, PolicyService
    db = _make_mock_db()
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        with pytest.raises(PolicyKindError):
            service.get_active("not_a_real_kind")
        with pytest.raises(PolicyKindError):
            service.set_policy(kind="not_a_real_kind", content={}, actor="alice")


def test_policy_service_replay_unknown_version_raises():
    from clearledgr.services.policy_service import PolicyService, PolicyVersionNotFound
    db = _make_mock_db()
    with patch("clearledgr.services.policy_service.get_db", return_value=db):
        service = PolicyService("org-1")
        with pytest.raises(PolicyVersionNotFound):
            service.replay_against("PV-doesnotexist")
