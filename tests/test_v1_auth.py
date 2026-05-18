"""Coverage for the /v1 auth dep (customer-side agent connection).

Tests the migration-74 scope contract:
* ``scopes = None``  → legacy full-access
* ``scopes = []``    → zero permissions
* ``scopes = [...]`` → explicit allow-list

Plus revocation and expiry handling, and the AuthorizationDenied
funnel for missing / invalid / wrong-scope keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from clearledgr.api.v1_auth import (
    AgentIdentity,
    _parse_scopes,
    _row_is_expired,
    _row_is_revoked,
    resolve_agent_key,
)
from clearledgr.core.authorization import AuthorizationDenied


# ─── AgentIdentity ──────────────────────────────────────────────


def test_agent_identity_actor_label_prefers_agent_id() -> None:
    ai = AgentIdentity(
        key_id="k1",
        organization_id="org_x",
        agent_id="agent:cs-bot-prod",
        agent_version="2.4.1",
        scopes=["write:ap_items"],
        user_id="alice@example.com",
    )
    assert ai.actor_label == "agent:cs-bot-prod"


def test_agent_identity_actor_label_falls_back_to_user_id() -> None:
    """Legacy key without an agent_id: actor_label uses user_id."""
    ai = AgentIdentity(
        key_id="k2",
        organization_id="org_x",
        agent_id=None,
        agent_version=None,
        scopes=None,
        user_id="alice@example.com",
    )
    assert ai.actor_label == "alice@example.com"


def test_agent_identity_actor_label_falls_back_to_unknown() -> None:
    """Neither agent_id nor user_id present."""
    ai = AgentIdentity(
        key_id="k3",
        organization_id="org_x",
        agent_id=None,
        agent_version=None,
        scopes=None,
    )
    assert ai.actor_label == "unknown_agent"


def test_agent_identity_scope_check_explicit_allow() -> None:
    ai = AgentIdentity(
        key_id="k1",
        organization_id="org_x",
        agent_id="agent:cs",
        agent_version=None,
        scopes=["write:ap_items", "read:audit"],
    )
    assert ai.has_scope("write:ap_items")
    assert ai.has_scope("read:audit")
    assert not ai.has_scope("manage:webhooks")


def test_agent_identity_scope_check_legacy_null_is_full_access() -> None:
    """Migration-74 contract: scopes=NULL → key was issued before
    scope was introduced; treat as full access for backward compat."""
    ai = AgentIdentity(
        key_id="k1",
        organization_id="org_x",
        agent_id=None,
        agent_version=None,
        scopes=None,
    )
    assert ai.has_scope("write:ap_items")
    assert ai.has_scope("anything:at_all")


def test_agent_identity_scope_check_empty_list_is_zero_perms() -> None:
    """scopes=[] is an explicit "this key has zero permissions" — every
    scope check fails."""
    ai = AgentIdentity(
        key_id="k1",
        organization_id="org_x",
        agent_id="agent:cs",
        agent_version=None,
        scopes=[],
    )
    assert not ai.has_scope("write:ap_items")


# ─── _parse_scopes ──────────────────────────────────────────────


def test_parse_scopes_handles_python_list() -> None:
    """Postgres JSONB returns a Python list via psycopg's JSON adapter."""
    assert _parse_scopes(["write:ap_items", "read:audit"]) == [
        "write:ap_items",
        "read:audit",
    ]


def test_parse_scopes_handles_json_string() -> None:
    """SQLite fallback returns the raw JSON text."""
    assert _parse_scopes('["write:ap_items"]') == ["write:ap_items"]


def test_parse_scopes_handles_null() -> None:
    assert _parse_scopes(None) is None  # legacy full-access marker


def test_parse_scopes_normalises_case_and_strips() -> None:
    assert _parse_scopes(["  WRITE:AP_ITEMS  ", "read:audit"]) == [
        "write:ap_items",
        "read:audit",
    ]


def test_parse_scopes_malformed_returns_empty() -> None:
    """Defence in depth: a malformed scopes value fails closed
    (empty list, no permissions)."""
    assert _parse_scopes("not json") == []
    assert _parse_scopes(42) == []


# ─── revoked / expired checks ──────────────────────────────────


def test_row_is_revoked_via_revoked_at() -> None:
    assert _row_is_revoked({"revoked_at": "2026-05-18T00:00:00Z"})
    assert not _row_is_revoked({"revoked_at": None})


def test_row_is_revoked_via_is_active_off() -> None:
    assert _row_is_revoked({"is_active": 0})
    assert _row_is_revoked({"is_active": False})
    assert not _row_is_revoked({"is_active": 1})
    assert not _row_is_revoked({"is_active": True})


