"""Tests for Group 5 of the agent-runtime audit: tenant isolation.

Covers:
  * FinanceAgentRuntime.__init__ rejects empty/whitespace organization_id
    rather than silently falling back to "default" (the prior behaviour
    that allowed a tokenless caller to drift onto the platform tenant).
  * _resolve_payload_org rejects cross-tenant payloads on real-org
    runtimes; accepts any payload org on the platform ("default") runtime.
  * get_platform_finance_runtime is bounded LRU + thread-safe + refreshes
    a stale db handle on cache hit.
  * resolve_org_id_for_user (FastAPI dep) raises 403 instead of
    silently routing to "default" when the user has no organization_id.
  * build_channel_runtime raises ValueError on empty org_id.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services import finance_agent_runtime as far  # noqa: E402
from clearledgr.services.agent_command_dispatch import (  # noqa: E402
    build_channel_runtime,
    resolve_org_id_for_user,
)


@pytest.fixture(autouse=True)
def _reset_runtime_cache():
    """Each test gets a fresh runtime cache so LRU/eviction tests
    don't bleed into one another."""
    far._reset_platform_finance_runtime_cache()
    yield
    far._reset_platform_finance_runtime_cache()


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    return inst


# ─── FinanceAgentRuntime.__init__ ───────────────────────────────────


class TestRuntimeInitRejectsEmptyOrg:
    def test_empty_string_raises(self, db):
        with pytest.raises(ValueError, match="organization_id is required"):
            far.FinanceAgentRuntime(
                organization_id="",
                actor_id="u1",
                db=db,
            )

    def test_whitespace_only_raises(self, db):
        with pytest.raises(ValueError, match="organization_id is required"):
            far.FinanceAgentRuntime(
                organization_id="   ",
                actor_id="u1",
                db=db,
            )

    def test_none_raises(self, db):
        with pytest.raises(ValueError, match="organization_id is required"):
            far.FinanceAgentRuntime(
                organization_id=None,  # type: ignore[arg-type]
                actor_id="u1",
                db=db,
            )

    def test_real_org_constructs(self, db):
        runtime = far.FinanceAgentRuntime(
            organization_id="orgX",
            actor_id="u1",
            db=db,
        )
        assert runtime.organization_id == "orgX"

    def test_default_org_explicitly_constructs(self, db):
        """The platform runtime ('default') is allowed when passed
        explicitly. Only empty/None is rejected. Note: the literal
        ``"default"`` here is the in-memory M10 platform sentinel,
        it's never written to a DB row (the platform runtime
        re-resolves to a target tenant via ``_resolve_payload_org``
        before any write). Migration v79's CHECK constraint blocks
        ``"default"`` from landing as ``organization_id`` on any row
        but does not affect this in-memory-only construction."""
        runtime = far.FinanceAgentRuntime(
            organization_id="default",
            actor_id="system",
            db=db,
        )
        assert runtime.organization_id == "default"

    def test_strips_surrounding_whitespace(self, db):
        runtime = far.FinanceAgentRuntime(
            organization_id="  orgY  ",
            actor_id="u1",
            db=db,
        )
        assert runtime.organization_id == "orgY"


# ─── Cross-tenant payload validation ────────────────────────────────


