"""Coverage for the migration-88 first-class governance columns on
``audit_events``: ``capability_id``, ``capability_version``, ``tool_scope``.

These tests live at the unit level (no Postgres needed) so the
threading contracts are checkable without the full integration
harness. The actual SQL INSERT is covered by the integration suite
under the testcontainer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clearledgr.api import v1_rate_limit
from clearledgr.api.v1_auth import AgentIdentity
from clearledgr.api.v1_rate_limit import (
    RateLimitExceeded,
    emit_rate_limit_exceeded_audit,
    enforce_v1_rate_limit,
)
from clearledgr.core import authorization
from clearledgr.core.authorization import (
    AuthorizationDenied,
    emit_authorization_denied_audit,
)


# ─── AuthorizationDenied carries tool_scope ─────────────────────────


def test_authorization_denied_records_tool_scope_attr() -> None:
    """The typed exception preserves the scope set the actor held at
    denial time, so the global handler in main.py can thread it onto
    the audit row without re-deriving it."""
    exc = AuthorizationDenied(
        "invalid_scope",
        actor_type="agent",
        actor_id="agent:cs",
        tool_scope=["records:read"],
        organization_id="org_x",
    )
    assert exc.tool_scope == ["records:read"]


def test_authorization_denied_tool_scope_none_when_absent() -> None:
    """No scope context (e.g. invalid_api_key — we have no identity)
    leaves the field None so the audit column stores SQL NULL."""
    exc = AuthorizationDenied("invalid_api_key")
    assert exc.tool_scope is None


def test_authorization_denied_tool_scope_is_copied() -> None:
    """Mutating the caller's list afterwards must not change what
    we recorded — the exception holds its own copy."""
    src = ["records:read"]
    exc = AuthorizationDenied("invalid_scope", tool_scope=src)
    src.append("records:write")
    assert exc.tool_scope == ["records:read"]


# ─── emit_authorization_denied_audit writes tool_scope ──────────────


def _stub_db() -> Any:
    db = MagicMock()
    return db


def test_emit_denial_audit_threads_tool_scope() -> None:
    db = _stub_db()
    with patch.object(authorization, "_get_db", return_value=db):
        emit_authorization_denied_audit(
            denial_reason="invalid_scope",
            actor_type="agent",
            actor_id="agent:cs",
            tool_scope=["records:read"],
            organization_id="org_x",
            attempted_action="scope:intents:execute",
            request_path="/v1/intents/execute",
            request_method="POST",
            http_status=403,
        )
    db.append_audit_event.assert_called_once()
    row = db.append_audit_event.call_args[0][0]
    assert row["tool_scope"] == ["records:read"]
    assert row["actor_type"] == "agent"
    assert row["actor_id"] == "agent:cs"


def test_emit_denial_audit_omits_tool_scope_when_absent() -> None:
    """For denial paths with no resolved identity (missing key,
    invalid key) tool_scope must be None — never an empty list,
    which would falsely imply 'this caller had zero permissions'."""
    db = _stub_db()
    with patch.object(authorization, "_get_db", return_value=db):
        emit_authorization_denied_audit(
            denial_reason="invalid_api_key",
            actor_type="user",
        )
    row = db.append_audit_event.call_args[0][0]
    assert row["tool_scope"] is None


# ─── RateLimitExceeded carries tool_scope ──────────────────────────


@pytest.fixture(autouse=True)
def _reset_counters():
    v1_rate_limit._reset_memory_for_tests()
    yield
    v1_rate_limit._reset_memory_for_tests()


_UNSET = object()


def _agent(scopes=_UNSET) -> AgentIdentity:
    """Build an AgentIdentity. ``scopes`` defaults to a one-element
    list; pass ``scopes=None`` explicitly to test the legacy
    full-access (NULL scopes) contract."""
    return AgentIdentity(
        key_id="k1",
        organization_id="org_x",
        agent_id="agent:cs",
        agent_version="1.0.0",
        scopes=["intents:execute"] if scopes is _UNSET else scopes,
    )


def _stub_request() -> Any:
    req = MagicMock()
    req.url.path = "/v1/intents/execute"
    req.method = "POST"
    return req


def test_rate_limit_breach_carries_scope() -> None:
    """The scope set on the key at breach time is preserved on the
    exception so the audit handler can record it."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 1):
        enforce_v1_rate_limit(_stub_request(), _agent(["intents:execute"]))
        with pytest.raises(RateLimitExceeded) as info:
            enforce_v1_rate_limit(_stub_request(), _agent(["intents:execute"]))
    assert info.value.tool_scope == ["intents:execute"]


def test_rate_limit_breach_with_legacy_null_scope() -> None:
    """A legacy full-access key (scopes=None) records None on the
    audit row — never a fabricated '[]' that would later look like
    'this key had zero scopes'."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 1):
        enforce_v1_rate_limit(_stub_request(), _agent(scopes=None))
        with pytest.raises(RateLimitExceeded) as info:
            enforce_v1_rate_limit(_stub_request(), _agent(scopes=None))
    assert info.value.tool_scope is None


def test_rate_limit_audit_writes_tool_scope() -> None:
    """The emit helper pulls tool_scope off the exception and onto
    the audit row alongside scope/limit/window."""
    db = _stub_db()
    with patch(
        "clearledgr.core.authorization._get_db", return_value=db
    ):
        emit_rate_limit_exceeded_audit(
            RateLimitExceeded(
                scope="per_key",
                identifier="k1",
                organization_id="org_x",
                key_id="k1",
                actor_id="agent:cs",
                limit=100,
                window_seconds=60,
                retry_after_seconds=17,
                tool_scope=["intents:execute", "audit:read"],
            )
        )
    row = db.append_audit_event.call_args[0][0]
    assert row["tool_scope"] == ["intents:execute", "audit:read"]
    assert row["event_type"] == "rate_limit_exceeded"
