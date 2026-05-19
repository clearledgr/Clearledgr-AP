"""Coverage for /v1 per-key + per-org rate limits.

Uses the in-memory backend (the dev/test default — Redis only kicks
in when REDIS_URL is set). Each test calls `_reset_memory_for_tests`
in setup so windows start at zero.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from solden.api import v1_rate_limit
from solden.api.v1_auth import AgentIdentity
from solden.api.v1_rate_limit import (
    RateLimitExceeded,
    emit_rate_limit_exceeded_audit,
    enforce_v1_rate_limit,
)


@pytest.fixture(autouse=True)
def _reset_counters():
    """Every test starts with empty counters."""
    v1_rate_limit._reset_memory_for_tests()
    yield
    v1_rate_limit._reset_memory_for_tests()


def _agent(
    key_id: str = "k1", org: str = "org_a", agent_id: str = "agent:cs"
) -> AgentIdentity:
    return AgentIdentity(
        key_id=key_id,
        organization_id=org,
        agent_id=agent_id,
        agent_version="1.0.0",
        scopes=["intents:execute"],
    )


def _stub_request() -> MagicMock:
    req = MagicMock()
    req.url.path = "/v1/intents/execute"
    req.method = "POST"
    return req


# ─── Per-key counter ─────────────────────────────────────────────


def test_per_key_under_limit_passes() -> None:
    """N requests where N < limit — never trips."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 5):
        for _ in range(4):
            enforce_v1_rate_limit(_stub_request(), _agent())


def test_per_key_at_limit_trips() -> None:
    """The (limit+1)-th call from the same key trips per_key."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 3):
        for _ in range(3):
            enforce_v1_rate_limit(_stub_request(), _agent())
        with pytest.raises(RateLimitExceeded) as info:
            enforce_v1_rate_limit(_stub_request(), _agent())
    assert info.value.scope == "per_key"
    assert info.value.key_id == "k1"
    assert info.value.organization_id == "org_a"
    assert info.value.limit == 3


def test_two_keys_in_same_org_have_separate_per_key_buckets() -> None:
    """Per-key bucket is keyed on key_id, not org. Two agents under
    one tenant can both burst up to their per-key cap independently."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 2):
        with patch.object(v1_rate_limit, "V1_ORG_LIMIT_PER_MIN", 100):
            for _ in range(2):
                enforce_v1_rate_limit(_stub_request(), _agent(key_id="k1"))
                enforce_v1_rate_limit(_stub_request(), _agent(key_id="k2"))


def test_disabled_flag_lets_everything_through() -> None:
    """V1_RATE_LIMIT_ENABLED=False is a kill switch — useful for
    incident response (e.g. customer key got rotated, drop limits
    for 10 min while they redeploy) and for dev environments."""
    with patch.object(v1_rate_limit, "V1_RATE_LIMIT_ENABLED", False):
        with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 1):
            for _ in range(100):
                enforce_v1_rate_limit(_stub_request(), _agent())


# ─── Per-org counter ─────────────────────────────────────────────


def test_per_org_trips_when_aggregate_exceeds_org_cap() -> None:
    """Two keys under one org sharing the per-org bucket: the (cap+1)-th
    request — regardless of which key sent it — trips per_org."""
    # Per-key 10, per-org 4 — second key trips per_org on its 3rd call.
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 10):
        with patch.object(v1_rate_limit, "V1_ORG_LIMIT_PER_MIN", 4):
            for _ in range(2):
                enforce_v1_rate_limit(_stub_request(), _agent(key_id="k1"))
            for _ in range(2):
                enforce_v1_rate_limit(_stub_request(), _agent(key_id="k2"))
            with pytest.raises(RateLimitExceeded) as info:
                enforce_v1_rate_limit(_stub_request(), _agent(key_id="k2"))
    assert info.value.scope == "per_org"
    assert info.value.organization_id == "org_a"
    assert info.value.limit == 4


