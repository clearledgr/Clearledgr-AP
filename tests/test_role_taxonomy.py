"""Tests for Phase 2.3 — five-role thesis taxonomy (DESIGN_THESIS.md §17).

Covers:
  - ROLE_RANK map + has_at_least pure comparison
  - normalize_user_role for legacy → canonical migration at every
    read boundary (token decode, reconcile, predicates)
  - The six human predicates (has_read_only through has_owner) respect
    the additive-upward semantics
  - The legacy predicates (has_ops_access, has_admin_access,
    has_fraud_control_admin) keep working but delegate to the rank
    system and respect the legacy → canonical mapping
  - The five FastAPI dependency functions
  - Migration v15 rewrites legacy users.role strings in place
  - Token decode normalizes legacy roles automatically
  - CFO-gated API endpoints from Phase 1.2a / 2.1.b / 2.2 still
    require CFO or owner — no regression
"""
from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from fastapi import HTTPException


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "role_taxonomy.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


# ===========================================================================
# ROLE_RANK + has_at_least
# ===========================================================================


class TestRoleRank:

    def test_rank_map_is_strictly_increasing(self):
        from clearledgr.core.auth import (
            ROLE_RANK, ROLE_READ_ONLY, ROLE_AP_CLERK, ROLE_AP_MANAGER,
            ROLE_FINANCIAL_CONTROLLER, ROLE_CFO, ROLE_OWNER,
        )
        assert ROLE_RANK[ROLE_READ_ONLY] < ROLE_RANK[ROLE_AP_CLERK]
        assert ROLE_RANK[ROLE_AP_CLERK] < ROLE_RANK[ROLE_AP_MANAGER]
        assert ROLE_RANK[ROLE_AP_MANAGER] < ROLE_RANK[ROLE_FINANCIAL_CONTROLLER]
        assert ROLE_RANK[ROLE_FINANCIAL_CONTROLLER] < ROLE_RANK[ROLE_CFO]
        assert ROLE_RANK[ROLE_CFO] < ROLE_RANK[ROLE_OWNER]

    def test_api_equals_owner_rank(self):
        from clearledgr.core.auth import ROLE_RANK, ROLE_OWNER, ROLE_API
        assert ROLE_RANK[ROLE_API] == ROLE_RANK[ROLE_OWNER]

    def test_has_at_least_owner_beats_everything(self):
        from clearledgr.core.auth import has_at_least
        assert has_at_least("owner", "read_only")
        assert has_at_least("owner", "ap_clerk")
        assert has_at_least("owner", "ap_manager")
        assert has_at_least("owner", "financial_controller")
        assert has_at_least("owner", "cfo")
        assert has_at_least("owner", "owner")

    def test_has_at_least_equality(self):
        from clearledgr.core.auth import has_at_least
        assert has_at_least("cfo", "cfo")
        assert has_at_least("ap_manager", "ap_manager")

    def test_has_at_least_strict_ordering(self):
        from clearledgr.core.auth import has_at_least
        assert has_at_least("cfo", "financial_controller")
        assert has_at_least("financial_controller", "ap_manager")
        assert has_at_least("ap_manager", "ap_clerk")
        assert has_at_least("ap_clerk", "read_only")

    def test_has_at_least_reverse_ordering_rejects(self):
        from clearledgr.core.auth import has_at_least
        assert not has_at_least("read_only", "ap_clerk")
        assert not has_at_least("ap_clerk", "ap_manager")
        assert not has_at_least("ap_manager", "financial_controller")
        assert not has_at_least("financial_controller", "cfo")
        assert not has_at_least("cfo", "owner")

    def test_has_at_least_unknown_role_returns_false(self):
        from clearledgr.core.auth import has_at_least
        assert not has_at_least("garbage", "ap_clerk")
        assert not has_at_least("", "ap_clerk")
        assert not has_at_least(None, "ap_clerk")


# ===========================================================================
# normalize_user_role
# ===========================================================================