def test_row_is_expired_past_timestamp() -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert _row_is_expired({"expires_at": past})


def test_row_is_expired_future_timestamp() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert not _row_is_expired({"expires_at": future})


def test_row_is_expired_null_never_expires() -> None:
    assert not _row_is_expired({"expires_at": None})
    assert not _row_is_expired({})


def test_row_is_expired_malformed_doesnt_lock_out() -> None:
    """A malformed timestamp shouldn't lock out callers — we'd rather
    let a broken expiry through than nuke a working key."""
    assert not _row_is_expired({"expires_at": "not a date"})


# ─── resolve_agent_key end-to-end ──────────────────────────────


def _stub_request(headers: Optional[Dict[str, str]] = None) -> Any:
    """Minimal stand-in for a starlette Request used by resolve_agent_key."""
    req = MagicMock()
    req.headers = headers or {}
    return req


def _stub_db(row: Optional[Dict[str, Any]]) -> Any:
    db = MagicMock()
    db.validate_api_key.return_value = row
    return db


def test_resolve_missing_key_raises_401() -> None:
    with pytest.raises(AuthorizationDenied) as info:
        resolve_agent_key(_stub_request(), db=_stub_db(None))
    assert info.value.http_status == 401
    assert info.value.denial_reason == "missing_api_key"


def test_resolve_invalid_key_raises_401() -> None:
    headers = {"Authorization": "Bearer sk_bogus"}
    with pytest.raises(AuthorizationDenied) as info:
        resolve_agent_key(_stub_request(headers), db=_stub_db(None))
    assert info.value.http_status == 401
    assert info.value.denial_reason == "invalid_api_key"


def test_resolve_revoked_key_raises_403() -> None:
    row = {
        "id": "k1",
        "organization_id": "org_x",
        "agent_id": "agent:cs",
        "user_id": "alice",
        "scopes": ["write:ap_items"],
        "revoked_at": "2026-05-01T00:00:00Z",
    }
    headers = {"Authorization": "Bearer sk_test"}
    with pytest.raises(AuthorizationDenied) as info:
        resolve_agent_key(_stub_request(headers), db=_stub_db(row))
    assert info.value.http_status == 403
    assert info.value.denial_reason == "api_key_revoked"


def test_resolve_expired_key_raises_403() -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    row = {
        "id": "k1",
        "organization_id": "org_x",
        "agent_id": "agent:cs",
        "user_id": "alice",
        "scopes": ["write:ap_items"],
        "expires_at": past,
    }
    headers = {"Authorization": "Bearer sk_test"}
    with pytest.raises(AuthorizationDenied) as info:
        resolve_agent_key(_stub_request(headers), db=_stub_db(row))
    assert info.value.http_status == 403
    assert info.value.denial_reason == "api_key_expired"


def test_resolve_happy_path_returns_agent_identity() -> None:
    row = {
        "id": "k1",
        "organization_id": "org_x",
        "agent_id": "agent:cs-bot-prod",
        "agent_version": "2.4.1",
        "user_id": "service@cs.local",
        "scopes": ["write:ap_items", "read:audit"],
    }
    headers = {"Authorization": "Bearer sk_test_abc123"}
    identity = resolve_agent_key(_stub_request(headers), db=_stub_db(row))
    assert identity.key_id == "k1"
    assert identity.organization_id == "org_x"
    assert identity.agent_id == "agent:cs-bot-prod"
    assert identity.agent_version == "2.4.1"
    assert identity.scopes == ["write:ap_items", "read:audit"]
    assert identity.actor_label == "agent:cs-bot-prod"


def test_resolve_reads_x_api_key_header() -> None:
    """X-API-Key is the alternative auth header."""
    row = {
        "id": "k1",
        "organization_id": "org_x",
        "agent_id": "agent:cs",
        "user_id": "alice",
        "scopes": ["write:ap_items"],
    }
    headers = {"X-API-Key": "sk_test_abc"}
    identity = resolve_agent_key(_stub_request(headers), db=_stub_db(row))
    assert identity.agent_id == "agent:cs"


def test_resolve_legacy_null_scopes_yields_full_access() -> None:
    """Migration-74 contract honoured end-to-end."""
    row = {
        "id": "k1",
        "organization_id": "org_x",
        "agent_id": None,
        "user_id": "legacy@example.com",
        "scopes": None,  # legacy unscoped
    }
    headers = {"Authorization": "Bearer sk_legacy"}
    identity = resolve_agent_key(_stub_request(headers), db=_stub_db(row))
    assert identity.scopes is None
    assert identity.has_scope("anything")
