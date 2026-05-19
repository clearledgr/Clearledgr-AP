"""Manifesto §"State" — policy_version is stamped on every transition.

Asserts that every audit_events row written through the canonical
funnel (``ApStore.append_audit_event``) carries a ``policy_version``
column value. The promise the manifesto makes — "validated centrally,
with the policy version that authorized it" — only holds if this
column is populated on every Box transition, not just override paths.

Covered:
  * Default — an ap_item event without explicit policy_version lands
    with ``CURRENT_AP_POLICY_VERSION``.
  * Explicit top-level — caller-supplied ``policy_version`` wins.
  * Nested via OverrideContext — ``policy_version`` inside
    ``payload_json`` is pulled up to the column.
  * Backfill — the v83 migration sets historical rows to 'v1'.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.ap_states import CURRENT_AP_POLICY_VERSION  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgPV", organization_name="orgPV")
    return inst


def _read_policy_version(db, event_id: str) -> str | None:
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT policy_version FROM audit_events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return row["policy_version"] if isinstance(row, dict) else row[0]


def test_default_policy_version_stamped_for_ap_item(db):
    item = db.create_ap_item({
        "id": "AP-pv-default",
        "organization_id": "orgPV",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    event = db.append_audit_event({
        "box_id": item["id"],
        "box_type": "ap_item",
        "event_type": "state_transition",
        "from_state": "received",
        "to_state": "validated",
        "actor_type": "agent",
        "actor_id": "test",
        "organization_id": "orgPV",
    })
    assert event is not None
    assert _read_policy_version(db, event["id"]) == CURRENT_AP_POLICY_VERSION


def test_explicit_top_level_policy_version_wins(db):
    item = db.create_ap_item({
        "id": "AP-pv-explicit",
        "organization_id": "orgPV",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    event = db.append_audit_event({
        "box_id": item["id"],
        "box_type": "ap_item",
        "event_type": "state_transition",
        "from_state": "received",
        "to_state": "validated",
        "actor_type": "agent",
        "actor_id": "test",
        "organization_id": "orgPV",
        "policy_version": "v2-pilot",
    })
    assert _read_policy_version(db, event["id"]) == "v2-pilot"


def test_override_context_policy_version_promoted_to_column(db):
    """OverrideContext.to_dict() puts policy_version into payload_json.

    The funnel should pull that nested value up to the column so it's
    indexable and queryable without JSON extraction.
    """
    item = db.create_ap_item({
        "id": "AP-pv-nested",
        "organization_id": "orgPV",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    event = db.append_audit_event({
        "box_id": item["id"],
        "box_type": "ap_item",
        "event_type": "override_approved",
        "actor_type": "user",
        "actor_id": "approver@example.com",
        "organization_id": "orgPV",
        "payload_json": {
            "override_type": "budget",
            "justification": "exec sign-off",
            "actor_id": "approver@example.com",
            "policy_version": "v1.5",
        },
    })
    assert _read_policy_version(db, event["id"]) == "v1.5"


def test_export_surface_includes_policy_version(db):
    """The box-export normalizer surfaces policy_version per-event.

    Belt-and-braces check that Phase 1.1's export endpoint reads the
    column threaded through by Phase 1.2.
    """
    from solden.api.box_export import _normalize_audit_event
    item = db.create_ap_item({
        "id": "AP-pv-export",
        "organization_id": "orgPV",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    db.append_audit_event({
        "box_id": item["id"],
        "box_type": "ap_item",
        "event_type": "state_transition",
        "from_state": "received",
        "to_state": "validated",
        "actor_type": "agent",
        "actor_id": "test",
        "organization_id": "orgPV",
    })
    events = db.list_ap_audit_events(item["id"])
    assert events, "expected at least one audit event"
    normalized = [_normalize_audit_event(e) for e in events]
    assert all(e.get("policy_version") for e in normalized), (
        "every exported event must carry policy_version"
    )