def test_orgs_are_isolated() -> None:
    """Org A trips at its per-org cap; org B is unaffected."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 100):
        with patch.object(v1_rate_limit, "V1_ORG_LIMIT_PER_MIN", 2):
            for _ in range(2):
                enforce_v1_rate_limit(_stub_request(), _agent(org="org_a"))
            with pytest.raises(RateLimitExceeded):
                enforce_v1_rate_limit(_stub_request(), _agent(org="org_a"))
            # org_b's bucket is fresh
            enforce_v1_rate_limit(_stub_request(), _agent(org="org_b"))


# ─── Exception payload ──────────────────────────────────────────


def test_rate_limit_exceeded_carries_request_context() -> None:
    """Path + method on the exception so the audit row + the 429
    body both include where the rejected request was going."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 1):
        enforce_v1_rate_limit(_stub_request(), _agent())
        with pytest.raises(RateLimitExceeded) as info:
            enforce_v1_rate_limit(_stub_request(), _agent())
    assert info.value.request_path == "/v1/intents/execute"
    assert info.value.request_method == "POST"
    assert info.value.actor_id == "agent:cs"
    assert info.value.retry_after_seconds > 0
    assert info.value.window_seconds == v1_rate_limit.V1_RATE_WINDOW_SECONDS


def test_rate_limit_exceeded_survives_missing_request() -> None:
    """enforce(...) accepts request=None for code paths (e.g. background
    workers) that don't have a Starlette request handy."""
    with patch.object(v1_rate_limit, "V1_KEY_LIMIT_PER_MIN", 1):
        enforce_v1_rate_limit(None, _agent())
        with pytest.raises(RateLimitExceeded) as info:
            enforce_v1_rate_limit(None, _agent())
    assert info.value.request_path is None
    assert info.value.request_method is None


# ─── Audit emission ─────────────────────────────────────────────


def test_emit_audit_writes_one_row() -> None:
    """A breach calls db.append_audit_event with event_type
    rate_limit_exceeded, actor_type=agent, payload carrying the
    scope/limit/window/path."""
    db = MagicMock()
    with patch(
        "solden.core.authorization._get_db", return_value=db
    ):
        emit_rate_limit_exceeded_audit(
            RateLimitExceeded(
                scope="per_key",
                identifier="k1",
                organization_id="org_a",
                key_id="k1",
                actor_id="agent:cs",
                limit=100,
                window_seconds=60,
                retry_after_seconds=42,
                request_path="/v1/intents/execute",
                request_method="POST",
            )
        )
    db.append_audit_event.assert_called_once()
    row = db.append_audit_event.call_args[0][0]
    assert row["event_type"] == "rate_limit_exceeded"
    assert row["actor_type"] == "agent"
    assert row["actor_id"] == "agent:cs"
    assert row["organization_id"] == "org_a"
    assert row["box_type"] == "organization"
    assert row["box_id"] == "org_a"
    payload = row["payload_json"]
    assert payload["scope"] == "per_key"
    assert payload["key_id"] == "k1"
    assert payload["limit"] == 100
    assert payload["window_seconds"] == 60
    assert payload["retry_after_seconds"] == 42
    assert payload["request_path"] == "/v1/intents/execute"
    assert payload["request_method"] == "POST"


def test_emit_audit_failure_is_swallowed() -> None:
    """Audit failure must never propagate — the 429 response went out,
    losing the audit row is a known-acceptable tradeoff (same contract
    as authorization_denied)."""
    db = MagicMock()
    db.append_audit_event.side_effect = RuntimeError("audit table down")
    with patch(
        "solden.core.authorization._get_db", return_value=db
    ):
        # Must not raise.
        emit_rate_limit_exceeded_audit(
            RateLimitExceeded(
                scope="per_key",
                identifier="k1",
                organization_id="org_a",
                key_id="k1",
                actor_id="agent:cs",
                limit=100,
                window_seconds=60,
                retry_after_seconds=42,
            )
        )
