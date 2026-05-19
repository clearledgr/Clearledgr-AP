"""Coverage for /v1/intents/execute idempotency helpers.

Tests the Stripe pattern: extract → hash → lookup → execute → store.
Per-function unit tests; the end-to-end wiring is exercised by the
v1_intents integration tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from solden.api import v1_idempotency
from solden.api.v1_idempotency import (
    extract_idempotency_key,
    hash_payload,
    lookup_cached_response,
    store_response,
)


# ─── extract_idempotency_key ────────────────────────────────────


def _stub_request(headers: Optional[Dict[str, str]] = None) -> Any:
    req = MagicMock()
    req.headers = headers or {}
    return req


def test_extract_prefers_header_over_body() -> None:
    """Header wins when both are present — that's the documented order."""
    assert extract_idempotency_key(
        _stub_request({"Idempotency-Key": "from-header"}),
        "from-body",
    ) == "from-header"


def test_extract_falls_back_to_body_when_header_missing() -> None:
    assert extract_idempotency_key(_stub_request(), "from-body") == "from-body"


def test_extract_returns_none_when_both_absent() -> None:
    assert extract_idempotency_key(_stub_request(), None) is None


def test_extract_treats_whitespace_only_as_absent() -> None:
    """A whitespace-only key is functionally absent — short-circuit the cache."""
    assert extract_idempotency_key(
        _stub_request({"Idempotency-Key": "   "}), "  "
    ) is None


def test_extract_strips_whitespace() -> None:
    assert extract_idempotency_key(
        _stub_request({"Idempotency-Key": "  abc  "}), None
    ) == "abc"


# ─── hash_payload ───────────────────────────────────────────────


def test_hash_is_deterministic_across_key_order() -> None:
    """Two payloads with the same content but different insertion order
    must hash to the same value — canonical JSON sorts keys."""
    a = hash_payload("approve_invoice", {"ap_item_id": "ap1", "amount": 100})
    b = hash_payload("approve_invoice", {"amount": 100, "ap_item_id": "ap1"})
    assert a == b


def test_hash_differs_when_intent_differs() -> None:
    """Same payload, different intent: different hash. This is the
    safety net against intent confusion under key reuse."""
    a = hash_payload("approve_invoice", {"ap_item_id": "ap1"})
    b = hash_payload("reject_invoice", {"ap_item_id": "ap1"})
    assert a != b


def test_hash_differs_when_payload_differs() -> None:
    a = hash_payload("approve_invoice", {"ap_item_id": "ap1"})
    b = hash_payload("approve_invoice", {"ap_item_id": "ap2"})
    assert a != b


def test_hash_handles_nested_dicts() -> None:
    """Canonical sort goes all the way down — nested ordering doesn't
    leak through."""
    a = hash_payload("x", {"meta": {"a": 1, "b": 2}})
    b = hash_payload("x", {"meta": {"b": 2, "a": 1}})
    assert a == b


def test_hash_is_64_hex_chars() -> None:
    """SHA-256 → 64 hex chars."""
    h = hash_payload("x", {"k": "v"})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ─── lookup_cached_response ────────────────────────────────────


def _stub_db_with_row(row: Optional[Dict[str, Any]]) -> Any:
    """Build a db.connect() context manager whose cursor.fetchone()
    returns ``row``. Mirrors how the real layer is exercised."""
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cur
    db = MagicMock()
    db.connect.return_value.__enter__.return_value = conn
    db.connect.return_value.__exit__.return_value = None
    return db


def test_lookup_miss_when_no_row() -> None:
    with patch.object(v1_idempotency, "_get_db", return_value=_stub_db_with_row(None)):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
        )
    assert result == {"status": "miss"}


def test_lookup_replay_when_hash_matches() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    row = {
        "payload_hash": "h1",
        "response_json": json.dumps({"ok": True, "result": {"id": "x"}}),
        "http_status": 200,
        "expires_at": future,
    }
    with patch.object(v1_idempotency, "_get_db", return_value=_stub_db_with_row(row)):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
        )
    assert result["status"] == "replay"
    assert result["response"] == {"ok": True, "result": {"id": "x"}}
    assert result["http_status"] == 200


def test_lookup_conflict_when_hash_differs() -> None:
    """Same key, different payload → 409. This is the bug-catcher
    that turns silent intent confusion into a hard error."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    row = {
        "payload_hash": "h_original",
        "response_json": json.dumps({"ok": True}),
        "http_status": 200,
        "expires_at": future,
    }
    with patch.object(v1_idempotency, "_get_db", return_value=_stub_db_with_row(row)):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h_different",
        )
    assert result == {"status": "conflict"}


def test_lookup_treats_expired_row_as_miss() -> None:
    """Expired rows are functionally absent — the caller re-executes
    and the next store overwrites the stale row."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = {
        "payload_hash": "h1",
        "response_json": json.dumps({"ok": True}),
        "http_status": 200,
        "expires_at": past,
    }
    with patch.object(v1_idempotency, "_get_db", return_value=_stub_db_with_row(row)):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
        )
    assert result == {"status": "miss"}


def test_lookup_malformed_response_json_is_miss() -> None:
    """A corrupted cache row is functionally absent — caller re-executes."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    row = {
        "payload_hash": "h1",
        "response_json": "not json",
        "http_status": 200,
        "expires_at": future,
    }
    with patch.object(v1_idempotency, "_get_db", return_value=_stub_db_with_row(row)):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
        )
    assert result == {"status": "miss"}


def test_lookup_db_failure_falls_through_to_miss() -> None:
    """Cache lookup failure must never block the request — log and
    fall through to execution. Worst case the intent runs twice."""
    db = MagicMock()
    db.connect.side_effect = RuntimeError("connection lost")
    with patch.object(v1_idempotency, "_get_db", return_value=db):
        result = lookup_cached_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
        )
    assert result == {"status": "miss"}


# ─── store_response ────────────────────────────────────────────


def test_store_writes_with_expected_args() -> None:
    """store_response builds an UPSERT — verify the columns it writes."""
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    db = MagicMock()
    db.connect.return_value.__enter__.return_value = conn

    with patch.object(v1_idempotency, "_get_db", return_value=db):
        store_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
            response={"ok": True, "result": {"id": "x"}},
            http_status=200,
        )

    # The SQL is an INSERT ... ON CONFLICT ... DO UPDATE
    sql_arg = cur.execute.call_args[0][0]
    assert "INSERT INTO intent_responses" in sql_arg
    assert "ON CONFLICT" in sql_arg

    params = cur.execute.call_args[0][1]
    # (org, key, hash, response_json, http_status, ts, expires_at)
    assert params[0] == "org_x"
    assert params[1] == "k1"
    assert params[2] == "h1"
    assert json.loads(params[3]) == {"ok": True, "result": {"id": "x"}}
    assert params[4] == 200

    conn.commit.assert_called_once()


def test_store_write_failure_is_swallowed() -> None:
    """A cache write failure must not propagate — the response went
    out, the audit chain has the truth."""
    db = MagicMock()
    db.connect.side_effect = RuntimeError("disk full")

    with patch.object(v1_idempotency, "_get_db", return_value=db):
        # Should not raise.
        store_response(
            organization_id="org_x",
            idempotency_key="k1",
            payload_hash="h1",
            response={"ok": True},
            http_status=200,
        )