class TestResolvePayloadOrg:
    def test_payload_matches_runtime_org_returns_value(self, db):
        runtime = far.FinanceAgentRuntime(organization_id="orgA", actor_id="u1", db=db)
        result = runtime._resolve_payload_org(
            {"organization_id": "orgA"}, context="test"
        )
        assert result == "orgA"

    def test_payload_missing_falls_back_to_runtime_org(self, db):
        runtime = far.FinanceAgentRuntime(organization_id="orgA", actor_id="u1", db=db)
        result = runtime._resolve_payload_org({}, context="test")
        assert result == "orgA"

    def test_payload_empty_string_falls_back(self, db):
        runtime = far.FinanceAgentRuntime(organization_id="orgA", actor_id="u1", db=db)
        result = runtime._resolve_payload_org(
            {"organization_id": "  "}, context="test"
        )
        assert result == "orgA"

    def test_cross_tenant_payload_rejected_on_real_org(self, db):
        """The hazard the audit flagged: real-org runtime accepting
        a different org_id from the payload is the cross-tenant
        write path."""
        runtime = far.FinanceAgentRuntime(organization_id="orgA", actor_id="u1", db=db)
        with pytest.raises(ValueError, match="cross_tenant_write_blocked"):
            runtime._resolve_payload_org(
                {"organization_id": "orgB"}, context="test_ctx"
            )

    def test_platform_runtime_accepts_any_payload_org(self, db):
        """The platform runtime is the only legitimate cross-tenant
        dispatcher. Platform privilege is gated by the explicit
        ``is_platform=True`` flag (M10) — the legacy
        ``organization_id == "default"`` string-sentinel was the
        landmine the audit flagged."""
        runtime = far.FinanceAgentRuntime(
            organization_id="default", actor_id="system", db=db,
            is_platform=True,
        )
        result = runtime._resolve_payload_org(
            {"organization_id": "orgA"}, context="platform_dispatch"
        )
        assert result == "orgA"

    def test_default_org_runtime_without_is_platform_is_tenant_confined(self, db):
        """M10 regression: a runtime constructed with
        ``organization_id="default"`` but WITHOUT ``is_platform=True``
        must be tenant-confined like any other tenant runtime. The
        prior shape used the string ``"default"`` as the privilege
        sentinel, which any silent ``"default"`` fallback could
        accidentally trigger."""
        runtime = far.FinanceAgentRuntime(
            organization_id="default", actor_id="u1", db=db,
        )
        with pytest.raises(ValueError, match="cross_tenant_write_blocked"):
            runtime._resolve_payload_org(
                {"organization_id": "orgA"}, context="default_string_sentinel"
            )

    def test_platform_runtime_with_no_payload_org_returns_default(self, db):
        runtime = far.FinanceAgentRuntime(
            organization_id="default", actor_id="system", db=db,
            is_platform=True,
        )
        result = runtime._resolve_payload_org({}, context="test")
        assert result == "default"

    def test_error_message_includes_context_and_orgs(self, db):
        runtime = far.FinanceAgentRuntime(organization_id="orgA", actor_id="u1", db=db)
        with pytest.raises(ValueError) as exc_info:
            runtime._resolve_payload_org(
                {"organization_id": "orgB"}, context="seed_invoice"
            )
        message = str(exc_info.value)
        assert "seed_invoice" in message
        assert "orgB" in message
        assert "orgA" in message


# ─── Platform runtime cache: bounded LRU + thread-safe ──────────────


