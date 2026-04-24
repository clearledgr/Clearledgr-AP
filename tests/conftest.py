"""
Shared pytest fixtures and hooks for the Clearledgr test suite.
"""

import os

import pytest

os.environ.setdefault("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", "true")
os.environ.setdefault("CLEARLEDGR_SKIP_DEFERRED_STARTUP", "true")

# Under C.1's PG test harness, a silent SQLite fallback from a pool
# hiccup is exactly the failure mode we're trying to eliminate: tests
# succeed on SQLite without ever exercising the PG path, and a
# mid-suite pool exhaustion flips the singleton's use_postgres flag
# to False, causing 13+ downstream tests to hit a SQLite file the
# TRUNCATE fixture doesn't clean. Disable fallback so any PG problem
# surfaces as a real exception — which is the whole point of running
# tests on PG in the first place. The env var only affects tests
# because production deploys ENV=production already default to
# fallback=False.
if os.environ.get("TEST_DB_ENGINE", "postgres").strip().lower() == "postgres":
    os.environ.setdefault("CLEARLEDGR_DB_FALLBACK_SQLITE", "false")


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
    # Pre-test guard: if a previous test's tmp_db fixture left the
    # singleton as a SQLite instance (``ClearledgrDB.__init__`` reads
    # DATABASE_URL once at construction; if the env var was missing at
    # that moment, use_postgres is pinned to False for the lifetime of
    # that instance), nuke it here so the current test's ``get_db()``
    # constructs a fresh PG-backed instance. Without this pre-check,
    # trust_arc hits sqlite3.IntegrityError on the second
    # create_organization('org_t') because the SQLite singleton
    # survived the teardown ordering.
    try:
        import clearledgr.core.database as _db_mod
        if _TEST_DB_ENGINE == "postgres":
            inst = _db_mod._DB_INSTANCE
            if inst is not None and not getattr(inst, "use_postgres", True):
                _db_mod._DB_INSTANCE = None
    except Exception:
        pass
    yield
    # Reset DB singleton so each test starts with the correct DB path.
    # Under Postgres, we normally keep the session singleton alive
    # (dropping it would trigger psycopg_pool thread-join teardown and
    # deadlock mid-session). BUT — some tests flip to SQLite with
    # `db_module._DB_INSTANCE = None; monkeypatch.delenv("DATABASE_URL")`
    # and leave a dangling SQLite singleton behind. Subsequent tests
    # calling `get_db()` then silently get that SQLite DB instead of
    # the session PG, and trip a ``sqlite3.IntegrityError`` on the
    # second ``create_organization("org_t")`` — because the SQLite
    # instance is not part of the per-test TRUNCATE cycle.
    #
    # Detection: under PG mode, if the current singleton has
    # ``use_postgres=False`` it was tainted by one of those flips —
    # nuke it so the next ``get_db()`` re-constructs from env (which
    # by now has DATABASE_URL restored by monkeypatch). When the
    # singleton is healthy (use_postgres=True) we still preserve it
    # to keep the shared pool alive.
    try:
        import clearledgr.core.database as _db_mod
        if _TEST_DB_ENGINE != "postgres":
            _db_mod._DB_INSTANCE = None
        else:
            inst = _db_mod._DB_INSTANCE
            if inst is not None and not getattr(inst, "use_postgres", True):
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

    Also pre-test: restore ``DATABASE_URL`` from the session-scoped DSN.
    Some tests in the suite set ``DATABASE_URL`` to a ``sqlite:///...``
    URL directly (not via monkeypatch) and then ``os.environ.pop()`` it
    in their finally — permanently removing it. Without this restore,
    any downstream test that triggers a fresh ``ClearledgrDB()``
    construction reads ``DATABASE_URL=None`` → locks
    ``use_postgres=False`` → silently uses SQLite, and hits the
    ``sqlite3.IntegrityError: UNIQUE constraint failed`` cascade in
    trust_arc.

    On Postgres: after each test, TRUNCATE every row across every
    user table in the public schema (RESTART IDENTITY zeroes
    auto-increment columns; CASCADE handles FKs). Container reuse
    across tests amortises the startup cost; per-test truncate keeps
    state isolation at roughly the same blast radius as the existing
    SQLite per-test temp-file pattern.

    Uses a direct ``psycopg.connect()`` rather than ``get_db().connect()``
    on purpose. Many tests (``tmp_db`` fixtures in
    ``test_iban_change_freeze``, ``test_override_window``, etc.)
    monkeypatch ``_DB_INSTANCE`` to a fresh ``ClearledgrDB`` that
    opens its OWN psycopg_pool against the same session PG DB.
    Going through ``get_db()`` here could land on an exhausted or
    monkeypatch-reverted pool depending on fixture teardown order,
    and a swallowed truncate failure shows up downstream as
    ``UniqueViolation`` in the next test. A direct connect sidesteps
    every pool-ownership question — same PG DB, new FD each call,
    guaranteed to succeed if PG is up at all.

    On SQLite: no-op. Tests still rely on per-test temp-file
    instantiation for isolation.
    """
    # Pre-test: restore DATABASE_URL from the session DSN so a prior
    # test that mutated (or removed) it can't leak that state forward.
    # Also nuke a tainted SQLite singleton — constructing ClearledgrDB
    # against the old DATABASE_URL locked its use_postgres flag, and
    # ``get_db()`` won't reconstruct unless _DB_INSTANCE is None.
    if postgres_test_db is not None:
        import os as _os
        _os.environ["DATABASE_URL"] = postgres_test_db
        try:
            import clearledgr.core.database as _db_mod
            inst = _db_mod._DB_INSTANCE
            if inst is not None and not getattr(inst, "use_postgres", True):
                _db_mod._DB_INSTANCE = None
        except Exception:
            pass
    yield
    if postgres_test_db is None:
        return
    import psycopg
    url = postgres_test_db  # session fixture yields the DSN string
    try:
        conn = psycopg.connect(url, connect_timeout=5)
    except Exception as exc:
        import sys as _sys
        print(f"[conftest] truncate: could not connect to PG ({exc})", file=_sys.stderr)
        return
    try:
        conn.autocommit = False
        cur = conn.cursor()
        # Truncate every user table EXCEPT:
        #  - schema_versions: re-running every migration from v1 is
        #    expensive and some are not idempotent on re-run.
        #  - pipelines / pipeline_stages / pipeline_columns: seeded
        #    once by migration v36 with organization_id='__default__'.
        #    Tests that look up the AP-invoices pipeline rely on this
        #    seed; truncating it leaves the suite empty for the rest
        #    of the session because the migration won't re-seed.
        cur.execute(
            "SELECT string_agg(format('%I.%I', schemaname, tablename), ', ') "
            "FROM pg_tables "
            "WHERE schemaname = 'public' "
            "AND tablename NOT IN ("
            "'schema_versions', 'pipelines', 'pipeline_stages', 'pipeline_columns'"
            ")"
        )
        row = cur.fetchone()
        if row is not None:
            table_list = row[0] if row else None
            if table_list:
                cur.execute(
                    f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"
                )
                conn.commit()
    except Exception as exc:
        import sys as _sys
        print(f"[conftest] per-test truncate failed: {exc}", file=_sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass
