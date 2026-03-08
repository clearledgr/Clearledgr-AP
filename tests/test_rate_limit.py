"""Tests for rate limiting middleware and backend logic."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from clearledgr.services.rate_limit import (
    _check_rate_limit_memory,
    _rate_limit_store,
    check_rate_limit,
    enforce_production_backend_requirements,
    get_client_identifier,
    get_rate_limit_backend_status,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
)


# ---------------------------------------------------------------------------
# Unit tests for in-memory backend
# ---------------------------------------------------------------------------


def test_memory_backend_allows_within_limit():
    """Requests within the limit should be allowed."""
    _rate_limit_store.clear()
    allowed, remaining, _ = _check_rate_limit_memory("test-client-1")
    assert allowed is True
    assert remaining == RATE_LIMIT_REQUESTS - 1


def test_memory_backend_rejects_over_limit():
    """Requests exceeding the limit should be rejected."""
    _rate_limit_store.clear()
    client_id = "test-client-flood"

    # Exhaust the limit
    for _ in range(RATE_LIMIT_REQUESTS):
        _check_rate_limit_memory(client_id)

    allowed, remaining, reset_after = _check_rate_limit_memory(client_id)
    assert allowed is False
    assert remaining == 0
    assert reset_after > 0


def test_check_rate_limit_disabled():
    """When rate limiting is disabled, all requests pass."""
    with patch("clearledgr.services.rate_limit.RATE_LIMIT_ENABLED", False):
        allowed, remaining, _ = check_rate_limit("any-client")
        assert allowed is True
        assert remaining == RATE_LIMIT_REQUESTS


# ---------------------------------------------------------------------------
# Client identifier extraction
# ---------------------------------------------------------------------------


def test_client_identifier_prefers_api_key():
    """API key header should be preferred over IP."""
    request = MagicMock()
    request.headers = {"X-API-Key": "sk-test-123"}
    request.client = MagicMock(host="10.0.0.1")

    result = get_client_identifier(request)
    assert result == "api_key:sk-test-123"


def test_client_identifier_falls_back_to_ip():
    """Without API key, should use client IP."""
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock(host="192.168.1.1")

    result = get_client_identifier(request)
    assert result == "ip:192.168.1.1"


# ---------------------------------------------------------------------------
# Middleware integration tests (via TestClient)
# ---------------------------------------------------------------------------


@patch("clearledgr.services.rate_limit.RATE_LIMIT_REQUESTS", 3)
def test_rate_limit_429_on_exceeded():
    """Middleware should return 429 when limit is exceeded."""
    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    _rate_limit_store.clear()

    api_key = "test-429-check"
    headers = {"X-API-Key": api_key}

    # Send requests up to the (patched) limit
    for _ in range(3):
        client.get("/v1/health", headers=headers)

    # Next request should be rate limited
    resp = client.get("/v1/health", headers=headers)
    assert resp.status_code == 429


def test_excluded_paths_bypass_rate_limit():
    """Health and docs endpoints should never be rate limited."""
    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    _rate_limit_store.clear()

    # Exhaust the limit for this client
    for _ in range(RATE_LIMIT_REQUESTS + 5):
        client.get("/v1/health", headers={"X-API-Key": "test-exclude"})

    # Health endpoint should still work even after limit exceeded
    resp = client.get("/health")
    assert resp.status_code != 429


def test_production_backend_requires_redis_by_default(monkeypatch):
    """Production-like ENV must fail startup if Redis limiter is unavailable."""
    import clearledgr.services.rate_limit as rate_limit

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", raising=False)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "_backend", "memory")
    monkeypatch.setattr(rate_limit, "_redis_client", None)

    with pytest.raises(RuntimeError, match="redis_rate_limit_backend_required_in_production"):
        enforce_production_backend_requirements()


def test_production_backend_can_use_explicit_override(monkeypatch):
    """Escape hatch should be explicit and visible via backend status."""
    import clearledgr.services.rate_limit as rate_limit

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", "true")
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "_backend", "memory")
    monkeypatch.setattr(rate_limit, "_redis_client", None)

    enforce_production_backend_requirements()
    status = get_rate_limit_backend_status()
    assert status["backend"] == "memory"


def test_check_rate_limit_fails_closed_in_production_without_redis(monkeypatch):
    """Runtime requests must be denied when production limiter backend is unavailable."""
    import clearledgr.services.rate_limit as rate_limit

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", raising=False)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit, "_redis_client", None)
    monkeypatch.setattr(rate_limit, "_backend", "memory")

    allowed, remaining, reset_after = check_rate_limit("prod-client-no-redis")
    assert allowed is False
    assert remaining == 0
    assert reset_after == RATE_LIMIT_WINDOW
