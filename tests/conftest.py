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
def reset_shared_http_client():
    """Drop the cached shared httpx.AsyncClient between tests.

    Many tests monkey-patch ``httpx.AsyncClient`` (via module-alias
    patches like ``clearledgr.integrations.erp_router.httpx.AsyncClient``)
    to inject mocks for outbound HTTP calls. The shared-client module
    caches one AsyncClient instance for the process lifetime; if that
    cache got populated before the patch (either by production import
    path or an earlier test), patching the class does nothing to the
    already-created instance. Clearing the cache before and after each
    test means the next ``get_http_client()`` call hits the patched
    constructor and the mock intercepts.
    """
    from clearledgr.core.http_client import _reset_for_testing
    _reset_for_testing()
    yield
    _reset_for_testing()


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
    try:
        from clearledgr.services.agent_memory import _agent_memory_services
        _agent_memory_services.clear()
    except Exception:
        pass
    try:
        from clearledgr.services.finance_learning import _finance_learning_services
        _finance_learning_services.clear()
    except Exception:
        pass
