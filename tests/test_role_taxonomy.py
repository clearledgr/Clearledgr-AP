"""Tests for the v89 two-axis auth model.

Pre-v89 this file documented the single-axis five-role taxonomy
(ap_clerk → ap_manager → financial_controller → cfo → owner).
Migration v89 split that axis into:

  * ``workspace_role`` (org governance) — read_only / member / admin /
    owner / api.
  * ``user_box_roles[ap_item]`` (per-Box AP rank) — viewer / clerk /
    approver / controller.

The legacy ``ROLE_*`` constants from pre-v89 survive as aliases pointing
at their workspace-axis equivalents (``ROLE_AP_CLERK == "member"``,
``ROLE_CFO == "admin"``, etc.). The legacy ``users.role`` column and
``normalize_user_role`` helper also survive for the sweep window;
v90 drops the column.

This file covers:
  - WORKSPACE_ROLE_RANK + has_workspace_role rank comparison
  - normalize_workspace_role legacy → canonical mapping
  - AP_ROLE_RANK + has_ap_role rank comparison + normalize_ap_role
  - Workspace and AP predicates respect additive-upward semantics
  - Legacy predicates (has_ops_access, has_admin_access, has_cfo,
    has_at_least) keep working but delegate to the new axes
  - FastAPI dependencies (require_workspace_*, require_ap_*, plus the
    legacy require_cfo / require_admin_user / require_ap_manager aliases)
  - Migration v15 still rewrites legacy users.role values in place
  - Token decode populates both ``role`` and ``workspace_role``
  - CFO-gated API endpoints still gate (collapsed to workspace_admin)
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


# ===========================================================================
# ROLE_RANK + has_at_least
# ===========================================================================


class TestRoleRank:

    def test_rank_map_is_strictly_increasing(self):
        """v89 workspace-axis rank: read_only < member < admin < owner.
        Legacy aliases (ROLE_AP_CLERK = member, ROLE_CFO = admin)
        collapse to a non-strict chain — assert the underlying
        workspace rank directly instead.
        """
        from clearledgr.core.auth import (
            WORKSPACE_ROLE_RANK,
            WORKSPACE_ROLE_READ_ONLY,
            WORKSPACE_ROLE_MEMBER,
            WORKSPACE_ROLE_ADMIN,
            WORKSPACE_ROLE_OWNER,
        )
        assert WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_READ_ONLY] < WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_MEMBER]
        assert WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_MEMBER] < WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_ADMIN]
        assert WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_ADMIN] < WORKSPACE_ROLE_RANK[WORKSPACE_ROLE_OWNER]

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
        """has_at_least operates on the workspace_role axis under v89.
        ap_manager/ap_clerk/operator all map to ``member`` so the
        comparisons among them are non-strict; assert only the
        cross-tier rejections (read_only < member < admin < owner).
        """
        from clearledgr.core.auth import has_at_least
        assert not has_at_least("read_only", "member")
        assert not has_at_least("member", "admin")
        assert not has_at_least("admin", "owner")
        # CFO and financial_controller both fold to ``admin`` under v89,
        # so the legacy ``cfo > financial_controller`` strict chain is
        # gone. They share rank now.
        assert has_at_least("cfo", "financial_controller")
        assert has_at_least("financial_controller", "cfo")

    def test_has_at_least_unknown_role_returns_false(self):
        from clearledgr.core.auth import has_at_least
        assert not has_at_least("garbage", "ap_clerk")
        assert not has_at_least("", "ap_clerk")
        assert not has_at_least(None, "ap_clerk")


# ===========================================================================
# normalize_user_role
# ===========================================================================


class TestNormalizeUserRole:
    """``normalize_user_role`` is the v89 alias for
    ``normalize_workspace_role`` — every value collapses to a
    workspace-axis canonical token. Pre-v89 it returned legacy
    single-axis values (``ap_clerk`` / ``ap_manager`` / etc.); under
    v89 those return ``member`` / ``admin`` / ``read_only``.
    """

    def test_canonical_workspace_values_pass_through(self):
        from clearledgr.core.auth import normalize_user_role
        for role in ("read_only", "member", "admin", "owner", "api"):
            assert normalize_user_role(role) == role

    def test_legacy_user_becomes_member(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("user") == "member"

    def test_legacy_member_stays_member(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("member") == "member"

    def test_legacy_operator_becomes_member(self):
        """Pre-v89 ``operator`` mapped to AP-axis ap_manager. Under v89
        the AP rank moved to user_box_roles; the workspace axis sees
        only ``member``.
        """
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("operator") == "member"

    def test_legacy_admin_stays_admin(self):
        """Pre-v89 ``admin`` was a legacy alias for financial_controller.
        Under v89 ``admin`` IS the canonical workspace_role value.
        """
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("admin") == "admin"

    def test_legacy_viewer_becomes_read_only(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("viewer") == "read_only"

    def test_legacy_ap_clerk_and_ap_manager_become_member(self):
        """The AP-flavoured single-axis values collapse onto the
        workspace axis as ``member``. AP rank now lives on
        ``user_box_roles[ap_item]``.
        """
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("ap_clerk") == "member"
        assert normalize_user_role("ap_manager") == "member"

    def test_legacy_cfo_and_financial_controller_become_admin(self):
        """CFO and financial_controller used to be distinct ranks
        above each other; v89 collapses both to workspace_admin.
        """
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("cfo") == "admin"
        assert normalize_user_role("financial_controller") == "admin"

    def test_case_insensitive(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("CFO") == "admin"
        assert normalize_user_role("Admin") == "admin"
        assert normalize_user_role("OPERATOR") == "member"

    def test_whitespace_stripped(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("  admin  ") == "admin"

    def test_empty_returns_empty(self):
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role(None) == ""
        assert normalize_user_role("") == ""
        assert normalize_user_role("   ") == ""

    def test_unknown_returns_empty(self):
        """Pre-v89 unknowns were preserved as-is. Under v89
        ``normalize_workspace_role`` returns ``""`` for any value
        outside the workspace enum + the legacy mapping table, so
        every ``has_workspace_*`` predicate rejects them. Empty is
        the strictly-safer failure mode.
        """
        from clearledgr.core.auth import normalize_user_role
        assert normalize_user_role("totally_unknown") == ""


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
        """v89: ``has_cfo`` is aliased to ``has_workspace_admin``.
        CFO + financial_controller + admin all collapse to admin
        rank and pass. Roles below admin still reject.
        """
        from clearledgr.core.auth import has_cfo
        # Workspace admin and above pass — CFO is no longer strictly
        # above financial_controller under v89.
        assert has_cfo("financial_controller") is True
        assert has_cfo("admin") is True
        # Member-tier and below still reject.
        assert has_cfo("ap_manager") is False
        assert has_cfo("ap_clerk") is False
        assert has_cfo("member") is False
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
        """v89: workspace-axis predicates (has_cfo / has_financial_controller
        / has_admin_access) all alias to ``has_workspace_admin``. AP-axis
        predicates (has_ap_manager / has_ap_clerk) read the AP-rank
        derived from the legacy single-axis value via
        ``_LEGACY_TO_AP_ROLE``.
        """
        from clearledgr.core.auth import (
            has_cfo, has_financial_controller, has_ap_manager, has_ap_clerk,
        )
        # "admin" → workspace_admin → passes both CFO and FC gates
        # under v89's collapsed model.
        assert has_cfo("admin") is True
        assert has_financial_controller("admin") is True
        # admin maps to AP ``controller`` on the box axis, so AP
        # predicates accept it.
        assert has_ap_manager("admin") is True
        assert has_ap_clerk("admin") is True

        # "operator" → workspace_member, AP rank approver.
        assert has_financial_controller("operator") is False
        assert has_ap_manager("operator") is True
        assert has_ap_clerk("operator") is True

        # "user" / "member" → workspace_member, AP rank clerk.
        assert has_ap_manager("user") is False
        assert has_ap_clerk("user") is True
        assert has_ap_clerk("member") is True

        # "viewer" → workspace_read_only, AP rank viewer.
        assert has_ap_clerk("viewer") is False


# ===========================================================================
# Legacy predicates (has_ops_access, has_admin_access, has_fraud_control_admin)
# ===========================================================================


class TestLegacyPredicatesStillWork:

    def test_has_ops_access_accepts_workspace_member_and_up(self):
        """v89: ``has_ops_access`` is aliased to ``has_workspace_member``.
        Pre-v89 it gated AP-Manager-and-above strictly; under v89
        ap_clerk and ap_manager both fold to ``member`` so both pass
        the workspace-axis gate. Per-Box AP authority is enforced
        separately via ``has_ap_approver`` / ``has_ap_controller``.
        """
        from clearledgr.core.auth import has_ops_access
        assert has_ops_access("owner") is True
        assert has_ops_access("cfo") is True
        assert has_ops_access("financial_controller") is True
        assert has_ops_access("ap_manager") is True
        # ap_clerk + member used to fail this gate; under v89 they pass
        # the workspace-member axis. AP write authority is checked
        # separately via the AP-side predicates.
        assert has_ops_access("ap_clerk") is True
        assert has_ops_access("member") is True
        assert has_ops_access("operator") is True
        assert has_ops_access("admin") is True
        assert has_ops_access("user") is True
        # read_only is below member and still rejects.
        assert has_ops_access("read_only") is False
        assert has_ops_access("viewer") is False

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

    def test_require_cfo_accepts_financial_controller(self):
        """v89 collapses CFO + financial_controller + admin onto the
        ``workspace_admin`` gate. The legacy ``require_cfo`` alias now
        passes anyone with admin authority (which every CFO and FC
        had pre-v89 anyway). Lower roles still reject with the
        ``cfo_role_required`` detail string callers depend on.
        """
        from clearledgr.core.auth import require_cfo
        user = require_cfo(self._user_with_role("financial_controller"))
        assert user is not None

    def test_require_cfo_rejects_member_tier(self):
        from clearledgr.core.auth import require_cfo
        with pytest.raises(HTTPException) as exc:
            require_cfo(self._user_with_role("ap_manager"))
        assert exc.value.status_code == 403
        assert exc.value.detail == "cfo_role_required"

    def test_require_cfo_accepts_legacy_admin(self):
        """Pre-v89 ``admin`` was a legacy alias for financial_controller
        (rank below CFO). Under v89 ``admin`` IS the canonical
        workspace_admin value, which now equals CFO authority.
        """
        from clearledgr.core.auth import require_cfo
        user = require_cfo(self._user_with_role("admin"))
        assert user is not None

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
        """v89: ``require_ap_manager`` is aliased to ``require_ap_approver``.
        The AP-axis role is derived from the legacy single-axis value
        via ``_ap_role_for_user``'s fallback (DB lookup → derivation).
        ap_manager/financial_controller/cfo/owner/api all map to
        AP rank approver-or-above.
        """
        from clearledgr.core.auth import require_ap_manager
        for role in ("ap_manager", "financial_controller", "cfo", "owner"):
            user = require_ap_manager(self._user_with_role(role))
            assert user is not None
        # ``api`` is the service-account workspace_role; it carries no
        # AP-axis role by design. ``require_ap_manager`` rejects it on
        # the AP gate. Service-account writes flow through API-key
        # scopes, not human role gates.
        with pytest.raises(HTTPException):
            require_ap_manager(self._user_with_role("api"))

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
                (
                    "INSERT INTO users (id, email, name, organization_id, role, "
                    "is_active, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
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
            # autocommit so idempotent try/except patterns in the
            # migration body don't poison the txn.
            conn.autocommit = True
            try:
                cur = conn.cursor()
                m15[2](cur, db)
            finally:
                conn.autocommit = False

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

    def test_token_decode_populates_workspace_role_from_legacy(self):
        """v89: pre-v89 tokens carry only ``role``. ``_token_data_from_payload``
        derives ``workspace_role`` via the legacy mapping while
        preserving ``role`` as-stored so legacy readers keep working.
        Pre-v89 the helper rewrote ``role`` itself; under v89 only
        ``workspace_role`` is normalized.
        """
        from clearledgr.core.auth import _token_data_from_payload
        payload = {
            "sub": "u1",
            "email": "u1@test.com",
            "org": "org_t",
            "role": "admin",  # legacy / v89-canonical workspace_role
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        token_data = _token_data_from_payload(payload)
        # ``admin`` is itself the v89 canonical workspace_role.
        assert token_data.role == "admin"
        assert token_data.workspace_role == "admin"

    def test_token_decode_default_when_role_missing(self):
        """v89: when neither ``role`` nor ``workspace_role`` is on the
        payload, default to workspace_role=``member`` (the safest
        write-capable seat). Pre-v89 the default was ``ap_clerk``;
        under v89 ``ap_clerk`` collapses to ``member`` anyway.
        """
        from clearledgr.core.auth import _token_data_from_payload
        payload = {
            "sub": "u1",
            "email": "u1@test.com",
            "org": "org_t",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        token_data = _token_data_from_payload(payload)
        assert token_data.workspace_role == "member"

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
        from clearledgr.core.database import get_db
        from clearledgr.core import database as db_module
        import main

        db = get_db()
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

    def test_fraud_controls_put_legacy_admin_accepted(self, app_client):
        """v89: legacy ``admin`` IS the canonical workspace_admin value,
        which now equals CFO authority on the workspace axis. Pre-v89
        this test pinned the opposite contract; under v89 the
        collapse is intentional and ``admin`` passes the CFO gate.
        """
        client, main, db = app_client
        db.create_organization("org_t", name="X", settings={})
        from clearledgr.core.auth import TokenData, get_current_user

        def _legacy_admin():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="admin",  # legacy + v89 canonical
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _legacy_admin
        try:
            resp = client.put(
                "/fraud-controls/org_t",
                json={"payment_ceiling": 50_000.0},
            )
            # v89 collapse: workspace_admin is the canonical CFO seat.
            assert resp.status_code == 200
        finally:
            main.app.dependency_overrides.clear()
