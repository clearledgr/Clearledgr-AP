"""
Shared pytest fixtures and hooks for the Clearledgr test suite.
"""

import pytest


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

    Services like PaymentExecutionService and RecurringManagementService use
    module-level dicts keyed by org ID.  Without clearing them, payments /
    schedules created in one test bleed into later tests running in the same
    process (e.g. ACH batch count assertions fail because earlier tests left
    PENDING payments behind).

    Also resets the DB singleton so that tests which swap out the DB path
    (via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)) do not leave a stale
    connection for subsequent tests that expect the production DB.
    """
    yield
    # Reset DB singleton so each test starts with the correct DB path
    try:
        import clearledgr.core.database as _db_mod
        _db_mod._DB_INSTANCE = None
    except Exception:
        pass
    try:
        from clearledgr.services.payment_execution import _payment_services
        _payment_services.clear()
    except Exception:
        pass
    try:
        from clearledgr.services.recurring_management import _recurring_services
        _recurring_services.clear()
    except Exception:
        pass
    try:
        from clearledgr.services.gl_correction import _gl_correction_services
        _gl_correction_services.clear()
    except Exception:
        pass
