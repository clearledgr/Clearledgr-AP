"""
Shared pytest fixtures and hooks for the Clearledgr test suite.
"""

import os

import pytest

os.environ.setdefault("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", "true")
os.environ.setdefault("CLEARLEDGR_SKIP_DEFERRED_STARTUP", "true")


@pytest.fixture(autouse=True)
def allow_in_memory_rate_limit_backend_for_tests(monkeypatch):
    """Keep test runs independent from external Redis dependencies.

    Production/staging startup enforces Redis-backed rate limiting. Tests toggle
    ENV frequently and run without Redis, so we opt in to the documented escape
    hatch unless a test overrides it explicitly.
    """
    monkeypatch.setenv("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", "true")
    yield


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    """Reset the in-memory rate-limit counter before every test.

    The rate-limit store is a module-level dict that accumulates across the
    whole test process.  Without this reset, running the full suite in a
    single process hits the 100-request limit mid-run and causes 429 errors
    in later tests.
    """
    from clearledgr.services.rate_limit import _rate_limit_store

    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest.fixture(autouse=True)
def reset_service_singletons():
    """Clear in-memory state from module-level service singletons between tests.

    Also resets the DB singleton so tests that swap out the DB path
    (via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)) do not leave a stale
    connection for subsequent tests.
    """
    yield
    # Reset DB singleton so each test starts with the correct DB path
    try:
        import clearledgr.core.database as _db_mod
        _db_mod._DB_INSTANCE = None
    except Exception:
        pass
    try:
        from clearledgr.services.gl_correction import _gl_correction_services
        _gl_correction_services.clear()
    except Exception:
        pass