class TestNormalizeUserRole:

    def test_canonical_values_pass_through(self):
        from clearledgr.core.auth import normalize_user_role
        for role in ("read_only", "ap_clerk", "ap_manager", "financial_controller", "cfo", "owner", "api"):
            assert normalize_user_role(role) == role

    def test_legacy_user_becomes_ap_clerk(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("user") == "ap_clerk"

    def test_legacy_member_becomes_ap_clerk(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("member") == "ap_clerk"

    def test_legacy_operator_becomes_ap_manager(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("operator") == "ap_manager"

    def test_legacy_admin_becomes_financial_controller(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("admin") == "financial_controller"

    def test_legacy_viewer_becomes_read_only(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("viewer") == "read_only"

    def test_case_insensitive(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("CFO") == "cfo"
        assert normalize_user_role("Admin") == "financial_controller"
        assert normalize_user_role("OPERATOR") == "ap_manager"

    def test_whitespace_stripped(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("  cfo  ") == "cfo"

    def test_empty_returns_empty(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role(None) == ""
        assert normalize_user_role("") == ""
        assert normalize_user_role("   ") == ""

    def test_unknown_preserved(self):
        """Unknown roles are NOT upgraded to a default — predicates
        will reject them downstream, which is the safer failure mode
        than silently promoting garbage to ap_clerk."""
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("totally_unknown") == "totally_unknown"


# ===========================================================================
# Human predicates
# ===========================================================================


class TestPredicates:

    def test_has_cfo_accepts_cfo_and_owner(self):
        from clearledgr.core.auth import has_cfo
        assert has_cfo("cfo") is True
        assert has_cfo("owner") is True
        assert has_cfo("api") is True  # api == owner rank

    def test_has_cfo_rejects_lower_roles(self):
        from clearledgr.core.auth import has_cfo
        assert has_cfo("financial_controller") is False
        assert has_cfo("ap_manager") is False
        assert has_cfo("ap_clerk") is False
        assert has_cfo("read_only") is False

    def test_has_financial_controller_additive(self):
        from clearledgr.core.auth import has_financial_controller
        assert has_financial_controller("owner") is True
        assert has_financial_controller("cfo") is True
        assert has_financial_controller("financial_controller") is True
        assert has_financial_controller("ap_manager") is False
        assert has_financial_controller("ap_clerk") is False

    def test_has_ap_manager_additive(self):
        from clearledgr.core.auth import has_ap_manager
        assert has_ap_manager("owner") is True
        assert has_ap_manager("cfo") is True
        assert has_ap_manager("financial_controller") is True
        assert has_ap_manager("ap_manager") is True
        assert has_ap_manager("ap_clerk") is False
        assert has_ap_manager("read_only") is False

    def test_has_ap_clerk_accepts_write_roles(self):
        from clearledgr.core.auth import has_ap_clerk
        assert has_ap_clerk("owner") is True
        assert has_ap_clerk("cfo") is True
        assert has_ap_clerk("financial_controller") is True
        assert has_ap_clerk("ap_manager") is True
        assert has_ap_clerk("ap_clerk") is True
        assert has_ap_clerk("read_only") is False

    def test_has_read_only_accepts_everything_except_unknown(self):
        from clearledgr.core.auth import has_read_only
        for role in ("owner", "cfo", "financial_controller", "ap_manager", "ap_clerk", "read_only"):
            assert has_read_only(role) is True
        assert has_read_only("garbage") is False

    def test_predicates_respect_legacy_migration(self):
        """The legacy → canonical mapping runs at every read boundary,
        so the predicates accept legacy values correctly."""
        from clearledgr.core.auth import (
            has_cfo, has_financial_controller, has_ap_manager, has_ap_clerk,
        )
        # "admin" legacy → financial_controller rank 60
        assert has_cfo("admin") is False
        assert has_financial_controller("admin") is True
        assert has_ap_manager("admin") is True
        assert has_ap_clerk("admin") is True

        # "operator" legacy → ap_manager rank 40
        assert has_financial_controller("operator") is False
        assert has_ap_manager("operator") is True
        assert has_ap_clerk("operator") is True

        # "user"/"member" legacy → ap_clerk rank 20
        assert has_ap_manager("user") is False
        assert has_ap_clerk("user") is True
        assert has_ap_clerk("member") is True

        # "viewer" legacy → read_only rank 10
        assert has_ap_clerk("viewer") is False


# ===========================================================================
# Legacy predicates (has_ops_access, has_admin_access, has_fraud_control_admin)
# ===========================================================================


class TestLegacyPredicatesStillWork:

    def test_has_ops_access_accepts_ap_manager_and_up(self):
        from clearledgr.core.auth import has_ops_access
        assert has_ops_access("owner") is True
        assert has_ops_access("cfo") is True
        assert has_ops_access("financial_controller") is True
        assert has_ops_access("ap_manager") is True
        assert has_ops_access("ap_clerk") is False
        assert has_ops_access("read_only") is False
        # Legacy values still work via normalize
        assert has_ops_access("operator") is True
        assert has_ops_access("admin") is True
        assert has_ops_access("user") is False

    def test_has_admin_access_accepts_financial_controller_and_up(self):
        from clearledgr.core.auth import has_admin_access
        assert has_admin_access("owner") is True
        assert has_admin_access("cfo") is True
        assert has_admin_access("financial_controller") is True
        assert has_admin_access("ap_manager") is False
        # Legacy values
        assert has_admin_access("admin") is True
        assert has_admin_access("operator") is False

    def test_has_fraud_control_admin_is_has_cfo(self):
        from clearledgr.core.auth import has_fraud_control_admin, has_cfo
        for role in ("owner", "cfo", "financial_controller", "ap_manager", "ap_clerk", "read_only", "admin", "operator"):
            assert has_fraud_control_admin(role) == has_cfo(role)


# ===========================================================================
# FastAPI dependency functions
# ===========================================================================


class TestFastAPIDependencies:

    def _user_with_role(self, role: str):
        from clearledgr.core.auth import TokenData
        return TokenData(
            user_id="u1",
            email="u1@test",
            organization_id="org_t",
            role=role,
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    def test_require_cfo_allows_cfo(self):
        from clearledgr.core.auth import require_cfo
        user = require_cfo(self._user_with_role("cfo"))
        assert user.role == "cfo"

    def test_require_cfo_allows_owner(self):
        from clearledgr.core.auth import require_cfo
        user = require_cfo(self._user_with_role("owner"))
        assert user.role == "owner"

    def test_require_cfo_rejects_fc(self):
        from clearledgr.core.auth import require_cfo
        with pytest.raises(HTTPException) as exc:
            require_cfo(self._user_with_role("financial_controller"))
        assert exc.value.status_code == 403
        assert exc.value.detail == "cfo_role_required"

    def test_require_cfo_rejects_legacy_admin(self):
        """Legacy admin maps to financial_controller which is below CFO."""
        from clearledgr.core.auth import require_cfo
        with pytest.raises(HTTPException):
            require_cfo(self._user_with_role("admin"))

    def test_require_financial_controller_allows_fc(self):
        from clearledgr.core.auth import require_financial_controller
        user = require_financial_controller(self._user_with_role("financial_controller"))
        assert user is not None

    def test_require_financial_controller_rejects_ap_manager(self):
        from clearledgr.core.auth import require_financial_controller
        with pytest.raises(HTTPException) as exc:
            require_financial_controller(self._user_with_role("ap_manager"))
        assert exc.value.detail == "financial_controller_role_required"

    def test_require_ap_manager_allows_all_above(self):
        from clearledgr.core.auth import require_ap_manager
        for role in ("ap_manager", "financial_controller", "cfo", "owner", "api"):
            user = require_ap_manager(self._user_with_role(role))
            assert user is not None

    def test_require_ap_manager_rejects_clerk(self):
        from clearledgr.core.auth import require_ap_manager
        with pytest.raises(HTTPException):
            require_ap_manager(self._user_with_role("ap_clerk"))

    def test_require_ap_clerk_rejects_read_only(self):
        from clearledgr.core.auth import require_ap_clerk
        with pytest.raises(HTTPException):
            require_ap_clerk(self._user_with_role("read_only"))

    def test_require_ap_clerk_accepts_clerk(self):
        from clearledgr.core.auth import require_ap_clerk
        user = require_ap_clerk(self._user_with_role("ap_clerk"))
        assert user is not None


# ===========================================================================
# Migration v15
# ===========================================================================


class TestMigrationV15:

    def _seed_legacy_user(self, db, user_id: str, role: str):
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "INSERT INTO users (id, email, name, organization_id, role, "
                    "is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)"
                ),
                (
                    user_id,
                    f"{user_id}@test.com",
                    user_id,
                    "org_t",
                    role,
                    1,
                    "2026-01-01",
                    "2026-01-01",
                ),
            )
            conn.commit()

    def _run_v15(self, db):
        from clearledgr.core.migrations import _MIGRATIONS
        m15 = next(m for m in _MIGRATIONS if m[0] == 15)
        with db.connect() as conn:
            cur = conn.cursor()
            m15[2](cur, db)
            conn.commit()

    def test_user_becomes_ap_clerk(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-u", "user")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-u")["role"] == "ap_clerk"

    def test_member_becomes_ap_clerk(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-m", "member")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-m")["role"] == "ap_clerk"

    def test_operator_becomes_ap_manager(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-o", "operator")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-o")["role"] == "ap_manager"

    def test_admin_becomes_financial_controller(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-a", "admin")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-a")["role"] == "financial_controller"

    def test_viewer_becomes_read_only(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-v", "viewer")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-v")["role"] == "read_only"

    def test_cfo_untouched(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-c", "cfo")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-c")["role"] == "cfo"

    def test_owner_untouched(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-own", "owner")
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-own")["role"] == "owner"

    def test_unknown_role_untouched(self, tmp_db):
        self._seed_legacy_user(tmp_db, "USR-garbage", "garbage_role")
        self._run_v15(tmp_db)
        # Unknown values are preserved so predicates reject them
        assert tmp_db.get_user("USR-garbage")["role"] == "garbage_role"

    def test_migration_idempotent(self, tmp_db):
        """Running v15 twice is a no-op after the first run."""
        self._seed_legacy_user(tmp_db, "USR-op", "operator")
        self._run_v15(tmp_db)
        self._run_v15(tmp_db)
        assert tmp_db.get_user("USR-op")["role"] == "ap_manager"


# ===========================================================================
# Token decode + reconcile normalize legacy roles on the wire
# ===========================================================================


class TestTokenDecodeNormalizes:

    def test_token_decode_upgrades_legacy_role(self):
        from clearledgr.core.auth import _token_data_from_payload
        payload = {
            "sub": "u1",
            "email": "u1@test.com",
            "org": "org_t",
            "role": "admin",  # legacy
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        token_data = _token_data_from_payload(payload)
        assert token_data.role == "financial_controller"

    def test_token_decode_default_when_role_missing(self):
        from clearledgr.core.auth import _token_data_from_payload
        payload = {
            "sub": "u1",
            "email": "u1@test.com",
            "org": "org_t",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        token_data = _token_data_from_payload(payload)
        assert token_data.role == "ap_clerk"

    def test_token_decode_preserves_canonical_role(self):
        from clearledgr.core.auth import _token_data_from_payload
        payload = {
            "sub": "u1",
            "email": "u1@test.com",
            "org": "org_t",
            "role": "cfo",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        token_data = _token_data_from_payload(payload)
        assert token_data.role == "cfo"


# ===========================================================================
# Regression: existing CFO-gated endpoints still enforce CFO or owner
# ===========================================================================


class TestCFOGatedEndpointsStillEnforce:
    """After Phase 2.3 renamed require_fraud_control_admin → require_cfo,
    the three CFO-gated API surfaces (fraud_controls, iban_verification,
    vendor_domains) must still reject non-CFO users. This is a
    regression guard for the rename."""

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        import main

        db = ClearledgrDB(db_path=str(tmp_path / "phase23_api.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _override_with_role(self, main, role: str):
        from clearledgr.core.auth import TokenData, get_current_user, require_cfo

        def _user():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role=role,
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        main.app.dependency_overrides[require_cfo] = _user

    def test_fraud_controls_put_requires_cfo(self, app_client):
        client, main, db = app_client
        db.create_organization("org_t", name="X", settings={})
        # Non-CFO — should 403
        from clearledgr.core.auth import TokenData, get_current_user

        def _ap_manager():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="ap_manager",
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _ap_manager
        try:
            resp = client.put(
                "/fraud-controls/org_t",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 403
            assert resp.json()["detail"] == "cfo_role_required"
        finally:
            main.app.dependency_overrides.clear()

    def test_fraud_controls_put_cfo_passes(self, app_client):
        client, main, db = app_client
        db.create_organization("org_t", name="X", settings={})
        self._override_with_role(main, "cfo")
        try:
            resp = client.put(
                "/fraud-controls/org_t",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 200
        finally:
            main.app.dependency_overrides.clear()

    def test_fraud_controls_put_owner_passes(self, app_client):
        client, main, db = app_client
        db.create_organization("org_t", name="X", settings={})
        self._override_with_role(main, "owner")
        try:
            resp = client.put(
                "/fraud-controls/org_t",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 200
        finally:
            main.app.dependency_overrides.clear()

    def test_fraud_controls_put_legacy_admin_still_rejected(self, app_client):
        """Legacy admin maps to financial_controller — not CFO."""
        client, main, db = app_client
        db.create_organization("org_t", name="X", settings={})
        from clearledgr.core.auth import TokenData, get_current_user

        def _legacy_admin():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="admin",  # legacy
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _legacy_admin
        try:
            resp = client.put(
                "/fraud-controls/org_t",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()
