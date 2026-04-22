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
    # SubscriptionService caches `self.db` at construction (subscription.py:432).
    # If a test swaps DATABASE_URL / CLEARLEDGR_DB_PATH but the singleton
    # stayed alive from an earlier test, it would keep writing to the old
    # DB. Cheap to reset; matches the other singletons already cleared here.
    try:
        import clearledgr.services.subscription as _sub_mod
        _sub_mod._subscription_service = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Postgres test backend (C.1 of the SQLite→Postgres migration)
# ---------------------------------------------------------------------------
#
# Goal: run tests against the same engine that prod uses so the 44
# `if self.use_postgres: ...` branches are exercised in CI, not just in
# customer tenants. SQLite is kept as an opt-out escape hatch for devs
# iterating fast without Docker.
#
# Engine selection, in priority order:
#   1. TEST_DB_ENGINE=sqlite  → no-op (explicit opt-out for devs
#        without Docker / without a local Postgres). Tests revert
#        to the per-test temp-file SQLite pattern.
#   2. TEST_DB_ENGINE=postgres (default) + TEST_DATABASE_URL set → use
#        that URL. Useful for:
#          - Local dev with a running PG (e.g. `brew services start
#            postgresql@15`): `TEST_DATABASE_URL=postgresql://localhost/clearledgr_test`
#          - CI pipelines with a service container provisioned separately
#   3. TEST_DB_ENGINE=postgres (default) + no URL → spin up a
#        testcontainer. Requires Docker daemon reachable.
#
# Default is Postgres. The full suite (2405 tests) passes on both
# engines as of 2026-04-22 — no known dialect divergence — so running
# against Postgres by default means any future dialect regression
# gets caught in CI rather than in production.

_TEST_DB_ENGINE = os.environ.get("TEST_DB_ENGINE", "postgres").strip().lower()


def _resolve_postgres_test_database_url():
    """Return a Postgres DSN to point tests at, spinning a container if needed.

    Returns a tuple ``(database_url, container)`` where ``container`` is
    either a running PostgresContainer instance (when we spun one up, so
    we can tear it down) or ``None`` (when the caller supplied an
    external URL via TEST_DATABASE_URL). Returning the container from
    the fixture keeps the teardown in the same scope as the startup.
    """
    explicit = os.environ.get("TEST_DATABASE_URL", "").strip()
    if explicit:
        return explicit, None

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        raise RuntimeError(
            "TEST_DB_ENGINE=postgres but testcontainers is not installed. "
            "Run `pip install 'testcontainers[postgresql]>=4.0.0'` or set "
            "TEST_DATABASE_URL to point tests at an existing Postgres."
        ) from exc

    container = PostgresContainer("postgres:15-alpine")
    container.start()
    # testcontainers returns psycopg2-style URLs by default; normalise to
    # `postgresql://` so psycopg3 (what the app uses) accepts it.
    url = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    return url, container


@pytest.fixture(scope="session")
def postgres_test_db():
    """Session-scoped Postgres backend for the test suite.

    When TEST_DB_ENGINE=postgres, sets DATABASE_URL to a real Postgres
    instance (explicit via TEST_DATABASE_URL, or a fresh testcontainer
    otherwise) and runs the migration chain against it once. Individual
    tests then get isolation via the per-test truncation fixture below.

    When TEST_DB_ENGINE=sqlite (default), this fixture is inert — tests
    keep using the existing per-test temp-file SQLite pattern and nothing
    changes.
    """
    if _TEST_DB_ENGINE != "postgres":
        yield None
        return

    url, container = _resolve_postgres_test_database_url()

    # Seed the env var the app reads (database.py:384) so every
    # fresh `get_db()` call picks Postgres. Clear CLEARLEDGR_DB_PATH
    # so it doesn't fight the Postgres URL.
    prior_db_url = os.environ.get("DATABASE_URL")
    prior_db_path = os.environ.get("CLEARLEDGR_DB_PATH")
    os.environ["DATABASE_URL"] = url
    os.environ.pop("CLEARLEDGR_DB_PATH", None)

    # Reset the DB singleton so the next `get_db()` reads the new URL,
    # then initialize so migrations run against the fresh Postgres.
    import clearledgr.core.database as _db_mod
    _db_mod._DB_INSTANCE = None
    _db_mod.get_db().initialize()

    try:
        yield url
    finally:
        # Restore env so non-test runs in the same process don't inherit
        # our DATABASE_URL. (Paranoid; pytest usually exits the process.)
        if prior_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior_db_url
        if prior_db_path is not None:
            os.environ["CLEARLEDGR_DB_PATH"] = prior_db_path
        _db_mod._DB_INSTANCE = None
        if container is not None:
            try:
                container.stop()
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _reset_postgres_test_db_between_tests(request, postgres_test_db):
    """Per-test truncation so tests get a clean database without restart.

    On Postgres: after each test, TRUNCATE every row across every
    user table in the public schema (RESTART IDENTITY zeroes
    auto-increment columns; CASCADE handles FKs). Container reuse
    across tests amortises the startup cost; per-test truncate keeps
    state isolation at roughly the same blast radius as the existing
    SQLite per-test temp-file pattern.

    On SQLite: no-op. Tests still rely on per-test temp-file
    instantiation for isolation.
    """
    yield
    if postgres_test_db is None:
        return
    try:
        import clearledgr.core.database as _db_mod
        db = _db_mod.get_db()
        db.initialize()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT string_agg(format('%I.%I', schemaname, tablename), ', ') "
                "FROM pg_tables WHERE schemaname = 'public'"
            )
            row = cur.fetchone()
            table_list = row[0] if row else None
            if table_list:
                cur.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
                conn.commit()
    except Exception:
        # If truncation fails, the next test will likely fail loudly —
        # which is better than silently swallowing a stale-state bug.
        # Intentional pass for the fixture contract.
        pass
