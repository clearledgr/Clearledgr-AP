"""Coverage for the /v1/records router (plan §Step 4).

Focus: the public-field allowlist, cursor encoding, and unsupported-
box-type behaviour. End-to-end DB tests live with the broader
integration suite (need psycopg + a real schema).
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import pytest

from clearledgr.api.v1_records import (
    _AP_ITEM_PUBLIC_FIELDS,
    _RECORD_READERS,
    _decode_cursor,
    _encode_cursor,
    _shape_record,
)


# ─── Cursor round-trip ────────────────────────────────────────────


def test_cursor_round_trip() -> None:
    assert _decode_cursor(_encode_cursor(0)) == 0
    assert _decode_cursor(_encode_cursor(50)) == 50
    assert _decode_cursor(_encode_cursor(12345)) == 12345


def test_cursor_missing_decodes_to_zero() -> None:
    assert _decode_cursor(None) == 0
    assert _decode_cursor("") == 0


def test_cursor_malformed_decodes_to_zero() -> None:
    """Defence in depth: a junk cursor restarts from the top rather
    than 500-ing the caller."""
    assert _decode_cursor("not-base64!") == 0
    assert _decode_cursor("!!!") == 0


def test_cursor_clamps_negative_to_zero() -> None:
    """A cursor that decodes to a negative offset is treated as 0."""
    import base64
    import json

    bogus = base64.urlsafe_b64encode(
        json.dumps({"o": -10}).encode("utf-8")
    ).decode("ascii")
    assert _decode_cursor(bogus) == 0


# ─── _shape_record: field allowlist ──────────────────────────────


def _row_with_sensitive_fields() -> Dict[str, Any]:
    """An ap_items row containing fields that must NOT leave the API."""
    return {
        "id": "ap_123",
        "state": "needs_approval",
        "organization_id": "org_x",
        "created_at": "2026-05-18T00:00:00Z",
        "updated_at": "2026-05-18T01:00:00Z",
        # PUBLIC fields (should land in `data`)
        "vendor_name": "Acme Co",
        "amount": 1234.56,
        "currency": "USD",
        "invoice_number": "INV-1",
        "due_date": "2026-06-18",
        "approval_required": True,
        # SENSITIVE / INTERNAL fields (must NOT land in `data`)
        "bank_details_encrypted": "<ciphertext>",
        "slack_thread_id": "T-channel-1",
        "thread_id": "gmail-thread-99",
        "message_id": "msg-77",
        "metadata": '{"internal": "blob"}',
        "user_id": "alice@x",
        "last_error": "internal trace text",
    }


def test_shape_record_emits_canonical_top_level_fields() -> None:
    row = _row_with_sensitive_fields()
    result = _shape_record(row, box_type="ap_item", fields=_AP_ITEM_PUBLIC_FIELDS)
    assert result["id"] == "ap_123"
    assert result["box_type"] == "ap_item"
    assert result["state"] == "needs_approval"
    assert result["organization_id"] == "org_x"
    assert result["created_at"] == "2026-05-18T00:00:00Z"
    assert result["updated_at"] == "2026-05-18T01:00:00Z"


def test_shape_record_includes_public_fields_in_data() -> None:
    row = _row_with_sensitive_fields()
    result = _shape_record(row, box_type="ap_item", fields=_AP_ITEM_PUBLIC_FIELDS)
    data = result["data"]
    assert data["vendor_name"] == "Acme Co"
    assert data["amount"] == 1234.56
    assert data["currency"] == "USD"
    assert data["invoice_number"] == "INV-1"
    assert data["approval_required"] is True


def test_shape_record_excludes_sensitive_columns() -> None:
    """The whole point of the allowlist: bank details and surface
    refs never leave the substrate."""
    row = _row_with_sensitive_fields()
    result = _shape_record(row, box_type="ap_item", fields=_AP_ITEM_PUBLIC_FIELDS)
    data = result["data"]
    for sensitive in (
        "bank_details_encrypted",
        "slack_thread_id",
        "thread_id",
        "message_id",
        "metadata",
        "user_id",
        "last_error",
    ):
        assert sensitive not in data, (
            f"field {sensitive!r} leaked through /v1/records — "
            f"public field allowlist must remain deny-by-default"
        )


def test_shape_record_with_minimal_row() -> None:
    """A row missing most fields shouldn't crash; missing values just
    don't appear in `data`."""
    row = {
        "id": "ap_minimal",
        "state": "received",
        "organization_id": "org_x",
        "created_at": "...",
        "updated_at": "...",
    }
    result = _shape_record(row, box_type="ap_item", fields=_AP_ITEM_PUBLIC_FIELDS)
    assert result["data"] == {}


# ─── _RECORD_READERS registry ────────────────────────────────────


def test_registry_contains_ap_item() -> None:
    assert "ap_item" in _RECORD_READERS
    reader = _RECORD_READERS["ap_item"]
    assert reader.box_type == "ap_item"
    assert callable(reader.list_fn)
    assert callable(reader.read_fn)
    assert isinstance(reader.fields, frozenset)


def test_registry_field_allowlist_excludes_known_sensitive_columns() -> None:
    """Tripwire: if someone adds bank_details_encrypted to the public
    set, this test fires. Keeps the allowlist deny-by-default through
    code review by mistake."""
    forbidden = {
        "bank_details_encrypted",
        "slack_thread_id",
        "slack_channel_id",
        "slack_message_ts",
        "thread_id",  # gmail thread
        "message_id",  # gmail message
        "metadata",
        "user_id",
        "last_error",
        "attachment_url",
    }
    leaked = forbidden & set(_RECORD_READERS["ap_item"].fields)
    assert not leaked, f"sensitive columns in v1 allowlist: {leaked}"
