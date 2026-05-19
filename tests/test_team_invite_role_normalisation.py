"""Coverage for the team-invite role-normalisation fix.

Bug: the SPA dropdown sends canonical thesis roles (ap_clerk,
ap_manager, financial_controller, cfo, read_only), but the
``TeamInviteCreateRequest`` Pydantic model had a stale regex
(``^(admin|member|viewer|user)$``) that 422'd every modern invite.

Fix: drop the Pydantic regex, validate inside the handler via
``normalize_user_role`` + ``ROLE_RANK`` membership. Legacy tokens
still work because normalize_user_role upgrades them in place.
"""

from __future__ import annotations

from solden.api.workspace_shell import TeamInviteCreateRequest
from solden.core.auth import (
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    ROLE_FINANCIAL_CONTROLLER,
    ROLE_RANK,
    ROLE_READ_ONLY,
    normalize_user_role,
)


# ─── Pydantic model accepts every shape the SPA might send ─────────


def test_canonical_thesis_roles_pass_pydantic_validation() -> None:
    """The five roles the SPA dropdown actually sends today."""
    for role in (
        "ap_clerk",
        "ap_manager",
        "financial_controller",
        "cfo",
        "read_only",
    ):
        req = TeamInviteCreateRequest(email="test@example.com", role=role)
        assert req.role == role


def test_legacy_role_tokens_still_pass_pydantic() -> None:
    """Older SPA builds + a few API callers still ship the
    legacy vocabulary. Pydantic shouldn't reject them — the
    handler's normalize_user_role call upgrades them in place."""
    for role in ("admin", "member", "viewer", "user", "operator"):
        req = TeamInviteCreateRequest(email="test@example.com", role=role)
        assert req.role == role


def test_default_role_is_member() -> None:
    """Omitted role defaults to ``member`` which the handler then
    normalises to ``ap_clerk``."""
    req = TeamInviteCreateRequest(email="test@example.com")
    assert req.role == "member"


# ─── Handler-level normalisation ───────────────────────────────────


def test_normalize_upgrades_legacy_to_canonical() -> None:
    """normalize_user_role does the legacy → canonical mapping the
    handler relies on. Tested independently so the contract is
    pinned even if the handler refactors."""
    assert normalize_user_role("member") == ROLE_AP_CLERK
    assert normalize_user_role("admin") == ROLE_FINANCIAL_CONTROLLER
    assert normalize_user_role("viewer") == ROLE_READ_ONLY
    assert normalize_user_role("operator") == ROLE_AP_MANAGER


def test_canonical_roles_pass_through_normalize() -> None:
    """Roles already in canonical form survive normalisation unchanged."""
    for role in (
        ROLE_AP_CLERK,
        ROLE_AP_MANAGER,
        ROLE_FINANCIAL_CONTROLLER,
        ROLE_CFO,
        ROLE_READ_ONLY,
    ):
        assert normalize_user_role(role) == role


def test_normalized_canonical_roles_are_in_role_rank() -> None:
    """The handler's check is ``normalized in ROLE_RANK``. Verify
    every canonical role passes that check — if a future refactor
    drops a role from ROLE_RANK, every invite for that role would
    400 silently. Pin the membership."""
    for role in (
        ROLE_READ_ONLY,
        ROLE_AP_CLERK,
        ROLE_AP_MANAGER,
        ROLE_FINANCIAL_CONTROLLER,
        ROLE_CFO,
    ):
        assert role in ROLE_RANK


def test_unknown_role_does_not_normalize_to_canonical() -> None:
    """A garbage token stays unknown so the handler's
    ``normalized in ROLE_RANK`` check correctly rejects it."""
    assert normalize_user_role("totally_made_up") not in ROLE_RANK


def test_owner_is_reserved_for_org_creator() -> None:
    """Owner is in ROLE_RANK (org creator gets it on org create)
    but the handler explicitly rejects ``normalized == 'owner'``
    so it can't be granted via invite."""
    assert normalize_user_role("owner") == "owner"
    assert "owner" in ROLE_RANK
