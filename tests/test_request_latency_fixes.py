"""Three latency-targeting fixes that ship together:

1. ``/health`` no longer calls ``get_metrics()`` (which runs 9 SELECTs
   per call). It reports the persistent-mode flag directly.

2. ``record_request`` / ``record_error`` are non-blocking — the DB
   write goes through a background ``ThreadPoolExecutor`` so the
   request handler returns before the INSERT lands.

3. ``_validate_google_token`` short-circuits for tokens that don't
   look like Google access tokens (no ``ya29.`` prefix), saving the
   ~1.7s round-trip to Google's tokeninfo endpoint on every 401.

Each fix has measurable upside on the prod traffic mix (observed
/health p50 of ~8.6s, /api/saved-views p95 of ~62s). These tests pin
the contracts so a future refactor can't silently re-block the hot
path.
"""
from __future__ import annotations

import time

import pytest

from clearledgr.core.auth import _looks_like_google_access_token
from clearledgr.services import metrics as metrics_module


# ---------------------------------------------------------------------------
# Fix 1 — /health drops the get_metrics() call
# ---------------------------------------------------------------------------


def test_health_endpoint_does_not_call_get_metrics(monkeypatch):
    """The /health handler must NOT call get_metrics() — that function
    runs 9 separate SELECTs to build a full metrics report and
    dominated /health latency.
    """
    import main

    calls = {"n": 0}

    def _spying_get_metrics():
        calls["n"] += 1
        return {"backend": {"mode": "test"}}

    monkeypatch.setattr(main, "get_metrics", _spying_get_metrics)

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code in {200, 503}, response.text
    assert calls["n"] == 0, "get_metrics() must not be invoked from /health"


def test_health_endpoint_still_reports_metrics_backend_mode(monkeypatch):
    """The metrics_backend.mode field stays in the payload — only the
    expensive lookup behind it changes. Surfaces dev/prod parity for
    operators reading /health.
    """
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = client.get("/health")
    payload = response.json()
    assert "metrics_backend" in payload["checks"]
    assert "mode" in payload["checks"]["metrics_backend"]


# ---------------------------------------------------------------------------
# Fix 2 — record_request / record_error are non-blocking
# ---------------------------------------------------------------------------


def test_record_request_does_not_block_caller_in_persistent_mode(monkeypatch):
    """In persistent mode the DB INSERT runs in the metrics executor,
    not on the calling thread. ``record_request`` should return
    immediately even if the persist path is artificially slow.
    """
    monkeypatch.setattr(metrics_module, "_PERSISTENT_MODE", True)

    persisted = []

    def _slow_persist(*args, **kwargs):
        # Simulate a ~1.5s DB write — much longer than any reasonable
        # p99. If record_request blocked, the caller-side timing would
        # exceed this.
        time.sleep(1.5)
        persisted.append((args, kwargs))

    monkeypatch.setattr(metrics_module, "_persist_request_metric", _slow_persist)

    started = time.monotonic()
    metrics_module.record_request("GET", "/probe", 200, 1.0)
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, f"record_request blocked for {elapsed:.3f}s; expected < 200ms"

    # The slow persist still completes in the background — give the
    # executor up to 3s to drain.
    deadline = time.monotonic() + 3.0
    while not persisted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert persisted, "background persist never ran"


def test_record_error_does_not_block_caller_in_persistent_mode(monkeypatch):
    """Same contract as record_request — error metrics are also off
    the request hot path."""
    monkeypatch.setattr(metrics_module, "_PERSISTENT_MODE", True)

    persisted = []

    def _slow_persist(*args, **kwargs):
        time.sleep(1.5)
        persisted.append((args, kwargs))

    monkeypatch.setattr(metrics_module, "_persist_error_metric", _slow_persist)

    started = time.monotonic()
    metrics_module.record_error("http_401", "/api/x")
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, f"record_error blocked for {elapsed:.3f}s; expected < 200ms"

    deadline = time.monotonic() + 3.0
    while not persisted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert persisted, "background error persist never ran"


def test_record_request_in_dev_mode_is_pure_in_memory(monkeypatch):
    """When _PERSISTENT_MODE is False, the executor is bypassed and we
    update the in-memory dict directly. Pinned so a future refactor
    that always submits to the executor doesn't quietly start writing
    in dev too.
    """
    monkeypatch.setattr(metrics_module, "_PERSISTENT_MODE", False)
    submit_calls = []

    def _spy_submit(*args, **kwargs):
        submit_calls.append((args, kwargs))

    monkeypatch.setattr(metrics_module, "_submit_persist", _spy_submit)
    metrics_module.record_request("GET", "/dev-probe", 200, 0.5)
    assert submit_calls == [], "dev mode must not submit to the metrics executor"


# ---------------------------------------------------------------------------
# Fix 3 — _validate_google_token short-circuits non-Google tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("ya29.a0AS3H6Nz...", True),
        # JWTs always start with eyJ (base64-encoded ``{"``).
        ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.foo.bar", False),
        ("notavalidjwt", False),
        ("", False),
        ("Bearer ya29.x", False),  # caller must strip "Bearer "
        # Refresh tokens (we never receive these but they're not access tokens):
        ("1//abc-refresh", False),
    ],
)
def test_looks_like_google_access_token_predicate(token, expected):
    assert _looks_like_google_access_token(token) is expected


def test_validate_google_token_does_not_call_httpx_for_non_google_token(monkeypatch):
    """The whole point of the predicate — for a token that obviously
    isn't a Google access token, return None without touching the
    network. The original implementation paid ~1.7s/call to discover
    the same answer via httpx.
    """
    from clearledgr.core import auth as auth_module

    sentinel = {"called": False}

    def _spy_get(*args, **kwargs):
        sentinel["called"] = True
        raise AssertionError("httpx.get must not be called for non-Google tokens")

    # Patch the module-level httpx so the import inside _validate_google_token
    # resolves to our spy.
    import httpx as real_httpx
    monkeypatch.setattr(real_httpx, "get", _spy_get)

    result = auth_module._validate_google_token("notavalidjwt")
    assert result is None
    assert sentinel["called"] is False