class TestPlatformRuntimeCache:
    def test_empty_org_rejected(self):
        with pytest.raises(ValueError, match="organization_id is required"):
            far.get_platform_finance_runtime("")

    def test_whitespace_org_rejected(self):
        with pytest.raises(ValueError, match="organization_id is required"):
            far.get_platform_finance_runtime("   ")

    def test_explicit_default_allowed(self):
        runtime = far.get_platform_finance_runtime("default")
        assert runtime.organization_id == "default"

    def test_returns_same_instance_on_second_call(self):
        runtime1 = far.get_platform_finance_runtime("orgA")
        runtime2 = far.get_platform_finance_runtime("orgA")
        assert runtime1 is runtime2

    def test_distinct_orgs_distinct_instances(self):
        runtime_a = far.get_platform_finance_runtime("orgA")
        runtime_b = far.get_platform_finance_runtime("orgB")
        assert runtime_a is not runtime_b

    def test_db_refreshed_on_cache_hit(self):
        """After the pool is reset (RDS failover, test teardown), the
        cached runtime should pick up the new db handle on next
        access — not keep the dead one."""
        far.get_platform_finance_runtime("orgA")  # warm the cache
        new_db = SimpleNamespace(marker="fresh-handle")
        with patch.object(far, "get_db", return_value=new_db):
            runtime = far.get_platform_finance_runtime("orgA")
        assert runtime.db is new_db

    def test_lru_eviction_when_over_cap(self, monkeypatch):
        """Bound prevents unbounded growth from malformed/attacker-
        supplied org_ids that never repeat."""
        monkeypatch.setattr(far, "_PLATFORM_RUNTIME_CACHE_MAX", 3)

        far.get_platform_finance_runtime("org1")
        far.get_platform_finance_runtime("org2")
        far.get_platform_finance_runtime("org3")
        # Cache full at 3.
        assert "org1" in far._PLATFORM_RUNTIME_CACHE
        far.get_platform_finance_runtime("org4")
        # org1 was oldest; should be evicted.
        assert "org1" not in far._PLATFORM_RUNTIME_CACHE
        assert "org4" in far._PLATFORM_RUNTIME_CACHE
        assert len(far._PLATFORM_RUNTIME_CACHE) == 3

    def test_lru_bumps_on_access(self, monkeypatch):
        monkeypatch.setattr(far, "_PLATFORM_RUNTIME_CACHE_MAX", 3)
        far.get_platform_finance_runtime("org1")
        far.get_platform_finance_runtime("org2")
        far.get_platform_finance_runtime("org3")
        # Touch org1 — should move it to the end of the LRU.
        far.get_platform_finance_runtime("org1")
        # Adding org4 should now evict org2 (the oldest), not org1.
        far.get_platform_finance_runtime("org4")
        assert "org2" not in far._PLATFORM_RUNTIME_CACHE
        assert "org1" in far._PLATFORM_RUNTIME_CACHE

    def test_concurrent_first_touch_returns_same_instance(self):
        """Two threads hitting an uncached org concurrently should
        not race-construct two runtimes (the first-touch race the
        audit flagged)."""
        results: list = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(far.get_platform_finance_runtime("orgRace"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 8
        assert all(r is results[0] for r in results), (
            "Concurrent first-touch produced multiple runtime instances"
        )


# ─── resolve_org_id_for_user (API auth dep) ────────────────────────


class TestResolveOrgIdForUser:
    def test_user_without_org_raises_403(self):
        user = SimpleNamespace(organization_id=None, role="user")
        with pytest.raises(HTTPException) as exc_info:
            resolve_org_id_for_user(user, requested_org_id="orgX")
        assert exc_info.value.status_code == 403
        assert "missing_user_organization_id" in exc_info.value.detail

    def test_user_with_empty_org_raises_403(self):
        user = SimpleNamespace(organization_id="", role="user")
        with pytest.raises(HTTPException) as exc_info:
            resolve_org_id_for_user(user, requested_org_id="orgX")
        assert exc_info.value.status_code == 403

    def test_user_with_real_org_no_request_returns_user_org(self):
        user = SimpleNamespace(organization_id="orgA", role="ap_clerk")
        assert resolve_org_id_for_user(user, requested_org_id=None) == "orgA"

    def test_non_admin_cross_org_request_rejected(self):
        user = SimpleNamespace(organization_id="orgA", role="ap_clerk")
        with pytest.raises(HTTPException) as exc_info:
            resolve_org_id_for_user(user, requested_org_id="orgB")
        assert exc_info.value.status_code == 403
        assert "org_mismatch" in exc_info.value.detail

    def test_admin_can_request_other_org(self):
        user = SimpleNamespace(organization_id="orgA", role="admin")
        result = resolve_org_id_for_user(user, requested_org_id="orgB")
        assert result == "orgB"


# ─── build_channel_runtime ──────────────────────────────────────────


class TestBuildChannelRuntime:
    def test_empty_org_rejected(self, db):
        with pytest.raises(ValueError, match="organization_id"):
            build_channel_runtime(
                organization_id="",
                actor_id="u1",
                actor_email="u1@example.com",
                db=db,
                fallback_actor="test",
            )

    def test_none_org_rejected(self, db):
        with pytest.raises(ValueError, match="organization_id"):
            build_channel_runtime(
                organization_id=None,
                actor_id="u1",
                actor_email="u1@example.com",
                db=db,
                fallback_actor="test",
            )

    def test_real_org_constructs(self, db):
        runtime = build_channel_runtime(
            organization_id="orgA",
            actor_id="u1",
            actor_email="u1@example.com",
            db=db,
            fallback_actor="test",
        )
        assert runtime.organization_id == "orgA"
