"""Tests for the cryptographic hash chain on ``audit_events`` (v77).

Pinned by these tests:

  - Every new audit row has ``hash``, ``prev_hash``, and ``chain_seq``
    populated by the ``trg_audit_events_hash_chain`` BEFORE INSERT
    trigger. Application code never has to compute the hash.
  - The first row in an organization's chain has
    ``prev_hash = sha256("solden:audit:genesis:" || organization_id)``
    and ``chain_seq = 1``. The genesis sentinel is deterministic
    so any verifier can reproduce it without state.
  - Subsequent rows: ``prev_hash`` equals the prior row's ``hash``
    (chain linkage); ``chain_seq`` increments by 1.
  - The hash itself is ``sha256(prev_hash || "||" || canonical(row))``
    where ``canonical(row)`` is concat_ws('|', ...) over the
    immutable identity fields of the row. Tests recompute this
    canonical form in Python and assert byte-for-byte equality.
  - Different organizations have independent chains. Inserts into
    org A do not affect chain_seq or hash linkage in org B.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402


# ─── Helpers ────────────────────────────────────────────────────────


def _genesis_hash(organization_id: str) -> str:
    """Deterministic per-org genesis sentinel. Must match the trigger
    and the migration backfill exactly."""
    sentinel = f"solden:audit:genesis:{organization_id or ''}"
    return hashlib.sha256(sentinel.encode("utf-8")).hexdigest()


def _canonical_row(row: dict) -> str:
    """Pipe-separated identity fields. Must match the SQL trigger's
    concat_ws('|', ...) call exactly. Order matters."""
    fields = [
        row.get("id") or "",
        row.get("ts") or "",
        row.get("box_id") or "",
        row.get("box_type") or "",
        row.get("event_type") or "",
        row.get("prev_state") or "",
        row.get("new_state") or "",
        row.get("actor_type") or "",
        row.get("actor_id") or "",
        row.get("idempotency_key") or "",
        row.get("payload_json") or "",
        row.get("organization_id") or "",
    ]
    return "|".join(fields)


def _expected_hash(prev_hash: str, row: dict) -> str:
    """Reproduce the trigger's hash formula:
    sha256(prev_hash || "||" || canonical(row))"""
    canonical = _canonical_row(row)
    payload = f"{prev_hash}||{canonical}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fetch_raw(db, event_id: str) -> dict:
    """Return the raw audit_events row (with payload_json as the
    serialized string the trigger hashed, not the deserialized
    dict ``get_ap_audit_event`` returns)."""
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ts, box_id, box_type, event_type, prev_state, "
            "new_state, actor_type, actor_id, idempotency_key, "
            "payload_json, organization_id, prev_hash, hash, chain_seq "
            "FROM audit_events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


# ─── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Globex EU GmbH")
    return inst


def _ap_item(db, *, item_id: str, organization_id: str = "orgA"):
    return db.create_ap_item({
        "id": item_id,
        "organization_id": organization_id,
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "EUR",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
        "metadata": {},
    })


def _audit(db, *, ap_item_id: str, organization_id: str = "orgA",
           event_type: str = "test_event"):
    return db.append_audit_event({
        "ap_item_id": ap_item_id,
        "event_type": event_type,
        "organization_id": organization_id,
        "actor_type": "agent",
        "actor_id": "test",
    })


# ─── Tests ─────────────────────────────────────────────────────────


class TestChainBootstrap:
    """First insert in an org's chain."""

    def test_first_event_has_chain_seq_1(self, db):
        _ap_item(db, item_id="ap-genesis-1")
        evt = _audit(db, ap_item_id="ap-genesis-1")
        raw = _fetch_raw(db, evt["id"])
        assert raw["chain_seq"] == 1

    def test_first_event_prev_hash_is_genesis_sentinel(self, db):
        _ap_item(db, item_id="ap-genesis-2")
        evt = _audit(db, ap_item_id="ap-genesis-2")
        raw = _fetch_raw(db, evt["id"])
        assert raw["prev_hash"] == _genesis_hash("orgA")

    def test_first_event_hash_recomputes_correctly(self, db):
        _ap_item(db, item_id="ap-genesis-3")
        evt = _audit(db, ap_item_id="ap-genesis-3")
        raw = _fetch_raw(db, evt["id"])
        expected = _expected_hash(_genesis_hash("orgA"), raw)
        assert raw["hash"] == expected


class TestChainLinkage:
    """Two consecutive events: prev_hash linkage is correct."""

    def test_second_event_prev_hash_equals_first_hash(self, db):
        _ap_item(db, item_id="ap-link-1")
        evt1 = _audit(db, ap_item_id="ap-link-1", event_type="event_a")
        evt2 = _audit(db, ap_item_id="ap-link-1", event_type="event_b")
        r1 = _fetch_raw(db, evt1["id"])
        r2 = _fetch_raw(db, evt2["id"])
        assert r2["prev_hash"] == r1["hash"]

    def test_chain_seq_increments(self, db):
        _ap_item(db, item_id="ap-link-2")
        evt1 = _audit(db, ap_item_id="ap-link-2", event_type="event_a")
        evt2 = _audit(db, ap_item_id="ap-link-2", event_type="event_b")
        evt3 = _audit(db, ap_item_id="ap-link-2", event_type="event_c")
        seqs = [
            _fetch_raw(db, evt1["id"])["chain_seq"],
            _fetch_raw(db, evt2["id"])["chain_seq"],
            _fetch_raw(db, evt3["id"])["chain_seq"],
        ]
        # Within the test's org, these are the only audit events
        # written by us, but other tests in the same session may have
        # inserted earlier rows. Assert relative monotonicity instead
        # of absolute values.
        assert seqs[1] == seqs[0] + 1
        assert seqs[2] == seqs[1] + 1

    def test_hash_recomputes_for_chained_event(self, db):
        _ap_item(db, item_id="ap-link-3")
        evt1 = _audit(db, ap_item_id="ap-link-3", event_type="event_a")
        evt2 = _audit(db, ap_item_id="ap-link-3", event_type="event_b")
        r1 = _fetch_raw(db, evt1["id"])
        r2 = _fetch_raw(db, evt2["id"])
        expected = _expected_hash(r1["hash"], r2)
        assert r2["hash"] == expected


class TestPerOrgIsolation:
    """Different orgs have independent chains."""

    def test_orgs_have_independent_genesis_sentinels(self, db):
        _ap_item(db, item_id="ap-iso-a", organization_id="orgA")
        _ap_item(db, item_id="ap-iso-b", organization_id="orgB")
        evt_a = _audit(db, ap_item_id="ap-iso-a", organization_id="orgA")
        evt_b = _audit(db, ap_item_id="ap-iso-b", organization_id="orgB")
        ra = _fetch_raw(db, evt_a["id"])
        rb = _fetch_raw(db, evt_b["id"])
        # Different sentinels because organization_id differs.
        assert ra["prev_hash"] == _genesis_hash("orgA")
        assert rb["prev_hash"] == _genesis_hash("orgB")
        assert ra["prev_hash"] != rb["prev_hash"]

    def test_org_a_inserts_do_not_advance_org_b_chain(self, db):
        _ap_item(db, item_id="ap-iso-c", organization_id="orgA")
        _ap_item(db, item_id="ap-iso-d", organization_id="orgB")

        # Pin the head chain_seq for orgB before any orgA traffic.
        evt_b1 = _audit(db, ap_item_id="ap-iso-d", organization_id="orgB")
        b1_seq = _fetch_raw(db, evt_b1["id"])["chain_seq"]

        # Three orgA inserts in between.
        for i in range(3):
            _audit(db, ap_item_id="ap-iso-c",
                   organization_id="orgA", event_type=f"orga_{i}")

        # Next orgB insert: chain_seq should be exactly b1_seq + 1.
        evt_b2 = _audit(db, ap_item_id="ap-iso-d", organization_id="orgB")
        b2_seq = _fetch_raw(db, evt_b2["id"])["chain_seq"]
        assert b2_seq == b1_seq + 1
