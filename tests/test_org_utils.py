"""Unit tests for ``clearledgr/core/org_utils.py`` — the canonical
replacement for M4 ``or "org-test"`` coercion.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from solden.core.org_utils import (
    OrgIdMissing,
    assert_org_id,
    coerce_org_id,
    require_org,
)


class TestAssertOrgId:
    def test_returns_stripped_org_on_valid_input(self):
        assert assert_org_id("acme") == "acme"
        assert assert_org_id("  acme  ") == "acme"

    def test_raises_on_empty_string(self):
        with pytest.raises(OrgIdMissing, match="organization_id is required"):
            assert_org_id("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(OrgIdMissing):
            assert_org_id("   ")

    def test_raises_on_none(self):
        with pytest.raises(OrgIdMissing):
            assert_org_id(None)

    def test_context_appended_to_error_message(self):
        with pytest.raises(OrgIdMissing, match=r"in create_payment"):
            assert_org_id("", context="create_payment")

    def test_org_id_missing_is_value_error_subclass(self):
        # Existing ``except ValueError`` handlers must still catch it.
        with pytest.raises(ValueError):
            assert_org_id("")

    def test_default_literal_rejected_post_tenant_rename(self):
        # M20 tenant-rename: ``"default"`` is no longer a real tenant
        # id. Migration v79 renamed any extant org with id="default"
        # to ``"org_legacy_default"`` and added a CHECK constraint
        # blocking future rows. ``assert_org_id`` rejects the literal
        # the same way it rejects an empty string, a write path that
        # somehow surfaces ``"default"`` is a regression.
        with pytest.raises(OrgIdMissing):
            assert_org_id("default")

    def test_unprovisioned_sentinel_rejected(self):
        # The ``_unprovisioned`` sentinel is the post-OAuth placeholder
        # for users awaiting manual provisioning. It must NEVER reach
        # a write path — ``require_org`` raises 403 before the request
        # gets here, but defense-in-depth says ``assert_org_id``
        # rejects it too.
        with pytest.raises(OrgIdMissing):
            assert_org_id("_unprovisioned")


class TestRequireOrg:
    def _user(self, org="acme"):
        return SimpleNamespace(organization_id=org)

    def test_returns_session_org_no_requested(self):
        assert require_org(self._user("acme")) == "acme"

    def test_raises_403_on_empty_session_org(self):
        with pytest.raises(HTTPException) as exc_info:
            require_org(self._user(""))
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "user_missing_organization_id"

    def test_raises_403_on_no_organization_id_attribute(self):
        with pytest.raises(HTTPException) as exc_info:
            require_org(SimpleNamespace())  # no attr at all
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "user_missing_organization_id"

    def test_raises_403_on_none_session_org(self):
        with pytest.raises(HTTPException):
            require_org(self._user(None))

    def test_requested_empty_returns_session_org(self):
        assert require_org(self._user("acme"), requested="") == "acme"
        assert require_org(self._user("acme"), requested=None) == "acme"

    def test_requested_legacy_default_treated_as_empty(self):
        # The "default" string is a legacy placeholder for "no value
        # supplied", extension clients in the wild still send it.
        assert require_org(self._user("acme"), requested="default") == "acme"

    def test_requested_matches_session_returns_session(self):
        assert require_org(self._user("acme"), requested="acme") == "acme"

    def test_requested_mismatch_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            require_org(self._user("acme"), requested="other-tenant")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "org_mismatch"

    def test_legacy_default_session_org_rejected_post_tenant_rename(self):
        """M20 tenant-rename: a session whose org is the literal
        ``"default"`` is no longer treated as a real tenant. Migration
        v79 renamed any such row to ``"org_legacy_default"``; if
        ``require_org`` ever sees ``"default"`` again it's a hard
        regression, fail closed with 403, same as a missing org.
        """
        u = self._user("default")
        with pytest.raises(HTTPException) as exc_info:
            require_org(u)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "user_missing_organization_id"

    def test_unprovisioned_session_org_rejects_with_pending_detail(self):
        """The ``_unprovisioned`` sentinel marks users who have
        OAuth'd but aren't bound to a real org yet. ``require_org``
        rejects them with the distinct
        ``organization_pending_provisioning`` detail so the frontend
        can route to a "your organization isn't set up yet" screen
        instead of the generic 403 page."""
        u = self._user("_unprovisioned")
        with pytest.raises(HTTPException) as exc_info:
            require_org(u)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "organization_pending_provisioning"


class TestCoerceOrgId:
    def test_returns_stripped_string(self):
        assert coerce_org_id("acme") == "acme"
        assert coerce_org_id("  acme  ") == "acme"

    def test_returns_none_on_empty(self):
        assert coerce_org_id("") is None
        assert coerce_org_id(None) is None
        assert coerce_org_id("   ") is None
