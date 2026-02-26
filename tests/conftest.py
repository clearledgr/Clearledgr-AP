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
